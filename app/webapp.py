import argparse
import json
import os
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageDraw

from .config import load_config
from .db import connect, get_setting, init_db, q1, qall, set_setting
from .util import now_utc_str
from .video import send_file_partial
from .storage import compute_storage_stats, run_storage_cleanup
from scanner.frames import iter_frames_mp4
from scanner.bbox import normalize_bbox, bbox_from_extra_json
from scanner.ocr import ocr_plate_and_metadata, is_generic_plate_label

APP_TITLE = os.getenv("APP_TITLE", "TeslaCam Plate Dashboard")


def create_app(config_path: Optional[str] = None) -> Flask:
    cfg = load_config(config_path or os.getenv("TESLACAM_PLATE_CONFIG", None) or None)
    con = connect(cfg.paths.db_path)
    init_db(con)

    template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    static_dir = cfg.paths.static_dir

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
        static_url_path="/static",
    )
    app.config["CFG"] = cfg
    app.config["DB"] = con

    def _static_abs(rel_path: Optional[str]) -> Optional[str]:
        if not rel_path:
            return None
        p = os.path.join(cfg.paths.static_dir, rel_path)
        return p if os.path.exists(p) else None

    def _extract_frame_jpeg(video_path: str, t_s: float) -> Optional[bytes]:
        try:
            target = max(0.0, float(t_s or 0.0))
        except Exception:
            target = 0.0
        # Exact seek first: we want the actual detection frame, not the first frame.
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_POS_MSEC, target * 1000.0)
                ok, frame = cap.read()
                cap.release()
                if ok and frame is not None:
                    ok2, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                    if ok2:
                        return bytes(buf)
        except Exception:
            pass
        # Fallback for older environments.
        try:
            for fr in iter_frames_mp4(video_path, every_s=max(0.1, target if target > 0 else 0.1), max_frames=1):
                if abs(float(fr.t_s) - target) < 0.75 or target <= 0.1:
                    return fr.jpeg
        except Exception:
            return None
        return None

    def _box_from_row(box: Dict[str, Any], img_w: int, img_h: int):
        x = float(box.get("bbox_x") or 0)
        y = float(box.get("bbox_y") or 0)
        w = float(box.get("bbox_w") or 0)
        h = float(box.get("bbox_h") or 0)
        if w >= 2 and h >= 2:
            return x, y, w, h
        ej = box.get("extra_json")
        bx, by, bw, bh = bbox_from_extra_json(ej, img_w=img_w, img_h=img_h)
        if bw >= 2 and bh >= 2:
            return bx, by, bw, bh
        return normalize_bbox(box, img_w=img_w, img_h=img_h)

    def _draw_box_bytes(video_path: str, t_s: float, boxes: List[Dict[str, Any]], quality: int = 85) -> Optional[bytes]:
        frame_jpeg = _extract_frame_jpeg(video_path, t_s)
        if not frame_jpeg:
            return None
        img = Image.open(BytesIO(frame_jpeg)).convert("RGB")
        draw = ImageDraw.Draw(img)
        W, H = img.size
        for box in boxes:
            x, y, w, h = _box_from_row(box, W, H)
            if w < 2 or h < 2:
                continue
            draw.rectangle([x, y, x + w, y + h], outline=(255, 48, 48), width=4)
            label = str(box.get("label") or box.get("ocr_text") or box.get("plate_text") or "")
            conf = box.get("conf") if box.get("conf") is not None else box.get("det_conf")
            bits = []
            if label and label != "(plate)":
                bits.append(label)
            try:
                if conf is not None:
                    bits.append(f"{float(conf):.2f}")
            except Exception:
                pass
            txt = " ".join(bits)
            if txt:
                ty = max(0, y - 18)
                draw.rectangle([x, ty, x + max(80, len(txt) * 8), ty + 18], fill=(0, 0, 0))
                draw.text((x + 4, ty + 2), txt, fill=(255, 255, 255))
        bio = BytesIO()
        img.save(bio, "JPEG", quality=quality)
        bio.seek(0)
        return bio.getvalue()


    def _expanded_crop(img: Image.Image, x: float, y: float, w: float, h: float) -> Image.Image:
        W, H = img.size
        px = max(4, int(w * 0.18))
        py = max(4, int(h * 0.28))
        left = max(0, int(x - px))
        top = max(0, int(y - py))
        right = min(W, int(x + w + px))
        bottom = min(H, int(y + h + py))
        return img.crop((left, top, right, bottom))

    def _refresh_video_best_plate(video_id: int) -> None:
        row = q1(
            con,
            """
            SELECT COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) AS plate, det_conf
            FROM detections
            WHERE video_id=?
              AND COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) IS NOT NULL
              AND COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) NOT IN ('', '(plate)', 'DAYPLATE', 'NIGHTPLATE', 'PLATE')
            ORDER BY det_conf DESC, COALESCE(ocr_conf,0) DESC, id DESC
            LIMIT 1
            """,
            (video_id,),
        )
        best_plate = row['plate'] if row else None
        best_conf = float(row['det_conf']) if row and row['det_conf'] is not None else None
        con.execute("UPDATE videos SET best_plate=?, best_conf=? WHERE id=?", (best_plate, best_conf, video_id))
        con.commit()

    def _reread_detection_ocr(detection_id: int) -> bool:
        row = q1(
            con,
            """
            SELECT d.id, d.video_id, d.frame_time_s, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h,
                   d.det_conf, d.plate_text, d.ocr_text, d.ocr_conf, d.ocr_state, d.extra_json,
                   v.path AS video_path
            FROM detections d
            JOIN videos v ON v.id = d.video_id
            WHERE d.id=?
            """,
            (detection_id,),
        )
        if not row:
            return False
        row = dict(row)
        frame_jpeg = _extract_frame_jpeg(row['video_path'], row.get('frame_time_s') or 0.0)
        if not frame_jpeg:
            return False
        img = Image.open(BytesIO(frame_jpeg)).convert('RGB')
        W, H = img.size
        x, y, w, h = _box_from_row(row, W, H)
        if w < 2 or h < 2:
            return False
        crop = _expanded_crop(img, x, y, w, h)
        plate_text = (row.get('ocr_text') or row.get('plate_text') or '').strip().upper()
        if plate_text == '(plate)' or is_generic_plate_label(plate_text):
            plate_text = ''
        ptxt, st, conf_ocr = ocr_plate_and_metadata(
            crop,
            cpai_base_url=(cfg.scanner.cpai_base_url if getattr(cfg.scanner, 'ocr_cpai_enabled', False) else None),
            cpai_ocr_endpoint=getattr(cfg.scanner, 'ocr_cpai_endpoint', '/v1/vision/ocr'),
        )
        if ptxt:
            plate_text = ptxt
        if not plate_text:
            plate_text = row.get('plate_text') or '(plate)'
        con.execute(
            """
            UPDATE detections
            SET plate_text=?,
                ocr_text=?,
                ocr_conf=COALESCE(?, ocr_conf),
                ocr_state=COALESCE(?, ocr_state)
            WHERE id=?
            """,
            (plate_text, ptxt or None, conf_ocr, st, detection_id),
        )
        con.commit()
        _refresh_video_best_plate(int(row['video_id']))
        return True

    def state_row() -> Dict[str, Any]:
        row = q1(con, "SELECT * FROM scanner_state WHERE id=1")
        return dict(row) if row else {}

    def recent_events(limit: int = 30):
        rows = qall(con, "SELECT * FROM scanner_events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def stats_overview() -> Dict[str, Any]:
        s: Dict[str, Any] = {}
        s["videos_total"] = (q1(con, "SELECT COUNT(*) AS n FROM videos") or {"n": 0})["n"]
        s["videos_done"] = (q1(con, "SELECT COUNT(*) AS n FROM videos WHERE status IN ('scanned','done')") or {"n": 0})["n"]
        s["videos_error"] = (q1(con, "SELECT COUNT(*) AS n FROM videos WHERE status='error'") or {"n": 0})["n"]
        s["detections_total"] = (q1(con, "SELECT COUNT(*) AS n FROM detections") or {"n": 0})["n"]
        s["plates_unique"] = (
            q1(
                con,
                "SELECT COUNT(DISTINCT COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,''))) AS n FROM detections WHERE COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) IS NOT NULL AND COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) != '(plate)'",
            )
            or {"n": 0}
        )["n"]
        s["objects_total"] = (q1(con, "SELECT COUNT(*) AS n FROM object_detections") or {"n": 0})["n"]
        s["object_labels"] = (q1(con, "SELECT COUNT(DISTINCT label) AS n FROM object_detections WHERE label IS NOT NULL AND label != ''") or {"n": 0})["n"]
        return s

    def cam_breakdown() -> List[Dict[str, Any]]:
        rows = qall(
            con,
            """
            SELECT camera,
                   COUNT(*) AS detections,
                   AVG(det_conf) AS avg_conf,
                   MAX(det_conf) AS max_conf
            FROM detections
            WHERE camera IS NOT NULL
            GROUP BY camera
            ORDER BY detections DESC
            """,
        )
        return [dict(r) for r in rows]

    def folder_breakdown() -> List[Dict[str, Any]]:
        rows = qall(
            con,
            """
            SELECT folder,
                   SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) AS new,
                   SUM(CASE WHEN status='scanning' THEN 1 ELSE 0 END) AS scanning,
                   SUM(CASE WHEN status IN ('scanned','done') THEN 1 ELSE 0 END) AS done,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error,
                   COUNT(*) AS total
            FROM videos
            GROUP BY folder
            ORDER BY total DESC
            """,
        )
        return [dict(r) for r in rows]

    def top_plates(limit: int = 10) -> List[Dict[str, Any]]:
        rows = qall(
            con,
            """
            SELECT COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) AS plate, COUNT(*) AS count, MAX(seen_utc) AS last_seen
            FROM detections
            WHERE COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) IS NOT NULL AND COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,'')) != '(plate)'
            GROUP BY COALESCE(NULLIF(ocr_text,''), NULLIF(plate_text,''))
            ORDER BY count DESC, last_seen DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    def daily_counts(days: int = 7) -> List[Dict[str, Any]]:
        rows = qall(
            con,
            """
            SELECT substr(seen_utc, 1, 10) AS date, COUNT(*) AS count
            FROM detections
            WHERE seen_utc IS NOT NULL AND seen_utc != ''
            GROUP BY date
            ORDER BY date DESC
            LIMIT ?
            """,
            (days,),
        )
        return [dict(r) for r in rows]

    def object_daily_counts(days: int = 14) -> List[Dict[str, Any]]:
        rows = qall(
            con,
            """
            SELECT substr(seen_utc, 1, 10) AS date, COUNT(*) AS count
            FROM object_detections
            WHERE seen_utc IS NOT NULL AND seen_utc != ''
            GROUP BY date
            ORDER BY date DESC
            LIMIT ?
            """,
            (days,),
        )
        return [dict(r) for r in rows]

    def recent_detections(limit: int = 24) -> List[Dict[str, Any]]:
        rows = qall(
            con,
            """
            SELECT d.id, d.seen_utc, d.camera, d.plate_text, d.det_conf, d.annotated_path, d.video_id,
                   d.frame_time_s, d.ocr_text, d.ocr_state
            FROM detections d
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    def recent_objects(limit: int = 24) -> List[Dict[str, Any]]:
        rows = qall(
            con,
            """
            SELECT id, video_id, seen_utc, camera, frame_time_s, frame_index, label, conf, annotated_path
            FROM object_detections
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    @app.get("/thumbnail/<int:video_id>")
    def thumbnail(video_id: int):
        row = q1(con, "SELECT path FROM videos WHERE id=?", (video_id,))
        if not row:
            return ("not found", 404)
        frame_data = _extract_frame_jpeg(row["path"], 0.0)
        if not frame_data:
            return ("no frame", 404)
        buf = BytesIO(frame_data)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg")

    @app.get("/detection-image/<int:detection_id>.jpg")
    def detection_image(detection_id: int):
        row = q1(
            con,
            """
            SELECT d.id, d.video_id, d.frame_time_s, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h,
                   d.det_conf, d.plate_text, d.ocr_text, d.annotated_path, d.extra_json, v.path AS video_path
            FROM detections d
            JOIN videos v ON v.id = d.video_id
            WHERE d.id=?
            """,
            (detection_id,),
        )
        if not row:
            return ("not found", 404)
        row = dict(row)
        abs_annot = _static_abs(row.get("annotated_path"))
        if abs_annot:
            return send_file(abs_annot, mimetype="image/jpeg")
        data = _draw_box_bytes(row["video_path"], row.get("frame_time_s") or 0.0, [row])
        if data:
            return send_file(BytesIO(data), mimetype="image/jpeg")
        return thumbnail(int(row["video_id"]))

    @app.get("/object-image/<int:object_id>.jpg")
    def object_image(object_id: int):
        row = q1(
            con,
            """
            SELECT o.id, o.video_id, o.frame_time_s, o.bbox_x, o.bbox_y, o.bbox_w, o.bbox_h,
                   o.conf, o.label, o.annotated_path, o.extra_json, v.path AS video_path
            FROM object_detections o
            JOIN videos v ON v.id = o.video_id
            WHERE o.id=?
            """,
            (object_id,),
        )
        if not row:
            return ("not found", 404)
        row = dict(row)
        abs_annot = _static_abs(row.get("annotated_path"))
        if abs_annot:
            return send_file(abs_annot, mimetype="image/jpeg")
        data = _draw_box_bytes(row["video_path"], row.get("frame_time_s") or 0.0, [row])
        if data:
            return send_file(BytesIO(data), mimetype="image/jpeg")
        return thumbnail(int(row["video_id"]))

    @app.get("/clip-thumb/<int:video_id>.jpg")
    def clip_thumb(video_id: int):
        det = q1(
            con,
            "SELECT id FROM detections WHERE video_id=? ORDER BY det_conf DESC, id DESC LIMIT 1",
            (video_id,),
        )
        if det:
            return detection_image(int(det["id"]))
        return thumbnail(video_id)

    @app.get("/")
    def root():
        return redirect(url_for("dashboard"))

    @app.get("/dashboard")
    def dashboard():
        st = state_row()
        stats = stats_overview()
        cams = cam_breakdown()
        folders = folder_breakdown()
        top = top_plates(10)
        try:
            days = int(request.args.get("days") or 7)
        except Exception:
            days = 7
        if days not in (7, 30, 90, 365):
            days = 7
        daily = daily_counts(days)
        recent = recent_detections(30)
        recent_objs = recent_objects(10)
        try:
            since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            wh = q1(
                con,
                """
                SELECT COUNT(*) AS n
                FROM detections d
                JOIN watchlist w ON UPPER(COALESCE(NULLIF(d.ocr_text,''), d.plate_text)) = w.plate
                WHERE d.seen_utc IS NOT NULL AND d.seen_utc >= ?
                """,
                (since,),
            )
            stats["watch_hits"] = (wh or {"n": 0})["n"]
        except Exception:
            stats["watch_hits"] = 0
        return render_template(
            "dashboard.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=st,
            stats=stats,
            cams=cams,
            folders=folders,
            recent=recent,
            recent_objects=recent_objs,
            top_plates=top,
            daily_counts=daily,
            days=days,
            events=recent_events(20),
        )

    @app.get("/status")
    def status_page():
        st = state_row()
        folders = cfg.scanner.folders
        per_folder = []
        for f in folders:
            n = q1(con, "SELECT COUNT(*) AS n FROM videos WHERE folder=?", (f,))
            done = q1(con, "SELECT COUNT(*) AS n FROM videos WHERE folder=? AND status IN ('scanned','done')", (f,))
            err = q1(con, "SELECT COUNT(*) AS n FROM videos WHERE folder=? AND status='error'", (f,))
            per_folder.append({
                "folder": f,
                "total": (n or {"n": 0})["n"],
                "done": (done or {"n": 0})["n"],
                "error": (err or {"n": 0})["n"],
            })
        return render_template(
            "status.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=st,
            stats=stats_overview(),
            cams=cam_breakdown(),
            per_folder=per_folder,
            events=recent_events(80),
        )

    @app.get("/clips")
    def clips():
        q = (request.args.get("q") or "").strip()
        camera = (request.args.get("camera") or "").strip()
        status = (request.args.get("status") or "").strip()
        object_label = (request.args.get("object") or "").strip().lower()
        plate_q = (request.args.get("plate") or "").strip().upper()
        where = []
        params: List[Any] = []
        if q:
            where.append("(v.path LIKE ? OR COALESCE(v.rel_path,'') LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if camera:
            where.append("v.camera=?")
            params.append(camera)
        if status:
            where.append("v.status=?")
            params.append(status)
        if object_label:
            where.append("EXISTS (SELECT 1 FROM object_detections o WHERE o.video_id=v.id AND LOWER(COALESCE(o.label,'')) = ?)")
            params.append(object_label)
        if plate_q:
            where.append("EXISTS (SELECT 1 FROM detections d WHERE d.video_id=v.id AND UPPER(COALESCE(NULLIF(d.ocr_text,''), d.plate_text)) LIKE ?)")
            params.append(f"%{plate_q}%")
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = qall(
            con,
            f"""
            SELECT v.*,
                   (SELECT COUNT(*) FROM object_detections o WHERE o.video_id=v.id) AS object_count,
                   (SELECT GROUP_CONCAT(DISTINCT o.label) FROM object_detections o WHERE o.video_id=v.id AND o.label IS NOT NULL AND o.label != '') AS object_labels
            FROM videos v
            {wsql}
            ORDER BY v.mtime DESC
            LIMIT 400
            """,
            tuple(params),
        )
        rows = [dict(r) for r in rows]
        cameras = qall(con, "SELECT DISTINCT camera AS c FROM videos WHERE camera IS NOT NULL AND camera != '' ORDER BY c")
        cameras = [r["c"] for r in cameras]
        return render_template(
            "clips.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            clips=rows,
            q=q,
            camera=camera,
            status=status,
            object_label=object_label,
            plate_q=plate_q,
            cameras=cameras,
        )

    @app.get("/clip/<int:video_id>")
    def clip_detail(video_id: int):
        v = q1(con, "SELECT * FROM videos WHERE id=?", (video_id,))
        if not v:
            return ("not found", 404)
        v = dict(v)
        dets = qall(
            con,
            """
            SELECT id, seen_utc, camera, frame_time_s, det_conf, plate_text, ocr_text, ocr_state, ocr_conf, annotated_path, crop_path
            FROM detections
            WHERE video_id=?
            ORDER BY id ASC
            """,
            (video_id,),
        )
        objs = qall(
            con,
            """
            SELECT id, label, conf, camera, frame_time_s, annotated_path
            FROM object_detections
            WHERE video_id=?
            ORDER BY id DESC
            LIMIT 250
            """,
            (video_id,),
        )
        dets = [dict(d) for d in dets]
        objs = [dict(o) for o in objs]
        return render_template(
            "clip_detail.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            video=v,
            detections=dets,
            objects=objs,
        )

    @app.get("/plates")
    def plates():
        q = (request.args.get("q") or "").strip().upper()
        rows = qall(
            con,
            """
            SELECT
              COALESCE(NULLIF(d.ocr_text,''), NULLIF(d.plate_text,'')) AS plate_text,
              COUNT(*) AS count,
              MAX(d.seen_utc) AS last_seen,
              MAX(d.det_conf) AS best_conf,
              (
                SELECT d2.id
                FROM detections d2
                WHERE COALESCE(NULLIF(d2.ocr_text,''), NULLIF(d2.plate_text,'')) = COALESCE(NULLIF(d.ocr_text,''), NULLIF(d.plate_text,''))
                ORDER BY d2.det_conf DESC, d2.id DESC
                LIMIT 1
              ) AS best_det_id
            FROM detections d
            WHERE COALESCE(NULLIF(d.ocr_text,''), NULLIF(d.plate_text,'')) IS NOT NULL AND COALESCE(NULLIF(d.ocr_text,''), NULLIF(d.plate_text,'')) != '(plate)'
            GROUP BY COALESCE(NULLIF(d.ocr_text,''), NULLIF(d.plate_text,''))
            ORDER BY last_seen DESC
            LIMIT 300
            """,
        )
        rows = [dict(r) for r in rows]
        if q:
            rows = [p for p in rows if q in (p.get("plate_text") or "")]
        return render_template(
            "plates.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            plates=rows,
            q=q,
        )

    @app.get("/plate/<plate>")
    def plate_detail(plate: str):
        plate = (plate or "").strip().upper()
        rows = qall(
            con,
            """
            SELECT d.id, d.seen_utc, d.camera, d.frame_time_s, d.det_conf, d.ocr_conf, d.annotated_path, d.crop_path, d.video_id, d.plate_text, d.ocr_text, d.ocr_state
            FROM detections d
            WHERE d.plate_text=? OR d.ocr_text=?
            ORDER BY d.id DESC
            LIMIT 500
            """,
            (plate, plate),
        )
        rows = [dict(r) for r in rows]
        return render_template(
            "plate_detail.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            plate_text=plate,
            detections=rows,
        )

    @app.get("/watchlist")
    def watchlist_page():
        rows = qall(
            con,
            """
            SELECT w.plate AS plate, MAX(d.seen_utc) AS last_seen, COUNT(d.id) AS count
            FROM watchlist w
            LEFT JOIN detections d ON UPPER(COALESCE(NULLIF(d.ocr_text,''), d.plate_text)) = w.plate
            GROUP BY w.plate
            ORDER BY CASE WHEN MAX(d.seen_utc) IS NULL THEN 1 ELSE 0 END ASC, MAX(d.seen_utc) DESC
            """,
        )
        entries = [dict(r) for r in rows]
        return render_template(
            "watchlist.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            entries=entries,
        )

    @app.post("/watchlist")
    def watchlist_save():
        new_plate = (request.form.get("new_plate") or "").strip().upper()
        remove_plate = (request.form.get("remove_plate") or "").strip().upper()
        if new_plate:
            con.execute("INSERT OR IGNORE INTO watchlist(plate) VALUES(?)", (new_plate,))
            con.commit()
        if remove_plate:
            con.execute("DELETE FROM watchlist WHERE plate=?", (remove_plate,))
            con.commit()
        return redirect(url_for("watchlist_page"))


    @app.post("/detection/<int:detection_id>/reread-ocr")
    def reread_detection_ocr(detection_id: int):
        _reread_detection_ocr(detection_id)
        nxt = (request.form.get('next') or request.referrer or url_for('sightings')).strip()
        return redirect(nxt)

    @app.get("/sightings")
    def sightings():
        q = (request.args.get("q") or "").strip().upper()
        camera = (request.args.get("camera") or "").strip()
        minc = request.args.get("minc")
        try:
            minc_f = float(minc) if minc not in (None, "") else None
        except Exception:
            minc_f = None
        where = []
        params: List[Any] = []
        if q:
            where.append("COALESCE(NULLIF(ocr_text,''), plate_text) LIKE ?")
            params.append(f"%{q}%")
        if camera:
            where.append("camera=?")
            params.append(camera)
        if minc_f is not None:
            where.append("det_conf >= ?")
            params.append(minc_f)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = qall(
            con,
            f"""
            SELECT id, seen_utc, camera, plate_text, det_conf, ocr_conf, annotated_path,
                   crop_path, video_id, frame_time_s, ocr_text, ocr_state
            FROM detections
            {wsql}
            ORDER BY id DESC
            LIMIT 400
            """,
            tuple(params),
        )
        rows = [dict(r) for r in rows]
        cameras = qall(con, "SELECT DISTINCT camera AS c FROM detections WHERE camera IS NOT NULL AND camera != '' ORDER BY c")
        cameras = [r["c"] for r in cameras]
        return render_template(
            "sightings.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            detections=rows,
            q=q,
            camera=camera,
            minc=minc,
            cameras=cameras,
        )

    @app.get("/objects")
    def objects_page():
        q = (request.args.get("q") or "").strip().lower()
        days = int(request.args.get("days") or 30)
        summary = qall(
            con,
            """
            SELECT label,
                   COUNT(*) AS count,
                   COUNT(DISTINCT video_id) AS clip_count,
                   MAX(seen_utc) AS last_seen,
                   MAX(conf) AS best_conf,
                   (
                     SELECT o2.id FROM object_detections o2
                     WHERE o2.label = o.label
                     ORDER BY o2.conf DESC, o2.id DESC
                     LIMIT 1
                   ) AS best_obj_id
            FROM object_detections o
            WHERE label IS NOT NULL AND label != ''
            GROUP BY label
            ORDER BY count DESC, last_seen DESC
            """,
        )
        summary = [dict(r) for r in summary]
        if q:
            summary = [r for r in summary if q in (r.get("label") or "").lower()]

        recent = qall(
            con,
            """
            SELECT id, label, seen_utc, camera, conf, video_id, frame_time_s
            FROM object_detections
            ORDER BY id DESC
            LIMIT 100
            """,
        )
        recent = [dict(r) for r in recent]
        if q:
            recent = [r for r in recent if q in (r.get("label") or "").lower()]

        daily = object_daily_counts(days)
        return render_template(
            "objects.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            objects=summary,
            recent_objects=recent,
            daily_counts=daily,
            q=q,
            days=days,
        )

    @app.get("/settings")
    def settings_page():
        def get(key: str, default: str) -> str:
            v = get_setting(con, key, None)
            return v if v is not None else default
        data = {
            "min_det_conf": get("min_det_conf", str(cfg.scanner.cpai_min_det_conf)),
            "frame_interval_s": get("frame_interval_s", str(cfg.scanner.frame_interval_s)),
            "scan_sleep_s": get("scan_sleep_s", str(cfg.scanner.scan_sleep_s)),
            "ocr_enabled": get("ocr_enabled", "1" if cfg.scanner.ocr_enabled else "0"),
            "ocr_min_conf": get("ocr_min_conf", str(cfg.scanner.ocr_min_conf)),
            "max_videos_per_pass": get("max_videos_per_pass", str(cfg.scanner.max_videos_per_pass)),
            "object_detection_enabled": get("object_detection_enabled", "1" if cfg.scanner.object_detection_enabled else "0"),
            "object_min_conf": get("object_min_conf", str(cfg.scanner.object_min_conf)),
            "object_frame_interval_s": get("object_frame_interval_s", str(getattr(cfg.scanner, "object_frame_interval_s", 10.0))),
            "auto_cleanup_enabled": get("auto_cleanup_enabled", "1" if cfg.scanner.auto_cleanup_enabled else "0"),
            "generated_retention_days": get("generated_retention_days", str(cfg.scanner.generated_retention_days)),
            "event_retention_days": get("event_retention_days", str(cfg.scanner.event_retention_days)),
            "delete_source_clips": get("delete_source_clips", "1" if cfg.scanner.delete_source_clips else "0"),
            "source_clip_retention_days": get("source_clip_retention_days", str(cfg.scanner.source_clip_retention_days)),
        }
        return render_template(
            "settings.html",
            title=APP_TITLE,
            app_title=APP_TITLE,
            state=state_row(),
            settings=data,
            storage=compute_storage_stats(cfg, con),
        )

    @app.post("/settings")
    def settings_save():
        fields = [
            "min_det_conf",
            "frame_interval_s",
            "scan_sleep_s",
            "ocr_enabled",
            "ocr_min_conf",
            "max_videos_per_pass",
            "object_detection_enabled",
            "object_min_conf",
            "object_frame_interval_s",
            "auto_cleanup_enabled",
            "generated_retention_days",
            "event_retention_days",
            "delete_source_clips",
            "source_clip_retention_days",
        ]
        for f in fields:
            if f in request.form:
                set_setting(con, f, str(request.form.get(f) or "").strip())
        set_setting(con, "settings_updated_utc", now_utc_str())
        return redirect(url_for("settings_page"))

    @app.post("/settings/cleanup-now")
    def settings_cleanup_now():
        run_storage_cleanup(cfg, con, force=True)
        return redirect(url_for("settings_page"))

    @app.get("/video/<int:video_id>")
    def video_stream(video_id: int):
        row = q1(con, "SELECT path FROM videos WHERE id=?", (video_id,))
        if not row:
            return ("not found", 404)
        return send_file_partial(row["path"], download_name=os.path.basename(row["path"]))

    @app.get("/api/state")
    def api_state():
        st = state_row()
        try:
            sleep_s = int(get_setting(con, "scan_sleep_s", str(cfg.scanner.scan_sleep_s)) or cfg.scanner.scan_sleep_s)
        except Exception:
            sleep_s = cfg.scanner.scan_sleep_s
        import time
        now = int(time.time())
        end_epoch = int(st.get("last_scan_end_epoch") or 0)
        st["next_scan_in_s"] = max(0, sleep_s - (now - end_epoch)) if end_epoch else 0
        return jsonify(st)

    @app.get("/api/live")
    def api_live():
        st = state_row()
        try:
            sleep_s = int(get_setting(con, "scan_sleep_s", str(cfg.scanner.scan_sleep_s)) or cfg.scanner.scan_sleep_s)
        except Exception:
            sleep_s = cfg.scanner.scan_sleep_s
        import time
        now = int(time.time())
        end_epoch = int(st.get("last_scan_end_epoch") or 0)
        st["next_scan_in_s"] = max(0, sleep_s - (now - end_epoch)) if end_epoch else 0

        def _count(where: str = "", params: tuple = ()) -> int:
            row = q1(con, f"SELECT COUNT(*) AS n FROM videos {where}", params)
            return int((row or {"n": 0})["n"])

        queue = {
            "new": _count("WHERE status='new'"),
            "scanning": _count("WHERE status='scanning'"),
            "done": _count("WHERE status IN ('scanned','done')"),
            "error": _count("WHERE status='error'"),
        }

        events = recent_events(40)
        cpai = {"ok": None, "error": None, "base_url": cfg.scanner.cpai_base_url}
        try:
            import requests
            base = (cfg.scanner.cpai_base_url or "").rstrip("/")
            url = base + "/v1/status/ping"
            r = requests.get(url, timeout=1.5)
            cpai["ok"] = True if r.status_code else False
            cpai["error"] = None if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as e:
            cpai["ok"] = False
            cpai["error"] = str(e)
        return jsonify({"state": st, "queue": queue, "events": events, "cpai": cpai})

    @app.post("/api/scan_now")
    def api_scan_now():
        set_setting(con, "scan_now", "1")
        return jsonify({"ok": True})

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.getenv("TESLACAM_PLATE_CONFIG", None))
    ap.add_argument("--host", default=os.getenv("HOST", None))
    ap.add_argument("--port", default=os.getenv("PORT", None))
    args = ap.parse_args()

    app = create_app(args.config)
    cfg = app.config["CFG"]
    host = args.host or cfg.app.host
    port = int(args.port or cfg.app.port)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except Exception:
        app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
