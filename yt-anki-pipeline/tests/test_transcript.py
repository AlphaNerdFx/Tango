"""
test_transcript.py

Unit tests use mocking — no real YouTube calls.
Integration tests (marked) hit YouTube and require network access.
Run unit tests only:    pytest tests/test_transcript.py -m "not integration"
Run all including live: pytest tests/test_transcript.py
"""

from unittest.mock import MagicMock, patch, PropertyMock
import pytest

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
        # 7.1 is the [Music] snippet — it should be absent after cleaning
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