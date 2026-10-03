export const selectedLanguage = window.LEARNOVA_LANGUAGE === "de" ? "German" : "English";

export function t(key, values) {
  const template = window.LEARNOVA_I18N?.[key] || key;
  if (!values) return template;
  // Same placeholder shape as the server's translate(), which uses str.format, so one
  // catalogue entry reads the same whether Python or the browser fills it in.
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match);
}
