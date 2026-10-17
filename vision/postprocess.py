"""Mask / logits → curb contour in camera pixels (per-camera tuning)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

ADAS_CURB = 2
ADAS_ROAD = 1

DEFAULT_CAMERA_PROFILE: Dict[str, Any] = {
    "min_pixels": 25,
    "bottom_roi": 0.40,
    "min_confidence": 0.12,
    "edge_fallback": True,
}

CAMERA_PROFILES: Dict[str, Dict[str, Any]] = {
    "front": {"min_pixels": 25, "bottom_roi": 0.38, "min_confidence": 0.15, "edge_fallback": True},
    "rear": {"min_pixels": 12, "bottom_roi": 0.52, "min_confidence": 0.10, "edge_fallback": True, "side_band": 0.55},
    "left": {"min_pixels": 12, "bottom_roi": 0.42, "min_confidence": 0.10, "edge_fallback": True, "side_band": 0.45, "side": "left"},
    "right": {"min_pixels": 12, "bottom_roi": 0.42, "min_confidence": 0.10, "edge_fallback": True, "side_band": 0.45, "side": "right"},
}


@dataclass
class CameraDetection:
    found: bool
    confidence: float
    x0: int
    y0: int
    x1: int
    y1: int
    contour: List[Tuple[int, int]]


def _profile(camera: Optional[str], overrides: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    base = dict(DEFAULT_CAMERA_PROFILE)
    if camera:
        cam = camera.strip().lower()
        if overrides and cam in overrides:
            base.update(overrides[cam])
        elif cam in CAMERA_PROFILES:
            base.update(CAMERA_PROFILES[cam])
    return base


def _curb_probability_map(logits: np.ndarray) -> np.ndarray:
    if logits.ndim != 4:
        return np.zeros((1, 1), dtype=np.float32)
    lg = logits[0]
    s = lg.sum(axis=0)
    if lg.min() >= -1e-4 and lg.max() <= 1.0001 and abs(float(s.mean()) - 1.0) < 1e-2:
        return lg[ADAS_CURB].astype(np.float32)          # already probabilities (the model ends with a Softmax)
    x = lg - np.max(lg, axis=0, keepdims=True)
    exp = np.exp(x, dtype=np.float32)
    p = exp / np.maximum(exp.sum(axis=0, keepdims=True), 1e-9)
    return p[ADAS_CURB]


def _band_mask(h: int, w: int, prof: Dict[str, Any]) -> np.ndarray:
    y0 = int(h * (1.0 - float(prof["bottom_roi"])))
    mask = np.zeros((h, w), dtype=bool)
    mask[y0:, :] = True
    side = prof.get("side")
    band = float(prof.get("side_band", 0.5))
    if side == "left":
        mask[:, int(w * (1.0 - band)) :] = False
    elif side == "right":
        mask[:, : int(w * band)] = False
    return mask


def _road_edge_line(road: np.ndarray, side: str, prof: Dict[str, Any]) -> Tuple[List[Tuple[float, float]], float]:
    """Curb as the boundary between ROAD and NON-ROAD on the camera's own side (left/right cameras only).

    road-segmentation-adas-0001 labels the road surface well but often calls a concrete curb 'background', not 'curb'.
    The curb is then the edge where the road mask ends.  For every row we take the outermost road pixel on the curb side,
    keep rows where a real non-road strip follows, and fit ONE straight line (RANSAC).  Returns (points in model pixels, score 0..1).
    """
    h, w = road.shape
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    m = cv2.morphologyEx(road.astype(np.uint8), cv2.MORPH_CLOSE, k)       # fill small holes (cracks, paint)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)                              # drop speckles
    top = int(h * float(prof.get("edge_top", 0.02)))
    bot = int(h * float(prof.get("edge_bottom", 0.78)))                     # below this the car's own body
    min_strip = int(w * float(prof.get("edge_min_strip", 0.08)))            # non-road width needed beyond the edge
    band = float(prof.get("side_band", 0.5))
    pts = []
    for y in range(top, bot, 2):
        row = m[y]
        xs = np.where(row > 0)[0]
        if len(xs) < w * 0.05:
            continue
        if side == "right":
            x = int(xs.max())
            if x < w * (1.0 - band) or w - 1 - x < min_strip:
                continue
        else:
            x = int(xs.min())
            if x > w * band or x < min_strip:
                continue
        pts.append((float(x), float(y)))
    n = len(pts)
    min_pts = int(prof.get("edge_min_points", 18))
    if n < min_pts:
        return [], 0.0
    P = np.array(pts, dtype=np.float32)
    rng = np.random.default_rng(1)
    best_in, best = None, 0
    tol = float(prof.get("edge_tol_px", 6.0)) * w / 640.0
    for _ in range(120):
        i, j = rng.choice(n, 2, replace=False)
        (x1, y1), (x2, y2) = P[i], P[j]
        if abs(y2 - y1) < 20:
            continue
        a, b = (x2 - x1) / (y2 - y1), x1 - (x2 - x1) / (y2 - y1) * y1     # x = a*y + b
        inl = np.abs(P[:, 0] - (a * P[:, 1] + b)) < tol
        c = int(inl.sum())
        if c > best:
            best, best_in = c, inl
    if best_in is None or best < min_pts:
        return [], 0.0
    Q = P[best_in]
    a, b = np.polyfit(Q[:, 1], Q[:, 0], 1)
    y0, y1_ = float(Q[:, 1].min()), float(Q[:, 1].max())
    span = (y1_ - y0) / max(1.0, float(bot - top))
    slope_ok = abs(a) < float(prof.get("edge_max_slope", 3.0))            # |dx/dy|: reject near-horizontal lines
    if span < float(prof.get("edge_min_span", 0.18)) or not slope_ok:
        return [], 0.0
    score = float(np.clip(0.5 * (best / n) + 0.5 * min(1.0, span / 0.5), 0.0, 1.0))
    ys_ = np.linspace(y0, y1_, 5)
    return [(float(a * y + b), float(y)) for y in ys_], score


def adas_logits_to_detection(
    logits: np.ndarray,
    orig_w: int,
    orig_h: int,
    camera: Optional[str] = None,
    camera_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> CameraDetection:
    prof = _profile(camera, camera_profiles)
    if logits.ndim == 4:
        cls_map = np.argmax(logits[0], axis=0)
        curb_prob = _curb_probability_map(logits)
    else:
        cls_map = (logits[0, 0] > 0.5).astype(np.uint8) * ADAS_CURB
        curb_prob = (cls_map == ADAS_CURB).astype(np.float32)

    curb = (cls_map == ADAS_CURB).astype(np.uint8)
    road = (cls_map == ADAS_ROAD).astype(np.uint8)
    h, w = curb.shape
    band = _band_mask(h, w, prof)
    roi = curb.copy()
    roi[~band] = 0

    if logits.ndim == 4 and int(roi.sum()) < int(prof["min_pixels"]) and prof.get("road_edge") and prof.get("side") in ("left", "right"):
        pts_m, score = _road_edge_line(road, prof["side"], prof)
        if pts_m and score >= float(prof["min_confidence"]):
            mh_, mw_ = road.shape
            sx_, sy_ = orig_w / mw_, orig_h / mh_
            cont = [(int(min(orig_w - 1, max(0, x * sx_))), int(min(orig_h - 1, max(0, y * sy_)))) for x, y in pts_m]
            xs_c = [c[0] for c in cont]; ys_c = [c[1] for c in cont]
            return CameraDetection(True, score, min(xs_c), min(ys_c), max(xs_c) + 1, max(ys_c) + 1, cont)
    if int(roi.sum()) < int(prof["min_pixels"]) and prof.get("edge_fallback"):
        edge = cv2.Canny((road * 255).astype(np.uint8), 40, 120)
        edge[~band] = 0
        roi = edge

    ys, xs = np.where(roi > 0)
    min_px = int(prof["min_pixels"])
    if len(xs) < min_px and prof.get("prob_fallback", True):
        # weak curb probability in band only.  NOTE: the softmax of this model never drops to 0, so with a low
        # min_confidence this path "finds" a curb in EVERY frame (constant ~0.19 confidence).  Set prob_fallback: false
        # in the camera profile to accept only pixels the network really classified as CURB.
        prob_band = curb_prob.copy()
        prob_band[~band] = 0
        ys, xs = np.where(prob_band >= float(prof["min_confidence"]) * 0.5)
        if len(xs) < min_px:
            return CameraDetection(False, 0.0, 0, 0, 0, 0, [])
    elif len(xs) < min_px:
        return CameraDetection(False, 0.0, 0, 0, 0, 0, [])

    mh, mw = logits.shape[-2], logits.shape[-1]
    sx, sy = orig_w / mw, orig_h / mh

    if logits.ndim == 4:
        model_ys = np.clip(ys.astype(int), 0, mh - 1)      # ys/xs come from the model-size masks already
        model_xs = np.clip(xs.astype(int), 0, mw - 1)
        conf = float(curb_prob[model_ys, model_xs].mean())
    else:
        conf = float(np.clip(roi[ys, xs].mean() / 255.0 if roi.max() > 1 else roi[ys, xs].mean(), 0.0, 1.0))

    conf = float(np.clip(conf, 0.0, 1.0))
    if conf < float(prof["min_confidence"]):
        return CameraDetection(False, conf, 0, 0, 0, 0, [])

    x0, x1 = int(xs.min() * sx), int(xs.max() * sx)
    y0 = int(ys.min() * sy)
    y1 = int(ys.max() * sy)
    x0, x1 = max(0, min(x0, orig_w - 1)), max(x0 + 1, min(x1, orig_w))
    y0, y1 = max(0, min(y0, orig_h - 1)), max(y0 + 1, min(y1, orig_h))

    contour: List[Tuple[int, int]] = []
    for frac in (0.15, 0.35, 0.5, 0.65, 0.85):
        row = int(ys.min() + (ys.max() - ys.min()) * frac)
        cols = xs[ys == row] if np.any(ys == row) else xs
        if len(cols) == 0:
            continue
        cx = int(cols.mean() * sx)
        cy = int(row * sy)
        contour.append((cx, min(orig_h - 1, cy)))

    if len(contour) < 2:
        return CameraDetection(False, conf, 0, 0, 0, 0, [])

    return CameraDetection(True, conf, x0, y0, x1, y1, contour)
