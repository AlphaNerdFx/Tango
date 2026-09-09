"""
test_cards.py

All file I/O uses tmp_path fixtures, no files written to the real filesystem.

Run unit tests:  pytest tests/test_cards.py -m "not integration"
"""

import json
import re
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
    _download_images,
    _image_field,
    _build_model,
    _build_note,
    _build_output_path,
    _find_in_snippets,
    _fold_snippets,
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
        # Every <span> that appears must have a matching </span>, no pill
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
        # Vacuous until 8 September 2026: it built the note and asserted
        # nothing, so it passed whatever went into field 6. The name says
        # what it should check, so it now checks it.
        note = _build_note(no_synonym_result, _build_model(), VIDEO_ID)
        assert note.fields[6] == "", "no synonyms must render as empty, not 'None'"
        assert note.fields[7] == ""

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
        # Also vacuous. A None example reaching the card as the four
        # characters "None" is the failure this name describes.
        note = _build_note(no_synonym_result, _build_model(), VIDEO_ID)
        assert note.fields[3] == ""
        assert note.fields[4] == ""

    def test_a_standard_note_is_not_marked_as_a_fallback(self, sample_result):
        # The old name referred to a field removed with FallbackNote, and the
        # body asserted nothing. What still matters is that a note built from
        # a real definition names its real source rather than "not_found".
        note = _build_note(sample_result, _build_model(), VIDEO_ID)
        assert note.fields[9] == "merriam-webster"
        assert note.fields[9] != "not_found"


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
        # Vacuous until 8 September 2026. A fallback card exists to carry the
        # transcript sentence, so "None" appearing there would be visible on
        # every card built for a word the video never clearly said.
        note = _build_fallback_note("obscure", None, _build_model(), VIDEO_ID)
        assert note.fields[5] == ""
        assert "None" not in note.fields[5]


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
        # "philosophy" has no snippet match in empty snippets dict, skipped
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
        assert result.total_cards == 2  # NOT 3, skipped words produce no card

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

    # -- Pronunciation on fallback cards (ADR-009 phase 1) -------------------

    def test_fallback_pronunciation_reaches_the_real_exported_card(self, tmp_path):
        # _build_fallback_note grew ipa/audio_url parameters that no call
        # site passed, so a card with no definition -- the one that needs
        # pronunciation most -- shipped without it while the index entry
        # supplying its example carried both. Reads the exported package
        # rather than the note object, so it also pins that the values land
        # at field indices 10 and 11 and not somewhere else.
        result = build_package(
            VIDEO_ID, DECK_NAME, [], ["philosophie"], {},
            not_found_examples={"philosophie": "Une phrase en français."},
            not_found_ipa={"philosophie": "\\fi.lɔ.zɔ.fi\\"},
            not_found_audio={"philosophie": "https://example.invalid/philo.ogg"},
        )
        extract_dir = tmp_path / "extracted"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract_dir)
        conn = sqlite3.connect(extract_dir / "collection.anki2")
        fields = conn.execute("SELECT flds FROM notes").fetchone()[0].split("\x1f")
        assert fields[10] == "\\fi.lɔ.zɔ.fi\\"                  # IPA
        assert "example.invalid/philo.ogg" in fields[11]        # Pronunciation
        assert fields[2] == "No definition found"               # still a fallback card

    def test_downloaded_audio_plays_in_the_card(self, tmp_path):
        # [sound:...] is what makes Anki play the file inline. A link opens a
        # browser, which is not reviewing.
        audio = tmp_path / "tango-de-haus-1234abcd.mp3"
        audio.write_bytes(b"ID3fake")   # genanki stats the file when packaging
        with patch.object(cards_module.media, "fetch_audio", return_value=audio):
            result = build_package(
                VIDEO_ID, DECK_NAME,
                [DefinitionResult(lemma="haus", definition="Gebaeude.", example_dict="Ex.",
                                  example_dict2=None, example_transcript="Ein Satz.",
                                  synonyms=[], antonyms=[], part_of_speech="noun",
                                  source="wiktionary", ipa="[haʊ̯s]",
                                  audio_url="https://e.invalid/de-haus.mp3")],
                [], language="de")
        extract = tmp_path / "x"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract)
            assert "media" in z.namelist()
        flds = sqlite3.connect(extract / "collection.anki2").execute(
            "SELECT flds FROM notes").fetchone()[0].split("\x1f")
        assert flds[11] == "[sound:tango-de-haus-1234abcd.mp3]"

    def test_a_failed_download_falls_back_to_a_link(self, tmp_path):
        # Per card, not per run. Sources do go down mid-run: dictionaryapi.dev
        # served one word's audio and returned 502 for another in the same
        # minute. Losing the recording must not lose the URL too.
        with patch.object(cards_module.media, "fetch_audio", return_value=None):
            result = build_package(
                VIDEO_ID, DECK_NAME,
                [DefinitionResult(lemma="haus", definition="Gebaeude.", example_dict="Ex.",
                                  example_dict2=None, example_transcript="Ein Satz.",
                                  synonyms=[], antonyms=[], part_of_speech="noun",
                                  source="wiktionary", ipa="[haʊ̯s]",
                                  audio_url="https://e.invalid/de-haus.mp3")],
                [], language="de")
        extract = tmp_path / "y"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract)
        flds = sqlite3.connect(extract / "collection.anki2").execute(
            "SELECT flds FROM notes").fetchone()[0].split("\x1f")
        assert "audio-link" in flds[11] and "e.invalid/de-haus.mp3" in flds[11]
        assert "[sound:" not in flds[11]

    def test_a_download_that_raises_does_not_kill_the_package(self, tmp_path):
        # By the time audio is fetched the expensive work is done. An
        # unexpected exception must cost one card its recording, not the run.
        with patch.object(cards_module.media, "fetch_audio",
                          side_effect=RuntimeError("boom")):
            result = build_package(
                VIDEO_ID, DECK_NAME,
                [DefinitionResult(lemma="haus", definition="Gebaeude.", example_dict="Ex.",
                                  example_dict2=None, example_transcript="Ein Satz.",
                                  synonyms=[], antonyms=[], part_of_speech="noun",
                                  source="wiktionary", ipa="[haʊ̯s]",
                                  audio_url="https://e.invalid/de-haus.mp3")],
                [], language="de")
        assert result.total_cards == 1

    def test_the_class_field_is_written_in_the_transcript_language(self):
        note = cards_module._build_note(
            DefinitionResult(lemma="haus", definition="Gebaeude.", example_dict="Ex.",
                             example_dict2=None, example_transcript="Ein Satz.",
                             synonyms=[], antonyms=[], part_of_speech="noun",
                             source="wiktionary", ipa=None, audio_url=None),
            cards_module._build_model(), VIDEO_ID, "de")
        at = dict(zip(cards_module.FIELDS, note.fields, strict=True))
        assert at["Class"] == "Substantiv"

    def test_the_class_field_follows_the_definition_language(self):
        # The pair. Class labels the definition, so it moves with it: a German
        # word defined in French reads "nom", not "Substantiv" and not "noun".
        note = cards_module._build_note(
            DefinitionResult(lemma="haus", definition="Bâtiment.", example_dict="Ex.",
                             example_dict2=None, example_transcript="Ein Satz.",
                             synonyms=[], antonyms=[], part_of_speech="noun",
                             source="wiktionary", ipa=None, audio_url=None),
            cards_module._build_model(), VIDEO_ID, "de", pos_language="fr")
        at = dict(zip(cards_module.FIELDS, note.fields, strict=True))
        assert at["Class"] == "nom"

    def test_build_package_routes_def_language_to_the_class_field(self, tmp_path):
        # The wiring, not the mapping. def_language has to reach the note
        # builder or the rule above never applies to a real run.
        with patch.object(cards_module.media, "fetch_audio", return_value=None):
            result = build_package(
                VIDEO_ID, DECK_NAME,
                [DefinitionResult(lemma="haus", definition="Bâtiment.", example_dict="Ex.",
                                  example_dict2=None, example_transcript="Ein Satz.",
                                  synonyms=[], antonyms=[], part_of_speech="noun",
                                  source="wiktionary", ipa=None, audio_url=None)],
                [], language="de", def_language="fr")
        extract = tmp_path / "poslang"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract)
        flds = sqlite3.connect(extract / "collection.anki2").execute(
            "SELECT flds FROM notes").fetchone()[0].split("\x1f")
        assert flds[1] == "nom"

    def test_the_embedded_count_reaches_the_caller(self):
        """
        The number that named the v0.5.2 rate-limit bug, put where it is seen.

        `_download_audio` already logged "13 of 377 cards will play inline"
        during the run that shipped broken -- at INFO, while the CLI
        configures WARNING. It was computed, formatted, and dropped. The
        count now goes to the progress callback the CLI prints, so a run
        where almost nothing embedded says so on screen.
        """
        lines = []
        with patch.object(cards_module.media, "fetch_audio", return_value=None):
            build_package(
                VIDEO_ID, DECK_NAME,
                [DefinitionResult(lemma="haus", definition="Gebaeude.", example_dict="Ex.",
                                  example_dict2=None, example_transcript="Ein Satz.",
                                  synonyms=[], antonyms=[], part_of_speech="noun",
                                  source="wiktionary", ipa="[haʊ̯s]",
                                  audio_url="https://e.invalid/de-haus.mp3")],
                [], language="de", progress=lines.append)
        summary = " ".join(lines)
        assert "0 of 1" in summary, f"embedded count not reported: {lines}"
        assert "link out" in summary, f"silent fallback not reported: {lines}"

    def test_a_run_with_no_audio_at_all_reports_nothing(self):
        # The partner. Reporting unconditionally would print "0 of 0
        # recordings embedded" on every English run without an index, which
        # reads as a failure and is not one.
        lines = []
        with patch.object(cards_module.media, "fetch_audio", return_value=None):
            build_package(
                VIDEO_ID, DECK_NAME,
                [DefinitionResult(lemma="haus", definition="Gebaeude.", example_dict="Ex.",
                                  example_dict2=None, example_transcript="Ein Satz.",
                                  synonyms=[], antonyms=[], part_of_speech="noun",
                                  source="wiktionary", ipa=None, audio_url=None)],
                [], language="de", progress=lines.append)
        assert not [line for line in lines if "recordings embedded" in line]

    def test_fallback_without_pronunciation_leaves_both_fields_empty(self):
        # The partner. A language with no index must produce exactly the old
        # card -- empty, not the string "None", which is what a bare
        # f-string interpolation of the absent URL would have produced.
        model = cards_module._build_model()
        note = cards_module._build_fallback_note(
            "develop", "A transcript sentence.", model, VIDEO_ID, "en",
        )
        at = dict(zip(cards_module.FIELDS, note.fields, strict=True))
        assert at["IPA"] == ""
        assert at["Pronunciation"] == ""

    # -- Language threaded into GUIDs (issue #14) --------------------------

    def test_language_passed_to_build_note(self, sample_result):
        # Verifies build_package() actually forwards its language argument
        # rather than always defaulting to "en" at the call site.
        #
        # Asserts on the language argument alone, not the whole call. This
        # used to use assert_called_once_with() on the full signature, which
        # coupled it to every unrelated parameter _build_note ever gains --
        # ADR-009's ipa/audio_url broke it while the behaviour it checks was
        # untouched. The sibling test below was already narrowed for exactly
        # this reason when issue #1 added dict_example.
        with patch.object(cards_module, "_build_note", wraps=cards_module._build_note) as spy:
            build_package(VIDEO_ID, DECK_NAME, [sample_result], [], language="fr")
            assert spy.call_count == 1
            assert spy.call_args[0][3] == "fr"

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
        # Same lemma resolved (found) and also passed as not_found, the
        # found note must win and no second fallback note gets written.
        result = build_package(
            VIDEO_ID, DECK_NAME, [sample_result], ["contaminate"], sample_snippets
        )
        assert result.standard_count == 1
        assert result.fallback_count == 0
        assert result.total_cards == 1


class TestCardLayout:
    """
    The card's own layout, which no other test covers because it is CSS and
    a template rather than a value in a field.

    Written after a review of a real deck, and revised after a second one.
    The first review found the image sized to the file, so every card came
    out a different height. The second found the whole card scrolling: a
    fully populated card was roughly 850-900px at the old fixed sizes.

    Both goals survive together in viewport units. The image is a share of
    the screen rather than a pixel count, so heights stay comparable without
    adding 240px to a card that already does not fit.
    """

    @staticmethod
    def _rules(selector: str) -> str:
        """
        The declarations inside one CSS rule, comments excluded.

        Asserting against the whole stylesheet is not enough, and this is
        not hypothetical: the comment above `.card-image img` explains the
        choice using the words "object-fit: contain", so a test searching
        the whole sheet passed with the declaration changed to `cover`.
        Mutation found it. The prose is not the rule.

        Tolerant of whitespace around the brace so reformatting the sheet
        cannot silently make every assertion here vacuous.
        """
        css = re.sub(r"/\*.*?\*/", "", _build_model().css, flags=re.S)
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert match, f"no rule for {selector} in the stylesheet"
        return match.group(1)

    def test_the_card_is_one_screen_tall(self):
        # The whole point of the column: the card ends where the screen
        # does. border-box because padding has to live inside that height,
        # not on top of it.
        block = self._rules(".card")
        assert "min-height: 100vh" in block
        assert "flex-direction: column" in block
        assert "box-sizing: border-box" in block

    def test_the_image_takes_the_height_the_text_leaves(self):
        # This replaced a fixed 30vh, which could not know whether the text
        # above it had used a fifth of the screen or all of it. `1 1` is
        # both halves: grow into a sparse card, shrink on a full one.
        assert "flex: 1 1 auto" in self._rules(".card-image")

    def test_the_image_has_a_floor_and_a_ceiling(self):
        # Without a floor a full card shows a sliver of photograph, and
        # without a ceiling a sparse one shows a wall of it, which is what
        # made every card a different height before 6 September.
        block = self._rules(".card-image")
        assert "min-height: 16vh" in block
        assert "max-height: 45vh" in block

    def test_the_image_is_bounded_by_its_box_and_by_the_viewport(self):
        # The pair to the two above, and it needs both halves. The percentage
        # tracks the box, which is what makes the picture yield to the text.
        # The viewport half is what survives when the box has no definite
        # height: a percentage against one computes to `none`, and an
        # unbounded photograph is the overflow this set out to remove.
        assert "max-height: min(100%, 45vh)" in self._rules(".card-image img")

    def test_the_flex_chain_survives_ankis_own_wrapper(self):
        # Anki renders the template inside `<div id="qa">` while `.card` is
        # the body, so without a rule for the wrapper the picture is a
        # grandchild of the column and `flex` on it does nothing.
        assert "flex: 1 1 auto" in self._rules("#qa")

    def test_the_image_is_not_a_fixed_pixel_box(self):
        # The pair to the test above, and a deliberate reversal: a fixed
        # 240x240 box kept card heights equal but added 240px to a card that
        # already scrolled on a phone.
        block = self._rules(".card-image img")
        assert "240px" not in block
        assert "width: auto" in block
        assert "height: auto" in block

    def test_the_image_still_cannot_overflow_sideways(self):
        assert "max-width: 100%" in self._rules(".card-image img")

    def test_the_aspect_ratio_is_preserved_inside_the_box(self):
        # contain letterboxes, cover crops. Cropping a photograph chosen to
        # show one thing can cut that thing out of frame.
        assert "object-fit: contain" in self._rules(".card-image img")

    def test_a_short_screen_tightens_the_text_not_the_picture(self):
        # The picture already yields continuously, so the media query has no
        # image rule left in it. What a short screen still buys is a line or
        # two of text, without touching a font size.
        css = re.sub(r"/\*.*?\*/", "", _build_model().css, flags=re.S)
        assert "@media (max-height: 640px)" in css
        short = css.split("@media (max-height: 640px)", 1)[1].split("}}", 1)[0]
        assert "line-height: 1.4" in short
        assert "card-image" not in short

    def test_the_credit_stays_with_the_picture(self):
        # As a sibling of the image box, the licence line was pushed to the
        # bottom of the screen while the picture stayed at the top, so a
        # sparse card showed a credit floating alone under empty space.
        template = _build_model().templates[0]["afmt"]
        block = template.split('<div class="card-image">', 1)[1].split("</div>", 1)[0]
        assert "{{Attribution}}" in block

    def test_every_font_size_scales_with_the_viewport(self):
        # A single absolute font-size is enough to make a card overflow on a
        # screen smaller than the one it was designed on.
        css = re.sub(r"/\*.*?\*/", "", _build_model().css, flags=re.S)
        fixed = re.findall(r"font-size:\s*\d+px", css)
        assert fixed == [], fixed

    def test_the_type_scale_has_a_readable_floor(self):
        # clamp() without a floor shrinks text to nothing on a small screen.
        css = re.sub(r"/\*.*?\*/", "", _build_model().css, flags=re.S)
        floors = [int(m) for m in re.findall(r"font-size:\s*clamp\((\d+)px", css)]
        assert floors, "no clamped font sizes found"
        assert min(floors) >= 9, floors

    def test_the_class_the_template_uses_for_audio_is_defined(self):
        # `.pronunciation` was used by the template and defined nowhere, so
        # the audio control sat unstyled.
        template = _build_model().templates[0]["afmt"]
        assert 'class="pronunciation"' in template
        assert self._rules(".pronunciation")

    def test_the_stylesheet_has_no_rules_nothing_uses(self):
        # `.word-secondary` and `.fallback-note` were styled for markup that
        # no longer exists, which is dead weight in every card.
        css = _build_model().css
        template = _build_model().templates[0]["afmt"]
        for dead in (".word-secondary", ".fallback-note"):
            assert dead not in css or dead[1:] in template, dead

    def test_the_image_and_its_credit_are_centred(self):
        assert "text-align: center" in self._rules(".card-image")

    def test_the_image_is_the_last_thing_on_the_card(self):
        # Below the definition, examples, synonyms, antonyms and audio, so
        # the image supports the word rather than leading with it.
        template = _build_model().templates[0]["afmt"]
        for earlier in ("{{#Synonyms}}", "{{#Antonyms}}", "{{#Pronunciation}}"):
            assert template.index(earlier) < template.index("{{#Image}}"), earlier

    def test_the_credit_sits_directly_under_its_image(self):
        template = _build_model().templates[0]["afmt"]
        assert template.index("{{#Image}}") < template.index("{{#Attribution}}")

    def test_the_sections_a_card_had_before_images_are_all_still_there(self):
        # A real regression report: a review deck showed no synonyms,
        # antonyms or audio. The cause was an empty test harness rather than
        # a lost template, but nothing pinned the template either way.
        template = _build_model().templates[0]["afmt"]
        for section in ("{{#Synonyms}}", "{{#Antonyms}}", "{{#IPA}}",
                        "{{#Pronunciation}}", "{{#Example from Youtube Video}}"):
            assert section in template, section


class TestImageField:
    """
    ADR-009 phase 3. The one place images deliberately differ from audio:
    _audio_field degrades to a link, _image_field degrades to nothing.
    """

    def test_a_downloaded_image_becomes_an_img_tag(self):
        assert _image_field("Q144.jpg") == '<img src="Q144.jpg">'

    def test_no_image_is_an_empty_field(self):
        assert _image_field(None) == ""

    def test_there_is_no_link_fallback(self):
        # A picture that has to be clicked is not a picture on a card: it
        # interrupts the review it was meant to help. Empty is the better
        # failure, and unlike audio there is no useful middle state.
        assert "href" not in _image_field("Q144.jpg")


def _an_image(qid="Q144", credit="Jane, CC0"):
    return cards_module.images.ImageResult(
        url="https://x/d.jpg", qid=qid, source="wikidata",
        filename="d.jpg", attribution=credit)


class TestDownloadImages:
    """
    The prefetch. Modelled on _download_audio, and like it nothing here may
    fail the run: the package is the expensive artefact.

    Every test patches `find_images`, the batched resolver that
    _download_images actually calls. Patching the single-lemma `find_image`
    leaves the real one reachable, and a unit test that quietly makes a
    Wikimedia request breaks CLAUDE.md 3.5 while still passing.
    """

    def test_no_candidates_costs_nothing(self):
        with patch.object(cards_module.images, "find_images") as find:
            assert _download_images([], "de") == ({}, {}, [])
        find.assert_not_called()

    def test_a_refused_lemma_contributes_nothing(self):
        # The gate refusing is the normal case, not an error.
        with patch.object(cards_module.images, "find_images", return_value={}), \
             patch.object(cards_module.images, "fetch_image") as fetch:
            assert _download_images(["freiheit"], "de") == ({}, {}, [])
        fetch.assert_not_called()

    def test_a_found_image_returns_its_name_credit_and_path(self, tmp_path):
        path = tmp_path / "Q144.jpg"
        path.write_bytes(b"x")
        with patch.object(cards_module.images, "find_images",
                          return_value={"hund": _an_image()}), \
             patch.object(cards_module.images, "fetch_image", return_value=path):
            names, credits, paths = _download_images(["hund"], "de")
        assert names == {"hund": "Q144.jpg"}
        assert credits == {"hund": "Jane, CC0"}
        assert paths == [path]

    def test_a_failed_download_is_not_a_card_with_a_broken_image(self):
        # Resolution succeeded, the download did not. The name must not be
        # recorded, or the card would reference a file the package lacks.
        with patch.object(cards_module.images, "find_images",
                          return_value={"hund": _an_image()}), \
             patch.object(cards_module.images, "fetch_image", return_value=None):
            assert _download_images(["hund"], "de") == ({}, {}, [])

    def test_a_raising_resolver_does_not_fail_the_run(self):
        with patch.object(cards_module.images, "find_images",
                          side_effect=RuntimeError("wikidata is down")):
            assert _download_images(["hund"], "de") == ({}, {}, [])

    def test_a_raising_download_does_not_fail_the_run(self):
        with patch.object(cards_module.images, "find_images",
                          return_value={"hund": _an_image()}), \
             patch.object(cards_module.images, "fetch_image",
                          side_effect=OSError("disk full")):
            assert _download_images(["hund"], "de") == ({}, {}, [])

    def test_a_picture_of_another_sense_is_dropped_before_it_is_downloaded(self):
        # The card would read "roof of the mouth" beside a photograph of a
        # palace. Dropping it after the download would cost the bandwidth
        # and the disk for a file thrown away, so the guard runs first.
        with patch.object(cards_module.images, "find_images",
                          return_value={"palais": _an_image()}), \
             patch.object(cards_module.wiktdata, "describes_other_sense",
                          return_value=True), \
             patch.object(cards_module.images, "fetch_image") as fetch:
            names, credits, paths = _download_images(
                ["palais"], "fr", senses={"palais": ("Paroi superieure", "noun")},
                definition_language="fr")
        assert (names, credits, paths) == ({}, {}, [])
        fetch.assert_not_called()

    def test_the_run_names_the_words_whose_picture_was_dropped(self):
        # Counted, a drop tells nobody whether the rule is working. Named,
        # it can be checked against the cards in a second. The definition
        # phase was changed for the same reason in v0.6.0.
        lines = []
        with patch.object(cards_module.images, "find_images",
                          return_value={"palais": _an_image(), "chien": _an_image()}), \
             patch.object(cards_module.wiktdata, "describes_other_sense",
                          side_effect=lambda lemma, *a, **k: lemma == "palais"), \
             patch.object(cards_module.images, "fetch_image", return_value=None):
            _download_images(["palais", "chien"], "fr", progress=lines.append,
                             senses={"palais": ("Paroi superieure", "noun"),
                                     "chien": ("Mammifere carnivore", "noun")},
                             definition_language="fr")
        dropped = [line for line in lines if "another sense" in line]
        assert dropped, f"no line named the drop: {lines}"
        assert "palais" in dropped[0]
        assert "chien" not in dropped[0]

    def test_an_agreeing_picture_is_kept(self, tmp_path):
        # The pair. Without it, a guard that dropped everything would pass.
        path = tmp_path / "Q144.jpg"
        path.write_bytes(b"x")
        with patch.object(cards_module.images, "find_images",
                          return_value={"chien": _an_image()}), \
             patch.object(cards_module.wiktdata, "describes_other_sense",
                          return_value=False), \
             patch.object(cards_module.images, "fetch_image", return_value=path):
            names, _, _ = _download_images(
                ["chien"], "fr", senses={"chien": ("Mammifere carnivore", "noun")},
                definition_language="fr")
        assert names == {"chien": "Q144.jpg"}

    def test_without_definitions_nothing_is_dropped(self, tmp_path):
        # review and backlog mode build cards from lemmas alone. No
        # definition to disagree with means no judgement to make, and the
        # guard must not be consulted at all rather than guessing.
        path = tmp_path / "Q144.jpg"
        path.write_bytes(b"x")
        with patch.object(cards_module.images, "find_images",
                          return_value={"chien": _an_image()}), \
             patch.object(cards_module.wiktdata, "describes_other_sense") as guard, \
             patch.object(cards_module.images, "fetch_image", return_value=path):
            names, _, _ = _download_images(["chien"], "fr")
        assert names == {"chien": "Q144.jpg"}
        guard.assert_not_called()

    def test_the_guard_reads_the_definitions_language_not_the_transcripts(self):
        # Under --def-lang the card's definition is French while the word is
        # German, so the concept has to be described in French for the two
        # to be comparable at all.
        with patch.object(cards_module.images, "find_images",
                          return_value={}) as find, \
             patch.object(cards_module.images, "fetch_image"):
            _download_images(["haus"], "de", senses={"haus": ("Une maison", "noun")},
                             definition_language="fr")
        assert find.call_args[1]["description_language"] == "fr"

    def test_resolution_is_one_call_for_many_lemmas(self):
        # The reason this is batched at all: resolution is paced by
        # Wikimedia, so 400 nouns must not become 400 round trips.
        with patch.object(cards_module.images, "find_images",
                          return_value={}) as find:
            _download_images(["a", "b", "c", "d"], "de")
        assert find.call_count == 1
        assert find.call_args[0][0] == ["a", "b", "c", "d"]


def _fields_of(apkg_path, tmp_path):
    """The first note's fields, read out of a built package."""
    extract_dir = tmp_path / "extracted_img"
    with zipfile.ZipFile(apkg_path) as z:
        z.extractall(extract_dir)
    conn = sqlite3.connect(extract_dir / "collection.anki2")
    try:
        return conn.execute("SELECT flds FROM notes").fetchone()[0].split("\x1f")
    finally:
        conn.close()


class TestImagesAreOffByDefault:
    """
    The safety property. ADR-009 requires the Part C measurement before
    images become a default, and the gate costs two network calls per word
    to say "no" to most of them. A run nobody asked for must pay neither.

    IMAGES_ENABLED is patched on pipeline.config rather than on cards,
    because build_package imports it at call time for exactly this reason.
    """

    def test_it_is_off_unless_asked_for(self):
        # Read from the source, not from the resolved value, and for the
        # reason CLAUDE.md 3.1 records about MODEL_ID: conftest forces
        # IMAGES_ENABLED to False for every test, so asserting the resolved
        # value would pass whatever the shipped default became.
        #
        # What must hold is that an unset environment means off. This used
        # to assert the literal `os.getenv("IMAGES_ENABLED", "")`, which
        # pinned the spelling rather than the intent (CLAUDE.md 18.5): the
        # test broke when config moved to a helper on 9 September 2026 while
        # the behaviour it names was byte-for-byte unchanged. It now reads
        # whatever default the assignment carries, through any helper.
        import inspect
        import re

        import pipeline.config

        source = inspect.getsource(pipeline.config)
        line = next((ln for ln in source.splitlines()
                     if ln.startswith("IMAGES_ENABLED")), None)
        assert line, "IMAGES_ENABLED is no longer assigned at config module level"

        match = re.search(r'\(\s*"IMAGES_ENABLED"\s*(?:,\s*"([^"]*)")?\s*\)', line)
        assert match, f"cannot read the default out of: {line}"
        default = match.group(1) or ""
        assert default.strip().lower() not in {"1", "true", "yes"}, (
            f"the shipped default for IMAGES_ENABLED is {default!r}, which is "
            "on. Unset must mean off: a picture roughly doubles a deck.")

    def test_asking_for_them_on_one_run_overrides_the_environment(self, sample_result):
        # What `--images` does. The default stays off, so a user turns them
        # on per run rather than editing .env to try the feature once.
        with patch("pipeline.config.IMAGES_ENABLED", False), \
             patch.object(cards_module.images, "find_images", return_value={}) as find:
            build_package("vidimg0005", "German", [sample_result], [], language="de",
                          images_enabled=True)
        find.assert_called_once()

    def test_refusing_them_on_one_run_overrides_the_environment(self, sample_result):
        # The pair, and the reason the parameter is three-state. Somebody
        # with IMAGES_ENABLED=true still needs one deck without pictures,
        # and a plain boolean flag could not express it.
        with patch("pipeline.config.IMAGES_ENABLED", True), \
             patch.object(cards_module.images, "find_images", return_value={}) as find:
            build_package("vidimg0006", "German", [sample_result], [], language="de",
                          images_enabled=False)
        find.assert_not_called()

    def test_no_answer_leaves_the_environment_to_decide(self, sample_result):
        # The third state: the flag was not given at all.
        with patch("pipeline.config.IMAGES_ENABLED", True), \
             patch.object(cards_module.images, "find_images", return_value={}) as find:
            build_package("vidimg0007", "German", [sample_result], [], language="de",
                          images_enabled=None)
        find.assert_called_once()

    def test_a_fallback_card_can_get_a_picture(self, sample_result, tmp_path):
        # A word with no definition still becomes a card, and a picture is
        # worth more there than anywhere: the card has nothing else on it.
        # The candidate list read `not_found_audio`, the Commons recording
        # map, so a fallback word without a recording was never offered one.
        img = tmp_path / "Q144.jpg"
        img.write_bytes(b"x")
        with patch("pipeline.config.IMAGES_ENABLED", True), \
             patch.object(cards_module.images, "find_images") as find, \
             patch.object(cards_module.images, "fetch_image", return_value=img):
            find.return_value = {}
            build_package("vidimg0008", "German", [], ["hund"], language="de",
                          snippets={"hund": "Der Hund bellt."})
        assert "hund" in find.call_args[0][0], (
            "a fallback lemma was never offered to the image resolver")

    def test_no_network_when_disabled(self, sample_result):
        with patch("pipeline.config.IMAGES_ENABLED", False), \
             patch.object(cards_module.images, "find_images") as find:
            build_package("vidimg0001", "German", [sample_result], [], language="de")
        find.assert_not_called()

    def test_the_fields_are_empty_when_disabled(self, sample_result, tmp_path):
        with patch("pipeline.config.IMAGES_ENABLED", False):
            result = build_package("vidimg0002", "German", [sample_result], [],
                                   language="de")
        fields = _fields_of(result.path, tmp_path)
        assert fields[12] == ""
        assert fields[13] == ""

    def test_enabling_it_populates_both_fields(self, sample_result, tmp_path):
        img = tmp_path / "Q144.jpg"
        img.write_bytes(b"x")
        with patch("pipeline.config.IMAGES_ENABLED", True), \
             patch.object(cards_module.images, "find_images",
                          return_value={"contaminate": _an_image()}), \
             patch.object(cards_module.images, "fetch_image", return_value=img):
            result = build_package("vidimg0003", "German", [sample_result], [],
                                   language="de")
        fields = _fields_of(result.path, tmp_path)
        assert fields[12] == '<img src="Q144.jpg">'
        assert fields[13] == "Jane, CC0"

    def test_the_image_is_packaged_so_the_card_can_render_it(
            self, sample_result, tmp_path):
        # A note referencing Q144.jpg with no Q144.jpg in the package is a
        # broken image on every card that has one.
        img = tmp_path / "Q144.jpg"
        img.write_bytes(b"x")
        with patch("pipeline.config.IMAGES_ENABLED", True), \
             patch.object(cards_module.images, "find_images",
                          return_value={"contaminate": _an_image()}), \
             patch.object(cards_module.images, "fetch_image", return_value=img):
            result = build_package("vidimg0004", "German", [sample_result], [],
                                   language="de")
        with zipfile.ZipFile(result.path) as z:
            media = json.loads(z.read("media"))
        assert "Q144.jpg" in media.values()


# -- Integration --------------------------------------------------------------

@pytest.mark.integration
class TestIntegration:
    def test_apkg_contains_collection(self, sample_result, sample_snippets):
        path = build_package(VIDEO_ID, DECK_NAME, [sample_result], [], sample_snippets)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        assert "collection.anki21" in names or "collection.anki2" in names

# -- Surface-form transcript matching -----------------------------------------
#
# The lemma frequently does not appear in the transcript at all: a French
# video says "sais", the lemma is "savoir". Searching the lemma alone left
# 137 of 1036 cards on a real run with an empty "Example from Youtube
# Video" despite the word being right there in a conjugated form.

class TestSurfaceFormMatching:

    @pytest.fixture
    def french_snippets(self) -> dict:
        return {
            0.0: {"end": 3.0, "text": "je sais pas ce qu'il faut faire"},
            3.0: {"end": 6.0, "text": "on écoutez bien la suite"},
            "_full_text": "full", "_language_code": "fr", "_snippet_count": 2,
        }

    def test_lemma_absent_from_transcript_is_found_via_surface_form(
        self, french_snippets
    ):
        # "savoir" never appears; "sais" does.
        assert _find_in_snippets("savoir", french_snippets) is None
        found = _find_in_snippets("savoir", french_snippets, ["sais"])
        assert found == "je sais pas ce qu'il faut faire"

    def test_lemma_is_preferred_over_surface_forms_when_both_match(self):
        # The lemma is the canonical form, so where it does appear it gives
        # the cleaner sentence. Ordering must not be left to chance.
        snippets = {
            0.0: {"end": 1.0, "text": "les formes conjuguées viennent après"},
            1.0: {"end": 2.0, "text": "le verbe savoir apparait ici"},
            "_full_text": "f", "_language_code": "fr", "_snippet_count": 2,
        }
        found = _find_in_snippets("savoir", snippets, ["conjuguées"])
        assert found == "le verbe savoir apparait ici"

    def test_no_surface_forms_behaves_exactly_as_before(self, sample_snippets):
        # Callers that pass nothing (review and backlog modes) are unaffected.
        assert _find_in_snippets("develop", sample_snippets) is not None
        assert _find_in_snippets("develop", sample_snippets, None) is not None

    def test_surface_form_identical_to_lemma_is_not_searched_twice(
        self, french_snippets
    ):
        assert _find_in_snippets("sais", french_snippets, ["sais"]) is not None

    def test_the_fold_keeps_transcript_lines_and_drops_the_metadata_keys(
        self, sample_snippets
    ):
        # `snippets` mixes float-keyed lines with string-keyed metadata.
        # Folding must carry the three lines and none of the metadata,
        # or "_language_code" becomes a searchable sentence.
        folded = _fold_snippets(sample_snippets)
        assert len(folded) == 3
        texts = [text for text, _ in folded]
        assert "So companies had to develop permanent solutions" in texts
        assert "full text here" not in texts
        assert all(lowered == text.casefold() for text, lowered in folded)

    def test_a_prefolded_transcript_gives_the_same_answer_as_folding_inside(
        self, sample_snippets, french_snippets
    ):
        # The optimization is only safe if the fast path cannot disagree
        # with the slow one. Matches in the LAST line matter as much as the
        # first: a fast path that read only lines[0] agreed with the slow
        # one on every first-line case, so those alone cannot catch it.
        cases = [
            ("develop",      sample_snippets, None),   # first line
            ("contaminated", sample_snippets, None),   # middle line
            ("photographic", sample_snippets, None),   # last line
            ("philosophy",   sample_snippets, None),   # absent
            ("savoir",       french_snippets, ["sais"]),      # first line
            ("ecouter",      french_snippets, ["écoutez"]),   # last line
        ]
        for lemma, snippets, forms in cases:
            slow = _find_in_snippets(lemma, snippets, forms)
            fast = _find_in_snippets(lemma, snippets, forms,
                                     lines=_fold_snippets(snippets))
            assert slow == fast, lemma

    def test_a_word_absent_as_a_substring_is_still_reported_absent(
        self, sample_snippets
    ):
        # The substring pre-filter skips the regex entirely for these. If it
        # ever skipped a word that IS present, this file's other tests would
        # catch it; this pins the other direction, that skipping still
        # returns None rather than a stale or wrong line.
        assert _find_in_snippets("philosophy", sample_snippets) is None
        assert _find_in_snippets("zzz", sample_snippets, ["qqq"]) is None

    def test_the_pre_filter_folds_the_way_the_regex_does(self):
        # The pre-filter must never skip a line the regex would have
        # matched. re.IGNORECASE matches "s" against a long s, and
        # str.lower() does not fold it, so a lower() pre-filter would skip
        # this line and the card would silently lose its example.
        # Checked against re directly rather than assumed: plain "STRASSE"
        # does not distinguish the two, and German eszett does not either.
        snippets = {0.0: {"end": 1.0, "text": "der \u017fchnee f\u00e4llt"},
                    "_full_text": "f", "_snippet_count": 1}
        assert re.compile(r"\bschnee\w*", re.IGNORECASE).search(
            "der \u017fchnee f\u00e4llt"), "premise: the regex does match a long s"
        assert "schnee" not in "der \u017fchnee f\u00e4llt".lower(), (
            "premise: lower() would skip it")
        assert _find_in_snippets("schnee", snippets) is not None

    def test_build_package_folds_the_transcript_once_not_once_per_word(
        self, tmp_path, monkeypatch
    ):
        # This is the optimization itself. Folding is proportional to
        # transcript length, so doing it per word made the cost length
        # times vocabulary: 120,000 casefolds on a 400-card deck over a
        # 300-line transcript, against 300.
        calls = []
        real = cards_module._fold_snippets
        monkeypatch.setattr(cards_module, "_fold_snippets",
                            lambda s: calls.append(1) or real(s))
        monkeypatch.setattr(cards_module, "OUTPUT_DIR", tmp_path)
        found = [
            DefinitionResult(f"word{i}", "a definition", "One.", "Two.",
                             None, [], [], "noun", "test")
            for i in range(5)
        ]
        snippets = {float(i): {"end": i + 1.0, "text": f"line {i} here"}
                    for i in range(4)}
        snippets["_full_text"] = "f"
        cards_module.build_package(VIDEO_ID, DECK_NAME, found, ["gone"],
                                   language="en", snippets=snippets)
        assert len(calls) == 1, f"folded {len(calls)} times, expected once"

    def test_build_package_uses_surface_forms_for_fallback_cards(
        self, french_snippets, tmp_path
    ):
        result = build_package(
            VIDEO_ID, DECK_NAME, [], ["savoir"], french_snippets,
            language="fr", surface_forms={"savoir": ["sais"]},
        )
        assert result.fallback_count == 1
        assert result.skipped_count == 0
        extract_dir = tmp_path / "sf"
        with zipfile.ZipFile(result.path) as z:
            z.extractall(extract_dir)
        conn = sqlite3.connect(extract_dir / "collection.anki2")
        fields = conn.execute("SELECT flds FROM notes").fetchone()[0].split("\x1f")
        assert fields[5] == "je sais pas ce qu'il faut faire"  # Example from Youtube

    def test_build_package_backfills_standard_cards_missing_an_example(
        self, french_snippets, tmp_path
    ):
        # fetch_definitions() searched the lemma alone, so a found word can
        # arrive here with an empty transcript example that surface forms
        # can still fill.
        result = DefinitionResult(
            lemma="savoir", definition="Connaitre.",
            example_dict=None, example_dict2=None, example_transcript=None,
            synonyms=[], antonyms=[], part_of_speech="verb", source="wiktionary",
        )
        build_package(
            VIDEO_ID, DECK_NAME, [result], [], french_snippets,
            language="fr", surface_forms={"savoir": ["sais"]},
        )
        assert result.example_transcript == "je sais pas ce qu'il faut faire"
