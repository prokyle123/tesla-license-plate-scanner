import os
import sqlite3
from typing import Any, Optional, Tuple


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _cols(con: sqlite3.Connection, table: str) -> set:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] if not isinstance(r, sqlite3.Row) else r["name"] for r in rows}
    except Exception:
        return set()


def _ensure_col(con: sqlite3.Connection, table: str, col: str, coltype: str):
    if col in _cols(con, table):
        return
    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def init_db(con: sqlite3.Connection):
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS videos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            rel_path TEXT,
            folder TEXT,
            camera TEXT,
            mtime INTEGER,
            size_bytes INTEGER,
            status TEXT DEFAULT 'new',
            scanned_utc TEXT,
            error TEXT,
            frames_scanned INTEGER DEFAULT 0,
            detections INTEGER DEFAULT 0,
            best_plate TEXT,
            best_conf REAL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS detections(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            seen_utc TEXT,
            camera TEXT,
            frame_time_s REAL,
            frame_index INTEGER,
            det_conf REAL,
            bbox_x REAL,
            bbox_y REAL,
            bbox_w REAL,
            bbox_h REAL,
            plate_text TEXT,
            ocr_conf REAL,
            region TEXT,
            annotated_path TEXT,
            crop_path TEXT,
            extra_json TEXT,
            created_utc TEXT,
            ocr_text TEXT,
            ocr_state TEXT,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_det_plate ON detections(plate_text)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_det_seen ON detections(seen_utc)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_det_video ON detections(video_id)")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS object_detections(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            seen_utc TEXT,
            camera TEXT,
            frame_time_s REAL,
            frame_index INTEGER,
            label TEXT,
            conf REAL,
            bbox_x REAL,
            bbox_y REAL,
            bbox_w REAL,
            bbox_h REAL,
            annotated_path TEXT,
            extra_json TEXT,
            created_utc TEXT,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_obj_label ON object_detections(label)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_obj_seen ON object_detections(seen_utc)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_obj_video ON object_detections(video_id)")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_state(
            id INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT,
            status_text TEXT,
            status_detail TEXT,
            last_scan_start TEXT,
            last_scan_end TEXT,
            last_scan_end_epoch INTEGER,
            last_error TEXT,
            current_video TEXT,
            current_cam TEXT,
            current_frame INTEGER,
            last_plate TEXT,
            cpai_endpoint TEXT,
            cpai_reachable INTEGER
        )
        """
    )
    con.execute("INSERT OR IGNORE INTO scanner_state(id, status, last_scan_end_epoch) VALUES (1, 'idle', 0)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_local TEXT,
            level TEXT,
            msg TEXT,
            extra_json TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_id ON scanner_events(id)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist(
            plate TEXT PRIMARY KEY
        )
        """
    )
    con.commit()

    try:
        _ensure_col(con, "videos", "rel_path", "TEXT")
        _ensure_col(con, "videos", "folder", "TEXT")
        _ensure_col(con, "videos", "size_bytes", "INTEGER")
        _ensure_col(con, "videos", "scanned_utc", "TEXT")
        _ensure_col(con, "videos", "error", "TEXT")
        _ensure_col(con, "videos", "frames_scanned", "INTEGER DEFAULT 0")
        _ensure_col(con, "videos", "detections", "INTEGER DEFAULT 0")
        _ensure_col(con, "videos", "best_plate", "TEXT")
        _ensure_col(con, "videos", "best_conf", "REAL")
        _ensure_col(con, "scanner_state", "status_text", "TEXT")
        _ensure_col(con, "scanner_state", "status_detail", "TEXT")
        _ensure_col(con, "detections", "ocr_text", "TEXT")
        _ensure_col(con, "detections", "ocr_state", "TEXT")
        _ensure_col(con, "detections", "ocr_conf", "REAL")
    except Exception:
        pass


def get_setting(con: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    return row[0]


def set_setting(con: sqlite3.Connection, key: str, value: str):
    con.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


def q1(con: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()): 
    return con.execute(sql, params).fetchone()


def qall(con: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()): 
    return con.execute(sql, params).fetchall()


def qexec(con: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> None:
    con.execute(sql, params)
    con.commit()
