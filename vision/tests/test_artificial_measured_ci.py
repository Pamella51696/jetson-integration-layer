"""Regression: artificial-measured ground truth passes accuracy gate."""
from pathlib import Path

import json

from accuracy_validation import run_validation


def test_artificial_measured_accuracy_gate():
    root = Path(__file__).resolve().parents[2]
    gt = root / "config/calibration/validation/ground_truth.artificial-measured.jsonl"
    th = json.loads(
        (root / "config/calibration/validation/thresholds.artificial-measured.json").read_text(encoding="utf-8")
    )
    assert gt.is_file(), "run tools/generate_artificial_ground_truth.py first"
    cal_cfg = {"directory": "../config/calibration", "profile": "artificial-measured"}
    report = run_validation(
        root / "vision",
        cal_cfg,
        "artificial-measured",
        gt,
        allow_synthetic=True,
        require_valid_calibration=False,
    )
    assert report.mae_m is not None
    assert report.mae_m <= float(th["maxMaeM"])
    assert report.pass_rate >= float(th["minPassRate"])
