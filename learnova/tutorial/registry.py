"""The tutorial registry: the quick tour and every feature chapter (pure data).

This is the single source of truth. The server filters it by what the student can use
(visibility.py), translates every text and sends it to static/js/tour.js as JSON; the
browser engine is generic and never contains step texts or feature gating.

Two layers (docs/TUTORIAL.md):
  * the QUICK TOUR (`overview`): one step per function the student can use, lit at its
    real entry point in the navigation (desktop bar, More and Account menus, phone menu);
    offered once to a new account, and on the hub;
  * FEATURE CHAPTERS: a few tips (1-6 steps) that belong to ONE page. A chapter starts by
    itself the first time its `anchor` has been visible for a moment on that page (no
    dialog, no running generation, not while the student types, one per page load), and
    never again once the student skipped or finished it. The hub replays any chapter.

Adding a step (docs/TUTORIAL.md has the full checklist):
  1. add a `Step` to its chapter below (English msgids, 1-3 short sentences);
  2. give the real element a stable hook: `data-tour-target="<name>"` at the END of its
     start tag (or a unique #id) and list it first in `targets`;
  3. translate every new msgid (de "du", fr "tu", es "tú") through the merge script;
  4. add the step's oracle to tests/test_tutorial_browser.py and run tests/test_tutorial.py.

Rules the tests enforce (tests/test_tutorial.py): ids are unique kebab-case and at most 60
characters; section ids never reuse a retired one (OLD_SECTION_IDS); kinds, advances,
viewports and pages are valid; every chapter step lives on its chapter's page; the quick
tour ends with a card; the step total stays between 70 and 100; no target or url touches
an admin, moderation or internal page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Part of the payload signature: bump it whenever steps change, so a browser tab that
# cached the old payload fetches the new one. 5: the quick tour + feature chapters;
# 6: the review fixes (follow-up chapters, the Tutorial link, clearer texts).
REGISTRY_VERSION = "6"

KINDS = ("card", "read", "action")
ADVANCES = ("next", "click", "change", "input")
VIEWPORTS = ("any", "phone", "desktop")
SECTION_KINDS = ("overview", "chapter")
PHONE_MAX = 820   # px; the navigation's own breakpoint (static/js/navigation.js)
STEP_TOTAL = (70, 100)   # the whole tutorial stays short (tests/test_tutorial.py)

# Context tokens (a chapter's "@token" url): the student's own newest record of a kind,
# looked up for the hub (app.tutorial_context_urls). A chapter whose token is absent says
# when it starts instead of offering a replay.
CONTEXT_TOKENS = ("chat_thread", "chat_history", "lesson", "project", "project_draft", "project_review", "exam_result",
                  "study_session", "flashcard_set", "flashcard_study", "community_set")

# Section ids of the tutorial before 2026-11-04. Their progress rows are kept (never delete
# student data) and simply ignored; no new section may take one of these ids.
OLD_SECTION_IDS = ("intro", "chat", "lessons", "projects", "practice", "profile", "community", "account", "more")

# The tutorial hub (/tutorial). Links and create actions that point here are dropped from
# the payload while the hub route does not exist (visibility.payload(hub="")).
HUB_URL = "/tutorial"
OVERVIEW_ID = "overview"
# The student's "Show tips automatically" switch lives in tutorial_progress as a reserved
# row (no migration): a row with this section id means automatic tips are OFF.
AUTO_TIPS_SECTION = "_auto_tips"


@dataclass(frozen=True)
class Step:
    """One step. Texts are English msgids, translated at serialisation.

    kind     "card" (no target, centred) | "read" (lit target, Next) | "action" (lit target;
             doing the real thing also moves on).
    targets  CSS selectors in priority order: the first selector with a VISIBLE match wins,
             and within it the first visible node. Prefer [data-tour-target=…], then #id.
             A target that is not there (a Premium-only control, a list without rows) is
             skipped silently inside a running chapter.
    also     extra selectors lit together with the target (union rectangle).
    advance  action steps: "click" | "change" | "input" (3+ characters); everything else "next".
    viewport "any" | "phone" (<= PHONE_MAX px) | "desktop".
    requires capabilities (visibility.capabilities); ALL must be present or the step does
             not exist for this student. "!name" means the capability must be ABSENT.
    wait_ms  how long a target that is not there yet is awaited before it is skipped.
    busy     selector; while visible the "still working" card is shown instead.
    create   (label msgid, "/url"): a button on a card (the quick tour's last card).
    shortcut (selector, step id): a click on selector jumps to that step.
    """

    id: str
    page: str
    title: str
    text: str
    kind: str = "read"
    targets: tuple[str, ...] = ()
    also: tuple[str, ...] = ()
    advance: str = "next"
    viewport: str = "any"
    requires: tuple[str, ...] = ()
    wait_ms: int = 1200
    busy: str = ""
    create: tuple[str, ...] = ()
    shortcut: tuple[str, ...] = ()
    icon: str = ""


@dataclass(frozen=True)
class Section:
    """The quick tour (kind "overview") or one feature chapter (kind "chapter").

    page     the chapter's page key; all its steps live there ("*" for the quick tour).
    anchor   CSS selector: the chapter starts by itself once this has been visible (and on
             screen) for a moment. Normally its first step's target.
    group    GROUPS key: where the hub lists it.
    url      where Replay goes: "/path[#anchor]" or "@token" (the student's own record).
    where    with an "@token" url: the page where that record is made ("Take me there"
             when the student has none yet).
    when     with an "@token" url: English msgid "Starts when you ..." for the hub.
    """

    id: str
    title: str
    icon: str
    steps: tuple[Step, ...]
    kind: str = "chapter"
    page: str = "*"
    anchor: str = ""
    group: str = ""
    url: str = ""
    where: str = ""
    when: str = ""
    summary: str = ""
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    id: str
    title: str          # English msgid
    icon: str
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Page:
    label: str            # English msgid
    url: str              # "/path" or "@token"
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Container:
    """A place where a target can hide: a menu, a drawer, a panel, a closed <details>.

    When a step's target is hidden inside `inside`, the engine lights `opener` with `text`
    first; the student taps it (or presses Next and the engine taps it). Leaving a step
    whose target was inside, for one outside, closes it again: `closer` when it is visible,
    else the opener when `toggle` (it opens and closes) and it says aria-expanded="true".
    """

    inside: str
    opener: str
    text: str
    closer: str = ""
    toggle: bool = False


def card(id: str, title: str, text: str, **extra) -> Step:
    return Step(id=id, page=extra.pop("page", "*"), title=title, text=text, kind="card", **extra)


# ---------------------------------------------------------------- pages and containers

PAGES: dict[str, Page] = {
    "chat": Page("Chat", "/", requires=("assistant_chat",)),
    "index": Page("New Lesson", "/lesson"),
    "dashboard": Page("Overview", "/dashboard"),
    "projects": Page("Project Mode", "/projects"),
    "project": Page("Your project", "@project"),
    # The same workspace before its topics are confirmed (stage B): app.project_dashboard
    # passes tour_page="project-draft".
    "project-draft": Page("Topic check", "@project_draft"),
    "exam-results": Page("Exam results", "@exam_result"),
    "study-session": Page("Study Session", "@study_session"),
    "project-review": Page("Check pages", "@project_review"),
    "flashcards": Page("Flashcards", "/flashcards", requires=("private_flashcards",)),
    "flashcard-set": Page("Flashcard set", "@flashcard_set", requires=("private_flashcards",)),
    "flashcard-mode": Page("Flashcards", "@flashcard_study", requires=("private_flashcards",)),
    "practice-today": Page("Today's Practice", "/practice/today"),
    "mistakes": Page("Mistake Intelligence", "/insights/mistakes"),
    "progress": Page("Progress", "/progress", requires=("gamification_enabled",)),
    "profile": Page("My profile", "/profile"),
    "leaderboard": Page("Leagues", "/leaderboard", requires=("leagues",)),
    "community": Page("Community", "/community", requires=("community_library",)),
    "settings": Page("Settings", "/settings"),
    "about": Page("About Learnova", "/about"),
    "contact": Page("Contact us", "/contact"),
    "tutorial": Page("Tutorial", HUB_URL),
}

# Page key per Flask endpoint (views that pass `tour_page` keep theirs: "chat" on /,
# /assistant and /assistant/c/<id>; "index" on /lesson, /?goal= and /?session_id=).
PAGE_BY_ENDPOINT: dict[str, str] = {
    "dashboard": "dashboard", "projects": "projects", "project_dashboard": "project",
    "project_start": "project-start", "review_project_recognition": "project-review",
    "learn_section": "project-section", "new_final_exam": "exam-setup", "final_exam_results": "exam-results",
    "study_plan_calendar": "calendar", "study_plan_session_detail": "study-session",
    "flashcards_page": "flashcards", "flashcards_overview_page": "flashcard-set",
    "flashcards_create_page": "flashcard-edit", "flashcards_edit_page": "flashcard-edit",
    "flashcards_study_page": "flashcard-mode", "flashcards_learn_page": "flashcard-mode",
    "flashcards_test_page": "flashcard-mode", "flashcards_match_page": "flashcard-mode",
    "flashcards_blast_page": "flashcard-mode", "flashcards_blocks_page": "flashcard-mode",
    "flashcards_test_results_page": "flashcard-mode", "flashcards_publish_page": "flashcard-publish",
    "community_page": "community", "community_set_detail_page": "community",
    "progress_page": "progress", "profile_page": "profile", "leaderboard_page": "leaderboard",
    "public_profile": "public-profile", "settings": "settings", "todays_practice": "practice-today",
    "mistake_intelligence": "mistakes", "concept_detail": "concept", "about": "about",
    "contact": "contact", "tutorial_hub": "tutorial", "grade_onboarding": "onboarding",
}

# Ordered: the first container that holds the hidden target wins. Openers may themselves
# be hidden in another container (the ⋯ of a chat inside the phone drawer); the engine
# follows that chain.
CONTAINERS: tuple[Container, ...] = (
    Container("#mobileAppMenu", ".mobile-nav-toggle", "Tap the menu button first.",
              closer="#mobileAppMenu [data-mobile-nav-close]"),
    Container("#moreMenu", '[data-tour-target="more"]', "Open More first.", toggle=True),
    Container("#profileMenu", '[data-tour-target="account"]', "Open the Account menu first.", toggle=True),
    Container("#asstItemMenu", '.asst-row.is-active [data-tour-target="chat-row-menu"]',
              "Tap ⋯ next to the chat first.", toggle=True),
    Container("#asstArchivedList", "#asstShowArchived", "Open Archived first.", toggle=True),
    Container("#asstSidebar", "#asstDrawerToggle", "Open your chats first.", closer="#asstScrim"),
    Container("#chatPanel", "#chatToggle", "Open the tutor chat first.", closer="#chatClose"),
    Container("details:not([open])", ":scope > summary", "Open this part first."),
)

# ------------------------------------------------------------------- interface texts

UI: dict[str, str] = {
    "previous": "Previous",          # NOT "Back": that msgid is a flashcard's back side
    "next": "Next",
    "finish": "Finish",
    "exit": "Exit quick tour",       # the quick tour (the hub is "Tutorial": another word)
    "skip": "Skip",                  # a feature chapter: skipped, never shown by itself again
    "stepOf": "Step {current} of {total}",
    "busy": "Learnova is still writing it. You can wait or go on.",
    "lit": "Highlighted: {name}",
}

OFFER: dict[str, str] = {
    "title": "Welcome to Learnova",
    "text": ("Your study tutor for every subject. Ask questions, send photos of your homework, build short "
             "lessons and plan for your exams, all explained at your class level."),
    "question": "How would you like to start?",
    "quick": "Show me around (1 minute)",
    "explore": "Explore on my own",
}

# The error every rejected progress update answers with (the code says which kind).
ERROR_UNKNOWN = "This tutorial step does not exist."

# The hub's "What Learnova can do" (templates/tutorial.html): (key, icon, English msgid,
# requires). Per key the first line whose requires the student has is shown; a key with
# no such line is left out, so the hub never promises a switched-off feature.
CAN_DO: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("chat", "💬", "Ask your AI tutor anything, also about a photo of your notes.", ("assistant_chat", "chat_images")),
    ("chat", "💬", "Ask your AI tutor anything, explained at your class level.", ("assistant_chat",)),
    ("lessons", "📖", "Build a short lesson with a test on any topic.", ()),
    ("projects", "🗂️", "Turn your notes into a study plan for your exam.", ()),
    ("cards", "🃏", "Study with flashcards and spaced repetition.", ("private_flashcards",)),
    ("community", "🌍", "Find and share flashcard sets.", ("community_library", "community_publishing")),
    ("community", "🌍", "Find flashcard sets from other students.", ("community_library",)),
    ("rewards", "🏆", "Earn XP, unlock avatars and climb the leagues.", ("gamification_enabled", "leagues")),
    ("rewards", "🏆", "Earn XP, unlock avatars and finish challenges.", ("gamification_enabled",)),
)

# The hub's feature groups, in display order (the old section titles, already translated).
GROUPS: tuple[Group, ...] = (
    Group("chat", "Chat Assistant", "💬", requires=("assistant_chat",)),
    Group("lessons", "Short Lessons", "📖"),
    Group("projects", "Project Mode", "🗂️"),
    Group("practice", "Learning and Practice", "🃏"),
    Group("profile", "Profile and Rewards", "🏆"),
    Group("community", "Community", "🌍", requires=("community_library",)),
    Group("account", "Premium and Account", "⚙️"),
)

_CARDS = ("private_flashcards",)
_GAMES = ("gamification_enabled",)
_LEAGUES = ("leagues",)

# ------------------------------------------------------------------------- the quick tour

# One step per function, at its real entry point. The desktop bar shows Chat, Overview,
# New Lesson and Project Mode; Flashcards, Today's Practice and Community hide in More;
# My profile, Leagues and Settings in the Account menu; on a phone everything is in the
# menu (the same hook sits on both copies; the engine lights the visible one and opens
# the menu or dropdown first). The XP chip is in the header on every layout.
OVERVIEW = Section(
    id=OVERVIEW_ID, kind="overview", title="Quick tour", icon="👋",
    summary="A one-minute look at everything you can use.",
    steps=(
        Step("overview-chat", "*", "Chat",
             "Ask your AI tutor any question about school. It explains everything at your class level.",
             targets=('[data-tour-target="chat"]',), requires=("assistant_chat",), icon="💬"),
        Step("overview-dashboard", "*", "Overview",
             "Your Overview shows what is due today, your weak topics and your projects in one place.",
             targets=('[data-tour-target="overview"]',), icon="🏠"),
        Step("overview-lesson", "*", "New Lesson",
             "Build a short lesson with an explanation and a test on any topic.",
             targets=('[data-tour-target="new-lesson"]',), icon="✨"),
        Step("overview-projects", "*", "Project Mode", "Turn your notes into a study plan for your exam.",
             targets=('[data-tour-target="projects"]',), icon="🗂️"),
        Step("overview-flashcards", "*", "Flashcards", "Study with flashcards and spaced repetition.",
             targets=('[data-tour-target="flashcards"]',), requires=_CARDS, icon="🃏"),
        Step("overview-practice", "*", "Today's Practice",
             "Today's Practice puts together what is due today. Mistake Intelligence shows what your mistakes "
             "have in common.",
             targets=('[data-tour-target="practice-today"]',), also=('[data-tour-target="mistake-insights"]',),
             icon="📅"),
        Step("overview-community", "*", "Community", "Find flashcard sets from other students.",
             targets=('[data-tour-target="community"]',), requires=("community_library",), icon="🌍"),
        Step("overview-profile", "*", "My profile", "Your profile shows your avatar, your username and your rewards.",
             targets=('[data-tour-target="profile"]',), icon="👤"),
        Step("overview-leagues", "*", "Leagues",
             "Earn XP each week and climb the leagues with other learners. Leagues are optional.",
             targets=('[data-tour-target="leagues"]',), requires=_LEAGUES, icon="🏆"),
        Step("overview-settings", "*", "Settings",
             "Change your language and your class here, and see your plan. Premium is optional.",
             targets=('[data-tour-target="settings"]',), icon="⚙️"),
        Step("overview-xp", "*", "XP and levels",
             "Learning earns XP. Your level is shown here, and your daily 🔥 streak once you have one.",
             targets=('[data-tour-target="xp"]',), requires=_GAMES, icon="⭐"),
        # The last step lights where the Tutorial lives (More / Account menu, phone menu).
        Step("overview-done", "*", "You are ready!",
             "When you open a feature for the first time, short tips show you how it works. You can replay all "
             "tips in the Tutorial.",
             targets=('[data-tour-target="tutorial"]',), icon="🎉"),
    ),
)

# ------------------------------------------------------------------------- chapters: chat

CHAT_BASICS = Section(
    id="chat-basics", title="Chat basics", icon="💬", group="chat", page="chat", anchor="#asstInput",
    url="/", requires=("assistant_chat",),
    steps=(
        Step("chat-input", "chat", "Ask your question",
             "Type your question here. Ask about your homework, a topic or an exam, and add follow-up questions "
             "whenever something is unclear.",
             targets=("#asstInput",), icon="✍️"),
        Step("chat-photo", "chat", "Photos of your notes",
             "Upload a photo of your school notes or homework, even handwritten. You can also paste or drag a "
             "photo into the chat. Learnova reads it and helps you understand it.",
             targets=('[data-tour-target="chat-photo"]',), requires=("chat_images",), icon="📷"),
        Step("chat-send", "chat", "Send and stop",
             "Tap the arrow to send. While the answer is being written, this button turns into Stop, in case you "
             "asked the wrong thing.",
             targets=("#asstSend", "#asstStop"), icon="➤"),
        Step("chat-style", "chat", "Choose a style",
             "Pick how your tutor works with you, for example Study coach or Plain explanation. Switch on Think "
             "harder for a really tricky question.",
             targets=("#asstPreset",), also=('[data-tour-target="chat-deep"]',), icon="🎨"),
    ),
)

CHAT_ANSWERS = Section(
    id="chat-answers", title="Tutor answers", icon="🤖", group="chat", page="chat",
    anchor='[data-tour-target="chat-reply"]', url="@chat_thread", where="/",
    when="Starts after your first question in the chat.", requires=("assistant_chat",),
    steps=(
        Step("chat-reply", "chat", "Your tutor's answer",
             "Answers show maths formulas and chemistry like H₂O neatly. Still unclear? Just ask a follow-up "
             "question.",
             targets=('[data-tour-target="chat-reply"]',), wait_ms=3000, icon="🤖"),
        Step("chat-tools", "chat", "Answer tools",
             "Tap Copy to keep an answer, for example in your notes. Not happy with the newest answer? Tap "
             "Regenerate, or Retry when an answer went wrong.",
             targets=('[data-tour-target="chat-reply"] .asst-actions', '[data-tour-target="chat-retry"]'), icon="📋"),
        Step("chat-chips", "chat", "Tap to answer",
             "When your tutor asks you a question, you can tap one of these answers instead of typing.",
             targets=('[data-tour-target="chat-chips"]',), icon="👆"),
        Step("chat-continue", "chat", "Continue learning",
             "Turn your conversation into a structured lesson that you can save and revisit.",
             targets=('[data-tour-target="chat-continue"]',), requires=("continue_learning",), icon="📖"),
    ),
)

# Two or more chats in the list (on a phone: once the student opens the list).
CHAT_HISTORY = Section(
    id="chat-history", title="Your chats", icon="🗂️", group="chat", page="chat",
    anchor="#asstList .asst-row ~ .asst-row", url="@chat_history", where="/",
    when="Starts when your chat list shows two chats.", requires=("assistant_chat",),
    steps=(
        Step("chat-history-list", "chat", "Your chats",
             "Every chat is saved here, newest first. Tap one to carry on, or search for an old one.",
             targets=('[data-tour-target="chat-history"]',), also=("#asstSearch",), wait_ms=2500, icon="🗂️"),
        Step("chat-new", "chat", "New chat", "Start a new chat when you change topic. Your old chats are saved.",
             targets=("#asstNew", "#asstHeadNew"), icon="🖊️"),
        # The last tip: opening the menu is the student's own tap and ends the chapter, so the
        # menu is never under the dimmed page. Next never opens it.
        Step("chat-menu", "chat", "The ⋯ menu",
             "Tap ⋯ next to a chat to rename, archive or delete it. Archived chats keep every message, and ⋯ "
             "under Archived has Restore.",
             kind="action", advance="click",
             targets=('.asst-row.is-active [data-tour-target="chat-row-menu"]',
                      '#asstList [data-tour-target="chat-row-menu"]'), icon="⋯"),
    ),
)

# ---------------------------------------------------------------------- chapters: lessons

# The texts marked (r) are the first lesson tour's own sentences, already translated.
_IDEAS = ('[data-tour-target="lesson-ideas"] button', "lessons-photos")

LESSON_START = Section(
    id="lesson-start", title="Build a lesson", icon="✨", group="lessons", page="index",
    anchor='[data-tour-target="lesson-subject"]', url="/lesson",
    steps=(
        Step("lessons-subject", "index", "Pick a subject",
             "Tap a subject - whatever you want to learn today.",  # (r)
             kind="action", advance="change", targets=('[data-tour-target="lesson-subject"]', ".subject-picker"),
             shortcut=_IDEAS, icon="📚"),
        Step("lessons-goal", "index", "Say what to learn",
             "Say what to learn, or tap one of the ideas below. One line is enough.",  # (r)
             kind="action", advance="input", targets=('[data-tour-target="lesson-goal"]',),
             also=('[data-tour-target="lesson-ideas"]',), shortcut=_IDEAS, icon="💭"),
        Step("lessons-photos", "index", "Add your notes",
             "Add photos of your notes or a worksheet, and the lesson is built from your own material.",
             targets=("#attachmentButton",), icon="📷"),
        # Building spends the day's lesson allowance on the Free plan: the student's own tap
        # finishes the chapter, Next never presses it.
        Step("lessons-build", "index", "Build the lesson",
             "Tap Build my lesson. Learnova writes an explanation and a short test for you.",  # (r)
             kind="action", advance="click", targets=('[data-tour-target="lesson-build"]', ".begin-button"),
             icon="🚀"),
    ),
)

_LESSON: dict[str, Any] = {"url": "@lesson", "where": "/lesson"}

# Engine lessons hide the explanation card and show #lessonParts instead: the first
# visible match wins, so either one is lit.
LESSON_INSIDE = Section(
    id="lesson-inside", title="Inside a lesson", icon="📖", group="lessons", page="index",
    anchor='[data-tour-target="lesson-explanation"], #lessonParts', when="Starts when your first lesson is ready.",
    **_LESSON,
    steps=(
        Step("lessons-explanation", "index", "Your lesson",
             "Here is your lesson. Read the explanation, then scroll down.",  # (r)
             targets=('[data-tour-target="lesson-explanation"]', "#lessonParts"), busy="#loading:not(.hidden)",
             wait_ms=2000, icon="📖"),
        Step("lessons-level", "index", "Made for your level",
             "Learnova matches every lesson to your class and to what you already know. You see the level here.",
             targets=("#levelBadge",), icon="🎯"),
        Step("lessons-listen", "index", "Listen",
             "Tap Listen and the lesson is read aloud to you. You can make it faster or slower.",
             targets=("#explanationListenControl", "#partsListenControl"), icon="🔊"),
        # Opening the tutor chat ends the chapter, so the chat is never under the dimmed page.
        Step("lessons-tutor", "index", "Ask your tutor",
             "Stuck? Your tutor chat is right here, any time during a lesson.",  # (r)
             kind="action", advance="click", targets=("#chatToggle",), icon="💬"),
    ),
)

# The tutor chat panel is open.
LESSON_TUTOR = Section(
    id="lesson-tutor", title="Your tutor chat", icon="💬", group="lessons", page="index",
    anchor='#chatPanel [data-tour-target="lesson-adapt"]', when="Starts when you open the tutor chat in a lesson.",
    **_LESSON,
    steps=(
        Step("lessons-adapt", "index", "Simpler or harder",
             "Tap Simpler explanation or Make it harder, and your tutor explains it again in a new way. Give me a "
             "hint gives you a small push.",
             targets=('[data-tour-target="lesson-adapt"]',), also=('[data-tour-target="lesson-hints"]',), icon="🎚️"),
        Step("more-microphone", "index", "Speak instead of typing",
             "In a lesson, tap Use microphone and say your question or answer. Learnova writes it down for you.",
             targets=("#chatMicrophoneControl",), icon="🎤"),
    ),
)

# Start test (or, once the test runs, the first question card) is on screen. A chapter
# that starts by itself begins at its first step that is there.
LESSON_TEST = Section(
    id="lesson-test", title="The lesson test", icon="✅", group="lessons", page="index",
    anchor="#startTest, #questionCard:not(.hidden)", when="Starts when you get to the test of a lesson.", **_LESSON,
    steps=(
        Step("lessons-start-test", "index", "Start the test",
             "Tap Start test. It asks as many questions as you need - 3 to 15 - until you really know it.",  # (r)
             kind="action", advance="click", targets=("#startTest",), wait_ms=600, icon="✅"),
        Step("lessons-question", "index", "Practise", "Answer each question. If you are stuck, tap Show a hint.",
             targets=("#questionCard:not(.hidden)",), wait_ms=2500, icon="❓"),
        Step("lessons-check", "index", "Check your answer",
             "Tap Check my answer. You see straight away what was right, and a wrong answer gets a new "
             "explanation.",
             targets=('[data-tour-target="lesson-check"]',), icon="✔️"),
        Step("lessons-confidence", "index", "How sure are you?",
             "Tell Learnova how sure you are. It helps decide what to repeat with you later.",
             targets=('[data-tour-target="lesson-confidence"]',), icon="🤔"),
    ),
)

# --------------------------------------------------------------------- chapters: projects

PROJECTS_START = Section(
    id="projects-start", title="Start a project", icon="🆕", group="projects", page="projects",
    anchor="#projectCreateForm", url="/projects",
    steps=(
        Step("projects-create", "projects", "Create a project",
             "Choose the subject and your class, and add the exam date if you know it. Without a date you get a "
             "flexible plan.",
             targets=('[data-tour-target="project-create-fields"]', "#projectCreateForm"),
             also=('[data-tour-target="project-exam-date"]',), icon="🆕"),
        Step("projects-upload", "projects", "Upload your notes",
             "Add photos or PDFs of your notes, worksheets or handwriting, or scan pages with your camera. Up to "
             "{pages} pages fit in one project.",
             targets=('[data-tour-target="project-upload"]',), also=("#openScanner",), icon="📄"),
        Step("projects-topics-only", "projects", "Only topic names?",
             "Open this to type your topics, one per line. You can also add a Kompetenzraster, so the topics your "
             "teacher will test count as important.",
             targets=('[data-tour-target="project-topics-only"]',), icon="📝"),
        # Creating spends the Free plan's project allowance: a read step, Next always works.
        Step("projects-create-free", "projects", "Create it",
             "Tap Create learning project. The Free plan has a limit for new projects; Settings shows how many you "
             "have left.",
             targets=("#createProjectButton",), requires=("free",), icon="🚀"),
        Step("projects-create-premium", "projects", "Create it",
             "Tap Create learning project. With Premium you can start as many projects as you like.",
             targets=("#createProjectButton",), requires=("premium",), icon="🚀"),
    ),
)

_PROJECT: dict[str, Any] = {"url": "@project", "where": "/projects"}

PROJECT_DRAFT = Section(
    id="project-draft", title="Topic check", icon="✅", group="projects", page="project-draft",
    anchor='[data-tour-target="project-topic-review"]', url="@project_draft", where="/projects",
    when="Starts when Learnova has found the topics in a new project.",
    steps=(
        Step("projects-topic-review", "project-draft", "Check the topics",
             "Learnova lists the topics it found in your notes. Untick any topic you do not need.",
             targets=('[data-tour-target="project-topic-review"]',), also=("#confirmTopics .pm-topics > li:first-child",),
             icon="✅"),
        Step("projects-edit-topics", "project-draft", "Rename or merge",
             "Open Rename or merge topics to fix a topic name or join two topics that belong together. A topic is "
             "missing? Use Add a missing topic just below.",
             targets=('[data-tour-target="project-edit-topics"]',), also=('[data-tour-target="project-add-topic"]',),
             icon="✏️"),
        Step("projects-start-learning", "project-draft", "Start learning",
             "When the list looks right, tap Start learning. Learnova makes your plan and your first lesson starts "
             "straight away.",
             targets=('[data-tour-target="project-start-learning"]',), icon="▶️"),
    ),
)

# Stage C, in the order the workspace shows its parts (top to bottom).
PROJECT_WORKSPACE = Section(
    id="project-workspace", title="Your project", icon="🧭", group="projects", page="project",
    anchor='[data-tour-target="project-next"], #overview', when="Starts when you open a project whose topics are "
    "confirmed.", **_PROJECT,
    steps=(
        Step("projects-next", "project", "Your next step",
             "Not sure what to do? Tap the purple Next → button in your project, and Learnova picks the most "
             "useful step for today.",
             targets=('[data-tour-target="project-next"]', "#overview"), icon="➡️"),
        Step("projects-today", "project", "Today's plan",
             "Here are today's tasks. They fit the study time you chose.",
             targets=("#today",), icon="📅"),
        Step("projects-exercises", "project", "Practise a topic",
             "Practise one topic, take its topic test, or do mixed practice with the learned topics you find "
             "hardest.",
             targets=('[data-tour-target="project-exercises"]', "#exercises"),
             also=("#exercises .pm-tasks > li:first-child",), icon="✍️"),
        Step("projects-mock-exam", "project", "Practice exam",
             "Write a timed practice exam over your topics. Answers and corrections appear after you hand it in. "
             "\"Customise the practice exam\" lets you choose how long it is.",
             targets=('[data-tour-target="project-mock-exam"]', "#tests"), icon="📝"),
    ),
)

# Starts when the student scrolls down to the progress part.
PROJECT_PROGRESS = Section(
    id="project-progress", title="Progress and plan", icon="📈", group="projects", page="project",
    anchor="#progress", when="Starts when you scroll to the progress of a confirmed project.", **_PROJECT,
    steps=(
        Step("projects-progress", "project", "Progress",
             "See how many topics you have learned and understood. After a few questions, your weak spots show up "
             "here too.",
             targets=("#progress",), icon="📈"),
        Step("projects-revision", "project", "Repeat to remember",
             "Topics you have learned come back after 1, 3, 7, 14 and 30 days, so you do not forget them. The "
             "calendar shows every study day.",
             targets=("#revision",), icon="🔁"),
        Step("projects-plan", "project", "Change your plan",
             "Open Exam date and study time to change the exam date, your daily study time or your days off. The "
             "study time counts for all your projects. Your plan changes to fit.",
             targets=('[data-tour-target="project-plan"]',), icon="⏱️"),
        Step("projects-materials", "project", "Your pages",
             "Your uploaded pages stay here. Add more pages or fix a page that was hard to read.",
             targets=("#materials",), icon="🗃️"),
    ),
)

PROJECT_REVIEW = Section(
    id="project-review", title="Fix reading mistakes", icon="🔧", group="projects", page="project-review",
    anchor='[data-tour-target="review-first-page"]', url="@project_review", where="/projects",
    when="Starts when you check the pages of a project.",
    steps=(
        Step("projects-review", "project-review", "Fix reading mistakes",
             "Check how each page was read. Correct a wrong word, turn a page the right way or take a new photo.",
             targets=('[data-tour-target="review-first-page"]', "#reviewPageList"), icon="🔧"),
    ),
)

EXAM_RESULTS = Section(
    id="exam-results", title="Close the gaps", icon="🧩", group="projects", page="exam-results",
    anchor='[data-tour-target="exam-close-gaps"]', url="@exam_result", where="/projects",
    when="Starts when you hand in a practice exam.",
    steps=(
        Step("projects-close-gaps", "exam-results", "Close the gaps",
             "After a practice exam you see what you know. Tap Close the gaps to practise the topics you did not "
             "know yet.",
             targets=('[data-tour-target="exam-close-gaps"]',), icon="🧩"),
    ),
)

STUDY_SESSION = Section(
    id="study-session", title="Done or missed", icon="🗓️", group="projects", page="study-session",
    anchor='[data-tour-target="session-complete"]', url="@study_session", where="/projects",
    when="Starts when you open a planned day in your study calendar.",
    steps=(
        Step("projects-day-done", "study-session", "Done or missed",
             "Done with a study day? Tap Mark session complete. If you missed it, tap I missed this day and the work "
             "moves to later.",
             targets=('[data-tour-target="session-complete"]',), icon="✅"),
    ),
)

# --------------------------------------------------------------------- chapters: practice

FLASHCARDS_LIBRARY = Section(
    id="flashcards-library", title="Flashcards", icon="🃏", group="practice", page="flashcards",
    anchor='[data-tour-target="flashcards-library"]', url="/flashcards", requires=_CARDS,
    steps=(
        Step("practice-library", "flashcards", "Your sets",
             "All your flashcard sets are here, also the ones from your projects. Search or filter to find one.",
             targets=('[data-tour-target="flashcards-library"]',), icon="🗂️"),
        Step("practice-create", "flashcards", "Make a set",
             "Write your own cards, or let AI make them from your text or a topic.",
             targets=('[data-tour-target="flashcards-create"]',), icon="➕"),
        Step("practice-import", "flashcards", "Import material",
             "Upload a photo or a PDF. Check the text Learnova found, then let AI turn it into flashcards.",
             targets=('[data-tour-target="flashcards-import"]',), requires=("flashcard_import",), icon="📥"),
        Step("practice-vocabulary", "flashcards", "Vocabulary Trainer",
             "Scan a word list from your textbook and practise the words.",
             targets=('[data-tour-target="vocabulary-import"]',), requires=("vocabulary_trainer",), icon="🔤"),
    ),
)

# The study modes are one step on the mode list: which cards it shows follows the flags.
FLASHCARD_SET = Section(
    id="flashcard-set", title="Flashcard set", icon="▶️", group="practice", page="flashcard-set",
    anchor="#ovStudy", url="@flashcard_set", where="/flashcards",
    when="Starts when you open one of your flashcard sets.", requires=_CARDS,
    steps=(
        Step("practice-set", "flashcard-set", "Start learning",
             "Open a set and tap Start learning to go through its cards.",
             targets=("#ovStudy",), icon="▶️"),
        Step("practice-edit", "flashcard-set", "Edit a set", "Tap Edit to fix a card or add new ones.",
             targets=("#ovEdit",), icon="✏️"),
        # Only when there is more than one mode to choose from (the flags of the modes).
        Step("practice-modes", "flashcard-set", "Study modes", "Choose how you want to study this set.",
             targets=('[data-tour-target="flashcard-modes"]',), requires=("flashcard_modes",), icon="🎛️"),
        # Publishing starts an AI review: a read step, Next always works.
        Step("community-publish", "flashcard-set", "Share your set",
             "Tap Publish on one of your own sets. Learnova checks it first. If it is okay, other students can find "
             "it.",
             targets=("#ovPublish",), requires=("community_publishing",), icon="📤"),
    ),
)

FLASHCARD_STUDY = Section(
    id="flashcard-study", title="Study a set", icon="🔄", group="practice", page="flashcard-mode",
    anchor="#modeConfig", url="@flashcard_study", where="/flashcards",
    when="Starts when you study one of your flashcard sets.", requires=_CARDS,
    steps=(
        Step("practice-study", "flashcard-mode", "Choose what to practise",
             "Study all cards, new ones, the ones due today, the hard ones, your starred cards or a quick session.",
             targets=("#modeConfig",), icon="🎯"),
        # Starting a session is free and changes nothing but the session itself, so doing it
        # moves on: the star sits on the running card.
        Step("practice-start", "flashcard-mode", "Flip and check",
             "Tap Start session. Flip each card and tap Correct or Wrong. Cards you get wrong come back sooner.",
             kind="action", advance="click", targets=("#startMode",), icon="🔄"),
        Step("practice-star", "flashcard-mode", "Star a card",
             "Tap ☆ to star a card. Practice starred cards then lets you study just those.",
             targets=("#modeStar",), wait_ms=2000, icon="⭐"),
    ),
)

TODAYS_PRACTICE = Section(
    id="todays-practice", title="Today's Practice", icon="📅", group="practice", page="practice-today",
    anchor='[data-tour-target="today-plan"]', url="/practice/today",
    steps=(
        Step("practice-today", "practice-today", "Today's Practice",
             "Learnova puts together what is due today: topics to review, repeated mistakes and weak spots. When "
             "something is due, one tap starts it.",
             targets=('[data-tour-target="today-plan"]',), icon="📅"),
    ),
)

MISTAKE_INSIGHTS = Section(
    id="mistake-insights", title="Mistake Intelligence", icon="🧠", group="practice", page="mistakes",
    anchor='[data-tour-target="mistake-patterns"]', url="/insights/mistakes",
    steps=(
        Step("practice-insights", "mistakes", "Mistake Intelligence",
             "Learnova looks for patterns in your mistakes and tells you what to practise next.",
             targets=('[data-tour-target="mistake-patterns"]',), icon="🧠"),
    ),
)

DASHBOARD = Section(
    id="dashboard", title="Your Overview", icon="🏠", group="practice", page="dashboard",
    anchor='[data-tour-target="dashboard-overview"]', url="/dashboard",
    steps=(
        Step("more-overview", "dashboard", "Your Overview",
             "Your Overview shows what is due today, your weak topics and your projects in one place.",
             targets=('[data-tour-target="dashboard-overview"]',), icon="🏠"),
        Step("lessons-saved", "dashboard", "Saved for later",
             "Every lesson is saved automatically. Find it again on your Overview and carry on where you stopped.",
             targets=('[data-tour-target="lesson-history"]',), icon="💾"),
        Step("practice-mistakes", "dashboard", "Mistake Notebook",
             "Wrong answers from your lessons are saved here. Try a similar question, or mark one as understood.",
             targets=("#mistake-notebook",), icon="📓"),
    ),
)

# ---------------------------------------------------------------------- chapters: profile

PROGRESS = Section(
    id="progress", title="Your progress", icon="📈", group="profile", page="progress",
    anchor='[data-tour-target="progress-hero"]', url="/progress", requires=_GAMES,
    steps=(
        Step("practice-progress", "progress", "Your progress", "Here you see your level, your XP and your streak.",
             targets=('[data-tour-target="progress-hero"]',), icon="📈"),
        Step("practice-goal", "progress", "Daily goal", "Set a goal for today: questions, cards, minutes or XP.",
             targets=("#dailyGoalForm",), requires=("daily_goals_enabled",), icon="🏁"),
    ),
)

# Avatars only (there is no photo upload). The Premium badge exists only for Premium.
MY_PROFILE = Section(
    id="my-profile", title="My profile", icon="👤", group="profile", page="profile",
    anchor='[data-tour-target="profile-hero"]', url="/profile",
    steps=(
        Step("profile-hero", "profile", "This is you",
             "Your avatar, your username and your level. Others see them when you publish a set or play in a league.",
             targets=('[data-tour-target="profile-hero"]',), icon="🙂"),
        Step("profile-premium", "profile", "Premium badge", "You have Premium, so a badge is shown next to your name.",
             targets=('[data-tour-target="profile-name"]',), requires=("premium",), icon="💎"),
        Step("profile-challenges", "profile", "Challenges",
             "Three new challenges every day and three every week. Finish them for extra XP.",
             targets=('[data-tour-target="challenges"]',), requires=_GAMES, icon="🏅"),
        Step("profile-avatars", "profile", "Avatars",
             "Choose one of 20 characters. A new one unlocks at each level, up to level 20. Tap one to see it.",
             targets=('[data-tour-target="avatars"]', "#avatars"), also=("#avatars .avatar-grid > li:first-child",),
             requires=_GAMES, icon="🎭"),
        # Without XP no new character unlocks; choosing among the unlocked ones still works.
        Step("profile-avatars-pick", "profile", "Avatars",
             "Choose your avatar from the characters you have unlocked. Tap one to see it.",
             targets=('[data-tour-target="avatars"]', "#avatars"), also=("#avatars .avatar-grid > li:first-child",),
             requires=("!gamification_enabled",), icon="🎭"),
        Step("profile-league", "profile", "Your league", "This shows your league this week. Tap it to see the ranking.",
             targets=('[data-tour-target="league-card"]',), requires=_LEAGUES + ("leagues_opted_in",), icon="🏆"),
    ),
)

LEAGUES = Section(
    id="leagues", title="Leagues", icon="🪜", group="profile", page="leaderboard",
    anchor='[data-tour-target="league-ladder"]', url="/leaderboard", requires=_LEAGUES,
    steps=(
        Step("leagues-ladder", "leaderboard", "Seven leagues",
             "Climb from Bronze all the way up to Legendary. When the week ends, the top {league_up} move up a "
             "league and the bottom {league_down} move down.",
             targets=('[data-tour-target="league-ladder"]',), icon="🪜"),
        Step("leagues-standing", "leaderboard", "This week's group",
             "You play in a group of up to {league_size} learners. The XP you earn this week decides your place.",
             targets=('[data-tour-target="league-standing"]',), icon="📊"),
    ),
)

# -------------------------------------------------------------------- chapters: community

# The list and a set share one page (community.js opens a set without a page load): the
# set chapter's anchor is the set's own Study button.
COMMUNITY_LIBRARY = Section(
    id="community-library", title="Community", icon="🌍", group="community", page="community",
    anchor="#commFilters", url="/community", requires=("community_library",),
    steps=(
        Step("community-search", "community", "Find sets",
             "Search for a topic and filter by subject, class, difficulty or language.",
             targets=("#commFilters",), icon="🔎"),
        Step("community-discover", "community", "Discover",
             "See trending, top-rated and new sets from other students. Tap a set to open it.",
             targets=('[data-tour-target="community-discover"]',), wait_ms=2500, icon="✨"),
        Step("community-ratings", "community", "Two kinds of ratings",
             "Sets show an AI quality score and stars from students who studied them.",
             targets=("#commGrid .community-card",), wait_ms=2500, icon="⭐"),
    ),
)

COMMUNITY_SET = Section(
    id="community-set", title="A community set", icon="📖", group="community", page="community",
    anchor="#detailStudy", url="@community_set", where="/community",
    when="Starts when you open a set in the community.", requires=("community_library",),
    steps=(
        Step("community-study", "community", "Study and save",
             "Study a set right here, or save your own copy to change it.",
             targets=("#detailStudy",), icon="📖"),
        Step("community-rate", "community", "Rate a set",
             "After you have studied a set, you can give it stars. You cannot rate your own sets.",
             targets=("#rateWidget", "#detailStudy"), icon="🌟"),
        Step("community-report", "community", "Report a problem",
             "Something wrong or not okay in a set? Tap Report this set and choose a reason. Learnova checks it "
             "again.",
             targets=("#detailReport",), requires=("community_moderation",), icon="🚩"),
    ),
)

# ---------------------------------------------------------------------- chapters: account

SETTINGS = Section(
    id="settings", title="Settings", icon="⚙️", group="account", page="settings",
    anchor='[data-tour-target="settings-language"]', url="/settings",
    steps=(
        Step("account-language", "settings", "Language",
             "Choose the language of Learnova and of your lessons: {languages}.",
             targets=('[data-tour-target="settings-language"]',), icon="🌐"),
        Step("account-grade", "settings", "Your class",
             "Your class and school type help Learnova explain things at the right level. Keep them up to date.",
             targets=('[data-tour-target="settings-grade"]',), also=("#school_type",), icon="🎓"),
        Step("profile-username", "settings", "Change your username",
             "Use small letters, numbers, dots, dashes or underscores. You need your password. You can change your "
             "username once every {username_days} days.",
             targets=("#new_username",), icon="🏷️"),
        Step("profile-public", "settings", "Public profile",
             "Choose if others can see your level, streak and achievements.",
             targets=('[data-tour-target="settings-public"]',), icon="👀"),
        Step("profile-leagues-optout", "settings", "Leagues are optional",
             "Do not want to compete? Switch leagues off. XP, levels and challenges keep working.",
             targets=('[data-tour-target="settings-leagues"]',), requires=_LEAGUES, icon="🔕"),
        Step("account-theme", "settings", "Light or dark",
             "Switch between light and dark mode. Learnova remembers your choice on this device.",
             targets=('[data-tour-target="theme"]',), icon="🌓"),
    ),
)

# Starts when the student scrolls down to the plan. Activating a code is the student's own
# choice: read steps only.
SETTINGS_PLAN = Section(
    id="settings-plan", title="Your plan", icon="💎", group="account", page="settings",
    anchor='[data-tour-target="plan-status"]', url="/settings#subscription",
    steps=(
        Step("account-status-free", "settings", "Your plan",
             "You are on the Free plan. Here you see what you have used: new projects, new lessons today and the AI "
             "assistant.",
             targets=('[data-tour-target="plan-status"]',), also=('[data-tour-target="plan-usage"]',), requires=("free",),
             icon="🆓"),
        Step("account-status-premium", "settings", "Your plan", "You have Premium. Here you see until when it is active.",
             targets=('[data-tour-target="plan-status"]',), also=(".sub-status",), requires=("premium",), icon="💎"),
        Step("account-compare", "settings", "Free or Premium",
             "Premium costs {price} € a month. It removes the limits on new projects, new lessons and the AI "
             "assistant, and shows no ads.",
             targets=('[data-tour-target="plan-compare"]',), icon="⚖️"),
        Step("account-code", "settings", "Activation code",
             "Pay in cash at school and you get a code. Type it here and tap Activate Premium. Premium does not renew "
             "by itself.",
             targets=('[data-tour-target="plan-code"]',), icon="🔑"),
    ),
)

# Hub and payload order: the quick tour first, then the chapters by group; on one page the
# earlier chapter is the one that starts by itself first.
SECTIONS: tuple[Section, ...] = (
    OVERVIEW,
    CHAT_BASICS, CHAT_ANSWERS, CHAT_HISTORY,
    LESSON_START, LESSON_INSIDE, LESSON_TUTOR, LESSON_TEST,
    PROJECTS_START, PROJECT_DRAFT, PROJECT_WORKSPACE, PROJECT_PROGRESS, PROJECT_REVIEW, EXAM_RESULTS, STUDY_SESSION,
    FLASHCARDS_LIBRARY, FLASHCARD_SET, FLASHCARD_STUDY, TODAYS_PRACTICE, MISTAKE_INSIGHTS, DASHBOARD,
    PROGRESS, MY_PROFILE, LEAGUES,
    COMMUNITY_LIBRARY, COMMUNITY_SET,
    SETTINGS, SETTINGS_PLAN,
)


# ----------------------------------------------------------------------- lookups

def section(section_id: str) -> Section | None:
    return next((item for item in SECTIONS if item.id == section_id), None)


def step(step_id: str) -> Step | None:
    return next((item for entry in SECTIONS for item in entry.steps if item.id == step_id), None)


def all_steps() -> list[tuple[Section, Step]]:
    return [(entry, item) for entry in SECTIONS for item in entry.steps]


def chapters() -> list[Section]:
    return [entry for entry in SECTIONS if entry.kind == "chapter"]


def group(group_id: str) -> Group | None:
    return next((item for item in GROUPS if item.id == group_id), None)


def all_msgids() -> set[str]:
    """Every English string the payload or the hub can contain (the translation test checks them)."""

    found: set[str] = set(UI.values()) | set(OFFER.values()) | {ERROR_UNKNOWN}
    found |= {line[2] for line in CAN_DO}
    found |= {page.label for page in PAGES.values()}
    found |= {container.text for container in CONTAINERS}
    found |= {item.title for item in GROUPS}
    for entry in SECTIONS:
        found |= {entry.title, entry.summary, entry.when}
        for item in entry.steps:
            found |= {item.title, item.text}
            if item.create:
                found.add(item.create[0])
    found.discard("")
    return found

