#!/usr/bin/env python3
"""Distance accuracy report: tape-measured ground truth vs the sidecar's ground-plane distance.

  cd vision
  python run_accuracy.py ../config/calibration/validation/ground_truth.jsonl
  python run_accuracy.py ground_truth.jsonl --allow-synthetic        (only to test the plumbing with a synthetic calibration)

Prints MAE / RMSE / bias / max error / pass-rate (within each row's tolerance_m). It measures the GEOMETRY (calibration + projection),
not the neural network: the curb contour of every row is taken as given.
"""
import argparse, json, os, sys
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parent))
from accuracy_validation import run_validation

ap = argparse.ArgumentParser()
ap.add_argument("ground_truth")
ap.add_argument("--config", default=os.environ.get("VISION_CONFIG", str(Path(__file__).resolve().parent / "config.yaml")))
ap.add_argument("--profile", default=None)
ap.add_argument("--allow-synthetic", action="store_true")
a = ap.parse_args()
cfg = yaml.safe_load(open(a.config, encoding="utf-8"))["vision"]
cal = cfg.get("calibration", {})
rep = run_validation(Path(__file__).resolve().parent, cal, a.profile or cal.get("profile", "default"), Path(a.ground_truth),
                     allow_synthetic=a.allow_synthetic, ground_z_m=float(cal.get("ground_plane_z_m", 0.0)))
d = rep.to_dict()
print(json.dumps(d["summary"], indent=2))
for r in d["results"]:
    est = r["estimatedM"]; err = r["errorM"]
    print(f'  {r["id"]:12s} {r["camera"]:6s} expected {r["expectedM"]:.2f} m  estimated {"-" if est is None else round(est, 2)}  error {"-" if err is None else round(err, 2)}  '
          f'{"OK" if r["withinTolerance"] else "FAIL (" + r["reason"] + ")"}')
if d["skippedInvalidCalibration"]:
    print("calibration not valid for:", sorted(set(d["skippedInvalidCalibration"])), "(fill the template and set valid: true)")
