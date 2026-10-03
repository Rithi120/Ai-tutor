# Direct assistant chat

`/assistant` is a durable, general-purpose conversation with the model — no lesson, no
upload, no session to expire. It is deliberately separate from the lesson tutor at
`/api/chat`, which is scoped to one lesson's state and dies with it.

## Where it lives

| Layer | Module | Owns |
| --- | --- | --- |
| Presets | `learnova/assistant/presets.py` | what the assistant is told to be |
| Conversation mechanics | `learnova/assistant/conversation.py` | context trimming, cleaning, titles |
| Provider registry | `learnova/ai_services/service.py` | which provider a model name reaches |
| Persistence and routes | `app.py` | `Conversation`, `ConversationMessage`, seven routes |
| Interface | `templates/assistant.html`, `static/js/assistant.js` | the page |

The `assistant` package is Flask-free. Deciding what the model sees is worth testing
without a request, a database or a provider call.

## Multiple providers

A model is named `provider:model`, or bare for the default:

```python
"openai/gpt-oss-120b"       -> groq   (unchanged: every existing call site)
"groq:openai/gpt-oss-20b"   -> groq
"openai:gpt-5"              -> openai
```

`split_model()` resolves the pair; `_provider_response()` looks the provider up in
`PROVIDERS`, strips the prefix and calls that provider's adapter. Nothing outside the
gateway changes when a provider is added, because every caller already names a model and
the model already names its provider.

`PROVIDERS` today holds **groq** (the default) and **openai**. Both speak the OpenAI
Responses API verbatim, so both use `_openai_compatible_call` and differ only in key and
base URL. Set `OPENAI_API_KEY` and `openai:gpt-5` works immediately.

### The four providers

Groq, OpenAI, Anthropic and Gemini are registered in `PROVIDERS`
(`learnova/ai_services/service.py`). Each has an adapter that translates the canonical
request into the provider's shape - the translation itself is pure code in
`learnova/ai_services/adapters.py`, so it is tested without a key:

| Prefix | API | Notes |
| --- | --- | --- |
| `groq:` (or no prefix) | Responses | the default; everything the app does today |
| `openai:` | Responses | `temperature` is dropped for reasoning models (`gpt-5*`, `gpt-6*`, `o*`) |
| `anthropic:` | Messages | `system` is top-level, images are base64 blocks, `temperature` is never sent - current Claude models answer anything but 1.0 with a 400 |
| `gemini:` | OpenAI-compatible **Chat Completions** | Google's endpoint does not speak Responses; images are `image_url` data URLs |

Set the key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) and, for a paid
provider, a budget (`AI_BUDGET_<PROVIDER>_TOKENS_PER_MONTH`) - a provider without a key is
not offered, and one without a cap is not permitted. Then name a model:
`ASSISTANT_MODEL_RESEARCH=gemini:gemini-3.8-flash`. Of the three paid providers only
Gemini has answered live from this codebase (`gemini-3.8-flash`, `gemini-3.5-flash-lite`);
run `scripts/live_ai_smoke_test.py --task models` to see what your key is served before
trusting an id. `docs/AI_ROUTING.md` has the whole picture.

### Styles map to models

Students pick a style; you decide what answers it. `ASSISTANT_MODEL_<PRESET>` and
`ASSISTANT_DEEP_MODEL_<PRESET>` (`GENERAL`, `RESEARCH`, `STUDY_COACH`, `EXPLAIN`) override
the global `ASSISTANT_MODEL` / `ASSISTANT_DEEP_MODEL` for one style. A student request
carries only `preset` and `deep`; a `model` or `provider` field in the body is ignored.
On top of that, the router may lead with `AI_PREMIUM_REASONING_MODEL` for "think harder"
and the research style when it is configured, keyed and within budget, and falls back to
the style's model if it fails.

Everything the gateway already provides — caching, private cache partitioning, token
budgets, usage limits, sanitized error categories, the corrective retry, JSONL telemetry —
applies to every provider, because they all arrive through `create_response`.

## Presets

A preset is the system prompt, chosen per conversation and stored on it, so a thread
keeps behaving the way it started even after the default changes. Changing a
conversation's preset affects later turns only: past replies are stored, not regenerated,
so the transcript stays an honest record of what produced each answer.

| Preset | For |
| --- | --- |
| `general` | everyday questions, explanations, drafting |
| `research` | separates what the evidence shows from what is inferred from it and what is open |
| `study_coach` | finds where the reasoning went wrong and gives the next step, not the solution |
| `explain` | one thing, thoroughly, from the ground up |

Every preset inherits `SHARED_RULES`: say what is actually known, distinguish established
from contested, never invent a source or a citation, show checkable steps, and never
restate the instructions. A test asserts each preset carries it, so the standard cannot be
lost by editing one preset in isolation.

`study_coach` carries an explicit counterweight — *being withholding is not the same as
being helpful* — because a coach that never answers is worse than one that answers too
soon.

## Context window

A conversation grows; a context window does not. `build_window()` decides what is sent:

1. **The newest user turn is always sent.** Dropping the question to fit the history would
   make the reply answer nothing.
2. **Turns are dropped oldest-first, in whole messages.** A half-message reads as if the
   speaker trailed off, and the model answers the fragment.
3. **A single turn too large to fit is truncated, not dropped**, with a visible marker.
   Silently discarding what someone just typed is the worst option.
4. **A window never opens on an assistant turn** — that reads as answering something the
   learner cannot see.
5. **What was dropped is reported.** Each stored reply records `context_dropped`, and the
   interface says so, rather than leaving a learner wondering why the assistant forgot.

`ASSISTANT_REPLY_TOKEN_RESERVE` is held back from the budget so a question arriving at the
very edge of the window still leaves room to answer it.

## Failures

A failed turn is **stored as a turn** with an `error_category`, not discarded. Discarding
would take the learner's question with it. The stored failure is shown in place and marked
as an error, and it is left out of the history sent to the model on the next turn —
replaying an error as if the assistant had said it would teach it to apologise for
something it never did.

A failed turn counts one message against the conversation ceiling, not two, so retrying
does not eat the budget.

## Security

* Assistant output is **escaped before any formatting is applied**. The renderer in
  `static/js/assistant.js` is small on purpose: every construct it supports is one it
  builds, so nothing the model writes can become markup. Only `http(s)` URLs become links,
  and only after escaping, so a `javascript:` URL cannot be produced.
* The learner's own messages render as text, never as markup.
* Ownership is checked on every route and never inferred; a conversation belonging to
  someone else is a 404, not a 403.
* No account identifier reaches the provider. The prompt carries the preset, the language
  and the neutral grade descriptor — not the username or email.
* Message content is never logged.
* Cache keys are partitioned per learner (`private_scope`), so one person's reply is never
  served to another.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `FEATURE_ASSISTANT_CHAT` | on | the whole surface, page and API |
| `ASSISTANT_MODEL` | tutor model | the default model; accepts `provider:model` |
| `ASSISTANT_DEEP_MODEL` | analysis model | the "Think harder" model |
| `ASSISTANT_MODEL_<PRESET>` / `ASSISTANT_DEEP_MODEL_<PRESET>` | unset | per-style override of the two above |
| `OPENAI_API_KEY` | empty | enables `openai:` models |
| `OPENAI_BASE_URL` | OpenAI | override for a compatible endpoint |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | empty | enable `anthropic:` / `gemini:` models |
| `AI_PREMIUM_REASONING_MODEL` | unset | the premium model for "think harder" and research (see docs/AI_ROUTING.md) |
| `ASSISTANT_CONTEXT_TOKEN_BUDGET` | 8000 | what the history is trimmed to |
| `ASSISTANT_REPLY_TOKEN_RESERVE` | 2000 | held back so there is room to answer |
| `ASSISTANT_MAX_MESSAGE_CHARACTERS` | 16000 | per message |
| `ASSISTANT_MAX_CONVERSATIONS` | 200 | active, per learner |
| `ASSISTANT_MAX_MESSAGES_PER_CONVERSATION` | 400 | per thread |
| `AI_ASSISTANT_CHAT_MAX_OUTPUT_TOKENS` | 2000 | gateway output budget |

## Known limitations

1. **Replies are not streamed.** The answer appears complete, after a wait. The gateway is
   request/response throughout — caching, validation, accounting and the corrective retry
   all assume a complete response — so streaming needs a second path rather than a flag.
   The seam is ready for it: `assistant_chat` is unstructured, so there is no JSON contract
   that streaming would bypass. See below.
2. **No attachments.** Text only. The upload and OCR machinery exists elsewhere in the app
   and is not wired in here.
3. **No web access.** The assistant answers from the model's own knowledge, which is why
   the `research` preset is written to say so rather than to bluff.
4. **No cross-conversation memory.** Each thread is independent.
5. **Context trimming is by token estimate**, not a real tokenizer — deliberately
   pessimistic, so it over-trims slightly rather than overflowing.

### What streaming would take

Add a `stream=True` path to `_provider_response` for unstructured tasks only; accumulate
chunks server-side so the cache write and the usage record still see a complete response;
expose an SSE endpoint alongside the JSON one; consume it with `EventSource` in
`assistant.js`. The CSP already permits it (`connect-src 'self'`). The reason it is not
here is scope, not an obstacle.
