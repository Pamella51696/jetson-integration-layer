"""CPU inference via existing ModelRunner (OpenVINO / ONNX Runtime)."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np

from model_runner import ModelRunner


class CpuBackend:
    def __init__(self, model_cfg: Dict[str, Any]) -> None:
        self._runner = ModelRunner(model_cfg)
        self.name = f"cpu-{self._runner.backend}"

    @property
    def input_width(self) -> int:
        return self._runner.input_w

    @property
    def input_height(self) -> int:
        return self._runner.input_h

    def infer_logits(self, bgr: np.ndarray) -> np.ndarray:
        return self._runner.infer_logits(bgr)

    def close(self) -> None:
        pass
