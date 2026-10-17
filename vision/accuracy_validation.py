"""Offline accuracy checks: ground truth vs ground-plane distance estimator (PR5)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from calibration_loader import CameraCalibration, load_all, resolve_calibration_directory
from curb_distance_estimator import estimate_from_contour


@dataclass
class GroundTruthSample:
    sample_id: str
    camera: str
    contour_camera_pixels: List[List[int]]
    distance_m: float
    image_height: int = 360
    tolerance_m: float = 0.15
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "GroundTruthSample":
        sid = str(raw.get("id") or raw.get("sample_id") or "")
        if not sid:
            raise ValueError("ground truth row requires id")
        cam = str(raw["camera"]).strip().lower()
        contour = raw.get("contour_camera_pixels") or raw.get("contour") or []
        if len(contour) < 2:
            raise ValueError(f"{sid}: contour_camera_pixels needs >= 2 points")
        dist = float(raw["distance_m"])
        return cls(
            sample_id=sid,
            camera=cam,
            contour_camera_pixels=[[int(p[0]), int(p[1])] for p in contour],
            distance_m=dist,
            image_height=int(raw.get("image_height", 360)),
            tolerance_m=float(raw.get("tolerance_m", 0.15)),
            notes=str(raw.get("notes", "")),
        )


@dataclass
class SampleResult:
    sample_id: str
    camera: str
    expected_m: float
    estimated_m: Optional[float]
    error_m: Optional[float]
    within_tolerance: bool
    valid: bool
    reason: str = ""
    tolerance_m: float = 0.15


@dataclass
class AccuracyReport:
    profile: str
    calibration_directory: str
    samples: List[SampleResult] = field(default_factory=list)
    skipped_invalid_calibration: List[str] = field(default_factory=list)

    @property
    def evaluated(self) -> List[SampleResult]:
        return [s for s in self.samples if s.valid and s.estimated_m is not None]

    @property
    def mae_m(self) -> Optional[float]:
        ev = self.evaluated
        if not ev:
            return None
        return sum(abs(s.error_m or 0.0) for s in ev) / len(ev)

    @property
    def max_abs_error_m(self) -> Optional[float]:
        ev = self.evaluated
        if not ev:
            return None
        return max(abs(s.error_m or 0.0) for s in ev)

    @property
    def pass_rate(self) -> float:
        if not self.samples:
            return 0.0
        ok = sum(1 for s in self.samples if s.within_tolerance and s.valid)
        return ok / len(self.samples)

    @property
    def rmse_m(self) -> Optional[float]:
        ev = self.evaluated
        if not ev:
            return None
        return (sum((s.error_m or 0.0) ** 2 for s in ev) / len(ev)) ** 0.5

    @property
    def bias_m(self) -> Optional[float]:
        ev = self.evaluated
        if not ev:
            return None
        return sum(s.error_m or 0.0 for s in ev) / len(ev)

    @property
    def valid_estimate_rate(self) -> float:
        if not self.samples:
            return 0.0
        return len(self.evaluated) / len(self.samples)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "calibrationDirectory": self.calibration_directory,
            "summary": {
                "count": len(self.samples),
                "maeM": self.mae_m,
                "rmseM": self.rmse_m,
                "biasM": self.bias_m,
                "maxAbsErrorM": self.max_abs_error_m,
                "passRate": round(self.pass_rate, 4),
                "validEstimateRate": round(self.valid_estimate_rate, 4),
            },
            "skippedInvalidCalibration": self.skipped_invalid_calibration,
            "results": [
                {
                    "id": s.sample_id,
                    "camera": s.camera,
                    "expectedM": s.expected_m,
                    "estimatedM": s.estimated_m,
                    "errorM": s.error_m,
                    "toleranceM": s.tolerance_m,
                    "withinTolerance": s.within_tolerance,
                    "valid": s.valid,
                    "reason": s.reason,
                }
                for s in self.samples
            ],
        }


def load_ground_truth_jsonl(path: Path) -> List[GroundTruthSample]:
    out: List[GroundTruthSample] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw = json.loads(line)
            try:
                out.append(GroundTruthSample.from_dict(raw))
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"{path}:{line_no}: {e}") from e
    return out


def evaluate_samples(
    samples: List[GroundTruthSample],
    calibrations: Dict[str, CameraCalibration],
    *,
    profile: str,
    cal_dir: Path,
    ground_z_m: float = 0.0,
    require_valid_calibration: bool = True,
) -> AccuracyReport:
    report = AccuracyReport(profile=profile, calibration_directory=str(cal_dir))
    for gt in samples:
        cal = calibrations.get(gt.camera)
        if cal is None:
            report.samples.append(
                SampleResult(
                    gt.sample_id,
                    gt.camera,
                    gt.distance_m,
                    None,
                    None,
                    False,
                    False,
                    "missing_calibration",
                    gt.tolerance_m,
                )
            )
            continue
        if require_valid_calibration and not cal.valid:
            report.skipped_invalid_calibration.append(gt.camera)
            report.samples.append(
                SampleResult(
                    gt.sample_id,
                    gt.camera,
                    gt.distance_m,
                    None,
                    None,
                    False,
                    False,
                    "calibration_not_valid",
                    gt.tolerance_m,
                )
            )
            continue
        geom = estimate_from_contour(cal, gt.contour_camera_pixels, gt.image_height, ground_z_m)
        if not geom.valid:
            report.samples.append(
                SampleResult(
                    gt.sample_id,
                    gt.camera,
                    gt.distance_m,
                    None,
                    None,
                    False,
                    False,
                    geom.reason,
                    gt.tolerance_m,
                )
            )
            continue
        err = geom.distance_m - gt.distance_m
        report.samples.append(
            SampleResult(
                gt.sample_id,
                gt.camera,
                gt.distance_m,
                geom.distance_m,
                err,
                abs(err) <= gt.tolerance_m,
                True,
                "ok",
                gt.tolerance_m,
            )
        )
    return report


def run_validation(
    vision_root: Path,
    cal_cfg: dict,
    profile: str,
    ground_truth_path: Path,
    *,
    allow_synthetic: bool = False,
    ground_z_m: float = 0.0,
    require_valid_calibration: bool = True,
) -> AccuracyReport:
    cal_dir, active = resolve_calibration_directory(vision_root, cal_cfg, profile)
    cals = load_all(cal_dir, allow_synthetic=allow_synthetic)
    samples = load_ground_truth_jsonl(ground_truth_path)
    return evaluate_samples(
        samples,
        cals,
        profile=active,
        cal_dir=cal_dir,
        ground_z_m=ground_z_m,
        require_valid_calibration=require_valid_calibration,
    )
