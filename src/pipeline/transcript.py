"""
Three responsibilities:
  1. get_transcript()   — fetch a Transcript object for a video ID
  2. get_properties()   — extract metadata from a fetched transcript
  3. get_snippets()     — build a timestamp-indexed dict of text per language

The caller (nlp.py) uses get_snippets() to get the clean joined text.
"""

from __future__ import annotations

import html
import re

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeDataUnparsable,
    YouTubeRequestFailed,
    WebshareProxyConfig,
    GenericProxyConfig,
)
from youtube_transcript_api._transcripts import FetchedTranscript, Transcript

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cleaning helpers ──────────────────────────────────────────────────────────

_ANNOTATION_RE = re.compile(r"\[[\w\s]+\]")   # [Music], [Applause] etc.
_WHITESPACE_RE  = re.compile(r"\s+")


def _clean(text: str) -> str:
    text = html.unescape(text)
    text = _ANNOTATION_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


# ── Proxy ─────────────────────────────────────────────────────────────────────

def _build_proxy() -> Optional[object]:
    """
    Returns a proxy config if environment variables are set, else None.

    Priority:
        1. Webshare  (WEBSHARE_USERNAME + WEBSHARE_PASSWORD)
        2. Generic   (PROXY_HTTP_URL and/or PROXY_HTTPS_URL)
        3. No proxy
    """
    from pipeline.config import (
        WEBSHARE_USERNAME as ws_user,
        WEBSHARE_PASSWORD as ws_pass,
        PROXY_HTTP_URL, PROXY_HTTPS_URL,
    )
    
    if ws_user and ws_pass:
        return WebshareProxyConfig(proxy_username=ws_user, proxy_password=ws_pass)

    http  = PROXY_HTTP_URL
    https = PROXY_HTTPS_URL
    if http or https:
        return GenericProxyConfig(http_url=http, https_url=https)

    return None


# ── 1. get_transcript ─────────────────────────────────────────────────────────

def get_transcript(video_id: str, languages: list[str] = ["en"]) -> Transcript:
    """
    Fetch and return a Transcript object for the given video ID.

    Args:
        video_id:  11-character YouTube video ID (not a URL).
        languages: Ordered language preference list. First available is used.

    Returns:
        youtube_transcript_api Transcript object.

    Raises:
        All exceptions are re-raised with a clear message. Callers should
        catch the specific types they want to handle; let the rest propagate.
    """
    api = YouTubeTranscriptApi(proxy_config=_build_proxy())

    try:
        transcript_list = api.list(video_id)
    except VideoUnavailable:
        raise VideoUnavailable(video_id)
    except AgeRestricted:
        raise AgeRestricted(video_id)
    except VideoUnplayable as exc:
        raise VideoUnplayable(video_id, exc.reason) from exc
    except TranscriptsDisabled:
        raise TranscriptsDisabled(video_id)
    except (IpBlocked, RequestBlocked):
        raise IpBlocked(video_id)
    except PoTokenRequired:
        raise PoTokenRequired(video_id)
    except YouTubeDataUnparsable:
        raise YouTubeDataUnparsable(video_id)
    except YouTubeRequestFailed as exc:
        raise YouTubeRequestFailed(video_id, exc) from exc

    # Use resolve_transcript for partial BCP-47 matching and manual-first preference
    from pipeline.language import resolve_transcript
    if languages and len(languages) == 1:
        return resolve_transcript(transcript_list, languages[0])

    try:
        return transcript_list.find_transcript(languages)
    except (NoTranscriptFound, CouldNotRetrieveTranscript):
        available = [t.language_code for t in transcript_list]
        raise NoTranscriptFound(video_id, languages, available)


# ── 2. get_properties ─────────────────────────────────────────────────────────

def get_properties(transcript: Transcript) -> dict:
    """
    Return metadata for a single Transcript object.

    Calls .fetch() internally to access snippet-level data (duration, count).
    The fetched result is NOT cached here — if you need snippets too,
    call get_snippets() separately (it also fetches internally).

    Returns:
        {
            "video_id":             str,
            "language":             str,   e.g. "English"
            "language_code":        str,   e.g. "en"
            "is_generated":         bool,
            "is_translatable":      bool,
            "translation_languages": list[str] | None,
            "snippet_count":        int,
            "duration_seconds":     float,
        }
    """
    fetched: FetchedTranscript = transcript.fetch()

    duration = 0.0
    if fetched.snippets:
        last = fetched.snippets[-1]
        first = fetched.snippets[0]
        duration = (last.start + last.duration) - first.start

    return {
        "video_id":              transcript.video_id,
        "language":              transcript.language,
        "language_code":         transcript.language_code,
        "is_generated":          transcript.is_generated,
        "is_translatable":       transcript.is_translatable,
        "translation_languages": (
            [lang["language_code"] for lang in transcript.translation_languages]
            if transcript.is_translatable else None
        ),
        "snippet_count":         len(fetched),
        "duration_seconds":      round(duration, 2),
    }


# ── 3. get_snippets ───────────────────────────────────────────────────────────

def get_snippets(transcript: Transcript) -> dict:
    """
    Fetch transcript snippets and return a timestamp-indexed structure.

    Also stores the joined, cleaned full text under the key "_full_text"
    for direct consumption by nlp.py.

    Returns:
        {
            "_full_text": str,          # cleaned joined string for spaCy
            "_language_code": str,      # e.g. "en"
            "_snippet_count": int,
            0.0: {"end": 3.5, "text": "So companies had to develop"},
            3.5: {"end": 7.1, "text": "permanent photographic records"},
            ...
        }

    Note:
        Timestamps are floats (seconds from video start).
        "_full_text" is the only key nlp.py should read.
        Timestamp keys are provided for future features (e.g. timestamped
        card context, confidence interval on sentence boundaries).
    """
    fetched: FetchedTranscript = transcript.fetch()

    result: dict = {
        "_language_code": fetched.language_code,
        "_snippet_count":  len(fetched),
    }

    texts = []
    for snippet in fetched:
        clean_text = _clean(snippet.text)
        if clean_text:
            result[snippet.start] = {
                "end":  round(snippet.start + snippet.duration, 3),
                "text": clean_text,
            }
            texts.append(clean_text)

    result["_full_text"] = " ".join(texts)

    return result