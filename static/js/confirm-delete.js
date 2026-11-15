/* One confirmation before a delete form is sent (dashboard projects and lessons, the
 * project workspace). The form carries its own question, rendered and translated on the
 * server:
 *
 *   <form method="post" action="/lessons/7/delete" data-confirm-delete="Delete the lesson ...?">
 *
 * The browser's own confirm dialog: accessible, keyboard-operable, the same pattern as
 * the page and flashcard deletes. Without JavaScript the form is a plain POST and still
 * works. A capturing listener on the window, so it runs before core.js's submit listener
 * on the document: that one marks the button busy in a microtask unless the event was
 * cancelled, and after a real tap that microtask runs as soon as its listener returns.
 * A cancelled delete therefore leaves its button as it was.
 */
window.addEventListener("submit", event => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm-delete")) return;
  const question = form.getAttribute("data-confirm-delete") || "";
  if (question && !window.confirm(question)) event.preventDefault();
}, true);
