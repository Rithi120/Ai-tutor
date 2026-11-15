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

Vouchers (2026-11-14, the bulk system): 28 characters of the same alphabet - 140 random
bits - shown in groups of four,

    LN-PREM-7KQ2-MX9T-AD4H-NWER-1CZP-5G8B-QV3D

and stored only as HMAC-SHA256 under a server-side pepper (`PREMIUM_VOUCHER_PEPPER`), so
the database alone - a backup, a leaked dump - cannot be searched for a code even by
someone who generates candidates. The three formats never collide: a voucher body is 28
characters, a v2 code 20, and a legacy key is matched as its exact string.
"""

from __future__ import annotations

import hashlib
import hmac
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
VOUCHER_BODY_LENGTH = 28          # 28 x 5 bits = 140 bits from `secrets`
VOUCHER_GROUP = 4
VOUCHER_PREFIX = "LN-PREM"
_VOUCHER_DOMAIN = b"learnova-voucher:v3:"
MIN_PEPPER_LENGTH = 32


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


def generate_voucher_code() -> str:
    """A fresh bulk voucher: 140 bits from the operating system's CSPRNG."""

    body = "".join(secrets.choice(ALPHABET) for _ in range(VOUCHER_BODY_LENGTH))
    groups = [body[index:index + VOUCHER_GROUP] for index in range(0, VOUCHER_BODY_LENGTH, VOUCHER_GROUP)]
    return "-".join([VOUCHER_PREFIX, *groups])


def normalize_voucher(raw: object) -> str | None:
    """The 28-character body of a voucher as typed (prefix, case, spaces, dashes and the
    look-alikes O/0, I/L/1 do not matter), or None when it cannot be one."""

    if not isinstance(raw, str) or len(raw) > MAX_INPUT_LENGTH:
        return None
    compact = "".join(raw.split()).replace("-", "").replace("_", "").upper()
    prefix = VOUCHER_PREFIX.replace("-", "")
    if len(compact) == VOUCHER_BODY_LENGTH + len(prefix) and compact.startswith(prefix):
        compact = compact[len(prefix):]
    compact = compact.translate(_READ_AS)
    if len(compact) != VOUCHER_BODY_LENGTH or any(char not in ALPHABET for char in compact):
        return None
    return compact


def voucher_hash(normalized: str, pepper: str) -> str:
    """HMAC-SHA256 of the voucher body under the server's pepper (hex)."""

    if not pepper or len(pepper) < MIN_PEPPER_LENGTH:
        raise ValueError("PREMIUM_VOUCHER_PEPPER is missing or too short")
    return hmac.new(pepper.encode("utf-8"), _VOUCHER_DOMAIN + normalized.encode("ascii"), hashlib.sha256).hexdigest()


def code_hash(normalized: str) -> str:
    return hashlib.sha256((_HASH_DOMAIN + normalized).encode("ascii")).hexdigest()


def code_hint(normalized: str) -> str:
    """The last four characters: enough to tell codes apart, nowhere near enough to use one."""

    return normalized[-4:]


def candidate_hashes(raw: object, pepper: str = "") -> list[str]:
    """Every stored hash this input could match: a voucher (with the pepper), a v2 code, then a
    legacy key."""

    hashes: list[str] = []
    voucher = normalize_voucher(raw)
    if voucher and pepper and len(pepper) >= MIN_PEPPER_LENGTH:
        hashes.append(voucher_hash(voucher, pepper))
    normalized = normalize_code(raw)
    if normalized:
        hashes.append(code_hash(normalized))
    if isinstance(raw, str) and len(raw) <= MAX_INPUT_LENGTH:
        stripped = raw.strip()
        if stripped.startswith(LEGACY_PREFIX):
            hashes.append(hashlib.sha256(stripped.encode("utf-8")).hexdigest())
    return hashes
