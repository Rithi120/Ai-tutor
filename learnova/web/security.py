"""Security headers and request-level security policy.

The Content Security Policy is built per response, around a nonce that changes on
every request:

- Scripts run only when they carry the nonce. The earlier policy allowed
  'unsafe-inline' scripts, so any injected inline script ran; a nonce does not.
  'strict-dynamic' lets a nonced script load further scripts (KaTeX, and Google's ad
  code, which loads its own helpers from domains Google says change over time).
  'self' and https://cdn.jsdelivr.net remain only as the fallback a CSP2-only browser
  reads, because CSP3 browsers ignore host sources next to 'strict-dynamic'.
- Pages that carry Google AdSense get, on that response only, what Google documents
  AdSense as requiring (support.google.com/adsense/answer/16283098):
  * script-src https: - the fallback for CSP2-only browsers, which cannot apply
    'strict-dynamic' (CSP3 browsers ignore it next to 'strict-dynamic');
  * 'unsafe-eval' - part of Google's only supported policy ("More restrictive policies
    may break without notice"); switchable with ADSENSE_CSP_UNSAFE_EVAL;
  * frame-src, img-src and connect-src https: - ad iframes, the consent message,
    pixels and ad requests come from Google and partner domains for which Google
    publishes no allowlist. They govern loading only; script execution stays nonce-bound.
  Not copied from Google's example: 'unsafe-inline' (ignored by every browser that
  supports nonces) and http: (blocked as mixed content on an HTTPS page anyway).
- Every other directive is unchanged, ad page or not.
"""

from __future__ import annotations

import secrets

from flask import current_app, g, request

_BASE = (
    ("default-src", "'self'"),
    ("base-uri", "'self'"),
    ("form-action", "'self'"),
    ("frame-ancestors", "'none'"),
    ("object-src", "'none'"),
    ("img-src", "'self' data: blob: https://upload.wikimedia.org"),
    ("font-src", "'self' https://fonts.gstatic.com https://cdn.jsdelivr.net"),
    ("style-src", "'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net"),
    ("script-src", "'nonce-{nonce}' 'strict-dynamic' 'self' https://cdn.jsdelivr.net"),
    ("connect-src", "'self'"),
    ("media-src", "'self' blob:"),
)
_AD_ADDITIONS = {
    "script-src": "https:",
    "frame-src": "'self' https:",
    "img-src": "https:",
    "connect-src": "https:",
}


def csp_nonce() -> str:
    """This request's script nonce, created on first use. Templates call it per tag."""

    nonce = g.get("csp_nonce")
    if not nonce:
        nonce = secrets.token_urlsafe(18)
        g.csp_nonce = nonce
    return nonce


def content_security_policy(nonce: str, *, ads: bool = False, unsafe_eval: bool = True,
                            report_uri: str = "") -> str:
    directives = {name: value.format(nonce=nonce) for name, value in _BASE}
    if ads:
        for name, extra in _AD_ADDITIONS.items():
            directives[name] = f"{directives[name]} {extra}" if name in directives else extra
        if unsafe_eval:
            directives["script-src"] += " 'unsafe-eval'"
    policy = "; ".join(f"{name} {value}" for name, value in directives.items())
    if report_uri:
        policy += f"; report-uri {report_uri}"
    return policy


def apply_security_headers(response):
    ads = bool(g.get("ads_on_page"))
    response.headers.setdefault("Content-Security-Policy", content_security_policy(
        csp_nonce(), ads=ads,
        unsafe_eval=bool(current_app.config.get("ADSENSE_CSP_UNSAFE_EVAL", True)),
        report_uri=str(current_app.config.get("CSP_REPORT_URI") or "")))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Google's consent message cannot show unless the Referer reaches it cross-origin
    # (strict-origin-when-cross-origin sends the origin only, never a path). Pages
    # without ads keep same-origin.
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin" if ads else "same-origin")
    # The microphone is Learnova's own speech input (SpeechRecognition needs this feature;
    # microphone=() refused it on every page). Same-origin only, like the camera: no
    # embedded third-party frame - an ad included - can use either.
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if current_app.config.get("ENV_NAME") == "production" and request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
