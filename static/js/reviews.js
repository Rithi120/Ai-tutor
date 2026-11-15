// Reviews on the About page (templates/components/_reviews_section.html) and the delete
// button in Settings. Everything works without JavaScript (plain forms and links); this
// adds the counter, the preview, saving without a reload and loading more in place.
// Review text is only ever inserted as text (textContent), never as HTML.

const csrf = () => document.querySelector("meta[name=csrf-token]")?.content || "";

document.querySelectorAll("form[data-confirm]").forEach(form => form.addEventListener("submit", event => {
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));

const form = document.querySelector("[data-review-form]");
if (form) {
  const text = form.querySelector("[data-review-text]");
  const counter = form.querySelector("[data-review-counter]");
  const preview = form.querySelector("[data-review-preview]");
  const previewBody = form.querySelector("[data-review-preview-body]");
  const message = form.querySelector("[data-review-message]");
  const max = Number(text?.getAttribute("maxlength") || 1000);

  const refresh = () => {
    const value = text.value.trim();
    counter.textContent = `${text.value.length} / ${max}`;
    counter.classList.toggle("is-near", text.value.length > max * 0.9);
    const stars = Number(form.querySelector("input[name=rating]:checked")?.value || 0);
    preview.hidden = !stars;
    previewBody.textContent = "";
    if (stars) {
      const line = document.createElement("span");
      line.className = "stars";
      line.textContent = "★".repeat(stars) + "☆".repeat(5 - stars);
      previewBody.append(line);
      if (value) {
        const body = document.createElement("p");
        body.className = "review-body";
        body.textContent = value;
        previewBody.append(body);
      }
    }
  };
  text?.addEventListener("input", refresh);
  form.querySelectorAll("input[name=rating]").forEach(input => input.addEventListener("change", refresh));
  refresh();

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    if (!form.querySelector("input[name=rating]:checked")) {
      message.textContent = form.querySelector("legend")?.textContent || "";
      message.className = "review-message is-error";
      return;
    }
    button.disabled = true;   // a double tap sends one request (the server allows one review anyway)
    try {
      const response = await fetch(form.action, {
        method: "POST", credentials: "same-origin",
        headers: { Accept: "application/json", "X-CSRFToken": csrf() },
        body: new FormData(form),
      });
      const data = await response.json().catch(() => ({}));
      message.textContent = data.message || "";
      message.className = `review-message ${data.ok ? "is-ok" : "is-error"}`;
    } catch (error) {
      form.submit();   // offline or blocked: the plain form still works
    } finally {
      button.disabled = false;
    }
  });
}

// Filters and sorting: changing a select applies it (the URL keeps it, so it can be shared).
document.querySelectorAll("[data-review-filters] select").forEach(select => select.addEventListener("change", () => {
  select.form.requestSubmit ? select.form.requestSubmit() : select.form.submit();
}));

const more = document.querySelector("[data-review-more]");
const list = document.querySelector("[data-review-list]");
more?.addEventListener("click", async event => {
  event.preventDefault();
  more.setAttribute("aria-busy", "true");
  try {
    const url = new URL(more.dataset.api, window.location.origin);
    url.searchParams.set("page", more.dataset.nextPage);
    const response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    const data = await response.json();
    const holder = document.createElement("template");
    holder.innerHTML = data.html;   // server-rendered, auto-escaped review cards
    list.append(holder.content);
    more.dataset.nextPage = String(data.page + 1);
    if (!data.has_more) more.remove();
  } catch (error) {
    window.location.href = more.href;
  } finally {
    more.removeAttribute("aria-busy");
  }
});

document.addEventListener("submit", async event => {
  const report = event.target.closest?.("[data-review-report]");
  if (!report) return;
  event.preventDefault();
  try {
    const response = await fetch(report.action, {
      method: "POST", credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": csrf() }, body: new FormData(report),
    });
    const data = await response.json().catch(() => ({}));
    const note = document.createElement("p");
    note.className = "review-message is-ok";
    note.textContent = data.message || "";
    report.replaceWith(note);
  } catch (error) {
    report.submit();
  }
});
