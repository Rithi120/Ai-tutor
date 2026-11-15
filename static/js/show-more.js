/* "Show more" for long lists (owner request 2026-11-13): dashboard lists, Settings.
 *
 *   <div class="mistake-list" data-show-more="3" data-show-more-step="5"> ...items... </div>
 *
 * Shows the first data-show-more items and puts one button after the list, "Show more (N)"
 * with N the items still hidden; each tap reveals data-show-more-step more (the first
 * count when absent) and moves the focus to the first item it revealed. Once everything
 * is shown the button reads "Show less" and folds the list back. The decisions are in
 * show-more-rules.js. Without JavaScript nothing is hidden: the server renders every item
 * and no stylesheet hides any before this runs.
 */
import { t } from "./i18n.js";
import { buttonState, initialState, nextShown } from "./show-more-rules.js";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([type=hidden]), select, textarea, [tabindex]";
let counter = 0;

function focusItem(item) {
  if (!item) return;
  if (!item.matches(FOCUSABLE)) item.setAttribute("tabindex", "-1");
  item.focus({ preventScroll: true });
  item.scrollIntoView({ block: "nearest" });
}

export function enhance(list) {
  if (!(list instanceof HTMLElement) || list.dataset.showMoreReady) return null;
  list.dataset.showMoreReady = "1";
  const items = Array.from(list.children).filter(item => !["TEMPLATE", "SCRIPT"].includes(item.tagName));
  const state = initialState(items.length, list.dataset.showMore, list.dataset.showMoreStep);
  if (!state.collapsible) return null;
  if (!list.id) list.id = `showMoreList${++counter}`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary-button show-more-button";
  button.setAttribute("aria-controls", list.id);

  const render = () => {
    items.forEach((item, index) => { item.hidden = index >= state.shown; });
    const view = buttonState(state);
    button.textContent = view.kind === "more" ? t("showMoreCount", { count: view.remaining }) : t("showLess");
    button.setAttribute("aria-expanded", String(view.expanded));
  };

  button.addEventListener("click", () => {
    const before = state.shown;
    state.shown = nextShown(state);
    render();
    if (state.shown > before) {
      // Cards that enter here must not wait for the page's staggered entrance delay.
      items.slice(before, state.shown).forEach(item => item.style.setProperty("--delay", "0ms"));
      focusItem(items[before]);
    } else {
      button.scrollIntoView({ block: "nearest" });
    }
  });
  render();
  list.after(button);
  return button;
}

document.querySelectorAll("[data-show-more]").forEach(enhance);
