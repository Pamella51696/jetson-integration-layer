# Jetson Orin Nano — PR6 GPU setup

PR6 adds optional TensorRT inference for `vision/models/curb_segmentation.onnx`. Java middleware is unchanged.

## 1. Baseline audit (PR6.1)

```bash
cat /etc/nv_tegra_release
dpkg -l | grep -E 'nvinfer|tensorrt|nvidia-l4t'
free -h && df -h /
python3 -c "import tensorrt; print(tensorrt.__version__)"
```

Run `tegrastats --interval 1000` while four-camera streaming and the vision sidecar are active.

## 2. Python sidecar (CPU default)

```bash
cd vision
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export VISION_CONFIG=config.yaml
python service.py
```

Do **not** reuse the Desktop VLM `.venv` (PyTorch/Triton multi‑GB stack).

## 3. Build TensorRT engine on the Jetson

Engines are **not portable** across GPU generations. Build on the Orin:

```bash
cd vision
python3 gpu/build_engine.py \
  --onnx models/curb_segmentation.onnx \
  --engine models/curb_segmentation_orin_fp16.engine \
  --workspace-mb 256
```

If `build_engine.py` fails on older TensorRT APIs, use `trtexec`:

```bash
trtexec --onnx=models/curb_segmentation.onnx \
  --saveEngine=models/curb_segmentation_orin_fp16.engine \
  --fp16 --workspace=256
```

## 4. Enable GPU layer

```bash
export VISION_GPU_ENABLED=1
export VISION_GPU_BACKEND=auto   # tensorrt | cpu | auto
python service.py
```

Or set `vision/config/gpu.yaml` → `gpu.enabled: true` and merge via `VISION_GPU_CONFIG`.

Check `http://127.0.0.1:8765/health` → `gpu.backend`, `gpu.healthy`, latency fields.

## 5. Java middleware

```bash
# From project root (OpenCV Java + natives on java.library.path)
java --enable-native-access=ALL-UNNAMED -cp "...:opencv-4.9.0-0.jar" \
  com.middleware.panorama.VideoStreamingServer 9091 \
  -Dwp5.vision.engine=auto
```

Video must keep running if GPU inference fails; zones may stop updating until CPU fallback recovers.

## 6. Storage budget

Target **≤ 950 MB** incremental for engine, deps, build temp, and logs. Repo ONNX is ~0.8 MB; engine size is measured after build.

## 7. Benchmarks (PR6.5)

Record before/after: stream FPS, `resultsPerSecond`, inference latency from `/health`, `free -h`, and swap use. Compare `VISION_GPU_BACKEND=cpu` vs `tensorrt`.
