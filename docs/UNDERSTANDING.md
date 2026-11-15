# Understanding the student's question

`learnova/understanding/` is the shared layer that makes every AI mode answer the question
the student actually asked. It is pure Python, Flask-free and makes no model calls.

## Why it exists

On 15 Nov 2026, the chat model (`openai/gpt-oss-20b`) answered "Was bedeutet Marketingsog?"
as "Was bedeutet ein Marketing-Slogan?". A live baseline of 36 word questions on the old
prompts found more of the same class:

- **Invented meaning.** "Marketingsog" became "Marketing-SOG = Standard Operating Guidelines".
- **Wrong reading.** "jüngster Teil der Reihe" was read as the last number of a sequence.
- **One meaning only.** "Bank" got only its money sense.
- **Unasked exercises.** 33 of 34 definition answers added a "Kurze Übung" or a check question.

The prompts had no rule to keep the student's term, and nothing checked the reply.

## The parts

| Part | What it does | Cost |
|---|---|---|
| Rules (`contract_rule`) | Added by `ai_services/prompts.output_contract` to every request of a task type, so no caller can leave them out. `UNDERSTANDING_RULE` goes to the chat tasks, `SOURCE_TERMS_RULE` to lessons, practice, exams, grading and flashcards, `TRANSLATION_RULE` to translation, `READING_RULE` to the photo and page readers. | ~45 tokens per request; ~220 for the two chat tasks |
| `find_focus(text, previous)` | Finds which word, phrase or pair of terms a message asks about, exactly as written, with the sentence the student supplied. Covers DE, EN, FR and ES patterns, and follow-ups like "Unterschied zu …". Messages that name no word return an empty focus, and an empty focus changes nothing. | none (regex) |
| `focus_block(focus, mode)` | A per-turn QUESTION FOCUS block, added to the input after the transcript so the system prompt stays cacheable. Modes: `answer` (chat), `tutor` (lesson chat: the lesson's subject decides a technical reading), `lesson` (Short Lesson goal), `card` (flashcard back). | ~120–250 tokens, only for word questions |
| `check_reply(focus, reply)` | A deterministic check that the reply names the student's term. It accepts inflections, hyphen and spacing variants, and a corrected one-letter typo. Otherwise it fails and lists the similar-looking words used instead. | none |
| `app.recheck_reply` | When the check fails, it makes **one** more call with a hidden `correction_note`: the full original request plus the note, with `fresh=True`. It never loops. If that call fails, the first reply is kept. It logs `understanding.check_failed` at WARNING with counts only, never content. | one extra call, only on a failed check |

## Where it is wired in

| Mode | Rule (contract) | Focus block | Check and one retry |
|---|---|---|---|
| Chat homepage (`assistant_chat`), including photo homework | yes | yes (`answer`) | yes |
| Lesson tutor chat (`tutor_chat`, also inside Project Mode lessons) | yes | yes (`tutor`) | yes |
| Short Lessons (`/api/analyze`, `lesson_generation`) | yes | yes (`lesson`) for a word goal | yes (lesson title, concepts, explanation, parts) |
| Project Mode lessons, Continue Learning lessons, practice, quizzes, mock exams, grading | yes (`SOURCE_TERMS_RULE`) | n/a: no student question | n/a |
| Project topic map (`project_section_generation`) | yes, still v4 (see `prompts.py`) | n/a | n/a |
| Flashcard generation | yes | n/a | n/a |
| Flashcard "suggest a definition" | yes | yes (`card`) | look-alike definitions are dropped |
| Vocabulary translation (`translation`) | yes (`TRANSLATION_RULE`) | n/a | n/a |
| Photo, page and vocabulary-page readers | yes (`READING_RULE`) | n/a | n/a |
| Chat photos | `CHAT_IMAGE_RULES` separates an unclear photo (`[?…]`) from a rare but clearly read word | | |

Tasks without a rule are listed with a reason in `tests/test_understanding.py` (`EXEMPT`):
moderation, report and set reviews, the diagnosis contract, and Kompetenzraster extraction.
A test fails when a new task type is in neither list.

The Lernplaner and Prüfungsplaner (part of Projektmodus) and the learning recommendations
are deterministic: they make no model call, so there is nothing to integrate.

## Limits

- The check catches a reply that explains a **different word**. It cannot see an invented
  meaning for the right word, such as the "SOG" acronym; the rules and the focus block
  address that. The live evaluation measures both (`UNDERSTANDING_IMPLEMENTATION.md`).
- Focus extraction only reads explicit word questions. "Was ist der Grund für …" is a
  question about the world and gets no focus on purpose.
