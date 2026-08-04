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


def _is_valid_lemma(lemma: str) -> bool:
    """
    A lemma is valid if it is at least two characters, contains no
    digits or underscores, and consists of alphabetic runs joined
    only by internal hyphens or apostrophes.

    Permits: semi-relevé, week-end, arc-en-ciel, aujourd'hui
    Rejects: e, -, ->, -là, qu', 3d, semi-
    """
    if len(lemma) < 2:
        return False
    return bool(_VALID_LEMMA.match(lemma))


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

    The vocabulary dict is keyed by token.lemma_, not token.text, so
    validity is decided on the lemma alone. token.is_alpha describes
    the surface form and is deliberately NOT checked here: real
    compounds and contractions (semi-relevé, week-end, aujourd'hui)
    have non-alphabetic surface forms, so gating on token.is_alpha
    would reject them before _is_valid_lemma ever ran. _is_valid_lemma
    already rejects punctuation, digits, and boundary-hyphen/apostrophe
    fragments on its own -- it is not a narrower check that needs
    token.is_alpha as a backstop.
    """
    if not _is_valid_lemma(token.lemma_):
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

def process_transcript(text: str, language: str = "en") -> dict[str, int]:
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

    vocabulary: dict[str, int] = {}
    for token in doc:
        if not _is_valid_token(token):
            continue
        lemma = token.lemma_.lower()
        if lemma in vocabulary:
            vocabulary[lemma] += 1
        else:
            # First appearance — insert now to preserve ordering
            vocabulary[lemma] = 1

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