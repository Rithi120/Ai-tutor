# Extending Learnova

## Add a domain capability

1. Put deterministic rules and validation in the matching `learnova/<domain>/` service. Do not read `request`, `session`, or `current_user` there.
2. Put persistence queries in a service/repository function that takes `user_id` explicitly. Every lookup of student-owned data must include it.
3. Keep the route limited to parsing input, calling the service, translating known errors, and rendering/serializing output.
4. Use `api_error()` for failures and include `ok=True` on JSON success responses.
5. Add an additive schema migration and indexes for any new query path. Never overwrite attempts or saved answers.
6. Add translations to the central catalogue, a reusable component under `templates/components/`, and focused JS/CSS modules rather than page-global code.
7. Test happy path, validation, authorization, restart persistence, and failure rollback.

## Add an AI workflow

Task-specific prompt construction belongs to the domain service. Add a stable task name to `SUPPORTED_TASK_TYPES`, call `learnova.ai_services.service.create_response` with `task_type`, `language`, `prompt_version`, and a private user scope where applicable, then validate the complete response before committing. The gateway owns provider access, private caching, and sanitized accounting. Add an English and a German sample response to `tests/fixtures/ai/<code>/valid.json`, update `_meta.prompt_versions`, and add a broken sample for any new failure mode. Never ask the model to decide ownership, scores, mastery, difficulty, or review dates when deterministic code can do so.

## Add a language

Extend `SUPPORTED_LANGUAGES`, the catalogue, the language-selector component, the explicit AI language instruction, and the language isolation/persistence tests. Interface language and generated learning-content language must continue to resolve from the signed-in account.

## Definition of done

Run the complete unit suite, byte compilation, Ruff and Pyright commands from the README. Manually smoke test desktop and mobile layouts, CSRF-protected forms, upload recovery, restart persistence, and cross-account direct URLs.

## Extending the diagnostics engine

* **A new mistake category** — add the tag to `DIAGNOSIS_TAGS` in
  `learnova/diagnostics/taxonomy.py`, give it an English label in `DIAGNOSIS_LABELS_EN`
  plus catalogue entries for every selectable language, and decide whether it belongs in
  `UNDERSTANDING_TAGS` (may reduce difficulty) or `EXECUTION_TAGS` (never does). Add a
  branch to `planner._select` and a labeled case to
  `tests/fixtures/diagnostics/cases.json`.
* **A new next action** — add it to `NEXT_ACTIONS` and `NEXT_ACTION_LABELS_EN`, then to
  `planner._select`, `_demand_for` and `_question_type_for`.
* **A new question check** — add it to `question_spec.validate_question` with an entry in
  `checks`, and add a `"kind": "question"` case with the check listed in
  `expected.failed_checks`.
* **A new language** — the taxonomy labels are ordinary catalogue strings, so
  `scripts/translate_catalog.py <code>` covers them with everything else.

Never add a category that blames the student without evidence, and never let a new tag
skip the evidence-link requirement in `schema.EVIDENCE_REQUIRED_TAGS`.


## Adding a moderated community surface

`learnova.moderation` is content-type agnostic. To moderate a new kind of published item:

1. Add its name to `CONTENT_TYPES` in `learnova/moderation/taxonomy.py`.
2. Flatten the item into `{"kind": ..., "text": ...}` entries, the way
   `prompts.flashcard_items` does for a flashcard set. Include every field a reader can
   see, titles and descriptions included.
3. Call `run_content_moderation` before the item becomes visible, and gate every read
   path on `visible_set_filters()` or its equivalent for the new model.
4. Store the decision with its `content_hash`, and re-moderate on edit. An approval must
   never outlive the text it was made about.

Adding a dimension needs `DIMENSIONS`, a label, a reason code, a role
(`SAFETY_DIMENSIONS` / `FIT_DIMENSIONS` / `SIGNAL_DIMENSIONS`) and a line in
`MODERATION_RULES`. The schema, prompt and stored record follow automatically. The policy
only changes if the new dimension should decide something.

Add cases to `tests/fixtures/moderation/cases.json` for anything new, in English and
German, and keep `scripts/run_moderation_eval.py` passing.
