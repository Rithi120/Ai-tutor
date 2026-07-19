const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const appMain = document.querySelector("body.authenticated-app main");
appMain?.classList.add("app-page");

const cardSelector = [
  ".dashboard-card", ".project-tile", ".saved-lesson", ".mistake", ".today-card",
  ".section-card", ".learning-section-row", ".exam-review", ".project-create-card"
].join(",");
document.querySelectorAll(cardSelector).forEach((card, index) => {
  card.classList.add("reveal-card");
  card.style.setProperty("--delay", `${Math.min(index * 45, 420)}ms`);
});

const progressItems = document.querySelectorAll(".mastery-bar i, .project-progress i, .trend-bars i, .result-bar i, .readiness-track i, [data-animate-progress]");
progressItems.forEach(item => item.classList.add("progress-animated"));
if (reducedMotion || !("IntersectionObserver" in window)) {
  progressItems.forEach(item => item.classList.add("progress-visible"));
} else {
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    entry.target.classList.add("progress-visible");
    observer.unobserve(entry.target);
  }), {threshold: .25});
  progressItems.forEach(item => observer.observe(item));
}

function countValue(element) {
  const explicit = element.dataset.countProgress;
  const match = element.textContent.trim().match(/^(\d+)(%)$/);
  if ((!explicit && !match) || reducedMotion) return;
  const target = Number(explicit ?? match[1]);
  const suffix = explicit ? (element.dataset.countSuffix || "") : match[2];
  const textNode = explicit ? [...element.childNodes].find(node => node.nodeType === Node.TEXT_NODE) : null;
  const started = performance.now();
  const duration = 520;
  function frame(now) {
    const progress = Math.min((now - started) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = `${Math.round(target * eased)}${suffix}`;
    if (textNode) textNode.nodeValue = value;
    else element.textContent = value;
    if (progress < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
document.querySelectorAll(".metric-row strong, [data-count-progress]").forEach(countValue);
