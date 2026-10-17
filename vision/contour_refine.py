"""Contour cleanup after NN segmentation (PR4)."""
from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np


def remove_small_components(mask: np.ndarray, min_area: int) -> np.ndarray:
    if min_area <= 0 or mask.sum() == 0:
        return mask
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    out = np.zeros_like(mask, dtype=np.uint8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[labels == i] = 1
    return out


def simplify_contour(points: List[Tuple[int, int]], epsilon: float = 2.0) -> List[Tuple[int, int]]:
    if len(points) < 3:
        return points
    arr = np.array(points, dtype=np.float32).reshape(-1, 1, 2)
    approx = cv2.approxPolyDP(arr, epsilon, False)
    return [(int(p[0][0]), int(p[0][1])) for p in approx]


def filter_point_jumps(points: List[Tuple[int, int]], max_jump_px: float = 80.0) -> List[Tuple[int, int]]:
    if len(points) < 2:
        return points
    out = [points[0]]
    for p in points[1:]:
        prev = out[-1]
        if (p[0] - prev[0]) ** 2 + (p[1] - prev[1]) ** 2 <= max_jump_px ** 2:
            out.append(p)
    return out if len(out) >= 2 else points[:2]


def refine_contour(
    contour: List[Tuple[int, int]],
    image_w: int,
    image_h: int,
    min_area: int = 20,
    simplify_epsilon: float = 2.0,
    max_jump_px: float = 80.0,
) -> List[Tuple[int, int]]:
    if len(contour) < 2:
        return []
    pts = filter_point_jumps(contour, max_jump_px=max_jump_px)
    pts = simplify_contour(pts, epsilon=simplify_epsilon)
    if len(pts) < 2:
        return []
    return pts
