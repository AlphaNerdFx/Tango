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
        text = ("[![Release](https://img.shields.io/github/v/release/AlphaNerdFx/Tango"
                "?color=orange)](https://github.com/AlphaNerdFx/Tango/releases/latest)")
        out, changed = pypi_readme.pin(text, "0.11.0")
        assert changed == 1
        assert "badge/release-v0.11.0-orange" in out
        assert "github/v/release" not in out

    def test_the_release_badge_links_to_its_own_release(self):
        # A badge reading "release v0.8.2" that opens whatever is newest is
        # the same lie in a different place, and /releases/latest stops
        # being this release the moment there is another one.
        text = ("[![Release](https://img.shields.io/github/v/release/AlphaNerdFx/Tango"
                "?color=orange)](https://github.com/AlphaNerdFx/Tango/releases/latest)")
        out, _ = pypi_readme.pin(text, "0.11.0")
        assert out.endswith("/releases/tag/v0.11.0)")

    def test_the_ci_badge_is_dropped(self):
        # It cannot be pinned. GitHub renders it from the workflow's current
        # state and badge.svg has no way to ask for a tag, so on a frozen
        # page it reports whatever main is doing today.
        text = ("[![CI](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml/"
                "badge.svg)](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml)\n"
                "[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://x/)\n")
        out, changed = pypi_readme.pin(text, "0.11.0")
        assert changed == 1
        assert "actions/workflows" not in out
        assert "python-3.10" in out, "only the CI line may go"

    def test_a_relative_link_becomes_an_absolute_one_at_this_tag(self):
        # `](LICENSE)` resolves against pypi.org once uploaded, which is a
        # 404 on every release page ever published.
        out, changed = pypi_readme.pin("See [the licence](LICENSE) for terms.", "0.11.0")
        assert changed == 1
        assert "](https://github.com/AlphaNerdFx/Tango/blob/v0.11.0/LICENSE)" in out

    def test_an_anchor_is_left_alone(self):
        # The pair to the test above. Anchors resolve on the PyPI page
        # itself, so rewriting them would break links that work today.
        text = "See [coverage](#definition-coverage)."
        out, changed = pypi_readme.pin(text, "0.11.0")
        assert changed == 0
        assert out == text

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
        out, _ = pypi_readme.pin(text, "9.9.9")
        assert "badge/pypi-v9.9.9" in out, (
            "README.md no longer carries the live PyPI badge in the shape "
            "pypi_readme.py matches; --pin would silently pin nothing"
        )
        assert "badge/release-v9.9.9" in out, "the live release badge is not matched"

    def test_pinning_the_real_readme_leaves_no_live_version_badge(self):
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        out, _ = pypi_readme.pin(text, "9.9.9")
        assert "shields.io/pypi/v" not in out
        assert "shields.io/github/v/release" not in out

    def test_pinning_the_real_readme_leaves_nothing_that_reads_now(self):
        # Everything on a frozen page that could answer with today rather
        # than with this release: the CI image, and the link that opens
        # whichever release happens to be newest.
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        out, _ = pypi_readme.pin(text, "9.9.9")
        assert "actions/workflows" not in out
        assert "/releases/latest" not in out

    def test_the_real_readme_has_no_link_that_would_404_on_pypi(self):
        # Every repository-relative link resolves against pypi.org once
        # uploaded. There were four of them, including the licence.
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        out, _ = pypi_readme.pin(text, "9.9.9")
        assert pypi_readme._RELATIVE_LINK.search(out) is None, (
            "a relative link survived pinning and would 404 on the PyPI page"
        )

    def test_the_real_readme_still_has_relative_links_to_rewrite(self):
        # The pair to the test above, and the reason it is not vacuous: a
        # README with no relative links at all would satisfy it for the
        # wrong reason, and hide the rewrite having stopped working.
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        assert pypi_readme._RELATIVE_LINK.search(text) is not None
