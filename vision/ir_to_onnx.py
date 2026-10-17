#!/usr/bin/env python3
"""
Convert the OpenVINO IR model  road-segmentation-adas-0001 (.xml + .bin)  into a plain ONNX file.

Why this script exists: OpenVINO has no "IR -> ONNX" exporter, and the model is only published as IR.
This model uses only a handful of simple operations (Conv, GroupConv, Add, ReLU, MaxPool, AvgPool, bilinear
Resize, Softmax), so each one is written out 1:1 as an ONNX node. The fixed input size 1x3x512x896 is kept.

Usage (inside the vision folder):
    pip install openvino onnx onnxruntime numpy
    python ir_to_onnx.py                                   # models/road-segmentation-adas-0001.xml -> models/curb_segmentation.onnx
    python ir_to_onnx.py in.xml out.onnx

After writing the file the script runs the same random pictures through OpenVINO and through onnxruntime and
refuses to finish unless the two outputs agree (max abs difference < 1e-3).

The ONNX output is exactly what OpenVINO outputs: 1x4x512x896 class PROBABILITIES
(background, road, curb, mark; the final Softmax is part of the model).
"""
import sys
from pathlib import Path

import numpy as np
import onnx
import openvino as ov
from onnx import TensorProto, helper, numpy_helper

HERE = Path(__file__).resolve().parent


def convert(xml_path: Path, onnx_path: Path) -> None:
    model = ov.Core().read_model(str(xml_path))
    ops = list(model.get_ordered_ops())

    def key(node, idx=0):
        return f"{node.get_friendly_name()}:{idx}"

    # --- find the ops that really matter: walk back from the Result. Interpolate's size inputs (ShapeOf/StridedSlice/
    #     Multiply) are NOT followed: the output size is fixed, so it is written as a constant instead.
    needed = set()

    def need(node):
        name = node.get_friendly_name()
        if name in needed:
            return
        needed.add(name)
        follow = 1 if node.get_type_name() == "Interpolate" else node.get_input_size()
        for i in range(follow):
            src = node.input(i).get_source_output()
            need(src.get_node())

    results = [o for o in ops if o.get_type_name() == "Result"]
    assert len(results) == 1, "expected one model output"
    need(results[0])

    nodes, inits, extra = [], [], 0
    graph_input = None
    graph_output = None

    def in_name(node, i):
        src = node.input(i).get_source_output()
        return key(src.get_node(), src.get_index())

    for op in ops:
        t, name = op.get_type_name(), op.get_friendly_name()
        if name not in needed:
            continue
        out = key(op)
        if t == "Parameter":
            shape = [int(d) for d in op.get_output_shape(0)]
            graph_input = helper.make_tensor_value_info("input", TensorProto.FLOAT, shape)
            nodes.append(helper.make_node("Identity", ["input"], [out]))
        elif t == "Constant":
            inits.append(numpy_helper.from_array(op.get_data().astype(np.float32), out))
        elif t == "Convolution":
            a = op.get_attributes()
            nodes.append(helper.make_node(
                "Conv", [in_name(op, 0), in_name(op, 1)], [out],
                strides=a["strides"], dilations=a["dilations"], pads=list(a["pads_begin"]) + list(a["pads_end"]), group=1))
        elif t == "GroupConvolution":
            a = op.get_attributes()
            w = op.input(1).get_source_output().get_node().get_data()        # [G, Cout/G, Cin/G, kH, kW]
            g = int(w.shape[0])
            wname = f"{name}:w_reshaped"
            inits.append(numpy_helper.from_array(w.reshape(g * w.shape[1], *w.shape[2:]).astype(np.float32), wname))
            nodes.append(helper.make_node(
                "Conv", [in_name(op, 0), wname], [out],
                strides=a["strides"], dilations=a["dilations"], pads=list(a["pads_begin"]) + list(a["pads_end"]), group=g))
        elif t == "Add":
            nodes.append(helper.make_node("Add", [in_name(op, 0), in_name(op, 1)], [out]))
        elif t == "Relu":
            nodes.append(helper.make_node("Relu", [in_name(op, 0)], [out]))
        elif t == "MaxPool":
            a = op.get_attributes()
            nodes.append(helper.make_node(
                "MaxPool", [in_name(op, 0)], [out], kernel_shape=a["kernel"], strides=a["strides"],
                pads=list(a["pads_begin"]) + list(a["pads_end"]), ceil_mode=1 if a["rounding_type"] == "ceil" else 0))
        elif t == "AvgPool":
            a = op.get_attributes()
            nodes.append(helper.make_node(
                "AveragePool", [in_name(op, 0)], [out], kernel_shape=a["kernel"], strides=a["strides"],
                pads=list(a["pads_begin"]) + list(a["pads_end"]), ceil_mode=1 if a["rounding_type"] == "ceil" else 0,
                count_include_pad=0 if a["exclude-pad"] else 1))
        elif t == "Interpolate":
            a = op.get_attributes()
            assert a["mode"] == "linear_onnx" and a["coordinate_transformation_mode"] == "align_corners", a
            size_name = f"{name}:sizes"
            inits.append(numpy_helper.from_array(np.array(op.get_output_shape(0), dtype=np.int64), size_name))
            nodes.append(helper.make_node(
                "Resize", [in_name(op, 0), "", "", size_name], [out],
                mode="linear", coordinate_transformation_mode="align_corners"))
        elif t == "Softmax":
            nodes.append(helper.make_node("Softmax", [in_name(op, 0)], [out], axis=int(op.get_attributes()["axis"])))
        elif t == "Result":
            shape = [int(d) for d in op.get_output_shape(0)] if op.get_output_size() else [1, 4, 512, 896]
            graph_output = helper.make_tensor_value_info("output", TensorProto.FLOAT, shape)
            nodes.append(helper.make_node("Identity", [in_name(op, 0)], ["output"]))
        else:
            raise RuntimeError(f"operation {t!r} ({name}) is not supported by this converter")

    graph = helper.make_graph(nodes, "road_segmentation_adas_0001", [graph_input], [graph_output], initializer=inits)
    onnx_model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], producer_name="ir_to_onnx")
    onnx_model.ir_version = 8
    onnx.checker.check_model(onnx_model)
    onnx.save(onnx_model, str(onnx_path))
    print(f"wrote {onnx_path}  ({onnx_path.stat().st_size / 1e6:.2f} MB), {len(nodes)} nodes")


def verify(xml_path: Path, onnx_path: Path) -> float:
    import onnxruntime as ort

    ovm = ov.Core().compile_model(ov.Core().read_model(str(xml_path)), "CPU")
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(3):
        x = (rng.random((1, 3, 512, 896)) * 255).astype(np.float32)
        a = ovm(x)[0]
        b = sess.run(None, {"input": x})[0]
        worst = max(worst, float(np.abs(a - b).max()))
    print(f"OpenVINO vs ONNX max abs difference on random pictures: {worst:.2e}")
    return worst


if __name__ == "__main__":
    xml = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "models" / "road-segmentation-adas-0001.xml"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "models" / "curb_segmentation.onnx"
    convert(xml, out)
    if verify(xml, out) > 1e-3:
        sys.exit("ONNX output does not match OpenVINO - do not use this file")
    print("OK: ONNX model matches OpenVINO.")
