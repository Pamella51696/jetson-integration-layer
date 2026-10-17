import numpy as np

from calibration_loader import CameraCalibration, vehicle_mount_yaw_deg_to_r
from curb_distance_estimator import estimate_from_contour, representative_pixel


def test_representative_pixel_uses_bottom_band():
    contour = [[100, 200], [110, 350], [120, 340]]
    u, v = representative_pixel(contour, 360, bottom_fraction=0.4)
    assert v >= 360 * 0.6 - 1
    assert 105 <= u <= 115


def test_estimate_from_contour_known_geometry():
    cal = CameraCalibration(
        name="left",
        valid=True,
        width=640,
        height=360,
        fx=320.0,
        fy=320.0,
        cx=320.0,
        cy=180.0,
        t_m=np.array([0.0, 0.0, 2.0]),
        r_cam_to_vehicle=vehicle_mount_yaw_deg_to_r(90.0, 45.0, 0.0),
    )
    contour = [[320, 350], [325, 355]]
    res = estimate_from_contour(cal, contour, 360, ground_z_m=0.0)
    assert res.valid
    assert 0.05 < res.distance_m < 5.0
