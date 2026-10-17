from temporal_filter import TemporalConfig, TemporalCurbFilter


def test_temporal_requires_stable_frames():
    f = TemporalCurbFilter(TemporalConfig(enabled=True, stable_frames_required=2, stale_timeout_ms=500))
    c = [(10, 10), (20, 20)]
    _, _, ok, st = f.update("front", 1000.0, True, 0.9, c)
    assert not ok and st == "NO_CURB"
    _, _, ok, st = f.update("front", 1050.0, True, 0.9, c)
    assert ok and st == "DETECTED"


def test_temporal_stale_after_timeout():
    f = TemporalCurbFilter(
        TemporalConfig(enabled=True, stable_frames_required=1, stale_timeout_ms=100, hold_on_miss_frames=0)
    )
    c = [(10, 10), (20, 20)]
    f.update("front", 1000.0, True, 0.9, c)
    _, _, ok, st = f.update("front", 1050.0, False, 0.0, [])
    assert not ok and st == "STALE"


def test_inference_error_clears_detection():
    f = TemporalCurbFilter(TemporalConfig(enabled=True, stable_frames_required=1))
    c = [(10, 10), (20, 20)]
    f.update("front", 1000.0, True, 0.9, c)
    _, _, ok, st = f.update("front", 1100.0, False, 0.0, [], inference_error=True)
    assert st == "INFERENCE_ERROR" and not ok
