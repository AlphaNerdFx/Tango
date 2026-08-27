"""
Tests for __main__.py — CLI argument parsing, mode dispatch,
summary output, and import prompt.

No real pipeline modules are called — all are mocked.

Run: pytest tests/test_main.py -m "not integration"
"""

import json
from pathlib import Path
from unittest.mock import DEFAULT, MagicMock, patch, call

import pytest

import pipeline.__main__ as main_module
from pipeline import cards as cards_module
from pipeline.__main__ import (
    _build_parser,
    _prompt_import,
    _print_summary,
    _report_torch_build,
    _wrap_words,
    _run_setup_wizard,
    _select_deck,
    main,
)
from pipeline.state import Session


@pytest.fixture
def parser():
    return _build_parser()


@pytest.fixture
def session():
    return Session()


@pytest.fixture
def tmp_apkg(tmp_path) -> Path:
    p = tmp_path / "LV_NoD2M54w_20260628_143022.apkg"
    p.write_bytes(b"PK")  # minimal zip header placeholder
    return p


VIDEO_ID  = "LV_NoD2M54w"
DECK_NAME = "Language::English::Vocabulary"

class TestArgumentParser:

    def test_default_mode_parses_video_id(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID, "--deck", DECK_NAME])
        assert args.video_id == VIDEO_ID

    def test_default_mode_parses_deck(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID, "--deck", DECK_NAME])
        assert args.deck == DECK_NAME

    def test_verbose_flag(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID, "--deck", DECK_NAME, "--verbose"])
        assert args.verbose is True

    def test_verbose_default_false(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID, "--deck", DECK_NAME])
        assert args.verbose is False

    def test_review_flag(self, parser):
        args = parser.parse_args(["--review", "--deck", DECK_NAME])
        assert args.review is True

    def test_process_backlog_flag(self, parser):
        args = parser.parse_args(["--process-backlog", "--deck", DECK_NAME])
        assert args.process_backlog is True

    def test_review_and_backlog_mutually_exclusive(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["--review", "--process-backlog", "--deck", DECK_NAME])

    def test_deck_optional(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID])
        assert args.deck is None

    def test_video_id_optional_in_review_mode(self, parser):
        args = parser.parse_args(["--review", "--deck", DECK_NAME])
        assert args.video_id is None

    def test_force_flag(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID, "--deck", DECK_NAME, "--force"])
        assert args.force is True

    def test_force_default_false(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID, "--deck", DECK_NAME])
        assert args.force is False

    def test_setup_flag(self, parser):
        args = parser.parse_args(["--setup"])
        assert args.setup is True

    def test_setup_default_false(self, parser):
        args = parser.parse_args(["--video-id", VIDEO_ID, "--deck", DECK_NAME])
        assert args.setup is False

    def test_setup_does_not_require_video_id(self, parser):
        # --setup and --list-languages are standalone modes -- neither
        # processes a video, so argparse itself must not require --video-id
        # for them (a --video-id is required error is a main() concern for
        # the default mode, not something argparse enforces here).
        args = parser.parse_args(["--setup"])
        assert args.video_id is None

class TestMainDispatch:

    def test_missing_video_id_exits(self):
        with patch("sys.argv", ["pipeline"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    @patch("pipeline.__main__._run_pipeline")
    def test_dispatches_to_pipeline(self, mock_run):
        with patch("sys.argv", ["pipeline", "--video-id", VIDEO_ID, "--deck", DECK_NAME]):
            main()
        mock_run.assert_called_once()

    @patch("pipeline.__main__._run_review")
    def test_dispatches_to_review(self, mock_run):
        with patch("sys.argv", ["pipeline", "--review", "--deck", DECK_NAME]):
            main()
        mock_run.assert_called_once()

    @patch("pipeline.__main__._run_backlog")
    def test_dispatches_to_backlog(self, mock_run):
        with patch("sys.argv", ["pipeline", "--process-backlog", "--deck", DECK_NAME]):
            main()
        mock_run.assert_called_once()

    @patch("pipeline.__main__._run_setup_wizard")
    def test_setup_dispatches_and_exits_cleanly(self, mock_wizard):
        # Regression guard: --setup used to be unreachable because the
        # --video-id requirement check ran before it and exited with
        # code 1 first, for every standalone mode, not just --setup.
        with patch("sys.argv", ["pipeline", "--setup"]):
            with pytest.raises(SystemExit) as exc:
                main()
        mock_wizard.assert_called_once()
        assert exc.value.code == 0

    def test_list_languages_works_without_video_id(self, capsys):
        # Same bug, same regression guard, for the flag that was already
        # there before --setup existed: --list-languages must not require
        # --video-id either, since it never processes a video.
        with patch("sys.argv", ["pipeline", "--list-languages"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert "fr" in capsys.readouterr().out

class TestSelectDeck:

    def test_deck_arg_bypasses_prompt(self, session):
        result = _select_deck(DECK_NAME, session)
        assert result == DECK_NAME

    def test_deck_arg_sets_session(self, session):
        _select_deck(DECK_NAME, session)
        assert session.deck_name == DECK_NAME

    @patch("pipeline.__main__.get_deck_names", return_value=["Deck A", "Deck B"])
    def test_interactive_selection(self, mock_decks, session):
        with patch("builtins.input", return_value="1"):
            result = _select_deck(None, session)
        assert result == "Deck A"

    @patch("pipeline.__main__.get_deck_names", return_value=["Deck A", "Deck B"])
    def test_invalid_then_valid_input(self, mock_decks, session):
        with patch("builtins.input", side_effect=["x", "0", "2"]):
            result = _select_deck(None, session)
        assert result == "Deck B"

    @patch("pipeline.__main__.get_deck_names",
           side_effect=__import__("pipeline.deck", fromlist=["AnkiNotRunningError"]).AnkiNotRunningError("down"))
    def test_anki_not_running_exits(self, mock_decks, session):
        with pytest.raises(SystemExit) as exc:
            _select_deck(None, session)
        assert exc.value.code == 1


class TestRunSetupWizard:
    """
    Issue #9: guided .env setup for the one genuinely optional credential
    (MW_API_KEY) worth walking a non-technical user through. Every test
    isolates _ENV_PATH/_ENV_EXAMPLE_PATH to tmp_path so nothing touches the
    real project .env.
    """

    @pytest.fixture(autouse=True)
    def isolated_env_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_module, "_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(main_module, "_ENV_EXAMPLE_PATH", tmp_path / ".env.example")
        return tmp_path

    def test_creates_env_from_example_when_missing(self, isolated_env_paths):
        (isolated_env_paths / ".env.example").write_text("DB_PATH=pipeline.db\nMW_API_KEY=\n")
        with patch("builtins.input", return_value="n"):
            _run_setup_wizard()
        env_path = isolated_env_paths / ".env"
        assert env_path.exists()
        assert "DB_PATH=pipeline.db" in env_path.read_text()

    def test_creates_empty_env_when_no_example_exists(self, isolated_env_paths):
        with patch("builtins.input", return_value="n"):
            _run_setup_wizard()
        assert (isolated_env_paths / ".env").exists()

    def test_leaves_existing_env_other_values_untouched(self, isolated_env_paths):
        env_path = isolated_env_paths / ".env"
        env_path.write_text("DB_PATH=custom.db\n")
        with patch("builtins.input", return_value="n"):
            _run_setup_wizard()
        assert "DB_PATH=custom.db" in env_path.read_text()

    def test_already_set_key_skips_prompt_entirely(self, isolated_env_paths):
        env_path = isolated_env_paths / ".env"
        env_path.write_text("MW_API_KEY=existingkey123\n")
        with patch("builtins.input") as mock_input:
            _run_setup_wizard()
        mock_input.assert_not_called()

    def test_declining_the_prompt_leaves_key_unset(self, isolated_env_paths):
        env_path = isolated_env_paths / ".env"
        env_path.write_text("")
        with patch("builtins.input", return_value="n"):
            _run_setup_wizard()
        from dotenv import get_key
        assert get_key(str(env_path), "MW_API_KEY") is None

    def test_accepting_and_pasting_a_key_writes_it(self, isolated_env_paths):
        env_path = isolated_env_paths / ".env"
        env_path.write_text("")
        with patch("builtins.input", side_effect=["y", "abc123realkey"]):
            _run_setup_wizard()
        from dotenv import get_key
        assert get_key(str(env_path), "MW_API_KEY") == "abc123realkey"

    def test_empty_key_input_exits_with_error(self, isolated_env_paths):
        env_path = isolated_env_paths / ".env"
        env_path.write_text("")
        with patch("builtins.input", side_effect=["y", ""]):
            with pytest.raises(SystemExit) as exc:
                _run_setup_wizard()
        assert exc.value.code == 1

    def test_key_with_whitespace_exits_with_error(self, isolated_env_paths):
        # A pasted key with an embedded newline/space is almost always a
        # copy-paste mistake (e.g. grabbing the whole "Your key is: X" line).
        env_path = isolated_env_paths / ".env"
        env_path.write_text("")
        with patch("builtins.input", side_effect=["y", "abc 123"]):
            with pytest.raises(SystemExit) as exc:
                _run_setup_wizard()
        assert exc.value.code == 1

    def test_rejected_key_is_not_written(self, isolated_env_paths):
        env_path = isolated_env_paths / ".env"
        env_path.write_text("")
        with patch("builtins.input", side_effect=["y", ""]):
            with pytest.raises(SystemExit):
                _run_setup_wizard()
        from dotenv import get_key
        assert get_key(str(env_path), "MW_API_KEY") is None


class TestPromptImport:

    @pytest.fixture(autouse=True)
    def _aligned_notetype(self):
        """
        Stub the pre-import notetype alignment for this class.

        Every test here patches `requests.post` with a single mock, which
        cannot represent two different AnkiConnect actions. Once
        _prompt_import() started aligning the notetype first, that one mock
        answered `modelNames` instead, and the assertions below read the
        wrong call -- test_import_on_y_answer still passed, but on the
        alignment POST rather than the import it names.

        Patched on the module object, not re-imported into this test module:
        _prompt_import() resolves it as deck_module.ensure_model_fields at
        call time, and patching a directly-imported name is how six vacuous
        tests in this suite silently stopped patching anything (SESSION.md
        6.11). The alignment itself is covered in test_deck.py.
        """
        with patch("pipeline.deck.ensure_model_fields", return_value=[]) as stub:
            yield stub

    def test_skip_on_n_answer(self, tmp_apkg):
        with patch("builtins.input", return_value="n"):
            with patch("requests.post") as mock_post:
                _prompt_import(tmp_apkg)
                mock_post.assert_not_called()

    def test_skip_on_empty_answer(self, tmp_apkg):
        with patch("builtins.input", return_value=""):
            with patch("requests.post") as mock_post:
                _prompt_import(tmp_apkg)
                mock_post.assert_not_called()

    def test_import_on_y_answer(self, tmp_apkg):
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True, "error": None}
        with patch("builtins.input", return_value="y"):
            with patch("requests.post", return_value=mock_response) as mock_post:
                _prompt_import(tmp_apkg)
                mock_post.assert_called_once()

    def test_import_uses_absolute_path(self, tmp_apkg):
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True, "error": None}
        with patch("builtins.input", return_value="y"):
            with patch("requests.post", return_value=mock_response) as mock_post:
                _prompt_import(tmp_apkg)
                call_kwargs = mock_post.call_args
                path_sent = call_kwargs[1]["json"]["params"]["path"]
                assert Path(path_sent).is_absolute()

    def test_anki_connect_error_warns_not_crashes(self, tmp_apkg):
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": None, "error": "file not found"}
        with patch("builtins.input", return_value="y"):
            with patch("requests.post", return_value=mock_response):
                # Should print warning but not raise
                _prompt_import(tmp_apkg)

    def test_network_error_warns_not_crashes(self, tmp_apkg):
        import requests
        with patch("builtins.input", return_value="y"):
            with patch("requests.post", side_effect=requests.exceptions.ConnectionError):
                _prompt_import(tmp_apkg)

    def test_wsl_path_actually_gets_translated_before_sending(self, tmp_path):
        # End-to-end wiring check, not just _translate_wsl_path() in
        # isolation: _prompt_import() must actually call it and send the
        # translated path to AnkiConnect, under a real /mnt/<drive> path
        # this time (unlike tmp_apkg, which lives under /tmp and never
        # exercised the translation at all).
        apkg_path = Path("/mnt/c/fake/output/video_20260101_000000.apkg")
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True, "error": None}
        with patch("pipeline.__main__._is_wsl", return_value=True), \
             patch("pipeline.__main__.Path.resolve", return_value=apkg_path), \
             patch("builtins.input", return_value="y"), \
             patch("requests.post", return_value=mock_response) as mock_post:
            _prompt_import(apkg_path)
            path_sent = mock_post.call_args[1]["json"]["params"]["path"]
        assert path_sent == "C:\\fake\\output\\video_20260101_000000.apkg"

    def test_notetype_is_aligned_before_the_package_is_imported(
        self, tmp_apkg, _aligned_notetype
    ):
        # Order is the whole point: aligning AFTER the import is aligning
        # after Anki has already forked the notetype. Measured on a real
        # collection -- a 12-field package against a 10-field notetype
        # produced a second notetype at a bumped ID and left 207 notes
        # behind on the old one (ARCHITECTURE.md 8.32).
        calls = []
        _aligned_notetype.side_effect = lambda *a, **k: calls.append("align") or []
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True, "error": None}
        with patch("builtins.input", return_value="y"), \
             patch("requests.post", side_effect=lambda *a, **k: (
                 calls.append("import"), mock_response)[1]):
            _prompt_import(tmp_apkg)
        assert calls == ["align", "import"]

    def test_the_real_field_list_is_what_gets_aligned(
        self, tmp_apkg, _aligned_notetype
    ):
        # Pins the arguments, not just the call. Passing a stale literal
        # here instead of cards.FIELDS would leave any future field out of
        # the alignment and fork the notetype on the import that adds it.
        # The id matters as much: resolving by NAME picks the wrong notetype
        # on any collection where a previous fork took the plain name
        # (ARCHITECTURE 8.33).
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True, "error": None}
        with patch("builtins.input", return_value="y"), \
             patch("requests.post", return_value=mock_response):
            _prompt_import(tmp_apkg)
        model_id, fields = _aligned_notetype.call_args[0]
        assert model_id == cards_module.MODEL_ID
        assert tuple(fields) == cards_module.FIELDS

    def test_exhausted_stdin_skips_the_import_instead_of_crashing(self, tmp_apkg):
        # The documented non-interactive recipe is `echo "s" | make run ...`,
        # which supplies ONE line. deck.prompt_queue consumes it, so this
        # prompt then read from a closed pipe and raised EOFError -- killing
        # the run with a traceback *after* the package had been written.
        # CLAUDE.md 4.4: no traceback for an expected failure.
        with patch("builtins.input", side_effect=EOFError), \
             patch("requests.post") as mock_post:
            _prompt_import(tmp_apkg)          # must not raise
            mock_post.assert_not_called()     # and must not import unasked

    def test_a_failed_alignment_blocks_the_import(self, tmp_apkg, _aligned_notetype):
        # The fail-safe. If we could not confirm the notetype's shape,
        # importing is what splits the collection, so it must not happen.
        _aligned_notetype.side_effect = RuntimeError("anki went away")
        with patch("builtins.input", return_value="y"), \
             patch("requests.post") as mock_post:
            _prompt_import(tmp_apkg)
            mock_post.assert_not_called()


# ── WSL path translation (issue #5) ──────────────────────────────────────────

from pipeline.__main__ import _is_wsl, _translate_wsl_path


class TestIsWsl:

    def test_true_when_proc_version_mentions_microsoft(self):
        with patch("builtins.open", MagicMock(
            return_value=MagicMock(
                __enter__=lambda self: MagicMock(
                    read=lambda: "Linux version 5.15.0 (Microsoft@...)"
                ),
                __exit__=lambda *a: None,
            )
        )):
            assert _is_wsl() is True

    def test_false_on_native_linux(self):
        with patch("builtins.open", MagicMock(
            return_value=MagicMock(
                __enter__=lambda self: MagicMock(
                    read=lambda: "Linux version 6.1.0-generic (gcc...)"
                ),
                __exit__=lambda *a: None,
            )
        )):
            assert _is_wsl() is False

    def test_false_when_proc_version_missing(self):
        # e.g. macOS or Windows native, no /proc filesystem at all
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert _is_wsl() is False


class TestTranslateWslPath:

    def test_translates_mnt_path_under_wsl(self):
        with patch("pipeline.__main__._is_wsl", return_value=True):
            result = _translate_wsl_path("/mnt/c/Users/name/output/file.apkg")
            assert result == "C:\\Users\\name\\output\\file.apkg"

    def test_uppercases_drive_letter(self):
        with patch("pipeline.__main__._is_wsl", return_value=True):
            result = _translate_wsl_path("/mnt/d/videos/clip.apkg")
            assert result.startswith("D:\\")

    def test_noop_when_not_wsl(self):
        with patch("pipeline.__main__._is_wsl", return_value=False):
            path = "/mnt/c/Users/name/file.apkg"
            assert _translate_wsl_path(path) == path

    def test_noop_for_non_mnt_path_even_under_wsl(self):
        # e.g. pytest's tmp_path, which lives under /tmp, not /mnt/<drive>
        with patch("pipeline.__main__._is_wsl", return_value=True):
            path = "/tmp/pytest-abc/output/file.apkg"
            assert _translate_wsl_path(path) == path

    def test_noop_for_home_path_under_wsl(self):
        with patch("pipeline.__main__._is_wsl", return_value=True):
            path = "/home/user/output/file.apkg"
            assert _translate_wsl_path(path) == path


class TestUndefinedWordsAreNamed:
    """
    A run reported "No definition found for 28 word(s)" and stopped there,
    which is a number you cannot act on.

    Measured on a real 406-card German run, 25 of those 28 were transcript
    damage or names -- "Bissch", "Herauszufinde", "Barack" -- the cards a
    learner deletes on sight. They are named rather than filtered: three
    signals were measured against that deck and none separates them from real
    words, so the user triages and the pipeline does not guess.
    """

    def test_the_words_are_printed_not_just_counted(self, tmp_apkg, capsys):
        _print_summary(
            video_id=VIDEO_ID, deck_name=DECK_NAME, apkg_path=tmp_apkg,
            card_count=10, fallback_count=2, skipped_count=0,
            not_found_count=2, not_found_words=["Bissch", "Herauszufinde"],
        )
        out = capsys.readouterr().out
        assert "Bissch" in out and "Herauszufinde" in out

    def test_nothing_is_printed_when_every_word_resolved(self, tmp_apkg, capsys):
        # The partner. A clean run must not grow an empty list under it.
        _print_summary(
            video_id=VIDEO_ID, deck_name=DECK_NAME, apkg_path=tmp_apkg,
            card_count=10, fallback_count=0, skipped_count=0,
            not_found_count=0, not_found_words=[],
        )
        assert "No definition found" not in capsys.readouterr().out

    def test_an_older_caller_that_passes_no_words_still_works(self, tmp_apkg, capsys):
        # The argument is optional, so review and backlog modes could not be
        # broken by adding it.
        _print_summary(
            video_id=VIDEO_ID, deck_name=DECK_NAME, apkg_path=tmp_apkg,
            card_count=1, fallback_count=1, skipped_count=0, not_found_count=1,
        )
        assert "No definition found" in capsys.readouterr().out


class TestWrapWords:

    def test_a_long_list_wraps_instead_of_running_off_the_line(self):
        lines = _wrap_words([f"wort{i:02d}" for i in range(20)], width=30)
        assert len(lines) > 1
        assert all(len(line) <= 30 for line in lines)

    def test_a_very_long_list_is_capped_and_says_how_many_were_left(self):
        # A badly transcribed video can put hundreds here, and a wall of words
        # is read as noise and skipped, which defeats the point.
        lines = _wrap_words([f"wort{i:03d}" for i in range(100)], cap=40)
        assert lines[-1] == "and 60 more"

    def test_a_short_list_is_not_capped(self):
        # The partner: the cap must not fire on an ordinary run.
        assert "more" not in " ".join(_wrap_words(["ah", "bissch"], cap=40))

    def test_no_words_produces_no_lines(self):
        assert _wrap_words([]) == []


class TestPrintSummary:

    def test_prints_without_error(self, tmp_apkg, capsys):
        _print_summary(
            video_id=VIDEO_ID,
            deck_name=DECK_NAME,
            apkg_path=tmp_apkg,
            card_count=42,
            fallback_count=3,
            skipped_count=1,
            not_found_count=3,
        )
        out = capsys.readouterr().out
        assert VIDEO_ID in out
        assert DECK_NAME in out
        assert "42" in out

    def test_not_found_warning_shown(self, tmp_apkg, capsys):
        _print_summary(
            video_id=VIDEO_ID,
            deck_name=DECK_NAME,
            apkg_path=tmp_apkg,
            card_count=10,
            fallback_count=2,
            skipped_count=0,
            not_found_count=2,
        )
        out = capsys.readouterr().out
        assert "No definition found" in out

    def test_no_warning_when_all_found(self, tmp_apkg, capsys):
        _print_summary(
            video_id=VIDEO_ID,
            deck_name=DECK_NAME,
            apkg_path=tmp_apkg,
            card_count=10,
            fallback_count=0,
            skipped_count=0,
            not_found_count=0,
        )
        out = capsys.readouterr().out
        assert "No definition found" not in out

    def test_package_path_shown(self, tmp_apkg, capsys):
        _print_summary(
            video_id=VIDEO_ID,
            deck_name=DECK_NAME,
            apkg_path=tmp_apkg,
            card_count=10,
            fallback_count=0,
            skipped_count=0,
            not_found_count=0,
        )
        out = capsys.readouterr().out
        assert tmp_apkg.name in out

# ── Run modes: the wiring the three CLI entry points do ──────────────────────
#
# __main__.py sat at 55% line coverage with these three functions untested.
# That is not an incidental gap: the two worst bugs found in this project
# were both wiring rather than logic -- a field name that did not match the
# model, and paths resolved against the wrong directory -- and neither was
# reachable from a unit test of any single module. These functions decide
# what gets called with which arguments, and nothing checked that.


def _pipeline_args(**overrides):
    """argparse.Namespace as _run_pipeline expects it, overridable per test."""
    base = dict(
        video_id=VIDEO_ID, deck=DECK_NAME, language="fr", def_lang=None,
        force=False, verbose=False,
    )
    base.update(overrides)
    return MagicMock(**base)


def _fake_package(tmp_path):
    result = MagicMock()
    result.path = tmp_path / "out.apkg"
    result.total_cards = 3
    result.standard_count = 2
    result.fallback_count = 1
    result.skipped_count = 0
    return result


def _fake_batch(found=None, not_found=None):
    batch = MagicMock()
    batch.found = found if found is not None else ["def-a", "def-b"]
    batch.not_found = not_found if not_found is not None else []
    batch.from_cache = []
    batch.not_found_examples = {}
    batch.not_found_examples2 = {}
    batch.not_found_synonyms = {}
    batch.not_found_antonyms = {}
    return batch


@pytest.fixture
def wired(tmp_path):
    """
    Patch every collaborator of the three run modes at once.

    Returns the mock namespace so a test can assert one wiring fact without
    restating the whole stack. Deliberately patches at the __main__ module
    level, since that is where the names these functions actually call live.
    """
    check = MagicMock()
    check.anki_available = True
    check.skip, check.queue = [], []
    new_word = MagicMock()
    new_word.lemma = "aller"
    check.new = [new_word]

    with patch.multiple(
        "pipeline.__main__",
        _select_deck=DEFAULT,
        reset_warning_state=DEFAULT,
        reset_circuit_breaker=DEFAULT,
        check_video_not_processed=DEFAULT,
        save_vocabulary=DEFAULT,
        log_package=DEFAULT,
        mark_video_processed=DEFAULT,
        prompt_queue=DEFAULT,
        load_review_decisions=DEFAULT,
        process_backlog=DEFAULT,
        _print_summary=DEFAULT,
        _prompt_import=DEFAULT,
        transcript_module=DEFAULT,
        nlp_module=DEFAULT,
        deck_module=DEFAULT,
        definition_module=DEFAULT,
        cards=DEFAULT,
    ) as mocks:
        mocks["_select_deck"].return_value = DECK_NAME
        mocks["prompt_queue"].return_value = ([], [])
        mocks["load_review_decisions"].return_value = (["aller"], [])
        mocks["process_backlog"].return_value = check
        mocks["transcript_module"].get_snippets.return_value = {
            "_snippet_count": 5, "_language_code": "fr", "_full_text": "je vais",
        }
        mocks["nlp_module"].process_transcript.return_value = {"aller": 2}
        mocks["deck_module"].check_vocabulary.return_value = check
        mocks["definition_module"].fetch_definitions.return_value = _fake_batch()
        mocks["cards"].build_package.return_value = _fake_package(tmp_path)
        mocks["_check_result"] = check
        yield mocks


class TestNormaliseVideoId:
    """
    --help has always advertised "YouTube video ID or URL" while nothing
    extracted an ID from a URL, and IDs beginning with "-" broke argument
    parsing outright. Both surfaced during release verification: a run on
    -an9d5V7Dvw created its deck, died at argparse, and left an empty deck
    that read as a pipeline failure.
    """

    def test_bare_id_passes_through(self):
        assert main_module._normalise_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_leading_hyphen_id_survives(self):
        """
        Roughly one ID in 64 starts with a hyphen. Nothing here may strip
        or reject it -- the Makefile passes --video-id=<value> so argparse
        accepts it, and this must not undo that.
        """
        assert main_module._normalise_video_id("-an9d5V7Dvw") == "-an9d5V7Dvw"

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    ])
    def test_urls_yield_the_id(self, url):
        assert main_module._normalise_video_id(url) == "dQw4w9WgXcQ"

    def test_url_with_a_hyphen_leading_id(self):
        """The two problems combined, which is the case that started this."""
        assert main_module._normalise_video_id(
            "https://youtu.be/-an9d5V7Dvw") == "-an9d5V7Dvw"

    def test_unreadable_url_raises_rather_than_failing_later(self):
        """
        The pair to the tests above. Passing a URL through unchanged sends
        an obviously-wrong string to the transcript API, which fails further
        down with a worse message.
        """
        with pytest.raises(ValueError, match="Could not find a video ID"):
            main_module._normalise_video_id("https://www.youtube.com/feed/subscriptions")

    def test_unrecognised_non_url_is_left_alone(self):
        """
        Not everything that fails the ID regex is an error to raise on.
        YouTube's format is stable now, but rejecting here would turn a
        future format change into an outage for a check that adds nothing --
        the transcript fetch reports a bad ID perfectly well.
        """
        assert main_module._normalise_video_id("  someFutureFormat123456  ") == "someFutureFormat123456"


class TestSetupCommands:
    """
    --doctor, --install-model and --install-translation.

    They exist because nearly every failure investigated in this project was
    setup rather than logic, and none of it was visible from the failure: an
    ARGOS_PACKAGES_DIR pointing at an empty directory hid every translation
    model, a missing dictionary index produced definition-less cards silently,
    and each printed either nothing or something that read like a different
    problem. --doctor makes that state inspectable in one command.

    The install commands exist for parity: everything the Makefile can do
    should be reachable without make, for anyone who does not use it.
    """

    def test_doctor_is_reachable_without_a_video_id(self, capsys):
        """
        Same regression guard as --setup and --list-languages: standalone
        modes must run before the --video-id requirement, which used to exit
        first for every one of them.
        """
        with patch("sys.argv", ["pipeline", "--doctor"]):
            with pytest.raises(SystemExit):
                main()
        assert "Tango environment" in capsys.readouterr().out

    def test_doctor_exit_code_reports_missing_items(self):
        """
        Non-zero when something is absent, so a setup script can branch.

        `make doctor` deliberately does NOT propagate this. The report's own
        last line says every missing item is optional, and make printing
        "Error 1" under that reads as a broken tool rather than a checklist.
        The Makefile swallows it; the CLI keeps it.
        """
        with patch("pipeline.__main__._run_doctor", return_value=1) as mock_doc:
            with patch("sys.argv", ["pipeline", "--doctor"]):
                with pytest.raises(SystemExit) as exc:
                    main()
        mock_doc.assert_called_once()
        assert exc.value.code == 1

    def test_build_antonyms_is_reachable_without_a_video_id(self):
        """Same standalone-mode guard as --doctor, for the ADR-010 index."""
        with patch("pipeline.antonyms.build_index", return_value=52156) as mock_build:
            with patch("sys.argv", ["pipeline", "--build-antonyms"]):
                with pytest.raises(SystemExit) as exc:
                    main()
        mock_build.assert_called_once()
        assert exc.value.code == 0

    def test_build_antonyms_reports_a_failure_as_a_message_not_a_traceback(self, capsys):
        """CLAUDE.md 4.4: an expected failure is a message and an exit code."""
        from pipeline.antonyms import AntonymDownloadError

        with patch("pipeline.antonyms.build_index", side_effect=AntonymDownloadError("no network")):
            with patch("sys.argv", ["pipeline", "--build-antonyms"]):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1
        # _err writes to stderr, which is where a message a script might
        # redirect belongs.
        assert "no network" in capsys.readouterr().err

    def test_doctor_reports_the_antonym_index_without_counting_it_missing(self, capsys):
        """
        The index is optional: a run without it produces the cards it
        produced before the index existed. Reporting it as missing would
        make --doctor exit non-zero on a perfectly working install.
        """
        with patch("pipeline.antonyms.is_available", return_value=False):
            with patch("sys.argv", ["pipeline", "--doctor"]):
                with pytest.raises(SystemExit):
                    main()
        assert "make antonyms" in capsys.readouterr().out

    @patch("pipeline.__main__.subprocess.run")
    def test_install_model_resolves_the_language_code(self, mock_run):
        """The user names a language; the spaCy model name is looked up."""
        mock_run.return_value = MagicMock(returncode=0)
        with patch("sys.argv", ["pipeline", "--install-model", "de"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert "de_core_news_sm" in mock_run.call_args.args[0]

    @patch("pipeline.__main__.subprocess.run")
    def test_install_model_rejects_an_unsupported_language(self, mock_run):
        """The pair to the test above — no download attempted for a bad code."""
        with patch("sys.argv", ["pipeline", "--install-model", "zzz"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1
        mock_run.assert_not_called()

    def test_install_translation_parses_the_pair(self):
        with patch("pipeline.translation.install_translation", return_value=True) as mock_i:
            with patch("sys.argv", ["pipeline", "--install-translation", "de:en"]):
                with pytest.raises(SystemExit) as exc:
                    main()
        mock_i.assert_called_once_with("de", "en")
        assert exc.value.code == 0

    def test_install_translation_rejects_a_malformed_pair(self):
        """FROM:TO, not "de en" or "de-en"."""
        with patch("pipeline.translation.install_translation") as mock_i:
            with patch("sys.argv", ["pipeline", "--install-translation", "de-en"]):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1
        mock_i.assert_not_called()


class TestRunPipelineWiring:

    def test_force_skips_the_already_processed_guard(self, wired, session):
        """--force must bypass the guard entirely, not catch its exception."""
        main_module._run_pipeline(_pipeline_args(force=True), session)
        wired["check_video_not_processed"].assert_not_called()

    def test_without_force_the_guard_runs(self, wired, session):
        """The pair to the test above."""
        main_module._run_pipeline(_pipeline_args(force=False), session)
        wired["check_video_not_processed"].assert_called_once_with(VIDEO_ID)

    def test_resolved_language_reaches_the_definition_fetch(self, wired, session):
        """
        --language fr must arrive at fetch_definitions. Defaulting to English
        here would query the wrong dictionary for every word, and silently:
        the run reports success either way.
        """
        main_module._run_pipeline(_pipeline_args(language="fr"), session)
        assert wired["definition_module"].fetch_definitions.call_args.kwargs["language"] == "fr"

    def test_resolved_language_reaches_the_card_builder(self, wired, session):
        """
        The pair to the test above, and not redundant: build_package's
        language feeds guid_for(lemma, video_id, language), so a wrong value
        here reintroduces the cross-language GUID collision issue #14 fixed.
        """
        main_module._run_pipeline(_pipeline_args(language="fr"), session)
        assert wired["cards"].build_package.call_args.kwargs["language"] == "fr"

    def test_def_lang_matching_language_is_treated_as_native(self, wired, session):
        """
        --def-lang fr with --language fr is native mode, so no translation
        should be requested. Passing "fr" through would send every lemma
        through a translator to produce the word it started as.
        """
        main_module._run_pipeline(_pipeline_args(language="fr", def_lang="fr"), session)
        assert wired["definition_module"].fetch_definitions.call_args.kwargs["def_language"] is None

    def test_differing_def_lang_is_passed_through(self, wired, session):
        """The pair to the test above: a genuine translation request survives."""
        main_module._run_pipeline(_pipeline_args(language="fr", def_lang="en"), session)
        assert wired["definition_module"].fetch_definitions.call_args.kwargs["def_language"] == "en"

    def test_surface_forms_reach_the_card_builder(self, wired, session):
        """
        The out-parameter 8.20 added. If it stops being threaded through,
        transcript examples silently fall back to lemma-only matching and
        coverage drops from 100% to 87% with nothing raised.
        """
        main_module._run_pipeline(_pipeline_args(), session)
        assert "surface_forms" in wired["cards"].build_package.call_args.kwargs

    def test_not_found_extras_reach_the_card_builder(self, wired, session):
        """
        Every not_found_* channel must be threaded. The second Wiktionary
        example was fetched and then dropped for exactly this reason: the
        data existed and the wiring did not carry it.
        """
        kwargs = None
        main_module._run_pipeline(_pipeline_args(), session)
        kwargs = wired["cards"].build_package.call_args.kwargs
        for name in ("not_found_examples", "not_found_examples2",
                     "not_found_synonyms", "not_found_antonyms"):
            assert name in kwargs, f"{name} not passed to build_package"

    def test_anki_unavailable_stops_before_fetching_definitions(self, wired, session):
        """
        Words went to the backlog, so there is nothing to define. Continuing
        would spend API quota building a package for a deck check that never
        happened.
        """
        wired["_check_result"].anki_available = False
        with pytest.raises(SystemExit) as exc:
            main_module._run_pipeline(_pipeline_args(), session)
        assert exc.value.code == 0
        wired["definition_module"].fetch_definitions.assert_not_called()

    def test_queue_approvals_are_added_to_the_words_defined(self, wired, session):
        """Approved queue words must join the new ones, not replace them."""
        queued = MagicMock()
        queued.lemma = "venir"
        wired["_check_result"].queue = [queued]
        wired["prompt_queue"].return_value = (["venir"], [])
        main_module._run_pipeline(_pipeline_args(), session)
        assert wired["definition_module"].fetch_definitions.call_args.args[0] == ["aller", "venir"]

    def test_vocabulary_is_saved_before_the_deck_check(self, wired, session):
        """
        save_vocabulary records what the video contained, which is a
        different question from what got added to the deck.
        """
        main_module._run_pipeline(_pipeline_args(), session)
        wired["save_vocabulary"].assert_called_once_with(VIDEO_ID, {"aller": 2})

    def test_marks_processed_with_the_real_counts(self, wired, session):
        main_module._run_pipeline(_pipeline_args(), session)
        kwargs = wired["mark_video_processed"].call_args.kwargs
        assert kwargs["card_count"] == 3
        assert kwargs["word_count"] == 1


class TestRunReviewWiring:

    def test_empty_decisions_exit_cleanly(self, wired, session):
        wired["load_review_decisions"].return_value = ([], [])
        with pytest.raises(SystemExit) as exc:
            main_module._run_review(_pipeline_args(), session)
        assert exc.value.code == 0

    def test_skip_only_decisions_build_nothing(self, wired, session):
        """Words marked 'skip' are decisions, but there is nothing to build."""
        wired["load_review_decisions"].return_value = ([], ["chien"])
        with pytest.raises(SystemExit) as exc:
            main_module._run_review(_pipeline_args(), session)
        assert exc.value.code == 0
        wired["cards"].build_package.assert_not_called()

    def test_language_reaches_the_definition_fetch(self, wired, session):
        """
        Review mode used to call fetch_definitions(to_add) with no language,
        taking the "en" default, so `make review DECK="French"` fetched every
        French word from English sources and reported success.
        """
        main_module._run_review(_pipeline_args(language="fr"), session)
        assert wired["definition_module"].fetch_definitions.call_args.kwargs["language"] == "fr"

    def test_language_reaches_the_card_builder(self, wired, session):
        """
        The pair: build_package's language feeds the note GUID, so an "en"
        default here makes a French review card collide with the English card
        for the same spelling -- the collision class issue #14 closed.
        """
        main_module._run_review(_pipeline_args(language="fr"), session)
        assert wired["cards"].build_package.call_args.kwargs["language"] == "fr"

    def test_unresolvable_language_falls_back_rather_than_exiting(self, wired, session):
        """
        A deck named "My Words" has no language in it and no transcript to
        select, so review mode must keep working. _run_pipeline exits here
        because the language picks the subtitle track; review has no such
        dependency, and failing hard would break decks that work today.
        """
        main_module._run_review(_pipeline_args(language=None, deck="My Words"), session)
        assert wired["cards"].build_package.call_args.kwargs["language"] == "en"


class TestRunBacklogWiring:

    def test_anki_down_exits_nonzero(self, wired, session):
        wired["process_backlog"].side_effect = main_module.AnkiNotRunningError("down")
        with pytest.raises(SystemExit) as exc:
            main_module._run_backlog(_pipeline_args(), session)
        assert exc.value.code == 1

    def test_empty_backlog_exits_cleanly(self, wired, session):
        empty = MagicMock()
        empty.new, empty.queue = [], []
        wired["process_backlog"].return_value = empty
        with pytest.raises(SystemExit) as exc:
            main_module._run_backlog(_pipeline_args(), session)
        assert exc.value.code == 0

    def test_language_reaches_the_definition_fetch(self, wired, session):
        """Same defect as review mode, same silence."""
        main_module._run_backlog(_pipeline_args(language="fr"), session)
        assert wired["definition_module"].fetch_definitions.call_args.kwargs["language"] == "fr"

    def test_language_reaches_the_card_builder(self, wired, session):
        main_module._run_backlog(_pipeline_args(language="fr"), session)
        assert wired["cards"].build_package.call_args.kwargs["language"] == "fr"

    def test_queue_approvals_join_the_new_words(self, wired, session):
        queued = MagicMock()
        queued.lemma = "venir"
        wired["_check_result"].queue = [queued]
        wired["prompt_queue"].return_value = (["venir"], [])
        main_module._run_backlog(_pipeline_args(), session)
        assert wired["definition_module"].fetch_definitions.call_args.args[0] == ["aller", "venir"]


@pytest.mark.integration
class TestIntegration:

    def test_full_pipeline_run(self):
        """
        Runs the full pipeline end-to-end against a real YouTube video.
        Requires: network, Anki running, MW_API_KEY set.
        """
        with patch("sys.argv", [
            "pipeline",
            "--video-id", VIDEO_ID,
            "--deck", DECK_NAME,
        ]):
            with patch("builtins.input", return_value="n"):  # skip import prompt
                main()

class TestTorchBuildReport:
    """
    4.5 GB of CUDA libraries, 76% of the virtualenv, on a machine that cannot
    call them.

    torch arrives through argostranslate -> stanza -> `torch>=1.3.0`, which
    names no variant, so pip takes the default PyPI wheel. Since torch 2.x
    that wheel bundles CUDA and pulls nvidia-* and triton. Nothing about the
    pipeline reveals this: it runs correctly either way, just four gigabytes
    larger, which is why `--doctor` reports it.
    """

    @staticmethod
    def _fake_torch(cuda_version, available):
        torch = MagicMock()
        torch.version.cuda = cuda_version
        torch.cuda.is_available.return_value = available
        return torch

    def test_a_cuda_build_with_no_usable_gpu_is_reported(self, capsys):
        with patch.dict("sys.modules", {"torch": self._fake_torch("13.0", False)}):
            counted = _report_torch_build()
        out = capsys.readouterr().out
        assert counted == 1
        assert "no usable GPU" in out
        assert "download.pytorch.org/whl/cpu" in out

    def test_a_cuda_build_with_a_working_gpu_is_left_alone(self, capsys):
        # The partner. Someone with a real GPU is not wasting anything and
        # must not be told to reinstall a CPU build over the top of it.
        with patch.dict("sys.modules", {"torch": self._fake_torch("13.0", True)}):
            counted = _report_torch_build()
        out = capsys.readouterr().out
        assert counted == 0
        assert "download.pytorch.org/whl/cpu" not in out

    def test_a_cpu_build_is_not_flagged(self, capsys):
        with patch.dict("sys.modules", {"torch": self._fake_torch(None, False)}):
            counted = _report_torch_build()
        assert counted == 0
        assert "CPU build" in capsys.readouterr().out

    def test_a_broken_cuda_probe_counts_as_no_gpu(self, capsys):
        # torch.cuda.is_available() raises on some driver mismatches rather
        # than returning False, and that must read as "cannot use it" instead
        # of crashing the one command meant to diagnose the machine.
        torch = self._fake_torch("13.0", False)
        torch.cuda.is_available.side_effect = RuntimeError("driver too old")
        with patch.dict("sys.modules", {"torch": torch}):
            counted = _report_torch_build()
        assert counted == 1
        assert "no usable GPU" in capsys.readouterr().out

    def test_torch_absent_is_not_a_problem(self, capsys):
        # Translation is optional, so no torch at all is the smallest install
        # there is and must not be reported as something to fix.
        import builtins
        import sys
        real_import = builtins.__import__

        def no_torch(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("no module named torch")
            return real_import(name, *args, **kwargs)

        with patch.dict("sys.modules", {}, clear=False):
            sys.modules.pop("torch", None)
            with patch.object(builtins, "__import__", no_torch):
                counted = _report_torch_build()
        assert counted == 0
        assert capsys.readouterr().out == ""
