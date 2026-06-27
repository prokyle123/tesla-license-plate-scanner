import re
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable, List, Optional, Tuple

from PIL import Image

from .cpai import CPaiClient


@dataclass
class OcrResult:
    text: str
    conf: float


STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO",
    "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY",
}
STATE_ABBRS = set(STATE_NAMES.values())
GENERIC_LABELS = {
    "PLATE", "DAYPLATE", "NIGHTPLATE", "LICENSEPLATE", "LICENCEPLATE", "LICENSE", "LICENCE",
    "CAR", "VEHICLE", "OBJECT", "TAG", "NUMBERPLATE", "USA", "AMERICA", "STATE", "TEMP",
}
AMBIG_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"}
AMBIG_TO_ALPHA = {v: k for k, v in AMBIG_TO_DIGIT.items() if v in {"0", "1", "2", "5", "8", "6", "7"}}
STATE_PATTERNS = {
    "IN": [r"[A-Z]{2}\d{4}", r"[A-Z]{3}\d{3,4}", r"\d[A-Z]{3}\d{3}", r"[A-Z0-9]{6,7}"],
    "IL": [r"[A-Z]{2}\d{5}", r"[A-Z]{3}\d{4}", r"[A-Z0-9]{6,7}"],
    "WI": [r"[A-Z]{3}\d{3,4}", r"[A-Z0-9]{6,7}"],
    "OH": [r"[A-Z]{3}\d{4}", r"[A-Z0-9]{6,7}"],
    "MI": [r"[A-Z]{3}\d{4}", r"[A-Z0-9]{6,7}"],
}
GENERIC_PATTERNS = [r"[A-Z]{1,3}\d{3,5}", r"\d[A-Z]{3}\d{3}", r"[A-Z0-9]{5,8}"]


def _clean_plate_text(s: str) -> str:
    s = (s or "").upper().strip()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s[:10]


def is_generic_plate_label(s: str) -> bool:
    t = _clean_plate_text(s)
    if not t:
        return False
    if t in GENERIC_LABELS:
        return True
    if t in STATE_ABBRS or t in STATE_NAMES:
        return True
    if t.endswith("PLATE") or t.startswith("PLATE"):
        return True
    return False


def _strip_state_words(raw: str) -> str:
    s = (raw or "").upper()
    for name, abbr in STATE_NAMES.items():
        s = s.replace(name, " ")
        s = re.sub(rf"{abbr}", " ", s)
    return s


def _candidate_variants(text: str, state: Optional[str] = None) -> Iterable[str]:
    raw = _strip_state_words(text)
    t = _clean_plate_text(raw)
    if not t or is_generic_plate_label(t):
        return []
    out = {t}

    # Ambiguous single-char replacements in both directions.
    chars = list(t)
    for i, ch in enumerate(chars):
        if ch in AMBIG_TO_DIGIT:
            repl = chars.copy(); repl[i] = AMBIG_TO_DIGIT[ch]; out.add("".join(repl))
        if ch in AMBIG_TO_ALPHA:
            repl = chars.copy(); repl[i] = AMBIG_TO_ALPHA[ch]; out.add("".join(repl))

    # Pattern-driven all-digit/all-alpha expectations for common plate shapes.
    patterns = STATE_PATTERNS.get(state or "", []) + GENERIC_PATTERNS
    for cand in list(out):
        for pat in patterns[:3]:
            if len(cand) < 5 or len(cand) > 8:
                continue
            if re.fullmatch(r"[A-Z]{2}\d{4}", cand):
                out.add(cand)
            if re.fullmatch(r"[A-Z]{3}\d{4}", cand):
                out.add(cand)
    return list(out)


def _score_plate_candidate(text: str, conf: Optional[float], source: str = "local", state: Optional[str] = None) -> float:
    t = _clean_plate_text(text)
    if not t or is_generic_plate_label(t):
        return -10.0

    score = 0.0
    score += max(0.0, 9.0 - abs(len(t) - 6.5))
    has_alpha = bool(re.search(r"[A-Z]", t))
    has_digit = bool(re.search(r"\d", t))
    if has_alpha:
        score += 1.5
    if has_digit:
        score += 3.0
    if 5 <= len(t) <= 8:
        score += 1.5

    if re.fullmatch(r"[A-Z]+", t):
        score -= 4.5
    elif re.fullmatch(r"\d+", t):
        score -= 2.0

    for pat in GENERIC_PATTERNS:
        if re.fullmatch(pat, t):
            score += 2.0
            break

    if state:
        for pat in STATE_PATTERNS.get(state, []):
            if re.fullmatch(pat, t):
                score += 2.25
                break

    # Prefer a balanced mix like US plates.
    transitions = sum(1 for i in range(1, len(t)) if t[i].isdigit() != t[i-1].isdigit())
    score += min(1.2, transitions * 0.4)

    # Penalize impossible looking values.
    if len(set(t)) <= 2:
        score -= 2.5
    if t.endswith("00") and len(t) <= 5:
        score -= 1.0

    if conf is not None:
        score += max(0.0, min(float(conf), 100.0) / 20.0)
    if source == "cpai":
        score += 0.75
    return score


def _pil_variants(pil_image):
    from PIL import ImageEnhance, ImageFilter, ImageOps

    img = pil_image.convert("L")
    w, h = img.size
    scale = 14 if max(w, h) < 120 else 10 if max(w, h) < 180 else 7 if max(w, h) < 280 else 5
    base = img.resize((max(16, w * scale), max(16, h * scale)))
    base = ImageOps.autocontrast(base)

    bw, bh = base.size
    mx = max(1, int(bw * 0.01))
    my = max(1, int(bh * 0.05))
    if bw > mx * 2 and bh > my * 2:
        base = base.crop((mx, my, bw - mx, bh - my))

    variants: List = []
    variants.append(base)
    variants.append(base.filter(ImageFilter.SHARPEN))
    variants.append(ImageEnhance.Contrast(base).enhance(2.8).filter(ImageFilter.SHARPEN))
    variants.append(ImageEnhance.Sharpness(base).enhance(3.6))
    variants.append(ImageEnhance.Brightness(base).enhance(1.05))
    variants.append(ImageOps.equalize(base))

    for thr in (80, 95, 110, 125, 140, 155, 170, 185, 200, 215):
        b = base.point(lambda x, t=thr: 255 if x > t else 0)
        variants.append(b)
        variants.append(ImageOps.invert(b))
    return variants


def _cv_variants(pil_image):
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return []

    gray = pil_image.convert("L")
    arr = np.array(gray)
    h, w = arr.shape[:2]
    scale = 14 if max(w, h) < 120 else 10 if max(w, h) < 180 else 7 if max(w, h) < 280 else 5
    arr = cv2.resize(arr, (max(16, w * scale), max(16, h * scale)), interpolation=cv2.INTER_CUBIC)
    arr = cv2.bilateralFilter(arr, 5, 50, 50)

    variants = []
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cimg = clahe.apply(arr)
        variants.append(cimg)
    except Exception:
        cimg = arr

    for block, c in ((25, 7), (35, 9), (45, 11), (55, 13)):
        try:
            th = cv2.adaptiveThreshold(cimg, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, c)
            variants.append(th)
            variants.append(255 - th)
        except Exception:
            pass
    try:
        _, otsu = cv2.threshold(cimg, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(otsu)
        variants.append(255 - otsu)
    except Exception:
        pass

    return [Image.fromarray(v) for v in variants]


def _variants(pil_image):
    seen = []
    out = []
    for v in list(_pil_variants(pil_image)) + list(_cv_variants(pil_image)):
        sig = (v.size, hash(v.tobytes()[:1024]))
        if sig in seen:
            continue
        seen.append(sig)
        out.append(v)
    return out


def _ocr_pass(img, config: str):
    import pytesseract  # type: ignore

    candidates = []
    timeout_s = 7
    try:
        data = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT, timeout=timeout_s)
        texts = data.get("text") or []
        confs = data.get("conf") or []
        parts = [t.strip() for t in texts if t and str(t).strip()]
        joined = _clean_plate_text("".join(parts))
        cvals = []
        for c in confs:
            try:
                f = float(c)
                if f >= 0:
                    cvals.append(f)
            except Exception:
                pass
        avg_conf = (sum(cvals) / len(cvals)) if cvals else None
        if joined and not is_generic_plate_label(joined):
            candidates.append((joined, avg_conf, "local"))
        raw_parts = " ".join(parts).upper()
        for token in re.findall(r"[A-Z0-9]{4,10}", raw_parts):
            token = _clean_plate_text(token)
            if token and not is_generic_plate_label(token):
                candidates.append((token, avg_conf, "local"))
    except Exception:
        pass

    try:
        raw = pytesseract.image_to_string(img, config=config, timeout=timeout_s) or ""
        for token in re.findall(r"[A-Z0-9]{4,10}", raw.upper()):
            token = _clean_plate_text(token)
            if token and not is_generic_plate_label(token):
                candidates.append((token, None, "local"))
    except Exception:
        pass
    return candidates


def _cpai_ocr_candidates(pil_image, base_url: Optional[str], endpoint: str) -> List[Tuple[str, Optional[float], str]]:
    if not base_url:
        return []
    try:
        buf = BytesIO()
        pil_image.convert("RGB").save(buf, format="JPEG", quality=94)
        client = CPaiClient(base_url=base_url, model="license-plate", timeout_s=20)
        res = client.ocr(buf.getvalue(), endpoint=endpoint)
        preds = list(res.predictions or [])
        def _key(p):
            y = p.get("y_min", p.get("top", p.get("y", 0))) or 0
            x = p.get("x_min", p.get("left", p.get("x", 0))) or 0
            return (float(y), float(x))
        preds.sort(key=_key)
        out: List[Tuple[str, Optional[float], str]] = []
        tokens: List[str] = []
        confs: List[float] = []
        for p in preds:
            raw = p.get("text") or p.get("label") or p.get("value") or p.get("name") or ""
            if not isinstance(raw, str):
                continue
            token = _clean_plate_text(raw)
            if not token or is_generic_plate_label(token):
                continue
            try:
                conf = float(p.get("confidence") or p.get("conf") or 0.0)
            except Exception:
                conf = 0.0
            tokens.append(token)
            confs.append(conf)
            out.append((token, conf, "cpai"))
        if tokens:
            joined = _clean_plate_text("".join(tokens))
            avg_conf = (sum(confs) / len(confs)) if confs else None
            if joined and not is_generic_plate_label(joined):
                out.insert(0, (joined, avg_conf, "cpai"))
        return out
    except Exception:
        return []


def _guess_state_from_text(raw_txt: str) -> Optional[str]:
    upper = " ".join((raw_txt or "").upper().split())
    for name, abbr in STATE_NAMES.items():
        if name in upper:
            return abbr
    for token in re.findall(r"\b[A-Z]{2}\b", upper):
        if token in STATE_ABBRS:
            return token
    return None


def ocr_plate(pil_image) -> Optional[OcrResult]:
    plate, _state, conf = ocr_plate_and_metadata(pil_image)
    if not plate:
        return None
    return OcrResult(text=plate, conf=float(conf or 0.0))


def ocr_plate_and_metadata(
    pil_image,
    cpai_base_url: Optional[str] = None,
    cpai_ocr_endpoint: str = "/v1/vision/ocr",
) -> Tuple[Optional[str], Optional[str], Optional[float]]:
    try:
        import pytesseract  # type: ignore
    except Exception:
        pytesseract = None

    plate_cfgs = [
        "--oem 1 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "--oem 1 --psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "--oem 1 --psm 13 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "--oem 1 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "--oem 1 --psm 11 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    ]

    best_text: Optional[str] = None
    best_conf: Optional[float] = None
    best_state: Optional[str] = None
    best_score = -999.0

    variants = _variants(pil_image)
    state_hint = None
    if pytesseract is not None:
        try:
            meta_img = variants[0] if variants else pil_image
            raw_txt = pytesseract.image_to_string(meta_img, config="--oem 1 --psm 6", timeout=7) or ""
            state_hint = _guess_state_from_text(raw_txt)
        except Exception:
            pass

    if pytesseract is not None:
        for variant in variants:
            for cfg in plate_cfgs:
                for cand, conf, source in _ocr_pass(variant, cfg):
                    for normalized in _candidate_variants(cand, state_hint):
                        score = _score_plate_candidate(normalized, conf, source=source, state=state_hint)
                        if score > best_score:
                            best_score = score
                            best_text = _clean_plate_text(normalized)
                            best_conf = conf
                            best_state = state_hint

    cpai_candidates = _cpai_ocr_candidates(variants[0] if variants else pil_image, cpai_base_url, cpai_ocr_endpoint)
    for cand, conf, source in cpai_candidates:
        for normalized in _candidate_variants(cand, state_hint):
            score = _score_plate_candidate(normalized, conf, source=source, state=state_hint)
            if score > best_score:
                best_score = score
                best_text = _clean_plate_text(normalized)
                best_conf = conf
                best_state = state_hint

    if best_text and (best_state is None) and len(best_text) > 8:
        best_state = "TEMP"

    if not best_text or best_score < 4.8:
        return None, best_state, best_conf
    return best_text, best_state, best_conf
