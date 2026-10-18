"""Backend selection, health, CPU fallback, and inference dispatch (PR6)."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

import numpy as np

from gpu.cpu_backend import CpuBackend
from gpu.resource_monitor import GpuHealth, ResourceMonitor
from postprocess import CameraDetection, adas_logits_to_detection

ROOT = Path(__file__).resolve().parent.parent


class _InferBackend(Protocol):
    name: str
    input_width: int
    input_height: int

    def infer_logits(self, bgr: np.ndarray) -> np.ndarray:
        ...

    def close(self) -> None:
        ...


def load_gpu_config(vision_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Merge gpu.yaml and env overrides into vision config."""
    gpu = dict(vision_cfg.get("gpu") or {})
    gpu_path = os.environ.get("VISION_GPU_CONFIG", str(ROOT / "config" / "gpu.yaml"))
    path = Path(gpu_path)
    if path.is_file():
        import yaml

        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        if "gpu" in doc:
            gpu = {**doc["gpu"], **gpu}

    env_enabled = os.environ.get("VISION_GPU_ENABLED", "").strip().lower()
    if env_enabled in ("1", "true", "yes"):
        gpu["enabled"] = True
    elif env_enabled in ("0", "false", "no"):
        gpu["enabled"] = False

    backend = os.environ.get("VISION_GPU_BACKEND", "").strip().lower()
    if backend:
        gpu["backend"] = backend
    return gpu


def gpu_layer_enabled(gpu_cfg: Dict[str, Any]) -> bool:
    return bool(gpu_cfg.get("enabled", False))


class GpuInferenceIntegration:
    def __init__(self, model_cfg: Dict[str, Any], gpu_cfg: Dict[str, Any]) -> None:
        self._model_cfg = dict(model_cfg)
        self._gpu_cfg = dict(gpu_cfg)
        pipeline = gpu_cfg.get("pipeline") or {}
        resources = gpu_cfg.get("resources") or {}
        self._max_pending = int(pipeline.get("max_pending_frames", 1))
        self._allow_fallback = bool(gpu_cfg.get("allow_cpu_fallback", True))
        self._requested = str(gpu_cfg.get("backend", "auto")).lower()
        self._monitor = ResourceMonitor(rss_limit_mb=float(resources.get("max_process_rss_mb", 0) or 0))
        self._monitor.health.requested_backend = self._requested
        self._backend: Optional[_InferBackend] = None
        self._cpu_fallback: Optional[CpuBackend] = None
        self._infer_lock = threading.Lock()
        self._pending = 0
        self._camera_profiles = model_cfg.get("camera_profiles") or {}
        self._init_backends()

    def _init_backends(self) -> None:
        order = self._backend_order()
        errors: list[str] = []
        for kind in order:
            try:
                if kind == "tensorrt":
                    from gpu.tensorrt_backend import TensorRTBackend

                    self._backend = TensorRTBackend(self._gpu_cfg)
                    self._monitor.health.backend = self._backend.name
                    self._monitor.health.healthy = True
                    self._monitor.health.fallback_active = False
                    return
                if kind == "cpu":
                    self._backend = CpuBackend(self._model_cfg)
                    self._monitor.health.backend = self._backend.name
                    self._monitor.health.healthy = True
                    self._monitor.health.fallback_active = self._requested in ("auto", "tensorrt")
                    return
            except Exception as exc:
                errors.append(f"{kind}: {exc}")
                self._monitor.health.last_gpu_error = str(exc)

        msg = "; ".join(errors) if errors else "no backend available"
        self._monitor.health.init_error = msg
        self._monitor.health.healthy = False
        self._backend = None

    def _backend_order(self) -> list[str]:
        req = self._requested
        if req == "cpu":
            return ["cpu"]
        if req == "tensorrt":
            return ["tensorrt"] + (["cpu"] if self._allow_fallback else [])
        return ["tensorrt", "cpu"] if self._allow_fallback else ["tensorrt"]

    @property
    def health(self) -> GpuHealth:
        return self._monitor.health

    def status_dict(self) -> Dict[str, Any]:
        return self._monitor.snapshot()

    def infer(self, bgr: np.ndarray, camera: str = "front") -> CameraDetection:
        if self._backend is None:
            raise RuntimeError("vision GPU integration unhealthy: " + self._monitor.health.init_error)

        acquired = False
        with self._infer_lock:
            if self._pending >= self._max_pending:
                self._monitor.record_drop()
                raise RuntimeError("inference queue full (drop_oldest_frame)")
            self._pending += 1
            acquired = True

        try:
            import time

            t0 = time.perf_counter()
            try:
                logits = self._backend.infer_logits(bgr)
            except Exception as exc:
                self._monitor.health.last_gpu_error = str(exc)
                if self._allow_fallback and self._backend.name == "tensorrt":
                    if self._cpu_fallback is None:
                        self._cpu_fallback = CpuBackend(self._model_cfg)
                    self._backend.close()
                    self._backend = self._cpu_fallback
                    self._monitor.health.fallback_active = True
                    self._monitor.health.backend = self._backend.name
                    logits = self._backend.infer_logits(bgr)
                else:
                    self._monitor.health.healthy = False
                    raise
            self._monitor.record_inference(time.perf_counter() - t0)
            oh, ow = bgr.shape[:2]
            return adas_logits_to_detection(
                logits, ow, oh, camera=camera, camera_profiles=self._camera_profiles
            )
        finally:
            if acquired:
                with self._infer_lock:
                    self._pending = max(0, self._pending - 1)

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
        if self._cpu_fallback is not None:
            self._cpu_fallback.close()
