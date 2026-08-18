"""
Tests for language resolution, deck name inference, BCP-47 partial matching,
and transcript selection preference (manual over auto-generated).

Run: pytest tests/test_language.py -m "not integration"
"""

from unittest.mock import MagicMock, patch

import pytest

import pipeline.language as language_module
from pipeline.language import (
    LANGUAGE_MAP,
    SPACY_MODELS,
    LanguageResolutionError,
    SpacyModelUnavailableError,
    _infer_from_deck_name,
    get_spacy_model,
    list_supported_languages,
    resolve_language_code,
    resolve_transcript,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_transcript(language_code: str, is_generated: bool = False) -> MagicMock:
    t = MagicMock()
    t.language_code = language_code
    t.is_generated  = is_generated
    return t


def _make_transcript_list(transcripts: list, video_id: str = "TEST123") -> MagicMock:
    tl = MagicMock()
    tl.video_id = video_id
    tl.__iter__ = lambda self: iter(transcripts)

    def find_transcript(codes):
        from youtube_transcript_api._errors import NoTranscriptFound
        for code in codes:
            for t in transcripts:
                if t.language_code == code:
                    return t
        raise NoTranscriptFound(video_id, codes, [t.language_code for t in transcripts])

    tl.find_transcript = find_transcript
    return tl


# ── LANGUAGE_MAP coverage ─────────────────────────────────────────────────────

class TestLanguageMap:

    def test_contains_40_distinct_codes(self):
        codes = set(LANGUAGE_MAP.values())
        assert len(codes) >= 40

    def test_english_maps_to_en(self):
        assert LANGUAGE_MAP["english"] == "en"

    def test_french_variants_all_map_to_fr(self):
        for name in ["french", "français", "francais", "frances", "französisch"]:
            assert LANGUAGE_MAP[name] == "fr", f"Failed for: {name}"

    def test_german_variants_all_map_to_de(self):
        for name in ["german", "deutsch", "allemand", "aleman"]:
            assert LANGUAGE_MAP[name] == "de", f"Failed for: {name}"

    def test_japanese_maps_to_ja(self):
        assert LANGUAGE_MAP["japanese"] == "ja"
        assert LANGUAGE_MAP["日本語"] == "ja"

    def test_simplified_chinese_maps_to_zh_cn(self):
        assert LANGUAGE_MAP["chinese"] == "zh-CN"
        assert LANGUAGE_MAP["mandarin"] == "zh-CN"
        assert LANGUAGE_MAP["simplified chinese"] == "zh-CN"

    def test_traditional_chinese_maps_to_zh_tw(self):
        assert LANGUAGE_MAP["traditional chinese"] == "zh-TW"
        assert LANGUAGE_MAP["cantonese"] == "zh-TW"

    def test_arabic_maps_to_ar(self):
        assert LANGUAGE_MAP["arabic"] == "ar"
        assert LANGUAGE_MAP["العربية"] == "ar"

    def test_russian_maps_to_ru(self):
        assert LANGUAGE_MAP["russian"] == "ru"
        assert LANGUAGE_MAP["русский"] == "ru"

    def test_all_values_are_nonempty_strings(self):
        for name, code in LANGUAGE_MAP.items():
            assert isinstance(code, str) and len(code) >= 2, \
                f"Invalid code for '{name}': {code!r}"

    def test_all_keys_are_lowercase(self):
        for key in LANGUAGE_MAP:
            assert key == key.lower(), f"Key not lowercase: {key!r}"


# ── _infer_from_deck_name ─────────────────────────────────────────────────────

class TestInferFromDeckName:

    def test_simple_language_name(self):
        assert _infer_from_deck_name("French") == "fr"

    def test_case_insensitive(self):
        assert _infer_from_deck_name("FRENCH") == "fr"
        assert _infer_from_deck_name("french") == "fr"
        assert _infer_from_deck_name("French") == "fr"

    def test_compound_deck_name(self):
        assert _infer_from_deck_name("Netflix French") == "fr"

    def test_subdeck_notation(self):
        assert _infer_from_deck_name("Language::French::B2") == "fr"

    def test_endonym_works(self):
        assert _infer_from_deck_name("Deutsch") == "de"
        assert _infer_from_deck_name("Français") == "fr"

    def test_multi_word_language_name(self):
        assert _infer_from_deck_name("Traditional Chinese") == "zh-TW"
        assert _infer_from_deck_name("Simplified Chinese") == "zh-CN"

    def test_unrecognised_deck_returns_none(self):
        assert _infer_from_deck_name("Youssef's Study") is None
        assert _infer_from_deck_name("Vocab") is None
        assert _infer_from_deck_name("Words I Don't Know") is None

    def test_level_indicator_ignored(self):
        # "B2" is not a language — should still find "German"
        assert _infer_from_deck_name("German B2") == "de"

    def test_underscore_and_hyphen_normalised(self):
        assert _infer_from_deck_name("french_vocab") == "fr"
        assert _infer_from_deck_name("french-vocab") == "fr"

    def test_empty_string_returns_none(self):
        assert _infer_from_deck_name("") is None


# ── resolve_language_code ─────────────────────────────────────────────────────

class TestResolveLanguageCode:

    def test_flag_wins_over_deck_name(self):
        result = resolve_language_code(language_flag="de", deck_name="French")
        assert result == "de"

    def test_flag_is_returned_as_is(self):
        result = resolve_language_code(language_flag="zh-TW", deck_name=None)
        assert result == "zh-tw"  # lowercased

    def test_deck_name_used_when_no_flag(self):
        result = resolve_language_code(language_flag=None, deck_name="French")
        assert result == "fr"

    def test_subdeck_resolved_from_name(self):
        result = resolve_language_code(language_flag=None, deck_name="Language::German::B2")
        assert result == "de"

    def test_raises_when_neither_resolves(self):
        with pytest.raises(LanguageResolutionError):
            resolve_language_code(language_flag=None, deck_name="My Vocab")

    def test_raises_when_no_flag_no_deck(self):
        with pytest.raises(LanguageResolutionError):
            resolve_language_code(language_flag=None, deck_name=None)

    def test_error_message_contains_deck_name(self):
        with pytest.raises(LanguageResolutionError) as exc_info:
            resolve_language_code(language_flag=None, deck_name="My Vocab")
        assert "My Vocab" in str(exc_info.value)

    def test_error_message_contains_flag_hint(self):
        with pytest.raises(LanguageResolutionError) as exc_info:
            resolve_language_code(language_flag=None, deck_name="My Vocab")
        assert "LANGUAGE" in str(exc_info.value) or "--language" in str(exc_info.value).lower()

    def test_none_flag_empty_string_treated_as_absent(self):
        # Empty string should not be treated as a valid language code
        result = resolve_language_code(language_flag="", deck_name="French")
        assert result == "fr"


# ── resolve_transcript ────────────────────────────────────────────────────────

class TestResolveTranscript:

    def test_exact_match_returned(self):
        t_fr = _make_transcript("fr", is_generated=False)
        tl = _make_transcript_list([t_fr])
        result = resolve_transcript(tl, "fr")
        assert result.language_code == "fr"

    def test_partial_match_fr_matches_fr_fr(self):
        t_fr_fr = _make_transcript("fr-FR", is_generated=False)
        tl = _make_transcript_list([t_fr_fr])
        result = resolve_transcript(tl, "fr")
        assert result.language_code == "fr-FR"

    def test_partial_match_zh_cn_matches_zh(self):
        t_zh = _make_transcript("zh-CN", is_generated=False)
        tl = _make_transcript_list([t_zh])
        result = resolve_transcript(tl, "zh-CN")
        assert result.language_code == "zh-CN"

    def test_manual_preferred_over_generated(self):
        t_manual    = _make_transcript("fr-FR", is_generated=False)
        t_generated = _make_transcript("fr-CA", is_generated=True)
        tl = _make_transcript_list([t_generated, t_manual])
        result = resolve_transcript(tl, "fr")
        assert result.is_generated is False
        assert result.language_code == "fr-FR"

    def test_generated_fallback_when_no_manual(self):
        t_generated = _make_transcript("fr-FR", is_generated=True)
        tl = _make_transcript_list([t_generated])
        result = resolve_transcript(tl, "fr")
        assert result.is_generated is True

    def test_raises_when_no_match(self):
        from youtube_transcript_api._errors import NoTranscriptFound
        t_en = _make_transcript("en", is_generated=False)
        tl = _make_transcript_list([t_en])
        with pytest.raises(NoTranscriptFound):
            resolve_transcript(tl, "fr")

    def test_case_insensitive_partial_match(self):
        t_fr = _make_transcript("fr-FR", is_generated=False)
        tl = _make_transcript_list([t_fr])
        result = resolve_transcript(tl, "FR")
        assert result.language_code == "fr-FR"

    def test_multiple_partial_matches_manual_wins(self):
        t_fr_fr = _make_transcript("fr-FR", is_generated=True)
        t_fr_ca = _make_transcript("fr-CA", is_generated=False)
        tl = _make_transcript_list([t_fr_fr, t_fr_ca])
        result = resolve_transcript(tl, "fr")
        assert result.language_code == "fr-CA"
        assert result.is_generated is False


# ── list_supported_languages ──────────────────────────────────────────────────

class TestListSupportedLanguages:

    def test_returns_list_of_tuples(self):
        result = list_supported_languages()
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)

    def test_contains_english(self):
        result = list_supported_languages()
        codes = [code for _, code in result]
        assert "en" in codes

    def test_contains_french(self):
        result = list_supported_languages()
        codes = [code for _, code in result]
        assert "fr" in codes

    def test_no_duplicate_codes(self):
        result = list_supported_languages()
        codes = [code for _, code in result]
        assert len(codes) == len(set(codes))

    def test_sorted_alphabetically(self):
        result = list_supported_languages()
        names = [name for name, _ in result]
        assert names == sorted(names)

    def test_minimum_40_languages(self):
        result = list_supported_languages()
        assert len(result) >= 40


# ── get_spacy_model ──────────────────────────────────────────────────────────

class TestGetSpacyModel:

    def test_english_maps_correctly(self):
        assert get_spacy_model("en") == "en_core_web_sm"

    def test_french_maps_correctly(self):
        # "md", not "sm" -- see issue #13. The small model inconsistently
        # misclassifies common conjugated verbs (POS wrong depending on
        # context), which stops the lemmatizer from normalizing them to
        # their infinitive at all.
        assert get_spacy_model("fr") == "fr_core_news_md"

    def test_regional_variant_falls_back_to_base(self):
        assert get_spacy_model("fr-CA") == "fr_core_news_md"
        assert get_spacy_model("fr-FR") == "fr_core_news_md"

    def test_chinese_variants_share_one_model(self):
        # zh-CN and zh-TW both resolve to the same base "zh" model --
        # spaCy doesn't ship separate simplified/traditional pipelines.
        assert get_spacy_model("zh-CN") == "zh_core_web_sm"
        assert get_spacy_model("zh-TW") == "zh_core_web_sm"

    def test_case_insensitive(self):
        assert get_spacy_model("FR") == "fr_core_news_md"

    def test_unsupported_language_raises(self):
        with pytest.raises(SpacyModelUnavailableError):
            get_spacy_model("ar")  # Arabic: in LANGUAGE_MAP, no spaCy model

    def test_error_message_names_the_language_and_lists_supported(self):
        with pytest.raises(SpacyModelUnavailableError) as exc_info:
            get_spacy_model("th")
        message = str(exc_info.value)
        assert "th" in message
        assert "fr" in message  # supported codes are listed

    def test_error_message_says_not_supported_thus_far(self):
        with pytest.raises(SpacyModelUnavailableError) as exc_info:
            get_spacy_model("sw")
        assert "isn't supported thus far" in str(exc_info.value)

    def test_model_cache_is_per_language(self):
        # get_spacy_model() itself is stateless (nlp.py owns the actual
        # model cache) -- this just confirms two different languages
        # resolve to two different model names, not a shared default.
        assert get_spacy_model("fr") != get_spacy_model("de")

    def test_norwegian_macrolanguage_code_resolves_via_alias(self):
        # LANGUAGE_MAP resolves "norwegian" to "no", but spaCy's model is
        # named "nb" (Bokmål-specific) -- there is no "no" model. Without
        # the alias this would incorrectly raise as unsupported.
        assert get_spacy_model("no") == "nb_core_news_sm"

    # -- SPACY_MODEL_SIZE_OVERRIDE (issue #13) ------------------------------
    # A general, language-agnostic escape hatch: anyone hitting a similar
    # lemmatization problem in a language other than French can try a
    # bigger model without a code change, rather than us guessing which of
    # the other 23 languages would actually benefit.

    @pytest.mark.parametrize("language,base_model", [
        ("de", "de_core_news"), ("es", "es_core_news"), ("ja", "ja_core_news"),
    ])
    def test_override_applies_to_any_language(self, monkeypatch, language, base_model):
        monkeypatch.setattr(language_module, "SPACY_MODEL_SIZE_OVERRIDE", "md")
        assert get_spacy_model(language) == f"{base_model}_md"

    def test_override_replaces_a_non_default_size_too(self, monkeypatch):
        # French already defaults to "md" -- an override must still apply
        # cleanly on top of that, not produce "fr_core_news_md_lg" or similar.
        monkeypatch.setattr(language_module, "SPACY_MODEL_SIZE_OVERRIDE", "lg")
        assert get_spacy_model("fr") == "fr_core_news_lg"

    def test_no_override_by_default(self):
        assert language_module.SPACY_MODEL_SIZE_OVERRIDE is None
        assert get_spacy_model("de") == "de_core_news_sm"

    def test_every_language_map_code_is_either_supported_or_raises_cleanly(self):
        # Every code LANGUAGE_MAP can resolve to must either have a spaCy
        # model or raise SpacyModelUnavailableError -- never a KeyError or
        # anything else uncaught.
        for code in set(LANGUAGE_MAP.values()):
            try:
                model = get_spacy_model(code)
                # "fr" is a deliberate exception (issue #13); every other
                # language stays on the small tier until individually
                # verified the same way.
                assert model.endswith(("_sm", "_md")) if code == "fr" \
                    else model.endswith("_sm")
            except SpacyModelUnavailableError:
                pass


class TestLocalisePos:
    """
    The part of speech is a label on the card, so it is written in the
    language the card is written in.

    wiktextract normalises the tag to English whichever edition the index came
    from, so every German card read "noun" and every French one read "adj".
    """

    def test_a_german_card_says_substantiv(self):
        assert language_module.localise_pos("noun", "de") == "Substantiv"

    def test_the_same_word_in_french_says_nom(self):
        # The pair. A table that only ever returned the German label would
        # pass the test above.
        assert language_module.localise_pos("noun", "fr") == "nom"

    def test_english_expands_the_abbreviated_tags(self):
        # "adj" and "intj" are not words. English gets no translation out of
        # this, but it does stop showing tags.
        assert language_module.localise_pos("adj", "en") == "adjective"
        assert language_module.localise_pos("intj", "en") == "interjection"

    def test_the_tag_is_matched_case_insensitively(self):
        assert language_module.localise_pos("Noun", "de") == "Substantiv"

    def test_a_tag_we_do_not_know_is_left_alone(self):
        # The French index really does contain "typographic variant". Losing
        # it would be worse than showing it: the field would silently empty.
        assert language_module.localise_pos("typographic variant", "de") == \
               "typographic variant"

    def test_an_empty_tag_stays_empty(self):
        # Fallback cards have no part of speech and must not gain one.
        assert language_module.localise_pos("", "de") == ""

    def test_a_language_with_no_table_falls_back_to_english(self):
        assert language_module.localise_pos("adj", "ja") == "adjective"

    def test_the_russian_indexs_own_misspelling_is_understood(self):
        # The Russian build stores 71 entries as "onomatopeia".
        assert language_module.localise_pos("onomatopeia", "ru") == "звукоподражание"

    def test_every_table_covers_the_same_tags(self):
        # A language added with half the tags filled in would show German for
        # some words and English for others on the same deck.
        english = set(language_module.POS_LABELS["en"])
        for code, table in language_module.POS_LABELS.items():
            assert set(table) == english, f"{code} does not cover the same tags"

    def test_no_table_is_a_copy_of_the_english_one(self):
        # Copying the English row as a starting point and forgetting to
        # translate it would leave the cards exactly as broken as before,
        # while looking supported. The four commonest tags account for most
        # of every real deck, so requiring those to differ is enough to
        # catch it without asserting on words like "symbol" that legitimately
        # match.
        english = language_module.POS_LABELS["en"]
        for code, table in language_module.POS_LABELS.items():
            if code == "en":
                continue
            for tag in ("noun", "adj", "adv", "pron"):
                assert table[tag] != english[tag], \
                    f"{code}[{tag}] is still the English label {table[tag]!r}"


# ── Filler sounds ─────────────────────────────────────────────────────────────

class TestIsFiller:
    """
    The stoplist behind CLAUDE.md's v0.6.0 card-quality goal: filler sounds
    reach cards because spaCy tags them NOUN or ADV in some sentences, so a
    POS rule cannot catch them and would cost real interjections if it did.
    """

    def test_a_measured_french_filler_is_recognised(self):
        assert language_module.is_filler("euh", "fr")

    def test_a_french_interjection_worth_learning_is_not_filtered(self):
        # The reason this is a stoplist and not a rule on INTJ: bonsoir
        # carries the same tag as euh and belongs on a card.
        assert not language_module.is_filler("bonsoir", "fr")

    def test_a_word_with_meaning_is_not_filtered_however_filler_ish(self):
        # "bon" is a discourse marker in speech and an adjective in the
        # dictionary. Filtering it would lose a word a learner wants.
        assert not language_module.is_filler("bon", "fr")

    def test_one_languages_sounds_do_not_filter_another_language(self):
        # euh is French. Nobody has measured whether it appears in English
        # transcripts, so English must not act on it.
        assert not language_module.is_filler("euh", "en")

    def test_an_elongated_sound_is_recognised(self):
        # A transcript spells a drawn-out hesitation with as many letters
        # as it likes; the table lists one spelling.
        assert language_module.is_filler("euuuh", "fr")
        assert language_module.is_filler("ahhhh", "de")

    def test_a_doubled_letter_is_not_collapsed(self):
        # "err" is a real English verb and would become "er", a hesitation,
        # if runs of two were collapsed as well as runs of three.
        assert not language_module.is_filler("err", "en")

    def test_case_and_surrounding_space_do_not_matter(self):
        assert language_module.is_filler("  EUH ", "fr")

    def test_a_regional_code_uses_its_base_languages_table(self):
        assert language_module.is_filler("euh", "fr-FR")

    def test_a_language_with_no_table_filters_nothing(self):
        # 20 of the 24 spaCy languages have no list. They must keep every
        # word rather than borrow another language's.
        assert not language_module.is_filler("ah", "pl")

    def test_an_empty_lemma_or_language_is_not_filler(self):
        assert not language_module.is_filler("", "fr")
        assert not language_module.is_filler("euh", "")

    def test_no_listed_sound_is_unreachable(self):
        # is_filler collapses runs of three or more before looking up, so an
        # entry containing one could never be matched by anything.
        for code, sounds in language_module.FILLER_SOUNDS.items():
            for sound in sounds:
                assert language_module.is_filler(sound, code), \
                    f"{code} lists {sound!r}, which its own lookup cannot match"


# ── Integration ───────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestIntegration:

    def test_real_french_video_transcript(self):
        """Requires network. Fetches a real French transcript."""
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        tl = api.list("2QkRcDSClS0")
        result = resolve_transcript(tl, "fr")
        assert result.language_code.startswith("fr")