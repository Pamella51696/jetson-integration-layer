"""Ground-plane ray intersection for curb points (vehicle frame)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from calibration_loader import CameraCalibration


@dataclass
class DistanceResult:
    distance_m: float
    ground_point: np.ndarray  # (3,) vehicle frame
    valid: bool
    reason: str = ""
    distance_type: str = "GROUND_PLANE_RANGE"
    lateral_m: float = 0.0
    longitudinal_m: float = 0.0
    pixel_u: float = 0.0
    pixel_v: float = 0.0
    contour_feature: str = "CURB_BASE_HEURISTIC"


def pixel_to_ray_cam(u: float, v: float, cal: CameraCalibration) -> np.ndarray:
    """OpenCV camera: x right, y down, z forward."""
    x = (u - cal.cx) / cal.fx
    y = (v - cal.cy) / cal.fy
    ray = np.array([x, y, 1.0], dtype=float)
    n = np.linalg.norm(ray)
    if n < 1e-12:
        return ray
    return ray / n


def ray_ground_intersection(
    cal: CameraCalibration,
    u: float,
    v: float,
    ground_z_m: float = 0.0,
) -> DistanceResult:
    """
    Vehicle frame: +x forward, +y left, +z up (WP5).

    Uses rectified camera pixels (640×360 from Java), not raw fisheye or panorama.
    Ground plane: n^T x + d = 0 (defaults to horizontal z = ground_z_m).
    Published GROUND_PLANE_RANGE = hypot(x, y) of intersection point p_v.
    """
    if not cal.valid:
        return DistanceResult(0.0, np.zeros(3), False, "calibration_not_valid", pixel_u=u, pixel_v=v)
    if cal.camera_model_type == "fisheye":
        return DistanceResult(
            0.0, np.zeros(3), False, "fisheye_unprojection_not_supported_use_rectified_feed", pixel_u=u, pixel_v=v
        )
    ray_cam = pixel_to_ray_cam(u, v, cal)
    d_vehicle = cal.r_cam_to_vehicle @ ray_cam
    if cal.ground_plane_enabled:
        n = cal.ground_plane_normal
        d_plane = cal.ground_plane_offset_m
        denom = float(np.dot(n, d_vehicle))
        if abs(denom) < 1e-9:
            return DistanceResult(0.0, np.zeros(3), False, "ray_parallel_to_ground", pixel_u=u, pixel_v=v)
        lam = -(float(np.dot(n, cal.t_m)) + d_plane) / denom
    else:
        dz = d_vehicle[2]
        if abs(dz) < 1e-9:
            return DistanceResult(0.0, np.zeros(3), False, "ray_parallel_to_ground", pixel_u=u, pixel_v=v)
        lam = (ground_z_m - cal.t_m[2]) / dz
    if lam <= 0:
        return DistanceResult(0.0, np.zeros(3), False, "intersection_behind_camera", pixel_u=u, pixel_v=v)
    p = cal.t_m + lam * d_vehicle
    dist = float(np.hypot(p[0], p[1]))
    if not np.isfinite(dist):
        return DistanceResult(0.0, p, False, "non_finite", pixel_u=u, pixel_v=v)
    return DistanceResult(
        dist,
        p,
        True,
        "ok",
        distance_type=cal.distance_type,
        lateral_m=float(p[1]),
        longitudinal_m=float(p[0]),
        pixel_u=u,
        pixel_v=v,
    )


def representative_pixel(
    contour: List[List[int]],
    image_height: int,
    bottom_fraction: float = 0.4,
) -> Optional[Tuple[float, float]]:
    """Curb contact heuristic: lowest points (max v) in bottom band of image."""
    if not contour:
        return None
    y_min = image_height * (1.0 - bottom_fraction)
    band = [(x, y) for x, y in contour if y >= y_min]
    if not band:
        band = contour
    # Prefer maximum row (closest to bottom of image)
    y_max = max(p[1] for p in band)
    xs = [p[0] for p in band if p[1] >= y_max - 2]
    if not xs:
        return None
    return float(sum(xs) / len(xs)), float(y_max)


def estimate_from_contour(
    cal: CameraCalibration,
    contour: List[List[int]],
    image_height: int,
    ground_z_m: float = 0.0,
) -> DistanceResult:
    pt = representative_pixel(contour, image_height)
    if pt is None:
        return DistanceResult(0.0, np.zeros(3), False, "no_contour_point")
    res = ray_ground_intersection(cal, pt[0], pt[1], ground_z_m)
    res.contour_feature = "CURB_BASE_HEURISTIC"
    return res
