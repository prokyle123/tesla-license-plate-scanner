import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_PATH = os.getenv(
    "TESLACAM_PLATE_CONFIG",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json"),
)


def _deep_get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


@dataclass
class AppConfig:
    host: str
    port: int


@dataclass
class PathConfig:
    teslacam_root: str
    db_path: str
    static_dir: str


@dataclass
class ScannerConfig:
    folders: List[str]
    scan_sleep_s: int
    max_videos_per_pass: int
    frame_interval_s: float
    cpai_base_url: str
    cpai_model: str
    cpai_timeout_s: float
    cpai_min_det_conf: float
    store_all_detections: bool
    ocr_enabled: bool
    ocr_min_conf: float
    ocr_cpai_enabled: bool
    ocr_cpai_endpoint: str
    tesseract_cmd: str
    preview_jpeg_quality: int
    object_detection_enabled: bool
    object_endpoint: str
    object_min_conf: float
    object_frame_interval_s: float
    auto_cleanup_enabled: bool
    generated_retention_days: int
    event_retention_days: int
    delete_source_clips: bool
    source_clip_retention_days: int


@dataclass
class Config:
    app: AppConfig
    paths: PathConfig
    scanner: ScannerConfig
    raw: Dict[str, Any]


def load_config(path: Optional[str] = None) -> Config:
    if not path:
        path = DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Config not found: {path}. Copy config.example.json to config.json and edit paths."
        )
    with open(path, "r", encoding="utf-8") as f:
        raw: Dict[str, Any] = json.load(f)

    app = AppConfig(
        host=str(_deep_get(raw, ["app", "host"], "0.0.0.0")),
        port=int(_deep_get(raw, ["app", "port"], 5057)),
    )

    project_root = os.path.dirname(os.path.dirname(path))
    default_static = os.path.join(project_root, "static")
    default_db = os.path.join(project_root, "data", "plates.db")

    paths = PathConfig(
        teslacam_root=str(_deep_get(raw, ["paths", "teslacam_root"], "/mnt/gadget/part1/TeslaCam")),
        db_path=str(_deep_get(raw, ["paths", "db_path"], default_db)),
        static_dir=str(_deep_get(raw, ["paths", "static_dir"], default_static)),
    )

    sc = ScannerConfig(
        folders=list(_deep_get(raw, ["scanner", "folders"], ["RecentClips", "SentryClips", "SavedClips"])),
        scan_sleep_s=int(_deep_get(raw, ["scanner", "scan_sleep_s"], 60)),
        max_videos_per_pass=int(_deep_get(raw, ["scanner", "max_videos_per_pass"], 40)),
        frame_interval_s=float(_deep_get(raw, ["scanner", "frame_interval_s"], 2.0)),
        cpai_base_url=str(_deep_get(raw, ["scanner", "cpai_base_url"], "http://127.0.0.1:32168")),
        cpai_model=str(_deep_get(raw, ["scanner", "cpai_model"], "license-plate")),
        cpai_timeout_s=float(_deep_get(raw, ["scanner", "cpai_timeout_s"], 12.0)),
        cpai_min_det_conf=float(_deep_get(raw, ["scanner", "cpai_min_det_conf"], 0.05)),
        store_all_detections=bool(_deep_get(raw, ["scanner", "store_all_detections"], True)),
        ocr_enabled=bool(_deep_get(raw, ["scanner", "ocr_enabled"], False)),
        ocr_min_conf=float(_deep_get(raw, ["scanner", "ocr_min_conf"], 45.0)),
        ocr_cpai_enabled=bool(_deep_get(raw, ["scanner", "ocr_cpai_enabled"], False)),
        ocr_cpai_endpoint=str(_deep_get(raw, ["scanner", "ocr_cpai_endpoint"], "/v1/vision/ocr")),
        tesseract_cmd=str(_deep_get(raw, ["scanner", "tesseract_cmd"], "")),
        preview_jpeg_quality=int(_deep_get(raw, ["scanner", "preview_jpeg_quality"], 85)),
        object_detection_enabled=bool(_deep_get(raw, ["scanner", "object_detection_enabled"], True)),
        object_endpoint=str(_deep_get(raw, ["scanner", "object_endpoint"], "/v1/vision/detection")),
        object_min_conf=float(_deep_get(raw, ["scanner", "object_min_conf"], 0.35)),
        object_frame_interval_s=float(_deep_get(raw, ["scanner", "object_frame_interval_s"], 10.0)),
        auto_cleanup_enabled=bool(_deep_get(raw, ["scanner", "auto_cleanup_enabled"], True)),
        generated_retention_days=int(_deep_get(raw, ["scanner", "generated_retention_days"], 14)),
        event_retention_days=int(_deep_get(raw, ["scanner", "event_retention_days"], 30)),
        delete_source_clips=bool(_deep_get(raw, ["scanner", "delete_source_clips"], False)),
        source_clip_retention_days=int(_deep_get(raw, ["scanner", "source_clip_retention_days"], 0)),
    )
    return Config(app=app, paths=paths, scanner=sc, raw=raw)
