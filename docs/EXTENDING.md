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

Task-specific prompt construction belongs to the domain service. Add a stable task name to `SUPPORTED_TASK_TYPES`, call `learnova.ai_services.service.create_response` with `task_type`, `language`, `prompt_version`, and a private user scope where applicable, then validate the complete response before committing. The gateway owns provider access, mock fixtures, private caching, latency simulation, and sanitized accounting. Add deterministic English and German fixtures plus malformed/failure cases. Never ask the model to decide ownership, scores, mastery, difficulty, or review dates when deterministic code can do so.

## Add a language

Extend `SUPPORTED_LANGUAGES`, the catalogue, the language-selector component, the explicit AI language instruction, and the language isolation/persistence tests. Interface language and generated learning-content language must continue to resolve from the signed-in account.

## Definition of done

Run the complete unit suite, byte compilation, Ruff and Pyright commands from the README. Manually smoke test desktop and mobile layouts, CSRF-protected forms, upload recovery, restart persistence, and cross-account direct URLs.
