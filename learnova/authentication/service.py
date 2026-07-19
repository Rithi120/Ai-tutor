"""Authentication validation and persistence, independent of Flask routes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError


USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,29}")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class RegistrationInput:
    username: str
    email: str
    password: str
    language: str


class AccountConflict(Exception):
    """Raised when a unique account identity already exists."""


def normalize_registration(username: str, email: str, password: str, language: str) -> RegistrationInput:
    return RegistrationInput(
        username=username.strip().casefold(),
        email=email.strip().lower()[:255],
        password=password,
        language=language,
    )


def validate_registration(data: RegistrationInput, supported_languages: set[str] | tuple[str, ...]) -> str | None:
    if not USERNAME_PATTERN.fullmatch(data.username):
        return "username"
    if data.language not in supported_languages:
        return "language"
    if not EMAIL_PATTERN.fullmatch(data.email):
        return "email"
    if not 8 <= len(data.password) <= 256:
        return "password"
    return None


def identity_conflict(database, user_model, data: RegistrationInput) -> str | None:
    if database.session.scalar(database.select(user_model).where(user_model.username == data.username)):
        return "username_taken"
    if database.session.scalar(database.select(user_model).where(user_model.email == data.email)):
        return "email_taken"
    return None


def create_user(database, user_model, data: RegistrationInput):
    user = user_model(
        username=data.username,
        email=data.email,
        preferred_language=data.language,
    )
    user.set_password(data.password)
    database.session.add(user)
    try:
        database.session.commit()
    except IntegrityError as error:
        database.session.rollback()
        raise AccountConflict from error
    return user


def authenticate(database, user_model, identifier: str, password: str):
    normalized = identifier.strip().casefold()[:255]
    user = database.session.scalar(
        database.select(user_model).where(
            (user_model.email == normalized) | (user_model.username == normalized)
        )
    )
    return user if user and user.check_password(password) else None
