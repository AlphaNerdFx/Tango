"""
All file I/O uses tmp_path fixtures — no files written to the real filesystem.

Run unit tests:  pytest tests/test_cards.py -m "not integration"
"""

import zipfile
import time
from pathlib import Path
from unittest.mock import patch

import genanki
import pytest

import pipeline.cards as cards_module
from pipeline.cards import (
    _build_fallback_note,
    _build_model,
    _build_note,
    _build_output_path,
    _find_in_snippets,
    _format_pills,
    build_package,
    PackageResult,
)
from pipeline.definition import DefinitionResult

# -- Fixtures -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_output(tmp_path, monkeypatch):
    monkeypatch.setattr(cards_module, "OUTPUT_DIR", tmp_path / "output")
    yield tmp_path / "output"


@pytest.fixture
def sample_result() -> DefinitionResult:
    return DefinitionResult(
        lemma="contaminate",
        definition="to make impure or unsafe by contact",
        example_dict="the water supply was contaminated",
        example_transcript="contaminated water gave rise to new regulations",
        synonyms=["pollute", "taint"],
        antonyms=["purify"],
        part_of_speech="verb",
        source="merriam-webster",
    )


@pytest.fixture
def no_synonym_result() -> DefinitionResult:
    return DefinitionResult(
        lemma="develop",
        definition="to bring out the capabilities of",
        example_dict=None,
        example_transcript="companies had to develop permanent solutions",
        synonyms=[],
        antonyms=[],
        part_of_speech="verb",
        source="dictionaryapi",
    )


@pytest.fixture
def sample_snippets() -> dict:
    return {
        0.0: {"end": 3.5,  "text": "So companies had to develop permanent solutions"},
        3.5: {"end": 7.1,  "text": "contaminated water gave rise to new regulations"},
        7.1: {"end": 10.0, "text": "the permanent photographic record was preserved"},
        "_full_text":     "full text here",
        "_language_code": "en",
        "_snippet_count": 3,
    }


VIDEO_ID  = "LV_NoD2M54w"
DECK_NAME = "Language::English::Vocabulary"


# -- _format_pills ------------------------------------------------------------

class TestFormatPills:
    def test_returns_html_spans(self):
        result = _format_pills(["pollute", "taint"], "vocab-pill")
        assert '<span class="vocab-pill">pollute</span>' in result
        assert '<span class="vocab-pill">taint</span>' in result

    def test_empty_list_returns_empty_string(self):
        assert _format_pills([], "vocab-pill") == ""

    def test_single_item(self):
        result = _format_pills(["purify"], "antonym-pill")
        assert '<span class="antonym-pill">purify</span>' in result

    def test_css_class_applied_correctly(self):
        result = _format_pills(["word"], "antonym-pill")
        assert "antonym-pill" in result
        assert "vocab-pill" not in result


# -- _find_in_snippets --------------------------------------------------------

class TestFindInSnippets:
    def test_finds_exact_match(self, sample_snippets):
        assert "develop" in _find_in_snippets("develop", sample_snippets)

    def test_finds_inflected_form(self, sample_snippets):
        assert _find_in_snippets("contaminate", sample_snippets) is not None

    def test_returns_none_when_not_found(self, sample_snippets):
        assert _find_in_snippets("philosophy", sample_snippets) is None

    def test_ignores_string_keys(self, sample_snippets):
        assert _find_in_snippets("full", sample_snippets) is None

    def test_empty_snippets_returns_none(self):
        assert _find_in_snippets("water", {}) is None

    def test_returns_first_occurrence(self, sample_snippets):
        result = _find_in_snippets("permanent", sample_snippets)
        assert result == "So companies had to develop permanent solutions"


# -- _build_model -------------------------------------------------------------

class TestBuildModel:
    def test_returns_genanki_model(self):
        assert isinstance(_build_model(), genanki.Model)

    def test_model_id_is_stable(self):
        assert _build_model().model_id == _build_model().model_id

    def test_model_has_expected_fields(self):
        field_names = [f["name"] for f in _build_model().fields]
        for name in ["Word", "Definition", "ExampleDict", "ExampleTranscript",
                     "Synonyms", "Antonyms", "FallbackNote", "VideoID", "Source"]:
            assert name in field_names

    def test_model_has_one_template(self):
        model = _build_model()
        assert len(model.templates) == 1
        assert model.templates[0]["name"] == "Recognition"

    def test_model_has_css(self):
        model = _build_model()
        assert ".card" in model.css


# -- _build_note --------------------------------------------------------------

class TestBuildNote:
    def test_returns_genanki_note(self, sample_result):
        assert isinstance(_build_note(sample_result, _build_model(), VIDEO_ID), genanki.Note)

    def test_word_field_capitalised(self, sample_result):
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        assert note.fields[0] == "Contaminate"

    def test_definition_field_present(self, sample_result):
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        assert "impure" in note.fields[2]

    def test_synonyms_rendered_as_pills(self, sample_result):
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        assert "vocab-pill" in note.fields[5]
        assert "pollute" in note.fields[5]

    def test_antonyms_rendered_as_pills(self, sample_result):
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        assert "antonym-pill" in note.fields[6]
        assert "purify" in note.fields[6]

    def test_empty_synonyms_give_empty_string(self, no_synonym_result):
        note = _build_note(no_synonym_result, _build_model(), VIDEO_ID)
        assert note.fields[5] == ""

    def test_guid_is_stable(self, sample_result):
        model = _build_model()
        assert _build_note(sample_result, model, VIDEO_ID).guid == \
               _build_note(sample_result, model, VIDEO_ID).guid

    def test_guid_differs_by_video(self, sample_result):
        model = _build_model()
        assert _build_note(sample_result, model, "VIDEO_A").guid != \
               _build_note(sample_result, model, "VIDEO_B").guid

    def test_video_id_in_tags(self, sample_result):
        assert VIDEO_ID in _build_note(sample_result, _build_model(), VIDEO_ID).tags

    def test_yt_anki_tag_present(self, sample_result):
        assert "yt-anki" in _build_note(sample_result, _build_model(), VIDEO_ID).tags

    def test_none_example_dict_becomes_empty_string(self, no_synonym_result):
        note = _build_note(no_synonym_result, _build_model(), VIDEO_ID)
        assert note.fields[3] == ""

    def test_fallback_field_empty_on_standard_note(self, sample_result):
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        assert note.fields[7] == ""


# -- _build_fallback_note -----------------------------------------------------

class TestBuildFallbackNote:
    def test_returns_genanki_note(self):
        assert isinstance(
            _build_fallback_note("obscure", "sentence here", _build_model(), VIDEO_ID),
            genanki.Note
        )

    def test_word_capitalised(self):
        note = _build_fallback_note("obscure", "sentence", _build_model(), VIDEO_ID)
        assert note.fields[0] == "Obscure"

    def test_fallback_note_field_populated(self):
        note = _build_fallback_note("obscure", "sentence", _build_model(), VIDEO_ID)
        assert "Note: no definition found" in note.fields[7]

    def test_transcript_example_in_field(self):
        note = _build_fallback_note("obscure", "the obscure word appeared", _build_model(), VIDEO_ID)
        assert "obscure" in note.fields[4]

    def test_no_definition_tag(self):
        note = _build_fallback_note("obscure", "sentence", _build_model(), VIDEO_ID)
        assert "no-definition" in note.tags

    def test_source_field_is_not_found(self):
        note = _build_fallback_note("obscure", "sentence", _build_model(), VIDEO_ID)
        assert note.fields[9] == "not_found"

    def test_none_transcript_becomes_empty_string(self):
        note = _build_fallback_note("obscure", None, _build_model(), VIDEO_ID)
        assert note.fields[4] == ""


# -- _build_output_path -------------------------------------------------------

class TestBuildOutputPath:
    def test_returns_path_object(self):
        assert isinstance(_build_output_path(VIDEO_ID), Path)

    def test_filename_contains_video_id(self):
        assert VIDEO_ID in _build_output_path(VIDEO_ID).name

    def test_filename_has_apkg_extension(self):
        assert _build_output_path(VIDEO_ID).suffix == ".apkg"

    def test_output_dir_created(self, tmp_output):
        _build_output_path(VIDEO_ID)
        assert tmp_output.exists()

    def test_different_calls_produce_different_filenames(self):
        p1 = _build_output_path(VIDEO_ID)
        time.sleep(1.1)
        p2 = _build_output_path(VIDEO_ID)
        assert p1.name != p2.name


# -- build_package ------------------------------------------------------------

class TestBuildPackage:
    def test_returns_package_result(self, sample_result, sample_snippets):
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result], [], sample_snippets)
        assert isinstance(result, PackageResult)

    def test_apkg_file_created(self, sample_result, sample_snippets):
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result], [], sample_snippets)
        assert result.path.exists()

    def test_apkg_is_valid_zip(self, sample_result, sample_snippets):
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result], [], sample_snippets)
        assert zipfile.is_zipfile(result.path)

    def test_raises_on_empty_inputs(self):
        with pytest.raises(ValueError):
            build_package(VIDEO_ID, DECK_NAME, [], [])

    def test_fallback_word_with_snippet_creates_card(self, sample_snippets):
        result = build_package(VIDEO_ID, DECK_NAME, [], ["develop"], sample_snippets)
        assert result.path.exists()

    def test_fallback_word_without_snippet_skipped(self):
        result = build_package(VIDEO_ID, DECK_NAME, [], ["philosophy"], {})
        assert result.path.exists()

    def test_subdeck_naming_accepted(self, sample_result):
        result = build_package(VIDEO_ID, "Language::English::Intermediate", [sample_result], [])
        assert result.path.exists()

    def test_video_id_in_filename(self, sample_result):
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result], [])
        assert VIDEO_ID in result.path.name

    def test_mixed_found_and_not_found(self, sample_result, sample_snippets):
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result], ["develop"], sample_snippets)
        assert zipfile.is_zipfile(result.path)

    # -- PackageResult count accuracy --------------------------------------

    def test_total_cards_equals_standard_plus_fallback(self, sample_result, sample_snippets):
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result], ["develop"], sample_snippets)
        assert result.total_cards == result.standard_count + result.fallback_count

    def test_standard_count_matches_found_list(self, sample_result, no_synonym_result):
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result, no_synonym_result], [])
        assert result.standard_count == 2
        assert result.fallback_count == 0

    def test_fallback_count_matches_resolved_not_found(self, sample_snippets):
        result = build_package(VIDEO_ID, DECK_NAME, [], ["develop"], sample_snippets)
        assert result.fallback_count == 1
        assert result.standard_count == 0

    def test_skipped_count_matches_unresolved_not_found(self):
        # "philosophy" has no snippet match in empty snippets dict — skipped
        result = build_package(VIDEO_ID, DECK_NAME, [], ["philosophy"], {})
        assert result.skipped_count == 1
        assert result.total_cards == 0

    def test_skipped_words_not_counted_in_total(self, sample_result, sample_snippets):
        # 1 standard card + 1 resolvable fallback + 1 unresolvable (skipped)
        result = build_package(
            VIDEO_ID, DECK_NAME,
            [sample_result],
            ["develop", "philosophy"],
            sample_snippets,
        )
        assert result.standard_count == 1
        assert result.fallback_count == 1
        assert result.skipped_count == 1
        assert result.total_cards == 2  # NOT 3 — skipped words produce no card


# -- Integration --------------------------------------------------------------

@pytest.mark.integration
class TestIntegration:
    def test_apkg_contains_collection(self, sample_result, sample_snippets):
        path = build_package(VIDEO_ID, DECK_NAME, [sample_result], [], sample_snippets)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        assert "collection.anki21" in names or "collection.anki2" in names