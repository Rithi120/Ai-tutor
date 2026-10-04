"""Download and load the local OCR models once, so the first scan is not the slow one.

Run at build time (render.yaml does) or by hand:

    python scripts/warm_local_ocr.py

Exit code 0 when an engine is ready, 1 when none can run here; the message says why.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from learnova.ocr import local as local_ocr  # noqa: E402


def main() -> int:
    config = {
        "LOCAL_OCR_ENGINE": os.getenv("LOCAL_OCR_ENGINE", "auto"),
        "LOCAL_OCR_LANGUAGE": os.getenv("LOCAL_OCR_LANGUAGE", "de"),
    }
    started = time.monotonic()
    try:
        name = local_ocr.warm_up(config)
    except local_ocr.LocalOcrUnavailable as error:
        print(f"local OCR unavailable: {error}")
        return 1
    print(f"local OCR ready: {name} in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
