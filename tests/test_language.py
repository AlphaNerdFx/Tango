"""
Tests for language resolution, deck name inference, BCP-47 partial matching,
and transcript selection preference (manual over auto-generated).

Run: pytest tests/test_language.py -m "not integration"
"""

from unittest.mock import MagicMock, patch

import pytest

from pipeline.language import (
    LANGUAGE_MAP,
    LanguageResolutionError,
    _infer_from_deck_name,
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