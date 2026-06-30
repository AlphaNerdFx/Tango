"""
Responsible for:
  1. AnkiConnect communication  — list decks, fetch card fronts, health check
  2. Confidence interval check  — classify each lemma as SKIP / QUEUE / NEW
  3. CLI prompt                 — interactive resolution of queued words
  4. SQLite backlog             — persist state when Anki is unavailable
  5. Review file                — write queued words to review.json for later

Confidence bands (configurable in config.py):
  score > 90   → SKIP   (word already in deck)
  score 60–90  → QUEUE  (possible duplicate — needs user decision)
  score < 60   → NEW    (fetch definition and create card)

Short word rule (< 4 chars):
  Exact match only. WRatio is unreliable on short tokens due to
  partial ratio inflation (e.g. "go" vs "going" scores 90 with WRatio).

Dependencies:
    rapidfuzz
    requests
    sqlite3 (stdlib)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import requests
from rapidfuzz import fuzz, process as fuzz_process

logger = logging.getLogger(__name__)

# ── Constants (override in config.py) ────────────────────────────────────────

from pipeline.config import (
    ANKI_HOST, ANKI_VERSION, ANKI_TIMEOUT,
    CONFIDENCE_HIGH, CONFIDENCE_LOW, SHORT_WORD_THRESHOLD,
    REVIEW_FILE, DB_PATH,
)








# ── Result types ──────────────────────────────────────────────────────────────

class Decision(str, Enum):
    SKIP  = "SKIP"   # word exists in deck — do not create card
    QUEUE = "QUEUE"  # possible duplicate — await user decision
    NEW   = "NEW"    # no match — create card


@dataclass(frozen=True)
class MatchResult:
    """
    Result of a confidence interval check for a single lemma.

    Attributes:
        lemma:        The incoming word from nlp.py.
        decision:     SKIP / QUEUE / NEW.
        matched_front: The existing card front it was compared against (if any).
        score:        WRatio score 0–100. None if no deck fronts exist or exact-only path.
    """
    lemma:         str
    decision:      Decision
    matched_front: Optional[str] = None
    score:         Optional[float] = None


@dataclass
class DeckCheckResult:
    """
    Aggregate result of checking all lemmas from one pipeline run.

    Attributes:
        skip:   Words confirmed already in deck.
        queue:  Words needing user review.
        new:    Words confirmed not in deck — proceed to definition fetch.
        anki_available: False if AnkiConnect was unreachable during this run.
    """
    skip:           list[MatchResult] = field(default_factory=list)
    queue:          list[MatchResult] = field(default_factory=list)
    new:            list[MatchResult] = field(default_factory=list)
    anki_available: bool = True


# ── Custom exceptions ─────────────────────────────────────────────────────────

class AnkiConnectError(Exception):
    """AnkiConnect returned an error response."""


class AnkiNotRunningError(Exception):
    """
    AnkiConnect is not reachable.
    Words will be queued to SQLite backlog until Anki is available.
    """


# ── AnkiConnect client ────────────────────────────────────────────────────────

def _anki_request(action: str, **params) -> object:
    """
    Send a request to AnkiConnect and return the result field.

    Raises:
        AnkiNotRunningError: If the connection is refused or times out.
        AnkiConnectError:    If AnkiConnect returns an error string.
    """
    payload = {"action": action, "version": ANKI_VERSION, "params": params}
    try:
        response = requests.post(ANKI_HOST, json=payload, timeout=ANKI_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise AnkiNotRunningError(
            f"AnkiConnect not reachable at {ANKI_HOST}. "
            "Ensure Anki is running with the AnkiConnect add-on installed."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise AnkiNotRunningError(
            f"AnkiConnect timed out after {ANKI_TIMEOUT}s."
        ) from exc

    data = response.json()
    if data.get("error"):
        raise AnkiConnectError(f"AnkiConnect error: {data['error']}")

    return data["result"]


def is_anki_running() -> bool:
    """Return True if AnkiConnect is reachable, False otherwise."""
    try:
        _anki_request("version")
        return True
    except (AnkiNotRunningError, AnkiConnectError):
        return False


def get_deck_names() -> list[str]:
    """
    Return all deck names from the running Anki instance.

    Raises:
        AnkiNotRunningError: Anki not running.
        AnkiConnectError:    AnkiConnect returned an error.
    """
    result = _anki_request("deckNames")
    return sorted(result)


def get_card_fronts(deck_name: str) -> list[str]:
    """
    Fetch the Front field of every note in the given deck.

    Returns an empty list if the deck has no cards.

    Raises:
        AnkiNotRunningError: Anki not running.
        AnkiConnectError:    AnkiConnect returned an error.
    """
    note_ids = _anki_request("findNotes", query=f'deck:"{deck_name}"')
    if not note_ids:
        logger.info("Deck '%s' has no cards.", deck_name)
        return []

    notes_info = _anki_request("notesInfo", notes=note_ids)

    fronts = []
    for note in notes_info:
        fields = note.get("fields", {})
        front_field = fields.get("Front", {})
        front_value = front_field.get("value", "").strip().lower()
        if front_value:
            fronts.append(front_value)

    logger.info("Fetched %d card fronts from deck '%s'.", len(fronts), deck_name)
    return fronts


# ── Confidence interval logic ─────────────────────────────────────────────────

def _is_sentence_structured_deck(fronts: list[str], threshold: float = 3.0) -> bool:
    """
    Detect whether a deck's card fronts are sentences/questions rather than
    single vocabulary words.

    Fuzzy duplicate detection (WRatio) is only meaningful when comparing
    word-to-word. Against sentence fronts, a single lemma will frequently
    appear as a substring of an unrelated sentence (e.g. "give" inside
    "give an example of a microcosm"), producing high-confidence false
    positives that have nothing to do with actual duplication.

    Args:
        fronts:    All card fronts fetched from the deck.
        threshold: Average word count above which a deck is considered
                   sentence-structured. Default 3.0 — a vocabulary deck's
                   fronts are almost always 1-2 words; sentence/question
                   decks average well above this.

    Returns:
        True if the deck should skip fuzzy matching entirely.
    """
    if not fronts:
        return False

    word_counts = [len(f.split()) for f in fronts]
    average = sum(word_counts) / len(word_counts)
    return average > threshold


def _check_single(
    lemma: str,
    fronts: list[str],
    skip_fuzzy: bool = False,
) -> MatchResult:
    """
    Run the confidence interval check for one lemma against all card fronts.

    Exact match (all words):
        Always checked first regardless of deck structure — an exact
        match is meaningful whether the front is a single word or a
        full sentence containing that word as a standalone token.

    skip_fuzzy=True (sentence-structured decks):
        Only the exact match check runs. Fuzzy matching is skipped
        entirely because WRatio against sentence fronts produces
        false positives (a lemma matching as a substring of an
        unrelated sentence). Anything not an exact match is NEW.

    Short word rule (len < SHORT_WORD_THRESHOLD), applied symmetrically:
        If the INCOMING lemma is short, exact match only.
        If a CANDIDATE front is short, it is excluded from fuzzy matching
        entirely — WRatio's partial-ratio component finds substring overlaps
        in short strings regardless of which side is short, producing
        false positives like "cartoon" vs "car" scoring 90.

    Standard rule (both lemma and front >= SHORT_WORD_THRESHOLD,
    skip_fuzzy=False):
        WRatio against eligible fronts via process.extractOne.
        score > CONFIDENCE_HIGH → SKIP
        score >= CONFIDENCE_LOW → QUEUE
        score < CONFIDENCE_LOW  → NEW
    """
    lemma_lower = lemma.lower()

    if not fronts:
        return MatchResult(lemma=lemma, decision=Decision.NEW)

    # ── Exact match check (all words, all deck structures) ───────────────────
    if lemma_lower in fronts:
        return MatchResult(
            lemma=lemma,
            decision=Decision.SKIP,
            matched_front=lemma_lower,
            score=100.0,
        )

    # ── Sentence-structured deck: fuzzy matching is not meaningful ───────────
    if skip_fuzzy:
        return MatchResult(lemma=lemma, decision=Decision.NEW)

    # ── Short word: exact only, no fuzzy ─────────────────────────────────────
    if len(lemma_lower) < SHORT_WORD_THRESHOLD:
        return MatchResult(lemma=lemma, decision=Decision.NEW)

    # ── Filter short fronts out of the fuzzy candidate pool ───────────────────
    # A short front (e.g. "car") will score artificially high against many
    # unrelated longer words due to WRatio's partial-ratio substring matching.
    # Excluding short fronts here is safe: if the incoming lemma genuinely
    # matches a short front, the exact-match check above already caught it.
    fuzzy_candidates = [f for f in fronts if len(f) >= SHORT_WORD_THRESHOLD]

    if not fuzzy_candidates:
        return MatchResult(lemma=lemma, decision=Decision.NEW)

    # ── Fuzzy match (WRatio) ──────────────────────────────────────────────────
    match = fuzz_process.extractOne(
        lemma_lower,
        fuzzy_candidates,
        scorer=fuzz.WRatio,
        score_cutoff=CONFIDENCE_LOW + 1,
    )

    if match is None:
        # No match reached the minimum threshold
        return MatchResult(lemma=lemma, decision=Decision.NEW)

    matched_front, score, _ = match

    if score > CONFIDENCE_HIGH:
        return MatchResult(
            lemma=lemma,
            decision=Decision.SKIP,
            matched_front=matched_front,
            score=score,
        )

    # score is between CONFIDENCE_LOW and CONFIDENCE_HIGH inclusive
    return MatchResult(
        lemma=lemma,
        decision=Decision.QUEUE,
        matched_front=matched_front,
        score=score,
    )


def check_vocabulary(
    vocabulary: dict[str, int],
    deck_name: str,
) -> DeckCheckResult:
    """
    Run confidence interval checks for all lemmas in the vocabulary dict.

    If AnkiConnect is unreachable, all words are written to the SQLite
    backlog and the result has anki_available=False and empty new/skip/queue.
    The backlog is processed when the user explicitly runs --process-backlog.

    Args:
        vocabulary: Ordered dict from nlp.process_transcript().
                    Keys are lemmas, values are frequency counts.
        deck_name:  Deck to check against. Must match exactly as returned
                    by get_deck_names().

    Returns:
        DeckCheckResult with skip / queue / new lists populated.
    """
    result = DeckCheckResult()

    # ── Fetch card fronts ─────────────────────────────────────────────────────
    try:
        fronts = get_card_fronts(deck_name)
    except AnkiNotRunningError as exc:
        logger.warning("Anki unavailable: %s. Writing all words to backlog.", exc)
        _write_backlog(list(vocabulary.keys()), deck_name)
        result.anki_available = False
        return result

    # ── Detect deck structure once for the whole run ──────────────────────────
    skip_fuzzy = _is_sentence_structured_deck(fronts)
    if skip_fuzzy:
        logger.info(
            "Deck '%s' appears sentence-structured (questions/sentences as "
            "fronts) — fuzzy duplicate matching disabled, exact match only.",
            deck_name,
        )

    # ── Check each lemma ──────────────────────────────────────────────────────
    for lemma in vocabulary:
        match_result = _check_single(lemma, fronts, skip_fuzzy=skip_fuzzy)
        if match_result.decision == Decision.SKIP:
            result.skip.append(match_result)
        elif match_result.decision == Decision.QUEUE:
            result.queue.append(match_result)
        else:
            result.new.append(match_result)

    logger.info(
        "Deck check complete: %d skip / %d queue / %d new",
        len(result.skip), len(result.queue), len(result.new),
    )

    # ── Persist queue to SQLite immediately ───────────────────────────────────
    if result.queue:
        _write_backlog([m.lemma for m in result.queue], deck_name)

    return result


# ── CLI prompt ────────────────────────────────────────────────────────────────

def prompt_queue(queue: list[MatchResult]) -> tuple[list[str], list[str]]:
    """
    Interactively prompt the user for each queued word.

    Prints each match with its score and asks y / n / s.
        y — add this word (goes to NEW, proceeds to definition fetch)
        n — skip this word (treated as already known, no card created)
        s — skip ALL remaining queued words (write them to review file)

    Returns:
        (approved, deferred)
        approved:  lemmas the user confirmed as new — proceed to definition fetch
        deferred:  lemmas sent to the review file for later resolution
    """
    if not queue:
        return [], []

    approved: list[str]  = []
    deferred: list[str]  = []
    skip_all: bool       = False

    print(f"\n{'─' * 60}")
    print(f"  {len(queue)} word(s) need your review")
    print(f"  [y] add card  [n] skip  [s] defer all remaining to review file")
    print(f"{'─' * 60}\n")

    for i, match in enumerate(queue, start=1):
        if skip_all:
            deferred.append(match.lemma)
            continue

        score_str = f"{match.score:.0f}%" if match.score is not None else "N/A"
        print(
            f"  [{i}/{len(queue)}]  "
            f'"{match.lemma}"  may already exist as  '
            f'"{match.matched_front}"  ({score_str} match)'
        )

        while True:
            answer = input("  Add anyway? [y/n/s]: ").strip().lower()
            if answer == "y":
                approved.append(match.lemma)
                break
            elif answer == "n":
                deferred.append(match.lemma)
                break
            elif answer == "s":
                deferred.append(match.lemma)
                skip_all = True
                break
            else:
                print("  Please enter y, n, or s.")

    print(f"\n  {len(approved)} approved / {len(deferred)} deferred to review file\n")

    if deferred:
        _write_review_file(
            [m for m in queue if m.lemma in deferred]
        )

    return approved, deferred


# ── Review file ───────────────────────────────────────────────────────────────

def _write_review_file(matches: list[MatchResult]) -> None:
    """
    Write deferred queue items to review.json.

    Appends to existing entries if the file already exists so multiple
    pipeline runs accumulate in one place.

    File structure:
    [
        {
            "lemma": "contaminate",
            "matched_front": "contamination",
            "score": 83.3,
            "decision": null   ← user fills in "add" or "skip"
        },
        ...
    ]
    """
    existing: list[dict] = []
    if REVIEW_FILE.exists():
        try:
            existing = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("review.json was malformed — overwriting.")

    existing_lemmas = {entry["lemma"] for entry in existing}

    new_entries = [
        {
            "lemma":         m.lemma,
            "matched_front": m.matched_front,
            "score":         round(m.score, 1) if m.score is not None else None,
            "decision":      None,
        }
        for m in matches
        if m.lemma not in existing_lemmas
    ]

    if new_entries:
        existing.extend(new_entries)
        REVIEW_FILE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Wrote %d entries to %s", len(new_entries), REVIEW_FILE)


def load_review_decisions() -> tuple[list[str], list[str]]:
    """
    Read review.json and return words the user has marked as add or skip.

    Words with decision=null are ignored — they haven't been reviewed yet.

    Returns:
        (to_add, to_skip)
        to_add:  lemmas marked "add" — proceed to definition fetch
        to_skip: lemmas marked "skip" — treat as known, no card
    """
    if not REVIEW_FILE.exists():
        return [], []

    try:
        entries = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Could not parse review.json: %s", exc)
        return [], []

    to_add  = [e["lemma"] for e in entries if e.get("decision") == "add"]
    to_skip = [e["lemma"] for e in entries if e.get("decision") == "skip"]

    return to_add, to_skip


# ── SQLite backlog ────────────────────────────────────────────────────────────




def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anki_backlog (
            lemma      TEXT NOT NULL,
            deck_name  TEXT NOT NULL,
            queued_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (lemma, deck_name)
        )
    """)
    conn.commit()
    return conn


def _write_backlog(lemmas: list[str], deck_name: str) -> None:
    """Persist lemmas to the SQLite backlog when Anki is unavailable."""
    with _get_db() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO anki_backlog (lemma, deck_name) VALUES (?, ?)",
            [(lemma, deck_name) for lemma in lemmas],
        )
    logger.info("Wrote %d lemmas to backlog for deck '%s'.", len(lemmas), deck_name)


def get_backlog(deck_name: str) -> list[str]:
    """Return all backlogged lemmas for a given deck."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT lemma FROM anki_backlog WHERE deck_name = ? ORDER BY queued_at",
            (deck_name,),
        ).fetchall()
    return [row["lemma"] for row in rows]


def clear_backlog(deck_name: str, lemmas: list[str]) -> None:
    """Remove processed lemmas from the backlog."""
    with _get_db() as conn:
        conn.executemany(
            "DELETE FROM anki_backlog WHERE lemma = ? AND deck_name = ?",
            [(lemma, deck_name) for lemma in lemmas],
        )
    logger.info("Cleared %d lemmas from backlog for deck '%s'.", len(lemmas), deck_name)


def process_backlog(deck_name: str) -> DeckCheckResult:
    """
    Process all backlogged lemmas for a deck.

    Called explicitly by the user via --process-backlog flag.
    Requires Anki to be running — raises AnkiNotRunningError if not.

    Returns:
        DeckCheckResult as if the backlogged words had just been checked.
    """
    backlogged = get_backlog(deck_name)
    if not backlogged:
        logger.info("No backlog for deck '%s'.", deck_name)
        return DeckCheckResult()

    logger.info(
        "Processing backlog: %d words for deck '%s'.", len(backlogged), deck_name
    )

    # Re-check all backlogged words against the current deck state
    vocabulary = {lemma: 1 for lemma in backlogged}
    result = check_vocabulary(vocabulary, deck_name)

    if result.anki_available:
        clear_backlog(deck_name, backlogged)

    return result