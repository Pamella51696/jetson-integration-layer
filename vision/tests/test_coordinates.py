from coordinate_mapper import camera_bbox_to_panorama


def test_right_camera_bbox_maps_into_strip():
    meta = {
        "panorama": {"width": 2320, "height": 360},
        "cameras": {
            "right": {"x": 560, "y": 0, "w": 640, "h": 360, "source_w": 640, "source_h": 360},
        },
    }
    x0, y0, x1, y1 = camera_bbox_to_panorama(meta, "right", 100, 200, 300, 350)
    assert x0 >= 560 and x1 <= 560 + 640
    assert 0 <= y0 < y1 <= 360
