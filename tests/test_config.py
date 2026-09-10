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

import os

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
        # Built from tmp_path rather than written as "/var/lib/...". A POSIX
        # absolute path is not absolute on Windows: Path("/var/lib") there is
        # drive-relative and resolves to C:/var/lib, so the literal made this
        # test fail on the Windows runner for a reason that had nothing to do
        # with what it checks.
        override = tmp_path.parent / "elsewhere" / "pipeline.db"
        monkeypatch.setenv("DB_PATH", str(override))

        result = config._resolve_path("DB_PATH", "pipeline.db", root=tmp_path)

        assert result == override
        # The intent: an absolute override is not re-anchored under root.
        assert not str(result).startswith(str(tmp_path) + os.sep)

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


class TestUnknownEnvKeys:
    """
    v0.9.0. `tango doctor` reports settings in .env that nothing reads,
    because a setting that does nothing is a failure with no message. A real
    .env held `SPACY_MODEL=en_core_web_sm`, which looks exactly like it
    chooses the model and is read by nothing.

    The detection itself needs pinning: a mutation that disabled it left
    every other test in this area passing, because they check the declared
    list rather than the function that uses it.
    """

    @staticmethod
    def _env(tmp_path, text):
        path = tmp_path / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_unknown_key_is_reported(self, tmp_path):
        from pipeline.config import unknown_env_keys

        env = self._env(tmp_path, "SPACY_MODEL=en_core_web_sm\nANKI_TIMEOUT=5\n")
        assert unknown_env_keys(env) == ["SPACY_MODEL"]

    def test_known_keys_are_not_reported(self, tmp_path):
        from pipeline.config import unknown_env_keys

        env = self._env(tmp_path, "ANKI_HOST=http://localhost:8765\nMW_API_KEY=x\n")
        assert unknown_env_keys(env) == []

    def test_comments_and_blank_lines_are_not_keys(self, tmp_path):
        from pipeline.config import unknown_env_keys

        env = self._env(tmp_path, "# SPACY_MODEL=commented out\n\n  \nANKI_TIMEOUT=5\n")
        assert unknown_env_keys(env) == []

    def test_a_publishing_credential_in_env_is_reported(self, tmp_path):
        # Reversed on 8 September 2026, and the reversal is the point.
        #
        # These used to be declared known so that doctor would stay quiet
        # about them. That was the wrong side: `config.py` calls
        # `load_dotenv()` at import, so anything in `.env` is loaded into the
        # environment of every `tango run`. A publish credential does not
        # belong in a file with that property, and the same argument applies
        # to the Docker Hub token that turned up there the same day.
        #
        # So doctor reports them now, which is the nudge to move them.
        # CONTRIBUTING.md says where they should live instead.
        from pipeline.config import unknown_env_keys

        env = self._env(tmp_path, "PYPI_USER=__token__\nPYPI_API=pypi-abc\n"
                                  "DOCKER_ACCESS_TOKEN=dckr-abc\n")
        assert unknown_env_keys(env) == ["DOCKER_ACCESS_TOKEN", "PYPI_API", "PYPI_USER"]

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        # This is a diagnostic. It must never be the thing that fails.
        from pipeline.config import unknown_env_keys

        assert unknown_env_keys(tmp_path / "does-not-exist") == []

    def test_several_unknowns_come_back_sorted_and_deduplicated(self, tmp_path):
        from pipeline.config import unknown_env_keys

        env = self._env(tmp_path, "ZZZ_LATER=1\nAPI_DELAY=2\nAPI_DELAY=3\n")
        assert unknown_env_keys(env) == ["API_DELAY", "ZZZ_LATER"]


class TestABlankValueMeansUnset:
    """
    `KEY=` in a .env is not the same as no KEY at all.

    python-dotenv loads a bare `KEY=` into the environment as an empty
    string, so `os.getenv(name, default)` returns "" and never reaches the
    default. `_resolve_path` was given this rule and a test for it; the
    numeric and string settings were not, and .env.example ships ten keys
    with an empty right-hand side.

    Two different failures came out of that. A string setting lost its
    default silently. A numeric setting raised ValueError at import, before
    `main()` existed to turn it into a message.
    """

    def test_a_blank_numeric_setting_uses_its_default(self, monkeypatch):
        monkeypatch.setenv("API_TIMEOUT", "")
        assert config._env_float("API_TIMEOUT", "8") == 8.0

    def test_a_blank_string_setting_uses_its_default(self, monkeypatch):
        # The one that mattered: Wikimedia rejects an empty User-Agent, and
        # a blank WIKTIONARY_USER_AGENT would have sent exactly that.
        monkeypatch.setenv("WIKTIONARY_USER_AGENT", "")
        assert config._env("WIKTIONARY_USER_AGENT", "Tango/x") == "Tango/x"

    def test_whitespace_only_is_blank_too(self, monkeypatch):
        monkeypatch.setenv("ANKI_HOST", "   ")
        assert config._env("ANKI_HOST", "http://localhost:8765") == \
            "http://localhost:8765"

    def test_a_real_value_is_still_honoured(self, monkeypatch):
        # The pair to the three above. A test that only proves blanks fall
        # back is satisfied by a helper that ignores the environment.
        monkeypatch.setenv("API_TIMEOUT", "22")
        monkeypatch.setenv("ANKI_HOST", "http://elsewhere:9999")
        assert config._env_float("API_TIMEOUT", "8") == 22.0
        assert config._env("ANKI_HOST", "http://localhost:8765") == \
            "http://elsewhere:9999"

    def test_a_value_is_stripped_before_use(self, monkeypatch):
        monkeypatch.setenv("ANKI_HOST", "  http://elsewhere:9999  ")
        assert config._env("ANKI_HOST", "x") == "http://elsewhere:9999"


class TestAMalformedValueIsReportedNotRaised:
    """
    A value that is present but unparseable is a mistake worth naming.

    It cannot raise where it is read: `config` is imported before `main()`
    exists to catch anything, so a raise there is a traceback, which
    CLAUDE.md 4.4 forbids for an expected failure. So the default is used
    and the name is recorded for `tango doctor`.
    """

    def setup_method(self):
        config.MALFORMED_ENV_VALUES.clear()

    def teardown_method(self):
        config.MALFORMED_ENV_VALUES.clear()

    def test_a_malformed_number_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("API_TIMEOUT", "banana")
        assert config._env_float("API_TIMEOUT", "8") == 8.0

    def test_a_malformed_number_is_recorded_with_what_the_user_wrote(
        self, monkeypatch
    ):
        # Recording the name alone is not enough to fix it. Doctor prints
        # the offending value back, so the user can see the typo.
        monkeypatch.setenv("API_TIMEOUT", "banana")
        config._env_float("API_TIMEOUT", "8")
        assert config.MALFORMED_ENV_VALUES == {"API_TIMEOUT": "banana"}

    def test_a_float_in_an_integer_setting_is_malformed(self, monkeypatch):
        # int("3.7") raises, which is the right answer: a burst of 3.7
        # requests is not a thing, and silently flooring it hides the typo.
        monkeypatch.setenv("MEDIA_BURST", "3.7")
        assert config._env_int("MEDIA_BURST", "8") == 8
        assert "MEDIA_BURST" in config.MALFORMED_ENV_VALUES

    def test_a_good_value_records_nothing(self, monkeypatch):
        monkeypatch.setenv("API_TIMEOUT", "12")
        assert config._env_float("API_TIMEOUT", "8") == 12.0
        assert config.MALFORMED_ENV_VALUES == {}

    def test_a_blank_value_is_not_malformed(self, monkeypatch):
        # Blank means unset, which is a normal thing to write, not an error.
        monkeypatch.setenv("API_TIMEOUT", "")
        assert config._env_float("API_TIMEOUT", "8") == 8.0
        assert config.MALFORMED_ENV_VALUES == {}


class TestTheSlowFilesystemWarning:
    """
    An install on a Windows drive under WSL is 20x slower to import.

    Measured 10 September 2026: importing spaCy took 44 seconds from a
    virtualenv under `/mnt/c` and 2.2 seconds from one on the Linux
    filesystem, same package, same machine. `tango --version` took 46
    seconds. Nothing in the code can fix it, so `tango doctor` reports it:
    a user whose every command takes 45 seconds will blame the tool, and the
    cause is invisible from the symptom.
    """

    def test_a_windows_drive_under_wsl_is_reported(self, monkeypatch):
        monkeypatch.setattr(config, "is_wsl", lambda: True)
        monkeypatch.setattr(config.sys, "prefix", "/mnt/c/proj/.tangovenv")
        warning = config.slow_filesystem_warning()
        assert warning and "Windows drive" in warning

    def test_the_linux_filesystem_under_wsl_is_fine(self, monkeypatch):
        # The pair. A check that fired on WSL alone would tell every WSL user
        # to move an install that is already in the right place.
        monkeypatch.setattr(config, "is_wsl", lambda: True)
        monkeypatch.setattr(config.sys, "prefix", "/home/someone/proj/.tangovenv")
        assert config.slow_filesystem_warning() is None

    def test_a_mnt_path_off_wsl_is_fine(self, monkeypatch):
        # The other half of the pair. /mnt is an ordinary mount point on
        # native Linux and says nothing about speed there.
        monkeypatch.setattr(config, "is_wsl", lambda: False)
        monkeypatch.setattr(config.sys, "prefix", "/mnt/data/proj/.tangovenv")
        assert config.slow_filesystem_warning() is None

    def test_the_warning_names_the_fix(self, monkeypatch):
        # Reporting a problem without the next step is the failure mode
        # v0.9.0 existed to remove.
        monkeypatch.setattr(config, "is_wsl", lambda: True)
        monkeypatch.setattr(config.sys, "prefix", "/mnt/c/proj/.tangovenv")
        assert "Reinstall" in config.slow_filesystem_warning()
