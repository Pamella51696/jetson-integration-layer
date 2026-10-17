"""Map camera-pixel geometry to stitched panorama using /meta."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def fetch_meta(base_url: str) -> Dict[str, Any]:
    import json
    import urllib.request

    with urllib.request.urlopen(base_url.rstrip("/") + "/meta", timeout=5) as r:
        return json.load(r)


def camera_point_to_panorama(meta: Dict[str, Any], camera: str, x: int, y: int) -> Optional[Tuple[int, int]]:
    cams = meta.get("cameras") or {}
    cam = cams.get(camera)
    if not cam:
        return None
    sw, sh = cam.get("source_w", cam.get("w", 640)), cam.get("source_h", cam.get("h", 360))
    ph = meta.get("panorama", {}).get("height", cam["h"])
    px = cam["x"] + int(x * cam["w"] / sw)
    py = min(ph - 1, max(0, int(y * cam["h"] / sh)))
    return px, py


def camera_bbox_to_panorama(
    meta: Dict[str, Any], camera: str, x0: int, y0: int, x1: int, y1: int
) -> Tuple[int, int, int, int]:
    cams = meta["cameras"]
    cam = cams[camera]
    sw, sh = cam.get("source_w", cam.get("w", 640)), cam.get("source_h", cam.get("h", 360))
    pano_x0 = cam["x"] + int(x0 * cam["w"] / sw)
    pano_x1 = cam["x"] + int(x1 * cam["w"] / sw)
    pano_y0 = int(y0 * cam["h"] / sh)
    pano_y1 = int(y1 * cam["h"] / sh)
    return pano_x0, pano_y0, pano_x1, pano_y1


def contour_camera_to_panorama(
    meta: Dict[str, Any], camera: str, points: List[Tuple[int, int]]
) -> List[List[int]]:
    out: List[List[int]] = []
    for x, y in points:
        p = camera_point_to_panorama(meta, camera, x, y)
        if p:
            out.append([p[0], p[1]])
    return out


def panorama_contour_strip(
    pano_contour: List[List[int]], min_width_px: int = 40
) -> Optional[Tuple[int, int]]:
    """Return (panoX, panoW) for curb zone ingest from panorama contour."""
    if len(pano_contour) < 2:
        return None
    xs = [p[0] for p in pano_contour]
    x0, x1 = min(xs), max(xs)
    w = max(min_width_px, x1 - x0)
    return x0, w
