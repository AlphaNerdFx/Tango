"""
Offline antonym index, built from ConceptNet's assertions dump.

Antonyms are the weakest field on a Tango card: 19.7% on a real French deck
against 98.6% for definitions. The offline Wiktionary index is not holding
anything back (0 of 40000 kaikki French entries carry sense-level antonyms),
so the ceiling belongs to the source, and the source needed widening.

ConceptNet is not a different kind of data. It is Wiktionary's own antonyms,
re-extracted by a different tool: kaikki runs wiktextract, ConceptNet ran
wikiparsec. Both read the same edition of Wiktionary for a given language,
and they disagree about which words have an antonym often enough that the
union is much larger than either. How much larger depends on the language:

    language   words the index has        words ConceptNet has   gain
    fr         15 045                     12 376                 +15.1 points
    de         30 616                      3 547                  +4.2 points
    ru         23 747                      1 857                  +1.0 points

French is where wiktextract's antonym capture is thinnest (0.78% of a
1.93 million word index, against 3.25% for German), which is why French had
the most to gain. ADR-010 has the full evaluation and
`scripts/measure_antonym_sources.py` reproduces every number.

One file covers every language, unlike the per-language Wiktionary indexes:
4.3 MB for 22 languages, against 131 to 404 MB for one language of Wiktionary.

**Constraint 3.3 lives in the build, not the caller.** Antonyms describe the
word shown, so they must be in the transcript language. Only pairs whose two
ends are in the same language are ever stored, which means a cross-language
edge cannot reach a card even if a caller asks carelessly. The dump does
contain such edges; they are dropped here.

Absence is normal. Nothing in the pipeline requires this index, and every
entry point degrades to today's behaviour when it is missing.
"""

from __future__ import annotations

from pipeline import TangoError

import gzip
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests

from pipeline.config import DICT_DIR

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────────

class AntonymError(TangoError):
    """Base class for antonym index failures."""


class AntonymDownloadError(AntonymError):
    """The ConceptNet dump could not be fetched."""


class AntonymBuildError(AntonymError):
    """The dump was fetched but the index could not be written."""


# ── Configuration ─────────────────────────────────────────────────────────────

DUMP_URL = (
    "https://s3.amazonaws.com/conceptnet/downloads/2019/edges/"
    "conceptnet-assertions-5.7.0.csv.gz"
)

# Bumped whenever the schema changes, so is_available() can refuse an index
# built by an older version rather than reading columns that moved.
_SCHEMA_VERSION = 1

# The relation to keep. ConceptNet also has /r/DistinctFrom, which asserts
# something weaker ("a dog is distinct from a cat") and is not an antonym.
# Measuring both is what `scripts/measure_antonym_sources.py` is for; only
# this one reaches a card.
_RELATION = "/r/Antonym"
_RELATION_BYTES = b"\t/r/Antonym\t"

# ConceptNet tags a term with one letter; spaCy gives the card a UPOS tag.
# Used to prefer the sense the word was actually used in, and never to
# exclude a row: an antonym with no part of speech is still an antonym.
_POS_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "n": ("NOUN", "PROPN"),
    "v": ("VERB", "AUX"),
    "a": ("ADJ",),
    "s": ("ADJ",),   # satellite adjective, WordNet's term, adjectives here
    "r": ("ADV",),
}

# How many antonyms a card can show. cards.py renders them as pills and the
# other list fields are capped at 5, so this matches rather than invents.
MAX_ANTONYMS = 5


# ── Paths and availability ────────────────────────────────────────────────────

def index_path() -> Path:
    """Return the on-disk location of the antonym index."""
    return DICT_DIR / "conceptnet_antonyms.sqlite"


def is_available() -> bool:
    """
    True if a usable antonym index exists.

    Never raises. A missing, empty, corrupt or outdated index is simply not
    available, and the pipeline runs without it.
    """
    path = index_path()
    if not path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != _SCHEMA_VERSION:
                logger.warning(
                    "Antonym index is schema v%s, expected v%s. "
                    "Rebuild it with 'tango build-antonyms'.",
                    version, _SCHEMA_VERSION,
                )
                return False
            return conn.execute("SELECT 1 FROM antonyms LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


# ── Term shapes ───────────────────────────────────────────────────────────────

def _key(word: str) -> str:
    """Return `word` in the shape ConceptNet keys its terms by."""
    return word.strip().lower().replace(" ", "_")


def _display(term: str) -> str:
    """
    Return a ConceptNet term in the shape a card should show.

    ConceptNet joins every token with an underscore, including across an
    elision, so French comes back as `s’_en_retourner`. Splitting on the
    underscore alone would print "s’ en retourner" with a space that does
    not belong in the language. Where the apostrophe survived in the data,
    the space after it is removed again.
    """
    text = term.replace("_", " ")
    for apostrophe in ("’", "'"):
        text = text.replace(f"{apostrophe} ", apostrophe)
    return text


# ── Lookup ────────────────────────────────────────────────────────────────────

# One connection per thread. definition.py fetches through a thread pool and
# a single sqlite3 connection is not safe to share across threads, the same
# reason wiktdata.py keeps one.
_local = threading.local()


def _connection() -> Optional[sqlite3.Connection]:
    """Return this thread's read-only connection, or None if unavailable."""
    conn = getattr(_local, "antonym_conn", None)
    if conn is not None:
        return conn
    if not is_available():
        return None
    try:
        conn = sqlite3.connect(f"file:{index_path()}?mode=ro", uri=True, check_same_thread=False)
    except sqlite3.Error:
        return None
    _local.antonym_conn = conn
    return conn


def close() -> None:
    """Close this thread's connection, if it has one. Tests use this."""
    conn = getattr(_local, "antonym_conn", None)
    if conn is not None:
        conn.close()
        _local.antonym_conn = None


def lookup(word: str, language: str, pos: Optional[str] = None) -> list[str]:
    """
    Return antonyms for one word, in that word's own language.

    Args:
        word:     The lemma as it appears on the card.
        language: The transcript language. Both ends of every stored pair
                  are in this language, so what comes back describes the
                  word shown, per constraint 3.3.
        pos:      Optional spaCy UPOS tag for the word in its own sentence.
                  Used to prefer one sense, never to exclude a row.

    Returns:
        Up to MAX_ANTONYMS antonyms, or an empty list. Never None, so a
        caller can use it directly where a list is expected.

    Never raises: a missing, unreadable or corrupt index degrades to an
    empty list, which is exactly what every card shows today.
    """
    conn = _connection()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT pos, antonyms FROM antonyms WHERE lang = ? AND word = ?",
            (language, _key(word)),
        ).fetchall()
    except sqlite3.Error:
        return []
    if not rows:
        return []

    # Prefer the sense the word was used in. Falling back to every row is
    # deliberate: unlike a definition, where showing the wrong sense is the
    # bug 8.29 fixed, an antonym of another sense of the same spelling is
    # still a word worth meeting, and the alternative is an empty field.
    if pos:
        wanted = [row for row in rows if row[0] and pos in _POS_EQUIVALENTS.get(row[0], ())]
        if wanted:
            rows = wanted

    seen: list[str] = []
    for _, joined in rows:
        for term in joined.split("|"):
            if term and term not in seen:
                seen.append(term)
    return seen[:MAX_ANTONYMS]


# ── Building ──────────────────────────────────────────────────────────────────

def _parse(line: str, languages: set[str]) -> Optional[tuple[str, str, str, str, str]]:
    """
    Return (language, left, left_pos, right, right_pos) for one antonym edge.

    Args:
        line:      One tab-separated row of the assertions dump.
        languages: Language codes worth keeping.

    Returns:
        None when the row is not a same-language antonym edge between two
        distinct terms in a wanted language.

    The same-language test is constraint 3.3 in one line. The dump holds
    cross-language antonym edges, and storing one would put a French word on
    a German card, which is the failure 3.3 has already been violated by
    three times.
    """
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 4 or parts[1] != _RELATION:
        return None

    start, end = parts[2].split("/"), parts[3].split("/")
    if len(start) < 4 or len(end) < 4:
        return None
    if start[2] != end[2] or start[2] not in languages:
        return None
    if start[3] == end[3]:
        return None

    return (
        start[2],
        start[3], start[4] if len(start) > 4 else "",
        end[3], end[4] if len(end) > 4 else "",
    )


def _stream(source: Optional[Path]) -> Iterable[bytes]:
    """
    Yield raw lines of the assertions dump, from disk or over the network.

    Args:
        source: A local `.csv.gz` to read instead of downloading. Used by
                tests and by anyone who already has the dump.

    Raises:
        AntonymDownloadError: The dump could not be fetched or opened.

    The network path is streamed rather than saved. The dump is 498 MB
    compressed and roughly 9 GB expanded, this project has a release goal
    about install size, and what is actually wanted out of it is 3 MB.
    """
    if source is not None:
        try:
            with gzip.open(source, "rb") as handle:
                yield from handle
            return
        except OSError as exc:
            raise AntonymDownloadError(f"Could not read {source}: {exc}") from exc

    try:
        with requests.get(DUMP_URL, stream=True, timeout=60) as response:
            response.raise_for_status()
            # The body is already a gzip file. Stopping requests from
            # transparently decoding a Content-Encoding it was not sent
            # keeps those two facts from colliding.
            response.raw.decode_content = False
            with gzip.GzipFile(fileobj=response.raw) as handle:
                yield from handle
    except (requests.RequestException, OSError) as exc:
        raise AntonymDownloadError(
            f"Could not download the ConceptNet dump.\n"
            f"  {exc}\n"
            f"  It is 498 MB and streamed, so a dropped connection means "
            f"starting again. Check you are online and retry."
        ) from exc


def build_index(
    source: Optional[Path] = None,
    languages: Optional[Iterable[str]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Build the antonym index, replacing any existing one.

    Args:
        source:    A local `.csv.gz` dump to read instead of downloading.
        languages: Language codes to keep. Defaults to every language Tango
                   has a spaCy model for, since a language that cannot be
                   tokenized has no path to a card.
        progress:  Called with human-readable status lines, so the CLI can
                   report progress on a multi-minute operation.

    Returns:
        Number of (language, word, part of speech) rows written.

    Raises:
        AntonymDownloadError: The dump could not be fetched.
        AntonymBuildError:    The dump could not be parsed or written.
    """
    from pipeline.language import SPACY_MODELS

    def _say(message: str) -> None:
        logger.info(message)
        if progress:
            progress(message)

    wanted = set(languages) if languages is not None else set(SPACY_MODELS)
    DICT_DIR.mkdir(parents=True, exist_ok=True)

    target = index_path()
    tmp = target.with_suffix(".building")
    tmp.unlink(missing_ok=True)

    if source is None:
        _say("Streaming the ConceptNet dump (498 MB, filtered in flight)...")
    else:
        _say(f"Reading {source}...")

    pairs: dict[tuple[str, str, str], set[str]] = {}
    scanned = 0
    try:
        for raw in _stream(source):
            scanned += 1
            # Checked on bytes before decoding. 34 million rows reach this
            # line and roughly 66 thousand pass it, so decoding first would
            # spend the whole build on rows that are thrown away.
            if _RELATION_BYTES not in raw:
                continue
            parsed = _parse(raw.decode("utf-8", errors="replace"), wanted)
            if parsed is None:
                continue
            language, left, left_pos, right, right_pos = parsed
            pairs.setdefault((language, left, left_pos), set()).add(_display(right))
            pairs.setdefault((language, right, right_pos), set()).add(_display(left))
            if scanned % 5_000_000 == 0:
                _say(f"  {scanned // 1_000_000}M rows scanned, {len(pairs)} words so far")
    except AntonymError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
        raise AntonymBuildError(f"Could not parse the ConceptNet dump: {exc}") from exc

    if not pairs:
        raise AntonymBuildError(
            "No antonym edges found. The dump format may have changed, or "
            "the source file is not a ConceptNet assertions dump."
        )

    _say(f"Writing {len(pairs)} words across {len({k[0] for k in pairs})} languages...")
    try:
        conn = sqlite3.connect(tmp)
        # A failed build is discarded and rebuilt, never repaired, so
        # durability buys nothing and costs minutes.
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute(
            "CREATE TABLE antonyms ("
            "  lang TEXT NOT NULL, word TEXT NOT NULL,"
            "  pos TEXT, antonyms TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO antonyms (lang, word, pos, antonyms) VALUES (?, ?, ?, ?)",
            (
                (language, word, pos, "|".join(sorted(terms)))
                for (language, word, pos), terms in pairs.items()
            ),
        )
        conn.execute("CREATE INDEX idx_antonyms_word ON antonyms (lang, word)")
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()
        conn.close()
    except sqlite3.Error as exc:
        tmp.unlink(missing_ok=True)
        raise AntonymBuildError(f"Could not write the antonym index: {exc}") from exc

    close()
    tmp.replace(target)
    size_mb = target.stat().st_size / 1e6
    _say(f"Antonym index built: {target} ({size_mb:.1f} MB)")
    return len(pairs)
