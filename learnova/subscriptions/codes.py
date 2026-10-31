"""Premium activation codes: generation, normalisation and hashing.

A code is 20 characters of Crockford Base32 (100 random bits from `secrets`), shown in
groups of five behind an `LN` prefix:

    LN-7KQ2M-X9TAD-4HNWE-R1CZP

Crockford's alphabet has no I, L, O or U, so a code read aloud or copied from paper
cannot be mistyped into another valid code: O is read as 0 and I/L as 1, case and the
dashes do not matter. That is what a student typing on a phone needs.

Only a SHA-256 hash of the normalised code is stored. A slow password hash would add
nothing here: brute-forcing 2^100 possibilities is out of reach whatever the hash
costs, and a fast hash keeps the lookup a single indexed equality. The plaintext exists
in exactly one place - the administrator's screen at issuance.

Keys issued before this format (`LN-PREM-<token_urlsafe>`) are still accepted: their
hash is SHA-256 of the exact string, so they are matched case-sensitively as before.
"""

from __future__ import annotations

import hashlib
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
BODY_LENGTH = 20
GROUP = 5
PREFIX = "LN"
LEGACY_PREFIX = "LN-PREM-"
# Generous for pasted input with spaces; anything longer is rejected before hashing.
MAX_INPUT_LENGTH = 120
_HASH_DOMAIN = "learnova-activation-code:v2:"
_READ_AS = str.maketrans({"O": "0", "I": "1", "L": "1"})


def generate_code() -> str:
    """A fresh code from the operating system's CSPRNG, formatted for reading aloud."""

    body = "".join(secrets.choice(ALPHABET) for _ in range(BODY_LENGTH))
    groups = [body[index:index + GROUP] for index in range(0, BODY_LENGTH, GROUP)]
    return "-".join([PREFIX, *groups])


def normalize_code(raw: object) -> str | None:
    """The 20-character body of a code as typed, or None when it cannot be one."""

    if not isinstance(raw, str) or len(raw) > MAX_INPUT_LENGTH:
        return None
    compact = "".join(raw.split()).replace("-", "").upper()
    # The prefix is recognised by length, so a body that happens to start with "LN"
    # is never mistaken for a prefixed code.
    if len(compact) == BODY_LENGTH + len(PREFIX) and compact.startswith(PREFIX):
        compact = compact[len(PREFIX):]
    compact = compact.translate(_READ_AS)
    if len(compact) != BODY_LENGTH or any(char not in ALPHABET for char in compact):
        return None
    return compact


def code_hash(normalized: str) -> str:
    return hashlib.sha256((_HASH_DOMAIN + normalized).encode("ascii")).hexdigest()


def code_hint(normalized: str) -> str:
    """The last four characters: enough to tell codes apart, nowhere near enough to use one."""

    return normalized[-4:]


def candidate_hashes(raw: object) -> list[str]:
    """Every stored hash this input could match: the current format, then a legacy key."""

    hashes: list[str] = []
    normalized = normalize_code(raw)
    if normalized:
        hashes.append(code_hash(normalized))
    if isinstance(raw, str) and len(raw) <= MAX_INPUT_LENGTH:
        stripped = raw.strip()
        if stripped.startswith(LEGACY_PREFIX):
            hashes.append(hashlib.sha256(stripped.encode("utf-8")).hexdigest())
    return hashes
