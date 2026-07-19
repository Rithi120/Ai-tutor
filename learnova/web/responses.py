"""Consistent response envelopes that retain the legacy ``error`` field."""

from __future__ import annotations

from flask import jsonify


def api_error(message: str, status: int, code: str = "request_failed", **details):
    payload = {"ok": False, "error": message, "code": code}
    if details:
        payload["details"] = details
    return jsonify(payload), status


def api_success(status: int = 200, **data):
    return jsonify(ok=True, **data), status
