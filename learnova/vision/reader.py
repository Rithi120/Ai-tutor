"""Read images with a vision model, in stages, through whatever gateway the caller passes.

Provider-neutral and Flask-free: the caller hands in `call`, which takes the gateway's
canonical request (task_type, model, instructions, input, ...) and returns an object with
`output_text`. In the app that is `app.create_response`, so routing, budgets, the free
allowance, caching and validation all apply exactly as for any other AI call.

The stages, each spent only when the previous one leaves a reason to:

1. One call reads every image of the request together (they belong to one question).
2. If the model says an image is sideways or upside down, that image is turned and the
   request is read once more. At most once: a model that keeps saying "turn it" is not
   believed a second time.
3. If words are still unclear and the model gave their position, they are cropped,
   enlarged and re-read on one sheet per image - the same second look Project Mode uses,
   because the usual reason messy handwriting fails is that the word is small, not that it
   is unreadable. A re-read only replaces a word when it is more confident.

Nothing here retries a failed call; the gateway's own candidate list does that, bounded.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from learnova.ocr.service import build_region_sheet, normalize_region_review, region_review_instructions
from learnova.vision import intake
from learnova.vision.prompts import SYSTEM, reading_instructions
from learnova.vision.reading import UNCLEAR_MARK, Block, VisualReading, normalize_reading

MAX_IMAGES = 4
SECOND_LOOK_CONFIDENCE = 0.8


@dataclass(frozen=True)
class ImageSource:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ReadOutcome:
    reading: VisualReading
    images: tuple[ImageSource, ...]   # as read: turned upright when stage 2 turned them
    provider_calls: int
    rotated: tuple[int, ...] = ()      # 1-based indices of images that were turned
    second_look_improved: int = 0


def _request(images: Sequence[ImageSource], purpose: str, subject: str, question: str,
             limits: intake.IntakeLimits) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"type": "input_text", "text": reading_instructions(
        purpose, subject=subject, image_count=len(images), question=question)}]
    for number, image in enumerate(images, start=1):
        data, mime = intake.provider_copy(image.data, limits)
        if len(images) > 1:
            parts.append({"type": "input_text", "text": f"Image {number}:"})
        parts.append({"type": "input_image", "image_url": intake.data_url(data, mime), "detail": "high"})
    return [{"role": "user", "content": parts}]


def read_images(
    images: Sequence[ImageSource], *, call: Callable[..., Any], parse: Callable[[str], Any],
    task_type: str, model: str, purpose: str = "chat", subject: str = "", question: str = "",
    limits: intake.IntakeLimits | None = None, max_output_tokens: int = 2500,
    region_task_type: str = "handwriting_region_review", region_tokens: int = 900,
    second_look: bool = True, second_look_regions: int = 8,
) -> ReadOutcome:
    """Read up to four images as one request. Raises whatever `call` raises."""

    if not images:
        raise ValueError("no images to read")
    if len(images) > MAX_IMAGES:
        raise ValueError(f"at most {MAX_IMAGES} images can be read together")
    limits = limits or intake.IntakeLimits()
    current = tuple(images)

    def read(batch: Sequence[ImageSource]) -> VisualReading:
        response = call(task_type=task_type, model=model, instructions=SYSTEM,
                        input=_request(batch, purpose, subject, question, limits),
                        max_output_tokens=max_output_tokens, temperature=0)
        return normalize_reading(parse(str(getattr(response, "output_text", "") or "")), len(batch))

    reading = read(current)
    calls = 1
    rotated: tuple[int, ...] = ()
    turns = {info.index: info.rotation for info in reading.images if info.rotation}
    if turns:
        upright = []
        for number, image in enumerate(current, start=1):
            if number in turns:
                data, mime = intake.rotate(image.data, turns[number])
                upright.append(ImageSource(data, mime))
            else:
                upright.append(image)
        second = read(upright)
        calls += 1
        if second.readable:
            rotated = tuple(sorted(turns))
            current = tuple(upright)
            reading = second.with_warnings(*[
                f"Image {number} was turned upright before reading." for number in rotated])

    improved = 0
    if second_look:
        for number, image in enumerate(current, start=1):
            reading, gained, spent = _second_look(
                reading, number, image, call=call, parse=parse, model=model, subject=subject,
                task_type=region_task_type, max_output_tokens=region_tokens, limit=second_look_regions)
            improved += gained
            calls += spent
    if improved:
        reading = reading.with_warnings(
            f"Re-read {improved} unclear {'part' if improved == 1 else 'parts'} in close-up.")
    return ReadOutcome(reading, current, calls, rotated, improved)


def _unclear(block: Block) -> bool:
    if block.type in {"crossed_out", "diagram"} or not block.bbox:
        return False
    return block.unclear or (block.confidence is not None and block.confidence < SECOND_LOOK_CONFIDENCE)


def _second_look(reading: VisualReading, number: int, image: ImageSource, *, call, parse, model: str,
                 subject: str, task_type: str, max_output_tokens: int, limit: int,
                 ) -> tuple[VisualReading, int, int]:
    """Re-read one image's unclear blocks enlarged. Returns (reading, improved, calls made).

    Never raises: this is an improvement on a reading that already exists, and losing that
    reading because the extra look failed would be worse than not looking.
    """

    candidates = [block for block in reading.blocks if block.image == number and _unclear(block)]
    if not candidates:
        return reading, 0, 0
    fragments = [{"order": block.order, "bbox": list(block.bbox), "content": block.text} for block in candidates]
    try:
        sheet = build_region_sheet(image.data, fragments, limit=limit)
        if sheet is None:
            return reading, 0, 0
        chosen = [item for item in fragments if item["order"] in set(sheet.orders)]
        chosen.sort(key=lambda item: sheet.orders.index(item["order"]))
        response = call(
            task_type=task_type, model=model,
            instructions=("You are a careful handwriting reader. You are shown enlarged close-ups of "
                          "fragments a first pass could not read. Report exactly what each one says, or "
                          "say it is illegible. Never invent a plausible word. Return structured JSON only."),
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": region_review_instructions(subject or "school", chosen)},
                {"type": "input_image", "image_url": intake.data_url(sheet.data, sheet.mime_type), "detail": "high"},
            ]}],
            max_output_tokens=max_output_tokens, temperature=0)
        answers = normalize_region_review(parse(str(getattr(response, "output_text", "") or "")), sheet.orders)
    except Exception:  # noqa: BLE001 - see the docstring
        return reading, 0, 1

    improved = 0
    replaced_text: list[str] = []
    blocks = list(reading.blocks)
    for position, block in enumerate(blocks):
        answer = answers.get(block.order)
        if not answer or block.image != number:
            continue
        before = block.confidence if block.confidence is not None else 0.0
        if answer["confidence"] < 0.6 or answer["confidence"] <= before or UNCLEAR_MARK.search(answer["content"]):
            continue
        replaced_text.append(block.text)
        blocks[position] = replace(block, text=answer["content"], confidence=answer["confidence"],
                                   type="handwriting" if block.type == "uncertain" else block.type)
        improved += 1
    if not improved:
        return reading, 0, 1
    # An uncertainty that belonged to a block now read confidently is resolved; others stay.
    uncertain = tuple(item for item in reading.uncertain
                      if item.image != number or not any(item.text and item.text in old for old in replaced_text))
    question = reading.clarification_question if any(item.affects_answer for item in uncertain) else ""
    return replace(reading, blocks=tuple(blocks), uncertain=uncertain,
                   clarification_question=question), improved, 1
