"""
test_definition.py

All HTTP calls and SQLite operations use temp fixtures.
No network access or API keys required for the unit suite.

Run unit tests:         pytest tests/test_definition.py -m "not integration"
Run all (needs keys):   pytest tests/test_definition.py
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import pipeline.definition as def_module
from pipeline.definition import (
    DefinitionBatchResult,
    DefinitionResult,
    _cache_get,
    _cache_set,
    _cache_key,
    _cache_set_key,
    _find_transcript_sentence,
    _parse_dictapi_response,
    _parse_mw_response,
    _strip_mw_markup,
    fetch_definition,
    fetch_definitions,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect all SQLite operations to a temp DB for each test."""
    monkeypatch.setattr(def_module, "DB_PATH", tmp_path / "test.db")
    yield


@pytest.fixture(autouse=True)
def no_offline_dictionary(monkeypatch):
    """
    Pretend no offline Wiktionary index exists, unless a test says otherwise.

    Without this, results depend on whether the developer happens to have
    run --build-dictionary: the real index lives on disk and
    fetch_definition() consults it, so the same test passes in CI (no
    index) and fails locally (index present). Tests that exercise the
    index build their own via the wiktdata fixtures instead.
    """
    monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _lang: False)
    monkeypatch.setattr(def_module.wiktdata, "lookup", lambda _w, _lang, pos=None: None)
    yield


@pytest.fixture(autouse=True)
def reset_circuit_breaker_state():
    """
    Reset circuit breaker state before and after each test so a source
    tripped in one test doesn't leak into the next.
    """
    def_module.reset_circuit_breaker()
    yield
    def_module.reset_circuit_breaker()


@pytest.fixture
def sample_snippets() -> dict:
    return {
        0.0: {"end": 3.5,  "text": "So companies had to develop permanent solutions"},
        3.5: {"end": 7.1,  "text": "contaminated water gave rise to new regulations"},
        7.1: {"end": 10.0, "text": "the permanent photographic record was preserved"},
        "_full_text":     "So companies had to develop permanent solutions contaminated water",
        "_language_code": "en",
        "_snippet_count": 3,
    }


@pytest.fixture
def sample_definition_result() -> DefinitionResult:
    return DefinitionResult(
        lemma="contaminate",
        definition="to make impure or unsafe by contact",
        example_dict="the water supply was contaminated",
        example_dict2="the contaminated river posed health risks",
        example_transcript="contaminated water gave rise to new regulations",
        synonyms=["pollute", "taint"],
        antonyms=["purify"],
        part_of_speech="verb",
        source="merriam-webster",
    )


@pytest.fixture
def mw_response() -> list:
    """Minimal valid MW Collegiate API response for 'contaminate'."""
    return [
        {
            "meta": {"id": "contaminate"},
            "fl": "verb",
            "shortdef": ["to make impure or unsafe by contact"],
            "def": [
                {
                    "sseq": [
                        [
                            [
                                "sense",
                                {
                                    "dt": [
                                        ["text", "{bc}to make impure or unsafe"],
                                        [
                                            "vis",
                                            [{"t": "the {it}contaminated{/it} water supply"}],
                                        ],
                                    ]
                                },
                            ]
                        ]
                    ]
                }
            ],
            "syns": [],
        }
    ]


@pytest.fixture
def dictapi_response() -> list:
    """Minimal valid dictionaryapi.dev response for 'contaminate'."""
    return [
        {
            "word": "contaminate",
            "meanings": [
                {
                    "partOfSpeech": "verb",
                    "definitions": [
                        {
                            "definition": "to make something impure by exposure to a pollutant",
                            "example": "the river was contaminated by factory waste",
                            "synonyms": ["pollute", "taint", "infect"],
                            "antonyms": ["purify", "clean"],
                        }
                    ],
                    "synonyms": ["pollute"],
                    "antonyms": ["purify"],
                }
            ],
        }
    ]


# ── _strip_mw_markup ──────────────────────────────────────────────────────────

class TestStripMwMarkup:

    def test_strips_bc_token(self):
        assert _strip_mw_markup("{bc}to make impure") == "to make impure"

    def test_strips_it_keeps_inner_text(self):
        assert _strip_mw_markup("the {it}contaminated{/it} water") == "the contaminated water"

    def test_strips_b_keeps_inner_text(self):
        assert _strip_mw_markup("{b}synonym{/b} of dirty") == "synonym of dirty"

    def test_strips_sx_keeps_word(self):
        assert _strip_mw_markup("see {sx|pollute||}") == "see pollute"

    def test_strips_unknown_tokens(self):
        # {dx}...{/dx} inner text is preserved — only the tags are stripped
        result = _strip_mw_markup("text {dx}cross-ref{/dx} more")
        assert "{dx}" not in result
        assert "cross-ref" in result
        assert "text" in result

    def test_collapses_whitespace(self):
        assert _strip_mw_markup("word   with    spaces") == "word with spaces"

    def test_empty_string_returns_empty(self):
        assert _strip_mw_markup("") == ""

    def test_no_markup_unchanged(self):
        assert _strip_mw_markup("plain text here") == "plain text here"


# ── _find_transcript_sentence ─────────────────────────────────────────────────

class TestFindTranscriptSentence:

    def test_finds_exact_lemma(self, sample_snippets):
        result = _find_transcript_sentence("develop", sample_snippets)
        assert result == "So companies had to develop permanent solutions"

    def test_finds_inflected_form(self, sample_snippets):
        # "contaminate" should match "contaminated"
        result = _find_transcript_sentence("contaminate", sample_snippets)
        assert "contaminated" in result

    def test_returns_none_when_not_found(self, sample_snippets):
        result = _find_transcript_sentence("philosophy", sample_snippets)
        assert result is None

    def test_ignores_metadata_keys(self, sample_snippets):
        # Should not crash on string keys like "_full_text"
        result = _find_transcript_sentence("full", sample_snippets)
        # "_full_text" key is a string, not a float — should be skipped
        # "full" doesn't appear in any snippet text either
        assert result is None

    def test_returns_first_occurrence(self, sample_snippets):
        # "permanent" appears in snippets at 0.0 and 7.1
        result = _find_transcript_sentence("permanent", sample_snippets)
        assert result == "So companies had to develop permanent solutions"

    def test_case_insensitive(self, sample_snippets):
        result = _find_transcript_sentence("DEVELOP", sample_snippets)
        assert result is not None

    def test_empty_snippets_returns_none(self):
        result = _find_transcript_sentence("water", {})
        assert result is None


# ── _parse_mw_response ────────────────────────────────────────────────────────

class TestParseMwResponse:

    def test_returns_definition_result(self, mw_response, sample_snippets):
        result = _parse_mw_response("contaminate", mw_response, sample_snippets)
        assert isinstance(result, DefinitionResult)

    def test_correct_lemma(self, mw_response, sample_snippets):
        result = _parse_mw_response("contaminate", mw_response, sample_snippets)
        assert result.lemma == "contaminate"

    def test_correct_pos(self, mw_response, sample_snippets):
        result = _parse_mw_response("contaminate", mw_response, sample_snippets)
        assert result.part_of_speech == "verb"

    def test_definition_stripped_of_markup(self, mw_response, sample_snippets):
        result = _parse_mw_response("contaminate", mw_response, sample_snippets)
        assert "{" not in result.definition

    def test_example_dict_extracted(self, mw_response, sample_snippets):
        result = _parse_mw_response("contaminate", mw_response, sample_snippets)
        assert result.example_dict is not None
        assert "contaminated" in result.example_dict

    def test_example_transcript_from_snippets(self, mw_response, sample_snippets):
        result = _parse_mw_response("contaminate", mw_response, sample_snippets)
        assert result.example_transcript is not None
        assert "contaminated" in result.example_transcript

    def test_source_is_merriam_webster(self, mw_response, sample_snippets):
        result = _parse_mw_response("contaminate", mw_response, sample_snippets)
        assert result.source == "merriam-webster"

    def test_returns_none_when_spelling_suggestions(self, sample_snippets):
        # MW returns list of strings when word not found
        result = _parse_mw_response("xyzqwerty", ["similar", "word", "list"], sample_snippets)
        assert result is None

    def test_returns_none_on_empty_response(self, sample_snippets):
        result = _parse_mw_response("contaminate", [], sample_snippets)
        assert result is None

    def test_returns_none_when_no_shortdef(self, sample_snippets):
        response = [{"fl": "verb", "shortdef": []}]
        result = _parse_mw_response("contaminate", response, sample_snippets)
        assert result is None

    def test_synonyms_capped_at_five(self, sample_snippets):
        # Build response where synonym extraction would give many
        result = _parse_mw_response("contaminate", [
            {"fl": "verb", "shortdef": ["to pollute"], "def": [], "syns": []}
        ], sample_snippets)
        assert len(result.synonyms) <= 5

    # -- Synonym Discussion parsing (issue #10) ------------------------------
    # MW's real 'syns' field for words with a Synonym Discussion is full
    # prose, not a comma-separated list — synonym words are individually
    # marked {sc}word{/sc} inside complete sentences. This fixture is the
    # real MW Collegiate API response structure for "ask" (fetched directly
    # against the live API during investigation), not a synthetic guess.

    ASK_SYNS_RESPONSE = [{
        "fl": "verb",
        "shortdef": ["to call on for an answer"],
        "def": [],
        "syns": [
            {
                "pl": "synonyms",
                "pt": [
                    ["text", "{sc}ask{/sc} {sc}question{/sc} {sc}interrogate{/sc} "
                             "{sc}query{/sc} {sc}inquire{/sc} mean to address a "
                             "person in order to gain information. {sc}ask{/sc} "
                             "implies no more than the putting of a question. "],
                    ["vis", [{"t": "{it}ask{/it} for directions"}]],
                    ["text", " {sc}question{/sc} usually suggests the asking of "
                             "series of questions. "],
                    ["vis", [{"t": "{it}questioned{/it} them"}]],
                ],
            },
            {
                "pl": "synonyms",
                "pt": [
                    ["text", "{sc}ask{/sc} {sc}request{/sc} {sc}solicit{/sc} mean "
                             "to seek to obtain by making one's wants known. "
                             "{sc}ask{/sc} implies no more than the statement of "
                             "the desire. "],
                    ["vis", [{"t": "{it}ask{/it} a favor of a friend"}]],
                ],
            },
        ],
    }]

    def test_synonym_discussion_produces_individual_words(self, sample_snippets):
        result = _parse_mw_response("ask", self.ASK_SYNS_RESPONSE, sample_snippets)
        # Every extracted synonym must be a single word/short phrase, never
        # an entire explanatory sentence pulled in whole because no comma
        # happened to appear in it.
        for syn in result.synonyms:
            assert " mean " not in syn, f"'{syn}' looks like unparsed prose, not a word"
            assert len(syn) < 20, f"'{syn}' is suspiciously long for a single synonym"

    def test_synonym_discussion_headword_excluded(self, sample_snippets):
        result = _parse_mw_response("ask", self.ASK_SYNS_RESPONSE, sample_snippets)
        assert "ask" not in [s.lower() for s in result.synonyms]

    def test_synonym_discussion_expected_words_present(self, sample_snippets):
        result = _parse_mw_response("ask", self.ASK_SYNS_RESPONSE, sample_snippets)
        lowered = [s.lower() for s in result.synonyms]
        # First 5 non-headword {sc} words across both syn groups, in order:
        # question, interrogate, query, inquire, request (solicit is 6th,
        # dropped by the synonyms[:5] cap).
        assert lowered == ["question", "interrogate", "query", "inquire", "request"]

    def test_synonym_discussion_dedupes_across_groups(self, sample_snippets):
        # "ask" (the headword) and "question" both repeat across the two
        # syn groups / multiple text segments — must not appear twice.
        result = _parse_mw_response("ask", self.ASK_SYNS_RESPONSE, sample_snippets)
        lowered = [s.lower() for s in result.synonyms]
        assert len(lowered) == len(set(lowered))

    def test_empty_syns_still_returns_empty_list(self, sample_snippets):
        # Regression guard: words with no Synonym Discussion (e.g. "ache",
        # "too" — verified against the live API) must still return an empty
        # list cleanly, not error, so the WordNet fallback can take over.
        result = _parse_mw_response("ache", [
            {"fl": "verb", "shortdef": ["to hurt"], "def": [], "syns": []}
        ], sample_snippets)
        assert result.synonyms == []

    def test_no_snippets_gives_no_transcript_example(self, mw_response):
        result = _parse_mw_response("contaminate", mw_response, None)
        assert result.example_transcript is None


# ── _parse_dictapi_response ───────────────────────────────────────────────────

class TestParseDictapiResponse:

    def test_returns_definition_result(self, dictapi_response, sample_snippets):
        result = _parse_dictapi_response("contaminate", dictapi_response, sample_snippets)
        assert isinstance(result, DefinitionResult)

    def test_correct_pos(self, dictapi_response, sample_snippets):
        result = _parse_dictapi_response("contaminate", dictapi_response, sample_snippets)
        assert result.part_of_speech == "verb"

    def test_definition_present(self, dictapi_response, sample_snippets):
        result = _parse_dictapi_response("contaminate", dictapi_response, sample_snippets)
        assert len(result.definition) > 0

    def test_example_dict_extracted(self, dictapi_response, sample_snippets):
        result = _parse_dictapi_response("contaminate", dictapi_response, sample_snippets)
        assert result.example_dict is not None

    def test_synonyms_extracted(self, dictapi_response, sample_snippets):
        result = _parse_dictapi_response("contaminate", dictapi_response, sample_snippets)
        assert isinstance(result.synonyms, list)
        assert "pollute" in result.synonyms

    def test_antonyms_extracted(self, dictapi_response, sample_snippets):
        result = _parse_dictapi_response("contaminate", dictapi_response, sample_snippets)
        assert isinstance(result.antonyms, list)
        assert "purify" in result.antonyms

    def test_source_is_dictionaryapi(self, dictapi_response, sample_snippets):
        result = _parse_dictapi_response("contaminate", dictapi_response, sample_snippets)
        assert result.source == "dictionaryapi"

    def test_returns_none_on_empty_response(self, sample_snippets):
        result = _parse_dictapi_response("contaminate", [], sample_snippets)
        assert result is None

    def test_returns_none_on_missing_meanings(self, sample_snippets):
        result = _parse_dictapi_response("contaminate", [{"word": "contaminate", "meanings": []}], sample_snippets)
        assert result is None

    def test_example_from_second_definition_when_first_has_none(self, sample_snippets):
        response = [
            {
                "word": "test",
                "meanings": [
                    {
                        "partOfSpeech": "noun",
                        "definitions": [
                            {"definition": "a procedure", "synonyms": [], "antonyms": []},
                            {"definition": "an exam", "example": "he passed the test", "synonyms": [], "antonyms": []},
                        ],
                        "synonyms": [],
                        "antonyms": [],
                    }
                ],
            }
        ]
        result = _parse_dictapi_response("test", response, sample_snippets)
        assert result.example_dict == "he passed the test"

    def test_synonyms_capped_at_five(self, sample_snippets):
        response = [
            {
                "word": "test",
                "meanings": [
                    {
                        "partOfSpeech": "noun",
                        "definitions": [{"definition": "a procedure", "synonyms": ["a","b","c","d","e","f","g"], "antonyms": []}],
                        "synonyms": [],
                        "antonyms": [],
                    }
                ],
            }
        ]
        result = _parse_dictapi_response("test", response, sample_snippets)
        assert len(result.synonyms) <= 5


# ── Wiktionary (issue #1) ────────────────────────────────────────────────────

class TestParseWiktionaryExamples:

    def test_extracts_example_sentences(self):
        data = [
            {
                "partOfSpeech": "Noun",
                "definitions": [
                    {"definition": "water", "examples": ["Il boit de l'<b>eau</b>."]},
                ],
            }
        ]
        assert def_module._parse_wiktionary_examples(data) == ["Il boit de l'eau."]

    def test_strips_html_tags(self):
        data = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ['<span class="x">Le</span> <b>chat</b> dort.']},
        ]}]
        assert def_module._parse_wiktionary_examples(data) == ["Le chat dort."]

    def test_caps_at_two_examples_across_entries(self):
        data = [
            {"partOfSpeech": "Noun", "definitions": [
                {"definition": "d1", "examples": ["Un.", "Deux.", "Trois."]},
            ]},
            {"partOfSpeech": "Verb", "definitions": [
                {"definition": "d2", "examples": ["Quatre."]},
            ]},
        ]
        assert def_module._parse_wiktionary_examples(data) == ["Un.", "Deux."]

    def test_no_examples_returns_empty_list(self):
        data = [{"partOfSpeech": "Noun", "definitions": [{"definition": "d"}]}]
        assert def_module._parse_wiktionary_examples(data) == []

    def test_empty_or_none_data_returns_empty_list(self):
        assert def_module._parse_wiktionary_examples([]) == []
        assert def_module._parse_wiktionary_examples(None) == []

    def test_deduplicates_identical_examples_across_entries(self):
        data = [
            {"partOfSpeech": "Noun", "definitions": [{"definition": "d1", "examples": ["Le chat dort."]}]},
            {"partOfSpeech": "Verb", "definitions": [{"definition": "d2", "examples": ["Le chat dort."]}]},
        ]
        assert def_module._parse_wiktionary_examples(data) == ["Le chat dort."]


class TestFetchFromWiktionary:

    def test_returns_language_section_on_success(self, monkeypatch):
        # language is just a dict key into the response -- this function is
        # not tied to any one language. Picking Japanese here deliberately,
        # since this project supports far more than French/English.
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        ja_section = [{"partOfSpeech": "Noun", "definitions": []}]
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data={"ja": ja_section, "en": []})
            result = def_module._fetch_from_wiktionary("mizu", "ja")
        assert result == ja_section

    def test_missing_language_key_returns_none(self, monkeypatch):
        # The word has a Wiktionary page but no section for this language.
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data={"en": []})
            result = def_module._fetch_from_wiktionary("word", "de")
        assert result is None

    def test_404_returns_none_and_does_not_trip_breaker(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(404)
            for _ in range(10):
                def_module._fetch_from_wiktionary("xyzqwerty", "fr")
        assert not def_module._circuit_is_tripped("wiktionary:fr")

    def test_429_counts_as_a_failure(self, monkeypatch):
        # A 429 means the source is telling us to back off, unlike a 404
        # which means the source is healthy and simply lacks the word.
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(429)
            for _ in range(3):
                def_module._fetch_from_wiktionary("word", "fr")
        assert def_module._circuit_is_tripped("wiktionary:fr")

    def test_tripped_breaker_skips_network_call(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(429)
            for _ in range(3):
                def_module._fetch_from_wiktionary("word", "fr")
            assert mock_get.call_count == 3
            def_module._fetch_from_wiktionary("another_word", "fr")
            assert mock_get.call_count == 3

    def test_breaker_independent_per_language(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(429)
            for _ in range(3):
                def_module._fetch_from_wiktionary("word", "fr")
        assert def_module._circuit_is_tripped("wiktionary:fr")
        assert not def_module._circuit_is_tripped("wiktionary:de")

    def test_sends_identifying_user_agent(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data={"es": []})
            def_module._fetch_from_wiktionary("word", "es")
            _, kwargs = mock_get.call_args
            assert "User-Agent" in kwargs["headers"]
            assert kwargs["headers"]["User-Agent"]


# ── SQLite cache ──────────────────────────────────────────────────────────────

class TestCache:

    def test_cache_miss_returns_none(self):
        assert _cache_get("notcached") is None

    def test_cache_set_and_get(self, sample_definition_result):
        _cache_set(sample_definition_result)
        cached = _cache_get("contaminate")
        assert cached is not None
        assert cached["definition"] == sample_definition_result.definition

    def test_cache_stores_synonyms_as_json(self, sample_definition_result):
        _cache_set(sample_definition_result)
        cached = _cache_get("contaminate")
        synonyms = json.loads(cached["synonyms"])
        assert "pollute" in synonyms

    def test_cache_replace_on_duplicate(self, sample_definition_result):
        _cache_set(sample_definition_result)
        updated = DefinitionResult(
            **{**sample_definition_result.__dict__, "definition": "updated definition"}
        )
        _cache_set(updated)
        cached = _cache_get("contaminate")
        assert cached["definition"] == "updated definition"

    # -- Cache resilience under concurrent access -----------------------------
    # Regression coverage for a real bug found live: fetch_definitions() runs
    # several lemmas concurrently, and a cache write/read losing a SQLite
    # lock race (sqlite3.OperationalError: database is locked) must never
    # turn an already-successful lookup into a lost one.

    def test_cache_write_failure_does_not_raise(self, sample_definition_result, monkeypatch):
        def _boom():
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(def_module, "_get_db", _boom)
        key = def_module._cache_key("contaminate", "en", "en")
        def_module._cache_set_key(key, sample_definition_result)  # must not raise

    def test_cache_read_failure_returns_none_not_raises(self, monkeypatch):
        def _boom():
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(def_module, "_get_db", _boom)
        assert def_module._cache_get(def_module._cache_key("contaminate", "en", "en")) is None

    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_fetch_definition_still_returns_result_when_cache_write_fails(
        self, mock_dict, mock_mw, mw_response, sample_snippets, monkeypatch
    ):
        # The actual bug, live: fetch_definition() successfully builds a
        # result, then calls _cache_set_key(cache_key, result) as its last
        # step before `return result`. When that write lost a SQLite lock
        # race, the exception used to propagate out of fetch_definition()
        # entirely -- return result never executed -- and
        # fetch_definitions()'s executor loop caught it and recorded the
        # lemma as not-found, discarding a lookup that had already
        # succeeded. _cache_set_key now fails soft internally, so this must
        # still return the built result.
        mock_mw.return_value = mw_response
        mock_dict.return_value = None
        monkeypatch.setattr(
            def_module, "_get_db",
            MagicMock(side_effect=sqlite3.OperationalError("database is locked")),
        )
        result = fetch_definition("contaminate", sample_snippets, use_cache=False)
        assert result is not None
        assert result.lemma == "contaminate"

    def test_schema_initialized_once_per_db_path(self, monkeypatch):
        monkeypatch.setattr(def_module, "_initialized_dbs", set())
        with patch("pipeline.definition._init_schema") as mock_init:
            def_module._get_db()
            def_module._get_db()
            def_module._get_db()
        mock_init.assert_called_once()


# ── Circuit breaker (issue #4) ───────────────────────────────────────────────

def _mock_response(status_code: int, json_data=None, raises: bool = False):
    """Build a mock requests.Response for circuit breaker tests."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    if raises:
        import requests
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status_code} error"
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestCircuitBreaker:

    def test_does_not_trip_before_threshold(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(502, raises=True)
            for _ in range(2):
                def_module._fetch_from_dictapi("word", language="fr")
        assert not def_module._circuit_is_tripped("dictapi:fr")

    def test_trips_after_threshold_consecutive_server_errors(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(502, raises=True)
            for _ in range(3):
                def_module._fetch_from_dictapi("word", language="fr")
        assert def_module._circuit_is_tripped("dictapi:fr")

    def test_tripped_source_skips_network_call_entirely(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(502, raises=True)
            for _ in range(3):
                def_module._fetch_from_dictapi("word", language="fr")
            assert mock_get.call_count == 3
            # Breaker is now tripped -- next call must not touch the network.
            def_module._fetch_from_dictapi("another_word", language="fr")
            assert mock_get.call_count == 3

    def test_404_does_not_count_as_a_failure(self, monkeypatch):
        # Regression guard for issue #1's 404-vs-502 finding: a 404 means
        # the source is healthy and the word is absent, not that the
        # source is broken. Many consecutive 404s (realistic for a
        # low-coverage language) must never trip the breaker.
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(404)
            for _ in range(10):
                def_module._fetch_from_dictapi("word", language="fr")
        assert not def_module._circuit_is_tripped("dictapi:fr")
        assert mock_get.call_count == 10  # every call actually hit the network

    def test_success_resets_the_failure_count(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(502, raises=True)
            def_module._fetch_from_dictapi("word", language="fr")
            def_module._fetch_from_dictapi("word", language="fr")
            # A success in between must reset the streak, not just pause it.
            mock_get.return_value = _mock_response(200, json_data=[{"word": "word"}])
            def_module._fetch_from_dictapi("word", language="fr")
            mock_get.return_value = _mock_response(502, raises=True)
            def_module._fetch_from_dictapi("word", language="fr")
            def_module._fetch_from_dictapi("word", language="fr")
        # Only 2 consecutive failures since the reset -- must not have tripped.
        assert not def_module._circuit_is_tripped("dictapi:fr")

    def test_sources_are_independent(self, monkeypatch):
        # A tripped dictapi:fr breaker must not affect dictapi:en or mw.
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(502, raises=True)
            for _ in range(3):
                def_module._fetch_from_dictapi("word", language="fr")
        assert def_module._circuit_is_tripped("dictapi:fr")
        assert not def_module._circuit_is_tripped("dictapi:en")
        assert not def_module._circuit_is_tripped("mw")

    def test_reset_circuit_breaker_clears_tripped_state(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.return_value = _mock_response(502, raises=True)
            for _ in range(3):
                def_module._fetch_from_dictapi("word", language="fr")
        assert def_module._circuit_is_tripped("dictapi:fr")
        def_module.reset_circuit_breaker()
        assert not def_module._circuit_is_tripped("dictapi:fr")

    def test_connection_error_counts_as_failure(self, monkeypatch):
        monkeypatch.setattr(def_module, "CIRCUIT_BREAKER_THRESHOLD", 3)
        # Independent of whether a real MW_API_KEY happens to be set in this
        # environment's .env -- _fetch_from_mw() returns early without
        # touching the breaker at all if the key is falsy, which would make
        # this test pass vacuously in a fresh clone or CI with no .env.
        monkeypatch.setattr(def_module, "MW_API_KEY", "fake-test-key")
        import requests
        with patch("pipeline.definition.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError("refused")
            for _ in range(3):
                def_module._fetch_from_mw("word")
        assert def_module._circuit_is_tripped("mw")


# ── fetch_definition ──────────────────────────────────────────────────────────

class TestFetchDefinition:

    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_returns_mw_result_when_available(
        self, mock_dict, mock_mw, mw_response, sample_snippets
    ):
        mock_mw.return_value = mw_response
        mock_dict.return_value = None
        result = fetch_definition("contaminate", sample_snippets, use_cache=False)
        assert result is not None
        assert result.source == "merriam-webster"

    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_falls_back_to_dictapi_when_mw_fails(
        self, mock_dict, mock_mw, dictapi_response, sample_snippets
    ):
        mock_mw.return_value = None
        mock_dict.return_value = dictapi_response
        result = fetch_definition("contaminate", sample_snippets, use_cache=False)
        assert result is not None
        assert result.source == "dictionaryapi"

    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_returns_none_when_both_fail(
        self, mock_dict, mock_mw, sample_snippets
    ):
        mock_mw.return_value = None
        mock_dict.return_value = None
        result = fetch_definition("xyzqwerty", sample_snippets, use_cache=False)
        assert result is None

    @patch("pipeline.definition._fetch_from_mw")
    def test_returns_cached_without_api_call(
        self, mock_mw, sample_definition_result, sample_snippets
    ):
        # Cache key is now composite: "lemma::language"
        # fetch_definition with default language="en" looks up the native key
        from pipeline.definition import _cache_set_key
        _cache_set_key(_cache_key("contaminate", "en", "en"), sample_definition_result)
        result = fetch_definition(
            "contaminate", sample_snippets, use_cache=True, language="en"
        )
        mock_mw.assert_not_called()
        assert result is not None
        assert result.lemma == "contaminate"

    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_result_cached_after_successful_fetch(
        self, mock_dict, mock_mw, mw_response, sample_snippets
    ):
        mock_mw.return_value = mw_response
        mock_dict.return_value = None
        fetch_definition(
            "contaminate", sample_snippets, use_cache=False, language="en"
        )
        # The key is composite; derive it rather than spelling it out, so
        # this cannot drift from _cache_key() the way it did when the
        # source language joined the key.
        cached = _cache_get(_cache_key("contaminate", "en", "en"))
        assert cached is not None

    # -- Wiktionary supplementation (issue #1) -----------------------------

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_wiktionary_supplements_missing_example_for_non_english(
        self, mock_dict, mock_mw, mock_wikt, sample_snippets
    ):
        # German, not French -- this pipeline supports 24+ languages and
        # the supplementation logic is not scoped to any one of them.
        mock_mw.return_value = None
        mock_dict.return_value = [{
            "word": "wasser", "meanings": [{
                "partOfSpeech": "noun",
                "definitions": [{"definition": "water", "synonyms": [], "antonyms": []}],
                "synonyms": [], "antonyms": [],
            }],
        }]
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "water", "examples": ["Wasser lassen"]},
        ]}]
        result = fetch_definition("wasser", sample_snippets, use_cache=False, language="de")
        assert result.example_dict == "Wasser lassen"
        mock_wikt.assert_called_once_with("wasser", "de")

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_wiktionary_not_called_when_dictapi_already_has_an_example(
        self, mock_dict, mock_mw, mock_wikt, sample_snippets
    ):
        mock_mw.return_value = None
        mock_dict.return_value = [{
            "word": "eau", "meanings": [{
                "partOfSpeech": "noun",
                "definitions": [{
                    "definition": "water", "example": "L'eau est froide.",
                    "synonyms": [], "antonyms": [],
                }],
                "synonyms": [], "antonyms": [],
            }],
        }]
        fetch_definition("eau", sample_snippets, use_cache=False, language="fr")
        mock_wikt.assert_not_called()

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_wiktionary_called_for_english_without_an_example(
        self, mock_dict, mock_mw, mock_wikt, mw_response, sample_snippets
    ):
        """
        English used to be excluded here, on the premise that MW covered its
        examples. Measured, it does not: an English video produced examples on
        14% of cards against French's 97%, and on eight English words the
        existing sources gave 3 examples where English Wiktionary gave 6.
        """
        mock_mw.return_value = mw_response
        mock_dict.return_value = None
        mock_wikt.return_value = None
        fetch_definition("contaminate", None, use_cache=False, language="en")
        mock_wikt.assert_called_once()

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._fetch_from_dictapi")
    def test_wiktionary_skipped_when_an_example_was_already_found(
        self, mock_dict, mock_mw, mock_wikt, mw_response, sample_snippets
    ):
        """
        The pair to the test above. The `not native_ex1` guard is what keeps
        this from becoming a Wikimedia request per word: it fires only where
        the card would otherwise ship with no example at all.
        """
        # native_ex1 comes from the dictionaryapi call in step 1, not from MW,
        # so the guard has to be set up with a dictionaryapi response that
        # carries an example -- mocking MW alone does not exercise it.
        mock_mw.return_value = mw_response
        mock_dict.return_value = [{
            "word": "contaminate",
            "meanings": [{"partOfSpeech": "verb", "definitions": [
                {"definition": "to make impure", "example": "they contaminated the water"}
            ]}],
        }]
        fetch_definition("contaminate", sample_snippets, use_cache=False, language="en")
        mock_wikt.assert_not_called()


class TestCrossLanguageFieldLanguage:
    """
    CLAUDE.md 3.3: examples, synonyms and antonyms stay in the transcript
    language; only the definition may differ. The coverage sweep caught this
    being violated -- a German video with --def-lang ru shipped
    "Вопросы по пройденному материалу есть?" as the example for "Frage",
    and the wrong-language content inflated the measured coverage, so
    cross-language rows scored higher on examples than native ones.
    """

    @patch("pipeline.definition.wiktdata")
    @patch("pipeline.definition._fetch_from_wiktionary", return_value=None)
    @patch("pipeline.definition._fetch_from_mw", return_value=None)
    @patch("pipeline.definition._fetch_from_dictapi", return_value=None)
    @patch("pipeline.definition.translate_word", create=True)
    def test_target_language_entry_gives_definition_only(
        self, _tr, _dict, _mw, _wikt, mock_wiktdata
    ):
        entry = MagicMock()
        entry.definition = "определение"
        entry.part_of_speech = "noun"
        entry.example1 = "Вопросы по пройденному материалу есть?"
        entry.example2 = None
        entry.synonyms = ["вопрос"]
        entry.antonyms = ["ответ"]
        mock_wiktdata.is_available.return_value = True
        mock_wiktdata.lookup.return_value = entry

        with patch("pipeline.translation.translate_word", return_value="вопрос"):
            r = fetch_definition("frage", None, use_cache=False,
                                 language="de", def_language="ru")

        assert r is not None
        assert r.definition == "определение"          # definition may be target-language
        assert r.example_dict is None                  # example may not
        assert r.synonyms == []
        assert r.antonyms == []

    @patch("pipeline.definition.wiktdata")
    @patch("pipeline.definition._fetch_from_wiktionary", return_value=None)
    @patch("pipeline.definition._fetch_from_mw", return_value=None)
    @patch("pipeline.definition._fetch_from_dictapi", return_value=None)
    def test_native_run_still_takes_every_field(self, _dict, _mw, _wikt, mock_wiktdata):
        """
        The pair to the test above. Gating on the wrong condition would strip
        these from native runs too, where the index entry IS in the
        transcript language and supplies most of the card.
        """
        entry = MagicMock()
        entry.definition = "eine Äußerung"
        entry.part_of_speech = "noun"
        entry.example1 = "Hast du eine Frage?"
        entry.example2 = None
        entry.synonyms = ["Anfrage"]
        entry.antonyms = ["Antwort"]
        mock_wiktdata.is_available.return_value = True
        mock_wiktdata.lookup.return_value = entry

        r = fetch_definition("frage", None, use_cache=False, language="de")

        assert r is not None
        assert r.example_dict == "Hast du eine Frage?"
        assert r.synonyms == ["Anfrage"]
        assert r.antonyms == ["Antwort"]


# ── _fetch_definition_or_fallback_example ───────────────────────────────────
#
# Covers the actual failure mode issue #1 documented: a language where
# dictionaryapi.dev has no definition at all, not just a missing example.
# fetch_definition() bails out via "if not definition: return None" before
# ever using a Wiktionary example in that case, so the Wiktionary
# supplementation exercised in TestFetchDefinition above never fires for
# it. This wrapper is what fetch_definitions() actually submits to the
# thread pool per lemma, and is what makes a real French run's fallback
# cards carry a dictionary example instead of nothing.

class TestFetchDefinitionOrFallbackExample:

    @patch("pipeline.definition.fetch_definition")
    def test_returns_result_when_definition_found(self, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        result, extras = def_module._fetch_definition_or_fallback_example(
            "contaminate", None, "en", None
        )
        assert result is sample_definition_result
        assert extras.example is None
        assert extras.synonyms == []
        assert extras.antonyms == []

    @pytest.mark.parametrize("language", ["fr", "de", "ja", "es"])
    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_falls_back_to_wiktionary_example_for_non_english(
        self, mock_fetch, mock_wikt, mock_wn, language
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ["Some native sentence."]},
        ]}]
        mock_wn.return_value = ([], [])
        result, extras = def_module._fetch_definition_or_fallback_example(
            "word", None, language, None
        )
        assert result is None
        assert extras.example == "Some native sentence."
        mock_wikt.assert_called_once_with("word", language)

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_wiktionary_attempted_for_english_too(self, mock_fetch, mock_wikt, mock_wn):
        """
        This path builds a fallback card for a lemma no source defined. It
        skipped English entirely, so an English fallback card carried the
        transcript sentence and nothing else, even where Wiktionary had a
        usable example.
        """
        mock_fetch.return_value = None
        mock_wn.return_value = ([], [])
        mock_wikt.return_value = [{"language": "English",
                                   "definitions": [{"examples": ["a real example"]}]}]
        result, extras = def_module._fetch_definition_or_fallback_example(
            "word", None, "en", None
        )
        assert result is None
        mock_wikt.assert_called_once()

    @pytest.mark.parametrize("language", ["fr", "de", "ja"])
    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_returns_none_none_when_wiktionary_also_has_nothing(
        self, mock_fetch, mock_wikt, mock_wn, language
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = ([], [])
        result, extras = def_module._fetch_definition_or_fallback_example(
            "xyzqwerty", None, language, None
        )
        assert result is None
        assert extras.example is None

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_falls_back_to_omw_synonyms_when_no_definition_anywhere(
        self, mock_fetch, mock_wikt, mock_wn
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = (["bonheur", "joie"], [])
        result, extras = def_module._fetch_definition_or_fallback_example(
            "content", None, "fr", None
        )
        assert result is None
        assert extras.synonyms == ["bonheur", "joie"]
        assert extras.antonyms == []
        mock_wn.assert_called_once_with("content", "fr")

    @patch("pipeline.definition.fetch_definition")
    def test_no_omw_lookup_when_definition_found(self, mock_fetch, sample_definition_result):
        # fetch_definition() already ran its own OMW lookup internally when
        # it found a definition -- this wrapper must not redo it.
        mock_fetch.return_value = sample_definition_result
        with patch("pipeline.definition._wordnet_synonyms_antonyms") as mock_wn:
            def_module._fetch_definition_or_fallback_example("content", None, "fr", None)
            mock_wn.assert_not_called()


# ── Offline Wiktionary index integration (issue #16) ─────────────────────────
#
# The index is the only source with non-English definitions. These tests
# enable it explicitly (the autouse fixture disables it everywhere else) so
# they behave identically whether or not a real index exists on disk.

class TestPronunciationResolution:
    """
    ADR-009. Pronunciation describes the word PRINTED ON THE CARD, so it is
    resolved once against (lemma, language) and never from whichever entry
    happened to supply the definition. See ARCHITECTURE.md 8.34 for the
    violation that produced this rule.
    """

    @staticmethod
    def _entry(**kw):
        from pipeline.wiktdata import DictionaryEntry
        base = dict(word="haus", part_of_speech="noun", definition="Gebäude.",
                    example1=None, example2=None, synonyms=[], antonyms=[],
                    ipa="[haʊ̯s]", audio_url="https://example.invalid/de-haus.ogg")
        base.update(kw)
        return DictionaryEntry(**base)

    def test_index_is_queried_with_the_transcript_language(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup",
            lambda w, l, pos=None: seen.update(word=w, lang=l) or self._entry(),
        )
        ipa, audio = def_module._resolve_pronunciation("haus", "de")
        assert seen == {"word": "haus", "lang": "de"}
        assert ipa == "[haʊ̯s]"
        assert audio == "https://example.invalid/de-haus.ogg"

    def test_a_language_with_no_index_and_no_api_gets_nothing(self, monkeypatch):
        # es/ja/ko/pt/zh have a spaCy model but no built index. The card must
        # omit the section rather than show an empty one, and nothing may
        # reach the network on their behalf.
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: False)
        called = []
        monkeypatch.setattr(def_module, "_fetch_from_dictapi",
                            lambda *a, **k: called.append(a) or None)
        assert def_module._resolve_pronunciation("casa", "es") == (None, None)
        assert not called, "no API call should be made for a non-English language"

    def test_english_falls_through_to_dictionaryapi(self, monkeypatch):
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: False)
        monkeypatch.setattr(def_module, "_fetch_from_dictapi", lambda *a, **k: [
            {"phonetics": [{"text": "/haʊs/", "audio": "https://x.invalid/house.mp3"}]}
        ])
        assert def_module._resolve_pronunciation("house", "en") == (
            "/haʊs/", "https://x.invalid/house.mp3"
        )

    def test_index_present_but_word_absent_does_not_fall_through(self, monkeypatch):
        # A built index is authoritative for its language. Falling through to
        # the English API for a German word it happens to miss would put an
        # English pronunciation on a German card.
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(def_module.wiktdata, "lookup", lambda *a, **k: None)
        called = []
        monkeypatch.setattr(def_module, "_fetch_from_dictapi",
                            lambda *a, **k: called.append(a) or None)
        assert def_module._resolve_pronunciation("kartoffel", "de") == (None, None)
        assert not called


class TestParseDictapiPhonetics:
    """
    dictionaryapi.dev returns IPA and a complete audio URL for English, on a
    call the pipeline already makes. Both were parsed nowhere and dropped --
    the sixth instance of fetched-parsed-discarded.
    """

    def test_prefers_an_entry_carrying_both_text_and_audio(self):
        # Regional variants disagree: /hʌʊs/ against /haʊs/ for "house".
        # Stitching the transcription from one and the recording from another
        # describes two different accents on one card.
        data = [{"phonetics": [
            {"text": "/hʌʊs/", "audio": ""},
            {"text": "/haʊs/", "audio": "https://x.invalid/us.mp3"},
        ]}]
        assert def_module._parse_dictapi_phonetics(data) == (
            "/haʊs/", "https://x.invalid/us.mp3"
        )

    def test_falls_back_to_separate_entries(self):
        data = [{"phonetics": [
            {"text": "/haʊs/", "audio": ""},
            {"text": "", "audio": "https://x.invalid/uk.mp3"},
        ]}]
        assert def_module._parse_dictapi_phonetics(data) == (
            "/haʊs/", "https://x.invalid/uk.mp3"
        )

    def test_falls_back_to_the_top_level_phonetic_field(self):
        data = [{"phonetic": "/haʊs/", "phonetics": [{"text": "", "audio": ""}]}]
        assert def_module._parse_dictapi_phonetics(data) == ("/haʊs/", None)

    def test_empty_and_malformed_input_yields_nothing(self):
        assert def_module._parse_dictapi_phonetics([]) == (None, None)
        assert def_module._parse_dictapi_phonetics([{}]) == (None, None)
        assert def_module._parse_dictapi_phonetics(["nonsense"]) == (None, None)


class TestOfflineDictionaryIntegration:

    def _entry(self, **kw):
        from pipeline.wiktdata import DictionaryEntry
        defaults = dict(
            word="maison", part_of_speech="noun",
            definition="Batiment servant de logis.",
            example1="Une belle maison.", example2="La maison est grande.",
            synonyms=["demeure"], antonyms=["dehors"],
        )
        defaults.update(kw)
        return DictionaryEntry(**defaults)

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition._fetch_from_dictapi")
    @patch("pipeline.definition._fetch_from_mw")
    def test_index_supplies_a_definition_no_online_source_has(
        self, mock_mw, mock_dict, mock_wikt, monkeypatch
    ):
        # The whole point of issue #16: dictionaryapi.dev returns nothing for
        # French, so before this the card said "No definition found".
        mock_mw.return_value = None
        mock_dict.return_value = None
        mock_wikt.return_value = None
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup", lambda _w, _l, pos=None: self._entry()
        )

        result = fetch_definition("maison", None, use_cache=False, language="fr")
        assert result is not None
        assert result.definition == "Batiment servant de logis."
        assert result.source == "wiktionary"

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition._fetch_from_dictapi")
    @patch("pipeline.definition._fetch_from_mw")
    def test_index_is_not_consulted_when_a_definition_already_exists(
        self, mock_mw, mock_dict, mock_wikt, monkeypatch, mw_response
    ):
        # English keeps its existing behaviour untouched: the index is a
        # fallback for the languages that have nothing, not a new primary.
        #
        # This used to assert the index was never touched at all. That was a
        # proxy for the real rule and it stopped holding in v0.5.1, when
        # pronunciation gained a legitimate second reason to consult the
        # index — one that has nothing to do with where the definition came
        # from (ARCHITECTURE 8.34). The intent is unchanged and is now
        # asserted directly: the DEFINITION must still come from
        # Merriam-Webster.
        mock_mw.return_value = mw_response
        mock_dict.return_value = None
        mock_wikt.return_value = None
        called = []
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup",
            lambda w, l, pos=None: called.append(w) or self._entry(),
        )
        result = fetch_definition("contaminate", None, use_cache=False, language="en")
        assert result.source == "merriam-webster"
        assert result.definition != self._entry().definition
        # And any lookup that did happen asked about the word on the card,
        # never a translation of it.
        assert set(called) <= {"contaminate"}

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_omw_synonyms_win_over_index_synonyms(
        self, mock_fetch, mock_wikt, mock_wn, monkeypatch
    ):
        # Measured on a real French deck: OMW covers 76% of cards, the index
        # only 49%. Layering them the wrong way round would trade better data
        # for worse, so OMW keeps first claim.
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = (["omw-synonym"], [])
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup",
            lambda _w, _l, pos=None: self._entry(synonyms=["index-synonym"]),
        )
        _r, extras = def_module._fetch_definition_or_fallback_example(
            "maison", None, "fr", None
        )
        assert extras.synonyms == ["omw-synonym"]

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_index_supplies_antonyms_omw_cannot(
        self, mock_fetch, mock_wikt, mock_wn, monkeypatch
    ):
        # OMW carries no antonyms outside English at all, so this field was
        # structurally empty rather than merely sparse.
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = ([], [])
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup", lambda _w, _l, pos=None: self._entry()
        )
        _r, extras = def_module._fetch_definition_or_fallback_example(
            "maison", None, "fr", None
        )
        assert extras.antonyms == ["dehors"]

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_missing_index_changes_nothing(
        self, mock_fetch, mock_wikt, mock_wn
    ):
        # Anyone who never runs --build-dictionary must get exactly the old
        # behaviour. The autouse fixture already reports it unavailable.
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = (["bonheur"], [])
        _r, extras = (
            def_module._fetch_definition_or_fallback_example("maison", None, "fr", None)
        )
        assert extras.example is None
        assert extras.synonyms == ["bonheur"]
        assert extras.antonyms == []

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_index_supplies_pronunciation_for_a_lemma_with_no_definition(
        self, mock_fetch, mock_wikt, mock_wn, monkeypatch
    ):
        # ADR-009 phase 1. The entry that already supplies this card's
        # example carries ipa and audio_url in schema v2, and both were
        # being read and dropped -- the fallback card is precisely the one
        # that benefits most from still showing how the word is said.
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = ([], [])
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup",
            lambda _w, _l, pos=None: self._entry(
                ipa="\\mɛ.zɔ̃\\", audio_url="https://example.invalid/maison.ogg"
            ),
        )
        _r, extras = def_module._fetch_definition_or_fallback_example(
            "maison", None, "fr", None
        )
        assert extras.ipa == "\\mɛ.zɔ̃\\"
        assert extras.audio_url == "https://example.invalid/maison.ogg"

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_pronunciation_survives_a_lemma_that_needs_nothing_else(
        self, mock_fetch, mock_wikt, mock_wn, monkeypatch
    ):
        # The partner, and the reason the index lookup is no longer gated on
        # `not example or not antonyms`: a lemma whose example and antonyms
        # both came from elsewhere used to skip the lookup entirely and so
        # lost its pronunciation, while a sparser lemma kept it.
        mock_fetch.return_value = None
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ["Deja une phrase."]},
        ]}]
        mock_wn.return_value = (["demeure"], ["dehors"])
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup",
            lambda _w, _l, pos=None: self._entry(ipa="\\mɛ.zɔ̃\\"),
        )
        _r, extras = def_module._fetch_definition_or_fallback_example(
            "maison", None, "fr", None
        )
        assert extras.example == "Deja une phrase."   # not taken from the index
        assert extras.antonyms == ["dehors"]          # nor these
        assert extras.ipa == "\\mɛ.zɔ̃\\"              # but the IPA still arrives


# ── fetch_definitions (batch) ─────────────────────────────────────────────────

# ── Log volume for missing definitions (issue #17) ───────────────────────────
#
# A per-word WARNING for a condition that holds for an entire language is
# noise, not signal: one real French video emitted 1047 identical lines and
# buried the SQLite lock errors that actually mattered. These pin the split
# so neither half can regress silently -- English keeps its per-word
# warning, non-English gets one batch-level line instead.

class TestMissingDefinitionLogVolume:

    @patch("pipeline.definition._fetch_from_dictapi")
    @patch("pipeline.definition._fetch_from_mw")
    def test_english_miss_still_warns_per_word(self, mock_mw, mock_dict, caplog):
        mock_mw.return_value = None
        mock_dict.return_value = None
        with caplog.at_level(logging.WARNING, logger="pipeline.definition"):
            fetch_definition("xyzqwerty", None, use_cache=False, language="en")
        assert any("xyzqwerty" in r.message for r in caplog.records)

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition._fetch_from_dictapi")
    @patch("pipeline.definition._fetch_from_mw")
    def test_non_english_miss_does_not_warn_per_word(
        self, mock_mw, mock_dict, mock_wikt, caplog
    ):
        mock_mw.return_value = None
        mock_dict.return_value = None
        mock_wikt.return_value = None
        with caplog.at_level(logging.WARNING, logger="pipeline.definition"):
            fetch_definition("xyzqwerty", None, use_cache=False, language="fr")
        assert not [r for r in caplog.records if "xyzqwerty" in r.message]

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_batch_warns_once_not_per_word_for_non_english(
        self, mock_fetch, mock_wikt, mock_wn, caplog
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = ([], [])
        words = [f"w{i}" for i in range(40)]
        with caplog.at_level(logging.WARNING, logger="pipeline.definition"):
            fetch_definitions(words, language="fr", max_workers=1)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"expected 1 summary warning, got {len(warnings)}"
        assert "40" in warnings[0].message

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_no_batch_warning_when_some_definitions_were_found(
        self, mock_fetch, mock_wikt, mock_wn, caplog, sample_definition_result
    ):
        # The summary is for "this language yields nothing", not for the
        # ordinary case of a few words missing.
        mock_fetch.side_effect = [sample_definition_result, None]
        mock_wikt.return_value = None
        mock_wn.return_value = ([], [])
        with caplog.at_level(logging.WARNING, logger="pipeline.definition"):
            fetch_definitions(["found", "missing"], language="fr", max_workers=1)
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]


class TestFetchDefinitions:

    @patch("pipeline.definition.fetch_definition")
    def test_returns_batch_result(self, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        result = fetch_definitions(["contaminate"])
        assert isinstance(result, DefinitionBatchResult)

    @patch("pipeline.definition.fetch_definition")
    def test_found_list_populated(self, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        result = fetch_definitions(["contaminate"])
        assert len(result.found) == 1
        assert result.found[0].lemma == "contaminate"

    @patch("pipeline.definition.fetch_definition")
    def test_not_found_list_populated(self, mock_fetch):
        mock_fetch.return_value = None
        result = fetch_definitions(["xyzqwerty"])
        assert "xyzqwerty" in result.not_found

    @pytest.mark.parametrize("language", ["fr", "de", "ja"])
    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_examples_populated_from_wiktionary(
        self, mock_fetch, mock_wikt, mock_wn, language
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ["A native example."]},
        ]}]
        mock_wn.return_value = ([], [])
        result = fetch_definitions(["word"], language=language)
        assert result.not_found == ["word"]
        assert result.not_found_examples == {"word": "A native example."}

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_examples_populated_for_english(self, mock_fetch, mock_wikt, mock_wn):
        """English is no longer excluded -- see the call-site tests above."""
        mock_fetch.return_value = None
        mock_wn.return_value = ([], [])
        mock_wikt.return_value = None
        result = fetch_definitions(["xyzqwerty"], language="en")
        assert result.not_found_examples == {}
        mock_wikt.assert_called_once()

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_second_wiktionary_example_is_kept_not_discarded(
        self, mock_fetch, mock_wikt, mock_wn
    ):
        # The card model has two example fields. Wiktionary commonly supplies
        # two (25 of 45 real French words), but only examples[0] was kept, so
        # the second field was blank on every fallback card while the data
        # had already been fetched.
        mock_fetch.return_value = None
        mock_wn.return_value = ([], [])
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ["Premiere phrase.", "Deuxieme phrase."]},
        ]}]
        result = fetch_definitions(["mot"], language="fr")
        assert result.not_found_examples == {"mot": "Premiere phrase."}
        assert result.not_found_examples2 == {"mot": "Deuxieme phrase."}

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_single_example_leaves_second_slot_empty(
        self, mock_fetch, mock_wikt, mock_wn
    ):
        # Pairs with the test above: one example must not be duplicated into
        # both fields.
        mock_fetch.return_value = None
        mock_wn.return_value = ([], [])
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ["Seule phrase."]},
        ]}]
        result = fetch_definitions(["mot"], language="fr")
        assert result.not_found_examples == {"mot": "Seule phrase."}
        assert result.not_found_examples2 == {}

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_synonyms_populated_from_omw(self, mock_fetch, mock_wikt, mock_wn):
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = (["contenu", "satisfait"], [])
        result = fetch_definitions(["content"], language="fr")
        assert result.not_found == ["content"]
        assert result.not_found_synonyms == {"content": ["contenu", "satisfait"]}

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_antonyms_populated_from_wordnet_english(
        self, mock_fetch, mock_wikt, mock_wn
    ):
        mock_fetch.return_value = None
        mock_wn.return_value = ([], ["discontented"])
        result = fetch_definitions(["content"], language="en")
        assert result.not_found_antonyms == {"content": ["discontented"]}

    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_synonyms_empty_when_omw_has_nothing(
        self, mock_fetch, mock_wikt, mock_wn
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        mock_wn.return_value = ([], [])
        result = fetch_definitions(["xyzqwerty"], language="fr")
        assert result.not_found_synonyms == {}
        assert result.not_found_antonyms == {}

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_examples_omits_lemmas_wiktionary_could_not_help(
        self, mock_fetch, mock_wikt
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        result = fetch_definitions(["xyzqwerty"], language="fr")
        assert result.not_found == ["xyzqwerty"]
        assert result.not_found_examples == {}

    @patch("pipeline.definition.fetch_definition")
    def test_processes_all_lemmas(self, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        result = fetch_definitions(["contaminate", "water", "develop"])
        assert mock_fetch.call_count == 3

    def test_cache_hit_skips_fetch_definition_call(
        self, sample_definition_result
    ):
        # Seeded through _cache_key() rather than a hand-written string.
        # This test spelled the key out, and when the source language joined
        # it the seed silently stopped matching: the lookup missed and the
        # assertion failed for a reason unrelated to what it tests. Deriving
        # the key means it cannot drift from the implementation again, which
        # is the same reason _cache_key() exists at all.
        _cache_set_key(
            _cache_key(sample_definition_result.lemma, "en", "en"),
            sample_definition_result,
        )
        with patch("pipeline.definition.fetch_definition") as mock_fetch:
            result = fetch_definitions(["contaminate"])
            mock_fetch.assert_not_called()
        assert "contaminate" in result.from_cache

    def test_all_cache_hits_never_start_thread_pool(self, sample_definition_result):
        # If every lemma is cached, fetch_definitions() should never even
        # construct a ThreadPoolExecutor — there is nothing for it to do.
        _cache_set_key(
            _cache_key(sample_definition_result.lemma, "en", "en"),
            sample_definition_result,
        )
        with patch("pipeline.definition.ThreadPoolExecutor") as mock_pool:
            fetch_definitions(["contaminate"])
            mock_pool.assert_not_called()

    @patch("pipeline.definition.fetch_definition")
    def test_concurrent_fetch_respects_max_workers(self, mock_fetch):
        # Each fake fetch blocks until released, letting us count how many
        # are in flight at once. If the pool ignored max_workers, all 6
        # lemmas would run at once instead of in batches of 2.
        in_flight = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def fake_fetch(lemma, *args, **kwargs):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            release.wait(timeout=1)
            with lock:
                in_flight -= 1
            return DefinitionResult(lemma=lemma, definition="d", example_dict=None,
                                     example_dict2=None, example_transcript=None,
                                     synonyms=[], antonyms=[],
                                     part_of_speech="n", source="dictionaryapi")

        mock_fetch.side_effect = fake_fetch
        lemmas = [f"word{i}" for i in range(6)]

        def run():
            fetch_definitions(lemmas, max_workers=2)

        t = threading.Thread(target=run)
        t.start()
        # Give the pool time to saturate its workers before releasing them.
        import time as _time
        _time.sleep(0.2)
        release.set()
        t.join(timeout=5)

        assert peak <= 2

    @patch("pipeline.definition.fetch_definition")
    def test_results_returned_in_first_appearance_order(self, mock_fetch):
        # Threads can finish in any order. The batch's order must still
        # match the input list, not completion order, since downstream
        # card generation assumes first-appearance ordering.
        def fake_fetch(lemma, *args, **kwargs):
            import time as _time
            # Make the first lemma finish last, to prove ordering isn't
            # just an accident of submission order.
            _time.sleep(0.05 if lemma == "alpha" else 0.0)
            return DefinitionResult(lemma=lemma, definition="d", example_dict=None,
                                     example_dict2=None, example_transcript=None,
                                     synonyms=[], antonyms=[],
                                     part_of_speech="n", source="dictionaryapi")

        mock_fetch.side_effect = fake_fetch
        result = fetch_definitions(["alpha", "beta", "gamma"], max_workers=3)
        assert [r.lemma for r in result.found] == ["alpha", "beta", "gamma"]

    @patch("pipeline.definition.fetch_definition")
    def test_empty_lemma_list_returns_empty_batch(self, mock_fetch):
        result = fetch_definitions([])
        mock_fetch.assert_not_called()
        assert result.found == []
        assert result.not_found == []


# ── Integration (real network + API keys required) ────────────────────────────

@pytest.mark.integration
class TestIntegration:

    def test_dictapi_real_word(self, sample_snippets):
        result = fetch_definition("water", sample_snippets, use_cache=False)
        assert result is not None
        assert result.definition
        assert result.part_of_speech

    def test_dictapi_unknown_word_returns_none(self):
        result = fetch_definition("xyzqwerty123", use_cache=False)
        assert result is None

    def test_batch_real_words(self, sample_snippets):
        result = fetch_definitions(["water", "develop"], sample_snippets)
        assert len(result.found) > 0

    def test_wiktionary_real_french_word_has_example(self):
        # "eau" was confirmed by hand to have a French example section on
        # en.wiktionary.org during the issue #1 research pass.
        data = def_module._fetch_from_wiktionary("eau", "fr")
        assert data is not None
        examples = def_module._parse_wiktionary_examples(data)
        assert len(examples) > 0
        assert "eau" in examples[0].lower()

    def test_wiktionary_real_german_word_has_example(self):
        # This source is not French-specific -- it works for any language
        # with a Wiktionary presence. "Wasser" confirmed by hand to have a
        # German example section on en.wiktionary.org.
        data = def_module._fetch_from_wiktionary("Wasser", "de")
        assert data is not None
        examples = def_module._parse_wiktionary_examples(data)
        assert len(examples) > 0

    def test_real_french_word_with_no_dictapi_coverage_gets_fallback_example(self):
        # This is the actual bug fixed for issue #1: dictionaryapi.dev has
        # no French coverage for "eau" (confirmed 404/502 in issue #1's own
        # investigation), so fetch_definitions() must put it in not_found,
        # but a real Wiktionary example should still show up in
        # not_found_examples for the resulting fallback card.
        result = fetch_definitions(["eau"], language="fr")
        assert "eau" in result.not_found
        assert "eau" in result.not_found_examples
        assert len(result.not_found_examples["eau"]) > 0

# ── Open Multilingual Wordnet (ADR-008, issue #1) ────────────────────────────
#
# Real, unmocked nltk calls against the actual downloaded omw-2.0 package,
# not integration-marked -- consistent with how the spaCy model download is
# a base CI/setup requirement rather than something gated behind
# @pytest.mark.integration. Both CI and `make spacy-model` download this
# data as a prerequisite, so it's always present when these run.

class TestOmwSynonymsAntonyms:

    def test_real_french_word_returns_french_synonyms(self):
        syns, ants = def_module._wordnet_synonyms_antonyms("maison", "fr")
        assert len(syns) > 0
        # Every returned word should look like French vocabulary, not the
        # English gloss text OMW also carries -- a loose but meaningful
        # sanity check that this is actually native-language data.
        assert "home" not in syns and "house" not in syns

    def test_real_spanish_word_returns_spanish_synonyms(self):
        syns, ants = def_module._wordnet_synonyms_antonyms("casa", "es")
        assert len(syns) > 0

    def test_non_english_never_returns_antonyms(self):
        # Confirmed live during ADR-008's research: antonym relations are
        # not meaningfully present for non-English OMW lemmas (empty in
        # every real case checked, and the one non-empty result pointed at
        # an English antonym object). Not worth the risk of surfacing that,
        # so antonyms are only ever attempted for English -- this must hold
        # even for a word overwhelmingly likely to have antonyms in
        # English ("grand"/large has "small").
        syns, ants = def_module._wordnet_synonyms_antonyms("grand", "fr")
        assert ants == []

    def test_uncovered_language_returns_empty_without_lookup_attempt(self):
        # German has no omw-2.0 data at all (see _OMW_LANGUAGE_CODES).
        syns, ants = def_module._wordnet_synonyms_antonyms("Haus", "de")
        assert syns == []
        assert ants == []

    def test_unknown_word_in_covered_language_returns_empty(self):
        syns, ants = def_module._wordnet_synonyms_antonyms("xyzqwertyfr", "fr")
        assert syns == []
        assert ants == []

    def test_english_path_unaffected_by_omw_addition(self):
        # Regression guard: adding language-aware OMW support must not
        # change the pre-existing English behavior, which doesn't pass a
        # lang= argument to wn.synsets()/lemmas() at all.
        syns, ants = def_module._wordnet_synonyms_antonyms("happy", "en")
        assert len(syns) > 0

    def test_ensure_omw_loaded_is_idempotent(self):
        # Calling this many times must not re-run the (comparatively
        # expensive) provenance scan every time.
        assert def_module._ensure_omw_loaded() is True
        assert def_module._ensure_omw_loaded() is True

    # -- Synonym ordering under the 5-item cap ----------------------------
    #
    # Matched pair (CLAUDE.md section 5). The cap discards whatever doesn't
    # fit, so ordering decides which words survive. These two fail in
    # opposite directions: the first fails if selection is alphabetical,
    # the second fails if synset order isn't preserved at all. Either one
    # alone could be satisfied accidentally.

    def test_cap_keeps_most_common_sense_over_alphabetically_earlier_words(self):
        # "aujourd'hui" pools 7+ synonyms across 3 synsets. "maintenant"
        # comes from synset 0 (presently.r.02, the everyday sense) but
        # sorts late alphabetically, so an alphabetical cut dropped it in
        # favour of "de notre temps" from the same pool. Verified against a
        # real French run before this was fixed.
        syns, _ = def_module._wordnet_synonyms_antonyms("aujourd'hui", "fr")
        assert "maintenant" in syns

    def test_synonyms_follow_synset_order_not_alphabetical_order(self):
        # Guards the mechanism rather than one word's output. Note that
        # asserting "the list isn't sorted" does NOT work: nltk returns
        # lemma names alphabetically *within* a synset, so any word whose
        # first synset alone fills all five slots comes back sorted no
        # matter which selection rule is in force.
        #
        # English on purpose. Cross-sense ordering can only be observed
        # where more than one synset is consulted, and non-English is
        # deliberately capped at a single synset (see the synset_limit
        # comment in definition.py). "build" spans senses inside the cap:
        # "physique" and "habitus" (first sense) must outrank the later
        # senses despite sorting after "anatomy"/"bod"/"chassis".
        from nltk.corpus import wordnet as wn
        def_module._ensure_omw_loaded("eng")
        word = "build"
        syns, _ = def_module._wordnet_synonyms_antonyms(word, "en")

        first_sense = {
            lemma.name().replace("_", " ").lower()
            for lemma in wn.synsets(word)[0].lemmas()
            if lemma.name().lower() != word
        }
        kept = {s.lower() for s in syns}
        kept_later = [s for s in syns if s.lower() not in first_sense]
        dropped_first = first_sense - kept

        # Without this the test is vacuous: it must be a word where a later
        # sense actually made it into the output, or there is nothing for a
        # first-sense word to have been displaced by.
        assert kept_later, "word must contribute a later sense to prove ordering"
        assert not dropped_first, (
            f"dropped first-sense {sorted(dropped_first)} "
            f"while keeping later-sense {kept_later}"
        )

    def test_non_english_is_capped_at_exactly_two_senses(self):
        # Pins the provisional non-English setting from both sides, so
        # neither widening nor narrowing it passes unnoticed (see
        # synset_limit in definition.py). "prêt" has three senses: ready
        # (adjective), loan (noun, -> "emprunt"), quick (adjective, ->
        # "rapide").
        #
        # Worth stating plainly: two senses does NOT fully resolve sense
        # mixing. "emprunt" is a noun landing on an adjective's card, and
        # it is admitted here deliberately as the price of coverage. This
        # setting is temporary pending a real per-language dictionary
        # source (issue #16).
        syns, _ = def_module._wordnet_synonyms_antonyms("prêt", "fr")
        assert "emprunt" in syns, "second sense missing -- narrowed below two?"
        assert "rapide" not in syns, "third sense present -- widened past two?"

    def test_english_still_uses_multiple_senses(self):
        # Guards the other half: narrowing English too was measured as a
        # straight regression (14/15 -> 9/15 words with any synonym at
        # all), because many English words have a top synset containing
        # only the word itself.
        for word in ("happy", "large", "quick", "think"):
            syns, _ = def_module._wordnet_synonyms_antonyms(word, "en")
            assert syns, f"{word} lost all synonyms"

    def test_synonyms_deduplicate_case_insensitively(self):
        # The same lemma reached through two synsets must not occupy two of
        # the five slots.
        syns, _ = def_module._wordnet_synonyms_antonyms("aller", "fr")
        lowered = [s.lower() for s in syns]
        assert len(lowered) == len(set(lowered))

    def test_concurrent_lookups_do_not_silently_lose_synonyms(self):
        # Regression guard for a real, silent data-loss bug. nltk's WordNet
        # reader seeks and reads a shared file handle, so concurrent
        # lookups corrupted each other and raised AssertionError, which
        # _wordnet_synonyms_antonyms swallowed into an empty result. A real
        # French run lost synonyms on a different subset of cards every
        # time (767/764/730 across three runs of the same video).
        #
        # "petit" is deliberate: it has 31 synsets, the most of any word
        # checked, and was the last one still failing after the corpus
        # warm-up alone fixed the others.
        import concurrent.futures as cf

        words = ["aller", "petit", "faire", "savoir", "croire", "monde"] * 4
        with cf.ThreadPoolExecutor(max_workers=5) as ex:
            results = list(
                ex.map(lambda w: def_module._wordnet_synonyms_antonyms(w, "fr")[0], words)
            )

        assert all(results), (
            f"{sum(1 for r in results if not r)} of {len(results)} concurrent "
            "lookups came back empty"
        )
        # Same word must give the same answer regardless of thread timing.
        per_word: dict = {}
        for word, syns in zip(words, results):
            per_word.setdefault(word, set()).add(tuple(syns))
        for word, variants in per_word.items():
            assert len(variants) == 1, f"{word} returned varying results: {variants}"

    def test_omw_language_codes_exclude_languages_confirmed_uncovered(self):
        # Direct regression guard for the specific finding ADR-008 records:
        # these five have no omw-2.0 data at all. If nltk's data ever
        # changes and starts covering one of them, this test failing is
        # the signal to revisit the mapping, not silently keep excluding it.
        for code in ("de", "ru", "uk", "mk", "ko"):
            assert code not in def_module._OMW_LANGUAGE_CODES


# Append these to tests/test_definition.py
# They cover the WordNet language-leak bug that Claude Code identified.

class TestWordNetLanguageGuard:
    """
    WordNet's own gloss/antonym data is English-only, and Open Multilingual
    Wordnet (ADR-008) only covers 18 of the other 23 supported languages
    for synonyms (see _OMW_LANGUAGE_CODES). These tests ensure the right
    language's data reaches the right card -- covered languages actually
    get looked up, uncovered languages get nothing rather than a lookup
    that can't succeed, antonyms are never attempted for non-English, and
    every lookup uses the original transcript lemma/language rather than a
    translated query word or the translation-mode definition language.

    Every mock setup below must let fetch_definition() actually find a
    definition. The original versions of these tests mocked every source
    (_fetch_from_mw and _fetch_from_dictapi) to return None unconditionally,
    which makes fetch_definition() return None at its own "no definition
    found" guard (definition.py: `if not definition: ... return None`)
    before the WordNet gate at line ~645 is ever reached. Three of the four
    tests then wrapped their real assertion in `if mock_wn.called:` or
    `if result:`, so they passed vacuously — they never actually ran the
    code they were written to guard. Only the fourth test asserted
    unconditionally, which is why it was the only one that failed.
    """

    @patch("pipeline.definition._fetch_from_dictapi")
    @patch("pipeline.definition._wordnet_synonyms_antonyms", return_value=([], []))
    def test_wordnet_called_for_omw_covered_language(
        self, mock_wn, mock_dict
    ):
        """
        Issue #1 / ADR-008: French has real Open Multilingual Wordnet data,
        so _wordnet_synonyms_antonyms must now run for it, unlike before
        OMW support existed.
        """
        mock_dict.return_value = [{
            "word": "bonjour",
            "meanings": [{
                "partOfSpeech": "interjection",
                "definitions": [{"definition": "hello"}],
                "synonyms": [],
                "antonyms": [],
            }],
        }]
        fetch_definition("bonjour", use_cache=False, language="fr")
        mock_wn.assert_called_once_with("bonjour", "fr")

    @patch("pipeline.definition._fetch_from_dictapi")
    def test_uncovered_language_gets_no_synonyms_end_to_end(self, mock_dict):
        """
        German has no Open Multilingual Wordnet data at all (see
        _OMW_LANGUAGE_CODES / ADR-008). fetch_definition() still calls
        _wordnet_synonyms_antonyms() for it (the language check moved
        inside that function, see the two tests below), but the result
        must come back with no synonyms, not a German word list that
        doesn't exist anywhere.
        """
        mock_dict.return_value = [{
            "word": "hallo",
            "meanings": [{
                "partOfSpeech": "interjection",
                "definitions": [{"definition": "hello"}],
                "synonyms": [],
                "antonyms": [],
            }],
        }]
        result = fetch_definition("hallo", use_cache=False, language="de")
        assert result.synonyms == []
        assert result.antonyms == []

    @patch("pipeline.definition._fetch_from_dictapi", return_value=None)
    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._wordnet_synonyms_antonyms",
           return_value=(["glad"], ["sad"]))
    def test_wordnet_called_for_english_language(
        self, mock_wn, mock_mw, mock_dict, mw_response
    ):
        """WordNet runs normally when the transcript language is English."""
        # Native dictapi fetch returns None, so native_syns/native_ants stay
        # empty. MW supplies the definition (mw_response has empty "syns",
        # so it doesn't also fill native_syns), which satisfies the gate's
        # emptiness check and lets _wordnet_synonyms_antonyms run.
        mock_mw.return_value = mw_response
        fetch_definition("happy", use_cache=False, language="en")
        mock_wn.assert_called_once()

    @patch("pipeline.definition._fetch_from_dictapi")
    @patch("pipeline.definition._wordnet_synonyms_antonyms",
           return_value=([], []))
    def test_wordnet_receives_original_lemma_not_translation(
        self, mock_wn, mock_dict
    ):
        """
        When translation occurs, WordNet must receive the ORIGINAL lemma.
        This is the regression guard for the query_lemma leak bug.

        The original test passed language=def_language="en", under which
        the translation branch (`if def_language and def_language !=
        language`) never runs at all, so it could never have caught a
        query_lemma leak regardless of the mock setup. Translation only
        happens when the two differ, and WordNet only runs when the
        transcript `language` is "en" — so the scenario that actually
        exercises both at once is an English transcript translated to a
        different definition language (not the French-transcript direction
        used elsewhere in this class).
        """
        def _dictapi_side_effect(lemma, language="en"):
            if language == "fr":
                # Definition-fetch step (translated query "content" in fr).
                # Empty synonyms/antonyms so this doesn't fill native_syns
                # before the WordNet gate is checked.
                return [{
                    "word": lemma,
                    "meanings": [{
                        "partOfSpeech": "adjective",
                        "definitions": [{"definition": "joyeux, satisfait"}],
                        "synonyms": [],
                        "antonyms": [],
                    }],
                }]
            return None  # Native-fetch step (language="en") finds nothing.

        mock_dict.side_effect = _dictapi_side_effect
        with patch("pipeline.translation.translate_word", return_value="content"):
            fetch_definition(
                "happy", use_cache=False, language="en", def_language="fr"
            )
        mock_wn.assert_called_once()
        called_with = mock_wn.call_args[0][0]
        assert called_with == "happy", (
            f"WordNet received '{called_with}' but must receive the "
            f"original lemma 'happy', not the translated query 'content'."
        )

    @patch("pipeline.definition._fetch_from_dictapi", return_value=None)
    @patch("pipeline.definition._fetch_from_mw")
    @patch("pipeline.definition._wordnet_synonyms_antonyms", return_value=([], []))
    def test_omw_receives_native_language_not_def_language(
        self, mock_wn, mock_mw, mock_dict, mw_response
    ):
        """
        End-to-end guard, updated for OMW support (ADR-008): a French word
        with DEF_LANG=en must query OMW with the NATIVE transcript language
        ("fr"), never the translated definition-target language ("en") --
        otherwise a French card could end up with English-only WordNet
        synonyms/antonyms instead of French OMW ones, or none at all
        instead of the French data OMW actually has for "bonjour".

        Before OMW existed this case never called WordNet at all (it's
        English-only), so there was nothing to get backwards. Now that
        French is a covered language, the call happens for a different
        reason and this guards that it uses the right language for it.
        """
        mock_mw.return_value = mw_response
        with patch("pipeline.translation.translate_word", return_value="hello"):
            result = fetch_definition(
                "bonjour", use_cache=False, language="fr", def_language="en"
            )
        assert result is not None, (
            "fetch_definition() returned None — a definition must actually "
            "be found for this test to meaningfully check the WordNet "
            "language guard rather than passing vacuously."
        )
        mock_wn.assert_called_once_with("bonjour", "fr")

# ── Cache key and part of speech ─────────────────────────────────────────────

class TestCacheKeyCarriesPartOfSpeech:
    """
    ARCHITECTURE.md 8.29, and the trap 8.27 records twice: the cache stores
    assembled fields, so a fix to which sense gets selected never reaches a
    row that is already written. The part of speech now decides the sense,
    so it has to be part of the identity of the row.
    """

    def test_different_pos_are_different_rows(self):
        # "fait" as a noun and "fait" as an adjective are different cards.
        # Sharing one row is exactly how a sense fix fails to land.
        assert (
            def_module._cache_key("fait", "fr", "fr", "NOUN")
            != def_module._cache_key("fait", "fr", "fr", "ADJ")
        )

    def test_same_pos_is_the_same_row(self):
        assert (
            def_module._cache_key("fait", "fr", "fr", "NOUN")
            == def_module._cache_key("fait", "fr", "fr", "NOUN")
        )

    def test_a_pos_that_cannot_narrow_does_not_split_the_cache(self):
        # PROPN selects no row, so keying on it would create a second row
        # holding the identical result. The partner to the test above:
        # one pins that a meaningful POS splits, this pins that a
        # meaningless one does not.
        assert (
            def_module._cache_key("fait", "fr", "fr", "PROPN")
            == def_module._cache_key("fait", "fr", "fr")
        )

    def test_language_still_separates_rows(self):
        # The pre-existing guarantee must survive: a German lemma cached
        # under ::en once served false-friend English definitions back.
        assert (
            def_module._cache_key("gift", "de", "de", "NOUN")
            != def_module._cache_key("gift", "en", "en", "NOUN")
        )

    def test_bare_lemma_is_recoverable_from_the_key(self):
        # _cache_row_to_result splits on "::" to restore the lemma, and a
        # third segment must not break that.
        key = def_module._cache_key("maison", "fr", "fr", "NOUN")
        assert key.split("::")[0] == "maison"

    def test_batch_and_single_agree_on_the_key(self, sample_definition_result):
        # SESSION.md 6.12: these two built the key separately once and
        # drifted, so a row written by one was invisible to the other. Seed
        # through the batch loop's key and read it back through the same
        # helper fetch_definition() uses.
        key = def_module._cache_key(sample_definition_result.lemma, "en", "en", "VERB")
        _cache_set_key(key, sample_definition_result)
        with patch("pipeline.definition.fetch_definition") as mock_fetch:
            result = fetch_definitions(
                ["contaminate"],
                parts_of_speech={"contaminate": "VERB"},
            )
            mock_fetch.assert_not_called()
        assert "contaminate" in result.from_cache

    def test_a_row_cached_under_another_pos_is_not_served(
        self, sample_definition_result
    ):
        # The partner: the batch must MISS a row whose POS differs, rather
        # than serving the sense chosen for a different reading of the word.
        _cache_set_key(
            def_module._cache_key(sample_definition_result.lemma, "en", "en", "NOUN"),
            sample_definition_result,
        )
        with patch("pipeline.definition.fetch_definition") as mock_fetch:
            mock_fetch.return_value = None
            result = fetch_definitions(
                ["contaminate"],
                parts_of_speech={"contaminate": "VERB"},
            )
        assert "contaminate" not in result.from_cache

    def test_pos_reaches_the_index_lookup(self, monkeypatch):
        # End to end through fetch_definitions: the tag nlp.py recorded has
        # to arrive at wiktdata.lookup, not be dropped somewhere in between.
        seen = {}
        monkeypatch.setattr(def_module.wiktdata, "is_available", lambda _l: True)
        monkeypatch.setattr(
            def_module.wiktdata, "lookup",
            lambda w, l, pos=None: seen.setdefault(w, pos),
        )
        with patch("pipeline.definition._fetch_from_mw", return_value=None), \
             patch("pipeline.definition._fetch_from_dictapi", return_value=None), \
             patch("pipeline.definition._fetch_from_wiktionary", return_value=None):
            fetch_definitions(
                ["marcher"], language="fr", max_workers=1,
                parts_of_speech={"marcher": "VERB"},
            )
        assert seen["marcher"] == "VERB"


# ── --no-cache, for measurement runs ─────────────────────────────────────────

class TestNoCache:
    """
    ARCHITECTURE.md 8.27. A sweep exists to report what the pipeline
    produces now, and a warm cache is what stops it -- rows hold assembled
    card fields, so they survive the fix that corrected them.
    """

    def test_cached_row_is_ignored(self, sample_definition_result):
        _cache_set_key(
            def_module._cache_key(sample_definition_result.lemma, "en", "en"),
            sample_definition_result,
        )
        with patch("pipeline.definition.fetch_definition") as mock_fetch:
            mock_fetch.return_value = sample_definition_result
            result = fetch_definitions(["contaminate"], use_cache=False)
        assert result.from_cache == []

    def test_cached_row_is_used_by_default(self, sample_definition_result):
        # The partner. Without this, a --no-cache implementation that simply
        # broke the cache read for everyone would still satisfy the test above.
        _cache_set_key(
            def_module._cache_key(sample_definition_result.lemma, "en", "en"),
            sample_definition_result,
        )
        with patch("pipeline.definition.fetch_definition"):
            result = fetch_definitions(["contaminate"])
        assert result.from_cache == ["contaminate"]

    def test_nothing_is_written_back(self, monkeypatch, mw_response):
        # A measurement run must not change what the next run sees. This is
        # how 4532 rows of Russian examples reached German cards.
        #
        # MW must actually return something. The first version of this test
        # mocked every source to None, so fetch_definition() returned before
        # reaching the write at all and the test passed whatever write_cache
        # did -- vacuous, and caught by mutation rather than by the suite
        # (SESSION.md 6.11).
        written = []
        monkeypatch.setattr(
            def_module, "_cache_set_key", lambda k, r: written.append(k)
        )
        with patch("pipeline.definition._fetch_from_mw", return_value=mw_response), \
             patch("pipeline.definition._fetch_from_dictapi", return_value=None), \
             patch("pipeline.definition._fetch_from_wiktionary", return_value=None), \
             patch("pipeline.definition._wordnet_synonyms_antonyms",
                   return_value=([], [])):
            batch = fetch_definitions(["contaminate"], max_workers=1, use_cache=False)
        assert batch.found, "a definition must be found, or this proves nothing"
        assert written == []

    def test_writes_happen_by_default(self, monkeypatch, mw_response):
        # The partner to the test above, and the one that matters most:
        # use_cache and write_cache are separate parameters precisely so
        # that fetch_definitions()'s own use_cache=False (it already
        # resolved the read) does not disable caching for the normal path.
        written = []
        monkeypatch.setattr(
            def_module, "_cache_set_key", lambda k, r: written.append(k)
        )
        with patch("pipeline.definition._fetch_from_mw", return_value=mw_response), \
             patch("pipeline.definition._fetch_from_dictapi", return_value=None), \
             patch("pipeline.definition._fetch_from_wiktionary", return_value=None), \
             patch("pipeline.definition._wordnet_synonyms_antonyms",
                   return_value=([], [])):
            fetch_definitions(["contaminate"], max_workers=1)
        assert written == [def_module._cache_key("contaminate", "en", "en")]


# ── Cache key and the transcript language ────────────────────────────────────

class TestCacheKeyCarriesSourceLanguage:
    """
    The key recorded the language a definition was written *in*, never the
    language of the video it came from, and constraint 3.3 means those pick
    different content: a German video with --def-lang en writes a row holding
    German example sentences.

    Measured on the real 5408-row cache, 265 rows keyed `::en` already hold
    German examples. None had collided yet only because no English run had
    met one of those spellings.
    """

    def test_a_cross_language_row_cannot_be_read_by_a_native_run(self):
        # The bug, stated directly. "hand", "arm", "band" and "wild" are
        # words in both languages, so an English card would have received
        # German examples from a German --def-lang en run.
        from_german_video = def_module._cache_key("hand", "de", "en", "NOUN")
        from_english_video = def_module._cache_key("hand", "en", "en", "NOUN")
        assert from_german_video != from_english_video

    def test_the_same_pairing_is_still_one_row(self):
        # The partner. Splitting on source must not defeat the cache: two
        # German videos defined in English share their rows as before.
        assert (
            def_module._cache_key("hand", "de", "en", "NOUN")
            == def_module._cache_key("hand", "de", "en", "NOUN")
        )

    def test_a_pairing_is_selectable_for_invalidation(self):
        # The other half of why source is in the key. Both cache-poisoning
        # incidents needed the vocabulary table joined back to each video's
        # language to find the affected rows, because nothing recorded it.
        # A LIKE on the pairing is now the whole job.
        assert "::de::en::" in def_module._cache_key("hand", "de", "en", "NOUN")

    def test_an_unresolved_pos_still_leaves_four_segments(self):
        # Without a placeholder, a row whose POS did not resolve would have
        # three segments and escape the LIKE above, so invalidation would
        # silently miss exactly the rows nobody chose a sense for.
        key = def_module._cache_key("hand", "de", "en", None)
        assert key.count("::") == 3
        assert "::de::en::" in key
        # The placeholder is asserted literally, not just counted. Without
        # `or "-"` the f-string interpolates the string "None", which still
        # leaves four segments and still passes a shape check while putting a
        # Python repr into every user's cache key.
        assert key.endswith("::-")
        assert "None" not in key

    def test_an_unresolvable_pos_does_not_split_the_cache(self):
        # The original guarantee survives the placeholder: PROPN selects no
        # index row, so it must share a row with no POS at all rather than
        # creating a second one holding identical content.
        assert (
            def_module._cache_key("fait", "fr", "fr", "PROPN")
            == def_module._cache_key("fait", "fr", "fr", None)
        )

    def test_the_bare_lemma_is_still_recoverable(self):
        # _cache_row_to_result splits on "::" to restore the lemma, and a
        # fourth segment must not break that.
        key = def_module._cache_key("maison", "fr", "en", "NOUN")
        assert key.split("::")[0] == "maison"


class TestCacheKeyMigration:
    """
    Old rows are keyed `lemma::target[::pos]` and record nothing about the
    transcript they came from, which is the whole reason the key changed.

    They cannot be rewritten: recovering a row's source language means
    knowing which video it came from, and no table stores that. On the real
    database only 6 of 25 videos have a deck name a language can even be
    inferred from. Guessing native for the rest would re-key cross-language
    rows as native, which is exactly the collision the new key prevents, so a
    wrong guess preserves the bug while looking migrated.
    """

    @staticmethod
    def _v0_database(path):
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE definitions (lemma TEXT PRIMARY KEY, definition TEXT NOT NULL,"
            " example_dict TEXT, example_dict2 TEXT, synonyms TEXT, antonyms TEXT,"
            " part_of_speech TEXT, source TEXT NOT NULL, fetched_at TEXT)"
        )
        conn.execute(
            "INSERT INTO definitions (lemma, definition, source, fetched_at)"
            " VALUES ('haus::en', 'A building.', 'wiktionary', '2026-01-01')"
        )
        conn.commit()
        conn.close()

    def test_old_rows_are_kept_rather_than_deleted(self, tmp_path, monkeypatch):
        # CLAUDE.md warns against deleting pipeline.db because this cache is
        # expensive to rebuild. Renaming honours that while starting clean.
        db = tmp_path / "old.db"
        self._v0_database(db)
        monkeypatch.setattr(def_module, "DB_PATH", db)
        monkeypatch.setattr(def_module, "_initialized_dbs", set())
        conn = def_module._get_db()
        assert conn.execute("SELECT count(*) FROM definitions_v0").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM definitions").fetchone()[0] == 0

    def test_a_v0_row_is_not_readable_through_the_new_key(self, tmp_path, monkeypatch):
        # The point of the migration. Left in place, "haus::en" would be
        # unreachable anyway, but a stale row that *did* match would serve
        # German examples to an English card.
        db = tmp_path / "old2.db"
        self._v0_database(db)
        monkeypatch.setattr(def_module, "DB_PATH", db)
        monkeypatch.setattr(def_module, "_initialized_dbs", set())
        def_module._get_db()
        assert def_module._cache_get(def_module._cache_key("haus", "en", "en")) is None

    def test_migrating_twice_does_not_lose_the_second_cache(self, tmp_path, monkeypatch):
        # Idempotence matters more here than usual: a second run must not
        # rename a freshly refilled cache away on top of the old one.
        db = tmp_path / "old3.db"
        self._v0_database(db)
        monkeypatch.setattr(def_module, "DB_PATH", db)
        monkeypatch.setattr(def_module, "_initialized_dbs", set())
        conn = def_module._get_db()
        conn.execute(
            "INSERT INTO definitions (lemma, definition, source, fetched_at)"
            " VALUES ('haus::de::de::noun', 'Ein Gebaeude.', 'wiktionary', '2026-01-02')"
        )
        conn.commit()
        def_module._init_schema(conn)
        assert conn.execute("SELECT count(*) FROM definitions").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM definitions_v0").fetchone()[0] == 1

    def test_a_fresh_database_is_marked_current_without_a_legacy_table(self, tmp_path, monkeypatch):
        # The partner: a new user has nothing to migrate and must not get an
        # empty definitions_v0 table for no reason.
        db = tmp_path / "fresh.db"
        monkeypatch.setattr(def_module, "DB_PATH", db)
        monkeypatch.setattr(def_module, "_initialized_dbs", set())
        conn = def_module._get_db()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == def_module._CACHE_KEY_VERSION
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='definitions_v0'"
        ).fetchone()
        assert legacy is None
