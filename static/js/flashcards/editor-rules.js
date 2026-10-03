// Pure rules for the flashcard editor, so they can be tested without a browser.

export function isBlankCard(card) {
  return !String(card?.front || "").trim() && !String(card?.back || "").trim();
}

// AI-generated cards take the place of the empty starter rows instead of being appended
// under them: a student who clicked "Generate" wants the cards at the top, not two blank
// rows they never typed in.
export function mergeGeneratedCards(existing, generated) {
  const kept = (existing || []).filter(card => !isBlankCard(card));
  return kept.concat(generated || []);
}

// A field needs a rendered preview when it carries maths: $...$, $$...$$ or \( ... \).
export function needsMathPreview(text) {
  const value = String(text || "");
  if (/\\\(.+?\\\)/s.test(value) || /\$\$[^$]+\$\$/s.test(value)) return true;
  const singles = value.split("$").length - 1;
  return singles >= 2 && /\$[^$\n]+\$/.test(value);
}
