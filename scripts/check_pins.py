#!/usr/bin/env python3
"""
Check that the pinned requirement files agree with pyproject.toml.

Why this exists. CI installs the project with `pip install -e ".[dev]"`, which
reads `pyproject.toml` and never looks at `requirements.txt` or
`requirements-dev.txt`. Nothing checked those files, so a bad pin passed every
check and looked ready to merge.

That is not hypothetical. Dependabot raised a pull request bumping thinc to
9.1.1 in `requirements.txt`. CI went green on Python 3.10, 3.11 and 3.12, and
the file it changed could not be installed at all:

    ERROR: ResolutionImpossible
        The user requested thinc==9.1.1
        spacy 3.8.14 depends on thinc<8.4.0 and >=8.3.12

Two different failures are possible and this checks the second; the first is
covered by a `pip install --dry-run` step in CI beside it.

1. The file does not resolve. pip catches that.
2. The file resolves but contradicts `pyproject.toml`. pip cannot catch that,
   because it never sees both. A pin of `spacy==4.1.0` against a declared
   `spacy>=3.8,<4.0` installs happily on its own and means the pinned file and
   the package metadata describe different projects.

Run it by hand with:

    python scripts/check_pins.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]


def _normalise(name: str) -> str:
    """PyPI treats these as the same project, so comparison must too."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def read_pins(path: Path) -> dict[str, str]:
    """
    Map package name to pinned version for every `name==version` line.

    Lines using any other operator are skipped rather than guessed at: a range
    in a pin file is unusual, and a wrong reading is worse than no reading.
    """
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if "==" not in line:
            continue
        name, _, version = line.partition("==")
        if name and version:
            pins[_normalise(name)] = version.strip()
    return pins


def declared_ranges() -> dict[str, Requirement]:
    """Every runtime and optional dependency declared in pyproject.toml."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    raws = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        raws.extend(group)

    ranges: dict[str, Requirement] = {}
    for raw in raws:
        req = Requirement(raw)
        ranges[_normalise(req.name)] = req
    return ranges


def main() -> int:
    ranges = declared_ranges()
    problems: list[str] = []
    checked = 0

    for filename in ("requirements.txt", "requirements-dev.txt"):
        path = ROOT / filename
        if not path.is_file():
            continue
        for name, version in read_pins(path).items():
            req = ranges.get(name)
            if req is None:
                # A transitive dependency pinned for reproducibility. It has no
                # declared range to contradict, so there is nothing to check.
                continue
            checked += 1
            try:
                parsed = Version(version)
            except InvalidVersion:
                problems.append(f"{filename}: {name}=={version} is not a valid version")
                continue
            if not req.specifier.contains(parsed, prereleases=True):
                problems.append(
                    f"{filename}: {name}=={version} is outside pyproject's "
                    f"{req.name}{req.specifier}"
                )

    if problems:
        print("Pinned versions disagree with pyproject.toml:")
        for problem in problems:
            print(f"  {problem}")
        print("\nFix the pin, or widen the range in pyproject.toml if the bump is wanted.")
        return 1

    print(f"{checked} pinned versions checked against pyproject.toml, all inside their ranges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
