from frame_adapter import (
    CoordinateSpace,
    build_layout_context,
    map_contour_to_output,
    scale_contour,
    validate_camera_in_meta,
)


def test_scale_contour_maps_resolution():
    pts = [[100, 50], [200, 150]]
    out = scale_contour(pts, 640, 360, 1280, 720)
    assert out == [[200, 100], [400, 300]]


def test_panorama_map_rejects_unknown_camera():
    meta = {
        "panorama": {"width": 1000, "height": 200},
        "cameras": {"front": {"x": 0, "y": 0, "w": 250, "h": 200, "source_w": 640, "source_h": 360}},
    }
    layout = build_layout_context(meta, {"allow_single_camera": True}, 1000, 200)
    contour, space, err = map_contour_to_output(layout, "left", [(10, 10), (20, 30)])
    assert err is not None
    assert contour == []


def test_single_camera_layout_uses_camera_pixels():
    layout = build_layout_context(None, {"allow_single_camera": True}, 2320, 360)
    assert layout.coordinate_space == CoordinateSpace.CAMERA_PIXELS
    contour, space, err = map_contour_to_output(layout, "front", [(1, 2), (3, 4)])
    assert err is None
    assert space == CoordinateSpace.CAMERA_PIXELS
    assert contour == [[1, 2], [3, 4]]


def test_validate_camera_in_meta():
    meta = {"cameras": {"front": {}, "left": {}}}
    assert validate_camera_in_meta(meta, "front")
    assert not validate_camera_in_meta(meta, "rear")
