from detection_schema import Detection, envelope


def test_distance_fields_only_when_valid():
    d = Detection(
        camera="left",
        confidence=0.8,
        coordinate_space="PANORAMA_PIXELS",
        distance_m=0.5,
        distance_valid=False,
    )
    out = d.to_dict()
    assert out["distanceValid"] is False
    assert "distanceM" not in out

    d.distance_valid = True
    out = d.to_dict()
    assert out["distanceM"] == 0.5


def test_pr4_validity_fields():
    d = Detection(
        camera="front",
        confidence=0.88,
        coordinate_space="PANORAMA_PIXELS",
        detection_valid=True,
        overlay_status="DETECTED",
        calibration_status="UNVERIFIED",
        distance_valid=False,
    )
    out = d.to_dict()
    assert out["detectionValid"] is True
    assert out["overlayStatus"] == "DETECTED"
    assert out["calibrationStatus"] == "UNVERIFIED"
    assert out["distanceValid"] is False


def test_envelope_overlay_status():
    body = envelope(1, 2, 100, 50, [], overlay_status="NO_CURB", camera_statuses={"front": "NO_CURB"})
    assert body["overlayStatus"] == "NO_CURB"
    assert body["cameraOverlayStatus"]["front"] == "NO_CURB"
