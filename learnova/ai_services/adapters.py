"""Translate the gateway's canonical request into each provider's shape, and back.

Pure and SDK-free: nothing here imports `openai` or `anthropic`, nothing reads Flask
config. `service.py` does the network calls; this module only reshapes dictionaries, so
every translation can be tested without a key and the provider boundary stays in one file
(tests/test_ai_gateway.py::test_provider_boundary_is_centralized enforces that).

The canonical request is the OpenAI *Responses* API shape, because that is what every
call site in the application speaks: `instructions` (system text), `input` (a string, or
one user message whose content is `input_text`/`input_image` parts), `max_output_tokens`,
optional `temperature`, optional `reasoning`.

Three providers, three shapes:

- **OpenAI** speaks Responses natively. Its reasoning models (gpt-5*, gpt-6*, o*) reject
  `temperature`, so it is dropped for them.
- **Anthropic** speaks Messages: `system` is top-level, images are base64 blocks, and
  `temperature` is deprecated - current models answer anything but 1.0 with a 400, and
  every call site here sends 0-0.3. So it is dropped for every Anthropic call. Losing it
  on the few older models that still accept it costs a little determinism; keeping it
  would break every current one.
- **Gemini** is reached through Google's OpenAI-compatible endpoint, which speaks Chat
  Completions only (not Responses): a system message, `image_url` parts with a data URL,
  `max_tokens`, `reasoning_effort`.

None of the three has been exercised live from this codebase at the time of writing (no
keys). What is verified is the *shape*; see docs/AI_ROUTING.md for what is not.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any

REASONING_MODEL_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")
IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
DEFAULT_IMAGE_DETAIL = "high"


@dataclass(frozen=True)
class Part:
    kind: str                 # "text" | "image"
    text: str = ""
    data_url: str = ""


@dataclass(frozen=True)
class ProviderResult:
    """What every adapter returns; `_gateway_response` reads exactly these names."""

    output_text: str
    model: str
    usage: "ProviderUsage"
    stop_reason: str = ""
    refusal: bool = False


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


# ---- the canonical input, taken apart --------------------------------------------------

def parse_data_url(value: str) -> tuple[str, str]:
    """Split `data:<media type>;base64,<payload>` into its two parts, validating both."""

    text = str(value or "").strip()
    if not text.startswith("data:") or ";base64," not in text:
        raise ValueError("not a base64 data URL")
    header, payload = text[5:].split(";base64,", 1)
    media_type = header.split(";", 1)[0].strip().lower()
    if media_type not in IMAGE_MEDIA_TYPES:
        raise ValueError(f"unsupported image type {media_type!r}")
    try:
        base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("data URL payload is not valid base64") from error
    return media_type, payload


def input_parts(canonical_input: Any) -> list[Part]:
    """Normalise a string or a Responses-style message list into neutral parts.

    Call sites send exactly one user message with `input_text` and `input_image` parts;
    anything else is reported rather than guessed at, because an adapter that silently
    dropped a part would send the model a different question than the student asked.
    """

    if canonical_input is None:
        return []
    if isinstance(canonical_input, str):
        return [Part("text", text=canonical_input)] if canonical_input else []
    if not isinstance(canonical_input, list):
        raise ValueError("input must be a string or a list of messages")
    parts: list[Part] = []
    for message in canonical_input:
        if not isinstance(message, dict):
            raise ValueError("every message must be an object")
        if str(message.get("role") or "user") != "user":
            raise ValueError("only user messages are supported in canonical input")
        content = message.get("content")
        if isinstance(content, str):
            parts.append(Part("text", text=content))
            continue
        if not isinstance(content, list):
            raise ValueError("message content must be text or a list of parts")
        for item in content:
            if not isinstance(item, dict):
                raise ValueError("every content part must be an object")
            kind = str(item.get("type") or "")
            if kind in {"input_text", "text"}:
                parts.append(Part("text", text=str(item.get("text") or "")))
            elif kind in {"input_image", "image_url"}:
                url = item.get("image_url")
                if isinstance(url, dict):
                    url = url.get("url")
                parts.append(Part("image", data_url=str(url or "")))
            else:
                raise ValueError(f"unsupported content part {kind!r}")
    return parts


def joined_text(parts: list[Part]) -> str:
    return "\n\n".join(part.text for part in parts if part.kind == "text" and part.text)


def is_reasoning_model(model: str) -> bool:
    return str(model or "").lower().startswith(REASONING_MODEL_PREFIXES)


def supports_temperature(provider: str, model: str) -> bool:
    if provider == "anthropic":
        return False
    if provider == "openai" and is_reasoning_model(model):
        return False
    return True


# ---- requests ------------------------------------------------------------------------

def openai_responses_request_from(request: dict[str, Any]) -> dict[str, Any]:
    """The canonical shape *is* the Responses shape; only drop what the model rejects."""

    out = dict(request)
    if is_reasoning_model(str(out.get("model") or "")):
        out.pop("temperature", None)
    return out


def chat_completion_request_from(request: dict[str, Any]) -> dict[str, Any]:
    """Canonical → Chat Completions (Gemini's OpenAI-compatible endpoint)."""

    content: list[dict[str, Any]] = []
    for part in input_parts(request.get("input")):
        if part.kind == "text":
            content.append({"type": "text", "text": part.text})
        else:
            parse_data_url(part.data_url)      # fail here, not at the provider
            content.append({"type": "image_url", "image_url": {"url": part.data_url}})
    messages: list[dict[str, Any]] = []
    instructions = str(request.get("instructions") or "")
    if instructions:
        messages.append({"role": "system", "content": instructions})
    # A text-only turn is sent as a plain string, which every compatible endpoint takes;
    # the parts form is only needed once an image is involved.
    if content and all(item["type"] == "text" for item in content):
        messages.append({"role": "user", "content": "\n\n".join(item["text"] for item in content)})
    else:
        messages.append({"role": "user", "content": content})
    out: dict[str, Any] = {"model": request["model"], "messages": messages}
    if request.get("max_output_tokens"):
        out["max_tokens"] = int(request["max_output_tokens"])
    if "temperature" in request and request["temperature"] is not None:
        out["temperature"] = request["temperature"]
    reasoning = request.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        out["reasoning_effort"] = str(reasoning["effort"])
    return out


def anthropic_request_from(request: dict[str, Any]) -> dict[str, Any]:
    """Canonical → Messages. `temperature` is deliberately never forwarded (see module doc)."""

    blocks: list[dict[str, Any]] = []
    for part in input_parts(request.get("input")):
        if part.kind == "text":
            blocks.append({"type": "text", "text": part.text})
        else:
            media_type, payload = parse_data_url(part.data_url)
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": media_type, "data": payload}})
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    out: dict[str, Any] = {
        "model": request["model"],
        "max_tokens": int(request.get("max_output_tokens") or 1024),
        "messages": [{"role": "user", "content": blocks}],
    }
    instructions = str(request.get("instructions") or "")
    if instructions:
        out["system"] = instructions
    return out


# ---- responses --------------------------------------------------------------------------

def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def responses_to_canonical(response: Any, requested_model: str) -> ProviderResult:
    usage = getattr(response, "usage", None)
    return ProviderResult(
        output_text=str(getattr(response, "output_text", "") or ""),
        model=str(getattr(response, "model", "") or requested_model),
        usage=ProviderUsage(_int(getattr(usage, "input_tokens", 0)), _int(getattr(usage, "output_tokens", 0)),
                            _int(getattr(usage, "total_tokens", 0))),
        stop_reason=str(getattr(response, "status", "") or ""),
    )


def chat_completion_to_canonical(completion: Any, requested_model: str) -> ProviderResult:
    """Chat Completions names its usage fields differently; without this they would be
    read as zero and silently *estimated* by the gateway."""

    choices = list(getattr(completion, "choices", None) or [])
    message = getattr(choices[0], "message", None) if choices else None
    content = getattr(message, "content", "") if message is not None else ""
    if isinstance(content, list):      # some compatible endpoints return parts
        content = "".join(str(getattr(item, "text", None) or item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")) for item in content)
    finish = str(getattr(choices[0], "finish_reason", "") or "") if choices else ""
    usage = getattr(completion, "usage", None)
    prompt = _int(getattr(usage, "prompt_tokens", 0))
    completion_tokens = _int(getattr(usage, "completion_tokens", 0))
    return ProviderResult(
        output_text=str(content or ""),
        model=str(getattr(completion, "model", "") or requested_model),
        usage=ProviderUsage(prompt, completion_tokens, _int(getattr(usage, "total_tokens", 0)) or prompt + completion_tokens),
        stop_reason=finish,
        refusal=finish == "content_filter",
    )


def anthropic_message_to_canonical(message: Any, requested_model: str) -> ProviderResult:
    """Join the text blocks, skip thinking blocks, map usage, and surface a refusal."""

    texts: list[str] = []
    for block in list(getattr(message, "content", None) or []):
        kind = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if kind == "text":
            texts.append(str(getattr(block, "text", None) or (block.get("text", "") if isinstance(block, dict) else "")))
    usage = getattr(message, "usage", None)
    input_tokens = _int(getattr(usage, "input_tokens", 0))
    output_tokens = _int(getattr(usage, "output_tokens", 0))
    stop_reason = str(getattr(message, "stop_reason", "") or "")
    return ProviderResult(
        output_text="".join(texts),
        model=str(getattr(message, "model", "") or requested_model),
        usage=ProviderUsage(input_tokens, output_tokens, input_tokens + output_tokens),
        stop_reason=stop_reason,
        refusal=stop_reason == "refusal",
    )
