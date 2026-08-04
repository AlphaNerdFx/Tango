"""
language.py
-----------
Language resolution for the Tango pipeline.

Responsible for:
  1. Mapping human-readable language names to BCP-47 codes
  2. Resolving the target language from --language flag or deck name
  3. Selecting the best available transcript for a given language

Priority:
  --language flag (explicit) > deck name inference > error

Manual transcripts are preferred over auto-generated ones.
Partial BCP-47 matching is handled here since youtube-transcript-api
uses exact key lookup internally.

Coverage: 40 languages with names in English, French, Spanish, German,
and the language's own endonym where it differs significantly.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from youtube_transcript_api._transcripts import Transcript, TranscriptList
from youtube_transcript_api._errors import NoTranscriptFound

logger = logging.getLogger(__name__)

# =============================================================================
# Language name -> BCP-47 code mapping
# Keys are lowercase name variants. Values are base BCP-47 codes.
# Partial matching against YouTube's full codes (fr-FR, zh-CN) is
# handled separately in resolve_transcript().
# =============================================================================

LANGUAGE_MAP: dict[str, str] = {

    # Western Europe
    "french": "fr", "français": "fr", "francais": "fr",
    "frances": "fr", "französisch": "fr", "french language": "fr",

    "spanish": "es", "español": "es", "espanol": "es",
    "castellano": "es", "spanisch": "es", "espagnol": "es",

    "portuguese": "pt", "português": "pt", "portugues": "pt",
    "portugiesisch": "pt", "portugais": "pt",

    "german": "de", "deutsch": "de", "allemand": "de",
    "aleman": "de", "tedesco": "de",

    "italian": "it", "italiano": "it", "italienisch": "it",
    "italien": "it",

    "dutch": "nl", "nederlands": "nl", "hollandais": "nl",
    "niederländisch": "nl", "néerlandais": "nl", "neerlandais": "nl",

    "swedish": "sv", "svenska": "sv", "suédois": "sv",
    "suedois": "sv", "schwedisch": "sv",

    "norwegian": "no", "norsk": "no", "norvégien": "no",
    "norvegien": "no", "norwegisch": "no",

    "danish": "da", "dansk": "da", "danois": "da",
    "dänisch": "da", "danisch": "da",

    "finnish": "fi", "suomi": "fi", "finnois": "fi",
    "finnisch": "fi",

    "greek": "el", "ελληνικά": "el", "ellinika": "el",
    "grec": "el", "griechisch": "el",

    # Eastern Europe
    "russian": "ru", "русский": "ru", "russkiy": "ru",
    "russe": "ru", "russisch": "ru",

    "polish": "pl", "polski": "pl", "polonais": "pl",
    "polnisch": "pl",

    "czech": "cs", "čeština": "cs", "cestina": "cs",
    "tchèque": "cs", "tcheque": "cs", "tschechisch": "cs",

    "slovak": "sk", "slovenčina": "sk", "slovencina": "sk",
    "slovaque": "sk", "slowakisch": "sk",

    "romanian": "ro", "română": "ro", "romana": "ro",
    "roumain": "ro", "rumänisch": "ro", "rumanisch": "ro",

    "hungarian": "hu", "magyar": "hu", "hongrois": "hu",
    "ungarisch": "hu",

    "bulgarian": "bg", "български": "bg", "bulgarski": "bg",
    "bulgare": "bg", "bulgarisch": "bg",

    "serbian": "sr", "српски": "sr", "srpski": "sr",
    "serbe": "sr", "serbisch": "sr",

    "croatian": "hr", "hrvatski": "hr", "croate": "hr",
    "kroatisch": "hr",

    "ukrainian": "uk", "українська": "uk", "ukrainska": "uk",
    "ukrainien": "uk", "ukrainisch": "uk",

    # Middle East / North Africa
    "arabic": "ar", "العربية": "ar", "alarabiyya": "ar",
    "arabe": "ar", "arabisch": "ar",

    "hebrew": "he", "עברית": "he", "ivrit": "he",
    "hébreu": "he", "hebreu": "he", "hebräisch": "he",

    "turkish": "tr", "türkçe": "tr", "turkce": "tr",
    "turc": "tr", "türkisch": "tr", "turkisch": "tr",

    "persian": "fa", "farsi": "fa", "فارسی": "fa",
    "persan": "fa", "persisch": "fa", "iranian": "fa",

    # East Asia
    "japanese": "ja", "日本語": "ja", "nihongo": "ja",
    "japonais": "ja", "japanisch": "ja",

    "chinese": "zh-CN", "中文": "zh-CN", "mandarin": "zh-CN",
    "putonghua": "zh-CN", "chinois": "zh-CN", "chinesisch": "zh-CN",
    "simplified chinese": "zh-CN", "chinese simplified": "zh-CN",

    "traditional chinese": "zh-TW", "chinese traditional": "zh-TW",
    "繁體中文": "zh-TW", "cantonese": "zh-TW",

    "korean": "ko", "한국어": "ko", "hangugeo": "ko",
    "coréen": "ko", "corean": "ko", "koreanisch": "ko",

    # South / Southeast Asia
    "hindi": "hi", "हिन्दी": "hi", "hindī": "hi",
    "hindi language": "hi",

    "bengali": "bn", "বাংলা": "bn", "bangla": "bn",
    "bengalais": "bn",

    "thai": "th", "ภาษาไทย": "th", "phasa thai": "th",
    "thaï": "th", "thai language": "th",

    "vietnamese": "vi", "tiếng việt": "vi", "tieng viet": "vi",
    "vietnamien": "vi", "vietnamesisch": "vi",

    "indonesian": "id", "bahasa indonesia": "id", "bahasa": "id",
    "indonésien": "id", "indonesisch": "id",

    "malay": "ms", "bahasa melayu": "ms", "bahasa malaysia": "ms",
    "malais": "ms", "malaysisch": "ms",

    "tagalog": "tl", "filipino": "tl", "wikang filipino": "tl",
    "tagal": "tl",

    # Other major languages
    "swahili": "sw", "kiswahili": "sw", "souahéli": "sw",
    "suaheli": "sw",

    "afrikaans": "af", "afrikaner": "af",

    "welsh": "cy", "cymraeg": "cy", "gallois": "cy",
    "walisisch": "cy",

    "catalan": "ca", "català": "ca", "catala": "ca",
    "catalán": "ca", "katalanisch": "ca",

    "latin": "la", "latina": "la", "latein": "la",
    "latin language": "la",

    # English (needed for completeness — user may have English deck)
    "english": "en", "anglais": "en", "englisch": "en",
    "inglés": "en", "ingles": "en",
}


# =============================================================================
# Language resolution
# =============================================================================

class LanguageResolutionError(Exception):
    """
    Raised when the target language cannot be determined.
    Contains a user-friendly message explaining how to fix the issue.
    """


class SpacyModelUnavailableError(Exception):
    """
    Raised when spaCy has no trained pipeline for the resolved language.

    LANGUAGE_MAP supports 40 languages for transcript fetching, but spaCy
    only ships trained pipelines for a subset of those -- this is a
    separate, narrower coverage question. Previously nlp.py silently used
    the English model for every language regardless (see ARCHITECTURE.md
    9.1); this error replaces that silent wrong behaviour with an explicit
    failure.
    """


def resolve_language_code(
    language_flag: Optional[str],
    deck_name: Optional[str],
) -> str:
    """
    Resolve the BCP-47 language code for this pipeline run.

    Priority:
        1. --language flag (explicit, always wins)
        2. Deck name inference (convenience fallback)
        3. LanguageResolutionError

    Args:
        language_flag: Raw value from --language CLI flag, or None.
        deck_name:     The Anki deck name selected for this session.

    Returns:
        BCP-47 language code string (e.g. "fr", "zh-CN").

    Raises:
        LanguageResolutionError: Cannot determine language from either source.
    """
    # ── Explicit flag wins ────────────────────────────────────────────────────
    if language_flag:
        code = language_flag.strip().lower()
        logger.info("Language set from --language flag: %s", code)
        return code

    # ── Deck name inference ───────────────────────────────────────────────────
    if deck_name:
        code = _infer_from_deck_name(deck_name)
        if code:
            logger.info(
                "Language '%s' inferred from deck name '%s'.", code, deck_name
            )
            return code

    # ── Neither source resolved ───────────────────────────────────────────────
    deck_hint = f" '{deck_name}'" if deck_name else ""
    raise LanguageResolutionError(
        f"Could not detect a language from deck name{deck_hint}.\n"
        f"  Either rename your deck to a language name (e.g. 'French', 'Deutsch'),\n"
        f"  or pass the language code explicitly:\n"
        f"    make run VIDEO_ID=<id> DECK=\"{deck_name or 'MyDeck'}\" LANGUAGE=fr\n"
        f"  Supported codes: fr, es, de, ja, zh-CN, ar, and more (see docs/languages.txt)"
    )


def _infer_from_deck_name(deck_name: str) -> Optional[str]:
    """
    Attempt to infer a BCP-47 code from an Anki deck name.

    Strips sub-deck notation (Language::French::B2 -> "Language French B2"),
    tokenises, and checks each word against LANGUAGE_MAP. Returns the first
    match found, or None if no match.

    Case-insensitive. Handles accented characters.

    Args:
        deck_name: Full Anki deck name, may include '::' sub-deck notation.

    Returns:
        BCP-47 code string, or None if no match found.
    """
    # Flatten sub-deck separators and normalise
    flat = deck_name.replace("::", " ").replace("_", " ").replace("-", " ")
    flat = flat.strip().lower()

    # Direct full-name lookup first (handles "French", "Français" etc.)
    if flat in LANGUAGE_MAP:
        return LANGUAGE_MAP[flat]

    # Word-by-word lookup (handles "Netflix French", "B2 Deutsch", "vocab fr")
    words = re.split(r"\s+", flat)
    for word in words:
        word = word.strip()
        if word in LANGUAGE_MAP:
            return LANGUAGE_MAP[word]

    # Multi-word phrase lookup (handles "Traditional Chinese", "Simplified Chinese")
    for phrase, code in LANGUAGE_MAP.items():
        if " " in phrase and phrase in flat:
            return code

    return None


# =============================================================================
# Transcript selection with partial BCP-47 matching
# =============================================================================

def resolve_transcript(
    transcript_list: TranscriptList,
    language_code: str,
) -> Transcript:
    """
    Select the best available transcript for the given language code.

    youtube-transcript-api's find_transcript() uses exact key lookup,
    so 'fr' will not match 'fr-FR'. This function adds partial matching:
    if the exact code is not available, it looks for any transcript whose
    language code starts with the base code.

    Manual transcripts are preferred over auto-generated ones by the
    library itself — find_transcript() checks manually_created_transcripts
    first and only falls back to generated ones if no manual exists.

    Args:
        transcript_list: TranscriptList from YouTubeTranscriptApi.list().
        language_code:   BCP-47 code to match (e.g. "fr", "zh-CN").

    Returns:
        The best matching Transcript object.

    Raises:
        NoTranscriptFound: No transcript available in the requested language.
        LanguageResolutionError: Ambiguous partial match with no clear winner.
    """
    # ── Exact match first (fastest path) ─────────────────────────────────────
    try:
        return transcript_list.find_transcript([language_code])
    except NoTranscriptFound:
        pass

    # ── Partial match: find all codes starting with the base code ─────────────
    # e.g. "fr" matches "fr-FR", "fr-CA", "fr-BE"
    available = list(transcript_list)
    partial_matches = [
        t for t in available
        if t.language_code.lower().startswith(language_code.lower())
    ]

    if not partial_matches:
        # Build a helpful list of what IS available
        available_codes = [t.language_code for t in available]
        raise NoTranscriptFound(
            transcript_list.video_id,
            [language_code],
            available_codes,
        )

    # ── Among partial matches, prefer manual over auto-generated ──────────────
    manual = [t for t in partial_matches if not t.is_generated]
    if manual:
        chosen = manual[0]
        logger.info(
            "Partial match: '%s' resolved to '%s' (manual).",
            language_code, chosen.language_code,
        )
        return chosen

    # Fall back to auto-generated if no manual exists
    chosen = partial_matches[0]
    logger.warning(
        "Partial match: '%s' resolved to '%s' (auto-generated). "
        "No manual transcript available in this language.",
        language_code, chosen.language_code,
    )
    return chosen


# =============================================================================
# Utility
# =============================================================================

def list_supported_languages() -> list[tuple[str, str]]:
    """
    Return a deduplicated list of (canonical English name, BCP-47 code) pairs
    for all supported languages, sorted alphabetically by name.

    Used by the --list-languages CLI flag.
    """
    seen_codes: set[str] = set()
    result: list[tuple[str, str]] = []

    # Canonical English names are the first key added per code
    canonical: dict[str, str] = {}
    for name, code in LANGUAGE_MAP.items():
        if code not in canonical and name.isascii():
            canonical[code] = name

    for code, name in sorted(canonical.items(), key=lambda x: x[1]):
        result.append((name.capitalize(), code))

    return result


# =============================================================================
# BCP-47 code -> spaCy model name
# =============================================================================
# spaCy only ships trained pipelines for these 24 languages. LANGUAGE_MAP
# above supports 40 languages for transcript fetching -- that is a
# different, wider coverage question. A language resolving successfully
# via LANGUAGE_MAP does NOT guarantee a spaCy model exists for it.
#
# Previously nlp.py used a single hardcoded English model for every
# language regardless, silently mangling non-English text with English
# morphology rules (see ARCHITECTURE.md 9.1). get_spacy_model() replaces
# that with an explicit lookup that fails loudly for languages spaCy
# doesn't support, instead of guessing wrong.

SPACY_MODELS: dict[str, str] = {
    "en": "en_core_web_sm",
    "fr": "fr_core_news_sm",
    "es": "es_core_news_sm",
    "de": "de_core_news_sm",
    "it": "it_core_news_sm",
    "pt": "pt_core_news_sm",
    "nl": "nl_core_news_sm",
    "ru": "ru_core_news_sm",
    "pl": "pl_core_news_sm",
    "ro": "ro_core_news_sm",
    "el": "el_core_news_sm",
    "da": "da_core_news_sm",
    "sv": "sv_core_news_sm",
    "nb": "nb_core_news_sm",
    "fi": "fi_core_news_sm",
    "lt": "lt_core_news_sm",
    "hr": "hr_core_news_sm",
    "uk": "uk_core_news_sm",
    "sl": "sl_core_news_sm",
    "mk": "mk_core_news_sm",
    "ca": "ca_core_news_sm",
    "ja": "ja_core_news_sm",
    "zh": "zh_core_web_sm",
    "ko": "ko_core_news_sm",
}

# Codes where LANGUAGE_MAP's resolved code doesn't match spaCy's model-
# naming code. Checked before both the exact-match and base-code lookups
# in get_spacy_model().
#
# "no" -> "nb": LANGUAGE_MAP resolves "norwegian" to the macrolanguage
# code "no", but spaCy only ships a Bokmål-specific pipeline under "nb".
# There is no "no" model -- without this alias, Norwegian would incorrectly
# raise SpacyModelUnavailableError even though a usable model exists.
_SPACY_CODE_ALIASES: dict[str, str] = {
    "no": "nb",
}


def get_spacy_model(language_code: str) -> str:
    """
    Resolve the spaCy model name for a BCP-47 language code.

    Tries an exact match first (after alias normalisation), then falls
    back to the base code -- the part before a '-' -- so regional variants
    like "fr-CA" or "zh-CN" resolve to the same model as "fr" / "zh".

    Args:
        language_code: BCP-47 code, e.g. "fr", "fr-CA", "zh-CN".

    Returns:
        spaCy model name, e.g. "fr_core_news_sm".

    Raises:
        SpacyModelUnavailableError: spaCy has no trained pipeline for this
            language or its base code, even after alias normalisation.
    """
    code = language_code.strip().lower()
    code = _SPACY_CODE_ALIASES.get(code, code)
    if code in SPACY_MODELS:
        return SPACY_MODELS[code]

    base = code.split("-")[0]
    base = _SPACY_CODE_ALIASES.get(base, base)
    if base in SPACY_MODELS:
        return SPACY_MODELS[base]

    supported = ", ".join(sorted(SPACY_MODELS))
    raise SpacyModelUnavailableError(
        f"'{language_code}' isn't supported thus far.\n"
        f"  spaCy has no trained pipeline for this language, so accurate\n"
        f"  vocabulary extraction can't run for it yet.\n"
        f"  Currently supported: {supported}"
    )