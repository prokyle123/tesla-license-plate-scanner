import os
import re
import json
import time
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

from PIL import Image, ImageOps, ImageEnhance
import pytesseract

DB_PATH_DEFAULT = str(Path(__file__).resolve().parents[1] / "data" / "plates.db")
PROJECT_ROOT = "/opt/teslacam-plate-dashboard-v1"

STATE_NAMES = {
    "ALABAMA":"AL","ALASKA":"AK","ARIZONA":"AZ","ARKANSAS":"AR","CALIFORNIA":"CA","COLORADO":"CO",
    "CONNECTICUT":"CT","DELAWARE":"DE","FLORIDA":"FL","GEORGIA":"GA","HAWAII":"HI","IDAHO":"ID",
    "ILLINOIS":"IL","INDIANA":"IN","IOWA":"IA","KANSAS":"KS","KENTUCKY":"KY","LOUISIANA":"LA",
    "MAINE":"ME","MARYLAND":"MD","MASSACHUSETTS":"MA","MICHIGAN":"MI","MINNESOTA":"MN",
    "MISSISSIPPI":"MS","MISSOURI":"MO","MONTANA":"MT","NEBRASKA":"NE","NEVADA":"NV",
    "NEW HAMPSHIRE":"NH","NEW JERSEY":"NJ","NEW MEXICO":"NM","NEW YORK":"NY","NORTH CAROLINA":"NC",
    "NORTH DAKOTA":"ND","OHIO":"OH","OKLAHOMA":"OK","OREGON":"OR","PENNSYLVANIA":"PA",
    "RHODE ISLAND":"RI","SOUTH CAROLINA":"SC","SOUTH DAKOTA":"SD","TENNESSEE":"TN","TEXAS":"TX",
    "UTAH":"UT","VERMONT":"VT","VIRGINIA":"VA","WASHINGTON":"WA","WEST VIRGINIA":"WV",
    "WISCONSIN":"WI","WYOMING":"WY"
}

def _abs_path(p: str) -> str:
    # allow absolute, /static/..., or relative to project root
    if not p:
        return p
    if os.path.isabs(p) and os.path.exists(p):
        return p
    if p.startswith("/static/"):
        rel = p[len("/static/"):]
        return os.path.join(PROJECT_ROOT, "static", rel)
    if p.startswith("static/"):
        return os.path.join(PROJECT_ROOT, p)
    return os.path.join(PROJECT_ROOT, p)

def preprocess_plate(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    # increase size for OCR
    img = img.resize((img.width * 2, img.height * 2))
    # contrast boost
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    # simple threshold
    img = img.point(lambda x: 255 if x > 140 else 0)
    return img

def ocr_plate_and_state(img_path: str) -> Tuple[Optional[str], Optional[str], str, Optional[float]]:
    raw_all = ""
    conf = None

    try:
        img = Image.open(img_path)
    except Exception as e:
        return None, None, f"open_failed:{e}", None

    # Pass 1: strict plate number
    try:
        pimg = preprocess_plate(img)
        cfg = "--oem 1 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        txt = pytesseract.image_to_string(pimg, config=cfg) or ""
        raw_all += "PLATE_PASS:\n" + txt.strip() + "\n"
        candidates = re.findall(r"[A-Z0-9]{4,8}", txt.upper())
        plate = max(candidates, key=len) if candidates else None
    except Exception as e:
        plate = None
        raw_all += f"PLATE_PASS_ERR:{e}\n"

    # Pass 2: relaxed state/name capture
    try:
        gimg = img.convert("L")
        gimg = gimg.resize((gimg.width * 2, gimg.height * 2))
        txt2 = pytesseract.image_to_string(gimg, config="--oem 1 --psm 6") or ""
        raw_all += "META_PASS:\n" + txt2.strip() + "\n"
        upper = " ".join(txt2.upper().split())
        state = None

        # try full names first
        for name, abbr in STATE_NAMES.items():
            if name in upper:
                state = abbr
                break

        # fallback: look for 2-letter abbreviations (less reliable)
        if not state:
            abbrs = set(STATE_NAMES.values())
            for token in re.findall(r"\b[A-Z]{2}\b", upper):
                if token in abbrs:
                    state = token
                    break
    except Exception as e:
        state = None
        raw_all += f"META_PASS_ERR:{e}\n"

    return plate, state, raw_all.strip(), conf

def detect_detections_table(con: sqlite3.Connection) -> str:
    con.row_factory = sqlite3.Row
    tables = [r["name"] for r in con.execute("select name from sqlite_master where type='table'").fetchall()]

    def cols(t):
        return [r["name"] for r in con.execute(f"pragma table_info({t})").fetchall()]

    best = None
    best_score = -1
    for t in tables:
        c = set(cols(t))
        score = 0
        for need in ["video_id", "crop_path", "annotated_path", "det_conf", "frame_time_s", "plate_text"]:
            if need in c:
                score += 1
        if score > best_score:
            best_score = score
            best = t
    if not best:
        raise RuntimeError("No detections table found")
    return best

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH_DEFAULT)
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--sleep", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    t = detect_detections_table(con)

    while True:
        # Pick rows missing OCR with a crop image
        rows = con.execute(
            f"SELECT id, crop_path, plate_text FROM {t} "
            f"WHERE (ocr_text IS NULL OR ocr_text='') AND crop_path IS NOT NULL AND crop_path!='' "
            f"ORDER BY id DESC LIMIT ?",
            (args.limit,)
        ).fetchall()

        if not rows:
            if args.once:
                break
            time.sleep(args.sleep)
            continue

        for r in rows:
            rid = r["id"]
            crop = _abs_path(r["crop_path"])
            if not crop or not os.path.exists(crop):
                con.execute(f"UPDATE {t} SET ocr_text='', ocr_raw=? WHERE id=?",
                            (f"missing_crop:{r['crop_path']}", rid))
                con.commit()
                continue

            plate, state, raw, conf = ocr_plate_and_state(crop)

            # If plate_text is empty, we can backfill it from OCR
            plate_text = r["plate_text"] or ""
            new_plate_text = plate_text
            if (not plate_text) and plate:
                new_plate_text = plate

            con.execute(
                f"UPDATE {t} SET ocr_text=?, ocr_state=?, ocr_raw=?, ocr_conf=?, ocr_engine=?, ocr_ts_local=?, plate_text=? WHERE id=?",
                (
                    plate or "",
                    state or "",
                    raw,
                    conf,
                    "tesseract",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    new_plate_text,
                    rid
                )
            )
            con.commit()

        if args.once:
            break

if __name__ == "__main__":
    main()
