"""PR5 calibration contract (status, distance semantics, camera model)."""
from __future__ import annotations

from enum import Enum


class CalibrationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    SYNTHETIC = "SYNTHETIC"
    VALIDATED = "VALIDATED"


class CalibrationType(str, Enum):
    NONE = "NONE"
    MEASURED = "MEASURED"
    SYNTHETIC = "SYNTHETIC"


class CameraModelType(str, Enum):
    """Rectified pinhole matches Java VisionFrameTap output (post-undistort)."""
    RECTIFIED_PINHOLE = "rectified_pinhole"
    PINHOLE = "pinhole"
    FISHEYE = "fisheye"


class DistanceType(str, Enum):
    GROUND_PLANE_RANGE = "GROUND_PLANE_RANGE"
    LATERAL = "LATERAL"
    LONGITUDINAL = "LONGITUDINAL"


class ContourFeature(str, Enum):
    """Which contour geometry is projected (provisional until field-validated)."""
    CURB_BASE_HEURISTIC = "CURB_BASE_HEURISTIC"
