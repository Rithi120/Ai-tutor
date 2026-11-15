"""Sign in with Google and Sign in with Apple: OpenID Connect, authorization code flow.

Flask-free. The application keeps the request state (state, nonce, PKCE verifier) on the
server and calls these functions; nothing here touches a session or a database.

* `authorization_url` - the provider's consent page, with state, nonce and (Google) PKCE S256.
* `exchange_code`     - code -> tokens at the provider's token endpoint (server to server).
* `verify_id_token`   - signature against the provider's published JWKS, issuer, audience,
  expiry, issued-at and nonce. Only the verified claims leave this module.
* `apple_client_secret` - Apple's client secret is a short ES256 JWT signed with the team's key.

Access and refresh tokens are never returned to the caller: Learnova only needs the
identity (sub, email, email_verified, name).

PyJWT (with its cryptography backend) is imported lazily, so an interpreter without it can
still run the app with these providers switched off (`available()` says so).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

PROVIDERS = ("google", "apple")
SCOPES = "openid email profile"
APPLE_SCOPES = "name email"
CLOCK_SKEW_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class Provider:
    name: str
    authorize_url: str
    token_url: str
    jwks_url: str
    issuers: tuple[str, ...]
    pkce: bool


GOOGLE = Provider("google", "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
                  "https://www.googleapis.com/oauth2/v3/certs", ("https://accounts.google.com", "accounts.google.com"),
                  pkce=True)
# Apple documents no PKCE for the web flow; state and nonce protect it.
APPLE = Provider("apple", "https://appleid.apple.com/auth/authorize", "https://appleid.apple.com/auth/token",
                 "https://appleid.apple.com/auth/keys", ("https://appleid.apple.com",), pkce=False)
BY_NAME = {"google": GOOGLE, "apple": APPLE}


class OIDCError(Exception):
    """A provider answer that cannot be trusted. `code` is safe to log; never the token."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str
    email: str = ""
    email_verified: bool = False
    private_relay: bool = False
    given_name: str = ""
    name: str = ""
    raw_claims: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


def available() -> bool:
    try:
        import jwt  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: F401
    except ImportError:
        return False
    return True


def configured(config: Mapping[str, Any], provider: str) -> bool:
    """Whether the owner set this provider up (credentials present, library installed)."""

    if not available():
        return False
    if provider == "google":
        return bool(str(config.get("GOOGLE_CLIENT_ID") or "").strip() and str(config.get("GOOGLE_CLIENT_SECRET") or "").strip())
    if provider == "apple":
        return all(str(config.get(name) or "").strip() for name in
                   ("APPLE_CLIENT_ID", "APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY"))
    return False


def client_id(config: Mapping[str, Any], provider: str) -> str:
    return str(config.get("GOOGLE_CLIENT_ID" if provider == "google" else "APPLE_CLIENT_ID") or "").strip()


# --------------------------------------------------------------------------- request
def random_token(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(provider: str, *, client: str, redirect_uri: str, state: str, nonce: str,
                      code_verifier: str = "", login_hint: str = "") -> str:
    spec = BY_NAME[provider]
    params: dict[str, str] = {"client_id": client, "redirect_uri": redirect_uri, "response_type": "code",
                              "state": state, "nonce": nonce}
    if provider == "google":
        params.update(scope=SCOPES, prompt="select_account")
        if login_hint:
            params["login_hint"] = login_hint
    else:
        # Apple sends name and email only with response_mode=form_post (a cross-site POST).
        params.update(scope=APPLE_SCOPES, response_mode="form_post")
    if spec.pkce and code_verifier:
        params.update(code_challenge=pkce_challenge(code_verifier), code_challenge_method="S256")
    return f"{spec.authorize_url}?{urlencode(params)}"


# --------------------------------------------------------------------------- apple secret
def apple_client_secret(config: Mapping[str, Any], now: float | None = None) -> str:
    """The ES256 client secret Apple wants at its token endpoint (valid 5 minutes)."""

    import jwt

    issued = int(now if now is not None else time.time())
    key = str(config.get("APPLE_PRIVATE_KEY") or "").replace("\\n", "\n").strip()
    return jwt.encode(
        {"iss": str(config["APPLE_TEAM_ID"]).strip(), "iat": issued, "exp": issued + 300,
         "aud": "https://appleid.apple.com", "sub": client_id(config, "apple")},
        key, algorithm="ES256", headers={"kid": str(config["APPLE_KEY_ID"]).strip()})


# --------------------------------------------------------------------------- exchange
def exchange_code(provider: str, config: Mapping[str, Any], *, code: str, redirect_uri: str,
                  code_verifier: str = "", post: Callable[..., Any] | None = None) -> str:
    """Trade the authorization code for tokens; return only the ID token."""

    if post is None:
        import requests
        post = requests.post
    spec = BY_NAME[provider]
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id(config, provider)}
    if provider == "google":
        data["client_secret"] = str(config.get("GOOGLE_CLIENT_SECRET") or "")
        if code_verifier:
            data["code_verifier"] = code_verifier
    else:
        data["client_secret"] = apple_client_secret(config)
    try:
        response = post(spec.token_url, data=data, headers={"Accept": "application/json"},
                        timeout=HTTP_TIMEOUT_SECONDS)
    except Exception as error:  # noqa: BLE001 - network: the student may try again
        raise OIDCError("token_endpoint_unreachable") from error
    if getattr(response, "status_code", 0) != 200:
        raise OIDCError(f"token_endpoint_{getattr(response, 'status_code', 0)}")
    try:
        payload = response.json()
    except ValueError as error:
        raise OIDCError("token_endpoint_not_json") from error
    token = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise OIDCError("no_id_token")
    return token


# --------------------------------------------------------------------------- verify
_JWKS_CLIENTS: dict[str, Any] = {}


def signing_key(provider: str, token: str) -> Any:
    """The provider's public key for this token's `kid` (JWKS fetched and cached by PyJWT)."""

    import jwt

    url = BY_NAME[provider].jwks_url
    client = _JWKS_CLIENTS.get(url)
    if client is None:
        client = _JWKS_CLIENTS[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        return client.get_signing_key_from_jwt(token).key
    except Exception as error:  # noqa: BLE001 - unknown kid, unreachable JWKS
        raise OIDCError("signing_key_unavailable") from error


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def verify_id_token(provider: str, token: str, *, audience: str, nonce: str,
                    key_resolver: Callable[[str, str], Any] | None = None, now: float | None = None) -> Identity:
    """Check everything an ID token promises; return the identity or raise OIDCError."""

    import jwt

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise OIDCError("malformed_token") from error
    if header.get("alg") != "RS256":
        # Both providers sign with RS256; "none" or an HMAC algorithm is an attack.
        raise OIDCError("unexpected_algorithm")
    key = (key_resolver or signing_key)(provider, token)
    spec = BY_NAME[provider]
    try:
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=audience, issuer=list(spec.issuers),
            leeway=CLOCK_SKEW_SECONDS, options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        ) if now is None else _decode_at(jwt, token, key, audience, spec, now)
    except jwt.ExpiredSignatureError as error:
        raise OIDCError("token_expired") from error
    except jwt.InvalidAudienceError as error:
        raise OIDCError("wrong_audience") from error
    except jwt.InvalidIssuerError as error:
        raise OIDCError("wrong_issuer") from error
    except jwt.PyJWTError as error:
        raise OIDCError("invalid_token") from error
    if not nonce or not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise OIDCError("nonce_mismatch")
    subject = str(claims.get("sub") or "")
    if not subject or len(subject) > 255:
        raise OIDCError("no_subject")
    email = str(claims.get("email") or "").strip().lower()[:255]
    return Identity(
        provider=provider, subject=subject, email=email, email_verified=bool(email) and _truthy(claims.get("email_verified")),
        private_relay=_truthy(claims.get("is_private_email")) or email.endswith("@privaterelay.appleid.com"),
        given_name=str(claims.get("given_name") or "").strip()[:60], name=str(claims.get("name") or "").strip()[:120],
        raw_claims=claims)


def _decode_at(jwt: Any, token: str, key: Any, audience: str, spec: Provider, now: float) -> dict[str, Any]:
    """jwt.decode with a fixed clock (tests): expiry checked against `now`, not the wall clock."""

    claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=list(spec.issuers),
                        options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_exp": False, "verify_iat": False})
    if float(claims["exp"]) + CLOCK_SKEW_SECONDS < now:
        raise jwt.ExpiredSignatureError("expired")
    return claims


def apple_user_name(raw: str | None) -> str:
    """Apple's `user` form field (first authorization only): {"name": {"firstName", "lastName"}}."""

    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return ""
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, dict):
        return ""
    return str(name.get("firstName") or "").strip()[:60]


__all__ = ["APPLE", "GOOGLE", "PROVIDERS", "Identity", "OIDCError", "apple_client_secret", "apple_user_name",
           "authorization_url", "available", "client_id", "configured", "exchange_code", "pkce_challenge",
           "random_token", "verify_id_token"]
