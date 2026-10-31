"""Small typed helpers for tests: lookups that must find what they look for.

`session.get()` and `re.search()` return None when nothing matches. A test that then
reads an attribute fails with an AttributeError far from the cause, and the type checker
rightly flags every such line. These fail at the lookup, with a message saying what was
missing. No app import here, so a test module's environment set-up order is untouched.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

T = TypeVar("T")


def fetch(session: Any, model: type[T], ident: Any) -> T:
    """`session.get(model, ident)` for a row the test expects to exist."""

    row = session.get(model, ident)
    assert row is not None, f"{model.__name__} {ident!r} does not exist"
    return row


def present(value: T | None, what: str = "value") -> T:
    """`value`, asserting that it is there."""

    assert value is not None, f"expected {what}, found None"
    return value


def match_group(pattern: str, text: str, group: int = 1) -> str:
    """The `group` of the first match of `pattern` in `text`, asserting there is one."""

    found = re.search(pattern, text)
    assert found is not None, f"{pattern!r} not found"
    return found.group(group)
