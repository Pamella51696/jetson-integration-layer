"""PR5 accuracy validation (geometry round-trip)."""
from pathlib import Path

import numpy as np

from accuracy_validation import evaluate_samples, GroundTruthSample
from calibration_loader import CameraCalibration, load_all, vehicle_mount_yaw_deg_to_r
from curb_distance_estimator import ray_ground_intersection


def _cal(t_m, r_cam_to_vehicle, valid=True, name="test") -> CameraCalibration:
    return CameraCalibration(
        name=name,
        valid=valid,
        width=640,
        height=360,
        fx=320.0,
        fy=320.0,
        cx=320.0,
        cy=180.0,
        t_m=np.array(t_m, dtype=float),
        r_cam_to_vehicle=r_cam_to_vehicle,
    )


def test_round_trip_center_ray_one_metre():
    r = vehicle_mount_yaw_deg_to_r(0.0, 45.0, 0.0)
    cal = _cal(t_m=[0.0, 0.0, 1.0], r_cam_to_vehicle=r)
    res = ray_ground_intersection(cal, 320.0, 180.0, ground_z_m=0.0)
    assert res.valid
    contour = [[320, 180], [325, 182]]
    report = evaluate_samples(
        [
            GroundTruthSample(
                "center-1m",
                "test",
                contour,
                res.distance_m,
                image_height=360,
                tolerance_m=0.02,
            )
        ],
        {"test": cal},
        profile="unit",
        cal_dir=Path("."),
        require_valid_calibration=True,
    )
    assert report.evaluated[0].within_tolerance
    assert abs(report.evaluated[0].error_m or 0) < 0.02


def test_invalid_calibration_skipped():
    r = vehicle_mount_yaw_deg_to_r(0.0, 45.0, 0.0)
    cal = _cal(t_m=[0.0, 0.0, 1.0], r_cam_to_vehicle=r, valid=False)
    report = evaluate_samples(
        [
            GroundTruthSample("x", "test", [[320, 180], [330, 185]], 1.0),
        ],
        {"test": cal},
        profile="unit",
        cal_dir=Path("."),
    )
    assert not report.evaluated
    assert report.samples[0].reason == "calibration_not_valid"
