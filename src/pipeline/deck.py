"""
deck.py
-------
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

import html
import json
import logging
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests
from rapidfuzz import fuzz, process as fuzz_process

logger = logging.getLogger(__name__)

# ── Constants (override in config.py) ────────────────────────────────────────

from pipeline.config import (
    ANKI_HOST, ANKI_HOST_EXPLICIT, ANKI_VERSION, ANKI_TIMEOUT, ANKI_IMPORT_TIMEOUT,
    CONFIDENCE_HIGH, CONFIDENCE_LOW, SHORT_WORD_THRESHOLD,
    REVIEW_FILE, DB_PATH, is_wsl, wsl_host_ip,
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

# Actions whose duration scales with the collection rather than the request.
# Everything else answers in milliseconds and keeps the short timeout.
_SLOW_ACTIONS = {"importPackage", "exportPackage", "sync"}


# The host actually in use. Starts as configured and only ever moves once,
# to the WSL fallback below, so every later call in the run goes straight to
# the address that answered.
_active_host: str = ANKI_HOST
_wsl_fallback_tried: bool = False


def _wsl_fallback_host() -> str | None:
    """
    The one alternative address worth trying after localhost is refused.

    Under WSL2's default NAT networking, Anki runs on the Windows side and
    `localhost` is the Linux VM, so the connection is refused by a machine
    that was never going to have Anki on it. The Windows host is at the
    default route (config.wsl_host_ip).

    This is a fallback rather than a default on purpose. Defaulting to the
    gateway under WSL would break WSL2 mirrored networking, where localhost
    IS correct and the gateway is not, so a setup that works today would
    stop. Trying it only after a refusal cannot do that: the refusal has
    already happened.

    Returns None -- meaning "nothing to try" -- when the user named the
    host themselves, when this is not WSL, when the gateway cannot be read,
    when it is where we just failed, and after one attempt, so a run that
    is going to fail fails at one request per call rather than two.
    """
    global _wsl_fallback_tried
    if _wsl_fallback_tried or ANKI_HOST_EXPLICIT or not is_wsl():
        return None
    _wsl_fallback_tried = True
    ip = wsl_host_ip()
    if not ip:
        return None
    port = urlsplit(_active_host).port or 8765
    candidate = f"http://{ip}:{port}"
    return None if candidate == _active_host else candidate


def _anki_request(action: str, **params) -> object:
    """
    Send a request to AnkiConnect and return the result field.

    Uses ANKI_IMPORT_TIMEOUT for the actions in _SLOW_ACTIONS and
    ANKI_TIMEOUT for everything else. One timeout for both was the bug:
    importing a package into a large collection exceeded 5s, raised
    AnkiNotRunningError, and looked exactly like Anki being closed.

    A refused connection under WSL is retried once against the Windows host
    (see _wsl_fallback_host). A timeout is not: something answered, so the
    address is right and a second address would be a worse guess.

    Raises:
        AnkiNotRunningError: If the connection is refused or times out.
        AnkiConnectError:    If AnkiConnect returns an error string.
    """
    global _active_host
    payload = {"action": action, "version": ANKI_VERSION, "params": params}
    timeout = ANKI_IMPORT_TIMEOUT if action in _SLOW_ACTIONS else ANKI_TIMEOUT
    try:
        response = requests.post(_active_host, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        fallback = _wsl_fallback_host()
        if fallback is None:
            raise AnkiNotRunningError(
                f"AnkiConnect not reachable at {_active_host}. "
                "Ensure Anki is running with the AnkiConnect add-on installed."
            ) from exc
        try:
            response = requests.post(fallback, json=payload, timeout=timeout)
            response.raise_for_status()
        except requests.exceptions.RequestException as retry_exc:
            raise AnkiNotRunningError(
                f"AnkiConnect not reachable at {_active_host}, nor at "
                f"{fallback}, the Windows host this WSL session routes "
                f"through.\n"
                f"  Ensure Anki is running on Windows with the AnkiConnect "
                f"add-on installed.\n"
                f"  AnkiConnect binds to 127.0.0.1 by default, which WSL "
                f"cannot reach. Set its config to \"0.0.0.0\" in Anki: "
                f"Tools, Add-ons, AnkiConnect, Config."
            ) from retry_exc
        _active_host = fallback
        logger.info(
            "AnkiConnect answered at %s after %s refused the connection. "
            "Set ANKI_HOST=%s in .env to skip this retry; the address is "
            "reassigned when Windows reboots.",
            fallback, ANKI_HOST, fallback,
        )
    except requests.exceptions.Timeout as exc:
        raise AnkiNotRunningError(
            f"AnkiConnect timed out after {timeout}s on '{action}'.\n"
            f"  Anki is running but did not answer. A modal dialog open in "
            f"Anki blocks every request; close it and retry.\n"
            f"  A slow import can also need a longer ANKI_IMPORT_TIMEOUT "
            f"in .env."
        ) from exc

    data = response.json()
    if data.get("error"):
        # Anki's own wording, then the two things that actually fix it.
        # "deck was not found" and "model was not found" are the common
        # pair, and neither says which of Anki's states to correct.
        raise AnkiConnectError(
            f"AnkiConnect error on '{action}': {data['error']}\n"
            f"  Check the deck and notetype exist in the open Anki profile, "
            f"then retry.\n"
            f"  Run 'tango doctor' to confirm Anki is reachable."
        )

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


def ensure_model_fields(model_id: int, fields: Sequence[str]) -> list[str]:
    """
    Add any field the collection's notetype is missing, before an import.

    Anki matches an incoming notetype by ID, and when the ID already exists
    with a DIFFERENT field list it does not merge -- it forks a new notetype
    with the ID bumped by one and the name suffixed. Measured: importing the
    12-field package into a collection holding the 10-field notetype at
    1607392321 produced "YT Anki Pipeline — Recognition-da2c0" at 1607392322,
    left all 207 existing notes on the old notetype, and put the new card on
    the fork. That is the same mechanism that moved this project's own ID
    from genanki's 1607392319 to 1607392321 -- twice, hence the +2. See
    ARCHITECTURE.md 8.32.

    Adding the fields first makes the schemas match, so the import merges.
    This is the safe direction: adding a field preserves every note, its
    content and its scheduling. Verified on a real collection -- all 207
    notes came through with byte-identical field values and two new empty
    fields.

    **Resolved by ID, not by name, and the distinction is not academic.**
    Anki's fork names the new notetype by suffixing, so on a collection with
    any history the ID and the plain name come apart: measured on the real
    collection, `1607392321` is named "YT Anki Pipeline — Recognition-6c3a0"
    and holds this pipeline's 2135 cards, while a *different* notetype named
    exactly "YT Anki Pipeline — Recognition" sits at 1782849352300 with 1134
    notes and an older, incompatible field list (PartOfSpeech, ExampleDict,
    FallbackNote). Looking up by name found that one and would have added six
    fields to a notetype the pipeline does not write to, while the import
    forked anyway. The ID is what Anki matches on, so the ID is what this
    must read. ARCHITECTURE.md 8.33.

    Args:
        model_id: The notetype's ID (config.MODEL_ID) -- the same integer
                  genanki stamps into the package.
        fields:   The full ordered field list (cards.FIELDS). Missing names
                  are added at their index in this sequence.

    Returns:
        The field names added, in the order added. Empty when the notetype
        already matched, and empty when no notetype carries this ID at all --
        a first import creates it whole and correct, so there is nothing to
        align.

    Raises:
        AnkiNotRunningError: Anki not running.
        AnkiConnectError:    AnkiConnect returned an error.
    """
    # modelNamesAndIds rather than modelNameFromId: it answers in one call
    # and returns nothing rather than raising when the ID is absent, which
    # is the ordinary first-import case.
    names_by_id = {v: k for k, v in _anki_request("modelNamesAndIds").items()}
    model_name = names_by_id.get(model_id)
    if model_name is None:
        return []

    existing = _anki_request("modelFieldNames", modelName=model_name)
    missing = [name for name in fields if name not in existing]

    for name in missing:
        # Index within the canonical order, not len(existing): a notetype
        # missing two fields must still receive them at 10 and 11 rather
        # than in whatever order the loop happens to reach them.
        _anki_request(
            "modelFieldAdd",
            modelName=model_name,
            fieldName=name,
            index=list(fields).index(name),
        )
        logger.info("Added field '%s' to notetype '%s'.", name, model_name)

    return missing


# Field names tried when a note carries no usable "order" key. "Front" is
# Anki's stock Basic type; "Word" is this pipeline's own model. Only the
# fallback path below consults these — the ordinary path does not care what
# the first field is called.
_FRONT_FIELD_NAMES = ("Front", "Word")

# Anki stores field values as HTML, and hand-made cards are full of it.
# Markup is never part of a headword, so it is removed before matching:
# "abominable<br><br>*qui inspire l'horreur" is a real front from a real
# deck, and the tags are noise to every comparison downstream.
_HTML_TAG = re.compile(r"<[^>]+>")
_ANKI_MEDIA = re.compile(r"\[(?:sound|anki:tts)[^]]*\]")


def _clean_field_value(value: str) -> str:
    """
    Reduce one raw Anki field value to comparable text.

    Removes media references and HTML tags, resolves entities, then
    collapses whitespace. Measured against two real decks (7453 notes):
    stripping lowers the average word count rather than raising it, since
    tag soup was previously being counted as words, so it does not push a
    vocabulary deck over `_is_sentence_structured_deck`'s threshold.

    Args:
        value: Raw field value as AnkiConnect returned it.

    Returns:
        Stripped, lowercased text with runs of whitespace collapsed.
    """
    text = _ANKI_MEDIA.sub(" ", value)
    text = _HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    return " ".join(text.split()).strip().lower()


def _extract_front(fields: dict) -> str:
    """
    Return one note's front text from its AnkiConnect ``fields`` mapping.

    Anki identifies a note by its FIRST field — that is what its own
    duplicate detection uses — so that is what this reads, via the ``order``
    key AnkiConnect returns beside each value.

    Reading a field named ``Front`` instead, as this did, was wrong for
    every deck the pipeline itself produces. The generated model's first
    field is named ``Word`` (see ``cards.py``), so a deck built by Tango
    yielded no fronts at all, `_check_single` took its empty-fronts branch,
    and every word added by an earlier run came back NEW: definitions
    re-fetched, and a duplicate card generated that Anki imports rather
    than merges, since the GUID includes the video ID.

    Args:
        fields: The ``fields`` mapping from one ``notesInfo`` entry, shaped
                ``{name: {"value": str, "order": int}}``.

    Returns:
        The front text, stripped and lowercased, or ``""`` when the note has
        no usable field.
    """
    ordered = [
        (spec["order"], name)
        for name, spec in fields.items()
        if isinstance(spec, dict) and isinstance(spec.get("order"), int)
    ]
    if ordered:
        _, first_field = min(ordered)
        return _clean_field_value(str(fields[first_field].get("value", "")))

    # No order information. Older AnkiConnect responses omit it, and so does
    # every fixture written against this function before now — which is
    # precisely why the bug above survived: no test could express a note
    # whose first field is not called "Front".
    for name in _FRONT_FIELD_NAMES:
        spec = fields.get(name)
        if isinstance(spec, dict) and spec.get("value"):
            return _clean_field_value(str(spec["value"]))

    return ""


def get_card_fronts(deck_name: str) -> list[str]:
    """
    Fetch the first field of every note in the given deck.

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
        front_value = _extract_front(note.get("fields") or {})
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

    # ── Fuzzy match (three-condition filter) ─────────────────────────────────
    # WRatio alone inflates scores when one string is a substring of the other
    # (e.g. "commencer" vs "comme" scores 90 via partial_ratio = 100).
    # Fix: require WRatio AND token_sort_ratio AND length_ratio all above threshold.
    # token_sort_ratio is insensitive to word order and penalises substring inflation.
    # length_ratio filters cases where one string is much shorter than the other.
    match = fuzz_process.extractOne(
        lemma_lower,
        fuzzy_candidates,
        scorer=fuzz.WRatio,
        score_cutoff=CONFIDENCE_LOW + 1,
    )

    if match is None:
        return MatchResult(lemma=lemma, decision=Decision.NEW)

    matched_front, score, _ = match

    # Apply secondary filters to eliminate substring inflation
    token_score   = fuzz.token_sort_ratio(lemma_lower, matched_front)
    shorter_len   = min(len(lemma_lower), len(matched_front))
    longer_len    = max(len(lemma_lower), len(matched_front))
    length_ratio  = shorter_len / longer_len if longer_len > 0 else 1.0

    # Minimum token_sort_ratio prevents pure substring matches scoring high
    # Minimum length_ratio prevents very short fronts inflating long lemmas
    if token_score < 50 or length_ratio < 0.6:
        return MatchResult(lemma=lemma, decision=Decision.NEW)

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
    The backlog is processed when the user explicitly runs `tango backlog`.

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
            # EOF -> "s": defer this and every remaining word to review.json,
            # which is what the documented non-interactive recipe asks for
            # and the only safe default when nobody is there to answer.
            try:
                answer = input("  Add anyway? [y/n/s]: ").strip().lower()
            except EOFError:
                print()
                answer = "s"
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

    Called explicitly by the user via the `tango backlog` command.
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