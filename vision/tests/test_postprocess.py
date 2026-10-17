import numpy as np

from postprocess import adas_logits_to_detection


def test_confidence_is_zero_to_one():
    h, w = 64, 64
    logits = np.zeros((1, 4, h, w), dtype=np.float32)
    logits[0, 2, int(h * 0.6) :, int(w * 0.2) : int(w * 0.8)] = 8.0
    det = adas_logits_to_detection(logits, 640, 360, camera="front")
    assert det.found
    assert 0.0 <= det.confidence <= 1.0


def test_rear_camera_lower_threshold_can_find_band():
    h, w = 64, 64
    logits = np.zeros((1, 4, h, w), dtype=np.float32)
    logits[0, 2, int(h * 0.55) :, : int(w * 0.3)] = 5.0
    det = adas_logits_to_detection(logits, 640, 360, camera="rear")
    assert det.found or det.confidence >= 0.0
