"""Generate a complete interface-translation catalogue for a language.

Usage:  python scripts/translate_catalog.py <code> [<code> ...]

Translates every REQUIRED_KEYS string (the English source strings the German locale
covers) into the target language with a strong multilingual model, validates 100% key
coverage (retrying any misses), and writes learnova/translations/data/<code>.json.
A language auto-enables in the app once its file covers every required key, so a
partial run never exposes half-English UI. Resumable: existing keys are kept.
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from learnova.translations.catalog import LANGUAGE_BY_CODE, REQUIRED_KEYS  # noqa: E402

DATA_DIR = ROOT / "learnova" / "translations" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODEL = os.getenv("GROQ_TRANSLATE_MODEL", "openai/gpt-oss-120b")
CLIENT = OpenAI(api_key=os.environ["GROQ_API_KEY"],
                base_url=os.getenv("GROQ_BASE_URL") or "https://api.groq.com/openai/v1")
BATCH = 30
THROTTLE_S = 9  # stay under Groq free-tier TPM


def translate_batch(strings, language_name, native):
    system = (
        f"You are a professional software UI localizer translating Learnova (a study app) "
        f"into {language_name} ({native}). Translate each English UI string. Rules: return ONLY a "
        f"JSON object mapping each EXACT original English string to its {language_name} translation; "
        f"keep placeholders such as {{name}}, {{count}}, {{n}}, %s and HTML tags unchanged; do NOT "
        f"translate product names (Learnova) or acronyms/tokens (AI, XP, OCR, PDF, KaTeX, ATP, CSV); "
        f"address the learner informally; keep translations concise and natural."
    )
    user = "Translate these UI strings:\n" + json.dumps(strings, ensure_ascii=False)
    resp = CLIENT.chat.completions.create(
        model=MODEL, temperature=0, max_tokens=4000,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    data = json.loads(resp.choices[0].message.content)
    return {k: str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}


def generate(code):
    entry = LANGUAGE_BY_CODE[code]
    out_path = DATA_DIR / f"{code}.json"
    result = {}
    if out_path.exists():
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except ValueError:
            result = {}
    keys = sorted(REQUIRED_KEYS)
    for attempt in range(4):
        missing = [k for k in keys if k not in result or not str(result.get(k, "")).strip()]
        if not missing:
            break
        print(f"[{code}] attempt {attempt + 1}: {len(missing)} strings remaining")
        for i in range(0, len(missing), BATCH):
            chunk = missing[i:i + BATCH]
            try:
                translated = translate_batch(chunk, entry["name"], entry["native"])
                for k in chunk:
                    if k in translated:
                        result[k] = translated[k]
            except Exception as error:  # noqa: BLE001 - log + continue; retried next pass
                print(f"[{code}] batch {i // BATCH} error: {type(error).__name__} {str(error)[:120]}")
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(THROTTLE_S)
    covered = sum(1 for k in keys if str(result.get(k, "")).strip())
    print(f"[{code}] coverage {covered}/{len(keys)} -> {out_path}")
    return covered == len(keys)


if __name__ == "__main__":
    codes = sys.argv[1:] or ["fr", "es", "ar"]
    done = {code: generate(code) for code in codes}
    print("COMPLETE:", {c: ("full" if ok else "partial") for c, ok in done.items()})
