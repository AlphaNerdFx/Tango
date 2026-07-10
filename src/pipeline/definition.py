"""
Responsible for fetching word definitions for a list of lemmas and
returning a normalised result for each word.

Source priority:
    1. Merriam-Webster Collegiate API (requires MW_API_KEY in environment)
    2. dictionaryapi.dev                (no auth required, fallback)

For each word the module returns:
    - definition       (first clean definition)
    - example_dict     (example sentence from dictionary, or None)
    - example_transcript (sentence from transcript containing the word)
    - synonyms         (list, may be empty)
    - antonyms         (list, may be empty — rare in free APIs)
    - part_of_speech   (str)
    - source           ("merriam-webster" | "dictionaryapi" | "not_found")

All successful lookups are cached to SQLite immediately.
Cached results are returned without an API call on subsequent runs.

Dependencies:
    requests
    sqlite3 (stdlib)

Environment variables:
    MW_API_KEY     — Merriam-Webster Collegiate API key (required for MW)
    API_DELAY      — Seconds to wait between calls (default 0.5)
    DB_PATH        — Path to SQLite database (default pipeline.db)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Constants (moved to config.py at end of project) ─────────────────────────

from pipeline.config import (
    MW_API_KEY, MW_API_BASE, DICT_API_BASE,
    API_TIMEOUT, API_DELAY, DB_PATH,
)






# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class DefinitionResult:
    """
    Normalised definition for one lemma, regardless of which API sourced it.

    Attributes:
        lemma:                The base word form from spaCy.
        definition:           First clean definition string.
        example_dict:         Example sentence from the dictionary API (or None).
        example_transcript:   Sentence from the video transcript containing
                              the word (or None if not found in snippets).
        synonyms:             List of synonyms. May be empty.
        antonyms:             List of antonyms. May be empty — rare in free APIs.
        part_of_speech:       e.g. "verb", "noun", "adjective".
        source:               Which API provided the data.
    """
    lemma:                str
    definition:           str
    example_dict:         Optional[str]
    example_transcript:   Optional[str]
    synonyms:             list[str]
    antonyms:             list[str]
    part_of_speech:       str
    source:               str


@dataclass
class DefinitionBatchResult:
    """
    Result of processing a full lemma list.

    Attributes:
        found:     DefinitionResult for each word successfully fetched.
        not_found: Lemmas that returned no result from either source.
        from_cache: Lemmas served from SQLite cache (no API call made).
    """
    found:      list[DefinitionResult] = field(default_factory=list)
    not_found:  list[str]              = field(default_factory=list)
    from_cache: list[str]              = field(default_factory=list)


# ── Custom exceptions ─────────────────────────────────────────────────────────

class DefinitionNotFoundError(Exception):
    """Neither API returned a usable definition for this word."""


class MWApiKeyMissingError(Exception):
    """MW_API_KEY is not set — MW lookups will be skipped."""


# ── MW markup cleaner ─────────────────────────────────────────────────────────

def _strip_mw_markup(text: str) -> str:
    """
    Remove Merriam-Webster's proprietary markup tokens from a string.

    MW uses curly-brace tokens in definition and example text:
        {bc}        → colon separator (dropped)
        {it}x{/it}  → italics (keep inner text)
        {b}x{/b}    → bold (keep inner text)
        {sx|x||}    → synonym cross-reference (keep word)
        {dx}...     → directional cross-reference (drop)
        All others  → drop
    """
    text = re.sub(r"\{bc\}", "", text)
    text = re.sub(r"\{it\}(.*?)\{/it\}", r"\1", text)
    text = re.sub(r"\{b\}(.*?)\{/b\}", r"\1", text)
    text = re.sub(r"\{sx\|([^|]+)\|[^}]*\}", r"\1", text)
    text = re.sub(r"\{[^}]+\}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Transcript sentence extractor ─────────────────────────────────────────────

def _find_transcript_sentence(lemma: str, snippets: dict) -> Optional[str]:
    """
    Search the snippet dict from get_snippets() for the first sentence
    that contains the lemma or any of its inflected forms.

    Uses a word-boundary regex so "run" matches "running", "runs", "ran"
    but not "rune" or "runner" when those are unrelated.

    Args:
        lemma:    Base word form (lowercase).
        snippets: Output of transcript.get_snippets(). Float keys are
                  timestamp entries; string keys are metadata.

    Returns:
        The snippet text containing the word, or None if not found.
    """
    pattern = re.compile(r"\b" + re.escape(lemma) + r"\w*", re.IGNORECASE)
    for key, val in snippets.items():
        if not isinstance(key, float):
            continue
        text = val.get("text", "")
        if pattern.search(text):
            return text.strip()
    return None


# ── SQLite cache ──────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS definitions (
            lemma               TEXT PRIMARY KEY,
            definition          TEXT NOT NULL,
            example_dict        TEXT,
            synonyms            TEXT,
            antonyms            TEXT,
            part_of_speech      TEXT,
            source              TEXT NOT NULL,
            fetched_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def _cache_get(lemma: str) -> Optional[dict]:
    """Return cached definition row or None."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM definitions WHERE lemma = ?", (lemma,)
        ).fetchone()
    return dict(row) if row else None


def _cache_set(result: DefinitionResult) -> None:
    """Persist a DefinitionResult to SQLite cache."""
    import json
    with _get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO definitions
                (lemma, definition, example_dict, synonyms, antonyms,
                 part_of_speech, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.lemma,
                result.definition,
                result.example_dict,
                json.dumps(result.synonyms),
                json.dumps(result.antonyms),
                result.part_of_speech,
                result.source,
            ),
        )


def _cache_set_key(key: str, result: DefinitionResult) -> None:
    """Persist a DefinitionResult to SQLite using a custom cache key."""
    import json
    with _get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO definitions
                (lemma, definition, example_dict, synonyms, antonyms,
                 part_of_speech, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                result.definition,
                result.example_dict,
                json.dumps(result.synonyms),
                json.dumps(result.antonyms),
                result.part_of_speech,
                result.source,
            ),
        )


def _cache_row_to_result(
    cache_key: str,
    row: dict,
    snippets: Optional[dict],
) -> DefinitionResult:
    """Reconstruct a DefinitionResult from a SQLite cache row.

    cache_key may be a composite "lemma::language" string.
    The lemma field in the result always returns the bare word only.
    """
    import json
    # Strip the "::language" suffix if present to restore bare lemma
    lemma = cache_key.split("::")[0]
    return DefinitionResult(
        lemma=lemma,
        definition=row["definition"],
        example_dict=row.get("example_dict"),
        example_transcript=(
            _find_transcript_sentence(lemma, snippets) if snippets else None
        ),
        synonyms=json.loads(row.get("synonyms") or "[]"),
        antonyms=json.loads(row.get("antonyms") or "[]"),
        part_of_speech=row.get("part_of_speech", ""),
        source=row["source"],
    )


# ── Merriam-Webster parser ────────────────────────────────────────────────────

def _parse_mw_response(
    lemma: str,
    data: list,
    snippets: Optional[dict],
) -> Optional[DefinitionResult]:
    """
    Parse a MW Collegiate API response into a DefinitionResult.

    MW returns a list. If the first item is a string, it means the word
    wasn't found and the strings are spelling suggestions — return None.
    """
    if not data or isinstance(data[0], str):
        return None

    entry = data[0]

    # ── Part of speech ────────────────────────────────────────────────────────
    pos = entry.get("fl", "").lower() or "unknown"

    # ── Definition ────────────────────────────────────────────────────────────
    short_defs = entry.get("shortdef", [])
    if not short_defs:
        return None
    definition = _strip_mw_markup(short_defs[0])

    # ── Dictionary example (from verbal illustrations in 'def') ───────────────
    example_dict: Optional[str] = None
    try:
        for def_block in entry.get("def", []):
            for sseq_group in def_block.get("sseq", []):
                for sense_pair in sseq_group:
                    if not isinstance(sense_pair, list) or len(sense_pair) < 2:
                        continue
                    sense = sense_pair[1]
                    if not isinstance(sense, dict):
                        continue
                    for dt_item in sense.get("dt", []):
                        if isinstance(dt_item, list) and dt_item[0] == "vis":
                            illustrations = dt_item[1]
                            if illustrations:
                                raw = illustrations[0].get("t", "")
                                example_dict = _strip_mw_markup(raw)
                                break
                    if example_dict:
                        break
                if example_dict:
                    break
    except (KeyError, IndexError, TypeError):
        pass

    # ── Synonyms (from 'syns' field) ──────────────────────────────────────────
    synonyms: list[str] = []
    try:
        for syn_group in entry.get("syns", []):
            for pl_pt in syn_group.get("pt", []):
                if isinstance(pl_pt, list) and pl_pt[0] == "text":
                    raw = _strip_mw_markup(pl_pt[1])
                    # MW synonym text is comma-separated inline
                    synonyms.extend([s.strip() for s in raw.split(",") if s.strip()])
    except (KeyError, IndexError, TypeError):
        pass

    # MW free tier rarely includes antonyms — leave empty
    antonyms: list[str] = []

    return DefinitionResult(
        lemma=lemma,
        definition=definition,
        example_dict=example_dict,
        example_transcript=_find_transcript_sentence(lemma, snippets) if snippets else None,
        synonyms=synonyms[:5],   # cap at 5
        antonyms=antonyms,
        part_of_speech=pos,
        source="merriam-webster",
    )


# ── dictionaryapi.dev parser ──────────────────────────────────────────────────

def _parse_dictapi_response(
    lemma: str,
    data: list,
    snippets: Optional[dict],
) -> Optional[DefinitionResult]:
    """
    Parse a dictionaryapi.dev response into a DefinitionResult.
    """
    if not data or not isinstance(data, list):
        return None

    entry = data[0]
    meanings = entry.get("meanings", [])
    if not meanings:
        return None

    meaning = meanings[0]
    pos = meaning.get("partOfSpeech", "unknown").lower()

    definitions = meaning.get("definitions", [])
    if not definitions:
        return None

    first_def = definitions[0]
    definition = first_def.get("definition", "").strip()
    if not definition:
        return None

    example_dict: Optional[str] = None
    # Try definition-level example first, then scan all definitions
    raw_example = first_def.get("example", "")
    if raw_example:
        example_dict = raw_example.strip()
    else:
        for defn in definitions[1:]:
            candidate = defn.get("example", "")
            if candidate:
                example_dict = candidate.strip()
                break

    # Synonyms and antonyms — present at both meaning and definition level
    synonyms: list[str] = (
        meaning.get("synonyms", []) or first_def.get("synonyms", [])
    )
    antonyms: list[str] = (
        meaning.get("antonyms", []) or first_def.get("antonyms", [])
    )

    return DefinitionResult(
        lemma=lemma,
        definition=definition,
        example_dict=example_dict,
        example_transcript=_find_transcript_sentence(lemma, snippets) if snippets else None,
        synonyms=synonyms[:5],
        antonyms=antonyms[:5],
        part_of_speech=pos,
        source="dictionaryapi",
    )


# ── API callers ───────────────────────────────────────────────────────────────

def _fetch_from_mw(lemma: str) -> Optional[list]:
    """
    Call the MW Collegiate API for one word.

    Returns the raw JSON list, or None if the key is missing or the call fails.
    """
    api_key = MW_API_KEY
    if not api_key:
        logger.debug("MW_API_KEY not set — skipping MW lookup for '%s'.", lemma)
        return None

    url = f"{MW_API_BASE}/{lemma}?key={api_key}"
    try:
        response = requests.get(url, timeout=API_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning("MW HTTP error for '%s': %s", lemma, exc)
    except requests.exceptions.RequestException as exc:
        logger.warning("MW request failed for '%s': %s", lemma, exc)
    return None


def _fetch_from_dictapi(lemma: str, language: str = "en") -> Optional[list]:
    """
    Call dictionaryapi.dev for one word in the specified language.

    When language is "en", queries the English endpoint (default).
    When language is "fr", "de", "es" etc., queries the native language endpoint
    returning definitions in that language.

    Returns the raw JSON list, or None on failure.
    """
    url = f"{DICT_API_BASE.rstrip('/')}/{language}/{lemma}"
    try:
        response = requests.get(url, timeout=API_TIMEOUT)
        if response.status_code == 404:
            logger.debug("dictionaryapi: '%s' not found.", lemma)
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning("dictionaryapi HTTP error for '%s': %s", lemma, exc)
    except requests.exceptions.RequestException as exc:
        logger.warning("dictionaryapi request failed for '%s': %s", lemma, exc)
    return None


# ── Single word fetch ─────────────────────────────────────────────────────────

def fetch_definition(
    lemma: str,
    snippets: Optional[dict] = None,
    use_cache: bool = True,
    language: str = "en",
    def_language: Optional[str] = None,
) -> Optional[DefinitionResult]:
    """
    Fetch a definition for one lemma.

    Checks SQLite cache first. If not cached, queries APIs based on the
    language resolution:

    Native mode (def_language is None or equals language):
        dictionaryapi.dev/{language}/{lemma} → fallback card

    Translation mode (def_language differs from language):
        Translates lemma via translation.translate_word()
        then MW(translated) → dictionaryapi.dev/en(translated)

    English mode (language == "en"):
        MW(lemma) → dictionaryapi.dev/en(lemma)  [original behaviour]

    Args:
        lemma:        Lowercase lemma from nlp.py.
        snippets:     Output of transcript.get_snippets(). Used to find the
                      transcript example sentence. Pass None to skip.
        use_cache:    Set False to force a fresh API call (e.g. in tests).
        language:     BCP-47 code of the transcript language (e.g. "fr").
        def_language: BCP-47 code for definition output. If None or same as
                      language, definitions are fetched in native language.
                      If different (e.g. "en"), word is translated first.

    Returns:
        DefinitionResult or None if neither API found the word.
    """
    # ── Cache check ───────────────────────────────────────────────────────────
    cache_key = f"{lemma}::{def_language or language}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached:
            logger.debug("Cache hit for '%s'.", cache_key)
            return _cache_row_to_result(cache_key, cached, snippets)

    # ── Resolve which word and language to query ──────────────────────────────
    target_language = def_language or language
    query_lemma     = lemma

    # Translation mode: translate lemma to def_language first
    if def_language and def_language != language:
        try:
            from pipeline.translation import translate_word, TranslationUnavailableError
            translated = translate_word(query_lemma, language, def_language)
            if translated:
                logger.info(
                    "Translated '%s' (%s->%s): '%s'",
                    lemma, language, def_language, translated,
                )
                query_lemma = translated
            else:
                # User chose to continue without translation
                target_language = language
                logger.warning(
                    "Translation unavailable for '%s'. "
                    "Falling back to native '%s' definition.",
                    lemma, language,
                )
        except TranslationUnavailableError:
            raise

    # ── English / translated word: MW first ───────────────────────────────────
    if target_language == "en":
        mw_data = _fetch_from_mw(query_lemma)
        if mw_data:
            result = _parse_mw_response(query_lemma, mw_data, snippets)
            if result:
                # Store under original lemma for consistent cache keys
                result = DefinitionResult(
                    lemma=lemma, definition=result.definition,
                    example_dict=result.example_dict,
                    example_transcript=result.example_transcript,
                    synonyms=result.synonyms, antonyms=result.antonyms,
                    part_of_speech=result.part_of_speech, source=result.source,
                )
                logger.info("MW: found '%s' (queried as '%s').", lemma, query_lemma)
                _cache_set_key(cache_key, result)
                return result

    # ── dictionaryapi.dev (native or English fallback) ────────────────────────
    dict_data = _fetch_from_dictapi(query_lemma, language=target_language)
    if dict_data:
        result = _parse_dictapi_response(query_lemma, dict_data, snippets)
        if result:
            result = DefinitionResult(
                lemma=lemma, definition=result.definition,
                example_dict=result.example_dict,
                example_transcript=result.example_transcript,
                synonyms=result.synonyms, antonyms=result.antonyms,
                part_of_speech=result.part_of_speech, source=result.source,
            )
            logger.info(
                "dictionaryapi[%s]: found '%s'.", target_language, lemma
            )
            _cache_set_key(cache_key, result)
            return result

    logger.warning("No definition found for '%s' from either source.", lemma)
    return None


# ── Batch fetch ───────────────────────────────────────────────────────────────

def fetch_definitions(
    lemmas: list[str],
    snippets: Optional[dict] = None,
    delay: float = API_DELAY,
    language: str = "en",
    def_language: Optional[str] = None,
) -> DefinitionBatchResult:
    """
    Fetch definitions for a list of lemmas in first-appearance order.

    Processes each lemma sequentially with a configurable delay between
    live API calls to stay within rate limits. Cache hits incur no delay.

    This function is designed to be called with the full NEW word list
    from deck.check_vocabulary(). Words in the SKIP or QUEUE lists
    should not be passed here.

    Args:
        lemmas:   Ordered list of lemmas (new words only).
        snippets: Output of transcript.get_snippets(). Pass for transcript
                  example sentences. Pass None to skip.
        delay:    Seconds to wait between live API calls. Default from
                  API_DELAY env var (0.5s). Set 0 in tests.

    Returns:
        DefinitionBatchResult with found, not_found, and from_cache lists.
    """
    batch = DefinitionBatchResult()

    for i, lemma in enumerate(lemmas, start=1):
        logger.debug("Processing %d/%d: '%s'", i, len(lemmas), lemma)

        # Check cache before sleeping
        cached = _cache_get(lemma)
        if cached:
            result = _cache_row_to_result(lemma, cached, snippets)
            batch.found.append(result)
            batch.from_cache.append(lemma)
            continue

        # Live API call — apply delay between requests
        if i > 1:
            time.sleep(delay)

        result = fetch_definition(
                lemma, snippets, use_cache=False,
                language=language, def_language=def_language,
            )

        if result:
            batch.found.append(result)
        else:
            batch.not_found.append(lemma)

    logger.info(
        "Batch complete: %d found (%d cached) / %d not found",
        len(batch.found),
        len(batch.from_cache),
        len(batch.not_found),
    )

    return batch