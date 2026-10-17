from pathlib import Path

import numpy as np

from calibration_loader import load_camera_yaml
from curb_distance_estimator import DistanceResult
from distance_validator import validate


def test_repo_left_calibration_not_valid():
    path = Path(__file__).resolve().parents[2] / "config/calibration/left.yaml"
    cal = load_camera_yaml(path)
    assert cal.name == "left"
    assert cal.valid is False


def test_validator_rejects_invalid_geometry():
    geom = DistanceResult(0.5, np.zeros(3), False, "calibration_not_valid")
    v = validate(0.9, geom, min_conf=0.6, min_m=0.05, max_m=5.0)
    assert not v.ok
    assert v.reason == "calibration_not_valid"


def test_validator_accepts_in_range():
    geom = DistanceResult(0.75, np.zeros(3), True, "ok")
    v = validate(0.82, geom, min_conf=0.6, min_m=0.05, max_m=5.0)
    assert v.ok
    assert abs(v.distance_m - 0.75) < 1e-9
