"""Per-camera temporal smoothing and stale overlay state (PR4)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


OverlayStatus = str  # DETECTED | NO_CURB | STALE | INFERENCE_ERROR


@dataclass
class TemporalConfig:
    enabled: bool = True
    smoothing_alpha: float = 0.4
    stable_frames_required: int = 2
    stale_timeout_ms: int = 500
    hold_on_miss_frames: int = 1


@dataclass
class _CamState:
    contour: List[Tuple[int, int]] = field(default_factory=list)
    confidence: float = 0.0
    stable_count: int = 0
    miss_count: int = 0
    last_seen_ms: float = 0.0
    status: OverlayStatus = "NO_CURB"


class TemporalCurbFilter:
    def __init__(self, cfg: TemporalConfig) -> None:
        self.cfg = cfg
        self._cams: Dict[str, _CamState] = {}

    def _smooth(self, prev: List[Tuple[int, int]], new: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        if not prev or not new:
            return new
        n = min(len(prev), len(new))
        a = self.cfg.smoothing_alpha
        out: List[Tuple[int, int]] = []
        for i in range(n):
            px, py = prev[i]
            nx, ny = new[i]
            out.append((int(round(a * nx + (1 - a) * px)), int(round(a * ny + (1 - a) * py))))
        if len(new) > n:
            out.extend(new[n:])
        return out

    def update(
        self,
        camera: str,
        now_ms: float,
        found: bool,
        confidence: float,
        contour: List[Tuple[int, int]],
        inference_error: bool = False,
    ) -> Tuple[List[Tuple[int, int]], float, bool, OverlayStatus]:
        st = self._cams.setdefault(camera, _CamState())

        if inference_error:
            st.status = "INFERENCE_ERROR"
            return [], 0.0, False, "INFERENCE_ERROR"

        if not self.cfg.enabled:
            if found and len(contour) >= 2:
                st.contour = contour
                st.confidence = confidence
                st.last_seen_ms = now_ms
                st.status = "DETECTED"
                return contour, confidence, True, "DETECTED"
            st.status = "NO_CURB"
            return [], 0.0, False, "NO_CURB"

        if found and len(contour) >= 2:
            st.miss_count = 0
            smoothed = self._smooth(st.contour, contour) if st.contour else contour
            st.stable_count += 1
            if st.stable_count >= self.cfg.stable_frames_required:
                st.contour = smoothed
                st.confidence = confidence
                st.last_seen_ms = now_ms
                st.status = "DETECTED"
                return st.contour, st.confidence, True, "DETECTED"
            st.last_seen_ms = now_ms
            return [], confidence, False, "NO_CURB"

        st.stable_count = 0
        st.miss_count += 1
        age = now_ms - st.last_seen_ms
        if st.contour and st.miss_count <= self.cfg.hold_on_miss_frames and age <= self.cfg.stale_timeout_ms:
            st.status = "DETECTED"
            return st.contour, st.confidence, True, "DETECTED"
        if st.contour and age <= self.cfg.stale_timeout_ms:
            st.status = "STALE"
            return [], st.confidence, False, "STALE"
        st.contour = []
        st.status = "NO_CURB"
        return [], 0.0, False, "NO_CURB"

    def statuses(self, now_ms: float) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for cam, st in self._cams.items():
            if st.contour and now_ms - st.last_seen_ms > self.cfg.stale_timeout_ms:
                out[cam] = "STALE"
            else:
                out[cam] = st.status
        return out
