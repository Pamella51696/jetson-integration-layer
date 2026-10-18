"""NN curb detection + postprocess + contour refine (PR4)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from contour_refine import refine_contour
from gpu.gpu_integration import GpuInferenceIntegration, gpu_layer_enabled
from model_runner import ModelRunner
from postprocess import CameraDetection


@dataclass
class CurbDetectResult:
    found: bool
    confidence: float
    contour: List[Tuple[int, int]]
    inference_error: bool = False
    reject_reason: str = ""


class CurbDetector:
    def __init__(
        self,
        model_cfg: Dict[str, Any],
        detection_cfg: Dict[str, Any],
        gpu_cfg: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._det_cfg = detection_cfg
        self._integration: GpuInferenceIntegration | None = None
        self._runner: ModelRunner | None = None
        gpu_cfg = gpu_cfg or {}
        if gpu_layer_enabled(gpu_cfg):
            self._integration = GpuInferenceIntegration(model_cfg, gpu_cfg)
        else:
            self._runner = ModelRunner(model_cfg)

    def gpu_status(self) -> Dict[str, Any]:
        if self._integration is not None:
            return {"enabled": True, **self._integration.status_dict()}
        return {"enabled": False, "backend": "legacy-model-runner", "healthy": True}

    def detect(self, bgr: np.ndarray, camera: str) -> CurbDetectResult:
        if bgr is None or bgr.size == 0:
            return CurbDetectResult(False, 0.0, [], inference_error=True, reject_reason="empty_frame")
        try:
            if self._integration is not None:
                raw: CameraDetection = self._integration.infer(bgr, camera)
            else:
                assert self._runner is not None
                raw = self._runner.infer(bgr, camera)
        except Exception as exc:
            return CurbDetectResult(False, 0.0, [], inference_error=True, reject_reason=str(exc))
        if not raw.found:
            return CurbDetectResult(False, raw.confidence, [], reject_reason="no_curb")
        h, w = bgr.shape[:2]
        contour = refine_contour(
            list(raw.contour),
            w,
            h,
            min_area=int(self._det_cfg.get("min_contour_area", 20)),
            simplify_epsilon=float(self._det_cfg.get("simplify_epsilon", 2.0)),
            max_jump_px=float(self._det_cfg.get("max_point_jump_px", 80.0)),
        )
        conf_th = float(self._det_cfg.get("confidence_threshold", 0.12))
        if len(contour) < 2 or raw.confidence < conf_th:
            return CurbDetectResult(False, raw.confidence, [], reject_reason="below_threshold")
        return CurbDetectResult(True, raw.confidence, contour)
