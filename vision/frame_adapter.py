"""Normalize incoming frames and map contours across coordinate spaces (PR4)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from coordinate_mapper import camera_point_to_panorama, contour_camera_to_panorama


class CoordinateSpace(str, Enum):
    CAMERA_PIXELS = "CAMERA_PIXELS"
    PANORAMA_PIXELS = "PANORAMA_PIXELS"


@dataclass
class NormalizedFrame:
    camera: str
    frame_id: int
    timestamp_ms: int
    source_width: int
    source_height: int
    bgr: np.ndarray
    decode_ok: bool
    reject_reason: str = ""


@dataclass
class LayoutContext:
    coordinate_space: CoordinateSpace
    meta: Optional[Dict[str, Any]]
    panorama_width: int
    panorama_height: int
    allow_single_camera: bool


def decode_jpeg_packet(jpeg: bytes, camera: str, frame_id: int, timestamp_ms: int, width: int, height: int) -> NormalizedFrame:
    if not jpeg or len(jpeg) < 100:
        return NormalizedFrame(camera, frame_id, timestamp_ms, width, height, np.array([]), False, "empty_jpeg")
    arr = np.frombuffer(jpeg, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return NormalizedFrame(camera, frame_id, timestamp_ms, width, height, np.array([]), False, "jpeg_decode_failed")
    h, w = bgr.shape[:2]
    if width > 0 and height > 0 and (w != width or h != height):
        # Preserve declared geometry for mapping; image is authoritative for inference.
        pass
    return NormalizedFrame(camera, frame_id, timestamp_ms, w, h, bgr, True, "")


def build_layout_context(
    meta: Optional[Dict[str, Any]],
    cfg_input: Dict[str, Any],
    fallback_pano_w: int,
    fallback_pano_h: int,
) -> LayoutContext:
    if meta and meta.get("panorama") and meta.get("cameras"):
        pano = meta["panorama"]
        return LayoutContext(
            coordinate_space=CoordinateSpace.PANORAMA_PIXELS,
            meta=meta,
            panorama_width=int(pano.get("width", fallback_pano_w)),
            panorama_height=int(pano.get("height", fallback_pano_h)),
            allow_single_camera=bool(cfg_input.get("allow_single_camera", True)),
        )
    return LayoutContext(
        coordinate_space=CoordinateSpace.CAMERA_PIXELS,
        meta=None,
        panorama_width=fallback_pano_w,
        panorama_height=fallback_pano_h,
        allow_single_camera=bool(cfg_input.get("allow_single_camera", True)),
    )


def validate_camera_in_meta(meta: Dict[str, Any], camera: str) -> bool:
    cams = meta.get("cameras") or {}
    return camera in cams


def map_contour_to_output(
    layout: LayoutContext,
    camera: str,
    contour_cam: List[Tuple[int, int]],
) -> Tuple[List[List[int]], CoordinateSpace, Optional[str]]:
    """Returns (contour, space, error). Rejects unknown camera in panorama mode."""
    if not contour_cam:
        return [], layout.coordinate_space, None
    if layout.coordinate_space == CoordinateSpace.PANORAMA_PIXELS:
        if layout.meta is None:
            return [], layout.coordinate_space, "missing_meta"
        if not validate_camera_in_meta(layout.meta, camera):
            return [], layout.coordinate_space, f"unknown_camera_in_meta:{camera}"
        pano = contour_camera_to_panorama(layout.meta, camera, contour_cam)
        if len(pano) < 2:
            return [], layout.coordinate_space, "projection_failed"
        return pano, CoordinateSpace.PANORAMA_PIXELS, None
    out = [[int(x), int(y)] for x, y in contour_cam]
    return out, CoordinateSpace.CAMERA_PIXELS, None


def scale_contour(
    contour: List[List[int]],
    from_w: int,
    from_h: int,
    to_w: int,
    to_h: int,
) -> List[List[int]]:
    if from_w <= 0 or from_h <= 0 or to_w <= 0 or to_h <= 0:
        return contour
    if from_w == to_w and from_h == to_h:
        return contour
    sx, sy = to_w / from_w, to_h / from_h
    return [[int(round(x * sx)), int(round(y * sy))] for x, y in contour]
