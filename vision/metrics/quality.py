"""Measurement quality gates (PR5) — flags, not fabricated uncertainty."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from calibration_loader import CameraCalibration
from curb_distance_estimator import DistanceResult


@dataclass
class QualityFlags:
    projection_valid: bool = False
    within_calibrated_region: bool = False
    calibration_usable: bool = False
    confidence_ok: bool = False
    temporal_ok: bool = True
    timestamp_ok: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "projectionValid": self.projection_valid,
            "withinCalibratedRegion": self.within_calibrated_region,
            "calibrationUsable": self.calibration_usable,
            "confidenceOk": self.confidence_ok,
            "temporalOk": self.temporal_ok,
            "timestampOk": self.timestamp_ok,
            "reasons": self.reasons,
        }


def pixel_in_calibrated_region(u: float, v: float, cal: CameraCalibration, margin_px: float = 2.0) -> bool:
    return margin_px <= u < cal.width - margin_px and margin_px <= v < cal.height - margin_px


def assess_quality(
    cal: CameraCalibration,
    u: float,
    v: float,
    geom: DistanceResult,
    det_confidence: float,
    min_confidence: float,
    *,
    prev_distance_m: Optional[float] = None,
    max_jump_m: float = 0.75,
    timestamp_ms: int = 0,
    last_timestamp_ms: int = 0,
) -> QualityFlags:
    q = QualityFlags()
    q.calibration_usable = cal.valid and cal.profile_validation_ok
    q.confidence_ok = det_confidence >= min_confidence
    q.projection_valid = geom.valid
    q.within_calibrated_region = pixel_in_calibrated_region(u, v, cal)
    if timestamp_ms > 0 and last_timestamp_ms > 0 and timestamp_ms <= last_timestamp_ms:
        q.timestamp_ok = False
        q.reasons.append("non_monotonic_timestamp")
    if prev_distance_m is not None and geom.valid:
        if abs(geom.distance_m - prev_distance_m) > max_jump_m:
            q.temporal_ok = False
            q.reasons.append("distance_jump")
    if not q.projection_valid:
        q.reasons.append(geom.reason or "projection_invalid")
    if not q.within_calibrated_region:
        q.reasons.append("outside_image_region")
    if not q.calibration_usable:
        q.reasons.append("calibration_not_usable")
    if not q.confidence_ok:
        q.reasons.append("low_detection_confidence")
    return q
