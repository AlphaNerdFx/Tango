"""
media.py
--------
Responsible for:
  1. Downloading pronunciation audio referenced by a card
  2. Caching it on disk so a repeated word is fetched once
  3. Naming it safely for Anki's shared media folder

Why this exists as its own module: `cards.py` is otherwise entirely offline
and therefore trivially testable, and definition fetching happens long before
a package is built. Downloading belongs to neither, so it lives here and
`cards.build_package()` calls it.

Why download at all, when v0.5.0 shipped a link: a link opens a browser,
which is not reviewing. An embedded file plays inside the card, works on
AnkiDroid and AnkiMobile, and keeps working when the source is down.

ADR-009 rejected embedding on size grounds and the estimate was wrong.
Measured on real Commons files: De-Haus 16 KB, De-Spaziergang 30 KB, roughly
20 KB average, so a 240-card German deck costs about 5 MB rather than the
"tens of megabytes" the ADR assumed. Wikimedia also already serves these
through its `transcoded` path as MP3 (`audio/mpeg`), which Anki plays
natively, so nothing needs converting.

Dependencies:
    requests
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from pipeline.config import (
    MEDIA_BURST,
    MEDIA_DIR,
    MEDIA_MAX_RETRIES,
    MEDIA_MAX_RETRY_WAIT,
    MEDIA_RATE_LIMIT,
    MEDIA_TIMEOUT,
    WIKTIONARY_USER_AGENT,
)

logger = logging.getLogger(__name__)

# Anki's media folder is a single flat namespace shared with everything the
# user already has. An unprefixed "house-us.mp3" could overwrite a file from
# another deck, so every filename this project writes is namespaced.
_PREFIX = "tango"

# Word characters in ANY script, so Cyrillic and CJK lemmas keep a readable
# name. An ASCII-only class collapsed every non-Latin word to the same string:
# "дом", "стол" and "книга" all became "tango-ru", so one Russian recording
# would have been served for every Russian card. Dots are excluded on purpose
# -- the only dot in the result is the extension's, which keeps ".." out of a
# name built from untrusted transcript text.
_UNSAFE = re.compile(r"[^\w-]+", re.UNICODE)


class AudioUnavailableError(Exception):
    """The audio could not be retrieved. Callers fall back to a link."""


class RateLimiter:
    """
    Paces every request this module makes, across all threads.

    A leaky bucket rather than a sleep between calls: `acquire()` hands out
    time slots `1 / rate` apart, and after an idle spell the scheduler may be
    up to `burst` slots behind, so a short run of a few words pays nothing and
    a long one settles to the sustainable rate.

    Threads queue on the lock only long enough to claim a slot, then sleep
    outside it, so downloads still overlap -- the limiter caps the request
    *rate*, it does not serialise the transfers.

    This exists because the download host rate-limits per IP and answers with
    429 rather than an error, which the previous code treated as a permanent
    failure. See config.MEDIA_RATE_LIMIT for the measurements.

    **Public, and used by definition.py as well as here.** Merriam-Webster
    turned out to have the same failure shape as the download host: a real
    English run made roughly 18 requests a second, the source stopped
    answering after 167 words, and 85% of that deck shipped with no
    definition. One implementation of this rather than two, because the
    subtle part is the scheduling and a copy would drift.
    """

    def __init__(self, rate: float, burst: int) -> None:
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._burst = max(1, burst)
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        """Block until this caller's slot is due. Returns immediately when unpaced."""
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            # How far behind the scheduler is allowed to be, which is what
            # turns "burst" into `burst` immediate slots after an idle spell.
            floor = now - self._burst * self._interval
            slot = max(floor, self._next_slot)
            self._next_slot = slot + self._interval
            wait = slot - now
        if wait > 0:
            time.sleep(wait)


_LIMITER = RateLimiter(MEDIA_RATE_LIMIT, MEDIA_BURST)


def _retry_after(response: requests.Response, default: float = 5.0) -> float:
    """
    Seconds to wait before repeating a rate-limited request.

    Args:
        response: The 429 response, whose `Retry-After` header is preferred.
        default:  Used when the header is absent or not a number.

    Returns:
        A delay clamped to MEDIA_MAX_RETRY_WAIT, so a server asking for an
        hour cannot stall a run.

    Only the delta-seconds form is parsed. `Retry-After` may also carry an
    HTTP date, but the host this matters for sends "11".
    """
    try:
        delay = float(response.headers.get("retry-after", ""))
    except (TypeError, ValueError):
        delay = default
    return max(0.0, min(delay, MEDIA_MAX_RETRY_WAIT))


def _extension(url: str) -> str:
    """
    File extension for `url`, defaulting to .mp3.

    Wikimedia's transcoded URLs end ".ogg.mp3" and are genuinely MP3; taking
    only the final suffix is correct and is what Anki needs to see.
    """
    tail = url.split("?")[0].rsplit("/", 1)[-1]
    suffix = Path(tail).suffix.lower()
    return suffix if suffix in {".mp3", ".ogg", ".wav", ".m4a"} else ".mp3"


def media_filename(lemma: str, language: str, url: str) -> str:
    """
    The name this audio will have inside Anki's media folder.

    Args:
        lemma:    The word the audio belongs to.
        language: Transcript language code, which keeps a cognate in two
                  languages from colliding on one filename.
        url:      Source URL, used only for its extension.

    Returns:
        A filename safe for Anki's flat, shared media namespace.

    Deterministic, so the same word in a later video reuses the cached file
    instead of downloading it again or adding a duplicate to the collection.

    The readable part is best-effort; the trailing hash is what actually
    guarantees uniqueness. Sanitising alone is not enough, and the failure is
    silent rather than loud: any two lemmas that clean to the same string
    share one file, so a card plays another word's recording. That is not
    hypothetical -- an ASCII-only filter mapped every Cyrillic and CJK word to
    the identical name.
    """
    digest = hashlib.sha1(f"{language}:{lemma}".encode("utf-8")).hexdigest()[:8]
    readable = _UNSAFE.sub("-", lemma.lower()).strip("-")
    stem = "-".join(part for part in (_PREFIX, language, readable, digest) if part)
    return f"{stem}{_extension(url)}"


def fetch_audio(url: str, lemma: str, language: str) -> Optional[Path]:
    """
    Download one pronunciation file, returning its local path.

    Args:
        url:      Remote audio URL from the index or dictionaryapi.dev.
        lemma:    The word, used for the cached filename.
        language: Transcript language code.

    Returns:
        Path to the cached file, or None when it could not be retrieved.

    Never raises. A missing recording costs one card its audio; it must not
    end a run that has already done the expensive work. The caller falls back
    to linking the URL, so the card still offers the audio either way.

    Failure is expected often enough to be designed for rather than logged as
    an error: measured while building this, dictionaryapi.dev served
    `water-uk.mp3` (200, 22 KB) and returned 502 for `run-us.mp3` in the same
    minute.
    """
    if not url:
        return None

    name = media_filename(lemma, language, url)
    path = MEDIA_DIR / name
    if path.exists() and path.stat().st_size > 0:
        return path

    # A 429 is worth repeating and nothing else is: the bucket refills, while
    # a 404 or a 502 means the file is simply not there and the card should
    # fall back to a link now rather than after two pointless waits.
    for attempt in range(MEDIA_MAX_RETRIES + 1):
        _LIMITER.acquire()
        try:
            # Wikimedia rejects the default python-requests User-Agent with
            # 403, per their User-Agent policy. Measured: the same URL that
            # curl fetches happily returns 403 unheadered. The project already
            # carries an identifying agent for the Wiktionary API; the same
            # one applies here, since this is the same operator hitting the
            # same foundation.
            response = requests.get(
                url,
                timeout=MEDIA_TIMEOUT,
                headers={"User-Agent": WIKTIONARY_USER_AGENT},
            )
        except requests.RequestException as exc:
            logger.debug("Audio unavailable for '%s' (%s): %s", lemma, url, exc)
            return None

        if response.status_code == 429 and attempt < MEDIA_MAX_RETRIES:
            delay = _retry_after(response)
            logger.debug(
                "Rate-limited fetching '%s'; retrying in %.1fs (attempt %d of %d).",
                lemma, delay, attempt + 1, MEDIA_MAX_RETRIES,
            )
            time.sleep(delay)
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            logger.debug("Audio unavailable for '%s' (%s): %s", lemma, url, exc)
            return None
        break
    else:
        # Only reachable if MEDIA_MAX_RETRIES is configured negative.
        return None

    # A 200 carrying an error page is the failure mode that would otherwise
    # write a text file into the collection and give Anki a silent, unplayable
    # "recording". dictionaryapi.dev's 502s arrive as text/plain.
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("audio/"):
        logger.debug(
            "Audio for '%s' was %s, not audio -- ignoring.", lemma, content_type or "untyped"
        )
        return None

    if not response.content:
        return None

    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    except OSError as exc:
        logger.debug("Could not cache audio for '%s': %s", lemma, exc)
        return None

    logger.debug("Cached %s (%d bytes) for '%s'.", name, len(response.content), lemma)
    return path
