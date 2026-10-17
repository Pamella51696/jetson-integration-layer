"""Reject incomplete or inconsistent calibration profiles (PR5)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from calibration.schema import CalibrationStatus, CameraModelType


@dataclass
class ProfileValidation:
    ok: bool
    status: CalibrationStatus
    reasons: List[str]


def _status_from_raw(raw: dict, cal_type: str, valid_flag: bool, allow_synthetic: bool) -> CalibrationStatus:
    block = raw.get("calibration") or {}
    explicit = str(block.get("status", "")).upper().strip()
    if explicit in CalibrationStatus.__members__:
        return CalibrationStatus(explicit)
    if cal_type == "synthetic":
        return CalibrationStatus.SYNTHETIC if (valid_flag and allow_synthetic) else CalibrationStatus.UNVERIFIED
    if valid_flag:
        return CalibrationStatus.VALIDATED
    return CalibrationStatus.UNVERIFIED


def validate_camera_yaml_raw(raw: dict, *, allow_synthetic: bool = False) -> ProfileValidation:
    reasons: List[str] = []
    img = raw.get("image") or {}
    w = int(img.get("width", 0))
    h = int(img.get("height", 0))
    fx = float(img.get("fx", 0) or 0)
    fy = float(img.get("fy", 0) or 0)
    cal_type = str(raw.get("calibration_type", "measured"))
    valid_flag = bool(raw.get("valid", False))
    status = _status_from_raw(raw, cal_type, valid_flag, allow_synthetic)

    model = raw.get("camera_model") or {}
    model_type = str(model.get("type", "rectified_pinhole")).lower()
    if model_type == CameraModelType.FISHEYE.value:
        dist = model.get("distortion") or []
        if status == CalibrationStatus.VALIDATED and not dist:
            reasons.append("fisheye_validated_requires_distortion_coefficients")

    if status == CalibrationStatus.VALIDATED:
        if fx <= 0 or fy <= 0:
            reasons.append("invalid_intrinsics")
        if w <= 0 or h <= 0:
            reasons.append("invalid_image_dimensions")
        ext = raw.get("extrinsics") or {}
        t = ext.get("t_m")
        if not t or len(t) != 3:
            reasons.append("missing_extrinsics_translation")

    if status == CalibrationStatus.SYNTHETIC and not allow_synthetic:
        reasons.append("synthetic_disabled_by_policy")
        status = CalibrationStatus.UNVERIFIED

    if status == CalibrationStatus.VALIDATED and cal_type == "synthetic":
        reasons.append("synthetic_type_cannot_be_validated")
        status = CalibrationStatus.SYNTHETIC if allow_synthetic else CalibrationStatus.UNVERIFIED

    ok = len(reasons) == 0 and status in (
        CalibrationStatus.SYNTHETIC,
        CalibrationStatus.VALIDATED,
    )
    if status == CalibrationStatus.UNVERIFIED:
        ok = False
    return ProfileValidation(ok=ok, status=status, reasons=reasons)


def metric_publication_allowed(status: CalibrationStatus, *, allow_synthetic: bool) -> bool:
    if status == CalibrationStatus.VALIDATED:
        return True
    if status == CalibrationStatus.SYNTHETIC:
        return allow_synthetic
    return False
