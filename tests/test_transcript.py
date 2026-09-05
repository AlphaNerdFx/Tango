"""
test_transcript.py

Unit tests use mocking, no real YouTube calls.
Integration tests (marked) hit YouTube and require network access.
Run unit tests only:    pytest tests/test_transcript.py -m "not integration"
Run all including live: pytest tests/test_transcript.py
"""

from unittest.mock import MagicMock, patch, PropertyMock
import pytest

import pipeline.transcript as transcript
from pipeline.transcript import get_transcript, get_properties, get_snippets


def _make_snippet(text: str, start: float, duration: float):
    s = MagicMock()
    s.text     = text
    s.start    = start
    s.duration = duration
    return s


def _make_fetched(snippets, video_id="LV_NoD2M54w", language="English",
                  language_code="en", is_generated=False):
    f = MagicMock()
    f.snippets      = snippets
    f.video_id      = video_id
    f.language      = language
    f.language_code = language_code
    f.is_generated  = is_generated
    f.__iter__      = lambda self: iter(self.snippets)
    f.__len__       = lambda self: len(self.snippets)
    return f


def _make_transcript(fetched, video_id="LV_NoD2M54w", language="English",
                     language_code="en", is_generated=False,
                     is_translatable=True):
    t = MagicMock()
    t.video_id             = video_id
    t.language             = language
    t.language_code        = language_code
    t.is_generated         = is_generated
    t.is_translatable      = is_translatable
    t.translation_languages = [{"language_code": "de"}, {"language_code": "fr"}]
    t.fetch.return_value   = fetched
    return t


SAMPLE_SNIPPETS = [
    _make_snippet("So companies had to develop",  0.0,  3.5),
    _make_snippet("permanent photographic records", 3.5, 3.6),
    _make_snippet("[Music]",                       7.1,  2.0),   # should be stripped
    _make_snippet("gave &amp; permanent",          9.1,  2.5),   # HTML entity
]

SAMPLE_FETCHED     = _make_fetched(SAMPLE_SNIPPETS)
SAMPLE_TRANSCRIPT  = _make_transcript(SAMPLE_FETCHED)

class TestGetTranscript:

    @patch("pipeline.transcript.YouTubeTranscriptApi")
    def test_returns_transcript_object(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.list.return_value.find_transcript.return_value = SAMPLE_TRANSCRIPT
        result = get_transcript("LV_NoD2M54w")
        assert result.video_id == "LV_NoD2M54w"

    @patch("pipeline.transcript.YouTubeTranscriptApi")
    def test_default_language_is_english(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.list.return_value.find_transcript.return_value = SAMPLE_TRANSCRIPT
        get_transcript("LV_NoD2M54w")
        mock_api.list.return_value.find_transcript.assert_called_once_with(["en"])

    @patch("pipeline.transcript.YouTubeTranscriptApi")
    def test_custom_language_list(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.list.return_value.find_transcript.return_value = SAMPLE_TRANSCRIPT
        get_transcript("LV_NoD2M54w", languages=["de", "en"])
        mock_api.list.return_value.find_transcript.assert_called_once_with(["de", "en"])

    @patch("pipeline.transcript.YouTubeTranscriptApi")
    def test_raises_on_video_unavailable(self, mock_api_cls):
        from youtube_transcript_api._errors import VideoUnavailable
        mock_api_cls.return_value.list.side_effect = VideoUnavailable("LV_NoD2M54w")
        with pytest.raises(VideoUnavailable):
            get_transcript("LV_NoD2M54w")

    @patch("pipeline.transcript.YouTubeTranscriptApi")
    def test_raises_on_transcripts_disabled(self, mock_api_cls):
        from youtube_transcript_api._errors import TranscriptsDisabled
        mock_api_cls.return_value.list.side_effect = TranscriptsDisabled("LV_NoD2M54w")
        with pytest.raises(TranscriptsDisabled):
            get_transcript("LV_NoD2M54w")

    @patch("pipeline.transcript.YouTubeTranscriptApi")
    def test_raises_on_no_transcript_found(self, mock_api_cls):
        from youtube_transcript_api._errors import NoTranscriptFound
        mock_api = mock_api_cls.return_value
        mock_api.list.return_value.__iter__ = lambda self: iter([])
        mock_api.list.return_value.find_transcript.side_effect = NoTranscriptFound(
            "LV_NoD2M54w", ["en"], []
        )
        with pytest.raises(NoTranscriptFound):
            get_transcript("LV_NoD2M54w")

    @patch("pipeline.transcript.YouTubeTranscriptApi")
    def test_raises_on_ip_blocked(self, mock_api_cls):
        from youtube_transcript_api._errors import IpBlocked
        mock_api_cls.return_value.list.side_effect = IpBlocked("LV_NoD2M54w")
        with pytest.raises(IpBlocked):
            get_transcript("LV_NoD2M54w")

class TestGetProperties:
    def test_returns_dict(self):
        props = get_properties(SAMPLE_TRANSCRIPT)
        assert isinstance(props, dict)

    def test_correct_video_id(self):
        props = get_properties(SAMPLE_TRANSCRIPT)
        assert props["video_id"] == "LV_NoD2M54w"

    def test_correct_language_code(self):
        props = get_properties(SAMPLE_TRANSCRIPT)
        assert props["language_code"] == "en"

    def test_snippet_count_matches(self):
        props = get_properties(SAMPLE_TRANSCRIPT)
        assert props["snippet_count"] == len(SAMPLE_SNIPPETS)

    def test_duration_is_float(self):
        props = get_properties(SAMPLE_TRANSCRIPT)
        assert isinstance(props["duration_seconds"], float)

    def test_duration_calculated_correctly(self):
        # duration = (last.start + last.duration) - first.start
        # = (9.1 + 2.5) - 0.0 = 11.6
        props = get_properties(SAMPLE_TRANSCRIPT)
        assert props["duration_seconds"] == pytest.approx(11.6, rel=1e-3)

    def test_translation_languages_none_when_not_translatable(self):
        t = _make_transcript(SAMPLE_FETCHED, is_translatable=False)
        props = get_properties(t)
        assert props["translation_languages"] is None

    def test_translation_languages_list_when_translatable(self):
        props = get_properties(SAMPLE_TRANSCRIPT)
        assert isinstance(props["translation_languages"], list)
        assert "de" in props["translation_languages"]


class TestGetSnippets:

    def test_returns_dict(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        assert isinstance(result, dict)

    def test_full_text_key_present(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        assert "_full_text" in result

    def test_full_text_is_string(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        assert isinstance(result["_full_text"], str)

    def test_annotation_tags_stripped(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        assert "[Music]" not in result["_full_text"]

    def test_html_entities_decoded(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        assert "&amp;" not in result["_full_text"]
        assert "&" in result["_full_text"]

    def test_timestamp_keys_are_floats(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        timestamp_keys = [k for k in result if isinstance(k, float)]
        assert len(timestamp_keys) > 0

    def test_each_timestamp_has_end_and_text(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        for key, val in result.items():
            if isinstance(key, float):
                assert "end" in val
                assert "text" in val

    def test_snippet_count_key_present(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        assert "_snippet_count" in result

    def test_empty_snippets_after_cleaning_not_indexed(self):
        """[Music]-only snippet should not appear as a timestamp key."""
        result = get_snippets(SAMPLE_TRANSCRIPT)
        # 7.1 is the [Music] snippet, it should be absent after cleaning
        assert 7.1 not in result

    def test_full_text_not_empty(self):
        result = get_snippets(SAMPLE_TRANSCRIPT)
        assert len(result["_full_text"]) > 0


@pytest.mark.integration
class TestIntegration:

    def test_real_video_transcript(self):
        t = get_transcript("LV_NoD2M54w")
        assert t.video_id == "LV_NoD2M54w"

    def test_real_video_properties(self):
        t = get_transcript("LV_NoD2M54w")
        props = get_properties(t)
        assert props["snippet_count"] > 0
        assert props["duration_seconds"] > 0

    def test_real_video_snippets(self):
        t = get_transcript("LV_NoD2M54w")
        snippets = get_snippets(t)
        assert len(snippets["_full_text"]) > 100

class TestProxyConfiguration:
    """
    v0.11.0. `_build_proxy` had no test at all: every test in this file
    patches `YouTubeTranscriptApi` wholesale, so only the `return None`
    branch ever ran. ARCHITECTURE 2771 names "proxy and fetch-failure paths"
    as the uncovered region of this module.

    The priority order is the part worth pinning. Issue #8 established the
    generic proxy as the recommendation and Webshare's free tier as
    measurably harmful, and every document says so, but the code preferred
    Webshare, so a machine with both set silently used the one the docs warn
    against.
    """

    @staticmethod
    def _with(http=None, https=None, user=None, password=None):
        import pipeline.config as cfg
        return (patch.object(cfg, "PROXY_HTTP_URL", http),
                patch.object(cfg, "PROXY_HTTPS_URL", https),
                patch.object(cfg, "WEBSHARE_USERNAME", user),
                patch.object(cfg, "WEBSHARE_PASSWORD", password))

    def _build(self, **kw):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for ctx in self._with(**kw):
                stack.enter_context(ctx)
            return transcript._build_proxy()

    def test_no_settings_means_no_proxy(self):
        assert self._build() is None

    def test_generic_urls_build_a_generic_config(self):
        from youtube_transcript_api.proxies import GenericProxyConfig
        assert type(self._build(http="http://host:1")) is GenericProxyConfig

    def test_webshare_credentials_build_a_webshare_config(self):
        from youtube_transcript_api.proxies import WebshareProxyConfig
        assert type(self._build(user="u", password="p")) is WebshareProxyConfig

    def test_generic_wins_when_both_are_set(self):
        # The whole point. Reversing this puts the provider the project
        # documents as harmful ahead of the one it recommends.
        #
        # `type(...) is`, not isinstance. WebshareProxyConfig SUBCLASSES
        # GenericProxyConfig, so isinstance is true for both and this test
        # passed with the priority reversed. A mutation caught that; the
        # idiomatic assertion was the wrong one here.
        from youtube_transcript_api.proxies import GenericProxyConfig
        got = self._build(http="http://host:1", user="u", password="p")
        assert type(got) is GenericProxyConfig

    def test_half_a_webshare_credential_is_not_enough(self):
        assert self._build(user="u") is None
        assert self._build(password="p") is None

    def test_the_proxy_classes_come_from_their_public_module(self):
        # They used to be imported from `_errors`, which re-exports them
        # only incidentally for its own use. A patch release could stop
        # doing that. The package root exports neither, so `proxies` is the
        # public path.
        from youtube_transcript_api.proxies import (  # noqa: F401
            GenericProxyConfig,
            WebshareProxyConfig,
        )
        import youtube_transcript_api as api

        assert not hasattr(api, "WebshareProxyConfig")


class TestBlockedMessageKnowsAboutTheProxy:
    """
    v0.11.0. A blocked user was always told "no proxy is configured", even
    with one configured, because the handler rebuilt the exception and the
    library resets its proxy reference in `__init__`.

    Letting the library's own exception through is not the fix. All three of
    its messages carry a Webshare affiliate referral link, and the Webshare
    one asks the reader to buy through it. This project recommends no
    provider and documents that Webshare's free tier is harmful.
    """

    @staticmethod
    def _raised(proxy):
        return transcript._ProxyAwareIpBlocked("abc12345678", proxy)

    def test_without_a_proxy_it_says_so(self):
        assert "No proxy is configured" in self._raised(None).cause

    def test_with_a_generic_proxy_it_does_not_claim_there_is_none(self):
        from youtube_transcript_api.proxies import GenericProxyConfig
        cause = self._raised(GenericProxyConfig(http_url="http://h:1")).cause
        assert "No proxy is configured" not in cause
        assert "even through the configured proxy" in cause

    def test_webshare_gets_the_warning_the_project_actually_measured(self):
        from youtube_transcript_api.proxies import WebshareProxyConfig
        cause = self._raised(WebshareProxyConfig(proxy_username="u", proxy_password="p")).cause
        assert "free tier" in cause

    @pytest.mark.parametrize("proxy_kind", ["none", "generic", "webshare"])
    def test_no_affiliate_link_reaches_the_user(self, proxy_kind):
        from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig
        proxy = {"none": None,
                 "generic": GenericProxyConfig(http_url="http://h:1"),
                 "webshare": WebshareProxyConfig(proxy_username="u", proxy_password="p")}[proxy_kind]
        text = str(self._raised(proxy)) + self._raised(proxy).cause
        assert "referral_code" not in text
        assert "affiliate" not in text.lower()

    def test_it_is_still_an_IpBlocked(self):
        # Callers and the existing test in this file catch IpBlocked. Only
        # the wording changed, not the type.
        from youtube_transcript_api._errors import IpBlocked
        assert isinstance(self._raised(None), IpBlocked)

    def test_the_call_site_actually_passes_the_proxy_through(self):
        # The tests above build the exception directly, so none of them
        # reaches the `raise` in get_transcript(). A mutation that passed
        # None there left every one of them green while reintroducing the
        # exact bug: a proxied user told they have no proxy.
        from youtube_transcript_api._errors import IpBlocked
        from youtube_transcript_api.proxies import GenericProxyConfig

        proxy = GenericProxyConfig(http_url="http://host:1")
        with patch.object(transcript, "_build_proxy", return_value=proxy), \
             patch.object(transcript, "YouTubeTranscriptApi") as api:
            api.return_value.list.side_effect = IpBlocked("abc12345678")
            with pytest.raises(IpBlocked) as exc:
                transcript.get_transcript("abc12345678")
        assert "No proxy is configured" not in exc.value.cause
        assert "even through the configured proxy" in exc.value.cause
