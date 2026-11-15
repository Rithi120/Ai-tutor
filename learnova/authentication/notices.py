"""Account and security messages (email and SMS), German and English. Flask-free.

Every message is transactional or security-related; none is marketing (`CATEGORIES`), so
none needs marketing consent - and `may_send` refuses a marketing message to anyone who
has not opted in, should one ever be added.

What a message never contains: a password, a one-time code (except the code SMS itself),
an OAuth code or token, the full phone number, or anything about the account beyond its
username. Links carry a signed, single-purpose token and nothing else.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

CATEGORIES = {
    "welcome": "transactional", "verify_email": "transactional", "password_reset": "security",
    "new_device": "security", "password_changed": "security", "account_recovered": "security",
    "provider_linked": "security", "provider_unlinked": "security", "phone_added": "security",
    "phone_changed": "security", "phone_removed": "security", "welcome_sms": "transactional",
}
PROVIDER_LABELS = {"google": "Google", "apple": "Apple", "phone": "Telefon / phone", "password": "Passwort / password"}


def may_send(kind: str, *, marketing_consent: bool = False) -> bool:
    category = CATEGORIES.get(kind)
    if category is None:
        return False
    return category != "marketing" or marketing_consent


def _lang(language: str) -> str:
    return "de" if str(language or "").lower().startswith("de") else "en"


FOOTER = {
    "de": "\n\n—\nLearnova · Diese Nachricht betrifft dein Konto und wird deshalb auch ohne Newsletter-Einwilligung "
          "verschickt.\nFragen? contact.studylearnova@gmail.com",
    "en": "\n\n—\nLearnova · This message is about your account, so it is sent even without newsletter "
          "consent.\nQuestions? contact.studylearnova@gmail.com",
}

TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "welcome": {
        "de": ("Willkommen bei Learnova, {name}!",
               "Hallo {name},\n\nschön, dass du da bist! Dein Learnova-Konto „{username}“ ist bereit.\n\n"
               "Frag im Chat alles, was du nicht verstehst, mach eine Kurzlektion oder plane deine nächste "
               "Klassenarbeit im Projektmodus.{verify}\n\nViel Erfolg beim Lernen!\nDein Learnova-Team"),
        "en": ("Welcome to Learnova, {name}!",
               "Hi {name},\n\ngreat to have you! Your Learnova account \"{username}\" is ready.\n\n"
               "Ask the chat anything you don't understand, take a short lesson, or plan your next test "
               "in Project Mode.{verify}\n\nHappy learning!\nThe Learnova team"),
    },
    "verify_email": {
        "de": ("Bestätige deine E-Mail-Adresse",
               "Hallo {name},\n\nbitte bestätige, dass diese E-Mail-Adresse zu deinem Learnova-Konto „{username}“ "
               "gehört:\n{link}\n\nDer Link gilt 48 Stunden. Wenn du kein Konto angelegt hast, ignoriere diese E-Mail."),
        "en": ("Confirm your email address",
               "Hi {name},\n\nplease confirm that this email address belongs to your Learnova account \"{username}\":\n"
               "{link}\n\nThe link works for 48 hours. If you did not create an account, ignore this email."),
    },
    "password_reset": {
        "de": ("Passwort zurücksetzen",
               "Hallo {name},\n\nfür dein Learnova-Konto „{username}“ wurde ein neues Passwort angefordert. "
               "Hier kannst du es festlegen:\n{link}\n\nDer Link gilt 60 Minuten und nur einmal. "
               "Wenn du das nicht warst, ignoriere diese E-Mail – dein Passwort bleibt gleich."),
        "en": ("Reset your password",
               "Hi {name},\n\na new password was requested for your Learnova account \"{username}\". Set it here:\n"
               "{link}\n\nThe link works for 60 minutes and only once. If this wasn't you, ignore this email - "
               "your password stays the same."),
    },
    "new_device": {
        "de": ("Neue Anmeldung bei Learnova",
               "Hallo {name},\n\nbei deinem Learnova-Konto „{username}“ hat sich gerade jemand auf einem Gerät "
               "angemeldet, das wir noch nicht kannten: {device} ({when}).\n\nWarst du das? Dann ist alles gut. "
               "Wenn nicht, ändere sofort dein Passwort und prüfe unter Einstellungen → Login & Sicherheit, "
               "welche Anmeldewege verbunden sind."),
        "en": ("New sign-in to Learnova",
               "Hi {name},\n\nsomeone just signed in to your Learnova account \"{username}\" on a device we had not "
               "seen before: {device} ({when}).\n\nWas this you? Then all is well. If not, change your password now "
               "and check Settings → Login & Security for the connected sign-in methods."),
    },
    "password_changed": {
        "de": ("Dein Passwort wurde geändert",
               "Hallo {name},\n\ndas Passwort deines Learnova-Kontos „{username}“ wurde gerade geändert ({when}).\n\n"
               "Warst du das nicht? Dann setze es über „Passwort vergessen“ sofort neu und schreib uns."),
        "en": ("Your password was changed",
               "Hi {name},\n\nthe password of your Learnova account \"{username}\" was just changed ({when}).\n\n"
               "Not you? Reset it right away with \"Forgot password\" and write to us."),
    },
    "account_recovered": {
        "de": ("Dein Konto wurde wiederhergestellt",
               "Hallo {name},\n\nfür dein Learnova-Konto „{username}“ wurde über den E-Mail-Link ein neues "
               "Passwort gesetzt ({when}).\n\nWarst du das nicht? Dann schreib uns sofort."),
        "en": ("Your account was recovered",
               "Hi {name},\n\na new password was set for your Learnova account \"{username}\" through the email "
               "link ({when}).\n\nNot you? Write to us right away."),
    },
    "provider_linked": {
        "de": ("Neuer Anmeldeweg verbunden",
               "Hallo {name},\n\nmit deinem Learnova-Konto „{username}“ ist jetzt „{provider}“ als Anmeldeweg "
               "verbunden ({when}).\n\nWarst du das nicht? Trenne ihn unter Einstellungen → Login & Sicherheit "
               "und ändere dein Passwort."),
        "en": ("New sign-in method connected",
               "Hi {name},\n\n\"{provider}\" is now connected as a sign-in method to your Learnova account "
               "\"{username}\" ({when}).\n\nNot you? Disconnect it in Settings → Login & Security and change your password."),
    },
    "provider_unlinked": {
        "de": ("Anmeldeweg getrennt",
               "Hallo {name},\n\n„{provider}“ ist nicht mehr mit deinem Learnova-Konto „{username}“ verbunden "
               "({when}).\n\nWarst du das nicht? Schreib uns sofort."),
        "en": ("Sign-in method disconnected",
               "Hi {name},\n\n\"{provider}\" is no longer connected to your Learnova account \"{username}\" "
               "({when}).\n\nNot you? Write to us right away."),
    },
    "phone_added": {
        "de": ("Telefonnummer hinzugefügt",
               "Hallo {name},\n\nzu deinem Learnova-Konto „{username}“ wurde die Telefonnummer {phone} hinzugefügt "
               "({when}). Warst du das nicht? Entferne sie unter Einstellungen → Login & Sicherheit."),
        "en": ("Phone number added",
               "Hi {name},\n\nthe phone number {phone} was added to your Learnova account \"{username}\" ({when}). "
               "Not you? Remove it in Settings → Login & Security."),
    },
    "phone_changed": {
        "de": ("Telefonnummer geändert",
               "Hallo {name},\n\ndie Telefonnummer deines Learnova-Kontos „{username}“ ist jetzt {phone} ({when}). "
               "Warst du das nicht? Schreib uns sofort."),
        "en": ("Phone number changed",
               "Hi {name},\n\nthe phone number of your Learnova account \"{username}\" is now {phone} ({when}). "
               "Not you? Write to us right away."),
    },
    "phone_removed": {
        "de": ("Telefonnummer entfernt",
               "Hallo {name},\n\ndie Telefonnummer wurde von deinem Learnova-Konto „{username}“ entfernt ({when}). "
               "Warst du das nicht? Schreib uns sofort."),
        "en": ("Phone number removed",
               "Hi {name},\n\nthe phone number was removed from your Learnova account \"{username}\" ({when}). "
               "Not you? Write to us right away."),
    },
}

SMS_TEMPLATES = {
    "code": {"de": "Dein Learnova-Code: {code}\nGilt 5 Minuten. Gib ihn niemandem weiter.",
             "en": "Your Learnova code: {code}\nValid for 5 minutes. Never share it."},
    "welcome_sms": {"de": "Willkommen bei Learnova, {name}! Dein Konto „{username}“ ist bereit.",
                    "en": "Welcome to Learnova, {name}! Your account \"{username}\" is ready."},
    "security": {"de": "Learnova: An deinem Konto „{username}“ wurde gerade etwas geändert ({what}). Warst du das "
                       "nicht? Melde dich und prüfe Einstellungen → Login & Sicherheit.",
                 "en": "Learnova: something just changed on your account \"{username}\" ({what}). Not you? Sign in "
                       "and check Settings → Login & Security."},
}
SMS_WHAT = {
    "de": {"password_changed": "Passwort", "account_recovered": "Passwort zurückgesetzt",
           "provider_linked": "Anmeldeweg verbunden", "provider_unlinked": "Anmeldeweg getrennt",
           "phone_added": "Telefonnummer", "phone_changed": "Telefonnummer", "phone_removed": "Telefonnummer",
           "new_device": "neue Anmeldung"},
    "en": {"password_changed": "password", "account_recovered": "password reset", "provider_linked": "sign-in method "
           "connected", "provider_unlinked": "sign-in method disconnected", "phone_added": "phone number",
           "phone_changed": "phone number", "phone_removed": "phone number", "new_device": "new sign-in"},
}


def render_email(kind: str, language: str, values: Mapping[str, Any]) -> tuple[str, str]:
    """(subject, plain-text body). Missing values render empty, never as "{name}"."""

    lang = _lang(language)
    subject, body = TEMPLATES[kind][lang]
    safe = _Values(values)
    return subject.format_map(safe), body.format_map(safe) + FOOTER[lang]


def render_sms(kind: str, language: str, values: Mapping[str, Any]) -> str:
    lang = _lang(language)
    if kind in SMS_WHAT[lang] and kind not in SMS_TEMPLATES:
        return SMS_TEMPLATES["security"][lang].format_map(_Values({**values, "what": SMS_WHAT[lang][kind]}))
    return SMS_TEMPLATES[kind][lang].format_map(_Values(values))


class _Values(dict):
    def __missing__(self, key: str) -> str:
        return ""


_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("Firefox/", "Firefox"),
             ("CriOS", "Chrome"), ("FxiOS", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
_SYSTEMS = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
            ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux"))


def device_label(user_agent: str) -> str:
    """"Chrome on Android" - browser and system family only; no version, no IP, no location."""

    text = str(user_agent or "")
    browser = next((name for marker, name in _BROWSERS if marker in text), "")
    system = next((name for marker, name in _SYSTEMS if marker in text), "")
    if browser and system:
        return f"{browser} · {system}"
    return browser or system or "unknown device"


_HAS_SECRET = re.compile(r"\b\d{6}\b")


def contains_code_like_number(text: str) -> bool:
    """A test helper and a guard: account emails must never carry a 6-digit code."""

    return bool(_HAS_SECRET.search(str(text or "")))


__all__ = ["CATEGORIES", "PROVIDER_LABELS", "SMS_TEMPLATES", "TEMPLATES", "contains_code_like_number", "device_label",
           "may_send", "render_email", "render_sms"]
