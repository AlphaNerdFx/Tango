"""
Responsible for all pipeline-level SQLite state:

    processed_videos   — tracks which video IDs have been fully run
    generated_packages — logs every .apkg file produced
    vocabulary         — persists NLP output keyed by (lemma, video_id)

This module does NOT own the definitions or anki_backlog tables —
those remain in definition.py and deck.py respectively as deliberate
fallback isolation. Each module can fail independently.

Session state (selected deck name) is in-memory only — lives in
the calling process and not persisted. When the process exits,
the session ends.

Constants (moved to config.py at end of project):
    DB_PATH
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# -- Constants (moved to config.py at end of project) -------------------------

from pipeline.config import DB_PATH


# -- Custom exceptions --------------------------------------------------------

class VideoAlreadyProcessedError(Exception):
    """
    Raised when a video ID has already been fully processed.
    The pipeline warns the user and continues without creating new cards.
    """
    def __init__(self, video_id: str, processed_at: str, deck_name: str) -> None:
        self.video_id     = video_id
        self.processed_at = processed_at
        self.deck_name    = deck_name
        super().__init__(
            f"Video '{video_id}' was already processed on {processed_at} "
            f"for deck '{deck_name}'. No new cards will be created."
        )


# -- DB connection ------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """
    Return a SQLite connection with WAL mode and row factory enabled.
    Creates all state.py-owned tables if they do not exist.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_videos (
            video_id     TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL,
            deck_name    TEXT NOT NULL,
            card_count   INTEGER NOT NULL DEFAULT 0,
            word_count   INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS generated_packages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id   TEXT NOT NULL,
            file_path  TEXT NOT NULL,
            deck_name  TEXT NOT NULL,
            card_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vocabulary (
            lemma          TEXT    NOT NULL,
            video_id       TEXT    NOT NULL,
            frequency      INTEGER NOT NULL DEFAULT 1,
            position       INTEGER NOT NULL,
            part_of_speech TEXT,
            added_at       TEXT    NOT NULL,
            PRIMARY KEY (lemma, video_id)
        );
    """)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- processed_videos ---------------------------------------------------------

def is_video_processed(video_id: str) -> bool:
    """Return True if this video ID has already been fully processed."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT video_id FROM processed_videos WHERE video_id = ?",
            (video_id,),
        ).fetchone()
    return row is not None


def check_video_not_processed(video_id: str) -> None:
    """
    Raise VideoAlreadyProcessedError if this video has been processed before.

    Call at pipeline startup. The caller catches the error, warns the user,
    and exits without creating cards.

    Raises:
        VideoAlreadyProcessedError
    """
    with _get_db() as conn:
        row = conn.execute(
            "SELECT processed_at, deck_name FROM processed_videos WHERE video_id = ?",
            (video_id,),
        ).fetchone()
    if row:
        raise VideoAlreadyProcessedError(
            video_id=video_id,
            processed_at=row["processed_at"],
            deck_name=row["deck_name"],
        )


def mark_video_processed(
    video_id: str,
    deck_name: str,
    card_count: int,
    word_count: int,
) -> None:
    """
    Record that a video has been fully processed.

    Called at the end of a successful pipeline run after the .apkg
    has been written. Updates the record if it already exists.
    """
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO processed_videos
                (video_id, processed_at, deck_name, card_count, word_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                processed_at = excluded.processed_at,
                deck_name    = excluded.deck_name,
                card_count   = excluded.card_count,
                word_count   = excluded.word_count
            """,
            (video_id, _now(), deck_name, card_count, word_count),
        )


def get_processed_videos() -> list[dict]:
    """Return all processed video records, most recent first."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM processed_videos ORDER BY processed_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


# -- generated_packages -------------------------------------------------------

def log_package(
    video_id: str,
    file_path: Path,
    deck_name: str,
    card_count: int,
) -> int:
    """
    Log a generated .apkg file to the database.

    Called immediately after cards.build_package() returns a path.
    Multiple packages can exist for the same video_id.

    Returns:
        The autoincrement row ID of the inserted record.
    """
    with _get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO generated_packages
                (video_id, file_path, deck_name, card_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (video_id, str(file_path), deck_name, card_count, _now()),
        )
    return cursor.lastrowid


def get_packages_for_video(video_id: str) -> list[dict]:
    """Return all package records for a video, most recent first."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM generated_packages WHERE video_id = ? ORDER BY created_at DESC",
            (video_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_packages() -> list[dict]:
    """Return all generated package records, most recent first."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM generated_packages ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


# -- vocabulary ---------------------------------------------------------------

def save_vocabulary(
    video_id: str,
    vocabulary: dict[str, int],
    pos_map: Optional[dict[str, str]] = None,
) -> None:
    """
    Persist the NLP vocabulary output for a video to SQLite.

    Keyed by (lemma, video_id) — the same word across different videos
    produces separate rows, enabling per-video vocabulary tracking for
    Phase 3 domain analysis.

    Position follows first-appearance order guaranteed by nlp.py.
    On conflict (rerun), frequency is summed rather than overwritten.

    Args:
        video_id:   YouTube video ID.
        vocabulary: Ordered dict from nlp.process_transcript().
        pos_map:    Optional lemma -> part_of_speech mapping.
    """
    if not vocabulary:
        logger.warning("save_vocabulary called with empty vocabulary for '%s'.", video_id)
        return

    now  = _now()
    rows = [
        (
            lemma,
            video_id,
            frequency,
            position,
            pos_map.get(lemma) if pos_map else None,
            now,
        )
        for position, (lemma, frequency) in enumerate(vocabulary.items())
    ]

    with _get_db() as conn:
        conn.executemany(
            """
            INSERT INTO vocabulary
                (lemma, video_id, frequency, position, part_of_speech, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(lemma, video_id) DO UPDATE SET
                frequency = frequency + excluded.frequency
            """,
            rows,
        )
    logger.info("Saved %d vocabulary entries for video '%s'.", len(rows), video_id)


def get_vocabulary_for_video(video_id: str) -> list[dict]:
    """
    Return all vocabulary entries for a video ordered by first-appearance.
    """
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM vocabulary WHERE video_id = ? ORDER BY position ASC",
            (video_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_word_across_videos(lemma: str) -> list[dict]:
    """
    Return all video records where a given lemma appears.

    Phase 3 use: track which videos introduced a word and build
    the domain graph for the recommendation engine.
    """
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM vocabulary WHERE lemma = ? ORDER BY added_at ASC",
            (lemma,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_top_words(video_id: str, limit: int = 20) -> list[dict]:
    """
    Return the most frequent words from a video's vocabulary.

    Args:
        video_id: YouTube video ID.
        limit:    Maximum number of words to return. Default 20.
    """
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM vocabulary WHERE video_id = ? ORDER BY frequency DESC LIMIT ?",
            (video_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


# -- Session (in-memory only) -------------------------------------------------

class Session:
    """
    Lightweight in-memory session container.

    Holds the user's selected deck for one pipeline run.
    Not persisted — when the process exits, the session ends.

    Usage:
        session = Session()
        session.set_deck("Language::English::Vocabulary")
        deck = session.deck_name
    """

    def __init__(self) -> None:
        self._deck_name: Optional[str] = None

    def set_deck(self, deck_name: str) -> None:
        self._deck_name = deck_name
        logger.info("Session deck set: '%s'", deck_name)

    @property
    def deck_name(self) -> Optional[str]:
        return self._deck_name

    @property
    def is_ready(self) -> bool:
        return self._deck_name is not None

    def clear(self) -> None:
        self._deck_name = None