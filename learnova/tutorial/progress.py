"""Pure rules for the stored tutorial progress (table tutorial_progress, app.TutorialProgress).

One row per (student, section) - the quick tour or one feature chapter: the id of the step
reached (an id, not an index, so it survives a flag that adds or removes steps), the status
and `completed_at` (set the first time and kept on every later run). No row = not seen.

  in_progress  started, not finished (the tab was closed, the student went elsewhere);
               a chapter in this state may start by itself again, resumed at its step
  skipped      Skip / Exit / Escape: it never starts by itself again
  completed    Finish or Mark as done

A write never makes a section "less done": in_progress never overwrites skipped or
completed, and skipped never overwrites completed (merge_status). Only a reset (which
deletes the row) starts a section from nothing. Rows of retired section ids and the
reserved "_auto_tips" row are not sections and are ignored here.

Nothing here touches the database: app.py reads the rows and hands them in, which keeps
these rules testable on their own and the same for every caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

STATUSES = ("in_progress", "skipped", "completed")
RANK = {"in_progress": 0, "skipped": 1, "completed": 2}
ALL = "all"


def _get(row: Any, name: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def validate_update(body: Any, visible: Mapping[str, Sequence[str]]) -> tuple[str, str, str] | str:
    """(section, step, status) for a valid POST /api/tutorial/progress body, else an error code.

    Only what this student can see is accepted: a section hidden by a flag or a plan is
    `unknown_section`, a step that is not one of that section's visible steps `unknown_step`.
    "completed" is accepted only with the section's last visible step (what Finish and
    Mark as done send), so no other step can claim the whole section.
    """

    if not isinstance(body, dict):
        return "bad_request"
    section = body.get("section")
    step = body.get("step")
    status = body.get("status", "in_progress")
    if not isinstance(section, str) or not isinstance(step, str) or not isinstance(status, str):
        return "bad_request"
    if section not in visible:
        return "unknown_section"
    if step not in visible[section]:
        return "unknown_step"
    if status not in STATUSES:
        return "bad_status"
    if status == "completed" and step != visible[section][-1]:
        return "bad_status"
    return section, step, status


def merge_status(stored: str | None, new: str) -> str:
    """The status a row keeps after a write: never less done than before."""

    if stored is not None and RANK.get(stored, -1) > RANK[new]:
        return stored
    return new


def validate_reset(body: Any, visible: Mapping[str, Sequence[str]]) -> str | tuple[str]:
    """(section,) or ("all",) for a valid POST /api/tutorial/reset body, else an error code."""

    if not isinstance(body, dict) or not isinstance(body.get("section"), str):
        return "bad_request"
    section = body["section"]
    if section != ALL and section not in visible:
        return "unknown_section"
    return (section,)


def resume_step(visible_ids: Sequence[str], stored: str, order: Mapping[str, int]) -> str:
    """Where a run continues: the stored step; if a flag hid it since, the first visible
    step that comes after it in the registry; otherwise the first step."""

    if stored in visible_ids:
        return stored
    if stored in order:
        rank = order[stored]
        for step_id in visible_ids:
            if order.get(step_id, -1) > rank:
                return step_id
    return visible_ids[0] if visible_ids else ""


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def section_state(visible_ids: Sequence[str], row: Any, order: Mapping[str, int]) -> dict[str, Any]:
    """The hub's view of one section: status (not_started | in_progress | skipped |
    completed), the resume step and "step n of N" for an unfinished one."""

    total = len(visible_ids)
    status = _get(row, "status")
    if row is None or status not in STATUSES:
        return {"status": "not_started", "step": "", "current": 0, "total": total, "completed_at": None}
    if status == "completed":
        return {"status": "completed", "step": "", "current": total, "total": total,
                "completed_at": _iso(_get(row, "completed_at"))}
    if status == "skipped":
        return {"status": "skipped", "step": "", "current": 0, "total": total, "completed_at": None}
    step = resume_step(visible_ids, str(_get(row, "step", "") or ""), order)
    position = list(visible_ids).index(step) if step in visible_ids else 0
    return {"status": "in_progress", "step": step, "current": position + 1, "total": total, "completed_at": None}


def fraction(visible_ids: Sequence[str], row: Any, order: Mapping[str, int]) -> float:
    """Share of a section done: 1 when completed, the steps before the resume step over all
    steps while in progress, 0 when skipped or not seen."""

    if row is None or not visible_ids:
        return 0.0
    status = _get(row, "status")
    if status == "completed":
        return 1.0
    if status != "in_progress":
        return 0.0
    step = resume_step(visible_ids, str(_get(row, "step", "") or ""), order)
    if step not in visible_ids:
        return 0.0
    return list(visible_ids).index(step) / len(visible_ids)


def percent(visible: Mapping[str, Sequence[str]], rows: Mapping[str, Any],
            orders: Mapping[str, Mapping[str, int]] | None = None) -> int:
    """round(100 x the mean fraction over the visible sections); 0 with no sections."""

    if not visible:
        return 0
    orders = orders or {}
    values = [fraction(ids, rows.get(section_id), orders.get(section_id, {})) for section_id, ids in visible.items()]
    return int(round(100 * sum(values) / len(values)))
