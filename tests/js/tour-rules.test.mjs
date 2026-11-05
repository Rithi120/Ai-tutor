// The tutorial engine's pure rules (static/js/tour-rules.js). Run with: node --test tests/js
// (Node is not installed here: tests/test_js_browser.py runs this file unchanged in Chrome.)
import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_WAIT, DWELL_MS, ENDED_KEY, FOLLOW_GAP_MS, HOLD_MS, LEGACY_KEY, OFFER_KEY, PAYLOAD_KEY, PHONE_MAX, RUN_KEY,
  RUN_VERSION, TYPING_MS,
  advancesOn, autoCandidates, bandScroll, clampRect, closeOnLeave, counter, displayMode, endStatus, fillText,
  firstIndex, fitRect, followUps, forViewport, isTall, leaveStatus, lightBand, mayAutoStart, nextIndex, normalizeStep,
  offerDismissed, offerValue, onPage, onScreen, overlapArea, pageKey, parseAuto, parseEnded, parseRun, placePanel,
  prevIndex, resumeIndex, runFitsPage, safePath, samePlace, sectionOf, sectionSteps, sentence, serializeEnded,
  serializeRun, shouldOffer, skipTarget,
} from "../../static/js/tour-rules.js";

const PAYLOAD = {
  sections: [
    { id: "overview", kind: "overview", page: "*", anchor: "", steps: ["o-nav", "o-menu", "o-done"] },
    { id: "lesson-test", kind: "chapter", page: "index", anchor: "#questionCard", steps: ["l-start", "l-question", "l-check", "l-sure"] },
    { id: "lesson-start", kind: "chapter", page: "index", anchor: ".subject-picker", steps: ["l-subject"] },
    { id: "chat-basics", kind: "chapter", page: "chat", anchor: "#asstInput", steps: ["c-input"] },
  ],
  steps: [
    { id: "o-nav", section: "overview", order: 0, page: "*", kind: "read", targets: ["nav"], viewport: "desktop" },
    { id: "o-menu", section: "overview", order: 1, page: "*", kind: "read", targets: [".toggle"], viewport: "phone" },
    { id: "o-done", section: "overview", order: 2, page: "*", kind: "card", targets: [] },
    { id: "l-start", section: "lesson-test", order: 0, page: "index", kind: "action", advance: "click", targets: ["#startTest"], wait: 600 },
    { id: "l-question", section: "lesson-test", order: 1, page: "index", kind: "read", targets: ["#questionCard"], wait: 2500 },
    { id: "l-check", section: "lesson-test", order: 3, page: "index", kind: "read", targets: ["#check"] },
    { id: "l-sure", section: "lesson-test", order: 4, page: "index", kind: "read", targets: ["#sure"] },
    { id: "l-subject", section: "lesson-start", order: 0, page: "index", kind: "action", advance: "change", targets: [".subject-picker"] },
    { id: "c-input", section: "chat-basics", order: 0, page: "chat", kind: "read", targets: ["#asstInput"] },
  ],
};
const TEST = sectionSteps(PAYLOAD, "lesson-test");
const TOUR = sectionSteps(PAYLOAD, "overview");

test("the storage keys and constants the engine and the browser test rely on", () => {
  assert.equal(RUN_KEY, "learnova.tutorial.run");
  assert.equal(PAYLOAD_KEY, "learnova.tutorial.payload");
  assert.equal(OFFER_KEY, "learnova.tutorial.offer");
  assert.equal(LEGACY_KEY, "learnova.tour.v2");
  assert.equal(PHONE_MAX, 820);
  assert.equal(RUN_VERSION, 5);
  assert.ok(HOLD_MS < DEFAULT_WAIT);
  assert.ok(DWELL_MS >= 500, "an anchor must stay a moment before a chapter starts by itself");
  assert.ok(TYPING_MS >= 1000);
  assert.ok(FOLLOW_GAP_MS >= 2000, "never two chapters back to back");
  assert.equal(ENDED_KEY, "learnova.tutorial.ended");
});

test("pages are recognised by path, with or without a trailing slash (the legacy lines are kept)", () => {
  assert.equal(pageKey("/"), "chat");
  assert.equal(pageKey("/assistant"), "chat");
  assert.equal(pageKey("/assistant/c/abc"), "chat");
  assert.equal(pageKey("/lesson"), "index");
  assert.equal(pageKey("/lesson/"), "index");
  assert.equal(pageKey("/dashboard/"), "dashboard");
  assert.equal(pageKey("/tutorial"), "tutorial");
  assert.equal(pageKey("/onboarding/grade"), "onboarding");
  assert.equal(pageKey("/projects"), "other");
  assert.equal(pageKey(""), "chat");
});

test("a stored run is read defensively", () => {
  assert.equal(parseRun(null), null);
  assert.equal(parseRun(""), null);
  assert.equal(parseRun("garbage"), null);
  assert.equal(parseRun('{"index": 4}'), null, "the old v2 shape is no run");
  assert.equal(parseRun('{"v": 4, "section": "lessons", "step": "l-build", "u": "a1"}', null, "a1"), null,
    "a run of the tutorial before the quick tour and chapters");
  assert.equal(parseRun('{"v": 5, "section": "", "step": "x"}'), null);
  assert.equal(parseRun('{"v": 5, "section": "lesson-test", "step": 5}'), null);
  assert.equal(parseRun('{"v": 5, "section": "lessons", "step": "x"}', PAYLOAD), null, "a retired section id");
  assert.deepEqual(parseRun('{"v": 5, "section": "lesson-test", "step": "l-check", "auto": 1, "u": "a1"}', PAYLOAD, "a1"),
    { v: 5, section: "lesson-test", step: "l-check", auto: true, u: "a1" });
});

test("a run another account left in this tab is no run (a shared school device)", () => {
  const alice = serializeRun({ section: "lesson-test", step: "l-check" }, "a1");
  assert.equal(parseRun(alice, PAYLOAD, "b2"), null, "Bob does not continue Alice's run");
  assert.equal(parseRun(alice, PAYLOAD, ""), null);
  assert.equal(parseRun('{"v": 5, "section": "lesson-test", "step": "l-check"}', PAYLOAD, "b2"), null, "nor an untagged one");
  assert.equal(parseRun(alice, PAYLOAD, "a1").step, "l-check");
  assert.equal(offerDismissed(offerValue("a1"), "a1"), true);
  assert.equal(offerDismissed(offerValue("a1"), "b2"), false, "Alice's Explore on my own is not Bob's");
  assert.equal(offerDismissed("dismissed", "b2"), false, "the untagged value of the old engine either");
  assert.equal(offerDismissed(null, "b2"), false);
});

test("a run survives the round trip through storage", () => {
  const raw = serializeRun({ section: "overview", step: "o-done", auto: false, extra: "dropped" }, "a1");
  assert.deepEqual(JSON.parse(raw), { v: 5, section: "overview", step: "o-done", auto: false, u: "a1" });
  assert.deepEqual(parseRun(raw, null, "a1"), { v: 5, section: "overview", step: "o-done", auto: false, u: "a1" });
  assert.deepEqual(parseRun(raw), { v: 5, section: "overview", step: "o-done", auto: false, u: "a1" }, "no account given: not checked");
});

test("a section's steps come in order with every default filled in", () => {
  assert.deepEqual(TEST.map(step => step.id), ["l-start", "l-question", "l-check", "l-sure"]);
  assert.equal(TEST[2].viewport, "any");
  assert.equal(TEST[2].wait, DEFAULT_WAIT);
  assert.deepEqual(TEST[2].also, []);
  assert.equal(TEST[1].wait, 2500);
  assert.deepEqual(sectionSteps(PAYLOAD, "nope"), []);
  assert.deepEqual(sectionSteps(null, "lesson-test"), []);
  assert.equal(normalizeStep({ id: "x" }).kind, "read");
  assert.equal(sectionOf(PAYLOAD, "chat-basics").page, "chat");
  assert.equal(sectionOf(PAYLOAD, "nope"), null);
});

test("a stored run continues only where it belongs", () => {
  assert.equal(runFitsPage(sectionOf(PAYLOAD, "overview"), "settings"), true, "the quick tour runs on every page");
  assert.equal(runFitsPage(sectionOf(PAYLOAD, "lesson-test"), "index"), true);
  assert.equal(runFitsPage(sectionOf(PAYLOAD, "lesson-test"), "chat"), false, "a chapter is left when the student goes elsewhere");
  assert.equal(runFitsPage(null, "index"), false);
});

test("viewport-only steps exist only on their layout", () => {
  assert.equal(forViewport(TOUR[0], false), true);
  assert.equal(forViewport(TOUR[0], true), false);
  assert.equal(forViewport(TOUR[1], true), true);
  assert.equal(forViewport(TOUR[1], false), false);
  assert.equal(forViewport(TOUR[2], true), true);
  assert.equal(onPage(TEST[0], "index"), true);
  assert.equal(onPage(TEST[0], "chat"), false);
  assert.equal(onPage(TOUR[2], "chat"), true, "a * step belongs to every page");
});

test("Next skips the other layout's steps and the ones found missing, and ends past the last step", () => {
  const phone = { page: "dashboard", isPhone: true };
  const desk = { page: "dashboard", isPhone: false };
  assert.equal(nextIndex(TOUR, -1, phone), 1, "the desktop-only step is skipped on a phone");
  assert.equal(nextIndex(TOUR, -1, desk), 0);
  assert.equal(nextIndex(TOUR, 0, desk), 2, "the phone-only step is skipped on a computer");
  assert.equal(nextIndex(TOUR, 2, desk), TOUR.length, "past the end = finished");
  const skip = new Set(["l-question"]);
  assert.equal(nextIndex(TEST, 0, { page: "index", isPhone: false, skip }), 2, "a missing step is not shown again");
  assert.equal(firstIndex(TEST, { page: "index", isPhone: false, skip: new Set(["l-start"]) }), 1);
});

test("Previous never lands on a skipped step and is disabled on the first one", () => {
  const here = { page: "index", isPhone: false };
  assert.equal(prevIndex(TEST, 0, here), -1);
  assert.equal(prevIndex(TEST, 2, here), 1);
  assert.equal(prevIndex(TEST, 2, { ...here, skip: new Set(["l-question"]) }), 0);
  assert.equal(prevIndex(TEST, 1, { ...here, skip: new Set(["l-start"]) }), -1, "nothing before it: Previous is off");
  assert.equal(prevIndex(TOUR, 2, { page: "*", isPhone: true }), 1);
  assert.equal(prevIndex(TOUR, 2, { page: "*", isPhone: false }), 0);
});

test("the counter leaves out the other layout's steps and the ones found missing", () => {
  assert.deepEqual(counter(TEST, 0, false), { current: 1, total: 4 });
  assert.deepEqual(counter(TEST, 3, false), { current: 4, total: 4 });
  assert.deepEqual(counter(TEST, 2, false, new Set(["l-start"])), { current: 2, total: 3 }, "Start test was already gone");
  assert.deepEqual(counter(TOUR, 2, true), { current: 2, total: 2 });
  assert.deepEqual(counter(TOUR, 1, true), { current: 1, total: 2 });
  assert.equal(counter(TEST, 99, false), null);
});

test("display modes: every state the panel can be in", () => {
  const base = { page: "index", found: false, openerFound: false, busyFound: false, elapsed: 5000, otherDialogOpen: false };
  const read = TEST[1];
  assert.equal(displayMode({ ...base, step: read, otherDialogOpen: true, found: true }), "paused");
  assert.equal(displayMode({ ...base, step: TOUR[2] }), "card");
  assert.equal(displayMode({ ...base, step: read, page: "chat" }), "elsewhere", "a chapter step on another page");
  assert.equal(displayMode({ ...base, step: read, found: true }), "spot");
  assert.equal(displayMode({ ...base, step: read, openerFound: true }), "opener");
  assert.equal(displayMode({ ...base, step: read, busyFound: true }), "busy");
  assert.equal(displayMode({ ...base, step: read, elapsed: 100 }), "hold");
  assert.equal(displayMode({ ...base, step: read, elapsed: 2000 }), "pending", "this step waits 2500 ms");
  assert.equal(displayMode({ ...base, step: read, elapsed: 2501 }), "missing");
  assert.equal(displayMode({ ...base, step: TEST[2], elapsed: DEFAULT_WAIT + 1 }), "missing", "the default wait");
});

test("a step that is not there is skipped silently: on, back after Previous, never in a loop", () => {
  const here = { page: "index", isPhone: false, skip: new Set(["l-question"]) };
  assert.deepEqual(skipTarget(TEST, 1, 1, here), { index: 2, direction: 1 });
  assert.deepEqual(skipTarget(TEST, 1, -1, here), { index: 0, direction: -1 }, "Previous over a missing step");
  const nothingBefore = { ...here, skip: new Set(["l-start", "l-question"]) };
  assert.deepEqual(skipTarget(TEST, 1, -1, nothingBefore), { index: 2, direction: 1 }, "nothing earlier: on again");
  assert.deepEqual(skipTarget(TEST, 3, 1, { ...here, skip: new Set(["l-sure"]) }), { index: TEST.length, direction: 1 },
    "past the last step the run is over");
});

test("only action steps advance on the student's own action; cards never do", () => {
  const change = sectionSteps(PAYLOAD, "lesson-start")[0];
  const click = TEST[0];
  const input = normalizeStep({ id: "g", kind: "action", advance: "input" });
  assert.equal(advancesOn(click, { type: "click", insideTarget: true }), true);
  assert.equal(advancesOn(click, { type: "click", insideTarget: false }), false);
  assert.equal(advancesOn(change, { type: "change", insideTarget: true }), true);
  assert.equal(advancesOn(change, { type: "click", insideTarget: true }), false);
  assert.equal(advancesOn(input, { type: "input", insideTarget: true, textLength: 2 }), false);
  assert.equal(advancesOn(input, { type: "input", insideTarget: true, textLength: 3 }), true);
  assert.equal(advancesOn(input, { type: "change", insideTarget: true, textLength: 5 }), true);
  assert.equal(advancesOn(TOUR[2], { type: "click", insideTarget: true }), false, "a card waits for Next");
  assert.equal(advancesOn(TEST[1], { type: "click", insideTarget: true }), false, "a read step waits for Next");
  assert.equal(advancesOn(null, { type: "click", insideTarget: true }), false);
});

test("a run resumes at its step, after a step a flag removed, or from the start", () => {
  assert.equal(resumeIndex(TEST, "l-check"), 2);
  assert.equal(resumeIndex(TEST, ""), 0);
  assert.equal(resumeIndex(TEST, "l-removed", { "l-removed": 2 }), 2, "the first visible step with a higher order");
  assert.equal(resumeIndex(TEST, "l-removed", new Map([["l-removed", 2]])), 2);
  assert.equal(resumeIndex(TEST, "unknown"), 0);
  assert.equal(resumeIndex(TEST, "l-late", { "l-late": 99 }), 0);
});

test("only same-site paths are followed", () => {
  assert.equal(safePath("/tutorial"), "/tutorial");
  assert.equal(safePath("//evil.example/x"), null, "not protocol-relative");
  assert.equal(safePath("https://evil.example"), null);
  assert.equal(safePath(""), null);
  assert.equal(safePath(null), null);
});

test("same place = same path and query", () => {
  assert.equal(samePlace("/lesson", "http://x.test/lesson"), true);
  assert.equal(samePlace("/lesson/", "http://x.test/lesson#top"), true);
  assert.equal(samePlace("/lesson", "http://x.test/?session_id=1"), false);
  assert.equal(samePlace("/?goal=a", "http://x.test/?goal=b"), false);
  assert.equal(samePlace("", "http://x.test/"), false);
});

test("the first-time offer: only for a new account, never on the grade prompt or the hub", () => {
  const offer = { autostart: true, page: "chat", run: null, dismissed: false };
  assert.equal(shouldOffer(offer), true);
  assert.equal(shouldOffer({ ...offer, autostart: false }), false);
  assert.equal(shouldOffer({ ...offer, page: "onboarding" }), false);
  assert.equal(shouldOffer({ ...offer, page: "tutorial" }), false);
  assert.equal(shouldOffer({ ...offer, run: { section: "overview" } }), false);
  assert.equal(shouldOffer({ ...offer, dismissed: true }), false);
});

test("the server's list of chapters that may start by themselves is read defensively", () => {
  assert.deepEqual(parseAuto("lesson-test lesson-start:l-subject"),
    [{ id: "lesson-test", step: "" }, { id: "lesson-start", step: "l-subject" }]);
  assert.deepEqual(parseAuto(""), []);
  assert.deepEqual(parseAuto(null), []);
  assert.deepEqual(parseAuto("<b> Bad lesson-test"), [{ id: "lesson-test", step: "" }], "only real ids");
  const index = autoCandidates(PAYLOAD, parseAuto("lesson-test lesson-start chat-basics overview gone"), "index");
  assert.deepEqual(index.map(item => item.id), ["lesson-test", "lesson-start"],
    "this page's chapters only, in the server's order; never the quick tour or an unknown id");
  assert.deepEqual(autoCandidates(PAYLOAD, parseAuto("chat-basics"), "index"), []);
});

test("a chapter starts by itself only when nothing speaks against it", () => {
  const calm = { run: null, offerOpen: false, dialogOpen: false, busy: false, sinceTyping: 99999,
    visibleFor: DWELL_MS };
  assert.equal(mayAutoStart(calm), true);
  assert.equal(mayAutoStart({ ...calm, visibleFor: DWELL_MS - 1 }), false, "the anchor has to stay a moment");
  assert.equal(mayAutoStart({ ...calm, visibleFor: undefined }), false, "the anchor is not on screen");
  assert.equal(mayAutoStart({ ...calm, offerOpen: true }), false, "never over the first-time offer");
  assert.equal(mayAutoStart({ ...calm, run: { section: "overview" } }), false, "never over another run");
  assert.equal(mayAutoStart({ ...calm, sinceEnded: FOLLOW_GAP_MS - 1 }), false, "a follow-up waits after a run or the offer");
  assert.equal(mayAutoStart({ ...calm, sinceEnded: FOLLOW_GAP_MS }), true);
  assert.equal(mayAutoStart({ ...calm, sinceEnded: Infinity }), true, "nothing ended on this page load");
  assert.equal(mayAutoStart({ ...calm, dialogOpen: true }), false, "never over a dialog or an open menu");
  assert.equal(mayAutoStart({ ...calm, busy: true }), false, "never while Learnova is writing");
  assert.equal(mayAutoStart({ ...calm, sinceTyping: TYPING_MS - 1 }), false, "never while the student types");
  assert.equal(mayAutoStart({ ...calm, sinceTyping: TYPING_MS }), true);
});

test("a follow-up on the same page load: never one that ended in this tab or was on screen when the last run ended", () => {
  const list = [{ id: "chat-basics" }, { id: "chat-answers" }, { id: "chat-history" }];
  assert.deepEqual(followUps(list, new Set(["chat-basics"]), new Set(["chat-history"])).map(item => item.id), ["chat-answers"]);
  assert.deepEqual(followUps(list, null, null).map(item => item.id), ["chat-basics", "chat-answers", "chat-history"]);
  const raw = serializeEnded(new Set(["flashcard-set"]), "a1");
  assert.deepEqual([...parseEnded(raw, "a1")], ["flashcard-set"]);
  assert.equal(parseEnded(raw, "b2").size, 0, "another account's list is no list");
  assert.equal(parseEnded("garbage", "a1").size, 0);
  assert.equal(parseEnded(null, "a1").size, 0);
});

test("using a lit link or submit button is doing the step", () => {
  assert.equal(leaveStatus({ kind: "chapter", isLast: true }), "completed");
  assert.equal(leaveStatus({ kind: "chapter", isLast: false }), "skipped", "never starts by itself again");
  assert.equal(leaveStatus({ kind: "overview", isLast: false }), "", "the quick tour goes on on the next page");
  assert.equal(leaveStatus({ kind: "overview", isLast: true }), "completed");
});

test("a lit box that fits is scrolled to the middle of the free band, not of the window", () => {
  const band = { top: 72, bottom: 812 - 240 - 14 };
  const box = { left: 0, top: 500, width: 300, height: 200 };
  const delta = bandScroll(box, band);
  assert.equal(box.top - delta + box.height / 2, band.top + (band.bottom - band.top) / 2);
  assert.ok(box.top - delta + box.height <= band.bottom, "nothing of it under the bottom sheet");
});

test("how a run ends decides its status", () => {
  assert.equal(endStatus("finish"), "completed");
  assert.equal(endStatus("skip"), "skipped");
  assert.equal(endStatus("exit"), "skipped", "Exit and Escape skip too");
});

test("a container opened for a step closes when the next target is outside it", () => {
  assert.equal(closeOnLeave({ inside: "#asstSidebar" }, false), true);
  assert.equal(closeOnLeave({ inside: "#asstSidebar" }, true), false);
  assert.equal(closeOnLeave(null, false), false);
});

test("placeholders are filled like the server's str.format and unknown ones stay", () => {
  assert.equal(fillText("Step {current} of {total}", { current: 2, total: 9 }), "Step 2 of 9");
  assert.equal(fillText("Highlighted: {name}", {}), "Highlighted: {name}");
  assert.equal(fillText(null, {}), "");
});

test("the ring stays on screen for a header button at the very top; an anchor must be on screen", () => {
  assert.deepEqual(clampRect({ left: -6, top: -6, width: 60, height: 50 }, 375, 812), { left: 4, top: 4, width: 50, height: 40 });
  assert.deepEqual(clampRect({ left: 350, top: 790, width: 40, height: 40 }, 375, 812), { left: 350, top: 790, width: 21, height: 18 });
  assert.equal(onScreen({ left: 10, top: 700, width: 100, height: 200 }, 375, 812), true, "partly on screen counts");
  assert.equal(onScreen({ left: 10, top: 900, width: 100, height: 200 }, 375, 812), false, "below the fold");
  assert.equal(onScreen({ left: 10, top: -300, width: 100, height: 200 }, 375, 812), false, "scrolled past");
  assert.equal(onScreen({ left: 10, top: 10, width: 0, height: 0 }, 375, 812), false);
});

test("phones: a bottom sheet, a top sheet when the target sits at the bottom (the chat composer)", () => {
  const sheet = { vw: 375, vh: 812, panelW: 360, panelH: 240, navH: 72 };
  assert.equal(placePanel({ ...sheet, rect: { left: 10, top: 100, width: 300, height: 60 } }).kind, "bottom");
  assert.equal(placePanel({ ...sheet, rect: { left: 10, top: 720, width: 300, height: 60 } }).kind, "top");
  // A tall target: the sheet that does not cover its middle; when both would, the half away from it.
  assert.equal(placePanel({ ...sheet, rect: { left: 0, top: 80, width: 300, height: 700 } }).kind, "bottom");
  assert.equal(placePanel({ ...sheet, panelH: 400, rect: { left: 0, top: 100, width: 300, height: 700 } }).kind, "top");
});

test("wider screens: below, else above, else beside the target - never over its middle", () => {
  const wide = { vw: 1280, vh: 900, panelW: 360, panelH: 220 };
  const below = placePanel({ ...wide, rect: { left: 100, top: 100, width: 200, height: 40 } });
  assert.deepEqual([below.kind, below.top], ["float", 154]);
  const above = placePanel({ ...wide, rect: { left: 100, top: 800, width: 200, height: 60 } });
  assert.equal(above.top, 800 - 14 - 220);
  const beside = placePanel({ ...wide, rect: { left: 40, top: 20, width: 300, height: 860 } });
  assert.equal(beside.left, 40 + 300 + 14, "a tall column gets the card to its right");
  const edge = placePanel({ ...wide, rect: { left: 1250, top: 300, width: 20, height: 20 } });
  assert.ok(edge.left + 360 <= 1280 - 12, "clamped inside the window");
});

test("no free side: the corner that covers the least of the lit element", () => {
  // Review m5: practice-study at 1280 x 800 - a wide section with its Start button at the
  // bottom right; the old rule put the card over the button's right end.
  const rect = { left: 250, top: 189, width: 780, height: 415 };
  const place = placePanel({ vw: 1280, vh: 800, panelW: 360, panelH: 240, rect });
  const box = { left: place.left, top: place.top, width: 360, height: 240 };
  const corners = [[12, 12], [1280 - 372, 12], [12, 800 - 252], [1280 - 372, 800 - 252]]
    .map(([left, top]) => overlapArea({ left, top, width: 360, height: 240 }, rect));
  assert.equal(overlapArea(box, rect), Math.min(...corners));
  assert.equal(overlapArea({ left: 0, top: 0, width: 10, height: 10 }, { left: 20, top: 20, width: 5, height: 5 }), 0);
  assert.equal(overlapArea({ left: 0, top: 0, width: 10, height: 10 }, { left: 5, top: 5, width: 10, height: 10 }), 25);
});

test("a target taller than the free band is lit from the band's top, with the card clear of it", () => {
  // Review M3: practice-today on a 375 x 812 phone lit the whole screen and nothing was dimmed.
  const phone = { vw: 375, vh: 812, panelW: 360, panelH: 240, navH: 64 };
  const tall = { left: 8, top: 72, width: 359, height: 1400 };
  const band = lightBand({ ...phone, rect: tall });
  assert.deepEqual(band, { top: 72, bottom: 812 - 240 - 14 });
  assert.equal(isTall(tall, band), true);
  const lit = fitRect(clampRect(tall, 375, 812), band);
  assert.ok(lit.top + lit.height <= 812 - 240 - 14, "ends above the bottom sheet: something stays dimmed");
  assert.equal(placePanel({ ...phone, rect: lit }).kind, "bottom");
  const small = { left: 10, top: 300, width: 200, height: 80 };
  assert.deepEqual(fitRect(small, band), small, "a normal target is lit as it is");
  // Wide screens: a full-width section leaves the card no side, so the band ends above it.
  const wide = { vw: 1280, vh: 800, panelW: 360, panelH: 240, navH: 64 };
  const section = { left: 40, top: 80, width: 1200, height: 1600 };
  const wideBand = lightBand({ ...wide, rect: section });
  assert.equal(wideBand.bottom, 800 - 240 - 14 - 12);
  const wideLit = fitRect(clampRect(section, 1280, 800), wideBand);
  const place = placePanel({ ...wide, rect: wideLit });
  assert.ok(place.top >= wideLit.top + wideLit.height, "the card sits below the lit part");
  const column = { left: 40, top: 80, width: 300, height: 1600 };
  assert.equal(lightBand({ ...wide, rect: column }).bottom, 800 - 12, "a narrow column keeps the card beside it");
});

test("announcements: one full stop, never two", () => {
  assert.equal(sentence("You are ready!"), "You are ready!");
  assert.equal(sentence("Not sure what to ask?"), "Not sure what to ask?");
  assert.equal(sentence("Step 2 of 9"), "Step 2 of 9.");
  assert.equal(sentence("Done."), "Done.");
  assert.equal(sentence(""), "");
});
