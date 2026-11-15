# Community-Karteikarten: filters fixed and redesigned (2026-11-14)

Nothing was committed, pushed or deployed. There is no database change.

## 1. Root cause of the broken filtering

Filtering happens on the server (`GET /api/community/library`); the page sends the form's
values. The page and the API together failed in five ways:

1. **Language could never match.**
   - The language list sends `de` / `en`.
   - A generated set stores its language as an English *name*: `requested_content_language()`
     → "German"/"English" → `FlashcardSet.language` → the published copy.
   - The publish form stores `de`/`en`.
   - The API compared the raw column with `==`, so "German" never equalled `de`, and most sets
     vanished as soon as a language was chosen.
2. **Grade rarely matched.**
   - Grade is free text when a set is published ("9", "Klasse 9", "9. Klasse"), and the filter
     was a free-text box compared with `==`.
3. **Subject spellings were missed.**
   - Exact equality also failed on subjects stored from older forms ("Mathe", "Englisch").
   - The dropdown offered only 8 hard-coded English names.
4. **The visible page did not change.**
   - The three large highlight shelves (Trending / Top rated / Newest) are loaded unfiltered and
     sit *above* the results.
   - A filter changed only the grid below them, so on a phone nothing visible changed.
5. **Results could belong to older filters.**
   - Every keystroke and change sent a new request, with no cancelling and no ordering.
   - A slower answer to an older request could overwrite the newer results.
   - The search box fired a request per letter.

There was also no result count, no reset, no paging (the API cut at 30 and the page never asked
for more), and the filters were not in the address, so Back/Forward and reloading lost them.

## 2. Backend changes (`app.py`, `community_library` and helpers)

- **Normalised filters:**
  - **subject:** `canonical_subject` on both sides ("Mathe" = "Mathematik" = Mathematics);
  - **grade:** the class number on both sides ("9" = "Klasse 9" = "9. Klasse" = "Grade 9");
  - **difficulty:** case-insensitive, `easy`/`medium`/`hard` only;
  - **language:** codes and names (`de` = "German" = "Deutsch");
  - **set type** (`kind`): unchanged, a whitelist.
  - **AI and student rating:** bounded numbers (0–5); anything else is ignored.
  - All filters combine with AND.
- **Search:**
  - case-insensitive; whitespace collapsed; at most 8 words and 100 characters;
  - **every word must match** somewhere in the title, topic, description, subject (its stored
    and canonical form and the translated name, so "mathe" works) or tags;
  - an empty search returns everything the other filters allow.
- **Sorting:** unchanged meanings (Best overall, AI rating, student rating, most studied, most
  saved, newest, trending), with a stable tie-break by id so pages never overlap. An unknown
  sort falls back to Best overall.
- **Paging:**
  - `limit` (1–50, default 30) and `offset`;
  - the answer now has `total`, `offset`, `limit` and `has_more`;
  - `sets` is unchanged, so existing clients (the highlight shelves) still work.
- **Safe and consistent:** no raw SQL; the visibility gate (`visible_set_filters`: approved and
  safety-allowed) still applies first. One implementation of the filters, on the server, over
  the whole library, never only the visible cards.

## 3. Frontend changes

**`templates/community.html`:**
- **One compact bar:** search (with a search icon), set type, subject (the app's full subject
  list, translated), a **Filter** button (sliders icon and a count of the panel's active
  filters) and sort.
- **The panel** (`hidden` until opened, `aria-expanded`/`aria-controls`; opens by itself when
  the address holds one of its filters) holds:
  - grade, as a select of the app's grade list ("Klasse 9");
  - difficulty, language, minimum AI rating, minimum student rating.
  - Each control has a visible label, and the empty choice is "Alle", so no label is cut off.
- **Status row:** removable chips for every active filter, and "Filter zurücksetzen".
- **Results:** a count ("4 Sets"), an empty state with a reset button, and "Mehr laden" (load
  more).
- **The existing options were kept.** Nothing was invented; there is still no
  teacher-verified filter, because nothing writes it.

**`static/js/community.js`:**
- **Request handling:**
  - every load cancels the previous one (`AbortController`), and a late answer of an older
    request is dropped;
  - search is debounced (300 ms); selects apply at once.
- **Filters in the address:** `/community?subject=English&grade=9`.
  - A select adds a history entry; typing replaces it.
  - Back/Forward restore the form and the results; a reload or a shared link opens the same
    view.
- **While filters or a non-default sort are active:** the highlight shelves step aside and the
  heading reads "Passende Sets" (matching sets).
- **Load more** appends the next page with the same filters.
- **"Alle ansehen"** (view all) applies the shelf's sort over the active filters.

**`static/css/learnova-pages.css`** (tokens only, so dark mode follows):
- the old eight-column grid was replaced;
- controls are 44 px with focus outlines and hover states, and the active Filter button is
  highlighted;
- below 720 px the bar wraps: search; then set type and subject; then Filter and sort;
- the panel has two columns (one below 380 px);
- the panel opens with a small animation only when reduced motion is not requested.

**Catalogues:** 8 new strings in German, French and Spanish (counted).

## 4. Tests

**`tests/test_community_filters.py`: 17 passed (API, real routes).**
- Each filter alone:
  - set type, and an unknown type ignored;
  - subject (Englisch = English, Mathe = Mathematics);
  - grade ("9", "Klasse 9", "9. Klasse");
  - difficulty (case, unknown ignored);
  - **language: `de` finds sets stored as "German"/"Deutsch"/"de"** (the root cause);
  - AI and student minimums.
- **Search:** title, topic, keyword, description, case, whitespace, several words (all must
  match), empty search; search plus filters; the subject's name.
- **Combinations:** subject + grade + difficulty; all filters at once; no match → total 0.
- **Sorting with a filter:** newest, AI, studied, saved, ranking; a bogus sort → ranking.
- **Paging:** 3 pages of 3 cover all 8 without repeats; paging with a filter; bad and huge
  limits and offsets are bounded.
- **The page:** it offers the real controls, and no teacher-verified filter.

**`tests/test_community_filters_browser.py`: 6 passed (real Chrome).**
- **At 360, 390, 430, 768 and 1440 px:**
  - the first page shows 30 cards;
  - choosing English shows exactly the 4 English sets, "4 sets", and the shelves step aside;
  - the Filter button opens the panel (`aria-expanded=true`);
  - adding grade 9 and easy shows exactly 2 sets, with the badge "2" and 3 chips;
  - nothing scrolls sideways, and every control is at least 44 px tall;
  - the button closes the panel; reset brings back all cards and the shelves.
- **Language `de`:** shows exactly the 4 German sets.
- **Search:** typing "steigung" letter by letter makes at most 2 requests and shows the one
  match; combined with English → empty state; its reset clears the search.
- **Back/Forward:** walk English + grade 9 → English → all → English, with the controls and
  results restored; a shared link with three filters opens with them and the panel open.
- **Load more:** 38 sets appear with no duplicates, and the button disappears.
- **"View all":** Newest sorts newest first; a subject filter keeps that order and the address
  says so.
- **Dark mode:** the controls are not light boxes.

**Existing tests:** `test_learnova_redesign.py`, `test_community_reports.py`,
`test_publication_visibility.py`, `test_publication_badge_browser.py` and `test_i18n.py` pass
(129 passed, 6 skipped, together with the new files).

**Full regression suite (2026-11-14, after the audit fixes, Reliability V1, vouchers, community filters and readability together):** 3754 passed, 11 skipped, 0 failed (52 min). `instance/` was unchanged.

## 5. Mobile check (screenshots, light and dark)

At 390 px: search on its own row; "Alle Sets" and "Englisch"; "Filtern ①" and "Beste
insgesamt"; the panel in two columns; chips "Englisch ×" and "Klasse 9 ×"; "Filter
zurücksetzen". No label is cut off and nothing scrolls sideways.

At 1440 px: the whole bar is on one row of 44 px, with the panel as a row of five labelled
selects.

Two problems found in screenshots were fixed:
- a too-narrow sort control cut off "Beste insgesamt";
- a chip read "Klasse: Klasse 9".

## 6. Remaining issues

- **Filtering runs in Python** over the visible library: correct for any spelling, and fine at
  today's size (hundreds of sets). At many thousands of sets the normalised values should be
  stored in their own columns and filtered in SQL.
- **Grade normalisation knows class numbers 1–13.** "Q1/Q2", "Oberstufe" or "university" are
  compared as typed (lowercase).
- **The highlight shelves stay unfiltered by design.** They hide while filtering rather than
  showing a filtered copy.
- **The minimum student rating** uses the existing Bayesian average, so sets with few ratings
  sit near the prior. That is unchanged.
