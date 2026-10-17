"""Validated curb metric measurement (PR5)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from calibration.schema import CalibrationStatus, ContourFeature, DistanceType
from calibration.validator import metric_publication_allowed
from calibration_loader import CameraCalibration
from curb_distance_estimator import DistanceResult, estimate_from_contour
from distance_validator import ValidatedDistance, validate as validate_distance
from metrics.quality import QualityFlags, assess_quality


@dataclass
class CurbMetricMeasurement:
    sensor_id: str
    pos: str
    camera: str
    confidence: float
    distance_m: float
    distance_type: str
    distance_valid: bool
    calibration_status: str
    timestamp_ms: int
    raw_distance_m: float
    quality: QualityFlags
    uncertainty_m: Optional[float] = None
    contour_feature: str = ContourFeature.CURB_BASE_HEURISTIC.value
    lateral_m: float = 0.0
    longitudinal_m: float = 0.0

    def to_ingest_sensor(
        self,
        pano_x: Optional[int] = None,
        pano_w: Optional[int] = None,
    ) -> Dict[str, Any]:
        s: Dict[str, Any] = {
            "id": self.sensor_id,
            "pos": self.pos,
            "distanceM": round(self.distance_m, 3),
            "kind": "CURB",
            "source": "VISION",
            "distanceValid": self.distance_valid,
            "confidence": round(self.confidence, 4),
            "distanceType": self.distance_type,
            "calibrationType": "synthetic"
            if self.calibration_status == CalibrationStatus.SYNTHETIC.value
            else "measured"
            if self.calibration_status == CalibrationStatus.VALIDATED.value
            else "unverified",
        }
        if pano_x is not None and pano_w is not None and pano_w > 0:
            s["panoX"] = int(pano_x)
            s["panoW"] = int(pano_w)
        return s

    def to_detection_extras(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "distanceType": self.distance_type or DistanceType.GROUND_PLANE_RANGE.value,
            "quality": self.quality.to_dict(),
            "contourFeature": self.contour_feature,
            "rawDistanceM": round(self.raw_distance_m, 3),
        }
        if self.lateral_m or self.longitudinal_m:
            out["lateralM"] = round(self.lateral_m, 3)
            out["longitudinalM"] = round(self.longitudinal_m, 3)
        return out


def build_metric_measurement(
    cal: CameraCalibration,
    camera: str,
    pos: str,
    sensor_id: str,
    contour_cam: list[list[int]],
    image_height: int,
    confidence: float,
    timestamp_ms: int,
    *,
    allow_synthetic: bool,
    min_conf: float,
    min_m: float,
    max_m: float,
    ground_z_m: float,
    report_uncertainty: bool,
    prev_distance_m: Optional[float] = None,
    last_timestamp_ms: int = 0,
) -> CurbMetricMeasurement:
    status = cal.calibration_status
    geom_raw = estimate_from_contour(cal, contour_cam, image_height, ground_z_m)
    u, v = 0.0, 0.0
    if geom_raw.valid:
        from curb_distance_estimator import representative_pixel

        pt = representative_pixel(contour_cam, image_height)
        if pt:
            u, v = pt

    quality = assess_quality(
        cal,
        u,
        v,
        geom_raw,
        confidence,
        min_conf,
        prev_distance_m=prev_distance_m,
        timestamp_ms=timestamp_ms,
        last_timestamp_ms=last_timestamp_ms,
    )

    can_publish = metric_publication_allowed(
        CalibrationStatus(status), allow_synthetic=allow_synthetic
    )
    vdist: ValidatedDistance = validate_distance(
        confidence, geom_raw, min_conf, min_m, max_m, report_uncertainty=report_uncertainty
    )
    distance_valid = (
        can_publish
        and vdist.ok
        and quality.projection_valid
        and quality.within_calibrated_region
        and quality.calibration_usable
    )
    if not quality.temporal_ok or not quality.timestamp_ok:
        distance_valid = False

    unc: Optional[float] = vdist.uncertainty_m if (report_uncertainty and vdist.ok) else None

    return CurbMetricMeasurement(
        sensor_id=sensor_id,
        pos=pos,
        camera=camera,
        confidence=confidence,
        distance_m=vdist.distance_m if vdist.ok else geom_raw.distance_m,
        distance_type=cal.distance_type or DistanceType.GROUND_PLANE_RANGE.value,
        distance_valid=distance_valid,
        calibration_status=status,
        timestamp_ms=timestamp_ms,
        raw_distance_m=geom_raw.distance_m,
        quality=quality,
        uncertainty_m=unc,
        lateral_m=geom_raw.lateral_m,
        longitudinal_m=geom_raw.longitudinal_m,
    )
