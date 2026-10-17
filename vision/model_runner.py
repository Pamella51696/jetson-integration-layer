"""ONNX / OpenVINO model loader and inference."""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

from postprocess import CameraDetection, adas_logits_to_detection

ROOT = Path(__file__).resolve().parent


class ModelRunner:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.backend = os.environ.get("VISION_BACKEND", cfg.get("backend", "openvino")).lower()
        self.input_w = int(cfg.get("input_width", 896))
        self.input_h = int(cfg.get("input_height", 512))
        self.camera_profiles = cfg.get("camera_profiles") or {}
        self._ov = None
        self._onnx = None
        if self.backend == "onnx":
            self._load_onnx()
        else:
            self._load_openvino()

    def _load_openvino(self) -> None:
        from openvino import Core

        model_dir = ROOT / "models"
        xml = model_dir / "road-segmentation-adas-0001.xml"
        if not xml.exists():
            raise FileNotFoundError(f"Run vision/download_model.py — missing {xml}")
        core = Core()
        model = core.read_model(str(xml))
        self._ov = core.compile_model(model, "CPU")
        inp = self._ov.inputs[0]
        if len(inp.shape) == 4:
            self.input_h, self.input_w = int(inp.shape[2]), int(inp.shape[3])

    def _load_onnx(self) -> None:
        import onnxruntime as ort

        default = str(ROOT / "models" / "curb_segmentation.onnx")
        path = Path(os.environ.get("VISION_ONNX_PATH", self.cfg.get("onnx_path", default)))
        if not path.exists():
            raise FileNotFoundError(path)
        self._onnx = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        _, _, h, w = self._onnx.get_inputs()[0].shape
        self.input_h, self.input_w = int(h), int(w)

    def infer(self, bgr: np.ndarray, camera: str = "front") -> CameraDetection:
        oh, ow = bgr.shape[:2]
        resized = cv2.resize(bgr, (self.input_w, self.input_h))
        blob = resized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
        if self._ov is not None:
            out = self._ov(blob)[0]
        else:
            name = self._onnx.get_inputs()[0].name
            out = self._onnx.run(None, {name: blob})[0]
        return adas_logits_to_detection(out, ow, oh, camera=camera, camera_profiles=self.camera_profiles)
