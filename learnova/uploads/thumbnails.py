"""Small previews of stored project pages, for the page gallery (the 'thumb' image variant).

A workspace with forty pages used to have no way to see them at all, and the only other
picture of a page is the full original (up to 15 MB) or the processed page (1-2 MB). A
thumbnail is a ~20 kB JPEG at most THUMB_EDGE px on its long side. JPEGs are decoded with
Pillow's draft mode (the decoder scales by 1/2, 1/4 or 1/8 while reading), so a 12 MP
photo is never decoded in full for a preview.

Made on first view and kept in a small in-process cache keyed by what decides the picture
(page id, file digest, rotation, retake), so scrolling the gallery again costs nothing; the
browser keeps it too (private, one hour, ETag). Nothing here is sent to an AI provider.
"""

from __future__ import annotations

import hashlib
import io
import threading
from collections import OrderedDict

from PIL import Image, ImageOps

THUMB_EDGE = 360
THUMB_QUALITY = 78
# Thumbnails are ~10-30 kB: 256 of them stay well under 10 MB of memory.
CACHE_ENTRIES = 256
# A picture larger than this is not decoded for a preview (Pillow's own bomb limit is
# higher); the gallery shows the page's tile instead.
MAX_PIXELS = 60_000_000


def make_thumbnail(data: bytes, *, rotation: int = 0, edge: int = THUMB_EDGE) -> bytes:
    """A JPEG of `data` (any image Pillow reads) at most `edge` px on its long side,
    upright (EXIF orientation applied), turned clockwise by `rotation` degrees, on white.

    Raises ValueError for bytes that are no readable image."""

    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width < 1 or height < 1 or width * height > MAX_PIXELS:
            raise ValueError("image too large for a thumbnail")
        # Draft mode: the JPEG decoder reads at a fraction of the size (no-op for others).
        image.draft("RGB", (edge * 2, edge * 2))
        image.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError("unreadable image") from error
    upright = ImageOps.exif_transpose(image)
    if upright is not None:
        image = upright
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        paper = Image.new("RGB", image.size, "white")
        paper.paste(image, mask=image.getchannel("A"))
        image = paper
    elif image.mode != "RGB":
        image = image.convert("RGB")
    turn = int(rotation or 0) % 360
    if turn:
        image = image.rotate(-turn, expand=True)  # PIL turns anticlockwise
    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return output.getvalue()


def thumbnail_key(*parts: object) -> str:
    """A stable short key (also the ETag) for whatever decides a thumbnail's picture."""

    return hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:24]


class ThumbnailCache:
    """A bounded, thread-safe least-recently-used map of key -> JPEG bytes."""

    def __init__(self, entries: int = CACHE_ENTRIES):
        self._entries = entries
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            data = self._items.get(key)
            if data is not None:
                self._items.move_to_end(key)
            return data

    def put(self, key: str, data: bytes) -> None:
        with self._lock:
            self._items[key] = data
            self._items.move_to_end(key)
            while len(self._items) > self._entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
