"""
nlp.py
------
Responsible for one thing: given a clean transcript string, return an
ordered dict of lemmas and their frequency counts.

Token ordering follows first appearance in the transcript — Python 3.7+
dict insertion order guarantees this.

Frequency is captured as the value: useful immediately for ranking cards
by relevance, and for vocabulary level modelling in Phase 3.

Dependencies:
    spacy
    A trained pipeline per language actually processed — see
    language.SPACY_MODELS for which languages are supported and
    `make spacy-model SPACY_LANG=<code>` to install one.

Each language's spaCy model is loaded lazily on first use and cached for
the lifetime of the process — one model per language, not a single global
model reused for every language regardless (that was the bug: see
ARCHITECTURE.md 9.1). Importing this module does not load any model.
"""

from __future__ import annotations

from typing import Optional

import logging
import re

import spacy
from spacy.language import Language

from pipeline.language import SpacyModelUnavailableError, get_spacy_model

logger = logging.getLogger(__name__)

# ── POS tags to keep ──────────────────────────────────────────────────────────
# NOUN  — contamination, water, company
# VERB  — develop, give, contaminate
# ADJ   — permanent, photographic
# ADV   — quickly, permanently
ACCEPTED_POS: frozenset[str] = frozenset({"NOUN", "VERB", "ADJ", "ADV"})

# ── Lazy per-language model cache ─────────────────────────────────────────────
# Keyed by spaCy model name rather than by BCP-47 code: several codes can
# share one model (zh-CN and zh-TW both resolve to zh_core_web_sm via
# get_spacy_model()'s base-code fallback), so keying by model name avoids
# loading the same model twice under two different language keys.
_nlp_models: dict[str, Language] = {}


# ── Custom exceptions ─────────────────────────────────────────────────────────

class NLPModelNotFoundError(Exception):
    """
    Raised when a resolved spaCy model exists in spaCy's catalog but isn't
    installed in this environment.
    Fix: python -m spacy download <model_name>
         or: make spacy-model SPACY_LANG=<code>
    """


class EmptyTranscriptError(Exception):
    """
    Raised when the transcript string is empty or whitespace only.
    Caller should verify get_snippets() produced a non-empty _full_text.
    """


# ── Model loader ──────────────────────────────────────────────────────────────

def _get_model(language: str = "en") -> Language:
    """
    Return the cached spaCy model for this language, loading it on first
    use for that language.

    Args:
        language: BCP-47 language code, e.g. "en", "fr", "zh-CN".

    Raises:
        SpacyModelUnavailableError: spaCy has no trained pipeline for this
            language at all (propagated from language.get_spacy_model()).
        NLPModelNotFoundError: The resolved model is a real spaCy model
            but isn't installed in this environment.
    """
    model_name = get_spacy_model(language)  # raises SpacyModelUnavailableError
    if model_name not in _nlp_models:
        logger.debug("Loading spaCy model: %s", model_name)
        try:
            _nlp_models[model_name] = spacy.load(model_name)
            logger.info("spaCy model loaded: %s", model_name)
        except OSError as exc:
            raise NLPModelNotFoundError(
                f"spaCy model '{model_name}' not found. "
                f"Run: python -m spacy download {model_name}"
            ) from exc
    return _nlp_models[model_name]


# ── Token filter ──────────────────────────────────────────────────────────────

# Unicode-aware "letter" runs joined only by internal hyphens or apostrophes.
# [^\W\d_] accepts é, ü, ñ, Cyrillic, CJK, etc. and rejects digits/underscores.
# ’ is the typographic apostrophe YouTube transcripts often use instead
# of ASCII '.
_VALID_LEMMA = re.compile(r"^[^\W\d_]+(?:[-'’][^\W\d_]+)*$", re.UNICODE)

# The two-character minimum below exists to reject Indo-European
# lemmatization debris -- a botched French lemma collapsing to a single
# letter like "e" (see ARCHITECTURE.md 8.4). It does not generalize to
# every language: Chinese, Japanese, and Korean all have real one-character
# words (人 "person", 大 "big", 水 "water"), confirmed live against
# zh_core_web_sm output, not assumed. A single character in one of these
# ranges is exempted from the length check rather than rejected outright.
_CJK_RANGES = (
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs (Chinese, Japanese Kanji, Korean Hanja)
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7A3),   # Hangul syllables
)


def _is_single_cjk_character(lemma: str) -> bool:
    if len(lemma) != 1:
        return False
    code_point = ord(lemma)
    return any(lo <= code_point <= hi for lo, hi in _CJK_RANGES)


def _is_valid_lemma(lemma: str) -> bool:
    """
    A lemma is valid if it is at least two characters, contains no
    digits or underscores, and consists of alphabetic runs joined
    only by internal hyphens or apostrophes. A single CJK character
    (see _CJK_RANGES) is exempt from the length minimum.

    Permits: semi-relevé, week-end, arc-en-ciel, aujourd'hui, 人, 水
    Rejects: e, -, ->, -là, qu', 3d, semi-
    """
    if len(lemma) < 2:
        return _is_single_cjk_character(lemma)
    return bool(_VALID_LEMMA.match(lemma))


def _effective_lemma(token) -> str:
    """
    Return the token's lemma, falling back to its surface text when the
    model doesn't populate one.

    Not every spaCy pipeline fills lemma_. Confirmed directly against
    zh_core_web_sm: it leaves lemma_ empty for every single token, since
    Chinese has no inflectional morphology for a lemmatizer to normalize
    away -- the surface form already is the canonical form. Without this
    fallback, _is_valid_lemma("") fails its length check for every token,
    and Chinese vocabulary extraction silently returns zero words. Not
    degraded output -- total, silent failure for the whole language, with
    no error raised anywhere to say so.
    """
    return token.lemma_ or token.text


# ── Verb-lemma fallback for model lookup gaps (issue #13) ────────────────────
#
# spaCy's lemma lookup tables are keyed on the surface form and are NOT
# POS-aware, so a verb form that is also a noun gets the noun's lemma even
# when the token is correctly tagged VERB with full morphology. French
# "joue" (plays / cheek) lemmatizes to "joue" rather than "jouer"; same for
# "porte" (carries / door), "monte", "donne", "cherche". Measured at 5 of 14
# common regular -er verbs in present tense, the highest-frequency forms in
# spoken transcripts.
#
# This is not fixable with a bigger model (fr_core_news_md fails identically),
# with spaCy's own rules (its French verb rules contain no "e" -> "er" entry,
# so rule_lemmatize returns the surface form too), or by swapping lemmatizer
# (simplemma scores identically, 9/14, failing the same five words).
#
# Keyed by language, listing (suffix, replacement) pairs tried only when
# spaCy returned the surface form unchanged for a VERB. A language absent
# here gets no fallback at all, which is the deliberate default: the same
# transformation produces nonsense in a language with different morphology,
# so an entry belongs here only once verified against that language's own
# model. Verified for French; every other language intentionally unlisted.
_VERB_LEMMA_FALLBACKS: dict[str, tuple[tuple[str, str], ...]] = {
    "fr": (("e", "er"),),
}

# Cap on surface forms recorded per lemma by process_transcript(). Only one
# matching transcript sentence is ever needed, so a handful is plenty, and
# this bounds memory on a long transcript in a heavily inflected language.
_MAX_SURFACE_FORMS = 8


def _known_lemmas(nlp) -> frozenset:
    """
    Collect every form the model's lemma lookup table knows about.

    Used to validate a rule-derived candidate before accepting it. Returns
    an empty set when the pipeline has no lookup table, which disables the
    fallback rather than letting it run unchecked.
    """
    try:
        lookup = nlp.get_pipe("lemmatizer").lookups.get_table("lemma_lookup")
    except Exception:
        return frozenset()
    known = set(lookup.keys())
    for value in lookup.values():
        if isinstance(value, str):
            known.add(value)
        else:
            known.update(value)
    return frozenset(known)


def _corrected_lemma(token, language: str, known: frozenset) -> str:
    """
    Return the token's lemma, repairing the POS-blind lookup gap above.

    Only fires when the model plainly gave up: the token is a VERB and its
    lemma came back identical to its surface form. Irregular verbs are
    untouched because the model already lemmatizes them correctly, so the
    condition is never met for them ("est" -> "être" stays "être").

    The candidate is accepted only if the model's own vocabulary contains
    it. That guard is what keeps the rule from inventing words: "être" is
    a VERB whose lemma equals its surface form, and the naive rule would
    produce "êtrer", which the lookup table has never heard of and so is
    rejected. Validating against the model rather than a hand-written
    exception list means this cannot drift out of sync with the model.
    """
    lemma = _effective_lemma(token)
    if token.pos_ != "VERB" or not known:
        return lemma
    surface = token.text.lower()
    if lemma.lower() != surface:
        return lemma
    for suffix, replacement in _VERB_LEMMA_FALLBACKS.get(language, ()):
        if surface.endswith(suffix):
            candidate = surface[: len(surface) - len(suffix)] + replacement
            if candidate in known:
                return candidate
    return lemma


def _is_valid_token(token) -> bool:
    """
    Return True if a token should be included in the vocabulary output.

    Keeps:   tokens whose lemma is a valid word (see _is_valid_lemma)
             with an accepted POS tag
    Removes: punctuation, numbers, symbols, single letters,
             proper nouns (names, places, brands, organisations).

    Proper nouns are excluded because they rarely have dictionary
    definitions and create noise cards. Single letters produce
    meaningless flashcards. Stop words are kept because beginners
    need basic vocabulary.

    The vocabulary dict is keyed by the effective lemma (see
    _effective_lemma), not token.text, so validity is decided on that,
    not the raw surface form. token.is_alpha describes the surface form
    and is deliberately NOT checked here: real compounds and contractions
    (semi-relevé, week-end, aujourd'hui) have non-alphabetic surface
    forms, so gating on token.is_alpha would reject them before
    _is_valid_lemma ever ran. _is_valid_lemma already rejects punctuation,
    digits, and boundary-hyphen/apostrophe fragments on its own -- it is
    not a narrower check that needs token.is_alpha as a backstop.
    """
    if not _is_valid_lemma(_effective_lemma(token)):
        return False
    if token.pos_ not in ACCEPTED_POS:
        return False
    if token.pos_ == "PROPN":
        return False
    if token.ent_type_ in ("PERSON", "GPE", "ORG", "LOC", "NORP",
                            "FAC", "PRODUCT", "EVENT", "WORK_OF_ART",
                            "LAW", "LANGUAGE"):
        return False
    return True


# ── Main processing function ──────────────────────────────────────────────────

def process_transcript(
    text: str,
    language: str = "en",
    surface_forms: Optional[dict] = None,
) -> dict[str, int]:
    """
    Process a clean transcript string and return a vocabulary frequency dict.

    Token order follows first appearance in the transcript. Frequency is
    the count of how many times that lemma appeared across all its forms
    (e.g. "running", "ran", "run" all increment the key "run").

    Args:
        text: Clean transcript string from get_snippets()["_full_text"].
              Must be non-empty.
        language: BCP-47 code of the transcript's actual language, used to
              select the matching spaCy model (see language.SPACY_MODELS).
              Defaults to "en" for callers that don't pass one. Passing
              the wrong language here reproduces the original bug this
              parameter exists to fix — always pass the transcript's
              resolved language, not a hardcoded default, in real use.
        surface_forms: Optional dict, populated in place with
              lemma -> list of the forms that word actually took in the
              transcript. Pass one when the caller needs to search the
              transcript text for a word, since the lemma frequently does
              not appear there literally: a French transcript says "sais",
              the lemma is "savoir", and searching for "savoir" finds
              nothing. cards.build_package() uses this to fill the
              "Example from Youtube Video" field. Left as an out-parameter
              rather than a second return value so existing callers and
              their tests are unaffected.

    Returns:
        Ordered dict mapping lemma (lowercase) → frequency count.
        Example:
        {
            "run":           3,
            "quick":         1,
            "contaminate":   2,
            "water":         5,
            ...
        }

    Raises:
        EmptyTranscriptError:        text is empty or whitespace only.
        SpacyModelUnavailableError:  spaCy has no trained pipeline for
                                     `language` at all.
        NLPModelNotFoundError:       The resolved model isn't installed.
    """
    if not text or not text.strip():
        raise EmptyTranscriptError(
            "Transcript text is empty. "
            "Ensure get_snippets() returned a non-empty '_full_text'."
        )

    nlp = _get_model(language)
    logger.info("Processing transcript: %d characters (language: %s)", len(text), language)

    doc = nlp(text)

    # Resolved once per run, not per token: the lookup table is large and
    # flattening it is the expensive part of the fallback (issue #13).
    known = _known_lemmas(nlp) if language in _VERB_LEMMA_FALLBACKS else frozenset()

    vocabulary: dict[str, int] = {}
    for token in doc:
        if not _is_valid_token(token):
            continue
        lemma = _corrected_lemma(token, language, known).lower()
        if lemma in vocabulary:
            vocabulary[lemma] += 1
        else:
            # First appearance — insert now to preserve ordering
            vocabulary[lemma] = 1

        # Record how the word actually appeared, which is rarely how it is
        # stored. A transcript says "sais", the lemma is "savoir", and a
        # search for the lemma finds nothing -- see cards._find_in_snippets.
        if surface_forms is not None:
            surface = token.text.lower()
            seen = surface_forms.setdefault(lemma, [])
            if surface not in seen and len(seen) < _MAX_SURFACE_FORMS:
                seen.append(surface)

    logger.info(
        "Vocabulary extracted: %d unique lemmas from %d tokens",
        len(vocabulary),
        len(doc),
    )

    return vocabulary


# ── Utility helpers (used by deck.py and state.py) ───────────────────────────

def get_sorted_by_frequency(vocabulary: dict[str, int]) -> dict[str, int]:
    """
    Return a copy of the vocabulary dict sorted by frequency descending.
    Use this when you need ranked output — e.g. most important words first.
    The main process_transcript() output remains first-appearance ordered.
    """
    return dict(sorted(vocabulary.items(), key=lambda item: item[1], reverse=True))


def get_unique_lemmas(vocabulary: dict[str, int]) -> list[str]:
    """
    Return just the lemma keys in first-appearance order.
    Convenience method for callers that only need the word list.
    """
    return list(vocabulary.keys())