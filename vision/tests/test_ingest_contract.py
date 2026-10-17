from metrics.measurement import CurbMetricMeasurement
from metrics.quality import QualityFlags


def test_ingest_sensor_vision_source_and_synthetic_flag():
    m = CurbMetricMeasurement(
        sensor_id="VISION_LEFT_REAR",
        pos="LEFT_REAR",
        camera="left",
        confidence=0.88,
        distance_m=0.82,
        distance_type="GROUND_PLANE_RANGE",
        distance_valid=True,
        calibration_status="SYNTHETIC",
        timestamp_ms=1,
        raw_distance_m=0.82,
        quality=QualityFlags(projection_valid=True, within_calibrated_region=True, calibration_usable=True),
    )
    s = m.to_ingest_sensor(100, 40)
    assert s["source"] == "VISION"
    assert s["kind"] == "CURB"
    assert s["distanceValid"] is True
    assert s["calibrationType"] == "synthetic"
    assert s["distanceType"] == "GROUND_PLANE_RANGE"
