"""Reject invalid metric readings before ingest."""
from __future__ import annotations

from dataclasses import dataclass

from curb_distance_estimator import DistanceResult


@dataclass
class ValidatedDistance:
    ok: bool
    distance_m: float
    uncertainty_m: float | None
    reason: str


def validate(
    det_confidence: float,
    geom: DistanceResult,
    min_conf: float,
    min_m: float,
    max_m: float,
    *,
    report_uncertainty: bool = False,
) -> ValidatedDistance:
    if det_confidence < min_conf:
        return ValidatedDistance(False, 0.0, None, "low_detection_confidence")
    if not geom.valid:
        return ValidatedDistance(False, 0.0, None, geom.reason or "invalid_geometry")
    d = geom.distance_m
    if d < min_m or d > max_m:
        return ValidatedDistance(False, d, None, "out_of_range")
    unc = None
    if report_uncertainty:
        unc = max(0.05, 0.08 + 0.02 * d)
    return ValidatedDistance(True, d, unc, "ok")
