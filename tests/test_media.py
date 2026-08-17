"""
test_media.py

Pronunciation audio download and caching (ADR-009, v0.5.2).

All network calls are mocked. Nothing here touches the real MEDIA_DIR --
every test redirects it to tmp_path, so a run cannot pollute the cache or
depend on what a previous run left there.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import pipeline.media as media


@pytest.fixture(autouse=True)
def tmp_media_dir(tmp_path, monkeypatch):
    """Redirect the audio cache for every test in this module."""
    d = tmp_path / "media"
    monkeypatch.setattr(media, "MEDIA_DIR", d)
    return d


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    """
    Remove the download pacing for every test that does not target it.

    The real limiter is global and deliberately slow -- roughly one request a
    second after a short burst. Left in place it would make this module take
    minutes and, worse, make the timing tests below depend on how many other
    tests ran first.
    """
    monkeypatch.setattr(media, "_LIMITER", media._RateLimiter(rate=0, burst=1))


def _response(content=b"ID3fakeaudio", content_type="audio/mpeg", status=200, retry_after=None):
    r = MagicMock()
    r.content = content
    r.status_code = status
    r.headers = {"content-type": content_type}
    if retry_after is not None:
        r.headers["retry-after"] = retry_after
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    return r


COMMONS = "https://upload.wikimedia.org/wikipedia/commons/transcoded/7/7e/De-Haus.ogg/De-Haus.ogg.mp3"


class TestMediaFilename:

    def test_is_namespaced_to_this_project(self):
        # Anki's media folder is one flat namespace shared with every other
        # deck the user has. An unprefixed "house-us.mp3" could overwrite
        # somebody else's file.
        assert media.media_filename("haus", "de", COMMONS).startswith("tango-")

    def test_includes_the_language(self):
        # A cognate spelled the same in two languages must not collide on one
        # filename and give a German card the French recording.
        de = media.media_filename("train", "de", COMMONS)
        fr = media.media_filename("train", "fr", COMMONS)
        assert de != fr

    def test_is_deterministic(self):
        # Re-running a video, or meeting the word in a second one, must reuse
        # the cached file rather than download it again.
        assert media.media_filename("haus", "de", COMMONS) == \
               media.media_filename("haus", "de", COMMONS)

    def test_takes_the_final_extension_from_a_transcoded_url(self):
        # Wikimedia's transcoded URLs end ".ogg.mp3" and really are MP3.
        # Taking ".ogg" would tell Anki the wrong thing about the bytes.
        assert media.media_filename("haus", "de", COMMONS).endswith(".mp3")

    def test_unsafe_characters_are_stripped(self):
        name = media.media_filename("l'été/../x", "fr", COMMONS)
        assert "/" not in name and ".." not in name and "'" not in name

    @pytest.mark.parametrize("language,words", [
        ("ru", ["дом", "стол", "книга"]),
        ("zh", ["家", "学校", "水"]),
        ("ja", ["日本", "学生", "本"]),
    ])
    def test_non_latin_words_get_distinct_filenames(self, language, words):
        """
        The bug this exists for, found by mutation of the sanitiser.

        An ASCII-only character class mapped EVERY Cyrillic and CJK lemma to
        the same string -- "дом", "стол" and "книга" all became
        "tango-ru.mp3". One recording would then have been shared by every
        card in the language: the first word downloaded wins and every other
        card plays the wrong word, silently. It hits exactly the languages
        this project supports an index or a spaCy model for.
        """
        names = [media.media_filename(w, language, COMMONS) for w in words]
        assert len(set(names)) == len(words), f"collision: {names}"

    def test_two_lemmas_differing_only_in_stripped_characters_still_differ(self):
        # Sanitising cannot guarantee uniqueness on its own, which is why the
        # name carries a hash of the original lemma.
        a = media.media_filename("co-operate", "en", COMMONS)
        b = media.media_filename("co operate", "en", COMMONS)
        assert a != b

    def test_unknown_extension_defaults_to_mp3(self):
        assert media.media_filename("x", "en", "https://e.invalid/a").endswith(".mp3")


class TestFetchAudio:

    def test_writes_the_file_and_returns_its_path(self, tmp_media_dir):
        with patch("pipeline.media.requests.get", return_value=_response()):
            path = media.fetch_audio(COMMONS, "haus", "de")
        assert path is not None
        assert path.read_bytes() == b"ID3fakeaudio"
        assert path.parent == tmp_media_dir

    def test_sends_an_identifying_user_agent(self):
        # Wikimedia answers the default python-requests agent with 403, per
        # their User-Agent policy. Measured: the exact URL curl fetches
        # happily returned 403 unheadered, so every card lost its audio.
        with patch("pipeline.media.requests.get", return_value=_response()) as get:
            media.fetch_audio(COMMONS, "haus", "de")
        assert "User-Agent" in get.call_args[1]["headers"]
        assert get.call_args[1]["headers"]["User-Agent"]

    def test_a_cached_file_is_not_downloaded_again(self, tmp_media_dir):
        tmp_media_dir.mkdir(parents=True)
        (tmp_media_dir / media.media_filename("haus", "de", COMMONS)).write_bytes(b"x")
        with patch("pipeline.media.requests.get") as get:
            path = media.fetch_audio(COMMONS, "haus", "de")
        assert path is not None
        get.assert_not_called()

    def test_a_network_error_returns_none_rather_than_raising(self):
        # One missing recording must cost one card its audio, never the run:
        # by this point the expensive work is already done.
        with patch("pipeline.media.requests.get",
                   side_effect=requests.ConnectionError("down")):
            assert media.fetch_audio(COMMONS, "haus", "de") is None

    def test_an_http_error_returns_none(self):
        with patch("pipeline.media.requests.get", return_value=_response(status=502)):
            assert media.fetch_audio(COMMONS, "haus", "de") is None

    def test_a_200_that_is_not_audio_is_rejected(self, tmp_media_dir):
        # The failure that would otherwise put an unplayable text file into
        # the user's collection: dictionaryapi.dev returns its 502 as a
        # 16-byte text/plain body, which is still a 200 to some proxies.
        with patch("pipeline.media.requests.get",
                   return_value=_response(b"error code: 502", "text/plain; charset=UTF-8")):
            assert media.fetch_audio(COMMONS, "haus", "de") is None
        assert not tmp_media_dir.exists() or not list(tmp_media_dir.iterdir())

    def test_an_empty_body_is_rejected(self):
        with patch("pipeline.media.requests.get", return_value=_response(b"")):
            assert media.fetch_audio(COMMONS, "haus", "de") is None

    def test_no_url_is_not_an_error(self):
        assert media.fetch_audio("", "haus", "de") is None

    def test_an_unwritable_cache_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(media.Path, "write_bytes",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
        with patch("pipeline.media.requests.get", return_value=_response()):
            assert media.fetch_audio(COMMONS, "haus", "de") is None


class TestRateLimiting:
    """
    The bug that shipped in v0.5.2, and the reason it was invisible.

    Measured against upload.wikimedia.org: about ten requests succeed, then
    every one after that is a 429 carrying `Retry-After: 11`. An 8-worker pool
    drained that in under a second, and because `fetch_audio` treated a 429 as
    a permanent failure, a real 406-card run embedded 13 recordings and handed
    the other 364 cards a link. Nothing errored, the package was valid, and
    every one of those URLs downloaded fine when tried on its own.
    """

    def test_requests_beyond_the_burst_are_paced(self):
        # Fails if pacing is dropped: without it six acquires cost nothing.
        limiter = media._RateLimiter(rate=25, burst=1)   # 40ms apart
        start = time.monotonic()
        for _ in range(6):
            limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15, f"not paced: {elapsed:.3f}s for 5 spaced slots"
        assert elapsed < 3.0, f"pacing is far slower than configured: {elapsed:.3f}s"

    def test_the_burst_is_not_paced(self):
        # The other half of the pair. A limiter that simply slept on every
        # call would satisfy the test above, and would make a five-word run
        # pay for a rate limit it was never going to hit.
        limiter = media._RateLimiter(rate=25, burst=6)
        start = time.monotonic()
        for _ in range(6):
            limiter.acquire()
        assert time.monotonic() - start < 0.10

    def test_a_rate_limited_request_is_retried_and_succeeds(self):
        with patch("pipeline.media.time.sleep") as slept, \
             patch("pipeline.media.requests.get",
                   side_effect=[_response(status=429, retry_after="11"), _response()]) as get:
            path = media.fetch_audio(COMMONS, "haus", "de")
        assert path is not None, "gave up on a 429 the server invited us to retry"
        assert get.call_count == 2
        slept.assert_called_once_with(11.0)

    def test_a_404_is_not_retried(self):
        # Matched pair with the test above. Retrying everything would satisfy
        # that one too, and would then wait out MEDIA_MAX_RETRIES on every
        # word the source genuinely does not have -- which is many of them.
        with patch("pipeline.media.time.sleep"), \
             patch("pipeline.media.requests.get", return_value=_response(status=404)) as get:
            assert media.fetch_audio(COMMONS, "nosuchword", "de") is None
        assert get.call_count == 1

    def test_a_persistent_429_gives_up_rather_than_looping(self):
        with patch("pipeline.media.time.sleep"), \
             patch("pipeline.media.requests.get",
                   return_value=_response(status=429, retry_after="11")) as get:
            assert media.fetch_audio(COMMONS, "haus", "de") is None
        assert get.call_count == media.MEDIA_MAX_RETRIES + 1

    def test_retries_are_paced_too(self, monkeypatch):
        # Retrying outside the limiter would walk straight back into the
        # rate limit that caused the retry.
        limiter = MagicMock()
        monkeypatch.setattr(media, "_LIMITER", limiter)
        with patch("pipeline.media.time.sleep"), \
             patch("pipeline.media.requests.get",
                   return_value=_response(status=429, retry_after="11")):
            media.fetch_audio(COMMONS, "haus", "de")
        assert limiter.acquire.call_count == media.MEDIA_MAX_RETRIES + 1

    def test_a_cached_file_costs_no_slot(self, tmp_media_dir, monkeypatch):
        # The cache is what makes a re-run instant; consuming a rate-limit
        # slot for a file already on disk would throw that away.
        limiter = MagicMock()
        monkeypatch.setattr(media, "_LIMITER", limiter)
        tmp_media_dir.mkdir(parents=True)
        (tmp_media_dir / media.media_filename("haus", "de", COMMONS)).write_bytes(b"x")
        media.fetch_audio(COMMONS, "haus", "de")
        limiter.acquire.assert_not_called()


class TestRetryAfter:

    def test_uses_the_period_the_server_asked_for(self):
        assert media._retry_after(_response(status=429, retry_after="11")) == 11.0

    def test_an_absurd_period_is_capped(self):
        # A server asking for an hour must not stall a run that has already
        # done the expensive work.
        assert media._retry_after(_response(status=429, retry_after="3600")) == \
               media.MEDIA_MAX_RETRY_WAIT

    def test_a_missing_header_falls_back_to_the_default(self):
        assert media._retry_after(_response(status=429), default=4.0) == 4.0

    def test_an_http_date_is_not_mistaken_for_seconds(self):
        # Retry-After may carry a date. Parsing that as a float fails, and
        # falling back beats waiting a nonsensical number of seconds.
        r = _response(status=429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT")
        assert media._retry_after(r, default=4.0) == 4.0
