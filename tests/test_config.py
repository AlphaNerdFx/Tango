"""
Unit tests for pipeline.config path resolution.

Every path setting is anchored to the project root rather than the process's
working directory, because running the pipeline from another directory used
to silently use a different database, a different review file, and an empty
dictionary directory, none of which raise.

The tests below come in matched pairs where a single test could pass by
accident. Anchoring only the default value, for example, satisfies
`test_unset_env_anchors_to_project_root` while leaving the real-world case
broken: the shipped .env.example sets DB_PATH=pipeline.db, so an actual
install never reaches the default at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import config

# ── _resolve_path ─────────────────────────────────────────────────────────────


class TestResolvePath:
    def test_unset_env_anchors_to_project_root(self, monkeypatch, tmp_path):
        """An unset variable falls back to the default, anchored to the root."""
        monkeypatch.delenv("DB_PATH", raising=False)

        result = config._resolve_path("DB_PATH", "pipeline.db", root=tmp_path)

        assert result == tmp_path / "pipeline.db"

    def test_relative_env_override_is_also_anchored(self, monkeypatch, tmp_path):
        """
        The pair to the test above.

        Anchoring the default alone leaves this failing, and this is the case
        that actually ships: .env.example writes DB_PATH=pipeline.db, so a
        real install always takes the override branch, never the default.
        """
        monkeypatch.setenv("DB_PATH", "pipeline.db")

        result = config._resolve_path("DB_PATH", "unused-default.db", root=tmp_path)

        assert result == tmp_path / "pipeline.db"
        assert result.is_absolute()

    def test_absolute_env_override_is_left_alone(self, monkeypatch, tmp_path):
        """An absolute override is deliberate and must not be re-anchored."""
        monkeypatch.setenv("DB_PATH", "/var/lib/tango/pipeline.db")

        result = config._resolve_path("DB_PATH", "pipeline.db", root=tmp_path)

        assert result == Path("/var/lib/tango/pipeline.db")

    def test_tilde_override_expands_to_home(self, monkeypatch, tmp_path):
        """
        ~ is expanded before the absolute check.

        Without expansion, Path("~/tango.db").is_absolute() is False, so the
        path would be anchored into a literal "~" directory inside the repo.
        """
        monkeypatch.setenv("DB_PATH", "~/tango.db")

        result = config._resolve_path("DB_PATH", "pipeline.db", root=tmp_path)

        assert result == Path.home() / "tango.db"
        assert "~" not in str(result)

    def test_empty_env_value_falls_back_to_default(self, monkeypatch, tmp_path):
        """
        An empty value is treated as unset.

        .env.example ships several keys with no value on the right-hand side,
        and python-dotenv loads those as empty strings rather than leaving
        them absent, an empty DB_PATH must not resolve to the project root
        directory itself.
        """
        monkeypatch.setenv("DB_PATH", "")

        result = config._resolve_path("DB_PATH", "pipeline.db", root=tmp_path)

        assert result == tmp_path / "pipeline.db"

    def test_nested_relative_override_keeps_its_structure(self, monkeypatch, tmp_path):
        """A multi-segment relative path is anchored, not flattened."""
        monkeypatch.setenv("DICT_DIR", "data/dictionaries")

        result = config._resolve_path("DICT_DIR", "dictionaries", root=tmp_path)

        assert result == tmp_path / "data" / "dictionaries"


# ── _project_root ─────────────────────────────────────────────────────────────


class TestProjectRoot:
    def test_returns_the_directory_holding_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

        assert config._project_root(root=tmp_path) == tmp_path

    def test_falls_back_to_cwd_without_the_marker(self, tmp_path, monkeypatch):
        """
        The pair to the test above.

        A non-editable install puts the package under site-packages, where
        three levels up is not a project root. Writing a database there would
        be worse than the working-directory behaviour it replaced, so the
        marker check falls back rather than anchoring blindly.
        """
        monkeypatch.chdir(tmp_path)
        marker_less = tmp_path / "site-packages"
        marker_less.mkdir()

        assert config._project_root(root=marker_less) == tmp_path

    def test_computed_root_is_this_repository(self):
        """
        No argument means resolve from config.py's own location: the real
        repository root, three levels above src/pipeline/config.py.
        """
        root = config._project_root()

        assert root.is_absolute()
        assert (root / "pyproject.toml").is_file()
        assert (root / "src" / "pipeline" / "config.py").is_file()


# ── Module-level constants ────────────────────────────────────────────────────


class TestConfiguredPaths:
    @pytest.mark.parametrize(
        "name",
        ["DB_PATH", "OUTPUT_DIR", "REVIEW_FILE", "DICT_DIR"],
    )
    def test_every_path_constant_is_absolute(self, name):
        """
        All four are resolved at import time. A relative one here means some
        module reads it after the process has changed directory and quietly
        gets a different file.
        """
        value = getattr(config, name)

        assert isinstance(value, Path)
        assert value.is_absolute(), f"{name} resolved to a relative path: {value}"

    def test_paths_sit_under_the_project_root(self):
        """
        True for the defaults and for the relative overrides in .env.example.
        Skipped rather than failed when a developer has pointed one somewhere
        else deliberately, since an absolute override is honoured by design.
        """
        for name in ("DB_PATH", "OUTPUT_DIR", "REVIEW_FILE", "DICT_DIR"):
            value = getattr(config, name)
            if not value.is_relative_to(config.PROJECT_ROOT):
                pytest.skip(f"{name} points outside the project root by explicit override")

        assert config.DB_PATH.parent == config.PROJECT_ROOT
