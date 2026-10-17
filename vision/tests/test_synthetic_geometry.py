"""Independently specified geometry (hand-checked 1 m ahead at 45° pitch)."""
import numpy as np

from calibration_loader import CameraCalibration, vehicle_mount_yaw_deg_to_r
from curb_distance_estimator import ray_ground_intersection


def test_one_metre_ground_hit_independent_setup():
    r = vehicle_mount_yaw_deg_to_r(0.0, 45.0, 0.0)
    cal = CameraCalibration(
        name="test",
        valid=True,
        width=640,
        height=360,
        fx=320.0,
        fy=320.0,
        cx=320.0,
        cy=180.0,
        t_m=np.array([0.0, 0.0, 1.0]),
        r_cam_to_vehicle=r,
        profile_validation_ok=True,
    )
    res = ray_ground_intersection(cal, 320.0, 180.0, ground_z_m=0.0)
    assert res.valid
    assert abs(res.distance_m - 1.0) < 1e-5
    assert abs(res.longitudinal_m - 1.0) < 1e-5
