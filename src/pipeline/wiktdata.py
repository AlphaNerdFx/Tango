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
import unicodedata
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


# Languages where building an index is a waste rather than a win. English is
# the only one so far, and it was measured rather than assumed: a live run
# produced 273 of 274 definitions from Merriam-Webster and consulted the
# index zero times. The single MW miss was a brand name the index does not
# have either. So it costs a 475 MB download and ~194 MB on disk to change
# nothing. Its first-sense definitions are frequently archaic too, because
# English Wiktionary orders senses historically rather than by frequency:
# "may" -> "To be strong; to have power (over)".
# English was here until 27 August 2026, on the grounds that a measured run
# used the index zero times so it cost a download to change nothing. That was
# true when measured on 7 August and stopped being true on the 14th, when IPA
# and Pronunciation became card fields and `_resolve_pronunciation` began
# consulting the index first for every language. The other half of the
# reasoning survives and is not recorded here as discouragement because it is
# not one: Merriam-Webster's definitions are better curated, which is why
# `fetch_definition` still tries MW first for English. The index is the floor
# under it, not a replacement. ADR-011.
_DISCOURAGED: dict[str, str] = {}


def is_discouraged(language: str) -> Optional[str]:
    """
    Return why building this language's index is not worth it, or None.

    Advisory only -- callers may still build. Exists so nobody repeats an
    experiment already run and recorded.
    """
    return _DISCOURAGED.get(language)

# Schema version, bumped when the table layout changes so a stale index is
# rebuilt rather than silently queried with the wrong columns.
#
# v2 added ipa, audio_url and form_of. Two changes were deliberately landed
# in one bump because the cost of a bump is a full re-download per language
# -- 288 MB for de, 682 MB for fr, 278 MB for ru -- and asking for that twice
# to add columns that were both already sitting in the same records would be
# hard to justify. See ADR-009 phase 1 and ARCHITECTURE.md 8.30.
_SCHEMA_VERSION = 2


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
    # v2. ipa is a transcription like "[haˈloː]"; audio_url points at a real
    # human recording on Wikimedia Commons. Measured on 20000 German entries:
    # 93% carry IPA and 91% an audio URL.
    ipa:         Optional[str] = None
    audio_url:   Optional[str] = None
    # Set when this row's gloss is an inflection pointer rather than a
    # definition ("Dativ Plural des Substantivs Krieg"), and names the word
    # it points at. Lets a caller follow the pointer instead of showing it.
    form_of:     Optional[str] = None


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
                    "Rebuild it with 'tango build-dictionary %s'.",
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


# spaCy's UPOS tags mapped to the `pos` values wiktextract writes. Only the
# four tags nlp.ACCEPTED_POS keeps are listed, because those are the only
# ones that ever reach a lookup. wiktextract normalises pos across editions,
# verified against all three built indexes: de, fr and ru all use exactly
# these strings, plus "name" for proper nouns.
_UPOS_TO_POS: dict[str, str] = {
    "NOUN": "noun",
    "VERB": "verb",
    "ADJ": "adj",
    "ADV": "adv",
}


def pos_for_upos(upos: Optional[str]) -> Optional[str]:
    """
    Return the index `pos` value a spaCy UPOS tag selects, or None.

    None means the tag cannot narrow a lookup — either no tag was supplied
    or it is one this index does not distinguish. Public because the
    definition cache keys on it: a POS that cannot change which row is
    selected must not fragment the cache into rows that differ in nothing.
    """
    return _UPOS_TO_POS.get((upos or "").upper())


def _select_row(rows: list, pos: Optional[str]):
    """
    Choose which of a word's index rows to build the entry from.

    The index holds one row per (word, part of speech), so an ambiguous word
    has several and the first is frequently the wrong one — Russian "вид"
    led with a river in Germany, "близкий" with an island in the Kara Sea,
    and French "super" with "Supercarburant" for a video using it to mean
    "very".

    Two rules, in order:

    1. Prefer a row whose part of speech matches the one spaCy assigned the
       word *in its own sentence*. Measured across three real videos (fr,
       de, ru) this changes 60 of 1209 picks, roughly 39 of them for the
       better against 14 for the worse — the reverse of the word-overlap
       heuristic tried before it, which went 1 better to 14 worse (8.28).

    2. Never a `name` row while any other exists, with or without a POS to
       go on. Proper nouns are filtered upstream by nlp._is_valid_token, so
       a `name` row is never the sense that put the word on a card.

    Falls back to the first row when neither rule finds anything, which is
    what this function did before it existed. A word whose POS has no row
    keeps its current card rather than losing one.
    """
    wanted = pos_for_upos(pos)
    if wanted:
        for row in rows:
            if row["pos"] == wanted:
                return row
    for row in rows:
        if row["pos"] != "name":
            return row
    return rows[0]


# Combining acute and grave. Russian Wiktionary marks stress with these, and
# the index stores headwords without them, so a pointer to "нача́ло" cannot
# find "начало" until the mark comes off.
#
# Stripped only from Cyrillic letters, and that restriction is the whole
# point. On Cyrillic these are a stress notation and carry no meaning; on
# Latin the identical codepoints spell different words. Stripping them
# everywhere turned French "à" into "a" and "élève" into "eleve", which is
# not a near miss but a pointer to a different entry.
_STRESS_MARKS = "́̀"


def _strip_cyrillic_stress(word: str) -> str:
    """Remove combining stress marks that sit on Cyrillic letters only."""
    out: list[str] = []
    for char in unicodedata.normalize("NFD", word):
        if char in _STRESS_MARKS and out and "CYRILLIC" in unicodedata.name(out[-1], ""):
            continue
        out.append(char)
    return unicodedata.normalize("NFC", "".join(out))


def _form_of_spellings(target: str) -> list[str]:
    """
    Spellings to try for an inflection pointer's target, most literal first.

    Args:
        target: The `form_of` value as the index stores it.

    Returns:
        Lower-cased candidates, without duplicates, in the order to try.

    The literal value is always first, so this can only add hits, never
    move one. Two transformations follow it, both measured against the real
    builds and both Russian in practice:

    - Everything from "#" is dropped. Russian Wiktionary disambiguates
      homographs in the pointer itself, so the target arrives as
      "толк#(существительное I)" or "кома#I", which matches no headword.
      595 of the Russian index's 11836 pointers carry one, and the German
      and French builds carry none across 2.3 million pointers.
    - Stress marks are stripped from Cyrillic, which recovers "нача́ло" ->
      "начало". Latin is left alone: the same codepoints spell French "à"
      and "élève", and stripping them there points at a different word.

    Both are safe for languages that do not need them, since neither "#" nor
    a Cyrillic stress mark appears in a German or French pointer.
    """
    bare = target.split("#")[0].strip()
    unstressed = _strip_cyrillic_stress(bare)

    spellings: list[str] = []
    for candidate in (target, bare, unstressed):
        lowered = candidate.strip().lower()
        if lowered and lowered not in spellings:
            spellings.append(lowered)
    return spellings


def _follow_form_of(conn: sqlite3.Connection, row, pos: Optional[str]):
    """
    Resolve an inflection pointer to the entry it points at.

    Wiktionary indexes every inflected form as its own entry -- 81.4% of the
    German index, measured on the real build -- so a selected row's whole
    gloss can be "1. Person Singular Indikativ Präsens Aktiv des Verbs
    glauben". That is a pointer, not a definition, and it is useless on a
    card.

    Since v2 stores the word it points at, the pointer can be followed
    rather than printed: "glaube" resolves to "glauben" and the card gets
    "geistig tätig sein, im Kopf formulieren".

    One hop only. A pointer whose target is itself a pointer keeps the
    original row rather than chasing a chain that may not terminate, and a
    target that is missing does the same -- a mediocre definition beats
    losing the card, which is the rule everywhere else in this module.
    """
    target = (row["form_of"] or "").strip()
    if not target:
        return row
    for spelling in _form_of_spellings(target):
        try:
            candidates = conn.execute(
                "SELECT * FROM entries WHERE word = ?", (spelling,)
            ).fetchall()
        except sqlite3.Error:
            return row
        real = [r for r in candidates if not (r["form_of"] or "").strip()]
        if real:
            return _select_row(real, pos)
    return row


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


def lookup(word: str, language: str, pos: Optional[str] = None) -> Optional[DictionaryEntry]:
    """
    Return one word's dictionary entry, or None if absent.

    Args:
        word:     The lemma to look up.
        language: Which language's index to read.
        pos:      Optional spaCy UPOS tag for this word as it was used in
                  the transcript, from nlp.process_transcript()'s
                  parts_of_speech output. Used to pick between the word's
                  senses — see _select_row. Omitting it keeps the previous
                  behaviour apart from the proper-noun rule.

    Never raises: a missing, unreadable or corrupt index degrades to None
    so the pipeline falls back to its other sources, matching how every
    other optional source in this project behaves.
    """
    conn = _connection(language)
    if conn is None:
        return None
    try:
        for variant in _lookup_variants(word):
            # Every row, not the first: choosing between the senses is the
            # point. Bounded in practice -- the most rows any word has is
            # 14, measured across the fr, de and ru indexes.
            rows = conn.execute(
                "SELECT * FROM entries WHERE word = ?", (variant,)
            ).fetchall()
            if rows:
                row = _select_row(rows, pos)
                # Meaning comes from whatever the row ultimately points at;
                # pronunciation stays with the word the learner actually saw.
                # "glaube" is pronounced [ˈɡlaʊ̯bə] even when its definition
                # is borrowed from "glauben".
                sense = _follow_form_of(conn, row, pos)
                return DictionaryEntry(
                    word=row["word"],
                    part_of_speech=sense["pos"] or "",
                    definition=sense["definition"] or "",
                    example1=sense["example1"] or None,
                    example2=sense["example2"] or None,
                    synonyms=[s for s in (sense["synonyms"] or "").split("|") if s],
                    antonyms=[a for a in (sense["antonyms"] or "").split("|") if a],
                    ipa=row["ipa"] or None,
                    audio_url=row["audio_url"] or None,
                    form_of=row["form_of"] or None,
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

    A real definition is preferred over an inflection pointer within the
    same record. Wiktionary indexes every inflected form as its own entry,
    so taking senses[0] unconditionally could store "Dativ Plural des
    Substantivs Krieg" while a real gloss sat two senses below it. When
    every sense is a pointer, the first is kept and the word it points at
    is recorded, so a caller can follow it rather than print it.
    """
    if raw.get("lang_code") != language:
        return None
    word = raw.get("word")
    if not word:
        return None

    def _sense_form_of(sense: dict) -> Optional[str]:
        """The word this sense is an inflected form of, or None."""
        targets = sense.get("form_of") or []
        target = next(
            (t.get("word") for t in targets if isinstance(t, dict) and t.get("word")),
            None,
        )
        if target:
            return target
        # wiktextract tags the sense even where it does not resolve a target.
        return "" if "form-of" in (sense.get("tags") or []) else None

    def _read(sense: dict) -> tuple:
        return (
            (sense.get("glosses") or [""])[0],
            [e.get("text", "") for e in (sense.get("examples") or []) if e.get("text")],
        )

    definition = ""
    examples: list[str] = []
    form_of = ""
    fallback: Optional[tuple] = None

    for sense in raw.get("senses") or []:
        if not (sense.get("glosses") or []):
            continue
        pointer = _sense_form_of(sense)
        if pointer is None:
            definition, examples = _read(sense)
            form_of = ""
            break
        if fallback is None:
            gloss, exs = _read(sense)
            fallback = (gloss, exs, pointer)
    else:
        if fallback is not None:
            definition, examples, form_of = fallback

    if not definition:
        return None

    def _joined(key: str) -> str:
        return "|".join(
            item.get("word", "")
            for item in (raw.get(key) or [])
            if item.get("word")
        )[:256]

    # Pronunciation. mp3_url is preferred over ogg_url because it is a direct
    # upload.wikimedia.org file while ogg_url is a Special:FilePath redirect,
    # and because Anki plays mp3 without relying on the user's codec setup.
    sounds = raw.get("sounds") or []
    ipa = next((s["ipa"] for s in sounds if s.get("ipa")), "")
    audio = next(
        (s.get("mp3_url") or s.get("ogg_url")
         for s in sounds if s.get("mp3_url") or s.get("ogg_url")),
        "",
    )

    return (
        word.lower(),
        (raw.get("pos") or "")[:32],
        definition[:256],
        (examples[0] if len(examples) > 0 else "")[:256],
        (examples[1] if len(examples) > 1 else "")[:256],
        _joined("synonyms"),
        _joined("antonyms"),
        ipa[:64],
        audio[:512],
        form_of[:128],
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
            # A partial download is worthless and can be hundreds of MB.
            # Only ever removes an archive this function fetched, never one
            # the caller supplied.
            archive.unlink(missing_ok=True)
            raise DictionaryDownloadError(
                f"Could not download the {language} dictionary.\n"
                f"  {exc}\n"
                f"  Check the language has an extract at https://kaikki.org "
                f"and that you are online."
            ) from exc
        except BaseException:
            # Interrupting a multi-minute download is normal, and leaving
            # a truncated archive behind after it is not acceptable.
            archive.unlink(missing_ok=True)
            raise

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
            "  example1 TEXT, example2 TEXT, synonyms TEXT, antonyms TEXT,"
            "  ipa TEXT, audio_url TEXT, form_of TEXT)"
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
                        "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)", batch
                    )
                    batch = []
                    _say(f"  indexed {indexed:,} entries (line {line_number:,})...")
        if batch:
            conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
        conn.execute("CREATE INDEX idx_word ON entries(word)")
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()
        conn.close()
    except (OSError, sqlite3.Error, gzip.BadGzipFile) as exc:
        tmp.unlink(missing_ok=True)
        if downloaded:
            archive.unlink(missing_ok=True)
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
