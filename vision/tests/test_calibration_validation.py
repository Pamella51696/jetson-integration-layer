from calibration.validator import ProfileValidation, metric_publication_allowed, validate_camera_yaml_raw
from calibration.schema import CalibrationStatus


def test_unverified_default_profile():
    raw = {
        "camera_name": "left",
        "image": {"width": 640, "height": 360, "fx": 320, "fy": 320},
        "extrinsics": {"t_m": [0, 0, 1], "r_ypr_deg": [0, 0, 0]},
        "valid": False,
        "calibration": {"status": "UNVERIFIED", "type": "NONE", "version": 1},
    }
    pv = validate_camera_yaml_raw(raw)
    assert pv.status == CalibrationStatus.UNVERIFIED
    assert not pv.ok
    assert not metric_publication_allowed(pv.status, allow_synthetic=False)


def test_synthetic_requires_allow_flag():
    raw = {
        "camera_name": "front",
        "calibration_type": "synthetic",
        "image": {"width": 640, "height": 360, "fx": 320, "fy": 320},
        "extrinsics": {"t_m": [0, 0, 1], "r_ypr_deg": [0, 45, 0]},
        "valid": True,
        "calibration": {"status": "SYNTHETIC", "type": "SYNTHETIC", "version": 1},
    }
    pv = validate_camera_yaml_raw(raw, allow_synthetic=True)
    assert pv.status == CalibrationStatus.SYNTHETIC
    assert pv.ok
    assert metric_publication_allowed(pv.status, allow_synthetic=True)
    assert not metric_publication_allowed(pv.status, allow_synthetic=False)


def test_validated_requires_intrinsics():
    raw = {
        "camera_name": "left",
        "image": {"width": 640, "height": 360, "fx": 0, "fy": 0},
        "extrinsics": {"t_m": [0, 0, 1]},
        "valid": True,
        "calibration": {"status": "VALIDATED", "type": "MEASURED", "version": 1},
    }
    pv = validate_camera_yaml_raw(raw)
    assert not pv.ok
    assert "invalid_intrinsics" in pv.reasons
