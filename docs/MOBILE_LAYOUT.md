# Mobile layout

Most students use Learnova on a phone, so the phone layout is the primary target. The
desktop composition is unchanged.

## Where the rules live

| File | What it holds |
|---|---|
| `static/css/navigation.css` | The app bar and both menus (logged in and guest). Phone rules apply at ≤ 820 px. |
| `static/css/learnova-responsive.css` | Loaded last on every page. Containment (long words, tables, formulas), touch targets, the 16 px input floor, lesson reading sizes, the chat bottom sheet, focus rings and reduced motion. It holds sizing only; colours stay in the token files, so dark mode needs nothing extra. |
| `static/css/learnova-tokens.css` | `--ln-gutter`, `--ln-tap`, `--ln-text-body`, `--ln-text-small`, `--ln-leading-read`, `--ln-heading-page`. |
| `static/css/assistant.css` | The chat home page (`/`, `/assistant`, `/assistant/c/<id>`): the app shell, the conversation drawer at ≤ 900 px, the composer. Tokens only, so dark mode follows. See [Chat](#chat). |

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

## Chat

The chat is a signed-in student's home page, so it is the screen phones see most. The
behaviour is in `static/js/assistant.js`, the layout in `static/css/assistant.css`, and the
routes and API in [ASSISTANT.md](ASSISTANT.md). On phones the global navigation menu and
the conversation drawer are two separate menus with different labels and icons.

**Shell.**
- The page never scrolls (`body.assistant-body { overflow: hidden }`). Only the thread
  scrolls inside, with `overscroll-behavior: contain`.
- The composer sits at the bottom with `env(safe-area-inset-bottom)` padding.
- The shell height is `calc(var(--asst-vvh) - var(--app-nav-height))`.
- `--asst-vvh` starts as `100dvh` (`100vh` where `dvh` is unknown).

**Keyboard.**
- iOS shrinks only the *visual* viewport when the keyboard opens. `assistant.js` listens
  to `window.visualViewport` `resize`/`scroll` and sets `--asst-vvh` to its height, so the
  composer stays above the keyboard.
- A thread that was at the bottom stays there.
- During a pinch zoom the layout is left alone.
- Chrome on Android resizes the page itself because `templates/assistant.html` sets the
  viewport to `width=device-width, initial-scale=1, interactive-widget=resizes-content`
  (`{% block viewport %}` in `base.html`). iOS ignores that setting.

**Drawer, up to 900 px.**
- The conversation list is an off-canvas drawer, `min(20rem, 86vw)` wide, below the app
  bar. It slides in from the start edge (mirrored for right-to-left).
- Closed, it is hidden as well as moved away, so it cannot be tabbed into.
- While it is open:
  - `#asstMain` is `inert` and Tab stays inside the drawer;
  - Escape, the backdrop or opening a conversation closes it;
  - focus returns to the toggle, and `aria-expanded` follows.
- Above 900 px the sidebar is a column that can be collapsed, and the choice is remembered
  in `localStorage` (`learnova:asst-sidebar`).

**Composer and messages.**
- The message box text is 16 px, so iOS does not zoom.
- Every composer control is at least 44 × 44 px, on one row.
- With a touch pointer, Enter adds a line and the send button sends.
- At ≤ 480 px the separate camera button is hidden, because the phone's photo picker
  offers the camera.
- At ≤ 640 px the welcome suggestions are one row that scrolls sideways, and the
  disclaimer shows only under the empty state.
- Wide display formulas and tables scroll inside their message (`display: block;
  overflow-x: auto`), with a fade on the edge that has more behind it. They never widen
  the page. An inline formula wider than the column scrolls inside itself.
- `.assistant-page [hidden] { display: none !important; }`: a component's own `display`
  (`.ghost-button { display: inline-flex }`) once kept hidden buttons visible.

### Real-phone checklist

The iOS keyboard behaviour above was only simulated in the browser tests (a scripted
`visualViewport` height). Check these on a real iPhone (Safari) and a real Android phone
(Chrome), in light and dark mode:

1. Open `/`, tap the message box: the keyboard opens and the composer stays fully visible
   directly above it, with no gap and no page scroll behind it.
2. Type a long message (several lines): the box grows, and the newest message stays
   visible above it.
3. Send a message with the keyboard open: the reply appears above the composer, and the
   thread stays at the bottom.
4. Close the keyboard: the composer returns to the bottom edge above the home indicator,
   with no white band.
5. Rotate to landscape and back with the keyboard open: nothing is cut off.
6. Open the conversation drawer (the conversations button in the chat header): it fits
   the screen, the conversation behind it does not scroll, and the backdrop closes it.
7. Long-press in the box and paste a photo (where the phone allows it), or attach two
   photos: the tray shows them above the box.
8. Send a reply containing a long formula and a wide table: both scroll sideways inside
   the message, and the page does not.
9. Pinch-zoom the thread and zoom out again: the layout comes back as it was.

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

It also creates a chat conversation with a reply (`ASSISTANT_THREAD`, its
`/assistant/c/<id>` address), and attaches a photo in both chats (the home page `/` and
`/assistant`, and the lesson tutor chat). On the chat pages up to 900 px it opens the
conversation drawer and fails if the drawer is missing, does not fit the screen, or does
not close on Escape. On the pages in `MENU_STEPS` (the flashcard editor and `/assistant`) it
also opens the first three-dot menu (see "Three-dot menus" below). It covers every page the app renders, including the tutorial hub
(`/tutorial`, docs/TUTORIAL.md) and the admin and internal ones, for which the audit account is made an admin on the
throwaway server. It renders each page in the installed Chrome or Edge and fails on any of
these:

- the page scrolls sideways;
- a control is clipped;
- header controls overlap;
- the menu is missing or broken;
- a three-dot menu's items overlap, or one is under 44 px, outside the screen or covered;
- a JavaScript error: an uncaught exception or a console error (load failures of what the
  audit blocks on purpose are excluded);
- an HTTP error, or a redirect somewhere other than the page asked for (the intentional
  redirects are listed in `EXPECTED_REDIRECTS`);
- in dark mode, light text on a light surface (the background of the nearest ancestor
  that paints one).

A page whose data could not be created is named as "not audited", never measured as a
404. The audit also reports, without failing, tap targets under 44 px and text under
14 px.

Web fonts and ads are blocked during audits, so pages render with the fallback font stack.
That stack is also what a student on a slow connection sees first.

## Three-dot menus

There are two: the flashcard editor's row menu (a `<details class="card-menu">` built in
`static/js/flashcards/create.js`: the card's Type — Word / Sentence / Other, three
`.card-kind-option` buttons in one row, each at least `--ln-tap` tall, with the menu
widened just enough that "Anderes" or "Palabra" never breaks — then Move up, Move down,
Regenerate for AI sets, Duplicate, Delete) and the chat's conversation menu (`#asstItemMenu` in `templates/assistant.html`:
Rename, Archive, Delete). The desktop nav dropdowns and the vocabulary review "More"
disclosure are not popup item lists and were checked separately.

- **One item style.** Both use `.ln-menu-item` (learnova-components.css): a full-width row,
  at least `--ln-tap` (44 px) tall on every width, ink text, the icon in a fixed 1.25em
  slot (`flex: none`) so it never squeezes or covers the label, and long labels wrapping
  between words. Delete is `.ln-menu-item.danger`, coloured with `--ln-danger-text`
  (learnova-tokens.css: the brand red darkened by a fifth in light, `--ln-danger` in dark),
  so no learnova-dark.css override is needed. Page stylesheets load before
  learnova-components.css, so a menu must not restyle its items with a `.menu button` rule:
  that is 0,1,1 and beats the shared 0,1,0.
- **Placement stays per menu.** The chat menu is `position: fixed` and placed by
  `assistant.js` (clamped to the screen, flips above the row). The card menu is absolute
  under its ⋯ button with `z-index: 60` (above the save bar's 50, below the app bar's 100);
  `placeCardMenu` in create.js (a capturing `toggle` listener) adds `.opens-up` when the menu
  does not fit between the row and the save bar or screen bottom but fits above, and
  otherwise scrolls the page just enough to show it.
- **Tests.** `MENU_JS` / `open_row_menu` in scripts/mobile_audit.py measure every item
  (boxes, label text rectangles, icons, `elementFromPoint` at each centre, colours).
  `tests/test_mobile_layout.py` opens both menus at 320, 375, 390, 430 and 1280 px in light
  and dark, and opens a card menu on a row placed just above the save bar with unsaved
  changes; `tests/test_creator_ux.py` forbids `.card-row-tools button`-style descendant rules
  in any stylesheet. Only German labels (the audit account's language) are rendered; the
  English ones are shorter. `MENU_JS` measures the `.ln-menu-item` rows only; the Type
  buttons are measured by `tests/test_flashcard_kind_browser.py` (44 px, inside the
  screen, not covered, side by side without overlap) at 390 px light and 320 px dark.

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
- **Descendant selectors that reach into a menu.** `.card-row-tools button { width: 44px;
  height: 44px }` (a phone rule for the row's icon buttons) also matched the menu items
  inside `.card-row-tools`: every item became a 44x44 box, the labels ran letter by letter
  into each other, and the same leak greyed out Delete. Use `>` for a component's own
  buttons, and the menu its own item class.
- **Measuring during the entrance animation.** While `.app-page`'s 420 ms entrance runs,
  `<main>` has a transform, so the fixed save bar sits at the bottom of the page, not the
  screen. Browser checks wait for `settle_page()` (scripts/mobile_audit.py) first.
- **Unscoped mobile rules.** A rule like `.text-button { position: absolute }` inside a mobile
  media query moved every text button on every page. Scope mobile rules to their component.
