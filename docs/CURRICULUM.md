# Baden-Württemberg Bildungsplan grounding

Learnova can ground its tutoring in the official Baden-Württemberg Bildungsplan. Imported
plan documents are split into citable chunks. For each lesson, chat turn, answer evaluation,
adaptive question, flashcard set and final exam, the excerpts that match the learner's school
type, grade and topic are added to the tutor instructions.

The code is in `learnova/curriculum/`. The models (`CurriculumDocument`, `CurriculumChunk`,
`User.school_type`) and the `flask curriculum` CLI are in `app.py`.

## Source policy (read first)

The system processes text only from a source whose usage terms a person has confirmed.

| Host | Status when checked (2026-10-18) |
|---|---|
| bildungsplaene-bw.de | Official portal (ZSL). `robots.txt` disallows `*.pdf` for every agent and sets `Crawl-Delay: 2`. The Impressum returned HTTP 500, so the terms could not be read. |
| schule-bw.de | Shut down. The notice "Abschaltung des Landesbildungsservers" is dated 2026-10-12 and redirects to lehrerfortbildung-bw.de. The Beispielcurricula links are gone. |

How the gate works:

- **Web sources must be cleared.** A web source is processed only if its host is listed in
  `CURRICULUM_CLEARED_SOURCES`, a comma-separated environment variable that is empty by
  default. Add a host only after you have checked its terms. Write who checked and when in
  `--rights-note`.
- **robots.txt is always obeyed**, even for a cleared host. PDFs from bildungsplaene-bw.de are
  therefore never fetched by the app. If the terms allow it, download a PDF in a browser and
  import the file with `--source-url` set to the page you downloaded it from.
- **Non-web sources need a rights note.** A teacher's own notes or a school's internal
  curriculum (*Schulcurriculum*) can be imported with a free-text `--source-url` and a
  required `--rights-note`.

## Importing

```bash
# A downloaded PDF (the host must be cleared):
CURRICULUM_CLEARED_SOURCES=bildungsplaene-bw.de \
flask curriculum import ~/Downloads/BP2016BW_ALLG_GYM_M.pdf \
  --source-url "https://www.bildungsplaene-bw.de/,Lde/LS/BP2016BW/ALLG/GYM/M" \
  --plan gymnasium --subject mathematik --grades 5-12 \
  --citation "BP2016 BW Mathematik Gymnasium" \
  --rights-note "Terms checked by <name> on <date>: <what they allow>"

# An HTML page from a cleared host (robots.txt and crawl delay respected):
flask curriculum fetch "https://…" --plan sek1 --subject deutsch --grades 5-10

flask curriculum list
flask curriculum search "Potenzgesetze" --school-type gymnasium --grade 8 --subject Mathematics
flask curriculum remove 3
```

The command options:

- **`--plan`** is one of `grundschule`, `sek1` (the shared plan for Werkrealschule,
  Realschule and Gemeinschaftsschule), `gymnasium` or `berufliche`.
- **`--subject`** is a Bildungsplan subject key, for example `mathematik`, `deutsch`,
  `englisch`, `physik`, `bnt` or `sachunterricht`.
- **`--grades`** is the band of the whole document. A band heading inside the document, such
  as "Klassen 7/8" or "Kursstufe", overrides it for the chunks below that heading.

Importing the same bytes twice does nothing. A scanned PDF has no text layer, so run it
through OCR first.

## How retrieval works

- **School type and grade select the plans searched.** The school type picks a plan family
  (`learnova/curriculum/catalog.py`). If the school type is not set, Klasse 1–4 implies
  Grundschule, and Klasse 5–10 searches both secondary plans. The `outside_bw` school type
  turns grounding off.
- **Subjects are widened in the lower grades.** Learnova subject names are mapped to
  Bildungsplan subjects. Science and history in Klasse 1–4 also search *Sachunterricht*, and
  science in Klasse 5/6 also searches *BNT*.
- **Retrieval uses BM25 over chunk text, with headings counted twice.** It folds umlauts and
  applies light German stemming. It needs no model, no dependency and no AI call. The index
  is cached in-process and rebuilt when the chunk table changes.
- **All grades of the plan are searched.** Chunks at the learner's grade are preferred. A
  topic from a later grade is labelled **LATER**, and the tutor is told to say so, teach the
  prerequisites first and keep the exercises at the learner's level.
- **The block in the instructions is capped.** It holds at most three excerpts and is limited
  by `CURRICULUM_CONTEXT_MAX_CHARS` (default 1800, about 450 tokens).
- **The rules in the block forbid invented references.** The model may not invent
  Bildungsplan references, and may not claim that a topic is or is not in the plan beyond
  what the excerpts show.
- **The feature is off when nothing is imported.** With no documents, prompts are byte-for-byte
  unchanged. `FEATURE_CURRICULUM=false` turns it off entirely.

Learners set their school type under **Settings**.
