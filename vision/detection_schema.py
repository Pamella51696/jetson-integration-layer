"""PR-1 vision detection message (overlay only, no metric distance)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Detection:
    camera: str
    confidence: float
    coordinate_space: str  # CAMERA_PIXELS | PANORAMA_PIXELS
    contour: List[List[int]] = field(default_factory=list)
    contour_camera_pixels: List[List[int]] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0
    pos: str = ""  # logical sensor e.g. LEFT_REAR (PR2 ingest)
    bbox: List[int] = field(default_factory=list)  # x0,y0,x1,y1 optional
    distance_m: float | None = None
    distance_valid: bool = False
    distance_uncertainty_m: float | None = None
    detection_valid: bool = False
    overlay_status: str = "NO_CURB"  # DETECTED | NO_CURB | STALE | INFERENCE_ERROR
    calibration_status: str = "UNVERIFIED"  # VALIDATED | UNVERIFIED | SYNTHETIC
    timestamp_ms: int = 0
    distance_type: str = ""
    quality: Dict[str, Any] = field(default_factory=dict)
    metric_mode: str = "disabled"  # disabled | development | calibrated

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "class": "CURB",
            "confidence": round(min(1.0, max(0.0, self.confidence)), 4),
            "camera": self.camera,
            "coordinateSpace": self.coordinate_space,
            "detectionValid": bool(self.detection_valid),
            "overlayStatus": self.overlay_status,
        }
        if self.pos:
            d["pos"] = self.pos
        if self.contour:
            d["contour"] = self.contour
        if self.contour_camera_pixels:
            d["contourCameraPixels"] = self.contour_camera_pixels
        if self.image_width and self.image_height:
            d["imageWidth"] = self.image_width
            d["imageHeight"] = self.image_height
        d["calibrationStatus"] = self.calibration_status
        if self.timestamp_ms:
            d["timestamp"] = self.timestamp_ms
        if self.distance_valid:
            d["distanceValid"] = True
            if self.distance_m is not None:
                d["distanceM"] = round(self.distance_m, 3)
            if self.distance_uncertainty_m is not None:
                d["distanceUncertaintyM"] = round(self.distance_uncertainty_m, 3)
        else:
            d["distanceValid"] = False
        if self.distance_type:
            d["distanceType"] = self.distance_type
        if self.quality:
            d["quality"] = self.quality
        if self.metric_mode and self.metric_mode != "disabled":
            d["metricMode"] = self.metric_mode
        if self.bbox:
            d["bbox"] = self.bbox
        return d


def envelope(
    frame_id: int,
    timestamp_ns: int,
    frame_width: int,
    frame_height: int,
    detections: List[Detection],
    coordinate_space: str = "PANORAMA_PIXELS",
    overlay_status: str = "NO_CURB",
    camera_statuses: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "frameId": frame_id,
        "timestampMonotonicNs": timestamp_ns,
        "frameWidth": frame_width,
        "frameHeight": frame_height,
        "coordinateSpace": coordinate_space,
        "overlayStatus": overlay_status,
        "detections": [d.to_dict() for d in detections],
    }
    if camera_statuses:
        body["cameraOverlayStatus"] = camera_statuses
    return body
