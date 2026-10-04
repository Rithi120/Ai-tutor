"""Word and sentence translation through dictionary/translation APIs - never an LLM.

The basic scanner's job is "what does this word mean, and this sentence" - a lookup, not
a piece of teaching. A translation API answers that in a few hundred milliseconds for
a fraction of a cent (or free), deterministically, and without a 2k-token reasoning
preamble. Three providers are known:

- DeepL     (`DEEPL_API_KEY`)        - best quality; free tier of 500k characters/month.
- LibreTranslate (`LIBRETRANSLATE_URL`, optional `LIBRETRANSLATE_API_KEY`) - self-hosted or
                                       a paid public instance.
- MyMemory  (no key)                 - translation memory + machine translation, free,
                                       ~5,000 characters/day anonymous, 50,000 with
                                       `MYMEMORY_EMAIL`. The default, so the scanner works
                                       on a fresh install.

They are tried in order; the first answer wins; a failure of one is a reason to try the
next, never a crash. Every answer is cached, so a word two students look up costs one
call, and a word one student taps twice costs none. HTTP is a seam (`http`) so tests and
future providers do not touch the network.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlencode

DEFAULT_TIMEOUT = 4.0
MAX_TEXT_CHARS = 2000
PROVIDER_NAMES = ("deepl", "libretranslate", "mymemory")

# (status_code, body_text). Keyword arguments: params (query), json (body), headers.
HttpCall = Callable[..., tuple[int, str]]


class TranslationError(RuntimeError):
    """One provider could not answer. Carries a short, safe reason."""


@dataclass(frozen=True)
class Translation:
    text: str
    provider: str = ""
    ok: bool = True
    reason: str = ""
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "provider": self.provider, "ok": self.ok,
                "reason": self.reason, "cached": self.cached}


def _http_requests(method: str, url: str, *, params: dict[str, Any] | None = None,
                   json_body: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                   timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """The one place the real network is touched."""

    import requests  # pyright: ignore[reportMissingImports]  # noqa: PLC0415 - optional at import time

    response = requests.request(method, url, params=params, json=json_body, headers=headers,
                                timeout=timeout)
    return response.status_code, response.text


def clean_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def cache_key(text: str, source: str, target: str) -> str:
    """Provider-independent: the same question answered by any provider is the same entry."""

    digest = hashlib.sha256(f"{source}\n{target}\n{clean_text(text).casefold()}".encode("utf-8")).hexdigest()
    return digest[:40]


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def translate(self, text: str, source: str, target: str, *, http: HttpCall, timeout: float) -> str: ...


@dataclass(frozen=True)
class MyMemoryProvider:
    """https://mymemory.translated.net/doc/spec.php - GET, no key, JSON."""

    email: str = ""
    name: str = "mymemory"
    url: str = "https://api.mymemory.translated.net/get"

    def translate(self, text: str, source: str, target: str, *, http: HttpCall, timeout: float) -> str:
        params: dict[str, Any] = {"q": text, "langpair": f"{source}|{target}"}
        if self.email:
            params["de"] = self.email
        status, body = http("GET", self.url, params=params, timeout=timeout)
        if status != 200:
            raise TranslationError(f"mymemory answered {status}")
        data = _json(body)
        response_data = data.get("responseData")
        payload: dict[str, Any] = response_data if isinstance(response_data, dict) else {}
        answer = clean_text(payload.get("translatedText"))
        if int(data.get("responseStatus") or 0) != 200 or not answer:
            raise TranslationError("mymemory had no translation")
        if answer.upper().startswith("MYMEMORY WARNING") or data.get("quotaFinished"):
            raise TranslationError("mymemory quota exhausted")
        # The machine guess for a short lookup is often a fragment of a longer segment;
        # an exact match from the memory for the same word or phrase is the better answer.
        if len(text.split()) <= 3:
            listed = data.get("matches")
            matches: list[Any] = listed if isinstance(listed, list) else []
            exact = [m for m in matches if isinstance(m, dict)
                     and clean_text(m.get("segment")).casefold() == text.casefold()
                     and clean_text(m.get("translation"))]
            if exact:
                best = max(exact, key=lambda m: _quality(m.get("quality")))
                answer = clean_text(best.get("translation"))
        return answer


@dataclass(frozen=True)
class DeepLProvider:
    """https://developers.deepl.com/docs/api-reference/translate - POST JSON with a key header."""

    api_key: str
    name: str = "deepl"

    @property
    def url(self) -> str:
        host = "api-free.deepl.com" if self.api_key.endswith(":fx") else "api.deepl.com"
        return f"https://{host}/v2/translate"

    def translate(self, text: str, source: str, target: str, *, http: HttpCall, timeout: float) -> str:
        targets = {"en": "EN-GB", "pt": "PT-PT"}
        status, body = http(
            "POST", self.url,
            json_body={"text": [text], "source_lang": source.upper(),
                       "target_lang": targets.get(target, target.upper())},
            headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"}, timeout=timeout)
        if status == 456:
            raise TranslationError("deepl quota exhausted")
        if status != 200:
            raise TranslationError(f"deepl answered {status}")
        data = _json(body)
        listed = data.get("translations")
        translations: list[Any] = listed if isinstance(listed, list) else []
        first = translations[0] if translations else None
        answer = clean_text(first.get("text")) if isinstance(first, dict) else ""
        if not answer:
            raise TranslationError("deepl had no translation")
        return answer


@dataclass(frozen=True)
class LibreTranslateProvider:
    """https://libretranslate.com/docs - POST JSON; `api_key` only where the instance wants one."""

    base_url: str
    api_key: str = ""
    name: str = "libretranslate"

    def translate(self, text: str, source: str, target: str, *, http: HttpCall, timeout: float) -> str:
        body: dict[str, Any] = {"q": text, "source": source, "target": target, "format": "text"}
        if self.api_key:
            body["api_key"] = self.api_key
        status, raw = http("POST", self.base_url.rstrip("/") + "/translate", json_body=body, timeout=timeout)
        if status != 200:
            raise TranslationError(f"libretranslate answered {status}")
        answer = clean_text(_json(raw).get("translatedText"))
        if not answer:
            raise TranslationError("libretranslate had no translation")
        return answer


def providers_from_config(config: Any) -> list[Provider]:
    """Build the provider chain from configuration, keyless MyMemory last by default.

    `TRANSLATION_PROVIDERS` names the order ("deepl,mymemory"); a provider without its
    credentials is skipped rather than failing every request later.
    """

    get = config.get if hasattr(config, "get") else (lambda key, default=None: getattr(config, key, default))
    wanted = [part.strip().casefold() for part in str(get("TRANSLATION_PROVIDERS") or ",".join(PROVIDER_NAMES)).split(",")]
    chain: list[Provider] = []
    for name in wanted:
        if name == "deepl" and get("DEEPL_API_KEY"):
            chain.append(DeepLProvider(str(get("DEEPL_API_KEY"))))
        elif name == "libretranslate" and get("LIBRETRANSLATE_URL"):
            chain.append(LibreTranslateProvider(str(get("LIBRETRANSLATE_URL")), str(get("LIBRETRANSLATE_API_KEY") or "")))
        elif name == "mymemory":
            chain.append(MyMemoryProvider(str(get("MYMEMORY_EMAIL") or "")))
    return chain


class Cache(Protocol):
    def get(self, key: str) -> tuple[str, str] | None: ...
    def set(self, key: str, text: str, provider: str) -> None: ...


class MemoryCache:
    """A bounded in-process cache. The app seeds it from its database cache before a
    lookup and stores new answers afterwards, on the request thread, because the
    provider calls run in worker threads that have no database session."""

    def __init__(self, capacity: int = 5000):
        self._items: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def set(self, key: str, text: str, provider: str) -> None:
        with self._lock:
            self._items[key] = (text, provider)
            self._items.move_to_end(key)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)


class Translator:
    """Try providers in order, cache what they say, never raise at the student."""

    def __init__(self, providers: list[Provider], *, cache: Cache | None = None,
                 http: HttpCall | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.providers = list(providers)
        self.cache = cache or MemoryCache()
        self.http = http or _http_requests
        self.timeout = timeout

    def translate(self, text: Any, source: str, target: str) -> Translation:
        cleaned = clean_text(text)
        if not cleaned:
            return Translation("", ok=False, reason="nothing to translate")
        if source == target:
            return Translation(cleaned, provider="identity")
        key = cache_key(cleaned, source, target)
        hit = self.cache.get(key)
        if hit:
            return Translation(hit[0], provider=hit[1], cached=True)
        reasons: list[str] = []
        for provider in self.providers:
            try:
                answer = provider.translate(cleaned, source, target, http=self.http, timeout=self.timeout)
            except TranslationError as error:
                reasons.append(str(error))
                continue
            except Exception as error:  # a timeout, a DNS failure, a malformed body
                reasons.append(f"{provider.name}: {type(error).__name__}")
                continue
            if not answer or answer.casefold() == cleaned.casefold() and " " not in cleaned:
                reasons.append(f"{provider.name}: returned the word unchanged")
                continue
            self.cache.set(key, answer, provider.name)
            return Translation(answer, provider=provider.name)
        return Translation("", ok=False, reason="; ".join(reasons) or "no translation provider configured")

    def translate_many(self, texts: list[Any], source: str, target: str) -> list[Translation]:
        """Several lookups at once (a word and its sentence) without waiting in series."""

        if len(texts) <= 1:
            return [self.translate(text, source, target) for text in texts]
        with ThreadPoolExecutor(max_workers=min(4, len(texts))) as pool:
            return list(pool.map(lambda text: self.translate(text, source, target), texts))


def _json(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as error:
        raise TranslationError("provider returned no JSON") from error
    return data if isinstance(data, dict) else {}


def _quality(value: Any) -> int:
    try:
        return int(re.sub(r"\D", "", str(value)) or 0)
    except ValueError:
        return 0


def query_string(params: dict[str, Any]) -> str:
    """For tests and logs: the GET query a provider would send."""

    return urlencode(params)
