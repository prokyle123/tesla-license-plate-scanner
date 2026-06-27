import json
import sqlite3
from typing import Any, Dict, Optional

from app.util import now_local_str, now_utc_str

def log_event(con: sqlite3.Connection, level: str, msg: str, extra: Optional[Dict[str, Any]] = None):
    con.execute(
        "INSERT INTO scanner_events(ts_local, level, msg, extra_json) VALUES(?, ?, ?, ?)",
        (now_local_str(), level, msg, json.dumps(extra or {}, separators=(",", ":"))),
    )
    # Keep scanner_events table capped at 2000 rows
    con.execute(
        "DELETE FROM scanner_events WHERE id NOT IN (SELECT id FROM scanner_events ORDER BY id DESC LIMIT 2000)"
    )
    con.commit()

def update_state(con: sqlite3.Connection, **fields):
    allowed = {
        "status", "status_text", "status_detail", "last_scan_start", "last_scan_end", "last_scan_end_epoch",
        "last_error", "current_video", "current_cam", "current_frame",
        "last_plate", "cpai_endpoint", "cpai_reachable",
    }
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sql = "UPDATE scanner_state SET " + ", ".join([f"{k}=?" for k, _ in pairs]) + " WHERE id=1"
    con.execute(sql, tuple([v for _, v in pairs]))
    con.commit()

def mark_scan_start(con: sqlite3.Connection):
    update_state(con, status="scanning", last_scan_start=now_utc_str(), last_error=None)

def mark_scan_end(con: sqlite3.Connection, epoch: int):
    update_state(con, status="idle", last_scan_end=now_utc_str(), last_scan_end_epoch=epoch)
