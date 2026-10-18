"""TensorRT engine loader and inference (Jetson / JetPack)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from gpu import cuda_runtime

ROOT = Path(__file__).resolve().parent.parent


class TensorRTBackend:
    def __init__(self, gpu_cfg: Dict[str, Any]) -> None:
        model_cfg = gpu_cfg.get("model") or {}
        self.name = "tensorrt"
        self.input_w = int(model_cfg.get("input_width", 896))
        self.input_h = int(model_cfg.get("input_height", 512))
        engine_rel = model_cfg.get("engine_path", "models/curb_segmentation_orin_fp16.engine")
        self._engine_path = Path(engine_rel)
        if not self._engine_path.is_absolute():
            self._engine_path = ROOT / self._engine_path

        self._engine = None
        self._context = None
        self._legacy_bindings: list[int] = []
        self._legacy_input_i = 0
        self._legacy_output_i = 1
        self._legacy_host_out: Optional[np.ndarray] = None
        self._io_mode_tensor_api = False
        self._input_name: str = ""
        self._output_name: str = ""
        self._input_ptr = 0
        self._output_ptr = 0
        self._host_out: Optional[np.ndarray] = None
        self._load()

    def _load(self) -> None:
        try:
            import tensorrt as trt  # type: ignore
        except ImportError as exc:
            raise RuntimeError("tensorrt Python module not installed") from exc

        if not self._engine_path.is_file():
            raise FileNotFoundError(f"TensorRT engine missing: {self._engine_path}")

        logger = trt.Logger(trt.Logger.WARNING)
        with open(self._engine_path, "rb") as f:
            runtime = trt.Runtime(logger)
            engine = runtime.deserialize_cuda_engine(f.read())
        if engine is None:
            raise RuntimeError(f"failed to deserialize engine {self._engine_path}")

        context = engine.create_execution_context()
        if context is None:
            raise RuntimeError("create_execution_context failed")

        self._engine = engine
        self._context = context

        if hasattr(engine, "num_io_tensors"):
            self._io_mode_tensor_api = True
            self._setup_tensor_api(trt)
        else:
            self._setup_legacy_bindings(trt)

    def _setup_legacy_bindings(self, trt) -> None:
        engine = self._engine
        assert engine is not None
        n = engine.num_bindings
        self._legacy_bindings = [0] * n
        for i in range(n):
            dtype = trt.nptype(engine.get_binding_dtype(i))
            shape = tuple(engine.get_binding_shape(i))
            vol = int(np.prod(shape))
            nbytes = vol * np.dtype(dtype).itemsize
            self._legacy_bindings[i] = cuda_runtime.malloc(nbytes)
            if engine.binding_is_input(i):
                self._legacy_input_i = i
            else:
                self._legacy_output_i = i
                self._legacy_host_out = np.empty(shape, dtype=dtype)

    def _setup_tensor_api(self, trt) -> None:
        engine = self._engine
        context = self._context
        assert engine is not None and context is not None
        for i in range(engine.num_io_tensors):
            name = engine.get_tensor_name(i)
            mode = engine.get_tensor_mode(name)
            dtype = trt.nptype(engine.get_tensor_dtype(name))
            shape = tuple(context.get_tensor_shape(name))
            vol = int(np.prod(shape))
            nbytes = vol * np.dtype(dtype).itemsize
            ptr = cuda_runtime.malloc(nbytes)
            if mode == trt.TensorIOMode.INPUT:
                self._input_name = name
                self._input_ptr = ptr
            else:
                self._output_name = name
                self._output_ptr = ptr
                self._host_out = np.empty(shape, dtype=dtype)

    def infer_logits(self, bgr: np.ndarray) -> np.ndarray:
        import cv2

        assert self._context is not None
        resized = cv2.resize(bgr, (self.input_w, self.input_h))
        blob = resized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)

        if self._io_mode_tensor_api:
            assert self._host_out is not None
            cuda_runtime.memcpy_htod(self._input_ptr, blob)
            self._context.set_tensor_address(self._input_name, self._input_ptr)
            self._context.set_tensor_address(self._output_name, self._output_ptr)
            if not self._context.execute_async_v3(0):
                raise RuntimeError("TensorRT execute_async_v3 failed")
            cuda_runtime.memcpy_dtoh(self._host_out, self._output_ptr)
            return np.asarray(self._host_out)

        assert self._legacy_host_out is not None
        cuda_runtime.memcpy_htod(self._legacy_bindings[self._legacy_input_i], blob)
        if not self._context.execute_v2(self._legacy_bindings):
            raise RuntimeError("TensorRT execute_v2 failed")
        cuda_runtime.memcpy_dtoh(self._legacy_host_out, self._legacy_bindings[self._legacy_output_i])
        return np.asarray(self._legacy_host_out)

    def close(self) -> None:
        if self._io_mode_tensor_api:
            for ptr in (self._input_ptr, self._output_ptr):
                if ptr:
                    try:
                        cuda_runtime.free(ptr)
                    except Exception:
                        pass
        else:
            for ptr in self._legacy_bindings:
                try:
                    cuda_runtime.free(ptr)
                except Exception:
                    pass
        self._legacy_bindings = []
        self._context = None
        self._engine = None
