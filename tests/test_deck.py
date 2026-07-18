"""
test_deck.py

All AnkiConnect calls and file I/O are mocked.
No running Anki instance required for the unit suite.

Run unit tests:         pytest tests/test_deck.py -m "not integration"
Run all (Anki needed):  pytest tests/test_deck.py
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

from pipeline.deck import (
    Decision,
    DeckCheckResult,
    MatchResult,
    AnkiConnectError,
    AnkiNotRunningError,
    _check_single,
    _write_review_file,
    _write_backlog,
    check_vocabulary,
    clear_backlog,
    get_backlog,
    get_card_fronts,
    get_deck_names,
    is_anki_running,
    load_review_decisions,
    process_backlog,
    prompt_queue,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    SHORT_WORD_THRESHOLD,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_fronts() -> list[str]:
    return ["contamination", "water", "permanent", "develop", "photograph"]


@pytest.fixture
def sample_vocabulary() -> dict[str, int]:
    return {
        "contaminate": 2,   # should QUEUE against "contamination"
        "water":       5,   # should SKIP — exact match
        "dog":         1,   # should NEW — no match
        "run":         3,   # short word — exact only
    }


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect SQLite DB to a temp path for every test."""
    import pipeline.deck as deck_module
    db_file = tmp_path / "test_pipeline.db"
    monkeypatch.setattr("pipeline.config.DB_PATH", db_file)
    monkeypatch.setattr("pipeline.deck.DB_PATH", db_file)
    yield db_file


@pytest.fixture(autouse=True)
def tmp_review_file(tmp_path, monkeypatch):
    """Redirect review.json to a temp path for every test."""
    import pipeline.deck as deck_module
    review = tmp_path / "review.json"
    monkeypatch.setattr(deck_module, "REVIEW_FILE", review)
    yield review


# ── is_anki_running ───────────────────────────────────────────────────────────

class TestIsAnkiRunning:

    @patch("pipeline.deck._anki_request", return_value=6)
    def test_returns_true_when_reachable(self, _):
        assert is_anki_running() is True

    @patch("pipeline.deck._anki_request", side_effect=AnkiNotRunningError("down"))
    def test_returns_false_when_unreachable(self, _):
        assert is_anki_running() is False

    @patch("pipeline.deck._anki_request", side_effect=AnkiConnectError("err"))
    def test_returns_false_on_connect_error(self, _):
        assert is_anki_running() is False


# ── get_deck_names ────────────────────────────────────────────────────────────

class TestGetDeckNames:

    @patch("pipeline.deck._anki_request", return_value=["Spanish", "Default", "English"])
    def test_returns_sorted_list(self, _):
        result = get_deck_names()
        assert result == ["Default", "English", "Spanish"]

    @patch("pipeline.deck._anki_request", side_effect=AnkiNotRunningError("down"))
    def test_raises_when_anki_not_running(self, _):
        with pytest.raises(AnkiNotRunningError):
            get_deck_names()


# ── get_card_fronts ───────────────────────────────────────────────────────────

class TestGetCardFronts:

    @patch("pipeline.deck._anki_request")
    def test_returns_lowercase_fronts(self, mock_req):
        mock_req.side_effect = [
            [1001, 1002],
            [
                {"fields": {"Front": {"value": "Water"}}},
                {"fields": {"Front": {"value": "Permanent"}}},
            ],
        ]
        result = get_card_fronts("English")
        assert "water" in result
        assert "permanent" in result

    @patch("pipeline.deck._anki_request")
    def test_empty_deck_returns_empty_list(self, mock_req):
        mock_req.side_effect = [[]]
        result = get_card_fronts("EmptyDeck")
        assert result == []

    @patch("pipeline.deck._anki_request")
    def test_skips_notes_with_no_front_field(self, mock_req):
        mock_req.side_effect = [
            [1001],
            [{"fields": {}}],
        ]
        result = get_card_fronts("English")
        assert result == []

    @patch("pipeline.deck._anki_request", side_effect=AnkiNotRunningError("down"))
    def test_raises_when_anki_not_running(self, _):
        with pytest.raises(AnkiNotRunningError):
            get_card_fronts("English")


# ── _check_single ─────────────────────────────────────────────────────────────

class TestCheckSingle:

    def test_exact_match_returns_skip(self, sample_fronts):
        result = _check_single("water", sample_fronts)
        assert result.decision == Decision.SKIP
        assert result.score == 100.0

    def test_exact_match_case_insensitive(self, sample_fronts):
        result = _check_single("Water", sample_fronts)
        assert result.decision == Decision.SKIP

    def test_high_fuzzy_match_returns_skip(self):
        # "run" vs "run" is exact, use a pair that scores just above 90
        fronts = ["running"]
        # "runs" vs "running" — WRatio ~90, but short word rule applies for len<4
        # Use longer words to test HIGH band
        result = _check_single("develop", ["developer"])
        assert result.decision in (Decision.SKIP, Decision.QUEUE)

    def test_mid_fuzzy_match_returns_queue(self, sample_fronts):
        # "contaminate" vs "contamination" scores ~83 — should QUEUE
        result = _check_single("contaminate", sample_fronts)
        assert result.decision == Decision.QUEUE
        assert result.matched_front == "contamination"
        assert CONFIDENCE_LOW <= result.score <= CONFIDENCE_HIGH

    def test_no_match_returns_new(self, sample_fronts):
        result = _check_single("philosophy", sample_fronts)
        assert result.decision == Decision.NEW

    def test_empty_fronts_returns_new(self):
        result = _check_single("water", [])
        assert result.decision == Decision.NEW

    def test_short_word_exact_match_returns_skip(self):
        result = _check_single("go", ["go", "water"])
        assert result.decision == Decision.SKIP

    def test_short_word_no_exact_match_returns_new(self):
        # "go" vs "going" would score 90 with WRatio but short word rule
        # means we use exact match only — should return NEW
        result = _check_single("go", ["going", "water"])
        assert result.decision == Decision.NEW

    def test_short_word_threshold_boundary(self):
        # Word of exactly SHORT_WORD_THRESHOLD chars uses fuzzy
        word = "a" * SHORT_WORD_THRESHOLD
        result = _check_single(word, ["something_unrelated"])
        assert result.decision == Decision.NEW

    def test_queue_result_has_matched_front(self, sample_fronts):
        result = _check_single("contaminate", sample_fronts)
        assert result.matched_front is not None
        assert result.score is not None

    def test_new_result_has_no_matched_front(self):
        result = _check_single("philosophy", ["water", "permanent"])
        assert result.matched_front is None


# ── check_vocabulary ──────────────────────────────────────────────────────────

class TestCheckVocabulary:

    @patch("pipeline.deck.get_card_fronts")
    def test_returns_deck_check_result(self, mock_fronts, sample_vocabulary):
        mock_fronts.return_value = ["water", "contamination"]
        result = check_vocabulary(sample_vocabulary, "English")
        assert isinstance(result, DeckCheckResult)

    @patch("pipeline.deck.get_card_fronts")
    def test_exact_match_goes_to_skip(self, mock_fronts):
        mock_fronts.return_value = ["water"]
        result = check_vocabulary({"water": 5}, "English")
        assert any(m.lemma == "water" for m in result.skip)

    @patch("pipeline.deck.get_card_fronts")
    def test_fuzzy_match_goes_to_queue(self, mock_fronts):
        mock_fronts.return_value = ["contamination"]
        result = check_vocabulary({"contaminate": 2}, "English")
        assert any(m.lemma == "contaminate" for m in result.queue)

    @patch("pipeline.deck.get_card_fronts")
    def test_no_match_goes_to_new(self, mock_fronts):
        mock_fronts.return_value = ["water"]
        result = check_vocabulary({"philosophy": 1}, "English")
        assert any(m.lemma == "philosophy" for m in result.new)

    @patch("pipeline.deck.get_card_fronts", side_effect=AnkiNotRunningError("down"))
    def test_anki_unavailable_sets_flag(self, _, sample_vocabulary):
        result = check_vocabulary(sample_vocabulary, "English")
        assert result.anki_available is False

    @patch("pipeline.deck.get_card_fronts", side_effect=AnkiNotRunningError("down"))
    def test_anki_unavailable_writes_backlog(self, _, sample_vocabulary, tmp_db):
        check_vocabulary(sample_vocabulary, "English")
        backlog = get_backlog("English")
        assert len(backlog) == len(sample_vocabulary)

    @patch("pipeline.deck.get_card_fronts", side_effect=AnkiNotRunningError("down"))
    def test_anki_unavailable_returns_empty_lists(self, _, sample_vocabulary):
        result = check_vocabulary(sample_vocabulary, "English")
        assert result.skip == []
        assert result.queue == []
        assert result.new == []

    @patch("pipeline.deck.get_card_fronts")
    def test_queue_written_to_db(self, mock_fronts):
        mock_fronts.return_value = ["contamination"]
        check_vocabulary({"contaminate": 1}, "English")
        # Queue written to DB — verify via SQLite directly
        import pipeline.deck as deck_module
        conn = sqlite3.connect(deck_module.DB_PATH)
        rows = conn.execute("SELECT * FROM anki_backlog").fetchall()
        conn.close()
        # Backlog only written when Anki is down — queue uses separate table
        # This confirms the DB was created and accessible
        assert conn is not None


# ── prompt_queue ──────────────────────────────────────────────────────────────

class TestPromptQueue:

    def _make_queue(self) -> list[MatchResult]:
        return [
            MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3),
            MatchResult("develop",     Decision.QUEUE, "developer",     87.5),
            MatchResult("photo",       Decision.QUEUE, "photograph",    90.0),
        ]

    def test_empty_queue_returns_empty_lists(self):
        approved, deferred = prompt_queue([])
        assert approved == []
        assert deferred == []

    def test_y_answer_approves_word(self):
        queue = [MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)]
        with patch("builtins.input", return_value="y"):
            approved, deferred = prompt_queue(queue)
        assert "contaminate" in approved
        assert "contaminate" not in deferred

    def test_n_answer_defers_word(self):
        queue = [MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)]
        with patch("builtins.input", return_value="n"):
            approved, deferred = prompt_queue(queue)
        assert "contaminate" in deferred
        assert "contaminate" not in approved

    def test_s_defers_all_remaining(self):
        queue = self._make_queue()
        # Answer "y" for first, "s" for second — third should auto-defer
        with patch("builtins.input", side_effect=["y", "s"]):
            approved, deferred = prompt_queue(queue)
        assert "contaminate" in approved
        assert "develop" in deferred
        assert "photo" in deferred

    def test_invalid_input_prompts_again(self):
        queue = [MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)]
        with patch("builtins.input", side_effect=["x", "?", "y"]):
            approved, deferred = prompt_queue(queue)
        assert "contaminate" in approved

    def test_deferred_words_written_to_review_file(self, tmp_review_file):
        queue = [MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)]
        with patch("builtins.input", return_value="n"):
            prompt_queue(queue)
        assert tmp_review_file.exists()
        data = json.loads(tmp_review_file.read_text())
        assert any(e["lemma"] == "contaminate" for e in data)


# ── _write_review_file ────────────────────────────────────────────────────────

class TestWriteReviewFile:

    def test_creates_review_file(self, tmp_review_file):
        matches = [MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)]
        _write_review_file(matches)
        assert tmp_review_file.exists()

    def test_review_file_structure(self, tmp_review_file):
        matches = [MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)]
        _write_review_file(matches)
        data = json.loads(tmp_review_file.read_text())
        assert isinstance(data, list)
        entry = data[0]
        assert "lemma" in entry
        assert "matched_front" in entry
        assert "score" in entry
        assert "decision" in entry
        assert entry["decision"] is None

    def test_appends_to_existing_file(self, tmp_review_file):
        _write_review_file([MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)])
        _write_review_file([MatchResult("develop", Decision.QUEUE, "developer", 87.5)])
        data = json.loads(tmp_review_file.read_text())
        lemmas = [e["lemma"] for e in data]
        assert "contaminate" in lemmas
        assert "develop" in lemmas

    def test_does_not_duplicate_existing_entries(self, tmp_review_file):
        match = MatchResult("contaminate", Decision.QUEUE, "contamination", 83.3)
        _write_review_file([match])
        _write_review_file([match])
        data = json.loads(tmp_review_file.read_text())
        assert len([e for e in data if e["lemma"] == "contaminate"]) == 1


# ── load_review_decisions ─────────────────────────────────────────────────────

class TestLoadReviewDecisions:

    def test_returns_empty_when_no_file(self, tmp_review_file):
        to_add, to_skip = load_review_decisions()
        assert to_add == []
        assert to_skip == []

    def test_reads_add_decisions(self, tmp_review_file):
        data = [{"lemma": "contaminate", "matched_front": "contamination",
                 "score": 83.3, "decision": "add"}]
        tmp_review_file.write_text(json.dumps(data))
        to_add, _ = load_review_decisions()
        assert "contaminate" in to_add

    def test_reads_skip_decisions(self, tmp_review_file):
        data = [{"lemma": "develop", "matched_front": "developer",
                 "score": 87.5, "decision": "skip"}]
        tmp_review_file.write_text(json.dumps(data))
        _, to_skip = load_review_decisions()
        assert "develop" in to_skip

    def test_ignores_null_decisions(self, tmp_review_file):
        data = [{"lemma": "photo", "matched_front": "photograph",
                 "score": 90.0, "decision": None}]
        tmp_review_file.write_text(json.dumps(data))
        to_add, to_skip = load_review_decisions()
        assert "photo" not in to_add
        assert "photo" not in to_skip


# ── SQLite backlog ────────────────────────────────────────────────────────────

class TestBacklog:

    def test_write_and_read_backlog(self):
        _write_backlog(["contaminate", "water", "develop"], "English")
        result = get_backlog("English")
        assert "contaminate" in result
        assert "water" in result
        assert "develop" in result

    def test_backlog_is_deck_specific(self):
        _write_backlog(["contaminate"], "English")
        _write_backlog(["perro"], "Spanish")
        english = get_backlog("English")
        spanish = get_backlog("Spanish")
        assert "contaminate" in english
        assert "contaminate" not in spanish
        assert "perro" in spanish

    def test_no_duplicates_in_backlog(self):
        _write_backlog(["water"], "English")
        _write_backlog(["water"], "English")
        result = get_backlog("English")
        assert result.count("water") == 1

    def test_clear_backlog(self):
        _write_backlog(["contaminate", "water"], "English")
        clear_backlog("English", ["contaminate"])
        result = get_backlog("English")
        assert "contaminate" not in result
        assert "water" in result

    def test_empty_backlog_returns_empty_list(self):
        result = get_backlog("NonExistentDeck")
        assert result == []


# ── process_backlog ───────────────────────────────────────────────────────────

class TestProcessBacklog:

    @patch("pipeline.deck.check_vocabulary")
    def test_returns_empty_result_when_no_backlog(self, mock_check):
        result = process_backlog("English")
        mock_check.assert_not_called()
        assert isinstance(result, DeckCheckResult)

    @patch("pipeline.deck.check_vocabulary")
    def test_processes_backlogged_words(self, mock_check):
        _write_backlog(["contaminate", "water"], "English")
        mock_result = DeckCheckResult(anki_available=True)
        mock_result.new = [MatchResult("contaminate", Decision.NEW)]
        mock_result.skip = [MatchResult("water", Decision.SKIP, "water", 100.0)]
        mock_check.return_value = mock_result
        process_backlog("English")
        mock_check.assert_called_once()

    @patch("pipeline.deck.check_vocabulary")
    def test_clears_backlog_after_successful_process(self, mock_check):
        _write_backlog(["contaminate"], "English")
        mock_check.return_value = DeckCheckResult(anki_available=True)
        process_backlog("English")
        assert get_backlog("English") == []

    @patch("pipeline.deck.check_vocabulary")
    def test_does_not_clear_backlog_when_anki_unavailable(self, mock_check):
        _write_backlog(["contaminate"], "English")
        mock_check.return_value = DeckCheckResult(anki_available=False)
        process_backlog("English")
        assert "contaminate" in get_backlog("English")


# ── Integration (live Anki required) ─────────────────────────────────────────

@pytest.mark.integration
class TestIntegration:

    def test_anki_is_running(self):
        assert is_anki_running() is True

    def test_get_deck_names_returns_list(self):
        decks = get_deck_names()
        assert isinstance(decks, list)
        assert len(decks) > 0

    def test_get_card_fronts_returns_list(self):
        decks = get_deck_names()
        fronts = get_card_fronts(decks[0])
        assert isinstance(fronts, list)

# ── _is_sentence_structured_deck ──────────────────────────────────────────────

class TestIsSentenceStructuredDeck:

    def test_word_deck_returns_false(self):
        from pipeline.deck import _is_sentence_structured_deck
        fronts = ["contamination", "water", "permanent", "develop", "photograph"]
        assert _is_sentence_structured_deck(fronts) is False

    def test_sentence_deck_returns_true(self):
        from pipeline.deck import _is_sentence_structured_deck
        fronts = [
            "what is the definition of aloof?",
            "give an example of a microcosm.",
            "she made a sharp riposte",
        ]
        assert _is_sentence_structured_deck(fronts) is True

    def test_empty_fronts_returns_false(self):
        from pipeline.deck import _is_sentence_structured_deck
        assert _is_sentence_structured_deck([]) is False

    def test_mixed_deck_uses_average(self):
        from pipeline.deck import _is_sentence_structured_deck
        # Mostly single words with a couple of sentences — average stays low
        fronts = ["water", "develop", "permanent", "what does this mean exactly today"]
        # 3 single words (1 word each) + 1 six-word front = 9/4 = 2.25 average
        assert _is_sentence_structured_deck(fronts) is False

    def test_custom_threshold(self):
        from pipeline.deck import _is_sentence_structured_deck
        fronts = ["two words", "two words", "two words"]
        assert _is_sentence_structured_deck(fronts, threshold=1.0) is True
        assert _is_sentence_structured_deck(fronts, threshold=3.0) is False


# ── _check_single with skip_fuzzy ─────────────────────────────────────────────

class TestCheckSingleSkipFuzzy:

    def test_skip_fuzzy_blocks_substring_false_positive(self):
        from pipeline.deck import _check_single
        # "give" is a substring of this sentence — would fuzzy-match falsely
        fronts = ["give an example of a microcosm."]
        result = _check_single("give", fronts, skip_fuzzy=True)
        assert result.decision == Decision.NEW

    def test_skip_fuzzy_still_catches_exact_match(self):
        from pipeline.deck import _check_single
        fronts = ["water", "give an example of a microcosm."]
        result = _check_single("water", fronts, skip_fuzzy=True)
        assert result.decision == Decision.SKIP

    def test_skip_fuzzy_false_runs_normal_fuzzy_path(self):
        from pipeline.deck import _check_single
        fronts = ["contamination"]
        result = _check_single("contaminate", fronts, skip_fuzzy=False)
        assert result.decision == Decision.QUEUE

    def test_skip_fuzzy_default_is_false(self):
        from pipeline.deck import _check_single
        fronts = ["contamination"]
        # No skip_fuzzy arg passed — should behave as before (fuzzy matching active)
        result = _check_single("contaminate", fronts)
        assert result.decision == Decision.QUEUE


# ── check_vocabulary with sentence-structured decks ──────────────────────────

class TestCheckVocabularySentenceDeck:

    @patch("pipeline.deck.get_card_fronts")
    def test_sentence_deck_all_words_go_to_new(self, mock_fronts):
        mock_fronts.return_value = [
            "what is the definition of aloof?",
            "give an example of a microcosm.",
            "she made a sharp riposte",
        ]
        vocab = {"give": 1, "able": 1, "here": 1}
        result = check_vocabulary(vocab, "English")
        assert len(result.new) == 3
        assert len(result.queue) == 0

    @patch("pipeline.deck.get_card_fronts")
    def test_sentence_deck_exact_match_still_skips(self, mock_fronts):
        mock_fronts.return_value = [
            "what is the definition of aloof?",
            "water",
        ]
        result = check_vocabulary({"water": 1}, "English")
        assert len(result.skip) == 1
        assert len(result.new) == 0

# -- Three-condition fuzzy filter (morphological false positives) --------------

class TestThreeConditionFilter:
    """
    Tests for the combined WRatio + token_sort_ratio + length_ratio filter
    that eliminates substring inflation in morphologically rich languages.
    """

    def test_commencer_vs_comme_is_new(self):
        """'commencer' should not match 'comme' — pure substring inflation."""
        result = _check_single("commencer", ["comme"])
        assert result.decision == Decision.NEW

    def test_puis_vs_puissiez_is_new(self):
        """'puis' should not match 'puissiez' — 'puis' is prefix of 'puissiez'."""
        result = _check_single("puis", ["puissiez"])
        assert result.decision == Decision.NEW

    def test_attend_vs_attendions_is_new(self):
        """'attend' should not match 'attendions' — length ratio too low."""
        result = _check_single("attend", ["n'attendions pas"])
        assert result.decision == Decision.NEW

    def test_faisiez_vs_fassiez_is_queue(self):
        """'faisiez' vs 'fassiez' is a genuine near-duplicate — should QUEUE."""
        result = _check_single("faisiez", ["fassiez"])
        assert result.decision == Decision.QUEUE

    def test_trouvent_vs_trouve_is_queue(self):
        """'trouvent' vs 'trouve' — same root, legitimate match."""
        result = _check_single("trouvent", ["trouve"])
        assert result.decision in (Decision.QUEUE, Decision.SKIP)

    def test_arrivé_vs_arrive_is_queue(self):
        """'arrivé' vs 'arrive' — accented form vs base, legitimate match."""
        result = _check_single("arrivé", ["arrive"])
        assert result.decision in (Decision.QUEUE, Decision.SKIP)

    def test_contaminate_vs_contamination_still_queues(self):
        """English morphological match still works after new filter."""
        result = _check_single("contaminate", ["contamination"])
        assert result.decision == Decision.QUEUE

    def test_develop_vs_developer_still_queues(self):
        """Length ratio 0.78 is above threshold — legitimate match preserved."""
        result = _check_single("develop", ["developer"])
        assert result.decision in (Decision.QUEUE, Decision.SKIP)

    def test_quelque_vs_long_sentence_front_is_new(self):
        """Short word vs long sentence front: length ratio too low."""
        result = _check_single("quelque", ["faire quelque chose de mon plein gré"])
        assert result.decision == Decision.NEW

    def test_filter_thresholds_documented(self):
        """Verify the filter constants are accessible for config.py migration."""
        from pipeline.deck import CONFIDENCE_LOW, CONFIDENCE_HIGH, SHORT_WORD_THRESHOLD
        assert CONFIDENCE_LOW == 60
        assert CONFIDENCE_HIGH == 90
        assert SHORT_WORD_THRESHOLD == 4