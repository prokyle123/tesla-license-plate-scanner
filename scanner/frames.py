import os
from dataclasses import dataclass
from typing import Generator, Optional, Tuple

@dataclass
class Frame:
    t_s: float
    jpeg: bytes
    width: int
    height: int

def iter_frames_mp4(path: str, every_s: float = 5.0, max_frames: Optional[int] = None) -> Generator[Frame, None, None]:
    try:
        import cv2
    except Exception as e:
        raise RuntimeError(
            "OpenCV (cv2) is not installed. Install with: sudo apt-get install -y python3-opencv"
        ) from e
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    if fps <= 0:
        fps = 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = (total_frames / fps) if total_frames > 0 else None
    t = 0.0
    yielded = 0
    while True:
        if duration_s is not None and t > duration_s:
            break
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        h, w = frame.shape[:2]
        ok2, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok2:
            yielded += 1
            yield Frame(t_s=t, jpeg=bytes(buf), width=w, height=h)
            if max_frames and yielded >= max_frames:
                break
        t += float(every_s)
    cap.release()

def crop_from_frame_jpeg(jpeg_bytes: bytes, bbox_xywh: Tuple[float, float, float, float]):
    from PIL import Image
    from io import BytesIO
    x, y, w, h = bbox_xywh
    img = Image.open(BytesIO(jpeg_bytes)).convert("RGB")
    W, H = img.size
    left = max(0, int(x))
    top = max(0, int(y))
    right = min(W, int(x + w))
    bottom = min(H, int(y + h))
    return img.crop((left, top, right, bottom))
