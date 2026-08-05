"""
test_definition.py

All HTTP calls and SQLite operations use temp fixtures.
No network access or API keys required for the unit suite.

Run unit tests:         pytest tests/test_definition.py -m "not integration"
Run all (needs keys):   pytest tests/test_definition.py
"""

import json
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
        def_module._cache_set_key("contaminate::en", sample_definition_result)  # must not raise

    def test_cache_read_failure_returns_none_not_raises(self, monkeypatch):
        def _boom():
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(def_module, "_get_db", _boom)
        assert def_module._cache_get("contaminate::en") is None

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
        # fetch_definition with default language="en" looks up "contaminate::en"
        from pipeline.definition import _cache_set_key
        _cache_set_key("contaminate::en", sample_definition_result)
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
        # Cache key is now composite: "contaminate::en"
        cached = _cache_get("contaminate::en")
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
    def test_wiktionary_not_called_for_english(
        self, mock_dict, mock_mw, mock_wikt, mw_response, sample_snippets
    ):
        # English already has solid MW/dictionaryapi.dev coverage; Wiktionary
        # exists to supplement non-English examples only.
        mock_mw.return_value = mw_response
        mock_dict.return_value = None
        fetch_definition("contaminate", sample_snippets, use_cache=False, language="en")
        mock_wikt.assert_not_called()


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
        result, example = def_module._fetch_definition_or_fallback_example(
            "contaminate", None, "en", None
        )
        assert result is sample_definition_result
        assert example is None

    @pytest.mark.parametrize("language", ["fr", "de", "ja", "es"])
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_falls_back_to_wiktionary_example_for_non_english(
        self, mock_fetch, mock_wikt, language
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ["Some native sentence."]},
        ]}]
        result, example = def_module._fetch_definition_or_fallback_example(
            "word", None, language, None
        )
        assert result is None
        assert example == "Some native sentence."
        mock_wikt.assert_called_once_with("word", language)

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_no_wiktionary_attempt_for_english(self, mock_fetch, mock_wikt):
        mock_fetch.return_value = None
        result, example = def_module._fetch_definition_or_fallback_example(
            "word", None, "en", None
        )
        assert result is None
        assert example is None
        mock_wikt.assert_not_called()

    @pytest.mark.parametrize("language", ["fr", "de", "ja"])
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_returns_none_none_when_wiktionary_also_has_nothing(
        self, mock_fetch, mock_wikt, language
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = None
        result, example = def_module._fetch_definition_or_fallback_example(
            "xyzqwerty", None, language, None
        )
        assert result is None
        assert example is None


# ── fetch_definitions (batch) ─────────────────────────────────────────────────

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
    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_examples_populated_from_wiktionary(
        self, mock_fetch, mock_wikt, language
    ):
        mock_fetch.return_value = None
        mock_wikt.return_value = [{"partOfSpeech": "Noun", "definitions": [
            {"definition": "d", "examples": ["A native example."]},
        ]}]
        result = fetch_definitions(["word"], language=language)
        assert result.not_found == ["word"]
        assert result.not_found_examples == {"word": "A native example."}

    @patch("pipeline.definition._fetch_from_wiktionary")
    @patch("pipeline.definition.fetch_definition")
    def test_not_found_examples_empty_for_english(self, mock_fetch, mock_wikt):
        mock_fetch.return_value = None
        result = fetch_definitions(["xyzqwerty"], language="en")
        assert result.not_found_examples == {}
        mock_wikt.assert_not_called()

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
        # fetch_definitions() reads the cache via the composite "lemma::language"
        # key (matching fetch_definition()'s own cache key format), so the seed
        # here must use _cache_set_key() with that same composite key rather
        # than the bare-lemma _cache_set(). Seeding with the bare key leaves
        # the composite-keyed lookup unable to find it, causing a spurious
        # cache miss and a real (mocked) fetch_definition call.
        _cache_set_key(f"{sample_definition_result.lemma}::en", sample_definition_result)
        with patch("pipeline.definition.fetch_definition") as mock_fetch:
            result = fetch_definitions(["contaminate"])
            mock_fetch.assert_not_called()
        assert "contaminate" in result.from_cache

    def test_all_cache_hits_never_start_thread_pool(self, sample_definition_result):
        # If every lemma is cached, fetch_definitions() should never even
        # construct a ThreadPoolExecutor — there is nothing for it to do.
        _cache_set_key(f"{sample_definition_result.lemma}::en", sample_definition_result)
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

# Append these to tests/test_definition.py
# They cover the WordNet language-leak bug that Claude Code identified.

class TestWordNetLanguageGuard:
    """
    WordNet is English-only. These tests ensure it can never inject English
    synonyms or antonyms into a non-English card, and that it uses the
    original lemma rather than the translated query word.

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
    @patch("pipeline.definition._wordnet_synonyms_antonyms")
    def test_wordnet_not_called_for_non_english_language(
        self, mock_wn, mock_dict
    ):
        """WordNet must not run when the transcript language is not English."""
        # language == target_language == "fr" here, so both the native fetch
        # and the definition fetch go through dictionaryapi.dev. Synonyms and
        # antonyms are deliberately empty in this payload — if they were
        # non-empty (like the shared dictapi_response fixture), the gate's
        # emptiness check alone would skip WordNet regardless of language,
        # and this test would pass even with the language check deleted.
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
        mock_wn.assert_not_called()

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
    @patch("pipeline.definition._wordnet_synonyms_antonyms",
           return_value=(["english_syn"], ["english_ant"]))
    def test_english_synonyms_never_leak_into_french_card(
        self, mock_wn, mock_mw, mock_dict, mw_response
    ):
        """
        End-to-end guard: a French word with DEF_LANG=en must never end up
        with English synonyms in its Synonyms field.
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
        mock_wn.assert_not_called()
        assert "english_syn" not in result.synonyms
        assert "english_ant" not in result.antonyms