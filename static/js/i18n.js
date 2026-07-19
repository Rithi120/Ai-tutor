export const selectedLanguage = window.LEARNOVA_LANGUAGE === "de" ? "German" : "English";

export function t(key) {
  return window.LEARNOVA_I18N?.[key] || key;
}
