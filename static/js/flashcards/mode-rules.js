export function normalizedAnswer(value) {
  return String(value ?? "").trim().toLocaleLowerCase().replace(/\s+/g, " ").replace(/[.!?,;:]+$/, "");
}
export function scoreAnswer(correct, combo, mode) {
  if (!correct) return { combo: 0, points: 0 };
  const next = combo + 1;
  return { combo: next, points: (mode === "match" ? 120 : 100) + Math.min(100, next * 10) };
}
export function nextLives(lives, correct) { return correct ? lives : Math.max(0, lives - 1); }
export function shouldReduceMotion(mediaMatches, selected) { return Boolean(mediaMatches || selected); }
export function matchPair(first, second) {
  return Boolean(first && second && first.cardId === second.cardId && first.side !== second.side);
}
