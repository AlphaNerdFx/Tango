"""
test_wiktdata.py

Offline Wiktionary index: build, lookup, and graceful degradation.

Every test builds its own miniature index in tmp_path from an inline
archive, so the default run needs no download, no network, and none of the
real 323 MB index (CLAUDE.md 3.5).

Run unit tests:  pytest tests/test_wiktdata.py -m "not integration"
"""

from __future__ import annotations

import gzip
import json
import sqlite3

import pytest

import pipeline.wiktdata as wiktdata
from pipeline.wiktdata import (
    DictionaryBuildError,
    DictionaryDownloadError,
    build_index,
    is_available,
    index_path,
    lookup,
)


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_dict_dir(tmp_path, monkeypatch):
    """Point the module at a throwaway directory and clear its connection cache."""
    monkeypatch.setattr(wiktdata, "DICT_DIR", tmp_path / "dictionaries")
    # Connections are cached per thread; a stale one would point at a
    # previous test's index.
    if hasattr(wiktdata._local, "conns"):
        for conn in wiktdata._local.conns.values():
            if conn is not None:
                conn.close()
        del wiktdata._local.conns
    yield
    if hasattr(wiktdata._local, "conns"):
        for conn in wiktdata._local.conns.values():
            if conn is not None:
                conn.close()
        del wiktdata._local.conns


def _record(word, lang_code="fr", gloss="Une definition.", examples=(),
            synonyms=(), antonyms=(), pos="noun"):
    return {
        "word": word,
        "lang_code": lang_code,
        "pos": pos,
        "senses": [{
            "glosses": [gloss],
            "examples": [{"text": t} for t in examples],
        }],
        "synonyms": [{"word": w} for w in synonyms],
        "antonyms": [{"word": w} for w in antonyms],
    }


def _archive(tmp_path, records) -> "object":
    path = tmp_path / "extract.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


# -- Graceful degradation -----------------------------------------------------

class TestNoIndex:

    def test_is_available_false_when_never_built(self):
        assert is_available("fr") is False

    def test_lookup_returns_none_without_an_index(self):
        # Must degrade, not raise: every other optional source in this
        # project behaves this way, and the pipeline has to keep working
        # for anyone who never runs --build-dictionary.
        assert lookup("maison", "fr") is None

    def test_lookup_returns_none_for_unbuilt_language(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        assert lookup("Haus", "de") is None


# -- Building -----------------------------------------------------------------

class TestBuildIndex:

    def test_builds_and_reports_count(self, tmp_path):
        count = build_index("fr", archive=_archive(tmp_path, [
            _record("maison"), _record("eau"),
        ]))
        assert count == 2
        assert is_available("fr") is True

    def test_filters_out_other_languages(self, tmp_path):
        # The French extract also documents German, Occitan and others *in*
        # French. Only real target-language words belong in the index --
        # this discards roughly 70% of the real file.
        count = build_index("fr", archive=_archive(tmp_path, [
            _record("maison", lang_code="fr"),
            _record("Haus", lang_code="de"),
            _record("casa", lang_code="oc"),
        ]))
        assert count == 1
        assert lookup("maison", "fr") is not None
        assert lookup("Haus", "fr") is None

    def test_records_without_a_gloss_are_skipped(self, tmp_path):
        bare = {"word": "vide", "lang_code": "fr", "senses": [{}]}
        count = build_index("fr", archive=_archive(tmp_path, [_record("maison"), bare]))
        assert count == 1

    def test_malformed_lines_do_not_abort_the_build(self, tmp_path):
        # A truncated line must not fail a multi-million-line build.
        path = tmp_path / "extract.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps(_record("maison")) + "\n")
            fh.write('{"word": "broken", "lang_c\n')
            fh.write(json.dumps(_record("eau")) + "\n")
        assert build_index("fr", archive=path) == 2

    def test_empty_result_raises_rather_than_leaving_a_dead_index(self, tmp_path):
        with pytest.raises(DictionaryBuildError, match="No 'fr' entries"):
            build_index("fr", archive=_archive(tmp_path, [_record("Haus", lang_code="de")]))
        # A failed build must not leave something is_available() calls usable.
        assert is_available("fr") is False

    def test_interrupted_build_leaves_no_partial_index(self, tmp_path):
        path = tmp_path / "corrupt.jsonl.gz"
        path.write_bytes(b"not actually gzip")
        with pytest.raises(DictionaryBuildError):
            build_index("fr", archive=path)
        assert not index_path("fr").exists()

    def test_download_failure_is_a_typed_error(self, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError("network down")
        monkeypatch.setattr(wiktdata.urllib.request, "urlretrieve", _boom)
        with pytest.raises(DictionaryDownloadError, match="Could not download"):
            build_index("fr")

    def test_progress_callback_receives_status_lines(self, tmp_path):
        seen: list = []
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]),
                    progress=seen.append)
        assert any("Indexed" in line for line in seen)


# -- Lookup -------------------------------------------------------------------

class TestLookup:

    def test_returns_all_fields(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [
            _record("maison", gloss="Batiment servant de logis.",
                    examples=["Une belle maison.", "La maison est grande."],
                    synonyms=["demeure", "logis"], antonyms=["dehors"], pos="noun"),
        ]))
        entry = lookup("maison", "fr")
        assert entry.definition == "Batiment servant de logis."
        assert entry.part_of_speech == "noun"
        assert entry.example1 == "Une belle maison."
        assert entry.example2 == "La maison est grande."
        assert entry.synonyms == ["demeure", "logis"]
        assert entry.antonyms == ["dehors"]

    def test_lookup_is_case_insensitive(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        assert lookup("MAISON", "fr") is not None

    def test_missing_word_returns_none(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        assert lookup("xyzqwerty", "fr") is None

    def test_empty_optional_fields_come_back_as_none_not_blank(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("eau")]))
        entry = lookup("eau", "fr")
        assert entry.example1 is None
        assert entry.example2 is None
        assert entry.synonyms == []
        assert entry.antonyms == []

    # -- Apostrophe normalisation ---------------------------------------------
    #
    # Matched pair. Wiktionary stores French elisions with a typographic
    # apostrophe, so an ASCII quote silently misses an entry that is
    # definitely present -- on one of the most common words in the language.
    # These fail in opposite directions if either spelling stops working.

    def test_ascii_apostrophe_finds_typographic_entry(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("aujourd’hui")]))
        assert lookup("aujourd'hui", "fr") is not None

    def test_typographic_apostrophe_finds_ascii_entry(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("aujourd'hui")]))
        assert lookup("aujourd’hui", "fr") is not None


# -- Schema versioning --------------------------------------------------------

class TestSchemaVersion:

    def test_stale_schema_is_rejected_rather_than_queried(self, tmp_path):
        # Querying an old layout with new column names would raise deep
        # inside a lookup. Better to report it unavailable and ask for a
        # rebuild.
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        conn = sqlite3.connect(index_path("fr"))
        conn.execute(f"PRAGMA user_version = {wiktdata._SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        assert is_available("fr") is False
