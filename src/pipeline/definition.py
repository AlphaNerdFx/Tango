"""
definition.py
-------------
Responsible for fetching word definitions for a list of lemmas and
returning a normalised result for each word.

Source priority:
    1. Merriam-Webster Collegiate API (requires MW_API_KEY in environment)
    2. dictionaryapi.dev                (no auth required, fallback)
    3. English Wiktionary's REST API    (no auth required, non-English
                                          example sentences only, see #1)

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
    MW_API_KEY                 — Merriam-Webster Collegiate API key (required for MW)
    DEFINITION_FETCH_WORKERS   — Max concurrent lookups in flight (default 5)
    DB_PATH                    — Path to SQLite database (default pipeline.db)

fetch_definitions() fetches live (non-cached) words concurrently via a
thread pool rather than one at a time. See ARCHITECTURE.md's design
patterns section for why a thread pool was chosen over asyncio/aiohttp.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# BCP-47 -> Open Multilingual Wordnet ISO 639-3 code, for the languages
# where the installed omw-2.0 package actually has data (see ADR-008).
# Confirmed by inspecting the downloaded package directly, not assumed:
# German, Russian, Ukrainian, Macedonian, and Korean have no entry here
# because OMW has no data for them at all, not because of an oversight.
_OMW_LANGUAGE_CODES: dict[str, str] = {
    "fr": "fra", "es": "spa", "it": "ita", "pt": "por", "nl": "nld",
    "pl": "pol", "ro": "ron", "el": "ell", "da": "dan", "sv": "swe",
    "nb": "nob", "fi": "fin", "lt": "lit", "hr": "hrv", "sl": "slv",
    "ca": "cat", "ja": "jpn", "zh": "cmn",
}

_omw_loaded_langs: set[str] = set()

# Guards EVERY read of nltk's WordNet corpus, not just the one-time load.
# See _wordnet_synonyms_antonyms for why reads need it too.
_wordnet_lock = threading.Lock()


def _ensure_omw_loaded(omw_lang: str = "eng") -> bool:
    """
    Load WordNet/OMW data for one language, once per process, under a lock.

    Returns False (without raising) if the data isn't installed, so callers
    degrade to no synonyms rather than crashing -- this mirrors every other
    optional-enhancement source in this module. Needs omw-2.0 specifically;
    the older omw-1.4 package installs without error but this NLTK version
    never uses it (see ADR-008).

    The warm-up query inside the lock is load-bearing, not a sanity check.
    nltk loads each language's data lazily on that language's first
    synsets() call, and that lazy load is NOT thread-safe: with a cold
    corpus and DEFINITION_FETCH_WORKERS threads racing into it, most calls
    raise (AssertionError from a half-populated index, or AttributeError on
    a None file handle). _wordnet_synonyms_antonyms catches those and
    returns empty, so the symptom was never an error -- just silently
    missing synonyms on a varying subset of cards each run, measured at
    7 of 8 words lost in a cold process. Forcing the load here, while
    holding the lock, means worker threads only ever hit an already-warm
    index. Tracked per language because each one loads separately.
    """
    if omw_lang in _omw_loaded_langs:
        return True
    with _wordnet_lock:
        if omw_lang in _omw_loaded_langs:
            return True
        try:
            from nltk.corpus import wordnet as wn
            if omw_lang != "eng":
                wn.add_omw()
            wn.synsets("test", lang=omw_lang)
            _omw_loaded_langs.add(omw_lang)
        except Exception:
            return False
    return True


def _wordnet_synonyms_antonyms(word: str, language: str = "en") -> tuple:
    """
    Return (synonyms, antonyms) from WordNet for a word in `language`.
    Used to supplement empty synonym/antonym lists from API sources.
    Returns empty lists if WordNet/OMW is unavailable, the language isn't
    covered (see _OMW_LANGUAGE_CODES), or the word isn't found.

    Synonyms come back in WordNet's synset order (most common sense first),
    capped at 5. That ordering is load-bearing, not incidental: the cap
    discards whatever doesn't fit, so ordering by anything other than
    relevance throws away the most useful words. See the inline comment.

    Antonyms are only ever attempted for English. Confirmed directly
    against OMW: antonym relations are not carried over for non-English
    lemmas (empty in every case checked, and the one non-empty result
    found during that check pointed at an English antonym object, not a
    real antonym pair in the requested language) -- not worth the risk of
    writing an English antonym into a non-English field.
    """
    omw_lang = "eng" if language == "en" else _OMW_LANGUAGE_CODES.get(language)
    if not omw_lang:
        return [], []
    # English needs this too: the core WordNet corpus has the same unsafe
    # lazy first-load, it just wasn't the language that surfaced it.
    if not _ensure_omw_loaded(omw_lang):
        return [], []

    try:
        from nltk.corpus import wordnet as wn
        # Collect synonyms in WordNet's own synset order, NOT alphabetically.
        # wn.synsets() returns senses roughly most-common-first, so synset 0's
        # lemmas are the ones a learner actually wants. Sorting the pooled
        # results alphabetically and then cutting at 5 discarded them whenever
        # a later, rarer sense happened to contribute earlier letters --
        # measured at 207 of 972 cards on a real French run. Concretely,
        # "aujourd'hui" lost "maintenant" while keeping "de notre temps", and
        # "faire" lost "mettre"/"organiser" while keeping "caguer"/"déféquer".
        synonyms: list[str] = []
        seen: set[str] = set()
        antonyms: set = set()
        lang_arg = {} if language == "en" else {"lang": omw_lang}
        # Serialize the whole traversal. nltk's WordNet reader seeks and
        # reads a shared file handle, so concurrent lookups corrupt each
        # other's reads and raise AssertionError -- measured at 12 of 80
        # lookups lost across DEFINITION_FETCH_WORKERS threads, silently,
        # because the except below turns each one into "no synonyms".
        # Warming the corpus first is necessary but not sufficient; the
        # reads themselves race too.
        #
        # This costs nothing. These are local, already-warm lookups, and
        # serialized they measured ~6x FASTER than the contended version
        # (11ms vs 68ms for 80 lookups) on top of being correct. The
        # thread pool exists for network I/O, which this is not.
        with _wordnet_lock:
            for syn in wn.synsets(word, **lang_arg)[:3]:
                for lemma in syn.lemmas(**lang_arg):
                    name = lemma.name().replace("_", " ")
                    key = name.lower()
                    if key != word.lower() and key not in seen:
                        seen.add(key)
                        synonyms.append(name)
                    if language == "en":
                        for ant in lemma.antonyms():
                            antonyms.add(ant.name().replace("_", " "))
        return synonyms[:5], sorted(antonyms)[:5]
    except Exception:
        return [], []

# ── Constants (moved to config.py at end of project) ─────────────────────────

from pipeline.config import (
    MW_API_KEY, MW_API_BASE, DICT_API_BASE,
    WIKTIONARY_API_BASE, WIKTIONARY_USER_AGENT,
    API_TIMEOUT, DB_PATH, CIRCUIT_BREAKER_THRESHOLD, DEFINITION_FETCH_WORKERS,
)






# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class DefinitionResult:
    """
    Normalised definition for one lemma, regardless of which API sourced it.

    Attributes:
        lemma:                The base word form from spaCy.
        definition:           First clean definition string.
        example_dict:         First example sentence from the dictionary (or None).
        example_dict2:        Second example sentence from the dictionary (or None).
        example_transcript:   Sentence from the video transcript (or None).
        synonyms:             List of synonyms. May be empty.
        antonyms:             List of antonyms. May be empty.
        part_of_speech:       e.g. "verb", "noun", "adjective".
        source:               Which API provided the data.
    """
    lemma:                str
    definition:           str
    example_dict:         Optional[str]
    example_dict2:        Optional[str]
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
        found:              DefinitionResult for each word successfully fetched.
        not_found:          Lemmas that returned no result from either source.
        from_cache:         Lemmas served from SQLite cache (no API call made).
        not_found_examples: Native-language example sentence from Wiktionary
                             for a not_found lemma, keyed by lemma. Populated
                             only for non-English lemmas where no definition
                             was found anywhere but Wiktionary still had a
                             usable example (issue #1) -- lets the resulting
                             fallback card show a real dictionary example
                             instead of just the transcript sentence.
        not_found_synonyms: OMW/WordNet synonyms for a not_found lemma, keyed
                             by lemma (see ADR-008). fetch_definition() only
                             runs its own OMW lookup after finding a
                             definition, so a lemma with no definition
                             anywhere -- the common case for languages
                             dictionaryapi.dev barely covers, like French --
                             would otherwise never get a synonym at all.
        not_found_antonyms: Same as not_found_synonyms, but antonyms. Rarely
                             populated for non-English lemmas since OMW only
                             carries antonym data for English synsets.
    """
    found:              list[DefinitionResult] = field(default_factory=list)
    not_found:          list[str]              = field(default_factory=list)
    from_cache:         list[str]              = field(default_factory=list)
    not_found_examples: dict[str, str]         = field(default_factory=dict)
    not_found_synonyms: dict[str, list[str]]   = field(default_factory=dict)
    not_found_antonyms: dict[str, list[str]]   = field(default_factory=dict)


# ── Custom exceptions ─────────────────────────────────────────────────────────

class DefinitionNotFoundError(Exception):
    """Neither API returned a usable definition for this word."""


class MWApiKeyMissingError(Exception):
    """MW_API_KEY is not set — MW lookups will be skipped."""


def _strip_accents(text: str) -> str:
    """
    Strip diacritical marks from accented characters.
    Used as fallback when the accented form returns no API results.
    Example: deteste -> deteste, etre -> etre.
    """
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


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
#
# fetch_definitions() now runs several lemmas concurrently (see
# DEFINITION_FETCH_WORKERS), and each one opens its own cache connection to
# read and write the same file. Two things about that need to be handled
# explicitly under real thread concurrency, neither of which mattered when
# every call was sequential:
#
# 1. Schema setup (CREATE TABLE / ALTER TABLE) used to run on every single
#    _get_db() call. DDL statements hold a stronger lock than a plain
#    INSERT, so doing that on every one of several concurrent connections
#    made lock contention worse than the actual cache traffic. Now runs
#    once per DB_PATH (guarded by _schema_lock), not once per connection.
# 2. A cache read or write can still lose a lock race under load even with
#    that fixed (observed live: sqlite3.OperationalError: database is
#    locked, during a concurrent English run). Caching is an optimisation,
#    not a correctness requirement -- a lock timeout on the write must
#    never cause an already-successfully-fetched definition to be
#    discarded, and a lock timeout on the read must never cause a lemma to
#    be treated as uncached when it might already be there. Both cache
#    functions below log and fail soft instead of propagating.

_schema_lock: threading.Lock = threading.Lock()
_initialized_dbs: set = set()


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS definitions (
            lemma               TEXT PRIMARY KEY,
            definition          TEXT NOT NULL,
            example_dict        TEXT,
            example_dict2       TEXT,
            synonyms            TEXT,
            antonyms            TEXT,
            part_of_speech      TEXT,
            source              TEXT NOT NULL,
            fetched_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Migrate existing databases that predate the example_dict2 column
    try:
        conn.execute("ALTER TABLE definitions ADD COLUMN example_dict2 TEXT")
    except Exception:
        pass  # Column already exists — safe to ignore
    conn.commit()


def _get_db() -> sqlite3.Connection:
    # timeout=30 (vs sqlite3's 5s default) gives a thread more room to wait
    # its turn for the write lock under concurrent access before giving up.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    db_key = str(DB_PATH)
    if db_key not in _initialized_dbs:
        with _schema_lock:
            if db_key not in _initialized_dbs:
                _init_schema(conn)
                _initialized_dbs.add(db_key)
    return conn


def _cache_get(lemma: str) -> Optional[dict]:
    """Return cached definition row, or None on a cache miss or read failure."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT * FROM definitions WHERE lemma = ?", (lemma,)
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        logger.warning("Cache read failed for '%s': %s", lemma, exc)
        return None


def _cache_set(result: DefinitionResult) -> None:
    """Persist a DefinitionResult to SQLite cache."""
    import json
    with _get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO definitions
                (lemma, definition, example_dict, example_dict2, synonyms, antonyms,
                 part_of_speech, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.lemma,
                result.definition,
                result.example_dict,
                getattr(result, "example_dict2", None),
                json.dumps(result.synonyms),
                json.dumps(result.antonyms),
                result.part_of_speech,
                result.source,
            ),
        )


def _cache_set_key(key: str, result: DefinitionResult) -> None:
    """
    Persist a DefinitionResult to SQLite using a custom cache key.

    Logs and swallows a write failure rather than raising. This is the
    return path fetch_definition() reaches only after successfully getting
    a real result -- if caching that result loses a lock race under
    concurrent fetches, the correct behaviour is a cache miss next time,
    not discarding a result that was already fetched successfully.
    """
    import json
    try:
        with _get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO definitions
                    (lemma, definition, example_dict, example_dict2, synonyms, antonyms,
                     part_of_speech, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    result.definition,
                    result.example_dict,
                    getattr(result, "example_dict2", None),
                    json.dumps(result.synonyms),
                    json.dumps(result.antonyms),
                    result.part_of_speech,
                    result.source,
                ),
            )
    except sqlite3.Error as exc:
        logger.warning("Cache write failed for '%s': %s", key, exc)


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
        example_dict2=row.get("example_dict2"),
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

    # ── Dictionary examples (from verbal illustrations in 'def') ──────────────
    examples_dict: list[str] = []
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
                            for illus in dt_item[1]:
                                raw = illus.get("t", "")
                                if raw:
                                    examples_dict.append(_strip_mw_markup(raw))
                                if len(examples_dict) >= 2:
                                    break
                    if len(examples_dict) >= 2:
                        break
                if len(examples_dict) >= 2:
                    break
    except (KeyError, IndexError, TypeError):
        pass
    example_dict  = examples_dict[0] if len(examples_dict) > 0 else None
    example_dict2 = examples_dict[1] if len(examples_dict) > 1 else None

    # ── Synonyms (from 'syns' field) ──────────────────────────────────────────
    # MW's 'syns' field is NOT a flat comma-separated list — for any word with
    # a real "Synonym Discussion" (a standard MW Collegiate feature covering
    # nuanced near-synonyms), it's full prose with the actual synonym words
    # individually marked {sc}word{/sc} (small-caps) inside complete sentences
    # with no commas at all. Splitting the whole sentence on "," (the previous
    # approach) produced one giant blob per sentence for any such word.
    # Extracting the {sc} tags directly, instead of the surrounding prose, is
    # required. See issue #10.
    synonyms: list[str] = []
    try:
        seen = {lemma.lower()}
        for syn_group in entry.get("syns", []):
            for pl_pt in syn_group.get("pt", []):
                if isinstance(pl_pt, list) and pl_pt[0] == "text":
                    for word in re.findall(r"\{sc\}(.*?)\{/sc\}", pl_pt[1]):
                        key = word.lower()
                        if key not in seen:
                            seen.add(key)
                            synonyms.append(word)
    except (KeyError, IndexError, TypeError):
        pass

    # MW free tier rarely includes antonyms — leave empty
    antonyms: list[str] = []

    return DefinitionResult(
        lemma=lemma,
        definition=definition,
        example_dict=example_dict,
        example_dict2=example_dict2,
        example_transcript=_find_transcript_sentence(lemma, snippets) if snippets else None,
        synonyms=synonyms[:5],
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

    # Collect up to 2 example sentences
    examples_dict: list[str] = []
    for defn in definitions:
        candidate = defn.get("example", "")
        if candidate:
            examples_dict.append(candidate.strip())
        if len(examples_dict) >= 2:
            break
    example_dict  = examples_dict[0] if len(examples_dict) > 0 else None
    example_dict2 = examples_dict[1] if len(examples_dict) > 1 else None

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
        example_dict2=example_dict2,
        example_transcript=_find_transcript_sentence(lemma, snippets) if snippets else None,
        synonyms=synonyms[:5],
        antonyms=antonyms[:5],
        part_of_speech=pos,
        source="dictionaryapi",
    )


# ── Circuit breaker ────────────────────────────────────────────────────────
# After CIRCUIT_BREAKER_THRESHOLD consecutive failures against one source,
# stop calling it for the rest of the run and treat further calls as an
# immediate failure, no network request made. A run with 108 failing
# lookups against a source that was down for the whole run paid a full
# network timeout on every single one of them — roughly 2.6s/failure, 280s
# total for that source alone.
#
# Only counts server errors, timeouts, and connection failures as failures.
# A 404 does NOT count — it means the source is reachable and healthy, it
# simply doesn't have this word (see issue #1's 404-vs-502 investigation:
# "404 means the endpoint works and the word is absent. 502 means the
# upstream server broke" — those are different failure modes and only the
# second one should trip the breaker). Treating 404s as failures would trip
# the breaker on any source with genuinely sparse coverage for a language
# after only a few words, even though the source itself is working fine.
#
# Keyed by source name, or "source:language" for dictionaryapi.dev and
# wiktionary, since a single run's native-language and target-language
# dictionaryapi.dev calls can have very different reliability (e.g. a
# French-to-English run: the French native lookups may be failing
# consistently per issue #1 while the English target lookups work fine —
# one must not trip the other's breaker).
_circuit_failures: dict[str, int] = {}
_circuit_tripped: set[str] = set()

# fetch_definitions() now runs live lookups concurrently across several
# threads (see DEFINITION_FETCH_WORKERS), and multiple threads can update
# the same source's failure count at once. A plain read-modify-write like
# `_circuit_failures[source] = _circuit_failures.get(source, 0) + 1` is not
# atomic across two bytecodes, so two threads could both read the same
# count before either writes back, undercounting a real failure streak.
# This lock makes each read-modify-write on the breaker state atomic.
_circuit_lock = threading.Lock()


def reset_circuit_breaker() -> None:
    """
    Clear all circuit breaker state. Call once at the start of each pipeline
    run (mirrors translation.reset_warning_state()) so a source that tripped
    in a previous run/process doesn't stay tripped — this module-level state
    would otherwise leak across multiple calls within the same process (e.g.
    tests, scratch scripts, or --review/--process-backlog re-invocations).
    """
    with _circuit_lock:
        _circuit_failures.clear()
        _circuit_tripped.clear()


def _circuit_is_tripped(source: str) -> bool:
    with _circuit_lock:
        return source in _circuit_tripped


def _circuit_record_failure(source: str) -> None:
    with _circuit_lock:
        count = _circuit_failures.get(source, 0) + 1
        _circuit_failures[source] = count
        if count >= CIRCUIT_BREAKER_THRESHOLD and source not in _circuit_tripped:
            _circuit_tripped.add(source)
            logger.warning(
                "Circuit breaker tripped for '%s' after %d consecutive failures — "
                "skipping it for the rest of this run.",
                source, count,
            )


def _circuit_record_success(source: str) -> None:
    with _circuit_lock:
        _circuit_failures[source] = 0


# ── API callers ───────────────────────────────────────────────────────────────

def _fetch_from_mw(lemma: str) -> Optional[list]:
    """
    Call the MW Collegiate API for one word.

    Returns the raw JSON list, or None if the key is missing, the circuit
    breaker for "mw" is tripped, or the call fails.
    """
    api_key = MW_API_KEY
    if not api_key:
        logger.debug("MW_API_KEY not set — skipping MW lookup for '%s'.", lemma)
        return None

    if _circuit_is_tripped("mw"):
        logger.debug("Circuit breaker open for 'mw' — skipping '%s'.", lemma)
        return None

    url = f"{MW_API_BASE}/{lemma}?key={api_key}"
    try:
        response = requests.get(url, timeout=API_TIMEOUT)
        response.raise_for_status()
        _circuit_record_success("mw")
        return response.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning("MW HTTP error for '%s': %s", lemma, exc)
        _circuit_record_failure("mw")
    except requests.exceptions.RequestException as exc:
        logger.warning("MW request failed for '%s': %s", lemma, exc)
        _circuit_record_failure("mw")
    return None


def _fetch_from_dictapi(lemma: str, language: str = "en") -> Optional[list]:
    """
    Call dictionaryapi.dev for one word in the specified language.

    When language is "en", queries the English endpoint (default).
    When language is "fr", "de", "es" etc., queries the native language endpoint
    returning definitions in that language.

    Returns the raw JSON list, or None if not found, the circuit breaker for
    this language is tripped, or the call fails.
    """
    source = f"dictapi:{language}"
    if _circuit_is_tripped(source):
        logger.debug("Circuit breaker open for '%s' — skipping '%s'.", source, lemma)
        return None

    url = f"{DICT_API_BASE.rstrip('/')}/{language}/{lemma}"
    try:
        response = requests.get(url, timeout=API_TIMEOUT)
        if response.status_code == 404:
            logger.debug("dictionaryapi: '%s' not found.", lemma)
            # Source responded and is healthy -- it just doesn't have this
            # word. Not a failure; do not count it toward the breaker.
            _circuit_record_success(source)
            return None
        response.raise_for_status()
        _circuit_record_success(source)
        return response.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning("dictionaryapi HTTP error for '%s': %s", lemma, exc)
        _circuit_record_failure(source)
    except requests.exceptions.RequestException as exc:
        logger.warning("dictionaryapi request failed for '%s': %s", lemma, exc)
        _circuit_record_failure(source)
    return None


def _fetch_from_wiktionary(lemma: str, language: str) -> Optional[list]:
    """
    Call English Wiktionary's REST definition endpoint for `lemma` and
    return the entry list for `language`'s section, or None.

    Always queries en.wiktionary.org regardless of `language` — see the
    WIKTIONARY_API_BASE comment in config.py for why. English is excluded
    by the caller; this source exists to supplement non-English examples.

    Returns the raw JSON list, or None if not found, rate-limited, the
    circuit breaker for this language is tripped, or the call fails.
    """
    source = f"wiktionary:{language}"
    if _circuit_is_tripped(source):
        logger.debug("Circuit breaker open for '%s' — skipping '%s'.", source, lemma)
        return None

    url = f"{WIKTIONARY_API_BASE}/{lemma}"
    try:
        response = requests.get(
            url, timeout=API_TIMEOUT, headers={"User-Agent": WIKTIONARY_USER_AGENT}
        )
        if response.status_code == 404:
            logger.debug("Wiktionary: '%s' not found.", lemma)
            _circuit_record_success(source)
            return None
        if response.status_code == 429:
            # Rate-limited, not "word not found" -- the source is telling
            # us to back off, which is exactly what the breaker is for.
            logger.warning("Wiktionary rate-limited us fetching '%s'.", lemma)
            _circuit_record_failure(source)
            return None
        response.raise_for_status()
        data = response.json()
        _circuit_record_success(source)
        return data.get(language)
    except requests.exceptions.HTTPError as exc:
        logger.warning("Wiktionary HTTP error for '%s': %s", lemma, exc)
        _circuit_record_failure(source)
    except requests.exceptions.RequestException as exc:
        logger.warning("Wiktionary request failed for '%s': %s", lemma, exc)
        _circuit_record_failure(source)
    except ValueError:
        logger.warning("Wiktionary returned invalid JSON for '%s'.", lemma)
        _circuit_record_failure(source)
    return None


def _parse_wiktionary_examples(data: list) -> list[str]:
    """
    Extract up to 2 native-language example sentences from a Wiktionary
    language-section entry list.

    Ignores the "definition" text entirely -- on en.wiktionary.org that
    text is an English gloss of the foreign word (e.g. "chat" -> "cat"),
    not a native-language definition, and CLAUDE.md 3.3 requires examples
    to stay in the transcript language while saying nothing that permits
    writing an English gloss into a native-language Definition field. Only
    the "examples" array is native-language text (confirmed by manual
    inspection: French entries return French usage sentences even though
    their definition text is English).

    Strips Wiktionary's HTML entirely (the matched word arrives wrapped in
    <b> tags, definitions carry template-generated <span>/<a> markup) since
    none of it is worth preserving in a plain-text example sentence.
    """
    examples: list[str] = []
    for entry in data or []:
        for defn in entry.get("definitions", []):
            for ex in defn.get("examples") or []:
                clean = re.sub(r"<[^>]+>", "", ex).strip()
                if clean and clean not in examples:
                    examples.append(clean)
                if len(examples) >= 2:
                    return examples
    return examples


# ── Single word fetch ─────────────────────────────────────────────────────────

def fetch_definition(
    lemma: str,
    snippets: Optional[dict] = None,
    use_cache: bool = True,
    language: str = "en",
    def_language: Optional[str] = None,
) -> Optional[DefinitionResult]:
    """
    Fetch a definition for one lemma using a dual-source strategy:

    Native source (always):
        dictionaryapi.dev/{language}/ -> examples, synonyms, antonyms
        These fields are always in the original transcript language.
        If dictionaryapi.dev has no example sentence and language != "en",
        English Wiktionary's REST API supplements the example only (see
        issue #1 and ARCHITECTURE.md 8.13) -- it does not supply synonyms,
        antonyms, or a native-language definition.

    Target source (definition + class only):
        If def_language == language or def_language is None:
            Same as native source — single fetch covers everything.
        If def_language differs:
            Translate lemma then MW -> dictionaryapi.dev/{def_language}/
            Only definition and part_of_speech come from this source.

    Field completion:
        If MW returns definition but missing examples/synonyms,
        dictionaryapi.dev supplements the missing fields.

    All text fields are capped at 256 characters.
    """
    target_language = def_language or language
    cache_key       = f"{lemma}::{target_language}"

    if use_cache:
        cached = _cache_get(cache_key)
        if cached:
            logger.debug("Cache hit for '%s'.", cache_key)
            return _cache_row_to_result(cache_key, cached, snippets)

    # ── Step 1: Always fetch native-language data ─────────────────────────────
    native_ex1: Optional[str]  = None
    native_ex2: Optional[str]  = None
    native_syns: list[str]     = []
    native_ants: list[str]     = []

    native_data = _fetch_from_dictapi(lemma, language=language)
    if native_data:
        nr = _parse_dictapi_response(lemma, native_data, snippets)
        if nr:
            native_ex1  = nr.example_dict
            native_ex2  = getattr(nr, "example_dict2", None)
            native_syns = nr.synonyms
            native_ants = nr.antonyms

    # Fallback: try accent-stripped variant if native fetch returned nothing
    if not native_ex1 and not native_syns:
        stripped = _strip_accents(lemma)
        if stripped != lemma:
            fallback_data = _fetch_from_dictapi(stripped, language=language)
            if fallback_data:
                nr2 = _parse_dictapi_response(stripped, fallback_data, snippets)
                if nr2:
                    native_ex1  = nr2.example_dict
                    native_ex2  = getattr(nr2, "example_dict2", None)
                    native_syns = nr2.synonyms
                    native_ants = nr2.antonyms
                    logger.debug("Accent-stripped fallback used for '%s'.", lemma)

    # Supplement native-language examples from Wiktionary when
    # dictionaryapi.dev came back with none. Non-English only -- English
    # already gets solid example coverage from MW/dictionaryapi.dev, and
    # Wiktionary has nothing to add for it here. See issue #1 and
    # ARCHITECTURE.md 8.13 for why this exists and what it does not fix
    # (it supplements examples only, not definitions, synonyms, or
    # antonyms -- those remain limited for non-English languages).
    if language != "en" and not native_ex1:
        wikt_data = _fetch_from_wiktionary(lemma, language)
        if wikt_data:
            wikt_examples = _parse_wiktionary_examples(wikt_data)
            if wikt_examples:
                native_ex1 = wikt_examples[0]
                native_ex2 = wikt_examples[1] if len(wikt_examples) > 1 else None
                logger.debug("Wiktionary supplied example(s) for '%s'.", lemma)

    # ── Step 2: Resolve query word for definition source ──────────────────────
    query_lemma = lemma

    if def_language and def_language != language:
        try:
            from pipeline.translation import translate_word, TranslationUnavailableError
            translated = translate_word(lemma, language, def_language)
            if translated:
                logger.info("Translated '%s' -> '%s'", lemma, translated)
                query_lemma = translated
            else:
                target_language = language
        except TranslationUnavailableError:
            raise

    # ── Step 3: Fetch definition in target language ───────────────────────────
    definition:    Optional[str] = None
    part_of_speech: str          = ""

    # Try MW first for English definitions
    _actual_source = "dictionaryapi"
    if target_language == "en":
        mw_data = _fetch_from_mw(query_lemma)
        if mw_data:
            mw = _parse_mw_response(query_lemma, mw_data, snippets)
            if mw:
                definition     = mw.definition
                part_of_speech = mw.part_of_speech
                _actual_source = "merriam-webster"
                # Supplement native fields from MW if native fetch was empty
                if not native_syns:
                    native_syns = mw.synonyms
                if not native_ants:
                    native_ants = mw.antonyms

    # dictionaryapi.dev fallback for definition (or primary for non-English targets)
    if not definition:
        dict_data = _fetch_from_dictapi(query_lemma, language=target_language)
        if dict_data:
            dr = _parse_dictapi_response(query_lemma, dict_data, snippets)
            if dr:
                definition     = dr.definition
                part_of_speech = dr.part_of_speech
                _actual_source = "dictionaryapi"
                if not native_syns:
                    native_syns = dr.synonyms
                if not native_ants:
                    native_ants = dr.antonyms

    if not definition:
        logger.warning("No definition found for '%s' from either source.", lemma)
        return None

    # ── Step 4: Build result with 256-char caps ───────────────────────────────
    def _cap(text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        if len(text) <= 256:
            return text
        cut = text[:256]
        dot = cut.rfind(". ")
        return (cut[:dot + 1] if dot > 128 else cut.rstrip() + "...")

    # Supplement synonyms (and, for English only, antonyms) from
    # WordNet/OMW. English uses WordNet directly; 18 other languages use
    # Open Multilingual Wordnet via _OMW_LANGUAGE_CODES (see ADR-008) --
    # unlisted languages return empty immediately rather than attempting
    # a lookup that can't succeed.
    #
    # Critical: use `lemma` (original word), never `query_lemma`. When
    # def_language differs from language, query_lemma holds the TRANSLATED
    # word. Passing it here would write the wrong language's synonyms into
    # native_syns, which must always hold words in the original transcript
    # language.
    if not native_syns or not native_ants:
        wn_syns, wn_ants = _wordnet_synonyms_antonyms(lemma, language)
        if not native_syns:
            native_syns = wn_syns
        if not native_ants:
            native_ants = wn_ants

    # Truncate definition at first sentence boundary for cleaner cards
    def _clean_def(text):
        if not text:
            return ""
        for sep in (". ", "; ", " : "):
            idx = text.find(sep)
            if idx > 20:
                text = text[:idx + 1]
                break
        return _cap(text) or ""

    result = DefinitionResult(
        lemma=lemma,
        definition=_clean_def(definition),
        example_dict=_cap(native_ex1),
        example_dict2=_cap(native_ex2),
        example_transcript=_cap(
            _find_transcript_sentence(lemma, snippets) if snippets else None
        ),
        synonyms=[s for s in native_syns[:5] if s],
        antonyms=[s for s in native_ants[:5] if s],
        part_of_speech=part_of_speech,
        source=_actual_source,
    )

    _cache_set_key(cache_key, result)
    return result


def _fetch_definition_or_fallback_example(
    lemma: str,
    snippets: Optional[dict],
    language: str,
    def_language: Optional[str],
) -> tuple[Optional[DefinitionResult], Optional[str], list[str], list[str]]:
    """
    Fetch one lemma's definition, falling back to a bare Wiktionary example
    and OMW/WordNet synonyms/antonyms when no definition exists anywhere.
    One thread pool task per lemma in fetch_definitions() runs this instead
    of fetch_definition() directly.

    fetch_definition() already tries Wiktionary for examples, and OMW for
    synonyms/antonyms, when dictionaryapi.dev finds a definition but is
    missing those fields (a real but narrower case: some languages have
    partial definition coverage with sparser examples). This function
    covers the opposite, more common case for near-zero-coverage languages
    like French (see issue #1 and ADR-008): no definition anywhere, so
    fetch_definition() returns None before ever reaching its own Wiktionary
    or OMW lookups. Redoing those lookups here, once fetch_definition() has
    already given up, is what lets a French video's fallback cards carry a
    real dictionary example and real synonyms instead of empty fields.

    Returns (result, None, [], []) when a definition was found -- the
    example/synonyms/antonyms are already inside `result` in that case.
    Otherwise returns (None, example, synonyms, antonyms), where each of
    the three may still be empty/None if no source had anything.
    """
    result = fetch_definition(
        lemma, snippets, use_cache=False, language=language, def_language=def_language,
    )
    if result:
        return result, None, [], []

    example: Optional[str] = None
    if language != "en":
        wikt_data = _fetch_from_wiktionary(lemma, language)
        if wikt_data:
            examples = _parse_wiktionary_examples(wikt_data)
            if examples:
                example = examples[0]

    synonyms, antonyms = _wordnet_synonyms_antonyms(lemma, language)
    return None, example, synonyms, antonyms


def fetch_definitions(
    lemmas: list[str],
    snippets: Optional[dict] = None,
    max_workers: int = DEFINITION_FETCH_WORKERS,
    language: str = "en",
    def_language: Optional[str] = None,
) -> DefinitionBatchResult:
    """
    Fetch definitions for a list of lemmas, returned in first-appearance
    order regardless of which lookup happens to complete first.

    Cache hits are resolved first, sequentially (a local SQLite read is
    fast enough that there is nothing to gain from a thread pool there).
    The remaining lemmas, the ones that actually need a live API call, are
    then fetched concurrently through a thread pool bounded by max_workers,
    which is what keeps this from overwhelming a source with a burst of
    simultaneous requests -- see ARCHITECTURE.md's design patterns section
    for why a thread pool was chosen over asyncio/aiohttp for this.

    This function is designed to be called with the full NEW word list
    from deck.check_vocabulary(). Words in the SKIP or QUEUE lists
    should not be passed here.

    Args:
        lemmas:      Ordered list of lemmas (new words only).
        snippets:    Output of transcript.get_snippets(). Pass for transcript
                     example sentences. Pass None to skip.
        max_workers: Maximum concurrent live lookups. Default from
                     DEFINITION_FETCH_WORKERS env var (5). Set 1 in tests
                     that need strictly sequential, deterministic ordering.

    Returns:
        DefinitionBatchResult with found, not_found, from_cache, and
        not_found_examples (Wiktionary fallback examples for not_found
        lemmas, see DefinitionBatchResult's docstring).
    """
    batch = DefinitionBatchResult()

    # Deduplicate lemma list while preserving first-appearance order.
    # Prevents the same word producing multiple cards when it appears
    # in both the NEW list and the user-approved QUEUE list.
    seen: set = set()
    unique_lemmas: list = []
    for l in lemmas:
        key = l.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique_lemmas.append(l)
    if len(unique_lemmas) < len(lemmas):
        logger.info(
            "Removed %d duplicate lemmas before fetching.",
            len(lemmas) - len(unique_lemmas),
        )
    lemmas = unique_lemmas

    # Cache hits first, sequentially -- resolves most of the batch on a
    # cache-warm second run without ever touching the thread pool.
    to_fetch: list[str] = []
    for lemma in lemmas:
        cached = _cache_get(f"{lemma}::{def_language or language}")
        if cached:
            result = _cache_row_to_result(lemma, cached, snippets)
            batch.found.append(result)
            batch.from_cache.append(lemma)
        else:
            to_fetch.append(lemma)

    # Concurrent live fetch for whatever's left, preserving first-appearance
    # order in the output even though threads complete out of order. Each
    # task also tries a Wiktionary fallback example when the lemma has no
    # definition at all -- see _fetch_definition_or_fallback_example().
    if to_fetch:
        results: dict[
            str, tuple[Optional[DefinitionResult], Optional[str], list[str], list[str]]
        ] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_lemma = {
                executor.submit(
                    _fetch_definition_or_fallback_example, lemma, snippets,
                    language, def_language,
                ): lemma
                for lemma in to_fetch
            }
            for future in as_completed(future_to_lemma):
                lemma = future_to_lemma[future]
                try:
                    results[lemma] = future.result()
                except Exception:
                    logger.exception("Unexpected error fetching '%s'.", lemma)
                    results[lemma] = (None, None, [], [])

        for lemma in to_fetch:
            result, fallback_example, fallback_synonyms, fallback_antonyms = results[lemma]
            if result:
                batch.found.append(result)
            else:
                batch.not_found.append(lemma)
                if fallback_example:
                    batch.not_found_examples[lemma] = fallback_example
                if fallback_synonyms:
                    batch.not_found_synonyms[lemma] = fallback_synonyms
                if fallback_antonyms:
                    batch.not_found_antonyms[lemma] = fallback_antonyms

    logger.info(
        "Batch complete: %d found (%d cached) / %d not found (%d with a "
        "Wiktionary fallback example, %d with fallback synonyms)",
        len(batch.found),
        len(batch.from_cache),
        len(batch.not_found),
        len(batch.not_found_examples),
        len(batch.not_found_synonyms),
    )

    return batch