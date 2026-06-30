import json
import logging
import math
import os
import re
import shutil
import urllib3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


INTERNAL_ROOT = Path(__file__).resolve().parents[2] / "internal"
DEFAULT_OUTPUT_DIR = "output"
IDENTITY_DIR = INTERNAL_ROOT / "identity_store"
MODEL_DIR = INTERNAL_ROOT / "models"
CAMERA_CONFIG_PATH = INTERNAL_ROOT / "cameras" / "config.json"
PERSON_CLASS_ID = 0
MIN_DETECTION_CONFIDENCE = 0.35
MIN_CROP_AREA = 1_800
FRAME_SAMPLE_SECONDS = 2.0
MAX_FRAMES_PER_SOURCE = 450
MAX_CONTENT_FRAMES_PER_SOURCE = 120
MAX_CONTENT_SAMPLES_PER_LABEL = 12
DEFAULT_CONTENT_LOOKBACK_MINUTES = 5
DEFAULT_CONTENT_RETENTION_DAYS = 2
APPEARANCE_MATCH_THRESHOLD = 0.62
TRACK_APPEARANCE_MATCH_THRESHOLD = 0.42
IDENTITY_MATCH_THRESHOLD = 0.62
DUPLICATE_SAMPLE_THRESHOLD = 0.96
TRACK_TIME_WINDOW_SECONDS = 45.0
TRACK_IOU_THRESHOLD = 0.02
TRACK_CENTER_DISTANCE_THRESHOLD = 1.9
SECRET_VALUE = "********"
VEHICLE_LABELS = {"bicycle", "car", "motorcycle", "bus", "train", "truck", "boat"}
ANIMAL_LABELS = {
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
}


@dataclass(frozen=True)
class Observation:
    camera_id: str
    camera_name: str
    timestamp: str
    confidence: float
    bbox: list[int]
    embedding: list[float]
    crop_path: Path
    relative_path: str


@dataclass
class Cluster:
    observations: list[Observation]
    centroid: list[float]
    confidence: float


def _ensure_camera_config_file() -> None:
    CAMERA_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CAMERA_CONFIG_PATH.exists():
        CAMERA_CONFIG_PATH.write_text("[]", encoding="utf-8")


def _read_camera_configs(include_disabled: bool = True) -> list[dict[str, Any]]:
    _ensure_camera_config_file()
    try:
        configs = json.loads(CAMERA_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Camera config file contains invalid JSON.") from exc

    if not isinstance(configs, list):
        raise ValueError("Camera config file must contain a JSON list.")

    normalized = [config for config in configs if isinstance(config, dict)]
    if include_disabled:
        return normalized
    return [config for config in normalized if config.get("enabled", True)]


def _write_camera_configs(configs: list[dict[str, Any]]) -> None:
    _ensure_camera_config_file()
    CAMERA_CONFIG_PATH.write_text(
        json.dumps(configs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _find_camera_index(configs: list[dict[str, Any]], camera_id: str) -> int:
    for index, config in enumerate(configs):
        if config.get("camera_id") == camera_id:
            return index
    raise ValueError("Camera config does not exist.")


def _public_camera_config(config: dict[str, Any]) -> dict[str, Any]:
    public = dict(config)
    for key in list(public):
        if "password" in key.lower():
            public.pop(key, None)
    public["has_password"] = bool(config.get("password"))
    public["has_nvr_password"] = bool(config.get("nvr_password"))
    return public


def _redact_secret(value: str) -> str:
    return re.sub(r":([^:@/\s]+)@", f":{SECRET_VALUE}@", value)


def list_camera_configs(include_disabled: bool = True) -> list[dict[str, Any]]:
    """List saved camera configs without returning passwords."""
    return [_public_camera_config(config) for config in _read_camera_configs(include_disabled)]


def get_camera_config(camera_id: str) -> dict[str, Any]:
    """Get one saved camera config without returning its password."""
    configs = _read_camera_configs()
    return _public_camera_config(configs[_find_camera_index(configs, camera_id)])


def upsert_camera_config(
    camera_id: str,
    name: str | None = None,
    ip: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    recording_source: str | None = None,
    rtsp_url: str | None = None,
    rtsp_path: str | None = None,
    playback_uri: str | None = None,
    onvif_port: int | None = None,
    nvr_host: str | None = None,
    nvr_port: int | None = None,
    nvr_username: str | None = None,
    nvr_password: str | None = None,
    nvr_channel: int | None = None,
    nvr_stream: int | None = None,
    local_path: str | None = None,
    allow_rtsp_fallback: bool | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """
    Create or update a camera config. Passwords are stored for camera access
    but never returned by this function.
    """
    safe_camera_id = _safe_name(camera_id)
    if not safe_camera_id:
        raise ValueError("camera_id is required.")

    configs = _read_camera_configs()
    now = datetime.utcnow().isoformat() + "Z"
    try:
        index = _find_camera_index(configs, safe_camera_id)
        config = dict(configs[index])
        created_at = config.get("created_at") or now
    except ValueError:
        index = -1
        config = {"camera_id": safe_camera_id}
        created_at = now

    updates = {
        "name": name,
        "ip": ip,
        "port": port,
        "username": username,
        "recording_source": recording_source,
        "rtsp_url": rtsp_url,
        "rtsp_path": rtsp_path,
        "playback_uri": playback_uri,
        "onvif_port": onvif_port,
        "nvr_host": nvr_host,
        "nvr_port": nvr_port,
        "nvr_username": nvr_username,
        "nvr_channel": nvr_channel,
        "nvr_stream": nvr_stream,
        "local_path": local_path,
        "allow_rtsp_fallback": allow_rtsp_fallback,
        "enabled": enabled,
    }
    for key, value in updates.items():
        if value is not None:
            config[key] = value

    if password is not None:
        config["password"] = password
    if nvr_password is not None:
        config["nvr_password"] = nvr_password

    config.setdefault("enabled", True)
    config.setdefault("allow_rtsp_fallback", True)
    config["created_at"] = created_at
    config["updated_at"] = now

    if index == -1:
        configs.append(config)
    else:
        configs[index] = config

    _write_camera_configs(configs)
    return _public_camera_config(config)


def delete_camera_config(camera_id: str) -> dict[str, str]:
    """Delete one saved camera config."""
    configs = _read_camera_configs()
    index = _find_camera_index(configs, camera_id)
    deleted = configs.pop(index)
    _write_camera_configs(configs)
    return {"deleted": str(deleted.get("camera_id", camera_id))}


def _camera_by_id() -> dict[str, dict[str, Any]]:
    return {str(config.get("camera_id")): config for config in _read_camera_configs(include_disabled=False)}


def _expand_camera_sources(camera_sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    saved = _camera_by_id()
    if not camera_sources:
        return list(saved.values())

    expanded: list[dict[str, Any]] = []
    for source in camera_sources:
        camera_id = source.get("camera_id") if isinstance(source, dict) else None
        base = dict(saved.get(str(camera_id), {})) if camera_id else {}
        base.update(source)
        expanded.append(base)
    return expanded


def _resolve_internal_dir(path: str) -> Path:
    target = (INTERNAL_ROOT / path).resolve()
    root = INTERNAL_ROOT.resolve()
    if target != root and root not in target.parents:
        raise ValueError("output_dir must stay inside the internal folder.")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _parse_time(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO datetime: {value}") from exc


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "camera"


def _relative_to_internal(path: Path) -> str:
    return path.resolve().relative_to(INTERNAL_ROOT.resolve()).as_posix()


def _configure_logger(output_root: Path, day: str, tool_name: str = "find_person") -> logging.Logger:
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(tool_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    file_handler = logging.FileHandler(
        logs_dir / f"{day}-{tool_name}.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def _load_optional_cv() -> tuple[Any, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "find_person requires OpenCV and NumPy for video analysis. "
            "Install opencv-python and numpy, then retry."
        ) from exc
    return cv2, np


def _load_yolo_model(logger: logging.Logger) -> Any | None:
    try:
        from ultralytics import YOLO  # type: ignore
    except ImportError:
        logger.warning("ultralytics is not installed; person detection is unavailable.")
        return None

    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model = YOLO(str(MODEL_DIR / "yolov8n.pt"))
        device = _select_inference_device()
        if device != "cpu":
            model.to("cuda:0")
        setattr(model, "_find_person_device", device)
        logger.info("YOLO inference device=%s", device)
        return model
    except Exception as exc:
        logger.exception("Failed to load YOLO model: %s", exc)
        return None


def _select_inference_device() -> str | int:
    try:
        import torch  # type: ignore
    except ImportError:
        return "cpu"
    return 0 if torch.cuda.is_available() else "cpu"


def _is_historical_request(end_time: datetime) -> bool:
    now = datetime.now(end_time.tzinfo) if end_time.tzinfo else datetime.now()
    return end_time < now


def _create_onvif_camera(camera: dict[str, Any]) -> Any:
    try:
        import requests
        from onvif import ONVIFCamera  # type: ignore
        from zeep.transports import Transport  # type: ignore
    except ImportError as exc:
        raise RuntimeError("ONVIF playback requires onvif-zeep.") from exc

    session = requests.Session()
    session.verify = False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    transport = Transport(session=session, timeout=10)
    return ONVIFCamera(
        str(camera["ip"]),
        int(camera.get("onvif_port") or 2020),
        str(camera.get("username") or ""),
        str(camera.get("password") or ""),
        transport=transport,
    )


def _onvif_replay_uri(camera: dict[str, Any], logger: logging.Logger) -> tuple[str | None, str | None]:
    try:
        onvif_camera = _create_onvif_camera(camera)
        recording_service = onvif_camera.create_recording_service()
        replay_service = onvif_camera.create_replay_service()
        recordings = recording_service.GetRecordings()
        if not recordings:
            return None, "ONVIF recording service returned no recordings."

        recording = recordings[0]
        recording_token = getattr(recording, "RecordingToken", None)
        if not recording_token and isinstance(recording, dict):
            recording_token = recording.get("RecordingToken")
        if not recording_token:
            return None, "ONVIF recording did not include a recording token."

        request = replay_service.create_type("GetReplayUri")
        request.StreamSetup = {
            "Stream": "RTP-Unicast",
            "Transport": {"Protocol": "RTSP"},
        }
        request.RecordingToken = recording_token
        uri = replay_service.GetReplayUri(request)
        if isinstance(uri, str) and uri.strip():
            return uri, None
        if hasattr(uri, "Uri") and uri.Uri:
            return str(uri.Uri), None
        return None, "ONVIF replay service did not return an RTSP replay URI."
    except Exception as exc:
        logger.warning("ONVIF replay lookup failed for %s: %s", camera.get("camera_id"), exc)
        return None, f"ONVIF replay unavailable: {exc}"


def _format_vigi_time(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y%m%dt%H%M%Sz")


def _vigi_nvr_replay_uri(
    camera: dict[str, Any],
    start_time: datetime,
    end_time: datetime,
) -> tuple[str | None, str | None]:
    host = camera.get("nvr_host")
    channel = camera.get("nvr_channel")
    if not host or channel is None:
        return None, "No NVR host/channel was configured."

    username = urllib.parse.quote(str(camera.get("nvr_username") or camera.get("username") or ""), safe="")
    password = urllib.parse.quote(str(camera.get("nvr_password") or camera.get("password") or ""), safe="")
    credentials = f"{username}:{password}@" if username else ""
    port = int(camera.get("nvr_port") or 554)
    stream = int(camera.get("nvr_stream") or 1)
    start = _format_vigi_time(start_time)
    end = _format_vigi_time(end_time)
    return (
        f"rtsp://{credentials}{host}:{port}/replay/{int(channel)}/{stream}/avm"
        f"?starttime={start}&endtime={end}",
        None,
    )


def _vigi_nvr_live_uri(camera: dict[str, Any]) -> tuple[str | None, str | None]:
    host = camera.get("nvr_host")
    channel = camera.get("nvr_channel")
    if not host or channel is None:
        return None, "No NVR host/channel was configured."

    username = urllib.parse.quote(str(camera.get("nvr_username") or camera.get("username") or ""), safe="")
    password = urllib.parse.quote(str(camera.get("nvr_password") or camera.get("password") or ""), safe="")
    credentials = f"{username}:{password}@" if username else ""
    port = int(camera.get("nvr_port") or 554)
    stream = int(camera.get("nvr_stream") or 1)
    return f"rtsp://{credentials}{host}:{port}/live/{int(channel)}/{stream}/avm", None


def _source_uri(
    camera: dict[str, Any],
    start_time: datetime,
    end_time: datetime,
    logger: logging.Logger,
) -> tuple[str | None, str | None]:
    for key in ("local_path", "video_path", "path", "file"):
        value = camera.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value)
            if not path.is_absolute():
                path = (INTERNAL_ROOT / value).resolve()
            return str(path), None

    rtsp_url = camera.get("rtsp_url") or camera.get("playback_uri")
    if isinstance(rtsp_url, str) and rtsp_url.strip():
        return rtsp_url, None

    recording_source = str(camera.get("recording_source", "")).lower()
    if _is_historical_request(end_time) and (recording_source == "vigi_nvr" or camera.get("nvr_host")):
        return _vigi_nvr_replay_uri(camera, start_time, end_time)
    if recording_source == "vigi_nvr" or camera.get("nvr_host"):
        return _vigi_nvr_live_uri(camera)

    if recording_source == "onvif" or camera.get("onvif_port"):
        replay_uri, warning = _onvif_replay_uri(camera, logger)
        if replay_uri:
            return replay_uri, None
        if _is_historical_request(end_time):
            return None, warning or "ONVIF replay unavailable for historical request."

    if _is_historical_request(end_time):
        return None, (
            "Historical request requires local exported footage, playback_uri, "
            "or ONVIF replay. Live RTSP fallback is disabled for past time ranges."
        )

    ip = camera.get("ip")
    if isinstance(ip, str) and ip.strip() and camera.get("allow_rtsp_fallback", True):
        username = str(camera.get("username") or "")
        password = str(camera.get("password") or "")
        port = int(camera.get("port") or 554)
        rtsp_path = str(camera.get("rtsp_path") or "Streaming/Channels/101").lstrip("/")
        credentials = ""
        if username:
            credentials = username
            if password:
                credentials += f":{password}"
            credentials += "@"
        return f"rtsp://{credentials}{ip.strip()}:{port}/{rtsp_path}", None

    if recording_source == "onvif":
        return None, (
            "ONVIF recording retrieval needs camera-specific ONVIF playback "
            "support and the onvif-zeep/ffmpeg stack. Provide a local_path or "
            "rtsp_url for this tool run."
        )

    return None, "No local_path, video_path, rtsp_url, playback_uri, or ONVIF replay source was available."


def _frame_timestamp(start_time: datetime, frame_index: int, fps: float) -> str:
    seconds = frame_index / fps if fps > 0 else 0
    timestamp = start_time.timestamp() + seconds
    return datetime.fromtimestamp(timestamp, tz=start_time.tzinfo).isoformat()


def _detect_people(model: Any, frame: Any) -> list[tuple[float, list[int]]]:
    device = getattr(model, "_find_person_device", "cpu")
    results = model.predict(
        frame,
        classes=[PERSON_CLASS_ID],
        device=device,
        verbose=False,
    )
    detections: list[tuple[float, list[int]]] = []
    for result in results:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            confidence = float(box.conf[0])
            if confidence < MIN_DETECTION_CONFIDENCE:
                continue
            x1, y1, x2, y2 = [int(round(value)) for value in box.xyxy[0].tolist()]
            if (x2 - x1) * (y2 - y1) < MIN_CROP_AREA:
                continue
            detections.append((confidence, [x1, y1, x2, y2]))
    return detections


def _model_class_names(model: Any) -> dict[int, str]:
    names = getattr(model, "names", {}) or {}
    if isinstance(names, dict):
        return {int(index): str(name) for index, name in names.items()}
    if isinstance(names, list):
        return {index: str(name) for index, name in enumerate(names)}
    return {}


def _normalize_labels(labels: list[str] | None) -> set[str] | None:
    if not labels:
        return None
    normalized = {str(label).strip().lower().replace("_", " ") for label in labels if str(label).strip()}
    return normalized or None


def _content_category(label: str) -> str:
    if label == "person":
        return "people"
    if label in VEHICLE_LABELS:
        return "vehicles"
    if label in ANIMAL_LABELS:
        return "animals"
    return "objects"


def _detect_content(
    model: Any,
    frame: Any,
    labels: set[str] | None = None,
) -> list[tuple[str, str, float, list[int]]]:
    device = getattr(model, "_find_person_device", "cpu")
    results = model.predict(frame, device=device, verbose=False)
    class_names = _model_class_names(model)
    detections: list[tuple[str, str, float, list[int]]] = []
    for result in results:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            confidence = float(box.conf[0])
            if confidence < MIN_DETECTION_CONFIDENCE:
                continue
            class_id = int(box.cls[0])
            label = class_names.get(class_id, str(class_id)).lower()
            if labels is not None and label not in labels:
                continue
            x1, y1, x2, y2 = [int(round(value)) for value in box.xyxy[0].tolist()]
            if (x2 - x1) * (y2 - y1) < MIN_CROP_AREA:
                continue
            detections.append((_content_category(label), label, confidence, [x1, y1, x2, y2]))
    return detections


def _extract_text(cv2: Any, frame: Any) -> list[dict[str, Any]]:
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return []

    try:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        data = pytesseract.image_to_data(rgb, output_type=pytesseract.Output.DICT)
    except Exception:
        return []

    texts: list[dict[str, Any]] = []
    count = len(data.get("text", []))
    for index in range(count):
        text = str(data["text"][index]).strip()
        if len(text) < 3:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if confidence < 35:
            continue
        x = int(data["left"][index])
        y = int(data["top"][index])
        w = int(data["width"][index])
        h = int(data["height"][index])
        texts.append({
            "text": text,
            "confidence": round(confidence / 100.0, 3),
            "bbox": [x, y, x + w, y + h],
        })
    return texts[:40]


def _crop_quality(cv2: Any, np: Any, crop: Any) -> float:
    if crop is None or crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur_score = min(float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 450.0, 1.0)
    height, width = crop.shape[:2]
    area_score = min((height * width) / 80_000.0, 1.0)
    brightness = float(np.mean(gray)) / 255.0
    exposure_score = 1.0 - min(abs(brightness - 0.5) * 1.7, 0.7)
    return max(0.0, min(1.0, (blur_score * 0.45) + (area_score * 0.35) + (exposure_score * 0.2)))


def _embedding(cv2: Any, np: Any, crop: Any) -> list[float]:
    resized = cv2.resize(crop, (96, 192), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    features: list[float] = []

    for segment in np.array_split(hsv, 4, axis=0):
        hist = cv2.calcHist([segment], [0, 1], None, [24, 12], [0, 180, 0, 256])
        hist = cv2.normalize(hist, hist).flatten()
        features.extend(float(value) for value in hist)

    height, width = crop.shape[:2]
    features.append(float(width / max(height, 1)))
    vector = np.array(features, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if norm:
        vector = vector / norm
    return [float(value) for value in vector.tolist()]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _timestamp_seconds(value: str) -> float:
    return _parse_time(value).timestamp()


def _bbox_iou(left: list[int], right: list[int]) -> float:
    left_x1, left_y1, left_x2, left_y2 = left
    right_x1, right_y1, right_x2, right_y2 = right
    inter_x1 = max(left_x1, right_x1)
    inter_y1 = max(left_y1, right_y1)
    inter_x2 = min(left_x2, right_x2)
    inter_y2 = min(left_y2, right_y2)
    inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
    if inter_area <= 0:
        return 0.0
    left_area = max(0, left_x2 - left_x1) * max(0, left_y2 - left_y1)
    right_area = max(0, right_x2 - right_x1) * max(0, right_y2 - right_y1)
    union = left_area + right_area - inter_area
    return inter_area / union if union else 0.0


def _bbox_center_distance_ratio(left: list[int], right: list[int]) -> float:
    left_cx = (left[0] + left[2]) / 2
    left_cy = (left[1] + left[3]) / 2
    right_cx = (right[0] + right[2]) / 2
    right_cy = (right[1] + right[3]) / 2
    distance = math.hypot(left_cx - right_cx, left_cy - right_cy)
    left_diag = math.hypot(max(1, left[2] - left[0]), max(1, left[3] - left[1]))
    right_diag = math.hypot(max(1, right[2] - right[0]), max(1, right[3] - right[1]))
    return distance / max((left_diag + right_diag) / 2, 1)


def _track_match_score(observation: Observation, cluster: Cluster) -> float:
    same_camera_observations = [
        item for item in cluster.observations
        if item.camera_id == observation.camera_id
    ]
    if not same_camera_observations:
        return 0.0

    observation_time = _timestamp_seconds(observation.timestamp)
    best_score = 0.0
    for item in same_camera_observations[-8:]:
        delta = abs(observation_time - _timestamp_seconds(item.timestamp))
        if delta > TRACK_TIME_WINDOW_SECONDS:
            continue
        iou = _bbox_iou(observation.bbox, item.bbox)
        distance_ratio = _bbox_center_distance_ratio(observation.bbox, item.bbox)
        distance_score = max(0.0, 1.0 - (distance_ratio / TRACK_CENTER_DISTANCE_THRESHOLD))
        time_score = max(0.0, 1.0 - (delta / TRACK_TIME_WINDOW_SECONDS))
        best_score = max(best_score, (iou * 0.45) + (distance_score * 0.35) + (time_score * 0.2))
    return best_score


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    width = len(vectors[0])
    averaged = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(width)]
    norm = math.sqrt(sum(value * value for value in averaged))
    return [value / norm for value in averaged] if norm else averaged


def _cluster_observations(observations: list[Observation]) -> list[Cluster]:
    clusters: list[Cluster] = []
    sorted_observations = sorted(observations, key=lambda item: item.timestamp)
    for observation in sorted_observations:
        best_cluster: Cluster | None = None
        best_score = 0.0
        for cluster in clusters:
            appearance = _cosine_similarity(observation.embedding, cluster.centroid)
            track_score = _track_match_score(observation, cluster)
            is_match = (
                appearance >= APPEARANCE_MATCH_THRESHOLD
                or (
                    appearance >= TRACK_APPEARANCE_MATCH_THRESHOLD
                    and track_score >= 0.38
                )
                or (
                    appearance >= 0.25
                    and track_score >= 0.62
                )
            )
            score = max(appearance, track_score)
            if is_match and score > best_score:
                best_cluster = cluster
                best_score = score

        if best_cluster is not None:
            best_cluster.observations.append(observation)
            best_cluster.centroid = _centroid([item.embedding for item in best_cluster.observations])
            best_cluster.confidence = _cluster_confidence(best_cluster.observations, best_cluster.centroid)
        else:
            clusters.append(Cluster(
                observations=[observation],
                centroid=observation.embedding,
                confidence=observation.confidence,
            ))
    return _merge_similar_clusters(clusters)


def _merge_similar_clusters(clusters: list[Cluster]) -> list[Cluster]:
    changed = True
    while changed:
        changed = False
        merged: list[Cluster] = []
        consumed: set[int] = set()
        for index, cluster in enumerate(clusters):
            if index in consumed:
                continue
            current = cluster
            for candidate_index in range(index + 1, len(clusters)):
                if candidate_index in consumed:
                    continue
                candidate = clusters[candidate_index]
                similarity = _cosine_similarity(current.centroid, candidate.centroid)
                if similarity < APPEARANCE_MATCH_THRESHOLD:
                    continue
                observations = current.observations + candidate.observations
                centroid = _centroid([item.embedding for item in observations])
                current = Cluster(
                    observations=observations,
                    centroid=centroid,
                    confidence=_cluster_confidence(observations, centroid),
                )
                consumed.add(candidate_index)
                changed = True
            merged.append(current)
        clusters = merged
    return clusters


def _cluster_confidence(observations: list[Observation], centroid: list[float]) -> float:
    if not observations:
        return 0.0
    detection_quality = sum(item.confidence for item in observations) / len(observations)
    consistency = sum(_cosine_similarity(item.embedding, centroid) for item in observations) / len(observations)
    sample_score = min(len(observations) / 5.0, 1.0)
    return round(max(0.0, min(1.0, detection_quality * 0.45 + consistency * 0.4 + sample_score * 0.15)), 3)


def _identity_path(day: str) -> Path:
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    return IDENTITY_DIR / f"{day}.json"


def _load_identity_store(day: str) -> dict[str, Any]:
    path = _identity_path(day)
    if not path.exists():
        return {"date": day, "people": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"date": day, "people": []}
    if not isinstance(data, dict) or not isinstance(data.get("people"), list):
        return {"date": day, "people": []}
    return data


def _save_identity_store(day: str, store: dict[str, Any]) -> None:
    path = _identity_path(day)
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _next_person_id(store: dict[str, Any]) -> str:
    highest = 0
    for person in store.get("people", []):
        person_id = str(person.get("person_id", ""))
        match = re.fullmatch(r"person_(\d+)", person_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"person_{highest + 1:03d}"


def _assign_identity(day: str, cluster: Cluster, store: dict[str, Any], best_image: str) -> str:
    best_person: dict[str, Any] | None = None
    best_similarity = 0.0
    for person in store.get("people", []):
        centroid = person.get("embedding_centroid")
        if not isinstance(centroid, list):
            continue
        similarity = _cosine_similarity(cluster.centroid, [float(value) for value in centroid])
        if similarity > best_similarity:
            best_similarity = similarity
            best_person = person

    cameras_seen = sorted({item.camera_id for item in cluster.observations})
    timestamps = [item.timestamp for item in cluster.observations]
    if best_person is not None and best_similarity >= IDENTITY_MATCH_THRESHOLD:
        person_id = str(best_person["person_id"])
        previous_count = int(best_person.get("observation_count", 0))
        new_count = previous_count + len(cluster.observations)
        previous_centroid = [float(value) for value in best_person.get("embedding_centroid", cluster.centroid)]
        best_person["embedding_centroid"] = [
            ((previous_centroid[index] * previous_count) + (cluster.centroid[index] * len(cluster.observations))) / new_count
            for index in range(len(cluster.centroid))
        ]
        best_person["observation_count"] = new_count
        best_person["best_image"] = best_person.get("best_image") or best_image
        best_person["last_seen"] = max([str(best_person.get("last_seen", ""))] + timestamps)
        best_person["cameras_seen"] = sorted(set(best_person.get("cameras_seen", [])) | set(cameras_seen))
        best_person["timestamps"] = sorted(set(best_person.get("timestamps", [])) | set(timestamps))[-200:]
        return person_id

    person_id = _next_person_id(store)
    store.setdefault("people", []).append({
        "person_id": person_id,
        "date": day,
        "embedding_centroid": cluster.centroid,
        "best_image": best_image,
        "observation_count": len(cluster.observations),
        "last_seen": max(timestamps),
        "cameras_seen": cameras_seen,
        "timestamps": sorted(timestamps)[-200:],
    })
    return person_id


def _process_camera(
    camera: dict[str, Any],
    start_time: datetime,
    end_time: datetime,
    day_dir: Path,
    output_root: Path,
    model: Any,
    cv2: Any,
    np: Any,
    logger: logging.Logger,
) -> tuple[list[Observation], list[dict[str, Any]]]:
    camera_id = _safe_name(str(camera.get("camera_id") or camera.get("name") or "camera"))
    camera_name = str(camera.get("name") or camera_id)
    warnings: list[dict[str, Any]] = []
    source, warning = _source_uri(camera, start_time, end_time, logger)
    if warning:
        warnings.append({"camera_id": camera_id, "camera_name": camera_name, "warning": warning})
        logger.warning("%s: %s", camera_id, warning)
        return [], warnings
    if source is None:
        return [], warnings

    os.environ.setdefault(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS",
        "rtsp_transport;tcp|stimeout;5000000|rw_timeout;5000000",
    )
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        message = f"Unable to open recording source: {_redact_secret(source)}"
        warnings.append({"camera_id": camera_id, "camera_name": camera_name, "warning": message})
        logger.warning("%s: %s", camera_id, message)
        return [], warnings

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    step = max(1, int(round(fps * FRAME_SAMPLE_SECONDS)))
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    observations: list[Observation] = []
    frame_index = 0
    processed = 0
    saved_embeddings: list[list[float]] = []

    camera_dir = day_dir / "_camera_samples" / camera_id
    camera_dir.mkdir(parents=True, exist_ok=True)
    logger.info("%s: processing source=%s fps=%.2f frames=%s", camera_id, _redact_secret(source), fps, total_frames)

    while processed < MAX_FRAMES_PER_SOURCE:
        ok = capture.grab()
        if not ok:
            break
        if frame_index % step != 0:
            frame_index += 1
            continue

        ok, frame = capture.retrieve()
        frame_index += 1
        if not ok or frame is None:
            continue

        timestamp = _frame_timestamp(start_time, frame_index, fps)
        if _parse_time(timestamp) > end_time:
            break

        processed += 1
        try:
            detections = _detect_people(model, frame)
        except Exception as exc:
            logger.exception("%s: detection failed: %s", camera_id, exc)
            continue

        for detection_confidence, bbox in detections:
            x1, y1, x2, y2 = bbox
            h, w = frame.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            crop = frame[y1:y2, x1:x2]
            quality = _crop_quality(cv2, np, crop)
            if quality < 0.28:
                continue
            embedding = _embedding(cv2, np, crop)
            if any(_cosine_similarity(embedding, existing) >= DUPLICATE_SAMPLE_THRESHOLD for existing in saved_embeddings):
                continue

            safe_time = timestamp.replace(":", "-").replace("+", "_").replace(".", "_")
            crop_path = camera_dir / f"{camera_id}_{safe_time}_{len(observations) + 1}.jpg"
            cv2.imwrite(str(crop_path), crop)
            saved_embeddings.append(embedding)
            confidence = round(max(0.0, min(1.0, detection_confidence * 0.7 + quality * 0.3)), 3)
            observations.append(Observation(
                camera_id=camera_id,
                camera_name=camera_name,
                timestamp=timestamp,
                confidence=confidence,
                bbox=[x1, y1, x2, y2],
                embedding=embedding,
                crop_path=crop_path,
                relative_path=_relative_to_internal(crop_path),
            ))

    capture.release()
    logger.info("%s: processed=%s observations=%s", camera_id, processed, len(observations))
    return observations, warnings


def _cleanup_camera_content(output_root: Path, retention_days: int, logger: logging.Logger) -> dict[str, int]:
    retention_days = max(1, int(retention_days))
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    removed_dirs = 0
    removed_logs = 0
    removed_memories = 0

    for path in output_root.iterdir() if output_root.exists() else []:
        if not path.is_dir():
            continue
        try:
            day = datetime.fromisoformat(path.name).date()
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed_dirs += 1

    logs_dir = output_root / "logs"
    if logs_dir.exists():
        for path in logs_dir.glob("*-camera_content.log"):
            day_text = path.name.split("-camera_content.log", 1)[0]
            try:
                day = datetime.fromisoformat(day_text).date()
            except ValueError:
                continue
            if day < cutoff:
                path.unlink(missing_ok=True)
                removed_logs += 1

    try:
        from core.db import connect, init_db

        init_db()
        with connect() as db:
            result = db.execute(
                """
                DELETE FROM memories
                WHERE tags LIKE ? AND updated_at < ?
                """,
                (
                    "%camera_content%",
                    datetime.combine(cutoff, datetime.min.time(), timezone.utc).isoformat(),
                ),
            )
            removed_memories = int(result.rowcount or 0)
    except Exception as exc:
        logger.warning("camera content memory cleanup failed: %s", exc)

    return {
        "removed_output_dirs": removed_dirs,
        "removed_logs": removed_logs,
        "removed_memories": removed_memories,
        "retention_days": retention_days,
    }


def _save_content_memory(summary: dict[str, Any], start_time: str, end_time: str) -> dict[str, Any] | None:
    try:
        from tools.memory.main import remember
    except Exception:
        return None

    counts = summary.get("counts", {})
    cameras = summary.get("cameras", [])
    lines = [
        f"Camera content scan from {start_time} to {end_time}.",
        f"Cameras scanned: {', '.join(cameras) if cameras else 'none'}.",
        "Counts: " + json.dumps(counts, ensure_ascii=False, sort_keys=True),
    ]
    if summary.get("texts"):
        lines.append("Texts: " + "; ".join(str(item.get("text")) for item in summary["texts"][:20]))
    if summary.get("top_observations"):
        rendered = []
        for item in summary["top_observations"][:20]:
            rendered.append(
                f"{item.get('label')} at {item.get('camera_id')} "
                f"{item.get('timestamp')} ({item.get('confidence')})"
            )
        lines.append("Top observations: " + "; ".join(rendered))

    try:
        return remember(
            title=f"Camera content {start_time[:10]}",
            content="\n".join(lines),
            tags=["camera_content", "surveillance", "auto_generated"],
        )
    except Exception:
        return None


def _process_camera_content(
    camera: dict[str, Any],
    start_time: datetime,
    end_time: datetime,
    day_dir: Path,
    output_root: Path,
    model: Any,
    cv2: Any,
    np: Any,
    logger: logging.Logger,
    labels: set[str] | None,
    include_text: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    camera_id = _safe_name(str(camera.get("camera_id") or camera.get("name") or "camera"))
    camera_name = str(camera.get("name") or camera_id)
    warnings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    source, warning = _source_uri(camera, start_time, end_time, logger)
    if warning:
        warnings.append({"camera_id": camera_id, "camera_name": camera_name, "warning": warning})
        logger.warning("%s: %s", camera_id, warning)
        return observations, texts, warnings
    if source is None:
        return observations, texts, warnings

    os.environ.setdefault(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS",
        "rtsp_transport;tcp|stimeout;5000000|rw_timeout;5000000",
    )
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        message = f"Unable to open recording source: {_redact_secret(source)}"
        warnings.append({"camera_id": camera_id, "camera_name": camera_name, "warning": message})
        logger.warning("%s: %s", camera_id, message)
        return observations, texts, warnings

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    step = max(1, int(round(fps * FRAME_SAMPLE_SECONDS)))
    frame_index = 0
    processed = 0
    label_counts: dict[str, int] = {}
    camera_dir = day_dir / "_content_samples" / camera_id
    camera_dir.mkdir(parents=True, exist_ok=True)
    logger.info("%s: content processing source=%s fps=%.2f", camera_id, _redact_secret(source), fps)

    while processed < MAX_CONTENT_FRAMES_PER_SOURCE:
        ok = capture.grab()
        if not ok:
            break
        if frame_index % step != 0:
            frame_index += 1
            continue

        ok, frame = capture.retrieve()
        frame_index += 1
        if not ok or frame is None:
            continue

        timestamp = _frame_timestamp(start_time, frame_index, fps)
        if _parse_time(timestamp) > end_time:
            break

        processed += 1
        try:
            detections = _detect_content(model, frame, labels)
        except Exception as exc:
            logger.exception("%s: content detection failed: %s", camera_id, exc)
            continue

        if include_text:
            for text_item in _extract_text(cv2, frame):
                text_item.update({
                    "camera_id": camera_id,
                    "camera_name": camera_name,
                    "timestamp": timestamp,
                })
                texts.append(text_item)

        for category, label, detection_confidence, bbox in detections:
            label_counts[label] = label_counts.get(label, 0) + 1
            if label_counts[label] > MAX_CONTENT_SAMPLES_PER_LABEL:
                continue
            x1, y1, x2, y2 = bbox
            h, w = frame.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            crop = frame[y1:y2, x1:x2]
            quality = _crop_quality(cv2, np, crop)
            if quality < 0.2:
                continue

            safe_time = timestamp.replace(":", "-").replace("+", "_").replace(".", "_")
            file_name = f"{camera_id}_{_safe_name(label)}_{safe_time}_{label_counts[label]}.jpg"
            crop_path = camera_dir / file_name
            cv2.imwrite(str(crop_path), crop)
            observations.append({
                "category": category,
                "label": label,
                "camera_id": camera_id,
                "camera_name": camera_name,
                "timestamp": timestamp,
                "confidence": round(float(detection_confidence), 3),
                "bbox": [x1, y1, x2, y2],
                "path": _relative_to_internal(crop_path),
                "media_type": "image",
            })

    capture.release()
    logger.info("%s: content processed=%s observations=%s texts=%s", camera_id, processed, len(observations), len(texts))
    return observations, texts, warnings


def inspect_camera_content(
    camera_sources: list[dict[str, Any]] | None = None,
    start_time: str = "",
    end_time: str = "",
    lookback_minutes: int = DEFAULT_CONTENT_LOOKBACK_MINUTES,
    labels: list[str] | None = None,
    include_text: bool = True,
    save_memory: bool = True,
    retention_days: int = DEFAULT_CONTENT_RETENTION_DAYS,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """
    Inspect camera footage for broad content such as people, vehicles, animals,
    objects, and OCR-readable text. Designed for recurring scheduler runs.
    """
    if bool(start_time) != bool(end_time):
        raise ValueError("Provide both start_time and end_time, or neither.")
    if start_time and end_time:
        parsed_start = _parse_time(start_time)
        parsed_end = _parse_time(end_time)
    else:
        parsed_start = datetime.now(timezone.utc)
        parsed_end = parsed_start + timedelta(minutes=max(1, int(lookback_minutes)))
        start_time = parsed_start.isoformat()
        end_time = parsed_end.isoformat()

    if parsed_end <= parsed_start:
        raise ValueError("end_time must be after start_time.")
    if camera_sources is not None and not isinstance(camera_sources, list):
        raise ValueError("camera_sources must be a list of camera objects.")
    camera_sources = _expand_camera_sources(camera_sources)
    if not camera_sources:
        raise ValueError("No enabled camera configs or camera_sources were provided.")

    day = parsed_start.date().isoformat()
    output_root = _resolve_internal_dir(output_dir)
    day_dir = output_root / day
    day_dir.mkdir(parents=True, exist_ok=True)
    logger = _configure_logger(output_root, day, "camera_content")
    logger.info("inspect_camera_content started cameras=%s start=%s end=%s", len(camera_sources), start_time, end_time)
    cleanup = _cleanup_camera_content(output_root, retention_days, logger)

    warnings: list[dict[str, Any]] = []
    try:
        cv2, np = _load_optional_cv()
    except RuntimeError as exc:
        logger.error(str(exc))
        return {
            "observations": [],
            "texts": [],
            "summary": {"counts": {}, "cameras": []},
            "warnings": [{"warning": str(exc)}],
            "cleanup": cleanup,
            "output_dir": output_dir,
            "log_path": _relative_to_internal(output_root / "logs" / f"{day}-camera_content.log"),
        }

    model = _load_yolo_model(logger)
    if model is None:
        message = "Content detection model is unavailable. Install ultralytics to enable YOLO detection."
        logger.error(message)
        return {
            "observations": [],
            "texts": [],
            "summary": {"counts": {}, "cameras": []},
            "warnings": [{"warning": message}],
            "cleanup": cleanup,
            "output_dir": output_dir,
            "log_path": _relative_to_internal(output_root / "logs" / f"{day}-camera_content.log"),
        }

    observations: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    normalized_labels = _normalize_labels(labels)
    for camera in camera_sources:
        try:
            camera_observations, camera_texts, camera_warnings = _process_camera_content(
                camera,
                parsed_start,
                parsed_end,
                day_dir,
                output_root,
                model,
                cv2,
                np,
                logger,
                normalized_labels,
                include_text,
            )
            observations.extend(camera_observations)
            texts.extend(camera_texts)
            warnings.extend(camera_warnings)
        except Exception as exc:
            camera_id = _safe_name(str(camera.get("camera_id") or camera.get("name") or "camera"))
            logger.exception("%s: camera content processing failed: %s", camera_id, exc)
            warnings.append({"camera_id": camera_id, "warning": str(exc)})

    counts: dict[str, dict[str, int]] = {}
    for observation in observations:
        category = str(observation["category"])
        label = str(observation["label"])
        counts.setdefault(category, {})
        counts[category][label] = counts[category].get(label, 0) + 1

    summary = {
        "counts": counts,
        "cameras": sorted({str(item["camera_id"]) for item in observations} | {str(item["camera_id"]) for item in texts}),
        "top_observations": sorted(observations, key=lambda item: item["confidence"], reverse=True)[:20],
        "texts": texts[:40],
    }
    memory = _save_content_memory(summary, start_time, end_time) if save_memory else None
    logger.info("inspect_camera_content completed observations=%s texts=%s warnings=%s", len(observations), len(texts), len(warnings))
    return {
        "observations": observations,
        "texts": texts,
        "summary": summary,
        "memory": memory,
        "warnings": warnings,
        "cleanup": cleanup,
        "output_dir": output_dir,
        "log_path": _relative_to_internal(output_root / "logs" / f"{day}-camera_content.log"),
    }


def find_person(
    camera_sources: list[dict[str, Any]] | None = None,
    start_time: str = "",
    end_time: str = "",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """
    Find and group people across user-provided property camera recordings.

    camera_sources can include camera_id references to saved configs,
    local_path/video_path for exported footage, or
    rtsp_url/playback_uri for a stream OpenCV can read. Returned media paths are
    relative to the app's internal folder so the chat UI can open them.
    """
    if not start_time or not end_time:
        raise ValueError("start_time and end_time are required.")

    parsed_start = _parse_time(start_time)
    parsed_end = _parse_time(end_time)
    if parsed_end <= parsed_start:
        raise ValueError("end_time must be after start_time.")
    if camera_sources is not None and not isinstance(camera_sources, list):
        raise ValueError("camera_sources must be a list of camera objects.")
    camera_sources = _expand_camera_sources(camera_sources)
    if not camera_sources:
        raise ValueError("No enabled camera configs or camera_sources were provided.")

    day = parsed_start.date().isoformat()
    output_root = _resolve_internal_dir(output_dir)
    day_dir = output_root / day
    day_dir.mkdir(parents=True, exist_ok=True)
    logger = _configure_logger(output_root, day)
    logger.info("find_person started cameras=%s start=%s end=%s", len(camera_sources), start_time, end_time)

    warnings: list[dict[str, Any]] = []
    try:
        cv2, np = _load_optional_cv()
    except RuntimeError as exc:
        logger.error(str(exc))
        return {
            "people": [],
            "warnings": [{"warning": str(exc)}],
            "output_dir": output_dir,
            "log_path": _relative_to_internal(output_root / "logs" / f"{day}-find_person.log"),
        }

    model = _load_yolo_model(logger)
    if model is None:
        message = "Person detection model is unavailable. Install ultralytics to enable YOLO detection."
        logger.error(message)
        return {
            "people": [],
            "warnings": [{"warning": message}],
            "output_dir": output_dir,
            "log_path": _relative_to_internal(output_root / "logs" / f"{day}-find_person.log"),
        }

    observations: list[Observation] = []
    for camera in camera_sources:
        try:
            camera_observations, camera_warnings = _process_camera(
                camera,
                parsed_start,
                parsed_end,
                day_dir,
                output_root,
                model,
                cv2,
                np,
                logger,
            )
            observations.extend(camera_observations)
            warnings.extend(camera_warnings)
        except Exception as exc:
            camera_id = _safe_name(str(camera.get("camera_id") or camera.get("name") or "camera"))
            logger.exception("%s: camera processing failed: %s", camera_id, exc)
            warnings.append({"camera_id": camera_id, "warning": str(exc)})

    clusters = _cluster_observations(observations)
    store = _load_identity_store(day)
    assigned_clusters: dict[str, list[Cluster]] = {}

    for cluster in clusters:
        best_observation = max(cluster.observations, key=lambda item: item.confidence)
        temporary_best = best_observation.relative_path
        person_id = _assign_identity(day, cluster, store, temporary_best)
        assigned_clusters.setdefault(person_id, []).append(cluster)

    people: list[dict[str, Any]] = []
    for person_id, person_clusters in assigned_clusters.items():
        combined_observations = [
            observation
            for cluster in person_clusters
            for observation in cluster.observations
        ]
        combined_centroid = _centroid([item.embedding for item in combined_observations])
        combined_confidence = _cluster_confidence(combined_observations, combined_centroid)
        best_observation = max(combined_observations, key=lambda item: item.confidence)
        person_dir = day_dir / person_id
        person_dir.mkdir(parents=True, exist_ok=True)

        items: list[dict[str, Any]] = []
        best_image = ""
        for observation in sorted(combined_observations, key=lambda item: item.timestamp):
            file_name = observation.crop_path.name
            destination = person_dir / file_name
            if observation.crop_path != destination:
                observation.crop_path.replace(destination)
            relative_path = _relative_to_internal(destination)
            if observation == best_observation:
                best_destination = person_dir / "best.jpg"
                cv2.imwrite(str(best_destination), cv2.imread(str(destination)))
                best_image = _relative_to_internal(best_destination)
            items.append({
                "path": relative_path,
                "media_type": "image",
                "camera_id": observation.camera_id,
                "camera_name": observation.camera_name,
                "timestamp": observation.timestamp,
                "confidence": observation.confidence,
                "bbox": observation.bbox,
            })

        people.append({
            "person_id": person_id,
            "confidence": combined_confidence,
            "best_image": best_image,
            "items": items,
        })

    _save_identity_store(day, store)
    logger.info("find_person completed people=%s observations=%s warnings=%s", len(people), len(observations), len(warnings))
    return {
        "people": sorted(people, key=lambda item: item["person_id"]),
        "warnings": warnings,
        "output_dir": output_dir,
        "log_path": _relative_to_internal(output_root / "logs" / f"{day}-find_person.log"),
    }


if __name__ == "__main__":
    print(json.dumps({
        "usage": (
            "Import find_person and pass camera_sources with local_path/video_path "
            "or rtsp_url plus ISO start_time and end_time."
        )
    }, indent=2))
