"""
test_media.py

Pronunciation audio download and caching (ADR-009, v0.5.2).

All network calls are mocked. Nothing here touches the real MEDIA_DIR --
every test redirects it to tmp_path, so a run cannot pollute the cache or
depend on what a previous run left there.
"""

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


def _response(content=b"ID3fakeaudio", content_type="audio/mpeg", status=200):
    r = MagicMock()
    r.content = content
    r.headers = {"content-type": content_type}
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
