"""
test_antonyms.py

Offline ConceptNet antonym index: build, lookup, and graceful degradation.

Every test builds its own miniature index in tmp_path from an inline dump,
so the default run needs no download, no network, and none of the real
498 MB assertions file (CLAUDE.md 3.5).

The dump format is five tab-separated columns:

    /a/[...]  /r/Antonym  /c/<lang>/<term>[/<pos>]  /c/<lang>/<term>  {json}

Run unit tests:  pytest tests/test_antonyms.py -m "not integration"
"""

from __future__ import annotations

import gzip
import sqlite3

import pytest

import pipeline.antonyms as antonyms
from pipeline.antonyms import (
    AntonymBuildError,
    MAX_ANTONYMS,
    build_index,
    index_path,
    is_available,
    lookup,
)


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_dict_dir(tmp_path, monkeypatch):
    """Point the module at a throwaway directory and clear its connection cache."""
    monkeypatch.setattr(antonyms, "DICT_DIR", tmp_path / "dictionaries")
    antonyms.close()
    yield
    antonyms.close()


_META = '{"dataset": "/d/wiktionary/fr", "license": "cc:by-sa/4.0", "weight": 1.0}'


def _edge(left, right, relation="/r/Antonym"):
    """Build one dump row. `left` and `right` are full /c/... URIs."""
    return f"/a/[{relation}/,{left}/,{right}/]\t{relation}\t{left}\t{right}\t{_META}"


def _dump(tmp_path, rows, name="assertions.csv.gz"):
    """Write rows to a gzipped dump and return its path."""
    path = tmp_path / name
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n")
    return path


@pytest.fixture
def built(tmp_path):
    """A small index covering French, German and one cross-language edge."""
    rows = [
        _edge("/c/fr/grand/a", "/c/fr/petit"),
        _edge("/c/fr/grand/n", "/c/fr/nain"),
        _edge("/c/fr/accord/n", "/c/fr/désaccord"),
        _edge("/c/fr/s’_en_retourner/v", "/c/fr/arriver"),
        _edge("/c/de/gross/a", "/c/de/klein"),
    ]
    build_index(source=_dump(tmp_path, rows), languages=["fr", "de"])
    return rows


# -- Building -----------------------------------------------------------------

class TestBuild:
    def test_build_writes_an_index_that_reports_available(self, tmp_path):
        assert not is_available()
        build_index(source=_dump(tmp_path, [_edge("/c/fr/grand/a", "/c/fr/petit")]),
                    languages=["fr"])
        assert is_available()
        assert index_path().exists()

    def test_build_returns_the_row_count(self, tmp_path):
        rows = [_edge("/c/fr/grand/a", "/c/fr/petit"), _edge("/c/de/gross/a", "/c/de/klein")]
        # Two edges, each storing both directions: four (lang, word, pos) rows.
        assert build_index(source=_dump(tmp_path, rows), languages=["fr", "de"]) == 4

    def test_pairs_are_stored_in_both_directions(self, built):
        assert "petit" in lookup("grand", "fr")
        assert "grand" in lookup("petit", "fr")

    def test_a_dump_with_no_antonym_edges_is_an_error(self, tmp_path):
        rows = [_edge("/c/fr/chien", "/c/fr/chat", relation="/r/DistinctFrom")]
        with pytest.raises(AntonymBuildError):
            build_index(source=_dump(tmp_path, rows), languages=["fr"])

    def test_languages_outside_the_wanted_set_are_dropped(self, tmp_path):
        rows = [
            _edge("/c/fr/grand/a", "/c/fr/petit"),
            _edge("/c/cs/velky/a", "/c/cs/maly"),
        ]
        build_index(source=_dump(tmp_path, rows), languages=["fr"])
        assert lookup("velky", "cs") == []
        assert lookup("grand", "fr") == ["petit"]

    def test_a_failed_build_leaves_no_partial_index(self, tmp_path):
        with pytest.raises(AntonymBuildError):
            build_index(source=_dump(tmp_path, ["not a dump row at all"]), languages=["fr"])
        assert not index_path().exists()


# -- Filtering, which is where constraint 3.3 is enforced ---------------------

class TestFiltering:
    def test_cross_language_edges_never_reach_the_index(self, tmp_path):
        """A French antonym must not be reachable from a German word."""
        rows = [
            _edge("/c/de/gross/a", "/c/fr/petit"),   # cross-language, must be dropped
            _edge("/c/de/gross/a", "/c/de/klein"),   # same-language, must be kept
        ]
        build_index(source=_dump(tmp_path, rows), languages=["fr", "de"])
        assert lookup("gross", "de") == ["klein"]
        assert "petit" not in lookup("gross", "de")

    def test_a_word_is_never_its_own_antonym(self, tmp_path):
        rows = [
            _edge("/c/fr/grande/a", "/c/fr/grande"),
            _edge("/c/fr/grande/a", "/c/fr/petite"),
        ]
        build_index(source=_dump(tmp_path, rows), languages=["fr"])
        assert lookup("grande", "fr") == ["petite"]

    def test_other_relations_are_ignored(self, tmp_path):
        rows = [
            _edge("/c/fr/chien/n", "/c/fr/chat", relation="/r/DistinctFrom"),
            _edge("/c/fr/grand/a", "/c/fr/petit"),
        ]
        build_index(source=_dump(tmp_path, rows), languages=["fr"])
        assert lookup("chien", "fr") == []


class TestParse:
    """
    `_parse` is tested directly as well as through a build.

    The build path skips non-antonym rows on a byte comparison before
    `_parse` ever sees them, so a build-level test cannot fail if `_parse`'s
    own relation check is removed. Two filters, two tests: this is the
    matched pair CLAUDE.md 5 asks for, on a rule rather than a variable.
    """

    WANTED = {"fr", "de"}

    def test_an_antonym_edge_parses(self):
        row = _edge("/c/fr/grand/a", "/c/fr/petit")
        assert antonyms._parse(row, self.WANTED) == ("fr", "grand", "a", "petit", "")

    def test_another_relation_is_rejected(self):
        row = _edge("/c/fr/chien/n", "/c/fr/chat", relation="/r/DistinctFrom")
        assert antonyms._parse(row, self.WANTED) is None

    def test_a_cross_language_edge_is_rejected(self):
        row = _edge("/c/de/gross/a", "/c/fr/petit")
        assert antonyms._parse(row, self.WANTED) is None

    def test_a_self_pair_is_rejected(self):
        row = _edge("/c/fr/grande/a", "/c/fr/grande")
        assert antonyms._parse(row, self.WANTED) is None

    def test_an_unwanted_language_is_rejected(self):
        row = _edge("/c/cs/velky/a", "/c/cs/maly")
        assert antonyms._parse(row, self.WANTED) is None

    def test_a_truncated_row_is_rejected(self):
        assert antonyms._parse("only\tthree\tcolumns", self.WANTED) is None


# -- Lookup -------------------------------------------------------------------

class TestLookup:
    def test_missing_index_returns_an_empty_list(self):
        assert lookup("grand", "fr") == []

    def test_unknown_word_returns_an_empty_list(self, built):
        assert lookup("verbatim", "fr") == []

    def test_lookup_is_case_insensitive(self, built):
        assert lookup("GRAND", "fr") == lookup("grand", "fr") != []

    def test_multiword_lemmas_match_the_underscore_form(self, tmp_path):
        build_index(
            source=_dump(tmp_path, [_edge("/c/fr/tout_de_suite/r", "/c/fr/plus_tard")]),
            languages=["fr"],
        )
        assert lookup("tout de suite", "fr") == ["plus tard"]

    def test_the_language_selects_the_row(self, built):
        assert lookup("grand", "de") == []
        assert lookup("gross", "fr") == []

    def test_results_are_capped(self, tmp_path):
        rows = [_edge("/c/fr/grand/a", f"/c/fr/petit{n}") for n in range(MAX_ANTONYMS + 3)]
        build_index(source=_dump(tmp_path, rows), languages=["fr"])
        assert len(lookup("grand", "fr")) == MAX_ANTONYMS

    def test_a_corrupt_index_degrades_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(antonyms, "DICT_DIR", tmp_path)
        index_path().write_text("this is not a database")
        assert lookup("grand", "fr") == []


# -- Part of speech -----------------------------------------------------------

class TestPartOfSpeech:
    def test_the_matching_part_of_speech_is_preferred(self, built):
        """`grand` is an adjective here and a noun there; ADJ picks the adjective."""
        assert lookup("grand", "fr", pos="ADJ") == ["petit"]
        assert lookup("grand", "fr", pos="NOUN") == ["nain"]

    def test_an_unmatched_part_of_speech_still_returns_something(self, built):
        """Preferring a sense must never empty a field that has content."""
        both = lookup("grand", "fr", pos="ADV")
        assert sorted(both) == ["nain", "petit"]

    def test_no_part_of_speech_returns_every_sense(self, built):
        assert sorted(lookup("grand", "fr")) == ["nain", "petit"]


# -- Term shapes --------------------------------------------------------------

class TestDisplay:
    def test_underscores_become_spaces(self, tmp_path):
        build_index(
            source=_dump(tmp_path, [_edge("/c/fr/arriver/v", "/c/fr/dans_le_temps")]),
            languages=["fr"],
        )
        assert lookup("arriver", "fr") == ["dans le temps"]

    def test_an_elision_keeps_its_apostrophe_closed(self, built):
        """ConceptNet writes `s’_en_retourner`; a card must not show `s’ en retourner`."""
        assert lookup("arriver", "fr") == ["s’en retourner"]


# -- Availability -------------------------------------------------------------

class TestAvailability:
    def test_absent_index_is_not_available(self):
        assert is_available() is False

    def test_empty_index_is_not_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(antonyms, "DICT_DIR", tmp_path)
        conn = sqlite3.connect(index_path())
        conn.execute("CREATE TABLE antonyms (lang TEXT, word TEXT, pos TEXT, antonyms TEXT)")
        conn.execute(f"PRAGMA user_version = {antonyms._SCHEMA_VERSION}")
        conn.commit()
        conn.close()
        assert is_available() is False

    def test_an_older_schema_is_refused(self, built, monkeypatch):
        monkeypatch.setattr(antonyms, "_SCHEMA_VERSION", antonyms._SCHEMA_VERSION + 1)
        assert is_available() is False
