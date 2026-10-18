# PR6: Jetson Orin Nano GPU Integration for Curb Detection

**Status:** Implemented in `vision/gpu/` (enable via `VISION_GPU_ENABLED=1` or `vision/config/gpu.yaml`)  
**Base:** PR1–PR5 middleware + Python vision sidecar (`vision/`)  
**Target hardware:** NVIDIA Jetson Orin Nano Super Developer Kit (8 GB unified memory, eMMC root filesystem)

---

## Summary

PR6 adds an **optional, isolated GPU inference layer** for the **existing** curb-segmentation model (`vision/models/curb_segmentation.onnx`, ~809 KB in repo) on Jetson, using **TensorRT** as the preferred backend and **explicit CPU fallback** to the current OpenVINO / ONNX Runtime paths. It does **not** replace Java video streaming, change the vision model, or pull in the Desktop VLM PyTorch/CUDA stack.

| Constraint | Jetson baseline (from device log) | PR6 planning target |
|------------|-----------------------------------|---------------------|
| Root filesystem free | **11 GB** on `/` (57 GB total, 82% used) | Finish with **≥ 10 GB** free if ~950 MB incremental budget holds; keep **≥ 3 GB** operational reserve |
| Home usage | **8.0 GB** (`~/Desktop` ~5.7 GB, `~/.cache` ~1 GB) | Do not copy VLM `.venv` or duplicate CUDA libs into middleware |
| Runtime RAM | **8 GB shared** (CPU + GPU); not measured in log | Measure with `free -h` / `tegrastats` under 4-camera load before tuning |
| Model on disk (repo) | `curb_segmentation.onnx` ~809 KB; OpenVINO ADAS pair ~1.1 MB | ONNX + Orin-built `.engine` capped at **400 MB** combined |

**Important:** `df -h` reports **disk** space, not GPU memory. On Orin Nano, GPU allocations come from **shared system RAM**; disk and runtime memory must be budgeted separately.

---

## Objectives

1. Accelerate inference for the **same** ONNX curb detector already used by `CurbDetector` / `ModelRunner`, via TensorRT on JetPack when compatible.
2. Preserve all PR1–PR5 behavior: four-camera tap, contours, metric validation (fail-closed), ingest/SSE, simulator, Java streaming.
3. **Isolate** GPU failures from the video pipeline: streaming continues; vision marks unhealthy and stops publishing fresh safety results when inference cannot run (no stale distances as current truth).
4. Stay within a **~950 MB incremental storage envelope** on the device (model/engine, deps, build temp, logs/tests), reusing JetPack TensorRT/CUDA already on the board.

## Non-goals

- New VLM, PyTorch training stack, Triton, or copying `~/Desktop/.../vlm/.venv` (multi‑GB cuDNN/cuBLAS/torch).
- Replacing `road-segmentation-adas-0001` OpenVINO default for non-Jetson dev unless explicitly configured.
- Rewriting middleware stitching, calibration YAML, or WP5 zone logic.

---

## Current codebase map (PR5)

Use these integration points; avoid duplicate services or config systems.

| Area | Location | PR6 touch |
|------|----------|-----------|
| Java streaming + frame tap | `VideoStreamingServer.java`, `-Dwp5.vision.*` | **No change** to streaming path; optional status fields only if needed |
| Vision HTTP sidecar | `vision/service.py`, `inference_loop()` | Wire **one** shared inference backend; expose backend health on `/health` |
| Frame queue / latest snapshot | `vision/frame_receiver.py` | Align with `gpu.pipeline.max_pending_frames` (default 1) |
| Pre/post unchanged | `frame_adapter.py`, `postprocess.py`, `contour_refine.py`, `temporal_filter.py`, `distance_validator.py` | GPU layer returns same tensor layout → existing `adas_logits_to_detection` |
| CPU inference today | `vision/model_runner.py` (`openvino` / `onnx` + CPU EP) | Becomes **`cpu_backend`** implementation |
| Model config | `vision/config.yaml` → `vision.model.*` | Add optional merge from `vision/config/gpu.yaml` |
| Model assets | `models/curb_segmentation.onnx`, ADAS OpenVINO pair | Engine: `models/curb_segmentation_orin_fp16.engine` (built on device) |
| Input shape (repo default) | **896 × 512** (`config.yaml`) | TensorRT build must match ONNX static dims (not the generic 640×384 example) |
| Tests | `vision/tests/test_*.py` | Add `vision/tests/test_gpu_integration.py`; full PR5 suite must pass |

---

## Architecture

```
Java middleware (4× camera, undistort, stitch, stream)
        │
        ▼  java_frame_tap (bounded; unchanged owner)
vision/frame_receiver.py
        │
        ▼  decode + normalize (frame_adapter)
vision/gpu/gpu_integration.py  ◄── config: vision/config/gpu.yaml
        │
        ├── tensorrt_backend.py  (preferred on Jetson)
        └── cpu_backend.py       (OpenVINO/ONNX Runtime — existing logic)
        │
        ▼  logits / mask (same as today)
postprocess → contour_refine → temporal → metric/validation
        │
        ▼
/api/v1/vision/detections, ingest, SSE (unchanged contracts)
```

### Isolation rules

1. GPU module is an **optional consumer** of frames, not a replacement for streaming or distance business logic.
2. On TensorRT/CUDA init failure, OOM, or repeated inference errors:
   - Log and record failure in health state.
   - Release GPU buffers where possible.
   - If `allow_cpu_fallback: true`, switch to CPU backend; else mark vision **unhealthy** and do not publish fresh detections/ingest distances.
3. Never fabricate metric distances when calibration requires valid inference (PR2 fail-closed preserved).
4. Java process must keep running if the Python sidecar crashes or GPU fails.

---

## Proposed additions (additive layout)

| File | Responsibility |
|------|----------------|
| `vision/gpu/gpu_integration.py` | Backend selection (`auto` \| `tensorrt` \| `cpu`), init, `infer()`, health, fallback, bounded dispatch |
| `vision/gpu/tensorrt_backend.py` | Load Orin-built engine, FP16, workspace cap, run inference, map outputs for `postprocess` |
| `vision/gpu/cpu_backend.py` | Thin wrapper over refactored `ModelRunner` CPU paths |
| `vision/gpu/resource_monitor.py` | RSS, avail mem, latency, queue depth, backend state (no heavy APM) |
| `vision/config/gpu.yaml` | Defaults (see below); merged under `vision.gpu` or loaded alongside `config.yaml` |
| `vision/tests/test_gpu_integration.py` | Mocked TRT, fallback, queue drop, stale-result guards |
| `docs/JETSON_PR6_SETUP.md` | JetPack audit commands, engine build, benchmark procedure (created during implementation) |

**Refactor note:** `ModelRunner` remains the CPU implementation surface; `gpu_integration` is what `CurbDetector` calls when `gpu.enabled` is true (or always, with internal backend pick).

---

## Configuration

Ship defaults in `vision/config/gpu.yaml` (dimensions match repo ONNX):

```yaml
gpu:
  enabled: true
  backend: auto            # auto | tensorrt | cpu
  allow_cpu_fallback: true

  model:
    onnx_path: "models/curb_segmentation.onnx"
    engine_path: "models/curb_segmentation_orin_fp16.engine"
    precision: "fp16"
    input_width: 896
    input_height: 512

  pipeline:
    max_pending_frames: 1
    max_inference_fps: 10
    drop_oldest_frame: true

  resources:
    max_inference_workspace_mb: 256
    max_gpu_buffers: 2
    max_process_rss_mb: 1200

  safety:
    preserve_video_stream: true
    publish_only_validated_results: true
    reject_stale_results: true

  diagnostics:
    report_backend: true
    report_latency: true
    report_memory: true
```

`auto`: probe JetPack TensorRT + engine compatibility; on failure use CPU only if fallback allowed.

Environment overrides (implementation): `VISION_GPU_CONFIG`, `VISION_BACKEND` (legacy), `VISION_ONNX_PATH`.

---

## Storage budget (incremental on Jetson)

| Component | Cap (MB) | Notes |
|-----------|----------|--------|
| ONNX + TensorRT engine | 400 | Repo ONNX ~0.8 MB; engine size TBD on device |
| Extra Python deps | 250 | Prefer system `tensorrt` / JetPack; avoid pip CUDA wheels |
| Build / conversion temp | 200 | Delete after engine build; keep outside `models/` if large |
| Logs, tests, profiling | 100 | Bounded rotation; no full-frame dumps |
| **Total incremental** | **950** | Stop and revise plan if exceeded |

### Safeguards (from device inventory)

- **Do not** install or copy the VLM virtualenv under `~/Desktop/.../vlm/.venv` (Triton, torch, cu13, cuDNN).
- **Do not** install full `nvidia-jetpack` dev meta-package without `dpkg -l | grep nvinfer` audit.
- Build TensorRT engine **on the Orin** (or matching L4T); desktop engines are not portable.
- Reuse `~/opencv-java-4.8.1`, `~/.m2` OpenCV JARs where the Java build already does; avoid second OpenCV tree in middleware.
- Do not delete `~/.cache/huggingface`, `~/TensorRT-Edge-LLM`, or OpenCV until ownership is confirmed.
- User log showed duplicate middleware tree under `~/Downloads/.../VideoStreamingServer_with_PR6*` — **consolidate to one repo** to avoid double model copies.

---

## Runtime RAM envelope (planning — measure on device)

| Consumer | Initial budget |
|----------|----------------|
| OS + drivers | 2.0 GB |
| Java middleware + buffers | 1.5 GB |
| Vision Python process | 1.2 GB |
| GPU / shared allocations | 1.0 GB |
| Headroom | 2.3 GB |
| **Total** | **8.0 GB** |

Operational guidance:

- **One** inference worker and **one** pending frame by default.
- Round-robin four cameras through a **single** model instance unless benchmarks justify parallelism.
- Start `max_inference_fps: 10`; Java default already samples every 3rd frame (`wp5.vision.everyNthFrame`).

---

## Jetson pre-flight (PR6.1 — run before coding GPU paths)

On `edag_india@edagindia` (or any Orin Nano Super under test):

```bash
cat /etc/nv_tegra_release
dpkg -l | grep -E 'nvidia-jetpack|nvidia-l4t-core|nvinfer|tensorrt'
free -h && swapon --show
df -h /
du -h --max-depth=1 "$HOME" | sort -hr | head -10
python3 -c "import tensorrt; print('tensorrt', tensorrt.__version__)" 2>/dev/null || echo "tensorrt import failed"
ls -lh /path/to/repo/vision/models/curb_segmentation.onnx
```

Under **four-camera** workload:

```bash
tegrastats --interval 1000   # 60–120 s while streaming + sidecar running
```

Record: stream FPS, sidecar `resultsPerSecond`, inference latency (p50/p95), RSS, swap use, `df -h /`.

---

## Implementation phases

| Phase | Scope |
|-------|--------|
| **PR6.1** | Baseline audit: JetPack/TensorRT versions, ONNX op compatibility, storage/RAM measurements, engine build feasibility |
| **PR6.2** | `tensorrt_backend.py` + engine build script (on-device), output parity vs CPU on sample frames |
| **PR6.3** | `gpu_integration.py`, bounded queue, `resource_monitor.py`, CPU fallback, config merge |
| **PR6.4** | Regression: PR5 tests, fault injection (bad engine, CUDA fail), streaming continuity, schema checks |
| **PR6.5** | On-device benchmarks: CPU vs GPU latency/FPS/RAM/accuracy; document limits and known ops gaps |

### Engine build (outline)

1. Verify ONNX input `[1,3,512,896]` (confirm with `onnx` or ORT on device).
2. `trtexec` or Python TensorRT API: FP16, workspace ≤ `max_inference_workspace_mb`.
3. Write engine to `engine_path`; verify size ≤ budget.
4. Compare mask/contour outputs to CPU within agreed tolerance before enabling metric mode.

---

## Testing requirements

### New unit tests (`test_gpu_integration.py`)

- `auto` selects TensorRT when mock init succeeds; CPU when TRT fails and fallback on.
- Fallback disabled → unhealthy, no valid fresh detection payload.
- Invalid output shape → `INFERENCE_ERROR`, no distance ingest.
- Queue depth 1 + `drop_oldest_frame` drops superseded frames.
- Resource monitor exposes backend name and latency fields.

### Regression

- All existing `vision/tests/` unchanged pass.
- Java/WP5 contract tests and `tools/fake_vehicle.py` flows unchanged.
- Manual: kill GPU path → video at `:9091` still works; `/api/v1/vision/status` reports degraded backend.

---

## Acceptance criteria

- [ ] All PR1–PR5 automated tests pass.
- [ ] TensorRT runs **`curb_segmentation.onnx`** engine on Orin Nano hardware.
- [ ] Detection JSON / contour pipeline unchanged for downstream metric code.
- [ ] Video streaming operational when GPU inference is forced to fail.
- [ ] CPU fallback works when enabled; explicit failure when disabled.
- [ ] Four-camera run: no unbounded queue growth or monotonic RSS over 30+ minutes.
- [ ] Incremental storage ≤ 950 MB target; root free ≥ 3 GB (goal ~10 GB if budget met).
- [ ] Documented CPU vs GPU latency, FPS, RAM, and detection parity.

---

## Deliverables checklist

- [ ] Additive source under `vision/gpu/`
- [ ] `vision/config/gpu.yaml` + setup notes in `docs/JETSON_PR6_SETUP.md`
- [ ] Minimal dependency delta (document pip vs JetPack packages)
- [ ] `test_gpu_integration.py`
- [ ] Benchmark report template filled on Jetson
- [ ] Changed-files list, assumptions (JetPack version, engine build host), known limitations

---

## Risks and open inputs

| Item | Action |
|------|--------|
| JetPack / TensorRT version not in user log | Capture in PR6.1; gates ONNX parser support |
| ONNX ops unsupported by TRT | Smallest change: ONNX simplify or stay on CPU OpenVINO |
| Engine build disk spike | Build in `/tmp` or external scratch; delete intermediates |
| Confusion with old “PR6” download folder | Use single git repo; retire duplicate `VideoStreamingServer_with_PR6` tree after merge |

---

## References

- [Jetson Orin Nano JetPack setup](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/setup_jetpack.html)
- [JetPack SDK components](https://developer.nvidia.com/embedded/jetpack-sdk-623)
- [Orin Nano Super specifications](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/)
- Repo: `docs/VISION_SIDECAR_SETUP.md`, `docs/WP5_SIGNAL_API.md`
