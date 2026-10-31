"""Shared image understanding for every Learnova feature that accepts a picture.

- `intake`: check, decode, orient, assess and size one uploaded image.
- `reader`: read one or more images with a vision model, in bounded stages.
- `reading`: the structured result every feature consumes, uncertainty included.
- `prompts`: the reading contract and the handwriting/maths rules shared with Project Mode.
- `notation`: canonical maths, for comparing readings by structure.

See docs/VISION.md.
"""

from learnova.vision.intake import (
    ImageProblem,
    IntakeLimits,
    PreparedImage,
    QualityReport,
    data_url,
    fit_data_url,
    image_size,
    limits_from_config,
    prepare,
    provider_copy,
    rotate,
)
from learnova.vision.reader import ImageSource, ReadOutcome, read_images
from learnova.vision.reading import VisualReading, normalize_reading, reading_context, reading_from_dict

__all__ = [
    "ImageProblem", "ImageSource", "IntakeLimits", "PreparedImage", "QualityReport", "ReadOutcome",
    "VisualReading", "data_url", "fit_data_url", "image_size", "limits_from_config", "normalize_reading", "prepare",
    "provider_copy", "read_images", "rotate", "reading_context", "reading_from_dict",
]
