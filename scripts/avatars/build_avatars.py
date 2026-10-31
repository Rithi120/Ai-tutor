"""Rebuild the 20 Learnova avatar SVGs from their drawing code.

    python scripts/avatars/build_avatars.py            # check: compare with static/avatars, write nothing
    python scripts/avatars/build_avatars.py --write    # write only files that differ (lists them first)
    python scripts/avatars/build_avatars.py --out DIR  # write the whole set into another directory
    python scripts/avatars/build_avatars.py --write explorer founder   # only these characters

The drawing code (lib.py for the shared face, chars1-4.py for each character's costume
and background) is deterministic: every random choice uses a fixed seed, so a rebuild
reproduces the committed files byte for byte. The default mode is a check so that
running the script never replaces the committed illustrations by accident; --write is
needed to change static/avatars, and it never touches a file outside the 20 known ids.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_OUT = ROOT / "static" / "avatars"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from learnova.gamification.avatars import AVATAR_BY_ID  # noqa: E402

FORBIDDEN = ("<script", "foreignobject", "<image", "javascript:", "@import", "<text")


def build() -> dict[str, str]:
    """Every avatar id -> its SVG text, validated."""

    drawn: dict[str, str] = {}
    for module_name in ("chars1", "chars2", "chars3", "chars4"):
        module = importlib.import_module(module_name)
        for make in getattr(module, "CHARS" + module_name[-1]):
            avatar = make()
            text = avatar.svg()
            root = ElementTree.fromstring(text)
            ids = [element.get("id") for element in root.iter() if element.get("id")]
            assert all(identifier.startswith(avatar.id + "-") for identifier in ids), avatar.id
            assert len(ids) == len(set(ids)), f"duplicate ids in {avatar.id}"
            assert not any(token in text.lower() for token in FORBIDDEN), avatar.id
            drawn[avatar.id] = text
    missing = set(AVATAR_BY_ID) - set(drawn)
    unknown = set(drawn) - set(AVATAR_BY_ID)
    assert not missing and not unknown, (missing, unknown)
    return drawn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", help="only these avatar ids")
    parser.add_argument("--write", action="store_true", help="write changed files into the output directory")
    parser.add_argument("--out", type=Path, default=None, help="write the set here instead of static/avatars")
    args = parser.parse_args(argv)

    drawn = build()
    wanted = args.ids or sorted(drawn)
    unknown = [identifier for identifier in wanted if identifier not in drawn]
    if unknown:
        parser.error(f"unknown avatar id(s): {', '.join(unknown)}")

    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        for identifier in wanted:
            (args.out / f"{identifier}.svg").write_bytes(drawn[identifier].encode("utf-8"))
        print(f"Wrote {len(wanted)} avatar(s) to {args.out}")
        return 0

    changed = []
    for identifier in wanted:
        path = DEFAULT_OUT / f"{identifier}.svg"
        current = path.read_bytes() if path.exists() else None
        if current != drawn[identifier].encode("utf-8"):
            changed.append(identifier)
    if not changed:
        print(f"All {len(wanted)} avatar(s) match static/avatars.")
        return 0
    print("Differs from static/avatars: " + ", ".join(changed))
    if not args.write:
        print("Nothing written. Re-run with --write to replace these files.")
        return 1
    for identifier in changed:
        (DEFAULT_OUT / f"{identifier}.svg").write_bytes(drawn[identifier].encode("utf-8"))
    print(f"Wrote {len(changed)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
