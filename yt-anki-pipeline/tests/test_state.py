"""
All tests use tmp_path to redirect the SQLite DB — no files written
to the real filesystem.

Run: pytest tests/test_state.py -m "not integration"
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import pipeline.state as state_module
from pipeline.state import (
    Session,
    VideoAlreadyProcessedError,
    _get_db,
    _now,
    check_video_not_processed,
    get_all_packages,
    get_packages_for_video,
    get_processed_videos,
    get_top_words,
    get_vocabulary_for_video,
    get_word_across_videos,
    is_video_processed,
    log_package,
    mark_video_processed,
    save_vocabulary,
)

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp file for every test."""
    monkeypatch.setattr(state_module, "DB_PATH", tmp_path / "test_state.db")
    yield tmp_path / "test_state.db"


VIDEO_ID   = "LV_NoD2M54w"
VIDEO_ID_2 = "ABC123defgh"
DECK_NAME  = "Language::English::Vocabulary"

SAMPLE_VOCAB = {
    "contaminate": 3,
    "develop":     2,
    "water":       5,
    "permanent":   1,
}

SAMPLE_POS = {
    "contaminate": "verb",
    "develop":     "verb",
    "water":       "noun",
    "permanent":   "adjective",
}

class TestNow:
    def test_returns_string(self):
        assert isinstance(_now(), str)

    def test_is_iso_format(self):
        result = _now()
        # Should parse without error
        datetime.fromisoformat(result)

    def test_is_utc_aware(self):
        result = _now()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

class TestGetDb:
    def test_creates_db_file(self, tmp_db):
        _get_db()
        assert tmp_db.exists()

    def test_creates_processed_videos_table(self):
        conn = _get_db()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "processed_videos" in names

    def test_creates_generated_packages_table(self):
        conn = _get_db()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "generated_packages" in names

    def test_creates_vocabulary_table(self):
        conn = _get_db()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "vocabulary" in names

    def test_idempotent_multiple_calls(self):
        # Should not raise on repeated calls
        _get_db()
        _get_db()
        _get_db()

class TestVideoAlreadyProcessedError:
    def test_stores_video_id(self):
        err = VideoAlreadyProcessedError(VIDEO_ID, "2026-01-01", DECK_NAME)
        assert err.video_id == VIDEO_ID

    def test_stores_processed_at(self):
        err = VideoAlreadyProcessedError(VIDEO_ID, "2026-01-01", DECK_NAME)
        assert err.processed_at == "2026-01-01"

    def test_stores_deck_name(self):
        err = VideoAlreadyProcessedError(VIDEO_ID, "2026-01-01", DECK_NAME)
        assert err.deck_name == DECK_NAME

    def test_message_contains_video_id(self):
        err = VideoAlreadyProcessedError(VIDEO_ID, "2026-01-01", DECK_NAME)
        assert VIDEO_ID in str(err)

class TestIsVideoProcessed:
    def test_returns_false_for_new_video(self):
        assert is_video_processed(VIDEO_ID) is False

    def test_returns_true_after_marking(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        assert is_video_processed(VIDEO_ID) is True

    def test_different_video_ids_independent(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        assert is_video_processed(VIDEO_ID_2) is False

class TestCheckVideoNotProcessed:
    def test_does_not_raise_for_new_video(self):
        check_video_not_processed(VIDEO_ID)  # Should not raise

    def test_raises_for_processed_video(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        with pytest.raises(VideoAlreadyProcessedError):
            check_video_not_processed(VIDEO_ID)

    def test_error_contains_correct_deck(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        with pytest.raises(VideoAlreadyProcessedError) as exc_info:
            check_video_not_processed(VIDEO_ID)
        assert exc_info.value.deck_name == DECK_NAME

    def test_error_contains_video_id(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        with pytest.raises(VideoAlreadyProcessedError) as exc_info:
            check_video_not_processed(VIDEO_ID)
        assert exc_info.value.video_id == VIDEO_ID

class TestMarkVideoProcessed:
    def test_records_video(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        assert is_video_processed(VIDEO_ID)

    def test_stores_card_count(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 42, 100)
        row = get_processed_videos()[0]
        assert row["card_count"] == 42

    def test_stores_word_count(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 99)
        row = get_processed_videos()[0]
        assert row["word_count"] == 99

    def test_stores_deck_name(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        row = get_processed_videos()[0]
        assert row["deck_name"] == DECK_NAME

    def test_upsert_updates_existing_record(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        mark_video_processed(VIDEO_ID, DECK_NAME, 20, 80)
        records = get_processed_videos()
        assert len(records) == 1
        assert records[0]["card_count"] == 20

class TestGetProcessedVideos:
    def test_returns_empty_list_initially(self):
        assert get_processed_videos() == []

    def test_returns_list_of_dicts(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        result = get_processed_videos()
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    def test_ordered_most_recent_first(self):
        mark_video_processed(VIDEO_ID,   DECK_NAME, 10, 50)
        mark_video_processed(VIDEO_ID_2, DECK_NAME, 5,  20)
        result = get_processed_videos()
        assert result[0]["video_id"] == VIDEO_ID_2

    def test_all_expected_keys_present(self):
        mark_video_processed(VIDEO_ID, DECK_NAME, 10, 50)
        row = get_processed_videos()[0]
        for key in ["video_id", "processed_at", "deck_name", "card_count", "word_count"]:
            assert key in row

class TestLogPackage:
    def test_returns_integer_id(self):
        result = log_package(VIDEO_ID, Path("output/test.apkg"), DECK_NAME, 10)
        assert isinstance(result, int)
        assert result > 0

    def test_multiple_packages_same_video(self):
        log_package(VIDEO_ID, Path("output/a.apkg"), DECK_NAME, 5)
        log_package(VIDEO_ID, Path("output/b.apkg"), DECK_NAME, 8)
        packages = get_packages_for_video(VIDEO_ID)
        assert len(packages) == 2

    def test_autoincrement_ids_unique(self):
        id1 = log_package(VIDEO_ID,   Path("output/a.apkg"), DECK_NAME, 5)
        id2 = log_package(VIDEO_ID_2, Path("output/b.apkg"), DECK_NAME, 3)
        assert id1 != id2

    def test_stores_file_path_as_string(self):
        log_package(VIDEO_ID, Path("output/test.apkg"), DECK_NAME, 10)
        packages = get_packages_for_video(VIDEO_ID)
        assert packages[0]["file_path"] == "output/test.apkg"

    def test_stores_card_count(self):
        log_package(VIDEO_ID, Path("output/test.apkg"), DECK_NAME, 42)
        packages = get_packages_for_video(VIDEO_ID)
        assert packages[0]["card_count"] == 42

class TestGetPackagesForVideo:
    def test_returns_empty_for_unknown_video(self):
        assert get_packages_for_video(VIDEO_ID) == []

    def test_returns_only_matching_video(self):
        log_package(VIDEO_ID,   Path("output/a.apkg"), DECK_NAME, 5)
        log_package(VIDEO_ID_2, Path("output/b.apkg"), DECK_NAME, 3)
        result = get_packages_for_video(VIDEO_ID)
        assert all(r["video_id"] == VIDEO_ID for r in result)

    def test_ordered_most_recent_first(self):
        log_package(VIDEO_ID, Path("output/a.apkg"), DECK_NAME, 5)
        log_package(VIDEO_ID, Path("output/b.apkg"), DECK_NAME, 8)
        result = get_packages_for_video(VIDEO_ID)
        assert result[0]["file_path"] == "output/b.apkg"

class TestGetAllPackages:
    def test_returns_empty_initially(self):
        assert get_all_packages() == []

    def test_returns_all_packages(self):
        log_package(VIDEO_ID,   Path("output/a.apkg"), DECK_NAME, 5)
        log_package(VIDEO_ID_2, Path("output/b.apkg"), DECK_NAME, 3)
        assert len(get_all_packages()) == 2

    def test_ordered_most_recent_first(self):
        log_package(VIDEO_ID,   Path("output/a.apkg"), DECK_NAME, 5)
        log_package(VIDEO_ID_2, Path("output/b.apkg"), DECK_NAME, 3)
        result = get_all_packages()
        assert result[0]["video_id"] == VIDEO_ID_2

class TestSaveVocabulary:
    def test_saves_all_entries(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        assert len(rows) == len(SAMPLE_VOCAB)

    def test_lemmas_correct(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        lemmas = {r["lemma"] for r in rows}
        assert lemmas == set(SAMPLE_VOCAB.keys())

    def test_frequencies_correct(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        freq_map = {r["lemma"]: r["frequency"] for r in rows}
        for lemma, freq in SAMPLE_VOCAB.items():
            assert freq_map[lemma] == freq

    def test_positions_in_order(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        positions = [r["position"] for r in rows]
        assert positions == sorted(positions)

    def test_first_lemma_at_position_zero(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        assert rows[0]["position"] == 0
        assert rows[0]["lemma"] == list(SAMPLE_VOCAB.keys())[0]

    def test_pos_map_stored(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB, SAMPLE_POS)
        rows = get_vocabulary_for_video(VIDEO_ID)
        pos_map = {r["lemma"]: r["part_of_speech"] for r in rows}
        assert pos_map["contaminate"] == "verb"
        assert pos_map["water"] == "noun"

    def test_no_pos_map_stores_none(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        for row in rows:
            assert row["part_of_speech"] is None

    def test_same_lemma_different_videos(self):
        save_vocabulary(VIDEO_ID,   {"water": 5})
        save_vocabulary(VIDEO_ID_2, {"water": 2})
        r1 = get_vocabulary_for_video(VIDEO_ID)
        r2 = get_vocabulary_for_video(VIDEO_ID_2)
        assert r1[0]["frequency"] == 5
        assert r2[0]["frequency"] == 2

    def test_rerun_accumulates_frequency(self):
        save_vocabulary(VIDEO_ID, {"water": 5})
        save_vocabulary(VIDEO_ID, {"water": 3})
        rows = get_vocabulary_for_video(VIDEO_ID)
        assert rows[0]["frequency"] == 8

    def test_empty_vocabulary_does_not_crash(self):
        save_vocabulary(VIDEO_ID, {})  # Should log warning and return
        assert get_vocabulary_for_video(VIDEO_ID) == []

class TestGetVocabularyForVideo:
    def test_returns_empty_for_unknown_video(self):
        assert get_vocabulary_for_video(VIDEO_ID) == []

    def test_ordered_by_position(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        positions = [r["position"] for r in rows]
        assert positions == sorted(positions)

    def test_returns_list_of_dicts(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_vocabulary_for_video(VIDEO_ID)
        assert all(isinstance(r, dict) for r in rows)

class TestGetWordAcrossVideos:
    def test_returns_empty_for_unknown_word(self):
        assert get_word_across_videos("philosophy") == []

    def test_finds_word_in_multiple_videos(self):
        save_vocabulary(VIDEO_ID,   {"water": 5})
        save_vocabulary(VIDEO_ID_2, {"water": 2})
        rows = get_word_across_videos("water")
        assert len(rows) == 2

    def test_returns_only_matching_lemma(self):
        save_vocabulary(VIDEO_ID, {"water": 5, "develop": 2})
        rows = get_word_across_videos("water")
        assert all(r["lemma"] == "water" for r in rows)

    def test_ordered_by_added_at(self):
        save_vocabulary(VIDEO_ID,   {"water": 5})
        save_vocabulary(VIDEO_ID_2, {"water": 2})
        rows = get_word_across_videos("water")
        dates = [r["added_at"] for r in rows]
        assert dates == sorted(dates)

class TestGetTopWords:
    def test_returns_empty_for_unknown_video(self):
        assert get_top_words(VIDEO_ID) == []

    def test_ordered_by_frequency_descending(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_top_words(VIDEO_ID)
        freqs = [r["frequency"] for r in rows]
        assert freqs == sorted(freqs, reverse=True)

    def test_highest_frequency_first(self):
        save_vocabulary(VIDEO_ID, SAMPLE_VOCAB)
        rows = get_top_words(VIDEO_ID)
        assert rows[0]["lemma"] == "water"   # frequency 5 is highest

    def test_limit_respected(self):
        vocab = {f"word{i}": i for i in range(30)}
        save_vocabulary(VIDEO_ID, vocab)
        rows = get_top_words(VIDEO_ID, limit=10)
        assert len(rows) == 10

    def test_default_limit_is_twenty(self):
        vocab = {f"word{i}": i for i in range(30)}
        save_vocabulary(VIDEO_ID, vocab)
        rows = get_top_words(VIDEO_ID)
        assert len(rows) == 20

class TestSession:
    def test_initial_deck_is_none(self):
        session = Session()
        assert session.deck_name is None

    def test_is_ready_false_initially(self):
        assert Session().is_ready is False

    def test_set_deck(self):
        session = Session()
        session.set_deck(DECK_NAME)
        assert session.deck_name == DECK_NAME

    def test_is_ready_after_set_deck(self):
        session = Session()
        session.set_deck(DECK_NAME)
        assert session.is_ready is True

    def test_clear_resets_deck(self):
        session = Session()
        session.set_deck(DECK_NAME)
        session.clear()
        assert session.deck_name is None

    def test_is_ready_false_after_clear(self):
        session = Session()
        session.set_deck(DECK_NAME)
        session.clear()
        assert session.is_ready is False

    def test_multiple_sessions_independent(self):
        s1 = Session()
        s2 = Session()
        s1.set_deck("Deck::A")
        s2.set_deck("Deck::B")
        assert s1.deck_name == "Deck::A"
        assert s2.deck_name == "Deck::B"

    def test_session_not_persisted_to_db(self, tmp_db):
        session = Session()
        session.set_deck(DECK_NAME)
        # DB should have no session table
        conn = sqlite3.connect(tmp_db)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='session_deck'"
        ).fetchall()
        conn.close()
        assert tables == []