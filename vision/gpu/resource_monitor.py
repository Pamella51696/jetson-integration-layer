"""Lightweight inference resource telemetry (no external APM)."""
from __future__ import annotations

import resource
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GpuHealth:
    healthy: bool = False
    backend: str = "none"
    requested_backend: str = "auto"
    fallback_active: bool = False
    init_error: str = ""
    last_latency_ms: float = 0.0
    latency_ms_p50: float = 0.0
    effective_fps: float = 0.0
    dropped_inferences: int = 0
    inference_count: int = 0
    process_rss_mb: float = 0.0
    last_gpu_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "healthy": self.healthy,
            "backend": self.backend,
            "requestedBackend": self.requested_backend,
            "fallbackActive": self.fallback_active,
            "initError": self.init_error or None,
            "lastLatencyMs": round(self.last_latency_ms, 2),
            "latencyMsP50": round(self.latency_ms_p50, 2),
            "effectiveFps": round(self.effective_fps, 2),
            "droppedInferences": self.dropped_inferences,
            "inferenceCount": self.inference_count,
            "processRssMb": round(self.process_rss_mb, 1),
            "lastGpuError": self.last_gpu_error or None,
        }


class ResourceMonitor:
    def __init__(self, rss_limit_mb: float = 0.0) -> None:
        self.health = GpuHealth()
        self._rss_limit_mb = rss_limit_mb
        self._latencies: List[float] = []
        self._window = 32
        self._last_infer_mono = 0.0

    def record_inference(self, latency_sec: float) -> None:
        ms = latency_sec * 1000.0
        self.health.last_latency_ms = ms
        self._latencies.append(ms)
        if len(self._latencies) > self._window:
            self._latencies.pop(0)
        sorted_lat = sorted(self._latencies)
        self.health.latency_ms_p50 = sorted_lat[len(sorted_lat) // 2]
        now = time.monotonic()
        if self._last_infer_mono > 0:
            dt = now - self._last_infer_mono
            if dt > 0:
                self.health.effective_fps = 1.0 / dt
        self._last_infer_mono = now
        self.health.inference_count += 1
        self._refresh_rss()

    def record_drop(self) -> None:
        self.health.dropped_inferences += 1

    def _refresh_rss(self) -> None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_mb = usage.ru_maxrss / 1024.0  # Linux: KB
        self.health.process_rss_mb = rss_mb
        if self._rss_limit_mb > 0 and rss_mb > self._rss_limit_mb:
            raise MemoryError(
                f"vision process RSS {rss_mb:.0f} MB exceeds guard {self._rss_limit_mb:.0f} MB"
            )

    def snapshot(self) -> Dict[str, Any]:
        self._refresh_rss()
        return self.health.to_dict()
