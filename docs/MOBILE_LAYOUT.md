# Mobile layout

Most students use Learnova on a phone, so the phone layout is the primary target. The
desktop composition is unchanged.

## Where the rules live

| File | What it holds |
|---|---|
| `static/css/navigation.css` | The app bar and both menus (logged in and guest). Phone rules apply at ≤ 820 px. |
| `static/css/learnova-responsive.css` | Loaded last on every page. Containment (long words, tables, formulas), touch targets, the 16 px input floor, lesson reading sizes, the chat bottom sheet, focus rings and reduced motion. It holds sizing only; colours stay in the token files, so dark mode needs nothing extra. |
| `static/css/learnova-tokens.css` | `--ln-gutter`, `--ln-tap`, `--ln-text-body`, `--ln-text-small`, `--ln-leading-read`, `--ln-heading-page`. |

## Header behaviour

**Logged in, ≤ 820 px.** The bar shows the brand, the level chip, the theme switch and the
menu button. The menu holds every navigation link, the account links and a labelled theme
switch. Below 431 px the chip drops its progress bar. Below 375 px the theme switch moves
into the menu.

**Logged out, ≤ 700 px.** The bar shows the brand, *Log in* and the menu button; *Create
account* is also shown above 520 px. The guest menu holds *Create account* (first, as the
primary action), *Log in*, *About*, *Settings* and the theme switch.

**Both menus:**

- They close on Escape, on the × button, on the backdrop, after following a link, and when
  the window widens past the phone layout.
- Focus moves into the menu when it opens, stays inside it while it is open, and returns to
  the menu button when it closes.

## Checking a change

```bash
pip install -r requirements-dev.txt
python scripts/mobile_audit.py                         # every page x 7 viewports x light/dark
python scripts/mobile_audit.py --pages /about LESSON --widths 320 390 --screenshots out/ --fold
python scripts/mobile_audit.py --list-light-surfaces   # also list light panels in dark mode
python scripts/mobile_audit.py --ignore-console REGEX  # set a known console error aside
python -m pytest tests/test_mobile_layout.py           # the same checks inside the test suite
```

In Git Bash, set `MSYS_NO_PATHCONV=1` first, or `/about` is rewritten into a Windows path.

The audit runs the app on a temporary SQLite database with the AI mocked and creates what
the data-dependent pages need:
- lessons (one with a written question);
- two projects;
- a study plan;
- an open and a submitted practice exam;
- a flashcard set;
- a concept.

It also attaches a photo in both chats. It covers every page the app renders, including
the admin and internal ones, for which the audit account is made an admin on the
throwaway server. It renders each page in the installed Chrome or Edge and fails on any of
these:

- the page scrolls sideways;
- a control is clipped;
- header controls overlap;
- the menu is missing or broken;
- a JavaScript error: an uncaught exception or a console error (load failures of what the
  audit blocks on purpose are excluded);
- an HTTP error, or a redirect somewhere other than the page asked for (two intentional
  redirects are listed in `EXPECTED_REDIRECTS`);
- in dark mode, light text on a light surface (the background of the nearest ancestor
  that paints one).

A page whose data could not be created is named as "not audited", never measured as a
404. The audit also reports, without failing, tap targets under 44 px and text under
14 px.

Web fonts and ads are blocked during audits, so pages render with the fallback font stack.
That stack is also what a student on a slow connection sees first.

## Pitfalls that caused real bugs

- **Gaps on phones.** The desktop bar's 30 px gaps pushed the menu button off-screen on
  every phone. Phone rules must set their own gap and padding.
- **Fixed elements inside animated containers.** A transform animation with `fill-mode: both`
  turns its element into the containing block for `position: fixed` children. On `<main>`
  this moved the tutor chat and its button. Entrance animations use `backwards`.
- **Absolutely positioned labels inside scrollers.** An element with `position: absolute` and
  no offsets, inside a horizontal scroller with no positioned ancestor, escapes the scroller
  and widens the page. Give the scroller `position: relative`.
- **Text-to-speech wrapping.** Code that wraps rendered content must move the existing nodes,
  not copy `textContent`. Copying flattened every tutor reply into plain text.
- **Unscoped mobile rules.** A rule like `.text-button { position: absolute }` inside a mobile
  media query moved every text button on every page. Scope mobile rules to their component.
