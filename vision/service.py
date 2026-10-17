#!/usr/bin/env python3
"""
Vision sidecar: overlay (PR1) and optional metric curb ingest (PR2).
PR4: frame adapter, contour refine, temporal filter, validity metadata, bounded frame store.
PR5: calibration contract, metric quality gates, development vs calibrated modes.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import uvicorn
import yaml
from fastapi import FastAPI, File, Form, UploadFile

from calibration.schema import CalibrationStatus
from calibration_loader import load_all, resolve_calibration_directory
from curb_detector import CurbDetector
from detection_schema import Detection, envelope
from metrics.distance_consistency import DistanceConsistencyFilter
from metrics.measurement import build_metric_measurement
from frame_adapter import (
    CoordinateSpace,
    build_layout_context,
    decode_jpeg_packet,
    map_contour_to_output,
)
from frame_receiver import FrameStore
from coordinate_mapper import fetch_meta, panorama_contour_strip
from temporal_filter import TemporalConfig, TemporalCurbFilter
from vision_ingest_client import build_sensor, post_range_sensors

CAMERA_TO_POS = {
    "left": "LEFT_REAR",
    "right": "RIGHT_REAR",
    "rear": "REAR_CENTER",
    "front": "FRONT_CENTER",
}

ROOT = Path(__file__).resolve().parent


def normalize_jpeg(body: bytes) -> bytes:
    """Java VisionFramePublisher may prefix image part with stray CRLF before SOI."""
    start = body.find(b"\xff\xd8")
    if start < 0:
        return body
    end = body.rfind(b"\xff\xd9")
    if end >= start:
        return body[start : end + 2]
    return body[start:]


def load_config() -> Dict[str, Any]:
    path = os.environ.get("VISION_CONFIG", str(ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["vision"]


CFG = load_config()
MIDDLEWARE = os.environ.get("MIDDLEWARE_URL", CFG.get("middleware_url", "http://127.0.0.1:9091")).rstrip("/")
HOST = os.environ.get("VISION_HOST", CFG["server"]["host"])
PORT = int(os.environ.get("VISION_PORT", CFG["server"]["port"]))
MODE = os.environ.get("VISION_MODE", CFG.get("mode", "overlay_only")).strip().lower()

INPUT_CFG = CFG.get("input", {})
DET_CFG = CFG.get("detection", {})
TEMP_CFG = CFG.get("temporal", {})
_inf = CFG.get("inference", {})
CONF_TH = float(DET_CFG.get("confidence_threshold", _inf.get("confidence_threshold", 0.12)))
DET_CFG = {**DET_CFG, "confidence_threshold": CONF_TH}
_max_fps = float(DET_CFG.get("max_inference_fps", _inf.get("target_hz", 12)))
TARGET_HZ = min(float(_inf.get("target_hz", 12)), _max_fps if _max_fps > 0 else 12.0)
MAX_AGE_MS = int(_inf.get("max_frame_age_ms", 2000))
PANO_W = int(CFG["output"]["width"])
PANO_H = int(CFG["output"]["height"])

CAL_CFG = CFG.get("calibration", {})
CAL_PROFILE = os.environ.get("VISION_CALIBRATION_PROFILE", CAL_CFG.get("profile", "default"))
ALLOW_SYNTHETIC = os.environ.get("VISION_ALLOW_SYNTHETIC", "").strip() in ("1", "true", "yes")
if not ALLOW_SYNTHETIC:
    ALLOW_SYNTHETIC = bool(CAL_CFG.get("allow_synthetic", False))
CAL_DIR, CAL_PROFILE = resolve_calibration_directory(ROOT, CAL_CFG, CAL_PROFILE)
GROUND_Z = float(CAL_CFG.get("ground_plane_z_m", 0.0))
CALIBRATIONS = load_all(CAL_DIR, allow_synthetic=ALLOW_SYNTHETIC)

DIST_CFG = CFG.get("distance", {})
MIN_DIST_M = float(DIST_CFG.get("min_valid_distance_m", 0.05))
MAX_DIST_M = float(DIST_CFG.get("max_valid_distance_m", 5.0))
REQUIRE_VALID_CAL = bool(DIST_CFG.get("require_valid_calibration", True))
ALLOW_SYNTHETIC_METRIC = ALLOW_SYNTHETIC and bool(DIST_CFG.get("allow_synthetic_calibration", False))
METRIC_CFG = CFG.get("metric", {})
REPORT_UNCERTAINTY = bool(METRIC_CFG.get("report_uncertainty", False))
MAX_DIST_JUMP_M = float(METRIC_CFG.get("max_distance_jump_m", 0.75))
METRIC_MODE = os.environ.get("VISION_METRIC_MODE", METRIC_CFG.get("mode", "development")).strip().lower()

VAL_CFG = CFG.get("validation", {})
MIN_DET_CONF = float(VAL_CFG.get("min_detection_confidence", 0.60))

INGEST_CFG = CFG.get("ingest", {})
INGEST_ENABLED = bool(INGEST_CFG.get("enabled", True)) and MODE in ("metric", "both")
INGEST_HZ = float(INGEST_CFG.get("publish_hz", 10.0))
SENSOR_PREFIX = str(INGEST_CFG.get("sensor_id_prefix", "VISION_"))
MIN_PANO_STRIP = int(INGEST_CFG.get("min_pano_strip_px", 40))

DO_OVERLAY = MODE in ("overlay_only", "both")
DO_METRIC = MODE in ("metric", "both")

frames = FrameStore(max_age_ms=MAX_AGE_MS)
meta: Dict[str, Any] = {}
meta_lock = threading.Lock()
detector: CurbDetector | None = None
dist_consistency = DistanceConsistencyFilter(max_jump_m=MAX_DIST_JUMP_M)
temporal = TemporalCurbFilter(
    TemporalConfig(
        enabled=bool(TEMP_CFG.get("enabled", True)),
        smoothing_alpha=float(TEMP_CFG.get("smoothing_alpha", 0.4)),
        stable_frames_required=int(TEMP_CFG.get("stable_frames_required", 2)),
        stale_timeout_ms=int(TEMP_CFG.get("stale_timeout_ms", 500)),
        hold_on_miss_frames=int(TEMP_CFG.get("hold_on_miss_frames", 1)),
    )
)
last_ingest_mono = 0.0
last_overlay_post_mono = 0.0
inference_errors = 0
last_inference_ok_mono = 0.0

app = FastAPI(title="WP5 vision sidecar")


def post_detections(body: dict) -> None:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        MIDDLEWARE + "/api/v1/vision/detections",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=2).read()


def refresh_meta() -> None:
    global meta
    if not bool(INPUT_CFG.get("allow_panorama", True)):
        return
    try:
        m = fetch_meta(MIDDLEWARE)
        with meta_lock:
            meta = m
    except Exception as e:
        print("[vision] meta:", e)


def calibration_status_for(camera: str) -> str:
    cal = CALIBRATIONS.get(camera)
    if cal is None:
        return CalibrationStatus.UNVERIFIED.value
    return cal.calibration_status


def effective_metric_mode() -> str:
    if METRIC_MODE == "calibrated":
        return "calibrated"
    if METRIC_MODE == "disabled":
        return "disabled"
    return "development"


def metric_for_detection(
    confidence: float,
    camera: str,
    cam_contour: List[List[int]],
    image_height: int,
    pano_contour: List[List[int]],
    detection_valid: bool,
    overlay_status: str,
    timestamp_ms: int,
) -> Tuple[Optional[float], bool, Optional[float], Optional[Dict[str, Any]], str, Dict[str, Any], str]:
    """Distance fields and optional ingest sensor; fail-closed when calibration invalid."""
    cal_status = calibration_status_for(camera)
    mode = effective_metric_mode()
    if not detection_valid or overlay_status != "DETECTED" or mode == "disabled":
        return None, False, None, None, cal_status, {}, mode
    cal = CALIBRATIONS.get(camera)
    if cal is None or not cal.valid:
        return None, False, None, None, cal_status, {}, mode

    allow_syn = ALLOW_SYNTHETIC_METRIC and mode == "development"
    if mode == "calibrated" and cal_status != CalibrationStatus.VALIDATED.value:
        return None, False, None, None, cal_status, {}, mode

    pos = CAMERA_TO_POS.get(camera, "")
    if not pos:
        return None, False, None, None, cal_status, {}, mode

    prev_d, last_ts = dist_consistency.last_state(camera)

    m = build_metric_measurement(
        cal,
        camera,
        pos,
        f"{SENSOR_PREFIX}{pos}",
        cam_contour,
        image_height,
        confidence,
        timestamp_ms,
        allow_synthetic=allow_syn,
        min_conf=MIN_DET_CONF,
        min_m=MIN_DIST_M,
        max_m=MAX_DIST_M,
        ground_z_m=GROUND_Z,
        report_uncertainty=REPORT_UNCERTAINTY,
        prev_distance_m=prev_d,
        last_timestamp_ms=last_ts,
    )
    if m.distance_valid:
        ok, reason = dist_consistency.check(camera, m.distance_m, timestamp_ms)
        if not ok:
            m.distance_valid = False
            m.quality.temporal_ok = False
            if reason:
                m.quality.reasons.append(reason)

    extras = m.to_detection_extras()
    sensor: Optional[Dict[str, Any]] = None
    if m.distance_valid:
        strip = panorama_contour_strip(pano_contour, MIN_PANO_STRIP)
        pano_x, pano_w = strip if strip else (None, None)
        sensor = m.to_ingest_sensor(pano_x, pano_w)
    return (
        m.distance_m if m.distance_valid else None,
        m.distance_valid,
        m.uncertainty_m,
        sensor,
        m.calibration_status,
        extras,
        mode,
    )


def aggregate_overlay_status(camera_statuses: Dict[str, str]) -> str:
    if not camera_statuses:
        return "NO_CURB"
    vals = set(camera_statuses.values())
    if "INFERENCE_ERROR" in vals:
        return "INFERENCE_ERROR"
    if "DETECTED" in vals:
        return "DETECTED"
    if "STALE" in vals:
        return "STALE"
    return "NO_CURB"


def inference_loop() -> None:
    global detector, last_ingest_mono, inference_errors, last_inference_ok_mono, last_overlay_post_mono
    while detector is None:
        time.sleep(0.1)
    interval = 1.0 / TARGET_HZ if TARGET_HZ > 0 else 0.1
    ingest_interval = 1.0 / INGEST_HZ if INGEST_ENABLED and INGEST_HZ > 0 else 0.0
    while True:
        time.sleep(interval)
        with meta_lock:
            m = dict(meta) if meta else None
        layout = build_layout_context(m, INPUT_CFG, PANO_W, PANO_H)
        if layout.coordinate_space == CoordinateSpace.PANORAMA_PIXELS and m is None:
            continue
        batch = frames.snapshot_latest()
        if not batch:
            continue
        now_ms = time.time() * 1000.0
        max_age = int(VAL_CFG.get("max_detection_age_ms", MAX_AGE_MS))
        all_det: List[Detection] = []
        ingest_sensors: List[Dict[str, Any]] = []
        camera_statuses: Dict[str, str] = {}
        max_fid = 0
        coord_space = layout.coordinate_space.value
        pano_w = layout.panorama_width
        pano_h = layout.panorama_height
        loop_error = False

        for cam, pkt in batch.items():
            if now_ms - pkt.timestamp_ms > max_age and pkt.timestamp_ms > 0:
                continue
            norm = decode_jpeg_packet(pkt.jpeg, cam, pkt.frame_id, pkt.timestamp_ms, pkt.width, pkt.height)
            if not norm.decode_ok:
                print(f"[vision] frame reject {cam}: {norm.reject_reason}")
                camera_statuses[cam] = "INFERENCE_ERROR"
                loop_error = True
                continue
            raw = detector.detect(norm.bgr, cam)
            if raw.inference_error:
                inference_errors += 1
                loop_error = True
                _, _, _, ost = temporal.update(cam, now_ms, False, 0.0, [], inference_error=True)
                camera_statuses[cam] = ost
                all_det.append(
                    Detection(
                        camera=cam,
                        confidence=0.0,
                        coordinate_space=coord_space,
                        detection_valid=False,
                        overlay_status=ost,
                        calibration_status=calibration_status_for(cam),
                        timestamp_ms=int(pkt.timestamp_ms),
                    )
                )
                continue

            contour_cam = list(raw.contour)
            smooth_contour, conf, det_valid, ost = temporal.update(
                cam, now_ms, raw.found, raw.confidence, contour_cam, inference_error=False
            )
            camera_statuses[cam] = ost

            if not det_valid or ost != "DETECTED":
                all_det.append(
                    Detection(
                        camera=cam,
                        confidence=conf,
                        coordinate_space=coord_space,
                        detection_valid=False,
                        overlay_status=ost,
                        calibration_status=calibration_status_for(cam),
                        timestamp_ms=int(pkt.timestamp_ms),
                    )
                )
                continue

            out_contour, out_space, map_err = map_contour_to_output(layout, cam, smooth_contour)
            if map_err:
                print(f"[vision] map {cam}: {map_err}")
                all_det.append(
                    Detection(
                        camera=cam,
                        confidence=conf,
                        coordinate_space=coord_space,
                        detection_valid=False,
                        overlay_status="NO_CURB",
                        calibration_status=calibration_status_for(cam),
                        timestamp_ms=int(pkt.timestamp_ms),
                    )
                )
                camera_statuses[cam] = "NO_CURB"
                continue

            max_fid = max(max_fid, pkt.frame_id)
            cam_contour = [[int(x), int(y)] for x, y in smooth_contour]

            dist_m: Optional[float] = None
            dist_valid = False
            dist_unc: Optional[float] = None
            sensor: Optional[Dict[str, Any]] = None
            cal_status = calibration_status_for(cam)
            metric_extras: Dict[str, Any] = {}
            metric_mode = effective_metric_mode()
            dist_type = ""
            if DO_METRIC:
                dist_m, dist_valid, dist_unc, sensor, cal_status, metric_extras, metric_mode = metric_for_detection(
                    conf,
                    cam,
                    cam_contour,
                    norm.source_height,
                    out_contour,
                    True,
                    "DETECTED",
                    int(pkt.timestamp_ms),
                )
                dist_type = str(metric_extras.get("distanceType", ""))
                if sensor is not None:
                    ingest_sensors.append(sensor)

            all_det.append(
                Detection(
                    camera=cam,
                    confidence=conf,
                    coordinate_space=out_space.value,
                    contour=out_contour,
                    contour_camera_pixels=cam_contour,
                    image_width=norm.source_width,
                    image_height=norm.source_height,
                    pos=CAMERA_TO_POS.get(cam, ""),
                    bbox=[],
                    distance_m=dist_m,
                    distance_valid=dist_valid,
                    distance_uncertainty_m=dist_unc,
                    detection_valid=True,
                    overlay_status="DETECTED",
                    calibration_status=cal_status,
                    timestamp_ms=int(pkt.timestamp_ms),
                    distance_type=dist_type or metric_extras.get("distanceType", ""),
                    quality=metric_extras.get("quality", {}),
                    metric_mode=metric_mode,
                )
            )

        if not loop_error:
            last_inference_ok_mono = time.monotonic()

        overlay_status = aggregate_overlay_status(camera_statuses)
        if DO_OVERLAY:
            try:
                post_detections(
                    envelope(
                        max_fid,
                        time.monotonic_ns(),
                        pano_w,
                        pano_h,
                        [d for d in all_det if d.detection_valid and d.contour],
                        coordinate_space=coord_space,
                        overlay_status=overlay_status,
                        camera_statuses=camera_statuses,
                    )
                )
                last_overlay_post_mono = time.monotonic()
            except Exception as e:
                print("[vision] post detections:", e)

        if INGEST_ENABLED and ingest_sensors:
            now = time.monotonic()
            if ingest_interval <= 0 or (now - last_ingest_mono) >= ingest_interval:
                last_ingest_mono = now
                try:
                    post_range_sensors(MIDDLEWARE, ingest_sensors)
                except Exception as e:
                    print("[vision] post ingest:", e)


@app.on_event("startup")
def startup() -> None:
    global detector
    refresh_meta()
    detector = CurbDetector(CFG.get("model", {}), DET_CFG)
    threading.Thread(target=inference_loop, daemon=True).start()

    def meta_loop() -> None:
        while True:
            time.sleep(30)
            refresh_meta()

    threading.Thread(target=meta_loop, daemon=True).start()
    cal_valid = [k for k, c in CALIBRATIONS.items() if c.valid]
    synth = [k for k, c in CALIBRATIONS.items() if c.calibration_type == "synthetic"]
    print(
        f"[vision] mode={MODE}  {HOST}:{PORT}  →  {MIDDLEWARE}  "
        f"overlay={DO_OVERLAY} metric={DO_METRIC} ingest={INGEST_ENABLED} "
        f"infer_hz={TARGET_HZ} temporal={temporal.cfg.enabled} "
        f"metric_mode={METRIC_MODE} cal_profile={CAL_PROFILE} allow_synthetic={ALLOW_SYNTHETIC} "
        f"calib_valid={cal_valid or 'none'} synthetic_files={synth or 'none'}"
    )


@app.post("/frame")
async def frame(
    camera: str = Form(...),
    frameId: int = Form(0),
    timestampMs: int = Form(0),
    width: int = Form(640),
    height: int = Form(360),
    image: UploadFile = File(...),
) -> dict:
    body = normalize_jpeg(await image.read())
    cam = camera.strip().lower()
    frames.put(cam, frameId, timestampMs, width, height, body)
    return {"ok": True, "camera": camera, "frameId": frameId}


@app.get("/health")
def health() -> dict:
    snap = frames.snapshot_fresh()
    now = time.monotonic()
    infer_stale = (now - last_inference_ok_mono) > 3.0 if last_inference_ok_mono > 0 else True
    return {
        "ok": True,
        "middleware": MIDDLEWARE,
        "mode": MODE,
        "model": detector is not None,
        "inferenceHz": TARGET_HZ,
        "inferenceErrors": inference_errors,
        "inferenceStale": infer_stale,
        "lastOverlayPostAgeMs": int((now - last_overlay_post_mono) * 1000) if last_overlay_post_mono else -1,
        "frameStore": frames.stats(),
        "freshFrames": {k: v.frame_id for k, v in snap.items()},
        "calibrationProfile": CAL_PROFILE,
        "allowSynthetic": ALLOW_SYNTHETIC,
        "calibration": {
            k: {
                "valid": c.valid,
                "type": c.calibration_type,
                "frame": c.coordinate_frame,
            }
            for k, c in CALIBRATIONS.items()
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
