#!/usr/bin/env python3
import argparse
import os
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from app.config import load_config
from app.db import connect, init_db
from scanner.bbox import bbox_from_extra_json
from scanner.ocr import ocr_plate_and_metadata, is_generic_plate_label


def extract_frame(video_path: str, t_s: float):
    try:
        import cv2  # type: ignore
    except Exception as e:
        raise RuntimeError(f"OpenCV required for backfill: {e}")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t_s or 0.0)) * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    ok2, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok2:
        return None
    return bytes(buf)


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def expanded_crop(img: Image.Image, x: float, y: float, w: float, h: float) -> Image.Image:
    W, H = img.size
    px = max(4, int(w * 0.18))
    py = max(4, int(h * 0.28))
    left = max(0, int(x - px))
    top = max(0, int(y - py))
    right = min(W, int(x + w + px))
    bottom = min(H, int(y + h + py))
    return img.crop((left, top, right, bottom))


def draw_overlay(img: Image.Image, x: float, y: float, w: float, h: float, label: str, conf: float) -> Image.Image:
    base = img.convert('RGBA')
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width = max(4, int(min(base.size) * 0.006))
    draw.rectangle([x, y, x + w, y + h], outline=(255, 48, 48, 255), width=width, fill=(255, 48, 48, 46))
    txt = f"{label} {conf:.2f}".strip()
    ty = max(0, y - 22)
    draw.rectangle([x, ty, x + max(88, len(txt) * 9), ty + 20], fill=(0, 0, 0, 180))
    draw.text((x + 4, ty + 2), txt, fill=(255, 255, 255, 255))
    return Image.alpha_composite(base, overlay).convert('RGB')


def save_rel(cfg, rel_path: str, img: Image.Image):
    abs_path = os.path.join(cfg.paths.static_dir, rel_path)
    _ensure_dir(os.path.dirname(abs_path))
    img.save(abs_path, 'JPEG', quality=90)
    return rel_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--limit', type=int, default=50)
    ap.add_argument('--all', action='store_true', help='rebuild overlays for all detections')
    args = ap.parse_args()

    cfg = load_config(args.config)
    con = connect(cfg.paths.db_path)
    init_db(con)

    sql = """
        SELECT d.id, d.video_id, d.frame_time_s, d.det_conf, d.plate_text, d.ocr_text, d.ocr_conf, d.annotated_path,
               d.crop_path, d.extra_json, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h, v.path AS video_path
        FROM detections d
        JOIN videos v ON v.id = d.video_id
    """
    if not args.all:
        sql += """
        WHERE d.ocr_text IS NULL OR d.ocr_text='' OR d.plate_text IS NULL OR d.plate_text='' OR d.plate_text='(plate)'
           OR d.plate_text='DAYPLATE' OR d.plate_text='NIGHTPLATE' OR d.plate_text='PLATE'
        """
    sql += " ORDER BY d.id DESC LIMIT ?"
    rows = con.execute(sql, (args.limit,)).fetchall()

    fixed = 0
    for row in rows:
        row = dict(row)
        frame_jpeg = extract_frame(row['video_path'], row.get('frame_time_s') or 0.0)
        if not frame_jpeg:
            continue
        img = Image.open(BytesIO(frame_jpeg)).convert('RGB')
        W, H = img.size
        x = float(row.get('bbox_x') or 0.0)
        y = float(row.get('bbox_y') or 0.0)
        w = float(row.get('bbox_w') or 0.0)
        h = float(row.get('bbox_h') or 0.0)
        if w < 2 or h < 2:
            x, y, w, h = bbox_from_extra_json(row.get('extra_json'), img_w=W, img_h=H)
        if w < 2 or h < 2:
            continue

        crop = expanded_crop(img, x, y, w, h)
        plate_text = row.get('ocr_text') or row.get('plate_text') or ''
        if plate_text == '(plate)' or is_generic_plate_label(plate_text):
            plate_text = ''
        state = None
        conf_ocr = row.get('ocr_conf')
        ptxt, st, ocr_conf = ocr_plate_and_metadata(crop)
        if ptxt:
            plate_text = ptxt
            conf_ocr = ocr_conf
        if st:
            state = st

        overlay = draw_overlay(img, x, y, w, h, plate_text or '(plate)', float(row.get('det_conf') or 0.0))
        ann_rel = row.get('annotated_path') or os.path.join('detections', str(row['video_id']), f"frame_backfill_{row['id']}.jpg")
        crop_rel = row.get('crop_path') or os.path.join('detections', str(row['video_id']), f"crop_backfill_{row['id']}.jpg")
        save_rel(cfg, ann_rel, overlay)
        save_rel(cfg, crop_rel, crop)

        con.execute(
            """
            UPDATE detections
            SET bbox_x=?, bbox_y=?, bbox_w=?, bbox_h=?,
                plate_text=?, ocr_text=?, ocr_conf=COALESCE(?, ocr_conf), ocr_state=COALESCE(?, ocr_state),
                annotated_path=?, crop_path=?
            WHERE id=?
            """,
            (x, y, w, h, plate_text or '(plate)', plate_text or None, conf_ocr, state, ann_rel, crop_rel, row['id'])
        )
        fixed += 1
    con.commit()
    print(f"backfilled {fixed} detections")


if __name__ == '__main__':
    main()
