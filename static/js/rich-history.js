/* Stored tutor replies on the lesson history page, rendered like the live chat.
 *
 * The page used to print each reply as escaped text in a <p>, so a reply that had shown
 * a table, a list and formulas in the chat came back as raw "**", "|" and "\frac". The
 * server prints the stored text (escaped) into [data-rich-text][data-no-math]; this
 * module reads it back as text and runs the same renderer as static/app.js's
 * addChatMessage: renderMarkdown, then renderMath (and code colours). data-no-math keeps
 * the global formula pass off the raw text until it has been rendered here.
 */
import { highlightCode } from "./code-highlight.js";
import { renderMarkdown } from "./markdown.js";
import { renderMath } from "./math.js";

export function renderRichText(root = document) {
  for (const element of root.querySelectorAll("[data-rich-text]")) {
    const source = element.textContent;
    element.removeAttribute("data-rich-text");
    element.innerHTML = renderMarkdown(source);
    element.removeAttribute("data-no-math");
    renderMath(element);
    highlightCode(element);
  }
}

renderRichText();
