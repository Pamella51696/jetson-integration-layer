import numpy as np

from calibration_loader import CameraCalibration, vehicle_mount_yaw_deg_to_r
from curb_distance_estimator import DistanceResult, ray_ground_intersection
from metrics.quality import assess_quality


def _cal() -> CameraCalibration:
    r = vehicle_mount_yaw_deg_to_r(0.0, 45.0, 0.0)
    return CameraCalibration(
        name="t",
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


def test_quality_flags_ok_at_center():
    cal = _cal()
    geom = ray_ground_intersection(cal, 320, 180, 0.0)
    q = assess_quality(cal, 320, 180, geom, 0.9, 0.6)
    assert q.projection_valid and q.within_calibrated_region and q.confidence_ok


def test_quality_rejects_outside_image():
    cal = _cal()
    geom = DistanceResult(0, np.zeros(3), False, "outside_image_region")
    q = assess_quality(cal, 700, 10, geom, 0.9, 0.6)
    assert not q.within_calibrated_region
