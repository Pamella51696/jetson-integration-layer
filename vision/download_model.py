#!/usr/bin/env python3
"""Download road-segmentation-adas-0001 (explicit curb class)."""
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "models"
FILES = {
    "road-segmentation-adas-0001.xml": (
        "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/"
        "models_bin/1/road-segmentation-adas-0001/FP32/road-segmentation-adas-0001.xml"
    ),
    "road-segmentation-adas-0001.bin": (
        "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/"
        "models_bin/1/road-segmentation-adas-0001/FP32/road-segmentation-adas-0001.bin"
    ),
}


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        dest = ROOT / name
        print("->", dest.name)
        urllib.request.urlretrieve(url, dest)
    print("Done.", ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
