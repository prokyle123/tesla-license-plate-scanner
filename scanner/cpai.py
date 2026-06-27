import time
from dataclasses import dataclass
from typing import Any, Dict, List

import requests


@dataclass
class CPaiResult:
    ok: bool
    predictions: List[Dict[str, Any]]
    raw: Dict[str, Any]
    latency_ms: int


class CPaiClient:
    def __init__(self, base_url: str, model: str, timeout_s: int = 12):
        self.base_url = base_url.rstrip("/")
        self.model = model.strip("/")
        self.timeout_s = timeout_s

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v1/vision/custom/{self.model}"

    def ping(self) -> bool:
        for path in ("/v1/status/ping", "/v1/status", "/v1/ping"):
            try:
                r = requests.get(self.base_url.rstrip("/") + path, timeout=3)
                if r.status_code:
                    return True
            except Exception:
                continue
        return False

    def _post(self, endpoint: str, image_bytes: bytes, min_confidence: float = 0.05, file_field: str = "image") -> CPaiResult:
        t0 = time.time()
        files = {file_field: ("frame.jpg", image_bytes, "image/jpeg")}
        data = {"min_confidence": str(min_confidence)}
        r = requests.post(endpoint, files=files, data=data, timeout=self.timeout_s)
        latency_ms = int((time.time() - t0) * 1000)
        if not r.ok:
            raise RuntimeError(f"CPAI HTTP {r.status_code}: {r.text[:200]}")
        try:
            j = r.json()
        except Exception:
            raise RuntimeError(f"CPAI returned non-JSON: {r.text[:200]}")
        preds = j.get("predictions") or j.get("results") or []
        if isinstance(preds, dict):
            preds = [preds]
        return CPaiResult(ok=True, predictions=preds, raw=j, latency_ms=latency_ms)

    def detect(self, image_bytes: bytes, min_confidence: float = 0.05) -> CPaiResult:
        return self._post(self.endpoint, image_bytes, min_confidence=min_confidence)

    def detect_objects(self, image_bytes: bytes, endpoint: str, min_confidence: float = 0.35) -> CPaiResult:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            url = endpoint
        else:
            url = self.base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        return self._post(url, image_bytes, min_confidence=min_confidence)

    def ocr(self, image_bytes: bytes, endpoint: str = "/v1/vision/ocr") -> CPaiResult:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            url = endpoint
        else:
            url = self.base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        try:
            return self._post(url, image_bytes, min_confidence=0.0, file_field="upload")
        except Exception:
            # Legacy OCR route fallback
            legacy = self.base_url.rstrip("/") + "/v1/image/ocr"
            return self._post(legacy, image_bytes, min_confidence=0.0, file_field="upload")
