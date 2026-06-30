"""
Tests for __main__.py — CLI argument parsing, mode dispatch,
summary output, and import prompt.

No real pipeline modules are called — all are mocked.

Run: pytest tests/test_main.py -m "not integration"
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from pipeline.__main__ import (
    _build_parser,
    _prompt_import,
    _print_summary,
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

class TestPromptImport:

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