"""Per-camera distance temporal checks (separate from PR4 contour filter)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class _DistState:
    last_distance_m: Optional[float] = None
    last_timestamp_ms: int = 0


class DistanceConsistencyFilter:
    def __init__(self, max_jump_m: float = 0.75) -> None:
        self.max_jump_m = max_jump_m
        self._cams: Dict[str, _DistState] = {}

    def last_state(self, camera: str) -> tuple[Optional[float], int]:
        st = self._cams.get(camera)
        if not st:
            return None, 0
        return st.last_distance_m, st.last_timestamp_ms

    def check(self, camera: str, distance_m: float, timestamp_ms: int) -> tuple[bool, Optional[str]]:
        st = self._cams.setdefault(camera, _DistState())
        if st.last_timestamp_ms and timestamp_ms > 0 and timestamp_ms <= st.last_timestamp_ms:
            return False, "non_monotonic_timestamp"
        if st.last_distance_m is not None and abs(distance_m - st.last_distance_m) > self.max_jump_m:
            return False, "distance_jump"
        st.last_distance_m = distance_m
        if timestamp_ms > 0:
            st.last_timestamp_ms = timestamp_ms
        return True, None
