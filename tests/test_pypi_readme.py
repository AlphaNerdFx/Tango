"""
test_pypi_readme.py

The release helper that pins the README's version badges for PyPI.

Worth testing because its failure is silent and lands on a page that cannot
be edited afterwards: if the regexes stop matching after someone edits the
README, --pin reports nothing to do and the upload carries live badges that
will show the wrong version on that release's page forever.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "pypi_readme", _ROOT / "scripts" / "pypi_readme.py")
pypi_readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pypi_readme)


class TestPinning:
    def test_the_pypi_badge_is_pinned_to_the_version(self):
        text = "![PyPI](https://img.shields.io/pypi/v/tango-anki?color=orange&label=pypi)"
        out, changed = pypi_readme.pin(text, "0.11.0")
        assert changed == 1
        assert "badge/pypi-v0.11.0-orange" in out
        assert "shields.io/pypi/v" not in out

    def test_the_release_badge_is_pinned_to_the_version(self):
        text = "![Release](https://img.shields.io/github/v/release/AlphaNerdFx/Tango?color=orange)"
        out, changed = pypi_readme.pin(text, "0.11.0")
        assert changed == 1
        assert "badge/release-v0.11.0-orange" in out
        assert "github/v/release" not in out

    def test_static_badges_are_left_alone(self):
        # Python and licence badges already say a fixed thing, so pinning
        # them would be churn with no reader-visible effect.
        text = ("![Python](https://img.shields.io/badge/python-3.10%2B-blue)\n"
                "![License: MIT](https://img.shields.io/badge/license-MIT-green)")
        out, changed = pypi_readme.pin(text, "0.11.0")
        assert changed == 0
        assert out == text

    def test_the_link_target_survives_pinning(self):
        # Only the image changes. The badge must still link to the project.
        text = ("[![PyPI](https://img.shields.io/pypi/v/tango-anki?color=orange&label=pypi)]"
                "(https://pypi.org/project/tango-anki/)")
        out, _ = pypi_readme.pin(text, "0.11.0")
        assert out.endswith("(https://pypi.org/project/tango-anki/)")


class TestAgainstTheRealReadme:
    """
    The regexes are matched against the README that actually ships. A test
    using only invented strings would keep passing after someone rewrote the
    badge lines, which is the failure this file exists to catch.
    """

    def test_both_live_badges_are_found_in_the_real_readme(self):
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        _, changed = pypi_readme.pin(text, "9.9.9")
        assert changed == 2, (
            "README.md no longer contains both live version badges; "
            "scripts/pypi_readme.py would silently pin nothing"
        )

    def test_pinning_the_real_readme_leaves_no_live_version_badge(self):
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        out, _ = pypi_readme.pin(text, "9.9.9")
        assert "shields.io/pypi/v" not in out
        assert "shields.io/github/v/release" not in out
