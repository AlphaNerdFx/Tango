"""
Three responsibilities:
  1. get_transcript()    fetch a Transcript object for a video ID
  2. get_properties()    extract metadata from a fetched transcript
  3. get_snippets()      build a timestamp-indexed dict of text per language

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
)

# The proxy classes come from their own module, not from _errors. _errors
# does re-export them, which is how this import used to be written, but only
# because it imports them for its own use: they are not part of that
# module's interface and a patch release could stop re-exporting them
# without warning. `youtube_transcript_api.proxies` is where they live. The
# package root exports neither, so this is the public path.
from youtube_transcript_api.proxies import (
    GenericProxyConfig,
    WebshareProxyConfig,
)

from pipeline import TangoError
from youtube_transcript_api._transcripts import FetchedTranscript, Transcript

import logging
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
    Return a proxy config from the environment, or None for no proxy.

    Priority:
        1. Generic   (PROXY_HTTP_URL and/or PROXY_HTTPS_URL)
        2. Webshare  (WEBSHARE_USERNAME + WEBSHARE_PASSWORD)
        3. No proxy

    Generic first, deliberately. This order used to be the other way round,
    which contradicted the project's own documented position: issue #8
    established that Webshare's free tier makes things measurably worse
    (transcript extraction failed with repeated 429s through it and
    succeeded without it), and every document was changed to put the generic
    variables first and recommend no provider. The code kept preferring
    Webshare, so on a machine with both set it silently chose the one the
    docs warn against.
    """
    from pipeline.config import (
        PROXY_HTTP_URL,
        PROXY_HTTPS_URL,
        WEBSHARE_PASSWORD as ws_pass,
        WEBSHARE_USERNAME as ws_user,
    )

    if PROXY_HTTP_URL or PROXY_HTTPS_URL:
        return GenericProxyConfig(http_url=PROXY_HTTP_URL, https_url=PROXY_HTTPS_URL)

    if ws_user and ws_pass:
        return WebshareProxyConfig(proxy_username=ws_user, proxy_password=ws_pass)

    return None


class _ProxyAwareIpBlocked(IpBlocked, TangoError):
    """
    An IP block that knows whether a proxy was in use.

    Two problems with the plain library exception, and they pull in
    opposite directions.

    Re-raising it as `IpBlocked(video_id)` resets the proxy config to None,
    so a user who *has* configured a proxy is told they have not. That is
    the bug this fixes.

    Letting the library's own exception through instead is not the answer:
    all three of its messages carry a Webshare affiliate referral link, and
    the Webshare one asks the reader to buy through it to support the
    project. This project recommends no provider and documents that
    Webshare's free tier is harmful, so shipping that text would contradict
    its own position, on someone else's behalf.

    So the type stays `IpBlocked`, which is what callers and tests expect,
    and only the wording is ours.

    It is also a `TangoError`, because it is a failure this project raises
    deliberately with a message saying what to do about it. Without that,
    the entry point would class an IP block as a bug and send the user to
    open an issue about YouTube's rate limiting. The scan in
    test_hard_constraints.py caught this the moment the class was added.
    """

    def __init__(self, video_id: str, proxy_config: Optional[object]) -> None:
        super().__init__(video_id)
        self._tango_proxy = proxy_config

    @property
    def cause(self) -> str:
        if self._tango_proxy is None:
            return (
                "YouTube is blocking requests from this IP address.\n"
                "  No proxy is configured. Waiting is usually enough: these "
                "blocks are temporary and per-IP.\n"
                "  If it persists, PROXY_HTTP_URL and PROXY_HTTPS_URL in "
                ".env accept a proxy you already trust. This project "
                "recommends no provider, and a free datacentre proxy "
                "measurably made this worse when tested."
            )
        if isinstance(self._tango_proxy, WebshareProxyConfig):
            return (
                "YouTube is blocking requests even through the Webshare "
                "proxy.\n"
                "  Webshare's free tier is confirmed to make this worse "
                "rather than better, because its datacentre IPs are blocked "
                "more aggressively than residential ones.\n"
                "  Try without the proxy, or use a residential proxy you "
                "already trust."
            )
        return (
            "YouTube is blocking requests even through the configured "
            "proxy.\n"
            "  A proxy only substitutes one IP for another, and that IP can "
            "be blocked too.\n"
            "  Try without it to see whether your own address still works."
        )


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
    proxy = _build_proxy()
    api = YouTubeTranscriptApi(proxy_config=proxy)

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
    except (IpBlocked, RequestBlocked) as exc:
        raise _ProxyAwareIpBlocked(video_id, proxy) from exc
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
    The fetched result is NOT cached here, if you need snippets too,
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