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

from pipeline import TangoError

import logging
import os
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

    # English (needed for completeness, user may have English deck)
    "english": "en", "anglais": "en", "englisch": "en",
    "inglés": "en", "ingles": "en",
}


# =============================================================================
# Language resolution
# =============================================================================

class LanguageResolutionError(TangoError):
    """
    Raised when the target language cannot be determined.
    Contains a user-friendly message explaining how to fix the issue.
    """


class SpacyModelUnavailableError(TangoError):
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
    library itself, find_transcript() checks manually_created_transcripts
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

    Used by the `tango languages` command.
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
#
# Every entry defaults to the smallest ("sm") model tier except "fr",
# which is pinned to "md" -- see issue #13. The small French model's POS
# tagger inconsistently misclassifies common conjugated verb forms
# ("sors" tagged NOUN or ADV depending on sentence context instead of
# VERB), and when the POS is wrong the lemmatizer never attempts verb
# normalization, so the same verb produces multiple different lemmas
# ("sortir" vs "sors") and duplicate cards. Verified directly: "md" fixed
# every one of 3 real reproduction sentences that "sm" got wrong,
# consistently, not just for one lucky sentence.
#
# This is not assumed to generalize to the other 23 languages. A parallel
# test against Spanish's analogous "juego" (play, verb vs noun homograph)
# showed "md" fix one misclassification but introduce a different one
# (NOUN in "sm" became PROPN in "md", still wrong) -- the size/accuracy
# tradeoff is a per-language-model training-data question, not something
# a single global rule can answer for all 24 languages at once.
# SPACY_MODEL_SIZE_OVERRIDE below exists so anyone hitting a similar
# problem in another language can test a larger model for themselves
# without a code change, rather than us guessing which of the other 23
# would actually benefit.
SPACY_MODELS: dict[str, str] = {
    "en": "en_core_web_sm",
    "fr": "fr_core_news_md",
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

# Optional global override for every language's model size tier, e.g. "md"
# or "lg". Unset by default, which means each language uses exactly what
# SPACY_MODELS specifies. Applies uniformly to whichever language is in use
# rather than requiring a code change per language -- see the comment above
# SPACY_MODELS for why we are not picking a size per language ourselves
# beyond the one (French) we have directly verified.
SPACY_MODEL_SIZE_OVERRIDE: str | None = os.getenv("SPACY_MODEL_SIZE_OVERRIDE") or None


def get_spacy_model(language_code: str) -> str:
    """
    Resolve the spaCy model name for a BCP-47 language code.

    Tries an exact match first (after alias normalisation), then falls
    back to the base code -- the part before a '-' -- so regional variants
    like "fr-CA" or "zh-CN" resolve to the same model as "fr" / "zh".

    If SPACY_MODEL_SIZE_OVERRIDE is set, the resolved model's size suffix
    is replaced with it regardless of language, e.g. "md" turns
    "es_core_news_sm" into "es_core_news_md".

    Args:
        language_code: BCP-47 code, e.g. "fr", "fr-CA", "zh-CN".

    Returns:
        spaCy model name, e.g. "fr_core_news_md".

    Raises:
        SpacyModelUnavailableError: spaCy has no trained pipeline for this
            language or its base code, even after alias normalisation.
    """
    code = language_code.strip().lower()
    code = _SPACY_CODE_ALIASES.get(code, code)
    if code in SPACY_MODELS:
        return _apply_size_override(SPACY_MODELS[code])

    base = code.split("-")[0]
    base = _SPACY_CODE_ALIASES.get(base, base)
    if base in SPACY_MODELS:
        return _apply_size_override(SPACY_MODELS[base])

    supported = ", ".join(sorted(SPACY_MODELS))
    raise SpacyModelUnavailableError(
        f"'{language_code}' isn't supported thus far.\n"
        f"  spaCy has no trained pipeline for this language, so accurate\n"
        f"  vocabulary extraction can't run for it yet.\n"
        f"  Currently supported: {supported}"
    )


def _apply_size_override(model_name: str) -> str:
    """Swap model_name's size suffix for SPACY_MODEL_SIZE_OVERRIDE, if set."""
    if not SPACY_MODEL_SIZE_OVERRIDE:
        return model_name
    prefix, _, _size = model_name.rpartition("_")
    return f"{prefix}_{SPACY_MODEL_SIZE_OVERRIDE}"


# Part-of-speech labels
#
# wiktextract normalises the part of speech to an English tag no matter which
# Wiktionary edition an index was built from, so a German card read "noun" and
# a French one read "adj". The tag is fine as a key and wrong on a card: it is
# neither the learner's language nor, for the abbreviated ones, a word.
#
# Keys are the tags the indexes actually contain, counted across the German,
# French and Russian builds. Anything not listed passes through unchanged, so
# a tag we have not seen shows up as itself rather than disappearing.
POS_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "noun": "noun", "verb": "verb", "adj": "adjective", "adv": "adverb",
        "pron": "pronoun", "prep": "preposition", "conj": "conjunction",
        "intj": "interjection", "det": "determiner", "article": "article",
        "num": "numeral", "name": "proper noun", "phrase": "phrase",
        "particle": "particle", "prefix": "prefix", "suffix": "suffix",
        "abbrev": "abbreviation", "symbol": "symbol", "character": "character",
        "onomatopoeia": "onomatopoeia", "gerund": "gerund",
    },
    "de": {
        "noun": "Substantiv", "verb": "Verb", "adj": "Adjektiv", "adv": "Adverb",
        "pron": "Pronomen", "prep": "Präposition", "conj": "Konjunktion",
        "intj": "Interjektion", "det": "Artikelwort", "article": "Artikel",
        "num": "Zahlwort", "name": "Eigenname", "phrase": "Redewendung",
        "particle": "Partikel", "prefix": "Präfix", "suffix": "Suffix",
        "abbrev": "Abkürzung", "symbol": "Symbol", "character": "Schriftzeichen",
        "onomatopoeia": "Lautmalerei", "gerund": "Gerundium",
    },
    "fr": {
        "noun": "nom", "verb": "verbe", "adj": "adjectif", "adv": "adverbe",
        "pron": "pronom", "prep": "préposition", "conj": "conjonction",
        "intj": "interjection", "det": "déterminant", "article": "article",
        "num": "numéral", "name": "nom propre", "phrase": "locution",
        "particle": "particule", "prefix": "préfixe", "suffix": "suffixe",
        "abbrev": "abréviation", "symbol": "symbole", "character": "caractère",
        "onomatopoeia": "onomatopée", "gerund": "gérondif",
    },
    "ru": {
        "noun": "существительное", "verb": "глагол", "adj": "прилагательное",
        "adv": "наречие", "pron": "местоимение", "prep": "предлог",
        "conj": "союз", "intj": "междометие", "det": "определитель",
        "article": "артикль", "num": "числительное", "name": "имя собственное",
        "phrase": "фраза", "particle": "частица", "prefix": "приставка",
        "suffix": "суффикс", "abbrev": "аббревиатура", "symbol": "символ",
        "character": "знак", "onomatopoeia": "звукоподражание",
        "gerund": "деепричастие",
    },
    "es": {
        "noun": "sustantivo", "verb": "verbo", "adj": "adjetivo", "adv": "adverbio",
        "pron": "pronombre", "prep": "preposición", "conj": "conjunción",
        "intj": "interjección", "det": "determinante", "article": "artículo",
        "num": "numeral", "name": "nombre propio", "phrase": "locución",
        "particle": "partícula", "prefix": "prefijo", "suffix": "sufijo",
        "abbrev": "abreviatura", "symbol": "símbolo", "character": "carácter",
        "onomatopoeia": "onomatopeya", "gerund": "gerundio",
    },
    "it": {
        "noun": "sostantivo", "verb": "verbo", "adj": "aggettivo", "adv": "avverbio",
        "pron": "pronome", "prep": "preposizione", "conj": "congiunzione",
        "intj": "interiezione", "det": "determinante", "article": "articolo",
        "num": "numerale", "name": "nome proprio", "phrase": "locuzione",
        "particle": "particella", "prefix": "prefisso", "suffix": "suffisso",
        "abbrev": "abbreviazione", "symbol": "simbolo", "character": "carattere",
        "onomatopoeia": "onomatopea", "gerund": "gerundio",
    },
    "pt": {
        "noun": "substantivo", "verb": "verbo", "adj": "adjetivo", "adv": "advérbio",
        "pron": "pronome", "prep": "preposição", "conj": "conjunção",
        "intj": "interjeição", "det": "determinante", "article": "artigo",
        "num": "numeral", "name": "nome próprio", "phrase": "locução",
        "particle": "partícula", "prefix": "prefixo", "suffix": "sufixo",
        "abbrev": "abreviatura", "symbol": "símbolo", "character": "caractere",
        "onomatopoeia": "onomatopeia", "gerund": "gerúndio",
    },
}

# Spellings that reach us from the indexes and mean something already listed.
# "onomatopeia" is how the Russian build spells it, 71 entries of it.
_POS_ALIASES: dict[str, str] = {
    "onomatopeia": "onomatopoeia",
    "proper noun": "name",
    "adjective": "adj",
    "adverb": "adv",
}


def localise_pos(pos: str, language: str) -> str:
    """
    Translate a wiktextract part-of-speech tag into a language's own word.

    Args:
        pos:      The tag as stored, e.g. "noun" or "adj". Case-insensitive.
        language: BCP-47 code for the language the label should be written in.

    Returns:
        The label, or `pos` unchanged when the tag is not one we know. Empty
        input gives an empty string, which is what a fallback card needs.

    Languages without a table fall back to the English one, because its job
    there is to expand "adj" into "adjective" rather than to translate, and
    an English word beats an abbreviation on a card either way.
    """
    raw = (pos or "").strip()
    if not raw:
        return ""
    key = raw.lower()
    key = _POS_ALIASES.get(key, key)
    code = (language or "en").lower()
    table = POS_LABELS.get(code) or POS_LABELS.get(code.split("-")[0]) or POS_LABELS["en"]
    return table.get(key, raw)


# =============================================================================
# Filler sounds
# =============================================================================
#
# Hesitation noises and the other non-lexical sounds a transcript writes down
# as if they were words. spaCy usually tags them INTJ, which nlp.py already
# drops, but usually is not always: one real French run put Ah, Bah, Ouai,
# Euh and Tss on cards, 3.4% of the deck, because the tagger read them as
# nouns and adverbs in the sentences they appeared in.
#
# A stoplist rather than a POS rule, and that is the whole reason this table
# exists: "Bonsoir" is also INTJ and is worth learning, so dropping the tag
# would cost real vocabulary. Only sounds are listed. A word a learner would
# want a card for does not belong here however filler-ish it feels in speech
# -- French "bon", German "na", Russian "ну" all carry meaning and are all
# deliberately absent.
#
# The test for an entry is not "does a speaker say this without thinking" but
# "would a course teach it". The first version of these lists failed that on
# eleven entries, all of which are dictionary words a learner wants: French
# "hein", "bof", "ouais" and "ben", German "tja" and "ach", Russian "эй",
# "ага", "ой" and "эх", English "yikes". They were removed, and
# TestRealWordsAreNotFiltered pins them so they cannot come back by hand.
#
# One asymmetry is deliberate and looks like a mistake. "ouai" is listed and
# "ouais" is not. They are the same word, but "ouais" is the dictionary
# spelling of a word worth learning, while "Ouai" is one of the five that a
# real French run actually put on a card. The measurement stays; the word
# does not.
#
# Per language, with nothing shared between them. "ah" is noise in French and
# noise in German, but one language's list must never decide what another
# filters. A language with no entry filters nothing, which is the default for
# the 20 codes in SPACY_MODELS whose transcripts nobody has listened to.
#
# Measured for French only (ARCHITECTURE.md 8.37). The other three lists are
# the standard written spellings of the same kinds of sound, and want a real
# run before anyone quotes a number for them.
#
# Entries are stored the way is_filler() normalises a lemma, so no entry may
# contain a run of three or more identical characters -- "ahhh" would be
# unreachable, since the lemma reaching the lookup has already collapsed to
# "ah". A test pins that.
#
# This is what is written by hand. FILLER_SOUNDS below is derived from it and
# is what the lookup uses; see _with_collapsed_forms for the one case an
# author cannot be expected to remember.
_FILLER_SOUNDS_AUTHORED: dict[str, frozenset[str]] = {
    "en": frozenset({
        "ah", "aha", "ahem", "eh", "er", "erm", "ha", "haha", "heh",
        "hm", "hmm", "huh", "mhm", "mm", "oh", "ooh", "psst", "shh",
        "ugh", "uh", "uh-huh", "um", "umm",
    }),
    "fr": frozenset({
        "ah", "aha", "bah", "beh", "eh", "euh",
        "hm", "hmm", "hum", "mmh", "oh", "ouah", "ouai",
        "pf", "pff", "ts", "tss",
    }),
    "de": frozenset({
        "ah", "aha", "äh", "ähem", "ähm", "hä", "hm", "hmm",
        "mhm", "oh", "ooh", "öh", "pf", "pff", "puh", "uff",
    }),
    "ru": frozenset({
        "ай", "ах", "гм", "мм", "ох", "тсс", "угу", "уф",
        "фух", "ух", "хм", "эм", "ээ",
    }),
}

# A transcript writes a drawn-out sound with as many letters as it feels
# like: "euuuh", "ahhhh", "тссс". Runs of three or more are collapsed to
# one so the table does not have to list every length.
#
# Three, not two, and the difference is a real word: English "err" would
# collapse to "er" under a two-run rule and be filtered as a hesitation.
# No ordinary word triples a letter, so three is safe where two is not.
_ELONGATION = re.compile(r"(.)\1{2,}")

# Runs of two or more. Used only to derive an entry's fully collapsed form,
# never on a lemma being looked up, because collapsing runs of two there is
# exactly what would eat "err".
_ANY_RUN = re.compile(r"(.)\1+")


def _collapse_elongation(word: str) -> str:
    """Collapse every run of three or more identical characters to one."""
    return _ELONGATION.sub(r"\1", word)


def _with_collapsed_forms(sounds: frozenset[str]) -> frozenset[str]:
    """
    Add each sound's fully collapsed spelling to the set.

    Args:
        sounds: The spellings written by hand for one language.

    Returns:
        Those spellings plus, for any containing a doubled letter, the same
        sound with every run reduced to a single character.

    A lemma is looked up by collapsing runs of three or more to one, which
    means a listed sound is only reachable from its elongations if the
    one-letter-run spelling is listed too. "tss" and "ts" both appear in the
    French set and both are needed: "tsss" collapses to "ts", not to "tss".

    Russian was listed as "тсс", "мм" and "ээ" without their short forms, so
    "тссс", "ммм" and "эээ" collapsed to spellings the table did not hold and
    became cards. Nothing failed and no test caught it, because every list
    looked complete on its own terms.

    Deriving the short forms rather than asking an author to remember them is
    the point: the next language added cannot reintroduce this.
    """
    return frozenset(sounds | {_ANY_RUN.sub(r"\1", sound) for sound in sounds})


# What the lookup actually reads.
FILLER_SOUNDS: dict[str, frozenset[str]] = {
    code: _with_collapsed_forms(sounds) for code, sounds in _FILLER_SOUNDS_AUTHORED.items()
}


def is_filler(lemma: str, language: str) -> bool:
    """
    Return True if this lemma is a filler sound in this language.

    Args:
        lemma:    The lemma as nlp.py keys it. Case-insensitive.
        language: BCP-47 code of the transcript's language. A code with no
                  table filters nothing, and a base code ("fr") answers for
                  its regional variants ("fr-FR").

    Returns:
        True when the lemma, or the lemma with elongated letter runs
        collapsed, is one of that language's filler sounds. False for an
        empty lemma, an unknown language, and every real word.

    There is deliberately no fallback to another language's table: filtering
    a French transcript against the German list would drop words nobody
    measured.
    """
    word = (lemma or "").strip().lower()
    if not word:
        return False
    code = (language or "").strip().lower()
    sounds = FILLER_SOUNDS.get(code) or FILLER_SOUNDS.get(code.split("-")[0])
    if not sounds:
        return False
    return word in sounds or _collapse_elongation(word) in sounds