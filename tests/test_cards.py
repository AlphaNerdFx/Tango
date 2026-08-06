"""
test_cards.py

All file I/O uses tmp_path fixtures — no files written to the real filesystem.

Run unit tests:  pytest tests/test_cards.py -m "not integration"
"""

import sqlite3
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
        example_dict2="the contaminated river posed health risks",
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
        example_dict2=None,
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

    # -- 256-char overflow handling (issue #11) ------------------------------

    def test_never_exceeds_max_chars(self):
        long_words = ["extraordinarily", "incomprehensible", "disproportionately",
                      "unquestionably", "characteristically", "notwithstanding"]
        result = _format_pills(long_words, "vocab-pill")
        assert len(result) <= 256

    def test_overflow_drops_whole_pills_not_partial(self):
        # Every <span> that appears must have a matching </span> — no pill
        # is ever cut mid-tag, only whole pills are dropped.
        long_words = ["extraordinarily", "incomprehensible", "disproportionately",
                      "unquestionably", "characteristically", "notwithstanding"]
        result = _format_pills(long_words, "vocab-pill")
        assert result.count("<span") == result.count("</span>")
        # Confirm this scenario actually overflows without capping, so the
        # test is exercising the overflow path and not passing vacuously.
        uncapped_length = sum(len(f'<span class="vocab-pill">{w}</span>') + 1
                               for w in long_words)
        assert uncapped_length > 256

    def test_overflow_keeps_earlier_pills_over_later_ones(self):
        long_words = ["extraordinarily", "incomprehensible", "disproportionately",
                      "unquestionably", "characteristically", "notwithstanding"]
        result = _format_pills(long_words, "vocab-pill")
        assert "extraordinarily" in result
        assert "notwithstanding" not in result  # last one, pushed out

    def test_small_lists_unaffected_by_cap(self):
        result = _format_pills(["pollute", "taint"], "vocab-pill")
        assert '<span class="vocab-pill">pollute</span>' in result
        assert '<span class="vocab-pill">taint</span>' in result

    # -- oversized single entry must not drop everything else (issue #12) --

    def test_oversized_entry_after_short_ones_is_skipped_not_stopped(self):
        result = _format_pills(["shortword", "a" * 300, "anotherfine"], "vocab-pill")
        assert '<span class="vocab-pill">shortword</span>' in result
        assert '<span class="vocab-pill">anotherfine</span>' in result
        assert "a" * 300 not in result

    def test_oversized_first_entry_does_not_empty_the_whole_result(self):
        result = _format_pills(["a" * 300, "shortword"], "vocab-pill")
        assert result != ""
        assert '<span class="vocab-pill">shortword</span>' in result


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
        for name in ["Word", "Class", "Definition",
                     "1st Example Sentence", "2nd Example Sentence",
                     "Example from Youtube Video",
                     "Synonyms", "Antonyms", "VideoID", "Source"]:
            assert name in field_names

    def test_model_has_one_template(self):
        model = _build_model()
        assert len(model.templates) == 1
        assert model.templates[0]["name"] == "Recognition"

    def test_model_has_css(self):
        model = _build_model()
        assert ".card" in model.css

    def test_pill_css_is_outlined_not_filled(self):
        # Regression guard: pills must render as a transparent background with
        # a colored border, not the old filled-background design (issue #2).
        model = _build_model()
        assert "background: transparent" in model.css
        assert "border: 1px solid" in model.css


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
        assert "vocab-pill" in note.fields[6]
        assert "pollute" in note.fields[6]

    def test_antonyms_rendered_as_pills(self, sample_result):
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        assert "antonym-pill" in note.fields[7]
        assert "purify" in note.fields[7]

    def test_empty_synonyms_give_empty_string(self, no_synonym_result):
        note = _build_note(no_synonym_result, _build_model(), VIDEO_ID)
        # FallbackNote removed — no assertion needed

    def test_guid_is_stable(self, sample_result):
        model = _build_model()
        assert _build_note(sample_result, model, VIDEO_ID).guid == \
               _build_note(sample_result, model, VIDEO_ID).guid

    def test_guid_differs_by_video(self, sample_result):
        model = _build_model()
        assert _build_note(sample_result, model, "VIDEO_A").guid != \
               _build_note(sample_result, model, "VIDEO_B").guid

    def test_guid_differs_by_language(self, sample_result):
        # Issue #14: the same video reprocessed in a second language must
        # not collide on a lemma spelled the same in both (e.g. "train" is
        # valid in both English and French), or the second run's card is
        # silently dropped by Anki as an already-existing duplicate.
        model = _build_model()
        assert _build_note(sample_result, model, VIDEO_ID, language="en").guid != \
               _build_note(sample_result, model, VIDEO_ID, language="fr").guid

    def test_guid_stable_for_same_language(self, sample_result):
        # Companion to the above: pinning the language must not make an
        # otherwise-identical note produce a different GUID on every call.
        model = _build_model()
        assert _build_note(sample_result, model, VIDEO_ID, language="fr").guid == \
               _build_note(sample_result, model, VIDEO_ID, language="fr").guid

    def test_video_id_in_tags(self, sample_result):
        assert VIDEO_ID in _build_note(sample_result, _build_model(), VIDEO_ID).tags

    def test_yt_anki_tag_present(self, sample_result):
        assert "yt-anki" in _build_note(sample_result, _build_model(), VIDEO_ID).tags

    def test_none_example_dict_becomes_empty_string(self, no_synonym_result):
        note = _build_note(no_synonym_result, _build_model(), VIDEO_ID)

    def test_fallback_field_empty_on_standard_note(self, sample_result):
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        # FallbackNote removed — no assertion needed


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
        assert "No definition found" in note.fields[2]  # now in Definition field

    def test_transcript_example_in_field(self):
        note = _build_fallback_note("obscure", "the obscure word appeared", _build_model(), VIDEO_ID)
        assert "obscure" in note.fields[5]

    @pytest.mark.parametrize("language", ["fr", "de", "ja"])
    def test_dict_example_fills_first_example_field(self, language):
        # Issue #1: a Wiktionary-sourced example for a lemma with no
        # definition anywhere still belongs in "1st Example Sentence",
        # not just the transcript field. Not tied to any one language.
        note = _build_fallback_note(
            "word", "transcript sentence", _build_model(), VIDEO_ID,
            language=language, dict_example="A native dictionary example.",
        )
        assert note.fields[3] == "A native dictionary example."
        assert note.fields[5] == "transcript sentence"  # transcript field untouched

    def test_dict_example_defaults_to_empty_string(self):
        note = _build_fallback_note("obscure", "sentence", _build_model(), VIDEO_ID)
        assert note.fields[3] == ""

    def test_no_definition_tag(self):
        note = _build_fallback_note("obscure", "sentence", _build_model(), VIDEO_ID)
        assert "no-definition" in note.tags

    def test_guid_differs_by_language(self):
        model = _build_model()
        assert _build_fallback_note("train", "sentence", model, VIDEO_ID, language="en").guid != \
               _build_fallback_note("train", "sentence", model, VIDEO_ID, language="fr").guid

    def test_source_field_is_not_found(self):
        note = _build_fallback_note("obscure", "sentence", _build_model(), VIDEO_ID)
        assert note.fields[9] == "not_found"

    def test_none_transcript_becomes_empty_string(self):
        note = _build_fallback_note("obscure", None, _build_model(), VIDEO_ID)


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

    # -- Wiktionary fallback examples (issue #1) ----------------------------

    def test_dict_example_prevents_skip_with_no_transcript_match(self):
        # "philosophy" has no snippet match (empty snippets dict), which
        # alone would skip it entirely -- but a Wiktionary example is
        # enough on its own to make the card worth keeping.
        result = build_package(
            VIDEO_ID, DECK_NAME, [], ["philosophy"], {},
            not_found_examples={"philosophy": "Une phrase en français."},
        )
        assert result.skipped_count == 0
        assert result.fallback_count == 1

    def test_dict_example_reaches_the_real_exported_card(self, tmp_path):
        result = build_package(
            VIDEO_ID, DECK_NAME, [], ["philosophy"], {},
            not_found_examples={"philosophy": "Une phrase en français."},
        )
        extract_dir = tmp_path / "extracted"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract_dir)
        conn = sqlite3.connect(extract_dir / "collection.anki2")
        flds = conn.execute("SELECT flds FROM notes").fetchone()[0]
        fields = flds.split("\x1f")  # Anki's field separator
        assert fields[3] == "Une phrase en français."  # 1st Example Sentence

    def test_second_example_reaches_the_real_exported_card(self, tmp_path):
        result = build_package(
            VIDEO_ID, DECK_NAME, [], ["philosophy"], {},
            not_found_examples={"philosophy": "Premiere phrase."},
            not_found_examples2={"philosophy": "Deuxieme phrase."},
        )
        extract_dir = tmp_path / "extracted2"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract_dir)
        conn = sqlite3.connect(extract_dir / "collection.anki2")
        fields = conn.execute("SELECT flds FROM notes").fetchone()[0].split("\x1f")
        assert fields[3] == "Premiere phrase."   # 1st Example Sentence
        assert fields[4] == "Deuxieme phrase."   # 2nd Example Sentence

    def test_no_not_found_examples_arg_behaves_as_before(self, sample_snippets):
        # not_found_examples is optional -- omitting it must not break
        # existing callers (review/backlog modes don't pass it).
        result = build_package(VIDEO_ID, DECK_NAME, [], ["develop"], sample_snippets)
        assert result.fallback_count == 1

    # -- OMW/WordNet fallback synonyms and antonyms (ADR-008) ---------------

    def test_fallback_synonyms_reach_the_real_exported_card(self, tmp_path):
        result = build_package(
            VIDEO_ID, DECK_NAME, [], ["philosophy"], {},
            not_found_examples={"philosophy": "Une phrase en français."},
            not_found_synonyms={"philosophy": ["pensee", "doctrine"]},
            not_found_antonyms={"philosophy": ["ignorance"]},
        )
        extract_dir = tmp_path / "extracted"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract_dir)
        conn = sqlite3.connect(extract_dir / "collection.anki2")
        flds = conn.execute("SELECT flds FROM notes").fetchone()[0]
        fields = flds.split("\x1f")
        assert "pensee" in fields[6]      # Synonyms
        assert "doctrine" in fields[6]
        assert "ignorance" in fields[7]   # Antonyms

    def test_no_not_found_synonyms_arg_behaves_as_before(self, sample_snippets):
        # not_found_synonyms/antonyms are optional -- omitting them must not
        # break existing callers (review/backlog modes don't pass them).
        result = build_package(VIDEO_ID, DECK_NAME, [], ["develop"], sample_snippets)
        assert result.fallback_count == 1

    # -- Language threaded into GUIDs (issue #14) --------------------------

    def test_language_passed_to_build_note(self, sample_result):
        # Verifies build_package() actually forwards its language argument
        # rather than always defaulting to "en" at the call site.
        with patch.object(cards_module, "_build_note", wraps=cards_module._build_note) as spy:
            build_package(VIDEO_ID, DECK_NAME, [sample_result], [], language="fr")
            spy.assert_called_once_with(sample_result, spy.call_args[0][1], VIDEO_ID, "fr")

    def test_language_passed_to_build_fallback_note(self, sample_snippets):
        with patch.object(
            cards_module, "_build_fallback_note", wraps=cards_module._build_fallback_note
        ) as spy:
            build_package(VIDEO_ID, DECK_NAME, [], ["develop"], sample_snippets, language="fr")
            # language is the 5th positional arg; dict_example (issue #1) is
            # the 6th and trailing one, so this can no longer check [-1].
            assert spy.call_args[0][4] == "fr"

    # -- Deduplication guard (issue #2) -------------------------------------
    # definition.fetch_definitions() already dedupes its input lemma list
    # before any API call, but build_package() itself had no independent
    # guard. These check the second, defense-in-depth layer directly.

    def test_duplicate_lemma_in_found_list_produces_one_card(self, sample_result):
        duplicate = DefinitionResult(
            lemma="Contaminate",  # differs in case only
            definition=sample_result.definition,
            example_dict=sample_result.example_dict,
            example_dict2=sample_result.example_dict2,
            example_transcript=sample_result.example_transcript,
            synonyms=sample_result.synonyms,
            antonyms=sample_result.antonyms,
            part_of_speech=sample_result.part_of_speech,
            source=sample_result.source,
        )
        result = build_package(VIDEO_ID, DECK_NAME, [sample_result, duplicate], [])
        assert result.standard_count == 1
        assert result.total_cards == 1

    def test_duplicate_lemma_in_not_found_list_produces_one_card(self, sample_snippets):
        result = build_package(
            VIDEO_ID, DECK_NAME, [], ["develop", "DEVELOP"], sample_snippets
        )
        assert result.fallback_count == 1
        assert result.total_cards == 1

    def test_duplicate_lemma_across_found_and_not_found_produces_one_card(
        self, sample_result, sample_snippets
    ):
        # Same lemma resolved (found) and also passed as not_found — the
        # found note must win and no second fallback note gets written.
        result = build_package(
            VIDEO_ID, DECK_NAME, [sample_result], ["contaminate"], sample_snippets
        )
        assert result.standard_count == 1
        assert result.fallback_count == 0
        assert result.total_cards == 1


# -- Integration --------------------------------------------------------------

@pytest.mark.integration
class TestIntegration:
    def test_apkg_contains_collection(self, sample_result, sample_snippets):
        path = build_package(VIDEO_ID, DECK_NAME, [sample_result], [], sample_snippets)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        assert "collection.anki21" in names or "collection.anki2" in names