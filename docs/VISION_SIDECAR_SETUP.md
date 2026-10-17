# Python vision sidecar - setup, distance and accuracy

## 1. What the vision model gives you
The sidecar (`vision/`, OpenVINO model `road-segmentation-adas-0001`, has a *curb* class) looks at the straightened 640x360 picture of each camera and returns:
1. **Is there a curb in this camera?**  -> which side gets a zone.
2. **Where?** -> a curb contour in camera pixels (the red line in `curb-debug`, the pink overlay).
3. **How far?** -> a distance in metres, ONLY when that camera has a VALIDATED calibration (section 3). Without it Java uses the placeholder row->metres scale.
Java turns this into zones (red <= 0.5 m, yellow <= 1.0 m, green beyond) and the same JSON for Android as before. Nothing in the Android JSON changed.

## 2. Run it  (PowerShell)
1. once:  `pip install -r vision/requirements.txt`
2. `cd vision` ; `$env:VISION_CONFIG="config.recommended.yaml"` ; `python service.py`      (port 8765)
3. Java:  `mvn exec:java "-Dexec.mainClass=com.middleware.panorama.VideoStreamingServer" "-Dexec.args=9091" "-Dwp5.vision.engine=auto"`
4. Open `http://localhost:9091/play` (video only runs while a viewer is open) and `http://localhost:9091/wp5/viewer`.
Check `http://localhost:9091/api/v1/vision/status`: `sidecarAlive: true`, and **`resultsPerSecond`** (about 10 is ideal).
Engines: `classic` (default, no Python), `auto` (Python when running, else built-in detector), `sidecar` (Python only).

**Speed:** the model needs ~140 ms per camera frame on ONE CPU core (measured), i.e. all four cameras once every ~0.6 s. A multi-core PC is faster - read `resultsPerSecond`.
If it is below ~2, a zone disappears between results because of the stale timer: start Java with `-Dwp5.visionStaleMs=3000`. Fewer cameras = faster: `-Dwp5.vision.sides=left,right`.

## 3. How a distance is produced (and what is missing)
curb pixel (lowest point of the contour) -> ray through the camera -> intersection with the ground plane -> `distance = hypot(x, y)` in the VEHICLE frame (origin = rear axle).
This needs, per camera, `config/calibration/<camera>.yaml`: image intrinsics (already filled in, exact for what Java sends) and the camera's **position and angles on the vehicle (you must measure)**.
Templates are in this package with `valid: false`, so they cannot publish a fake distance.
To switch distances on:
1. Measure `t_m` (x,y,z in metres) and `r_ypr_deg` for each camera, set `valid: true` and `calibration: {status: VALIDATED}`.
2. Run the sidecar with `config.metric.example.yaml` (mode both, calibrated, ingest off, confidence gate 0.21).
3. Java then uses the sidecar's metric distance automatically (kept for 1 s if one frame is rejected, so two scales never mix).
Why the confidence gate was changed: the original `validation.min_detection_confidence: 0.60` blocked every distance, because this model's confidence for a real curb is only ~0.2-0.4.

**Know this before trusting a number**
* The distance is measured from the **rear-axle origin to the curb contact point**, NOT the gap between the car's body (or wheel) and the curb. The zones (0.5 m / 1.0 m) mean "gap to the body", so decide with your colleague how to convert (subtract the body offset per camera).
* Java straightens the fisheye with an **assumed** lens model (150 deg; 160 deg rear), not a measured one. A wrong lens model shifts every distance. Calibrate the real lenses (checkerboard) and put the real model into `FisheyeUndistorter` / `RearFeedPipeline`.
* The contour is the centre of the "curb" pixels, not the exact bottom edge where the curb meets the road; the code calls this "CURB_BASE_HEURISTIC, provisional until field-validated".

## 4. Measure the accuracy (do this on the real vehicle)
* **Distance:** park next to a curb at tape-measured distances, put the sidecar's contour + the measured distance in `config/calibration/validation/ground_truth.jsonl` (see the example), run
  `cd vision ; python run_accuracy.py ../config/calibration/validation/ground_truth.jsonl`  -> MAE / RMSE / bias / pass-rate. (This measures calibration + geometry, not the network.)
* **Detection (is it a curb?):** there is no tool for this yet. Collect ~100+ real frames per camera with and without curbs, label them, and count wrong/missed.
* Thresholds that need re-tuning on that data: `min_confidence` / `confidence_threshold` (0.21) and `min_pixels` per camera in `config.yaml`.

## 5. Safety
Python not running or crashing: `auto` -> built-in detector within ~3 s; `sidecar` -> no zones. Video and the rest of the server keep running. One log line, retry every 5 s.
`/api/v1/vision/detections` ignores a bad message (HTTP 400); in `classic` mode it ignores everything.
The model does NOT recognise curbs that were drawn into a video: use `classic` for such test clips.
