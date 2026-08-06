"""
wiktdata.py — offline native-language dictionary built from Wiktionary.

Solves the problem recorded in ARCHITECTURE.md 9.1 and issue #1:
dictionaryapi.dev has no usable non-English data, so every non-English
card shipped with "No definition found". Measured at 0 definitions across
1047 French words, and 0/9 for every non-English language tested.

Source is wiktextract output published per language by kaikki.org, built
from *that language's own* Wiktionary edition, so glosses are in the
target language rather than English. This matters: the Wiktionary REST
endpoint already used for example sentences (8.13) returns an English
gloss of the foreign word, which is why it never fixed definitions.

Why bulk data rather than the live API (ADR-008, issue #16): Wikimedia
rate-limits anonymous requests to roughly 8-10 before a 429, regardless
of pacing, and a single video needs 100-1000+ lookups. Downloading once
sidesteps the limit entirely rather than circumventing it, needs no
proxies, and makes lookups offline and instant. It also skips the two
hardest parts of parsing raw wikitext -- recursive template stripping and
detecting extractions that silently returned template syntax -- because
wiktextract already did that work upstream.

Measured against a real generated deck (958 French lemmas): definitions
95%, first example 92%, second example 80%, antonyms 20%. Synonyms come
back at 49%, which is *worse* than OMW's 76%, so this is layered with
OMW rather than replacing it -- see definition.py's call site.
"""

from __future__ import annotations

import gzip
import json
import logging
import sqlite3
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from pipeline.config import DICT_DIR

logger = logging.getLogger(__name__)


# ── Custom exceptions ─────────────────────────────────────────────────────────

class DictionaryError(Exception):
    """Base for every error raised by this module."""


class DictionaryDownloadError(DictionaryError):
    """The bulk extract could not be downloaded."""


class DictionaryBuildError(DictionaryError):
    """The index could not be built from a downloaded extract."""


# ── Source ────────────────────────────────────────────────────────────────────

_DOWNLOAD_URL = "https://kaikki.org/dictionary/downloads/{lang}/{lang}-extract.jsonl.gz"

# kaikki publishes most languages under that uniform per-language path, but
# English is its primary extraction and lives elsewhere under a different
# filename -- the uniform path 404s for "en". Confirmed by probing both.
_DOWNLOAD_URL_OVERRIDES: dict[str, str] = {
    "en": "https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz",
}


def download_url(language: str) -> str:
    """Return the bulk-extract URL for a language."""
    return _DOWNLOAD_URL_OVERRIDES.get(language, _DOWNLOAD_URL.format(lang=language))

# Schema version, bumped when the table layout changes so a stale index is
# rebuilt rather than silently queried with the wrong columns.
_SCHEMA_VERSION = 1


@dataclass
class DictionaryEntry:
    """One word's data, all fields in the target language."""
    word:        str
    part_of_speech: str
    definition:  str
    example1:    Optional[str]
    example2:    Optional[str]
    synonyms:    list[str]
    antonyms:    list[str]


# ── Paths and availability ────────────────────────────────────────────────────

def index_path(language: str) -> Path:
    """Return the on-disk location of one language's index."""
    return DICT_DIR / f"wiktionary_{language}.sqlite"


def is_available(language: str) -> bool:
    """True if a usable index exists for this language."""
    path = index_path(language)
    if not path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != _SCHEMA_VERSION:
                logger.warning(
                    "Dictionary index for '%s' is schema v%s, expected v%s. "
                    "Rebuild it with --build-dictionary %s.",
                    language, version, _SCHEMA_VERSION, language,
                )
                return False
            return conn.execute("SELECT 1 FROM entries LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


# ── Lookup ────────────────────────────────────────────────────────────────────

# One connection per thread. definition.py fetches through a thread pool, and
# a single sqlite3 connection is not safe to share across threads. Read-only
# so there is no writer contention to worry about, unlike the cache in
# definition.py which needed a busy timeout for exactly that reason.
_local = threading.local()


def _connection(language: str) -> Optional[sqlite3.Connection]:
    cache = getattr(_local, "conns", None)
    if cache is None:
        cache = _local.conns = {}
    if language in cache:
        return cache[language]
    path = index_path(language)
    if not path.exists():
        cache[language] = None
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        logger.warning("Could not open dictionary index for '%s': %s", language, exc)
        conn = None
    cache[language] = conn
    return conn


def _lookup_variants(word: str) -> list[str]:
    """
    Return the spellings to try for one word, most likely first.

    Wiktionary stores French elisions with a typographic apostrophe, so
    "aujourd'hui" typed with an ASCII quote misses an entry that is
    definitely there. That is a silent whole-word miss on one of the most
    common words in the language, so both forms are always tried.
    """
    lowered = word.lower().strip()
    variants = [lowered]
    for a, b in (("'", "’"), ("’", "'")):
        if a in lowered:
            swapped = lowered.replace(a, b)
            if swapped not in variants:
                variants.append(swapped)
    return variants


def lookup(word: str, language: str) -> Optional[DictionaryEntry]:
    """
    Return one word's dictionary entry, or None if absent.

    Never raises: a missing, unreadable or corrupt index degrades to None
    so the pipeline falls back to its other sources, matching how every
    other optional source in this project behaves.
    """
    conn = _connection(language)
    if conn is None:
        return None
    try:
        for variant in _lookup_variants(word):
            row = conn.execute(
                "SELECT * FROM entries WHERE word = ? LIMIT 1", (variant,)
            ).fetchone()
            if row:
                return DictionaryEntry(
                    word=row["word"],
                    part_of_speech=row["pos"] or "",
                    definition=row["definition"] or "",
                    example1=row["example1"] or None,
                    example2=row["example2"] or None,
                    synonyms=[s for s in (row["synonyms"] or "").split("|") if s],
                    antonyms=[a for a in (row["antonyms"] or "").split("|") if a],
                )
    except sqlite3.Error as exc:
        logger.warning("Dictionary lookup failed for '%s' (%s): %s", word, language, exc)
    return None


# ── Build ─────────────────────────────────────────────────────────────────────

def _extract_record(raw: dict, language: str) -> Optional[tuple]:
    """
    Flatten one wiktextract JSON record into an index row.

    Returns None for records that belong to another language or carry no
    gloss. The per-language extract documents *other* languages too (the
    French edition describes German, Occitan and so on in French), so the
    language filter is what keeps the index to real target-language words:
    it discards roughly 70% of the file.
    """
    if raw.get("lang_code") != language:
        return None
    word = raw.get("word")
    if not word:
        return None

    definition = ""
    examples: list[str] = []
    for sense in raw.get("senses") or []:
        glosses = sense.get("glosses") or []
        if glosses:
            definition = glosses[0]
            examples = [
                e.get("text", "")
                for e in (sense.get("examples") or [])
                if e.get("text")
            ]
            break
    if not definition:
        return None

    def _joined(key: str) -> str:
        return "|".join(
            item.get("word", "")
            for item in (raw.get(key) or [])
            if item.get("word")
        )[:256]

    return (
        word.lower(),
        (raw.get("pos") or "")[:32],
        definition[:256],
        (examples[0] if len(examples) > 0 else "")[:256],
        (examples[1] if len(examples) > 1 else "")[:256],
        _joined("synonyms"),
        _joined("antonyms"),
    )


def build_index(
    language: str,
    archive: Optional[Path] = None,
    keep_archive: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Download a language's bulk extract and build its lookup index.

    Args:
        language:     Target language code, e.g. "fr".
        archive:      Use this already-downloaded .jsonl.gz instead of
                      fetching one. Mainly for tests and for re-running a
                      build without re-downloading hundreds of megabytes.
        keep_archive: Leave the downloaded archive in place afterwards.
                      Off by default since it is large and only needed
                      once.
        progress:     Called with human-readable status lines, so the CLI
                      can report progress on a multi-minute operation
                      without this module importing anything from it.

    Returns:
        Number of entries indexed.

    Raises:
        DictionaryDownloadError: the extract could not be fetched.
        DictionaryBuildError:    the extract could not be parsed or written.
    """
    def _say(message: str) -> None:
        logger.info(message)
        if progress:
            progress(message)

    DICT_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = False
    if archive is None:
        archive = DICT_DIR / f"{language}-extract.jsonl.gz"
        url = download_url(language)
        _say(f"Downloading {url} (this is a large one-time download)...")
        try:
            urllib.request.urlretrieve(url, archive)
            downloaded = True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise DictionaryDownloadError(
                f"Could not download the {language} dictionary.\n"
                f"  {exc}\n"
                f"  Check the language has an extract at https://kaikki.org "
                f"and that you are online."
            ) from exc

    target = index_path(language)
    tmp = target.with_suffix(".building")
    tmp.unlink(missing_ok=True)

    _say("Building index (streamed, the archive is never fully unpacked)...")
    indexed = 0
    try:
        conn = sqlite3.connect(tmp)
        # Durability is pointless here: a failed build is thrown away and
        # rebuilt from the archive rather than repaired.
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute(
            "CREATE TABLE entries ("
            "  word TEXT, pos TEXT, definition TEXT,"
            "  example1 TEXT, example2 TEXT, synonyms TEXT, antonyms TEXT)"
        )
        batch: list[tuple] = []
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    # A truncated or malformed line is not worth failing a
                    # multi-million-line build over.
                    continue
                record = _extract_record(raw, language)
                if record is None:
                    continue
                batch.append(record)
                indexed += 1
                if len(batch) >= 20000:
                    conn.executemany(
                        "INSERT INTO entries VALUES (?,?,?,?,?,?,?)", batch
                    )
                    batch = []
                    _say(f"  indexed {indexed:,} entries (line {line_number:,})...")
        if batch:
            conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?)", batch)
        conn.execute("CREATE INDEX idx_word ON entries(word)")
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()
        conn.close()
    except (OSError, sqlite3.Error, gzip.BadGzipFile) as exc:
        tmp.unlink(missing_ok=True)
        raise DictionaryBuildError(
            f"Could not build the {language} dictionary index.\n  {exc}"
        ) from exc

    if indexed == 0:
        tmp.unlink(missing_ok=True)
        raise DictionaryBuildError(
            f"No '{language}' entries found in the extract. "
            f"Check that '{language}' is the right language code for it."
        )

    # Swap in only once complete, so an interrupted build never leaves a
    # half-populated index that is_available() would report as usable.
    tmp.replace(target)
    if downloaded and not keep_archive:
        archive.unlink(missing_ok=True)

    _say(f"Indexed {indexed:,} {language} entries into {target}.")
    return indexed
