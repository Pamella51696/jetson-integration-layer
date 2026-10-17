"""POST metric curb readings to existing Java range ingest."""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional


def post_range_sensors(
    base_url: str,
    sensors: List[Dict[str, Any]],
    timeout_s: float = 2.0,
) -> None:
    if not sensors:
        return
    body = json.dumps({"sensors": sensors}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/ingest/range",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=timeout_s).read()


def build_sensor(
    sensor_id: str,
    pos: str,
    distance_m: float,
    pano_x: Optional[int] = None,
    pano_w: Optional[int] = None,
) -> Dict[str, Any]:
    s: Dict[str, Any] = {
        "id": sensor_id,
        "pos": pos,
        "distanceM": round(distance_m, 3),
        "kind": "CURB",
        "source": "VISION",
    }
    if pano_x is not None and pano_w is not None and pano_w > 0:
        s["panoX"] = int(pano_x)
        s["panoW"] = int(pano_w)
    return s
