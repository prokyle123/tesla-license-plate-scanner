import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .db import get_setting, qall, qexec, set_setting
from .util import now_utc_str


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _int_setting(con: sqlite3.Connection, key: str, default: int) -> int:
    try:
        return int(float(get_setting(con, key, str(default)) or default))
    except Exception:
        return int(default)


def _bool_setting(con: sqlite3.Connection, key: str, default: bool) -> bool:
    return _truthy(get_setting(con, key, "1" if default else "0"))


def _safe_unlink(path: str) -> int:
    try:
        if os.path.isfile(path):
            sz = os.path.getsize(path)
            os.remove(path)
            return int(sz)
    except Exception:
        pass
    return 0


def compute_storage_stats(cfg, con: sqlite3.Connection) -> Dict[str, Any]:
    static_root = cfg.paths.static_dir
    det_dir = os.path.join(static_root, "detections")
    obj_dir = os.path.join(static_root, "objects")
    db_path = cfg.paths.db_path
    tesla_root = cfg.paths.teslacam_root

    def _dir_stats(root: str) -> Dict[str, int]:
        files = 0
        total = 0
        if not os.path.isdir(root):
            return {"files": 0, "bytes": 0}
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    files += 1
                    total += os.path.getsize(fp)
                except Exception:
                    continue
        return {"files": files, "bytes": total}

    det = _dir_stats(det_dir)
    obj = _dir_stats(obj_dir)
    db_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    try:
        du = shutil.disk_usage(tesla_root if os.path.exists(tesla_root) else static_root)
        disk = {"total": int(du.total), "used": int(du.used), "free": int(du.free)}
    except Exception:
        disk = {"total": 0, "used": 0, "free": 0}

    v = qall(con, "SELECT status, COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS bytes FROM videos GROUP BY status")
    by_status = {r["status"]: {"count": int(r["n"] or 0), "bytes": int(r["bytes"] or 0)} for r in v}
    total_vid = qall(con, "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS bytes FROM videos")
    total_vid = dict(total_vid[0]) if total_vid else {"n": 0, "bytes": 0}

    return {
        "static_detections": det,
        "static_objects": obj,
        "db_bytes": int(db_bytes),
        "disk": disk,
        "videos_total": {"count": int(total_vid.get("n", 0) or 0), "bytes": int(total_vid.get("bytes", 0) or 0)},
        "videos_by_status": by_status,
        "auto_cleanup_enabled": _bool_setting(con, "auto_cleanup_enabled", True),
        "generated_retention_days": _int_setting(con, "generated_retention_days", 14),
        "event_retention_days": _int_setting(con, "event_retention_days", 30),
        "delete_source_clips": _bool_setting(con, "delete_source_clips", False),
        "source_clip_retention_days": _int_setting(con, "source_clip_retention_days", 0),
        "cleanup_last_run_utc": get_setting(con, "cleanup_last_run_utc", "") or "",
    }


def run_storage_cleanup(cfg, con: sqlite3.Connection, *, force: bool = False) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not force:
        last = get_setting(con, "cleanup_last_run_utc", "") or ""
        try:
            if last:
                dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                if (now - dt).total_seconds() < 6 * 3600:
                    return {"skipped": True, "reason": "recently-ran", **compute_storage_stats(cfg, con)}
        except Exception:
            pass

    generated_days = _int_setting(con, "generated_retention_days", 14)
    event_days = _int_setting(con, "event_retention_days", 30)
    delete_source = _bool_setting(con, "delete_source_clips", False)
    source_days = _int_setting(con, "source_clip_retention_days", 0)

    bytes_freed = 0
    files_deleted = 0
    source_deleted = 0
    db_rows_deleted = 0

    cutoff_ts = None
    if generated_days > 0:
        cutoff_ts = (now - timedelta(days=generated_days)).timestamp()
        for rel_root in ("detections", "objects"):
            abs_root = os.path.join(cfg.paths.static_dir, rel_root)
            if not os.path.isdir(abs_root):
                continue
            for dirpath, _dirnames, filenames in os.walk(abs_root):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        if os.path.getmtime(fp) < cutoff_ts:
                            bytes_freed += _safe_unlink(fp)
                            files_deleted += 1
                    except Exception:
                        continue

    if event_days > 0:
        cutoff = (now - timedelta(days=event_days)).strftime("%Y-%m-%d %H:%M:%S")
        before = qall(con, "SELECT COUNT(*) AS n FROM scanner_events WHERE ts_local < ?", (cutoff,))
        n_before = int(before[0]["n"]) if before else 0
        qexec(con, "DELETE FROM scanner_events WHERE ts_local < ?", (cutoff,))
        db_rows_deleted += n_before

    if delete_source and source_days > 0:
        cutoff_epoch = int((now - timedelta(days=source_days)).timestamp())
        rows = qall(
            con,
            """
            SELECT id, path FROM videos
            WHERE mtime IS NOT NULL AND mtime < ? AND status IN ('scanned','done','error')
            ORDER BY mtime ASC
            LIMIT 5000
            """,
            (cutoff_epoch,),
        )
        for row in rows:
            path = row["path"]
            try:
                if path and os.path.abspath(path).startswith(os.path.abspath(cfg.paths.teslacam_root)) and os.path.exists(path):
                    bytes_freed += _safe_unlink(path)
                    source_deleted += 1
                qexec(con, "DELETE FROM videos WHERE id=?", (int(row["id"]),))
                db_rows_deleted += 1
            except Exception:
                continue

    try:
        con.execute("VACUUM")
    except Exception:
        pass

    set_setting(con, "cleanup_last_run_utc", now_utc_str())
    stats = compute_storage_stats(cfg, con)
    stats.update(
        {
            "skipped": False,
            "files_deleted": files_deleted,
            "bytes_freed": bytes_freed,
            "source_deleted": source_deleted,
            "db_rows_deleted": db_rows_deleted,
        }
    )
    return stats
