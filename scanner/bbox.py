import json
from typing import Any, Dict, Optional, Tuple


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def normalize_bbox(pred: Dict[str, Any], img_w: Optional[int] = None, img_h: Optional[int] = None) -> Tuple[float, float, float, float]:
    """Return bbox as top-left x,y,width,height.

    Supports CPAI payload variants:
    - x,y,width,height (top-left)
    - x,y,w,h (top-left)
    - x,y,width,height where x/y are centre coords
    - x_min,y_min,x_max,y_max
    - xmin,ymin,xmax,ymax
    - left,top,right,bottom
    - relative 0..1 values when image size is available
    """
    if not isinstance(pred, dict):
        return 0.0, 0.0, 0.0, 0.0

    # 1) explicit min/max corners
    left = _f(pred.get("left"))
    top = _f(pred.get("top"))
    right = _f(pred.get("right"))
    bottom = _f(pred.get("bottom"))
    if None not in (left, top, right, bottom):
        x1, y1, x2, y2 = left, top, right, bottom
    else:
        x1 = _f(pred.get("x_min"))
        y1 = _f(pred.get("y_min"))
        x2 = _f(pred.get("x_max"))
        y2 = _f(pred.get("y_max"))
        if None in (x1, y1, x2, y2):
            x1 = _f(pred.get("xmin"))
            y1 = _f(pred.get("ymin"))
            x2 = _f(pred.get("xmax"))
            y2 = _f(pred.get("ymax"))

    if None not in (x1, y1, x2, y2):
        # relative values
        if img_w and img_h and max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
            x1 *= img_w
            x2 *= img_w
            y1 *= img_h
            y2 *= img_h
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        return x, y, w, h

    # 2) x/y/width/height or x/y/w/h
    x = _f(pred.get("x"))
    y = _f(pred.get("y"))
    w = _f(pred.get("width"))
    h = _f(pred.get("height"))
    if w is None:
        w = _f(pred.get("w"))
    if h is None:
        h = _f(pred.get("h"))
    if x is None:
        x = _f(pred.get("cx"))
    if y is None:
        y = _f(pred.get("cy"))
    if None in (x, y, w, h):
        return 0.0, 0.0, 0.0, 0.0

    # relative values
    if img_w and img_h and max(abs(x), abs(y), abs(w), abs(h)) <= 1.5:
        x *= img_w
        y *= img_h
        w *= img_w
        h *= img_h

    # Heuristic: treat x/y as centre if top-left variant looks impossible
    if img_w and img_h:
        tl_ok = (0 <= x <= img_w) and (0 <= y <= img_h) and (x + w <= img_w + 2) and (y + h <= img_h + 2)
        cx_ok = (0 <= x - w / 2 <= img_w) and (0 <= y - h / 2 <= img_h) and (x + w / 2 <= img_w + 2) and (y + h / 2 <= img_h + 2)
        if (not tl_ok and cx_ok) or pred.get("center") or pred.get("centre"):
            x = x - w / 2
            y = y - h / 2

    return max(0.0, x), max(0.0, y), max(0.0, w), max(0.0, h)


def bbox_from_extra_json(extra_json: Any, img_w: Optional[int] = None, img_h: Optional[int] = None) -> Tuple[float, float, float, float]:
    try:
        if isinstance(extra_json, str):
            extra_json = json.loads(extra_json)
        if not isinstance(extra_json, dict):
            return 0.0, 0.0, 0.0, 0.0
        cpai = extra_json.get("cpai") or extra_json.get("prediction") or extra_json
        if not isinstance(cpai, dict):
            return 0.0, 0.0, 0.0, 0.0
        return normalize_bbox(cpai, img_w=img_w, img_h=img_h)
    except Exception:
        return 0.0, 0.0, 0.0, 0.0
