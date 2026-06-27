import os
import re
import time
from typing import Optional

def now_utc_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

def now_local_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def safe_name(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s or "")
    s = s.strip("_")
    return (s[:180] or "x")

def parse_seen_from_filename(fn: str) -> Optional[str]:
    # TeslaCam filenames: YYYY-MM-DD_HH-MM-SS-<cam>.mp4
    m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", fn)
    if not m:
        return None
    d = m.group(1)
    t = m.group(2).replace("-", ":")
    return f"{d} {t}"

def parse_cam_from_filename(fn: str) -> Optional[str]:
    base = os.path.basename(fn)
    if base.lower().endswith(".mp4"):
        base = base[:-4]
    parts = base.split("-")
    if len(parts) < 4:
        return None
    return parts[-1]

def is_mp4(path: str) -> bool:
    return path.lower().endswith(".mp4")
