import json
import os
import sqlite3
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw

from app.config import load_config
from app.db import connect, get_setting, init_db, q1, qall, qexec
from app.util import now_utc_str, parse_seen_from_filename, safe_name
from app.storage import run_storage_cleanup

from .cpai import CPaiClient
from .frames import crop_from_frame_jpeg, iter_frames_mp4
from .ocr import ocr_plate_and_metadata, is_generic_plate_label
from .bbox import normalize_bbox, bbox_from_extra_json
from .state import log_event, mark_scan_end, mark_scan_start, update_state


@dataclass
class Runtime:
    min_det_conf: float
    frame_interval_s: float
    scan_sleep_s: int
    max_videos_per_pass: int
    ocr_enabled: bool
    ocr_min_conf: float
    object_enabled: bool
    object_min_conf: float
    object_frame_interval_s: float


def _runtime_from_db(con: sqlite3.Connection, cfg) -> Runtime:
    def _g(key: str, default: str) -> str:
        v = get_setting(con, key, None)
        return v if v is not None and v != "" else default

    try:
        min_det_conf = float(_g("min_det_conf", str(cfg.scanner.cpai_min_det_conf)))
    except Exception:
        min_det_conf = cfg.scanner.cpai_min_det_conf
    try:
        frame_interval_s = float(_g("frame_interval_s", str(cfg.scanner.frame_interval_s)))
    except Exception:
        frame_interval_s = cfg.scanner.frame_interval_s
    try:
        scan_sleep_s = int(float(_g("scan_sleep_s", str(cfg.scanner.scan_sleep_s))))
    except Exception:
        scan_sleep_s = cfg.scanner.scan_sleep_s
    try:
        max_videos_per_pass = int(float(_g("max_videos_per_pass", str(cfg.scanner.max_videos_per_pass))))
    except Exception:
        max_videos_per_pass = cfg.scanner.max_videos_per_pass
    ocr_enabled = _g("ocr_enabled", "1" if cfg.scanner.ocr_enabled else "0") in ("1", "true", "yes", "on")
    try:
        ocr_min_conf = float(_g("ocr_min_conf", str(cfg.scanner.ocr_min_conf)))
    except Exception:
        ocr_min_conf = cfg.scanner.ocr_min_conf
    object_enabled = _g(
        "object_detection_enabled",
        "1" if getattr(cfg.scanner, "object_detection_enabled", True) else "0",
    ) in ("1", "true", "yes", "on")
    try:
        object_min_conf = float(_g("object_min_conf", str(getattr(cfg.scanner, "object_min_conf", 0.35))))
    except Exception:
        object_min_conf = getattr(cfg.scanner, "object_min_conf", 0.35)
    try:
        object_frame_interval_s = float(_g("object_frame_interval_s", str(getattr(cfg.scanner, "object_frame_interval_s", 10.0))))
    except Exception:
        object_frame_interval_s = float(getattr(cfg.scanner, "object_frame_interval_s", 10.0))
    return Runtime(
        min_det_conf=min_det_conf,
        frame_interval_s=frame_interval_s,
        scan_sleep_s=scan_sleep_s,
        max_videos_per_pass=max_videos_per_pass,
        ocr_enabled=ocr_enabled,
        ocr_min_conf=ocr_min_conf,
        object_enabled=object_enabled,
        object_min_conf=object_min_conf,
        object_frame_interval_s=object_frame_interval_s,
    )


def _guess_camera_from_filename(name: str) -> str:
    n = (name or "").lower()
    for cam in ("front", "back", "left_repeater", "right_repeater", "left", "right"):
        if f"-{cam}" in n:
            return cam
        if n.endswith(f"_{cam}.mp4") or n.endswith(f"-{cam}.mp4"):
            return cam
    if "left" in n and "repeat" in n:
        return "left_repeater"
    if "right" in n and "repeat" in n:
        return "right_repeater"
    return "unknown"


def _clean_plate_text(s: str) -> str:
    import re as _re
    s = (s or '').upper().strip()
    s = _re.sub(r'[^A-Z0-9]', '', s)
    # Typical license plates are short; keep a sane upper bound.
    return s[:10]


def _prediction_plate_text(pred: dict) -> str:
    """Best-effort extraction of plate text directly from the CPAI payload.

    Plate models often return generic labels like DAYPLATE / PLATE in ``label``
    or ``name``. Those should not be treated as actual OCR output.
    """
    explicit = []
    fallback = []

    for key in ('plate','text','value','plateText','license','license_plate','licensePlate'):
        v = pred.get(key)
        if isinstance(v, str):
            explicit.append(v)
        elif isinstance(v, dict):
            for sk in ('text','plate','value'):
                sv = v.get(sk)
                if isinstance(sv, str):
                    explicit.append(sv)

    for key in ('plate','bestPlate','best_plate','result'):
        v = pred.get(key)
        if isinstance(v, dict):
            for kk in ('text','plate','value'):
                sv = v.get(kk)
                if isinstance(sv, str):
                    explicit.append(sv)

    for key in ('label','name','class'):
        v = pred.get(key)
        if isinstance(v, str):
            fallback.append(v)

    for raw in explicit + fallback:
        t = _clean_plate_text(raw)
        if len(t) < 4 or is_generic_plate_label(t):
            continue
        if not any(ch.isdigit() for ch in t):
            continue
        if 4 <= len(t) <= 8:
            return t
    return ''


def _walk_mp4s(root: str, folders: List[str]) -> Iterable[str]:
    for folder in folders:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for fn in filenames:
                if fn.lower().endswith(".mp4"):
                    yield os.path.join(dirpath, fn)


def ensure_video_rows(con: sqlite3.Connection, cfg):
    root = cfg.paths.teslacam_root
    folders = list(cfg.scanner.folders)
    count_new = 0
    for path in _walk_mp4s(root, folders):
        try:
            st = os.stat(path)
        except Exception:
            continue
        rel_path = os.path.relpath(path, root)
        camera = _guess_camera_from_filename(os.path.basename(path))
        qexec(
            con,
            """
            INSERT INTO videos(path, rel_path, folder, camera, mtime, size_bytes, status)
            VALUES(?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(path) DO UPDATE SET
                mtime=excluded.mtime,
                size_bytes=excluded.size_bytes,
                folder=excluded.folder,
                camera=excluded.camera
            """,
            (
                path,
                rel_path,
                rel_path.split(os.sep, 1)[0] if os.sep in rel_path else (folders[0] if folders else ""),
                camera,
                int(st.st_mtime),
                int(st.st_size),
            ),
        )
        if con.total_changes:
            count_new += 1
    if count_new:
        log_event(con, "info", f"Indexed videos. New/updated: {count_new}")


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _plate_frame_rel(video_id: int, frame_index: int, t_s: float) -> str:
    return os.path.join("detections", str(video_id), f"frame_{frame_index}_{int(t_s * 1000):06d}.jpg")


def _object_frame_rel(video_id: int, frame_index: int, t_s: float) -> str:
    return os.path.join("objects", str(video_id), f"frame_{frame_index}_{int(t_s * 1000):06d}.jpg")


def _plate_crop_rel(video_id: int, frame_index: int, idx: int, plate_text: str) -> str:
    return os.path.join("detections", str(video_id), f"crop_{frame_index}_{idx}_{safe_name(plate_text)}.jpg")


def _save_rel_image(cfg, rel_path: str, img: Image.Image, quality: int = 85) -> str:
    abs_path = os.path.join(cfg.paths.static_dir, rel_path)
    _ensure_dir(os.path.dirname(abs_path))
    img.save(abs_path, "JPEG", quality=quality)
    return rel_path


def _load_frame_image(frame_jpeg: bytes) -> Image.Image:
    return Image.open(BytesIO(frame_jpeg)).convert("RGB")


def _expanded_crop(img: Image.Image, x: float, y: float, w: float, h: float) -> Image.Image:
    W, H = img.size
    px = max(4, int(w * 0.18))
    py = max(4, int(h * 0.28))
    left = max(0, int(x - px))
    top = max(0, int(y - py))
    right = min(W, int(x + w + px))
    bottom = min(H, int(y + h + py))
    return img.crop((left, top, right, bottom))


def _draw_predictions(base_img: Image.Image, preds: List[dict], label_builder, outline=(255, 64, 64)) -> Image.Image:
    img = base_img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    W, H = img.size
    for p in preds:
        x, y, w, h = normalize_bbox(p, img_w=W, img_h=H)
        if w < 2 or h < 2:
            continue
        width = max(4, int(min(W, H) * 0.006))
        draw.rectangle([x, y, x + w, y + h], outline=(*outline, 255), width=width, fill=(255, 48, 48, 46))
        label = label_builder(p)
        if label:
            text_y = max(0, y - 22)
            box_w = max(88, len(label) * 9)
            draw.rectangle([x, text_y, x + box_w, text_y + 20], fill=(0, 0, 0, 180))
            draw.text((x + 4, text_y + 2), label, fill=(255, 255, 255, 255))
    return Image.alpha_composite(img, overlay).convert("RGB")


def scan_one_video(con: sqlite3.Connection, cfg, rt: Runtime, cpai: CPaiClient, video_row):
    if video_row is not None and not hasattr(video_row, "get"):
        video_row = dict(video_row)

    vid = int(video_row["id"])
    path = video_row["path"]
    camera = video_row.get("camera") or "unknown"
    video_seen = parse_seen_from_filename(os.path.basename(path)) or now_utc_str()
    update_state(con, current_video=os.path.basename(path), current_cam=camera, current_frame=0)

    frames_scanned = 0
    plate_count = 0
    best_plate: Optional[str] = None
    best_conf: float = 0.0
    object_error_logged = False
    last_object_t = -1e9

    for i, frame in enumerate(iter_frames_mp4(path, every_s=rt.frame_interval_s)):
        frames_scanned += 1
        update_state(con, current_frame=i)
        frame_img = None
        jpeg_q = int(getattr(cfg.scanner, "preview_jpeg_quality", 85))

        try:
            plate_res = cpai.detect(frame.jpeg, min_confidence=rt.min_det_conf)
            plate_preds = [p for p in (plate_res.predictions or []) if float(p.get("confidence") or p.get("conf") or 0) >= rt.min_det_conf]
        except Exception as e:
            raise RuntimeError(f"CPAI plate detect failed: {e}")

        object_preds: List[dict] = []
        if rt.object_enabled and (float(frame.t_s) - float(last_object_t) >= max(0.1, float(rt.object_frame_interval_s))):
            try:
                last_object_t = float(frame.t_s)
                obj_res = cpai.detect_objects(
                    frame.jpeg,
                    endpoint=getattr(cfg.scanner, "object_endpoint", "/v1/vision/detection"),
                    min_confidence=rt.object_min_conf,
                )
                object_preds = [p for p in (obj_res.predictions or []) if float(p.get("confidence") or p.get("conf") or 0) >= rt.object_min_conf]
            except Exception as e:
                if not object_error_logged:
                    log_event(con, "WARN", f"Object detect failed: {e}")
                    object_error_logged = True
                object_preds = []

        if plate_preds:
            frame_img = frame_img or _load_frame_image(frame.jpeg)
            plate_rel = _plate_frame_rel(vid, i, frame.t_s)
            plate_preds = [dict(p, _plate_text_guess=_prediction_plate_text(p)) for p in plate_preds]
            plate_annot_img = _draw_predictions(
                frame_img,
                plate_preds,
                lambda p: ((p.get('_plate_text_guess') or '') + (' ' if p.get('_plate_text_guess') else '') + f"{float(p.get('confidence') or p.get('conf') or 0):.2f}").strip(),
                outline=(255, 48, 48),
            )
            _save_rel_image(cfg, plate_rel, plate_annot_img, quality=jpeg_q)

            for idx, p in enumerate(plate_preds):
                conf = float(p.get("confidence") or p.get("conf") or 0)
                x, y, w, h = normalize_bbox(p, img_w=frame.width, img_h=frame.height)

                crop_img = _expanded_crop(frame_img or _load_frame_image(frame.jpeg), x, y, w, h)
                cpai_plate_text = _prediction_plate_text(p)
                plate_text = cpai_plate_text or ""
                if is_generic_plate_label(plate_text):
                    plate_text = ""
                ocr_text = None
                ocr_state = None
                ocr_conf = None
                if rt.ocr_enabled and crop_img.width >= 2 and crop_img.height >= 2:
                    ptxt, st, conf_ocr = ocr_plate_and_metadata(
                        crop_img,
                        cpai_base_url=(cfg.scanner.cpai_base_url if getattr(cfg.scanner, "ocr_cpai_enabled", False) else None),
                        cpai_ocr_endpoint=getattr(cfg.scanner, "ocr_cpai_endpoint", "/v1/vision/ocr"),
                    )
                    if ptxt:
                        ocr_text = ptxt
                        # Prefer OCR when it is reasonably confident, otherwise keep the CPAI text guess.
                        if not plate_text or conf_ocr is None or float(conf_ocr) >= rt.ocr_min_conf:
                            plate_text = ptxt
                    if st:
                        ocr_state = st
                    if conf_ocr is not None:
                        ocr_conf = float(conf_ocr)
                if not plate_text:
                    plate_text = "(plate)"

                crop_rel = ""
                if crop_img.width >= 2 and crop_img.height >= 2:
                    crop_rel = _plate_crop_rel(vid, i, idx, plate_text)
                    _save_rel_image(cfg, crop_rel, crop_img, quality=jpeg_q)

                con.execute(
                    """
                    INSERT INTO detections(
                        video_id, seen_utc, camera, frame_time_s, frame_index,
                        det_conf, bbox_x, bbox_y, bbox_w, bbox_h,
                        plate_text, ocr_conf, region,
                        annotated_path, crop_path, extra_json, created_utc, ocr_text, ocr_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vid,
                        video_seen,
                        camera,
                        float(frame.t_s),
                        i,
                        conf,
                        x,
                        y,
                        w,
                        h,
                        plate_text,
                        ocr_conf,
                        None,
                        plate_rel,
                        crop_rel,
                        json.dumps({"cpai": p, "source": "license-plate", "cpai_plate_text": cpai_plate_text or ""}, separators=(",", ":")),
                        now_utc_str(),
                        ocr_text,
                        ocr_state,
                    ),
                )
                con.commit()
                plate_count += 1
                if conf > best_conf:
                    best_conf = conf
                    best_plate = None if plate_text == "(plate)" else plate_text
                update_state(con, last_plate=plate_text)
                if plate_text not in ("", "(plate)") and q1(con, "SELECT 1 FROM watchlist WHERE plate=?", (plate_text.upper(),)):
                    log_event(con, "WARN", f"Watchlist plate detected: {plate_text}")

        if object_preds:
            frame_img = frame_img or _load_frame_image(frame.jpeg)
            object_rel = _object_frame_rel(vid, i, frame.t_s)
            object_annot_img = _draw_predictions(
                frame_img,
                object_preds,
                lambda p: f"{(p.get('label') or p.get('name') or 'object')} {float(p.get('confidence') or p.get('conf') or 0):.2f}",
                outline=(255, 80, 80),
            )
            _save_rel_image(cfg, object_rel, object_annot_img, quality=jpeg_q)
            for p in object_preds:
                label = str(p.get("label") or p.get("name") or p.get("class") or "object")
                conf = float(p.get("confidence") or p.get("conf") or 0)
                x, y, w, h = normalize_bbox(p, img_w=frame.width, img_h=frame.height)
                con.execute(
                    """
                    INSERT INTO object_detections(
                        video_id, seen_utc, camera, frame_time_s, frame_index,
                        label, conf, bbox_x, bbox_y, bbox_w, bbox_h,
                        annotated_path, extra_json, created_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vid,
                        video_seen,
                        camera,
                        float(frame.t_s),
                        i,
                        label,
                        conf,
                        x,
                        y,
                        w,
                        h,
                        object_rel,
                        json.dumps({"cpai": p, "source": "objects"}, separators=(",", ":")),
                        now_utc_str(),
                    ),
                )
            con.commit()

    con.execute(
        """
        UPDATE videos
        SET status='scanned', scanned_utc=?, frames_scanned=?, detections=?, best_plate=?, best_conf=?
        WHERE id=?
        """,
        (now_utc_str(), frames_scanned, plate_count, best_plate, float(best_conf), vid),
    )
    con.commit()


def scan_loop(config_path: Optional[str] = None):
    cfg = load_config(config_path)
    con = connect(cfg.paths.db_path)
    init_db(con)
    cpai = CPaiClient(cfg.scanner.cpai_base_url, cfg.scanner.cpai_model, timeout_s=cfg.scanner.cpai_timeout_s)
    update_state(con, cpai_endpoint=cpai.endpoint, cpai_reachable=1 if cpai.ping() else 0)
    log_event(con, "info", f"Scanner started. CPAI={cpai.endpoint}")

    while True:
        scan_now = get_setting(con, "scan_now", "0")
        if scan_now == "1":
            con.execute("UPDATE settings SET value='0' WHERE key='scan_now'")
            con.commit()
        rt = _runtime_from_db(con, cfg)
        try:
            mark_scan_start(con)
            update_state(con, status="scanning", status_text="Indexing videos", status_detail=None)
            ensure_video_rows(con, cfg)
            update_state(con, status="scanning", status_text="Selecting batch", status_detail=None, cpai_reachable=1 if cpai.ping() else 0)
            rows = qall(
                con,
                """
                SELECT * FROM videos
                WHERE status='new'
                ORDER BY mtime ASC
                LIMIT ?
                """,
                (rt.max_videos_per_pass,),
            )
            if not rows:
                epoch = int(time.time())
                mark_scan_end(con, epoch)
                update_state(con, status="idle", current_video=None, current_cam=None, current_frame=None, status_text="Idle", status_detail=None)
                time.sleep(max(5, rt.scan_sleep_s))
                continue
            log_event(con, "info", f"Scanning batch: {len(rows)} videos")
            for row in rows:
                vid = row["id"]
                con.execute("UPDATE videos SET status='scanning', error=NULL WHERE id=?", (vid,))
                con.commit()
                try:
                    scan_one_video(con, cfg, rt, cpai, row)
                except Exception as e:
                    con.execute("UPDATE videos SET status='error', error=? WHERE id=?", (str(e), vid))
                    con.commit()
                    update_state(con, last_error=str(e), status_detail=str(e))
                    log_event(con, "ERROR", f"Video failed: {row['path']}: {e}")
            epoch = int(time.time())
            mark_scan_end(con, epoch)
            update_state(con, status="idle", current_video=None, current_cam=None, current_frame=None, status_text="Idle", status_detail=None)
            try:
                if get_setting(con, "auto_cleanup_enabled", "1" if getattr(cfg.scanner, "auto_cleanup_enabled", True) else "0") in ("1", "true", "yes", "on"):
                    stats = run_storage_cleanup(cfg, con, force=False)
                    if not stats.get("skipped"):
                        log_event(con, "info", f"Cleanup: freed {int(stats.get('bytes_freed', 0))} bytes, removed {int(stats.get('files_deleted', 0))} generated files, deleted {int(stats.get('source_deleted', 0))} source clips")
            except Exception as ce:
                log_event(con, "WARN", f"Cleanup failed: {ce}")
            time.sleep(max(5, rt.scan_sleep_s))
        except Exception as e:
            update_state(con, status="error", last_error=str(e), status_text="Scanner error", status_detail=str(e))
            log_event(con, "ERROR", f"Scanner loop failed: {e}")
            time.sleep(5)
