document.querySelectorAll('[data-planner-skeleton]').forEach((element) => {
  requestAnimationFrame(() => element.setAttribute('hidden', ''));
});
