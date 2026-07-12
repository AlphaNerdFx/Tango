"""
test_definition.py

All HTTP calls and SQLite operations use temp fixtures.
No network access or API keys required for the unit suite.

Run unit tests:         pytest tests/test_definition.py -m "not integration"
Run all (needs keys):   pytest tests/test_definition.py
"""

import json
import sqlite3
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


# ── fetch_definitions (batch) ─────────────────────────────────────────────────

class TestFetchDefinitions:

    @patch("pipeline.definition.fetch_definition")
    def test_returns_batch_result(self, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        result = fetch_definitions(["contaminate"], delay=0)
        assert isinstance(result, DefinitionBatchResult)

    @patch("pipeline.definition.fetch_definition")
    def test_found_list_populated(self, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        result = fetch_definitions(["contaminate"], delay=0)
        assert len(result.found) == 1
        assert result.found[0].lemma == "contaminate"

    @patch("pipeline.definition.fetch_definition")
    def test_not_found_list_populated(self, mock_fetch):
        mock_fetch.return_value = None
        result = fetch_definitions(["xyzqwerty"], delay=0)
        assert "xyzqwerty" in result.not_found

    @patch("pipeline.definition.fetch_definition")
    def test_processes_all_lemmas(self, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        result = fetch_definitions(["contaminate", "water", "develop"], delay=0)
        assert mock_fetch.call_count == 3

    def test_cache_hit_skips_fetch_definition_call(
        self, sample_definition_result
    ):
        _cache_set(sample_definition_result)
        with patch("pipeline.definition.fetch_definition") as mock_fetch:
            result = fetch_definitions(["contaminate"], delay=0)
            mock_fetch.assert_not_called()
        assert "contaminate" in result.from_cache

    @patch("pipeline.definition.fetch_definition")
    @patch("pipeline.definition.time.sleep")
    def test_delay_applied_between_live_calls(self, mock_sleep, mock_fetch, sample_definition_result):
        mock_fetch.return_value = sample_definition_result
        fetch_definitions(["water", "develop", "permanent"], delay=0.5)
        # Delay applied between calls — first call has no delay, rest do
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.5)

    @patch("pipeline.definition.fetch_definition")
    @patch("pipeline.definition.time.sleep")
    def test_no_delay_for_cache_hits(self, mock_sleep, mock_fetch, sample_definition_result):
        _cache_set(sample_definition_result)
        fetch_definitions(["contaminate"], delay=0.5)
        mock_sleep.assert_not_called()

    @patch("pipeline.definition.fetch_definition")
    def test_empty_lemma_list_returns_empty_batch(self, mock_fetch):
        result = fetch_definitions([], delay=0)
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
        result = fetch_definitions(["water", "develop"], sample_snippets, delay=1.0)
        assert len(result.found) > 0