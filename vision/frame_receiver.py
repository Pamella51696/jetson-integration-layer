"""HTTP receiver for Java VisionFrameTap (FastAPI routes delegate here)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Dict, Optional


@dataclass
class FramePacket:
    camera: str
    frame_id: int
    timestamp_ms: int
    width: int
    height: int
    jpeg: bytes
    received_monotonic_ns: int


class FrameStore:
    """Latest frame per camera; drops older frames automatically."""

    def __init__(self, max_age_ms: int = 200) -> None:
        self._max_age_ms = max_age_ms
        self._lock = Lock()
        self._latest: Dict[str, FramePacket] = {}
        self._dropped_frames: int = 0
        self._accepted_frames: int = 0

    def put(self, camera: str, frame_id: int, timestamp_ms: int, width: int, height: int, jpeg: bytes) -> None:
        now = time.monotonic_ns()
        with self._lock:
            if camera in self._latest:
                self._dropped_frames += 1
            self._accepted_frames += 1
            self._latest[camera] = FramePacket(
                camera, frame_id, timestamp_ms, width, height, jpeg, now
            )

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "camerasBuffered": len(self._latest),
                "acceptedFrames": self._accepted_frames,
                "droppedFrames": self._dropped_frames,
            }

    def snapshot_fresh(self) -> Dict[str, FramePacket]:
        now = time.monotonic_ns()
        limit = self._max_age_ms * 1_000_000
        with self._lock:
            return {
                k: v
                for k, v in self._latest.items()
                if now - v.received_monotonic_ns <= limit
            }

    def snapshot_latest(self) -> Dict[str, FramePacket]:
        """Latest frame per camera regardless of age (for inference when NN is slower than frame TTL)."""
        with self._lock:
            return dict(self._latest)
