const navigation = document.querySelector("[data-app-navigation]");

function closeDesktopMenus(except = null, restoreFocus = false) {
  navigation?.querySelectorAll(".app-menu").forEach(menu => {
    if (menu === except) return;
    const trigger = menu.querySelector(".app-menu-trigger");
    const wasOpen = trigger?.getAttribute("aria-expanded") === "true";
    trigger?.setAttribute("aria-expanded", "false");
    const panel = menu.querySelector(".app-dropdown");
    panel?.classList.remove("is-open");
    panel?.setAttribute("aria-hidden", "true");
    if (restoreFocus && wasOpen) trigger?.focus({preventScroll: true});
  });
}

navigation?.querySelectorAll(".app-menu-trigger").forEach(trigger => {
  trigger.addEventListener("click", event => {
    event.stopPropagation();
    const menu = trigger.closest(".app-menu");
    const panel = menu?.querySelector(".app-dropdown");
    const opening = trigger.getAttribute("aria-expanded") !== "true";
    closeDesktopMenus(opening ? menu : null);
    trigger.setAttribute("aria-expanded", String(opening));
    panel?.classList.toggle("is-open", opening);
    panel?.setAttribute("aria-hidden", String(!opening));
    if (opening) panel?.querySelector("[role='menuitem']")?.focus({preventScroll: true});
  });
});
navigation?.querySelectorAll(".app-dropdown a").forEach(link => link.addEventListener("click", () => closeDesktopMenus()));
navigation?.querySelectorAll(".app-menu").forEach(menu => menu.addEventListener("focusout", event => {
  if (!menu.contains(event.relatedTarget)) closeDesktopMenus();
}));

const mobileToggle = navigation?.querySelector(".mobile-nav-toggle");
const mobileMenu = navigation?.querySelector(".mobile-app-menu");
const mobileBackdrop = navigation?.querySelector(".mobile-nav-backdrop");
let returnFocus = null;

function mobileFocusable() {
  return [...(mobileMenu?.querySelectorAll("a[href], button:not([disabled]), input:not([type='hidden'])") || [])];
}

function setMobileMenu(open) {
  if (!mobileToggle || !mobileMenu || !mobileBackdrop) return;
  mobileToggle.setAttribute("aria-expanded", String(open));
  mobileMenu.setAttribute("aria-hidden", String(!open));
  mobileMenu.classList.toggle("is-open", open);
  mobileBackdrop.classList.toggle("is-open", open);
  document.body.classList.toggle("mobile-menu-open", open);
  if (open) {
    returnFocus = document.activeElement;
    window.setTimeout(() => {
      if (!mobileMenu.classList.contains("is-open")) return;
      (mobileFocusable()[0] || mobileMenu).focus({preventScroll: true});
    }, 0);
  } else if (returnFocus instanceof HTMLElement) {
    returnFocus.focus({preventScroll: true});
  }
}

mobileToggle?.addEventListener("click", () => setMobileMenu(mobileToggle.getAttribute("aria-expanded") !== "true"));
navigation?.querySelectorAll("[data-mobile-nav-close]").forEach(item => item.addEventListener("click", () => setMobileMenu(false)));
mobileMenu?.querySelectorAll("a").forEach(link => link.addEventListener("click", () => setMobileMenu(false)));

document.addEventListener("click", event => {
  if (!event.target.closest(".app-menu")) closeDesktopMenus();
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    closeDesktopMenus(null, true);
    setMobileMenu(false);
  }
  if (event.key === "Tab" && mobileMenu?.classList.contains("is-open")) {
    const focusable = mobileFocusable();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  const openMenu = document.querySelector(".app-dropdown.is-open");
  if (openMenu && ["ArrowDown", "ArrowUp"].includes(event.key)) {
    const items = [...openMenu.querySelectorAll("[role='menuitem']")];
    const current = items.indexOf(document.activeElement);
    const offset = event.key === "ArrowDown" ? 1 : -1;
    event.preventDefault();
    items[(current + offset + items.length) % items.length]?.focus();
  }
});
