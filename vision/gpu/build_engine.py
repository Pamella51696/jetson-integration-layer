#!/usr/bin/env python3
"""Build a TensorRT engine from the curb ONNX model (run on the target Jetson)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser(description="ONNX → TensorRT engine for curb_segmentation.onnx")
    p.add_argument(
        "--onnx",
        type=Path,
        default=ROOT / "models" / "curb_segmentation.onnx",
        help="Source ONNX model",
    )
    p.add_argument(
        "--engine",
        type=Path,
        default=ROOT / "models" / "curb_segmentation_orin_fp16.engine",
        help="Output .engine path",
    )
    p.add_argument("--fp16", action="store_true", default=True, help="Enable FP16 (default)")
    p.add_argument("--workspace-mb", type=int, default=256, help="Builder workspace size MiB")
    args = p.parse_args()

    if not args.onnx.is_file():
        print(f"ONNX not found: {args.onnx}", file=sys.stderr)
        return 1

    try:
        import tensorrt as trt  # type: ignore
    except ImportError:
        print("Install JetPack TensorRT Python bindings on the Jetson before building.", file=sys.stderr)
        return 1

    logger = trt.Logger(trt.Logger.INFO)
    builder = trt.Builder(logger)
    network_flags = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
    network = builder.create_network(network_flags)
    parser = trt.OnnxParser(network, logger)

    with open(args.onnx, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                print(parser.get_error(i), file=sys.stderr)
            return 1

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, args.workspace_mb << 20)
    if args.fp16 and builder.platform_has_fast_fp16:
        config.set_flag(trt.BuilderFlag.FP16)

    print(f"Building engine from {args.onnx} …")
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        print("build_serialized_network failed", file=sys.stderr)
        return 1

    args.engine.parent.mkdir(parents=True, exist_ok=True)
    args.engine.write_bytes(serialized)
    print(f"Wrote {args.engine} ({args.engine.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
