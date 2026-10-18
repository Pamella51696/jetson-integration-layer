"""PR6 GPU integration — backend selection, fallback, queue (mocked TensorRT)."""
from __future__ import annotations

import numpy as np
import pytest

from gpu.gpu_integration import GpuInferenceIntegration, gpu_layer_enabled, load_gpu_config


def test_gpu_layer_disabled_by_default():
    cfg = load_gpu_config({})
    assert gpu_layer_enabled(cfg) is False


def test_gpu_enabled_via_env(monkeypatch):
    monkeypatch.setenv("VISION_GPU_ENABLED", "1")
    cfg = load_gpu_config({"gpu": {"enabled": False}})
    assert gpu_layer_enabled(cfg) is True


def test_auto_falls_back_to_cpu_without_tensorrt():
    gpu_cfg = {
        "enabled": True,
        "backend": "auto",
        "allow_cpu_fallback": True,
        "model": {"backend": "onnx", "onnx_path": "models/curb_segmentation.onnx"},
        "pipeline": {"max_pending_frames": 1},
        "resources": {"max_process_rss_mb": 0},
    }
    model_cfg = {"backend": "onnx", "onnx_path": "models/curb_segmentation.onnx", "input_width": 896, "input_height": 512}
    integ = GpuInferenceIntegration(model_cfg, gpu_cfg)
    assert integ.health.healthy
    assert integ.health.backend.startswith("cpu")
    assert integ.health.fallback_active is True


def test_tensorrt_backend_used_when_mock_succeeds(monkeypatch):
    class FakeTrt:
        name = "tensorrt"
        input_width = 896
        input_height = 512

        def infer_logits(self, bgr):
            return np.zeros((1, 4, 128, 224), dtype=np.float32)

        def close(self):
            pass

    monkeypatch.setattr(
        "gpu.tensorrt_backend.TensorRTBackend",
        lambda gpu_cfg: FakeTrt(),
    )
    gpu_cfg = {
        "enabled": True,
        "backend": "auto",
        "allow_cpu_fallback": False,
        "model": {},
        "pipeline": {"max_pending_frames": 2},
        "resources": {"max_process_rss_mb": 0},
    }
    model_cfg = {"backend": "onnx", "onnx_path": "models/curb_segmentation.onnx"}
    integ = GpuInferenceIntegration(model_cfg, gpu_cfg)
    assert integ.health.backend == "tensorrt"
    assert integ.health.fallback_active is False
    bgr = np.zeros((360, 640, 3), dtype=np.uint8)
    det = integ.infer(bgr, "front")
    assert det.found is False or isinstance(det.confidence, float)


def test_tensorrt_failure_without_fallback_unhealthy():
    gpu_cfg = {
        "enabled": True,
        "backend": "tensorrt",
        "allow_cpu_fallback": False,
        "model": {"engine_path": "models/nonexistent.engine"},
        "pipeline": {"max_pending_frames": 1},
        "resources": {"max_process_rss_mb": 0},
    }
    model_cfg = {"backend": "onnx"}
    integ = GpuInferenceIntegration(model_cfg, gpu_cfg)
    assert integ.health.healthy is False
    bgr = np.zeros((360, 640, 3), dtype=np.uint8)
    with pytest.raises(RuntimeError, match="unhealthy"):
        integ.infer(bgr, "front")


def test_queue_full_raises(monkeypatch):
    class SlowCpu:
        name = "cpu-onnx"
        input_width = 896
        input_height = 512

        def infer_logits(self, bgr):
            import time

            time.sleep(0.05)
            return np.zeros((1, 4, 32, 56), dtype=np.float32)

        def close(self):
            pass

    monkeypatch.setattr("gpu.gpu_integration.CpuBackend", lambda mc: SlowCpu())
    gpu_cfg = {
        "enabled": True,
        "backend": "cpu",
        "allow_cpu_fallback": True,
        "pipeline": {"max_pending_frames": 1},
        "resources": {"max_process_rss_mb": 0},
    }
    model_cfg = {"backend": "onnx"}
    integ = GpuInferenceIntegration(model_cfg, gpu_cfg)
    bgr = np.zeros((360, 640, 3), dtype=np.uint8)
    import threading

    errors = []

    def run():
        try:
            integ.infer(bgr, "front")
        except RuntimeError as e:
            errors.append(str(e))

    t = threading.Thread(target=run)
    t.start()
    try:
        integ.infer(bgr, "front")
    except RuntimeError as e:
        errors.append(str(e))
    t.join(timeout=2)
    assert any("queue full" in e for e in errors)
