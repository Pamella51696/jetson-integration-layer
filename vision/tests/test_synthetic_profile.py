from pathlib import Path

import numpy as np

from calibration_loader import (
    load_all,
    load_camera_yaml,
    project_vehicle_point_to_pixel,
    vehicle_mount_yaw_deg_to_r,
)
from curb_distance_estimator import ray_ground_intersection


SYNTH_DIR = Path(__file__).resolve().parents[2] / "config/calibration/synthetic-dev"


def test_synthetic_valid_gated_without_allow_flag():
    cal = load_camera_yaml(SYNTH_DIR / "front.yaml", allow_synthetic=False)
    assert cal.calibration_type == "synthetic"
    assert cal.valid is False


def test_synthetic_valid_when_allowed():
    cal = load_camera_yaml(SYNTH_DIR / "front.yaml", allow_synthetic=True)
    assert cal.valid is True


def test_front_camera_yaw_zero_points_forward():
    r = vehicle_mount_yaw_deg_to_r(0.0)
    d = r @ np.array([0.0, 0.0, 1.0])
    assert abs(d[0] - 1.0) < 1e-6
    assert abs(d[1]) < 1e-6
    assert abs(d[2]) < 1e-6


def test_roundtrip_ground_point_front_camera():
    cals = load_all(SYNTH_DIR, allow_synthetic=True)
    front = cals["front"]
    target = np.array([2.8, 0.0, 0.0])
    u, v = project_vehicle_point_to_pixel(front, target)
    res = ray_ground_intersection(front, u, v, ground_z_m=0.0)
    assert res.valid
    assert abs(res.ground_point[0] - 2.8) < 0.02
    assert abs(res.distance_m - 2.8) < 0.02
