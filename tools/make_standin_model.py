"""Makes a TINY FAKE segmentation model (brightness based) - ONLY to test that the plumbing works.
It is NOT a real model: it calls mid-grey pixels 'road' (class 0) and bright pixels 'sidewalk' (class 1).
    pip install onnx numpy
    python tools/make_standin_model.py            ->  models/standin.onnx
    mvn exec:java "-Dexec.mainClass=com.middleware.panorama.wp5.CurbSegmenter" "-Dexec.args=picture.png right" "-Dwp5.seg.model=models/standin.onnx"
"""
import numpy as np, onnx
from onnx import helper, TensorProto, numpy_helper
K, C = 10.0, 19
W = np.zeros((C, 3, 1, 1), np.float32); B = np.full((C,), -100.0, np.float32)
W[0, :, 0, 0] = -K / 3; B[0] = 0.7 * K
W[1, :, 0, 0] = K / 3;  B[1] = -0.7 * K
g = helper.make_graph([helper.make_node("Conv", ["input", "W", "B"], ["output"], kernel_shape=[1, 1])], "standin",
    [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 352, 640])],
    [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, C, 352, 640])],
    [numpy_helper.from_array(W, "W"), numpy_helper.from_array(B, "B")])
m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)]); m.ir_version = 8
onnx.checker.check_model(m); onnx.save(m, "models/standin.onnx"); print("saved models/standin.onnx")
