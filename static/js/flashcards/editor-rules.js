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

/* A card's Type (Word / Sentence / Other) is stored as one reserved tag (server:
 * learnova/flashcards/service.py CARD_KINDS). The editor keeps it apart from the
 * student's own tags, so the Tags field can never delete it by accident. "phrase" is the
 * vocabulary importer's fixed phrase: shown as Word and kept until another type is tapped.
 * A card without a kind tag stays without one (shown as Word, its data never rewritten). */
export const KIND_TAGS = new Set(["word", "phrase", "sentence", "other"]);
export const KIND_CHOICES = ["word", "sentence", "other"];

// {kind, tags}: an explicit card.kind wins (the editor's own model, an API card); else the
// first kind tag. Idempotent, so a draft, a duplicate or a reload can run it again. Tags
// match exactly, as on the server (service.card_kind): "Sentence" is a topic tag.
export function splitKind(card) {
  const tags = (Array.isArray(card?.tags) ? card.tags : []).map(String);
  const explicit = typeof card?.kind === "string" ? card.kind.trim().toLowerCase() : null;
  const kind = explicit !== null
    ? (KIND_TAGS.has(explicit) ? explicit : "")
    : (tags.find(tag => KIND_TAGS.has(tag)) || "");
  return { kind, tags: tags.filter(tag => !KIND_TAGS.has(tag)) };
}

// What the menu shows as chosen: Word for a word, a phrase or a card without a kind.
export function displayKind(kind) {
  return kind === "sentence" || kind === "other" ? kind : "word";
}

// The kind after a tap. Tapping Word on a phrase or an untagged card changes nothing (it
// already is a word), so an untouched card is never rewritten.
export function tapKind(current, tapped) {
  if (!KIND_CHOICES.includes(tapped)) return current || "";
  if (tapped === "word" && displayKind(current) === "word") return current || "";
  return tapped;
}

// Tags typed into the Tags field. A reserved type word ("sentence") is not a tag - the
// server would drop it on save (the Type sets it) - so it is dropped here too.
export function typedTags(value) {
  return String(value || "").split(",").map(s => s.trim()).filter(tag => tag && !KIND_TAGS.has(tag));
}
