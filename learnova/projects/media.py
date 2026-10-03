"""Pictures for exercises, taken from the student's own scanned pages.

A diagram cropped out of the page a student photographed cannot be the wrong picture for
their material - it *is* their material. That makes it the one image source that needs no
relevance check at all, which is why photo exercises prefer it over anything fetched.

The model never supplies a URL or picks a file. It is offered a numbered list of the
diagrams found on the pages behind the section it is writing about, and may refer to them
by id; anything it names that was not on that list is discarded. The rendered URL is
built by the app from the id, and the route that serves it re-checks ownership.

Framework-free and deterministic: the caller does the database work and passes plain
dicts in, so every rule here is unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


MAX_OFFERED = 8          # a prompt does not need more, and the list is read by a model
MAX_PER_QUESTION = 6     # what fits as ordering tiles on a phone
MAX_LABEL_LENGTH = 120


@dataclass(frozen=True)
class OfferedPicture:
    """One diagram from the student's own page, offered to the question writer."""

    block_id: int
    page_id: int
    labels: str          # the visible text the OCR read inside the diagram

    def as_prompt_entry(self) -> dict[str, Any]:
        return {"block_id": self.block_id, "visible_labels": self.labels}


def offer_pictures(blocks: Any, limit: int = MAX_OFFERED) -> list[OfferedPicture]:
    """Pick the diagrams worth building an exercise on, from one section's pages.

    `blocks` are plain dicts so this stays free of the ORM: each needs `id`, `page_id`,
    `block_type` and `content`. A diagram the recogniser crossed out, or one it could not
    read at all, is not offered - an exercise about an unreadable smudge is worse than no
    exercise.
    """

    offered: list[OfferedPicture] = []
    if not isinstance(blocks, (list, tuple)):
        return offered
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("block_type") or "") != "diagram":
            continue
        if block.get("crossed_out"):
            continue
        if str(block.get("confidence_status") or "") == "unclear":
            continue
        try:
            block_id, page_id = int(block["id"]), int(block["page_id"])
        except (KeyError, TypeError, ValueError):
            continue
        labels = re.sub(r"\s+", " ", str(block.get("content") or "")).strip()[:MAX_LABEL_LENGTH]
        offered.append(OfferedPicture(block_id=block_id, page_id=page_id, labels=labels))
        if len(offered) >= limit:
            break
    return offered


def verify_media(raw: Any, offered: list[OfferedPicture],
                 limit: int = MAX_PER_QUESTION) -> list[dict[str, Any]]:
    """Keep only the pictures the model was actually offered.

    This is the whole security and correctness story for photo exercises: a block id the
    model invented, remembered from another session, or copied from another student's
    project is not in `offered` and is dropped here, long before a URL is built. Returns
    [] when nothing survives, which the caller treats as "this cannot be a photo question".
    """

    allowed = {picture.block_id: picture for picture in offered}
    if isinstance(raw, (int, str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []

    verified: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in raw:
        if isinstance(item, dict):
            value = item.get("block_id", item.get("id"))
        else:
            value = item
        try:
            block_id = int(value)          # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        picture = allowed.get(block_id)
        if picture is None or block_id in seen:
            continue
        seen.add(block_id)
        verified.append({"kind": "page_region", "block_id": block_id,
                         "page_id": picture.page_id, "labels": picture.labels})
        if len(verified) >= limit:
            break
    return verified


def media_urls(media: Any, project_id: int) -> list[dict[str, Any]]:
    """Add the URL the browser should load. Built here, never supplied by the model."""

    entries = []
    for item in media if isinstance(media, (list, tuple)) else []:
        if not isinstance(item, dict) or "block_id" not in item:
            continue
        entries.append({**item,
                        "url": f"/projects/{int(project_id)}/blocks/{int(item['block_id'])}/region"})
    return entries


def photo_question_is_usable(question: Any) -> bool:
    """Whether a photo question has enough verified pictures to be worth asking.

    `photo_ordering` with one tile is not a sorting exercise; `photo_response` with none
    is a question about a picture the student cannot see.
    """

    if not isinstance(question, dict):
        return False
    kind = str(question.get("type") or question.get("question_type") or "")
    media = question.get("media") or []
    if kind == "photo_response":
        return len(media) >= 1
    if kind == "photo_ordering":
        return len(media) >= 2
    return True
