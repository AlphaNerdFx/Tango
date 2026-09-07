#!/usr/bin/env python3
"""
Pin the README's version badges before a PyPI upload, and restore after.

    python scripts/pypi_readme.py --pin       # before python -m build
    python scripts/pypi_readme.py --restore   # after
    python scripts/pypi_readme.py --check     # show what --pin would do

Why this exists.

PyPI freezes a release's description at upload time, but a badge is not
text: it is an `<img>` the reader's browser fetches when the page opens. The
two version badges in README.md ask shields.io for *the current* version, so
they answer with today's number on every release page ever published. The
page for v0.8.2 shows a v0.10.0 badge, which reads as though the page is
mislabelled.

CLAUDE.md 18.2 says to hardcode nothing that has a live source, and pinning
a badge is exactly that. The exception is narrow and is the whole point
here: the page it lands on is itself frozen, so a live badge on it is not
fresh, it is wrong. The GitHub README keeps the live badges, because that
page really does track the current state.

So README.md on disk and on GitHub stays live, and only the copy uploaded to
PyPI carries pinned badges. --restore puts the file back, and the backup is
written beside it rather than relying on git, so an interrupted release
cannot lose the original.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
BACKUP = ROOT / "README.md.orig"
REPO = "https://github.com/AlphaNerdFx/Tango"

sys.path.insert(0, str(ROOT / "src"))

# The two badges that answer with "now" rather than with this release.
_LIVE_BADGES = (
    (re.compile(r"!\[PyPI\]\(https://img\.shields\.io/pypi/v/tango-anki[^)]*\)"),
     "![PyPI](https://img.shields.io/badge/pypi-v{v}-orange)"),
    # The link goes with the image here. A badge reading "release v0.8.2"
    # that opens the latest release is the same lie in a different place,
    # and on a frozen page /releases/latest is never this release for long.
    (re.compile(r"\[!\[Release\]\(https://img\.shields\.io/github/v/release/[^)]*\)\]"
                r"\([^)]*\)"),
     "[![Release](https://img.shields.io/badge/release-v{v}-orange)]"
     "(" + REPO + "/releases/tag/v{v})"),
)

# The CI badge cannot be pinned, only dropped, and that is a property of the
# badge rather than a decision taken here. GitHub renders it from the
# workflow's *current* state and offers no way to ask for the state at a tag:
# badge.svg takes ?branch= and ?event=, and a tag is neither. On a frozen page
# it therefore reports whatever main is doing months later, so the v0.8.2 page
# would turn red the next time someone breaks a build. Saying nothing is
# better than saying something that is only accidentally true.
_CI_BADGE = re.compile(r"^\[!\[CI\]\([^)]*\)\]\([^)]*\)\n", re.M)

# A README written for GitHub links to files by repository-relative path, and
# those resolve against pypi.org once uploaded: `](LICENSE)` becomes a 404 on
# the project page. Rewritten to point into the repository *at this tag*, so a
# frozen page links to the files as they were when it was frozen.
#
# Anchors are left alone, they work on the PyPI page like anywhere else.
_RELATIVE_LINK = re.compile(r"\]\((?!https?://|#)([^)]+)\)")


def version() -> str:
    """The version being released, from the one place it lives."""
    from pipeline import __version__
    return __version__


def pin(text: str, v: str) -> tuple[str, int]:
    """
    Return the text ready for a frozen page, and how many things changed.

    Three edits, all of the same kind: anything that answers with "now" is
    replaced by what it says at this release, and anything that resolves
    against the wrong host is made absolute.
    """
    changed = 0
    for pattern, replacement in _LIVE_BADGES:
        text, n = pattern.subn(replacement.format(v=v), text)
        changed += n
    text, n = _CI_BADGE.subn("", text)
    changed += n
    text, n = _RELATIVE_LINK.subn(rf"]({REPO}/blob/v{v}/\1)", text)
    changed += n
    return text, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pin", action="store_true", help="rewrite README.md with pinned badges")
    group.add_argument("--restore", action="store_true", help="put README.md back")
    group.add_argument("--check", action="store_true", help="report without writing")
    args = parser.parse_args()

    if args.restore:
        if not BACKUP.exists():
            print("Nothing to restore: no README.md.orig.")
            return 1
        README.write_text(BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
        BACKUP.unlink()
        print("README.md restored, backup removed.")
        return 0

    original = README.read_text(encoding="utf-8")
    v = version()
    pinned, changed = pin(original, v)

    if args.check:
        print(f"Version {v}. {changed} badge(s) would be pinned.")
        for line in pinned.splitlines():
            if "shields.io" in line:
                print(f"  {line}")
        return 0 if changed else 1

    if BACKUP.exists():
        print("README.md.orig already exists; restore first.", file=sys.stderr)
        return 1
    if not changed:
        print("No live version badges found; nothing pinned.", file=sys.stderr)
        return 1

    BACKUP.write_text(original, encoding="utf-8")
    README.write_text(pinned, encoding="utf-8")
    print(f"Pinned {changed} badge(s) to v{v}. Build now, then --restore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
