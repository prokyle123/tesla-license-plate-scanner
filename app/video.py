import mimetypes
import os
from typing import Optional, Tuple

from flask import Response, abort, request

def _parse_range(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    # Supports Range header: "bytes=start-end"
    if not range_header or not range_header.startswith("bytes="):
        return None
    try:
        rng = range_header.split("=", 1)[1].strip()
        if "," in rng:
            rng = rng.split(",", 1)[0].strip()
        start_s, end_s = rng.split("-", 1)
        if start_s == "":
            # suffix bytes: "-N" means last N bytes
            suffix = int(end_s)
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
        if start > end or start < 0:
            return None
        end = min(end, file_size - 1)
        return start, end
    except Exception:
        return None

def send_file_partial(abs_path: str, download_name: Optional[str] = None) -> Response:
    if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
        abort(404)
    file_size = os.path.getsize(abs_path)
    range_header = request.headers.get("Range", "")
    byte_range = _parse_range(range_header, file_size)
    mime, _ = mimetypes.guess_type(abs_path)
    mime = mime or "application/octet-stream"
    if not byte_range:
        # Send full file
        def generate():
            with open(abs_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        resp = Response(generate(), mimetype=mime)
        resp.headers["Content-Length"] = str(file_size)
        return resp
    # Send ranged bytes for video seek
    start, end = byte_range
    length = end - start + 1
    def generate_range():
        with open(abs_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    resp = Response(generate_range(), status=206, mimetype=mime)
    resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(length)
    if download_name:
        resp.headers["Content-Disposition"] = f'inline; filename="{download_name}"'
    return resp
