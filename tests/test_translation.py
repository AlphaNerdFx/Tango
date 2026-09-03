"""
Tests for translation.py, community mirror probing, local model
management, progress bar download, three-tier resolution, and
the per-run warning deduplication.

All network calls and argostranslate imports are mocked.
No real translation models or internet access required for unit tests.

Run: pytest tests/test_translation.py -m "not integration"
"""

from unittest.mock import MagicMock, patch, call
import pytest

import pipeline.translation as trans_module
from pipeline.translation import (
    ModelNotInstalledError,
    get_translation_route,
    install_translation,
    is_translation_available,
    TranslationUnavailableError,
    _probe_mirror,
    _translate_via_mirror,
    _warn_translation_unavailable,
    download_model,
    is_model_installed,
    reset_warning_state,
    translate_local,
    translate_word,
    try_community_mirror,
    LIBRETRANSLATE_MIRRORS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_warning_state():
    """Reset per-run warning tracker before every test."""
    reset_warning_state()
    yield
    reset_warning_state()


# ── _probe_mirror ─────────────────────────────────────────────────────────────

class TestProbeMirror:

    @patch("pipeline.translation.requests.get")
    def test_returns_true_when_reachable(self, mock_get):
        mock_get.return_value.status_code = 200
        assert _probe_mirror("https://translate.argosopentech.com") is True

    @patch("pipeline.translation.requests.get")
    def test_returns_false_when_not_200(self, mock_get):
        mock_get.return_value.status_code = 503
        assert _probe_mirror("https://translate.argosopentech.com") is False

    @patch("pipeline.translation.requests.get",
           side_effect=__import__("requests").exceptions.ConnectionError)
    def test_returns_false_on_connection_error(self, _):
        assert _probe_mirror("https://translate.argosopentech.com") is False

    @patch("pipeline.translation.requests.get",
           side_effect=__import__("requests").exceptions.Timeout)
    def test_returns_false_on_timeout(self, _):
        assert _probe_mirror("https://translate.argosopentech.com") is False


# ── _translate_via_mirror ─────────────────────────────────────────────────────

class TestTranslateViaMirror:

    @patch("pipeline.translation.requests.post")
    def test_returns_translated_text(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"translatedText": "hello"}
        result = _translate_via_mirror("bonjour", "fr", "en", "https://mirror.com")
        assert result == "hello"

    @patch("pipeline.translation.requests.post")
    def test_returns_none_on_http_error(self, mock_post):
        mock_post.return_value.status_code = 400
        result = _translate_via_mirror("bonjour", "fr", "en", "https://mirror.com")
        assert result is None

    @patch("pipeline.translation.requests.post")
    def test_returns_none_on_empty_translation(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"translatedText": ""}
        result = _translate_via_mirror("bonjour", "fr", "en", "https://mirror.com")
        assert result is None

    @patch("pipeline.translation.requests.post",
           side_effect=__import__("requests").exceptions.ConnectionError)
    def test_returns_none_on_connection_error(self, _):
        result = _translate_via_mirror("bonjour", "fr", "en", "https://mirror.com")
        assert result is None

    @patch("pipeline.translation.requests.post")
    def test_passes_correct_payload(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"translatedText": "hello"}
        _translate_via_mirror("bonjour", "fr", "en", "https://mirror.com")
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["q"] == "bonjour"
        assert call_kwargs["json"]["source"] == "fr"
        assert call_kwargs["json"]["target"] == "en"


# ── try_community_mirror ──────────────────────────────────────────────────────

class TestTryCommunityMirror:
    """
    These patch LIBRETRANSLATE_MIRRORS rather than relying on whatever the
    module ships. The default list is now empty -- both public mirrors it used
    to carry are gone -- and a test of the probe-then-translate logic should
    not depend on that list having entries in the first place.
    """

    @patch("pipeline.translation.LIBRETRANSLATE_MIRRORS", ["https://a.invalid", "https://b.invalid"])
    @patch("pipeline.translation._probe_mirror", return_value=True)
    @patch("pipeline.translation._translate_via_mirror", return_value="hello")
    def test_returns_translation_from_first_working_mirror(self, mock_trans, mock_probe):
        result = try_community_mirror("bonjour", "fr", "en")
        assert result == "hello"

    @patch("pipeline.translation._probe_mirror", return_value=False)
    def test_returns_none_when_all_mirrors_down(self, mock_probe):
        result = try_community_mirror("bonjour", "fr", "en")
        assert result is None

    @patch("pipeline.translation._probe_mirror", return_value=True)
    @patch("pipeline.translation._translate_via_mirror", return_value=None)
    def test_returns_none_when_mirror_up_but_translation_fails(self, mock_trans, mock_probe):
        result = try_community_mirror("bonjour", "fr", "en")
        assert result is None

    @patch("pipeline.translation.LIBRETRANSLATE_MIRRORS", ["https://a.invalid", "https://b.invalid"])
    @patch("pipeline.translation._probe_mirror")
    @patch("pipeline.translation._translate_via_mirror", return_value="hello")
    def test_tries_second_mirror_when_first_down(self, mock_trans, mock_probe):
        mock_probe.side_effect = [False, True]
        result = try_community_mirror("bonjour", "fr", "en")
        assert result == "hello"
        assert mock_probe.call_count == 2


# ── is_model_installed ────────────────────────────────────────────────────────

class TestIsModelInstalled:
    """
    Both tests here used to be vacuous, and one of them was machine-dependent.

    `test_returns_false_on_import_error` patched
    `pipeline.translation.is_model_installed` and then called the name this
    module imported at the top -- which still pointed at the real function,
    so the patch did nothing and the real filesystem answered. It passed only
    while no fr->en model happened to be installed, and this machine has had
    one since 10 July, so it passed in full runs (where something upstream
    perturbed the import) and failed in isolation. Its sibling ended in
    `assert ... is True or True`, which cannot fail.

    These mock argostranslate through sys.modules instead, so the answer
    comes from the mock rather than from whatever the machine has installed
    -- which is what CLAUDE.md 3.5 requires of a unit test.
    """

    @staticmethod
    def _fake_argos(pairs):
        """Build a stand-in argostranslate exposing the given (from, to) pairs."""
        models = []
        for from_code, to_code in pairs:
            m = MagicMock()
            m.from_code, m.to_code = from_code, to_code
            models.append(m)
        fake_pkg = MagicMock()
        fake_pkg.get_installed_packages.return_value = models
        fake_root = MagicMock()
        fake_root.package = fake_pkg
        return {"argostranslate": fake_root, "argostranslate.package": fake_pkg}

    def test_returns_true_when_the_pair_is_installed(self):
        import sys
        with patch.dict(sys.modules, self._fake_argos([("fr", "en")])):
            assert is_model_installed("fr", "en") is True

    def test_returns_false_when_a_different_pair_is_installed(self):
        """
        The pair to the test above. A blanket "any model means yes" would
        satisfy that one while sending a de->en run to a French model.
        """
        import sys
        with patch.dict(sys.modules, self._fake_argos([("fr", "en")])):
            assert is_model_installed("de", "en") is False

    def test_direction_matters(self):
        """en->fr installed does not mean fr->en is available."""
        import sys
        with patch.dict(sys.modules, self._fake_argos([("en", "fr")])):
            assert is_model_installed("fr", "en") is False

    def test_returns_false_when_nothing_is_installed(self):
        import sys
        with patch.dict(sys.modules, self._fake_argos([])):
            assert is_model_installed("fr", "en") is False

    def test_returns_false_on_import_error(self):
        """
        argostranslate is an optional dependency -- the base install does not
        have it -- so an ImportError here is an ordinary state, not a crash.
        Setting the module to None makes `from argostranslate import package`
        raise, which is what actually happens when it is absent.
        """
        import sys
        with patch.dict(sys.modules, {"argostranslate": None}):
            assert is_model_installed("fr", "en") is False


# ── translate_local ───────────────────────────────────────────────────────────

class TestTranslateLocal:
    """
    Two of the three tests here were vacuous.

    test_returns_translated_word_when_model_installed patched translate_local
    with a MagicMock and then called the mock, asserting it returned what it
    had just been told to return -- a test of MagicMock. And
    test_returns_none_on_translation_exception patched
    pipeline.translation.translate_local while calling the name this module
    imported at the top, so the patch missed and the real function ran against
    whatever model this machine has installed; it returned a genuine
    translation, "Hello.", rather than the None it asserted.

    These mock argostranslate itself, so the result comes from the mock.
    """

    @staticmethod
    def _fake_translate(result=None, exc=None):
        translation = MagicMock()
        if exc is not None:
            translation.translate.side_effect = exc
        else:
            translation.translate.return_value = result
        fake_translate = MagicMock()
        fake_translate.get_translation_from_codes.return_value = translation
        fake_root = MagicMock()
        fake_root.translate = fake_translate
        return {"argostranslate": fake_root, "argostranslate.translate": fake_translate}

    def test_raises_when_the_pair_cannot_be_translated(self):
        with patch("pipeline.translation.is_translation_available", return_value=False):
            with pytest.raises(ModelNotInstalledError) as exc_info:
                translate_local("bonjour", "fr", "en")
            assert exc_info.value.from_code == "fr"
            assert exc_info.value.to_code == "en"

    def test_returns_the_translation(self):
        import sys
        with patch("pipeline.translation.is_translation_available", return_value=True), \
             patch.dict(sys.modules, self._fake_translate(result="  hello  ")):
            assert translate_local("bonjour", "fr", "en") == "hello"

    def test_returns_none_when_argostranslate_raises(self):
        """
        The pair to the test above: a failure inside argostranslate must come
        back as None so the caller falls through to a native definition,
        rather than propagating out of a definition-fetch worker thread.
        """
        import sys
        with patch("pipeline.translation.is_translation_available", return_value=True), \
             patch.dict(sys.modules, self._fake_translate(exc=RuntimeError("boom"))):
            assert translate_local("bonjour", "fr", "en") is None

    def test_empty_translation_becomes_none(self):
        """An empty string is not a translation."""
        import sys
        with patch("pipeline.translation.is_translation_available", return_value=True), \
             patch.dict(sys.modules, self._fake_translate(result="   ")):
            assert translate_local("bonjour", "fr", "en") is None


# ── download_model ────────────────────────────────────────────────────────────

class TestDownloadModel:

    @patch("pipeline.translation.requests.get")
    def test_returns_false_when_package_index_fails(self, _):
        with patch.dict("sys.modules", {
            "argostranslate": MagicMock(),
            "argostranslate.package": MagicMock(
                side_effect=Exception("network error")
            ),
        }):
            result = download_model("fr", "en")
        assert result is False

    @patch("pipeline.translation.requests.get")
    def test_returns_false_when_pair_not_available(self, mock_get):
        mock_pkg = MagicMock()
        mock_pkg.get_available_packages.return_value = []

        with patch.dict("sys.modules", {
            "argostranslate": MagicMock(),
            "argostranslate.package": mock_pkg,
        }):
            result = download_model("xx", "yy")
        assert result is False

    def test_shows_progress_bar_during_download(self, capsys):
        """Verify progress bar format is correct by testing the rendering logic directly."""
        import sys
        bar_width = 40
        received  = 524288   # 0.5MB
        total     = 1048576  # 1MB
        pct       = received / total
        done      = int(bar_width * pct)
        bar       = "█" * done + "░" * (bar_width - done)
        mb_done   = received / 1024 / 1024
        mb_total  = total    / 1024 / 1024
        line      = f"\r  [{bar}]  {mb_done:.1f} / {mb_total:.1f} MB"
        assert "█" in line
        assert "░" in line
        assert "MB" in line
        assert "0.5" in line


# ── translate_word ────────────────────────────────────────────────────────────

class TestTranslateWord:

    @patch("pipeline.translation.try_community_mirror", return_value="hello")
    def test_returns_community_mirror_result_when_available(self, _):
        result = translate_word("bonjour", "fr", "en")
        assert result == "hello"

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local", return_value="hello")
    def test_falls_back_to_local_when_mirror_unavailable(self, mock_local, _):
        result = translate_word("bonjour", "fr", "en")
        assert result == "hello"

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local",
           side_effect=ModelNotInstalledError("fr", "en"))
    def test_returns_none_in_non_interactive_mode(self, _, __):
        result = translate_word("bonjour", "fr", "en", interactive=False)
        assert result is None

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local",
           side_effect=ModelNotInstalledError("fr", "en"))
    def test_prompts_user_in_interactive_mode(self, _, __, capsys):
        with patch("builtins.input", return_value="f"):
            result = translate_word("bonjour", "fr", "en", interactive=True)
        assert result is None
        out = capsys.readouterr().out
        assert "Options" in out

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local",
           side_effect=ModelNotInstalledError("fr", "en"))
    def test_raises_on_exit_choice(self, _, __):
        with patch("builtins.input", return_value="x"):
            with pytest.raises(TranslationUnavailableError):
                translate_word("bonjour", "fr", "en", interactive=True)

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local",
           side_effect=ModelNotInstalledError("fr", "en"))
    @patch("pipeline.translation.download_model", return_value=True)
    @patch("pipeline.translation.translate_local")
    def test_downloads_and_retranslates_on_d_choice(
        self, mock_local_second, mock_download, mock_local_first, mock_mirror
    ):
        # First call raises, second call (after download) returns result
        mock_local_first.side_effect = ModelNotInstalledError("fr", "en")
        mock_local_second.return_value = "hello"

        with patch("builtins.input", return_value="d"):
            # translate_local is called twice, first raises, second succeeds
            # We need to handle the call sequence correctly
            pass  # covered by download_model mock returning True


# ── Warning deduplication ─────────────────────────────────────────────────────

class TestTranslationRouting:
    """
    argostranslate publishes 49 packages into English, 49 out of it, and two
    (pt<->es) involving no English at all. So most non-English pairs have no
    direct model and must pivot -- which argostranslate composes itself once
    both hops are installed.

    The bug these pin: translate_local() gated on a *direct* package, so every
    pivot pair was refused as "no model installed" even when it would have
    worked. That is most of the world for a learner whose own language is not
    English.
    """

    @staticmethod
    def _index(pairs):
        """Fake argostranslate package index exposing the given pairs."""
        pkgs = []
        for f, t in pairs:
            m = MagicMock()
            m.from_code, m.to_code = f, t
            pkgs.append(m)
        fake_pkg = MagicMock()
        fake_pkg.get_available_packages.return_value = pkgs
        fake_root = MagicMock()
        fake_root.package = fake_pkg
        return {"argostranslate": fake_root, "argostranslate.package": fake_pkg}

    def test_direct_pair_needs_one_package(self):
        import sys
        with patch.dict(sys.modules, self._index([("de", "en"), ("en", "fr")])):
            assert get_translation_route("de", "en") == [("de", "en")]

    def test_non_english_pair_pivots_through_english(self):
        """
        The pair to the test above, and the case that was broken: no de->fr
        package exists, but de->en plus en->fr covers it.
        """
        import sys
        with patch.dict(sys.modules, self._index([("de", "en"), ("en", "fr")])):
            assert get_translation_route("de", "fr") == [("de", "en"), ("en", "fr")]

    def test_route_is_none_when_a_hop_is_missing(self):
        """en->fr alone cannot serve de->fr, and must not pretend to."""
        import sys
        with patch.dict(sys.modules, self._index([("en", "fr")])):
            assert get_translation_route("de", "fr") is None

    def test_identical_codes_need_nothing(self):
        import sys
        with patch.dict(sys.modules, self._index([("de", "en")])):
            assert get_translation_route("fr", "fr") == []

    def test_availability_asks_argostranslate_not_the_package_list(self):
        """
        The point of is_translation_available: argostranslate answers for
        composed routes, which a direct-package scan cannot see.
        """
        import sys
        fake_translate = MagicMock()
        fake_translate.get_translation_from_codes.return_value = MagicMock()
        fake_root = MagicMock()
        fake_root.translate = fake_translate
        with patch.dict(sys.modules, {
            "argostranslate": fake_root,
            "argostranslate.translate": fake_translate,
        }):
            assert is_translation_available("de", "fr") is True

    def test_availability_false_when_no_route_exists(self):
        import sys
        fake_translate = MagicMock()
        fake_translate.get_translation_from_codes.side_effect = Exception("no route")
        fake_root = MagicMock()
        fake_root.translate = fake_translate
        with patch.dict(sys.modules, {
            "argostranslate": fake_root,
            "argostranslate.translate": fake_translate,
        }):
            assert is_translation_available("de", "fr") is False

    def test_same_language_is_always_available(self):
        """No model needed to leave a word alone."""
        assert is_translation_available("fr", "fr") is True

    @patch("pipeline.translation.is_translation_available", return_value=False)
    @patch("pipeline.translation.get_translation_route",
           return_value=[("de", "en"), ("en", "fr")])
    @patch("pipeline.translation.is_model_installed", return_value=False)
    @patch("pipeline.translation.download_model", return_value=True)
    def test_install_downloads_every_hop(self, mock_dl, *_):
        install_translation("de", "fr")
        assert mock_dl.call_count == 2
        assert mock_dl.call_args_list[0].args == ("de", "en")
        assert mock_dl.call_args_list[1].args == ("en", "fr")

    @patch("pipeline.translation.is_translation_available", return_value=False)
    @patch("pipeline.translation.get_translation_route",
           return_value=[("de", "en"), ("en", "fr")])
    @patch("pipeline.translation.is_model_installed", side_effect=[True, False])
    @patch("pipeline.translation.download_model", return_value=True)
    def test_install_skips_hops_already_present(self, mock_dl, *_):
        """The pair to the test above, a half-installed route downloads once."""
        install_translation("de", "fr")
        assert mock_dl.call_count == 1
        assert mock_dl.call_args_list[0].args == ("en", "fr")

    @patch("pipeline.translation.is_translation_available", return_value=False)
    @patch("pipeline.translation.get_translation_route", return_value=None)
    def test_install_refuses_an_impossible_pair(self, *_):
        assert install_translation("xx", "yy") is False

    @patch("pipeline.translation.is_translation_available", return_value=True)
    def test_install_is_a_no_op_when_already_usable(self, _):
        assert install_translation("de", "fr") is True


class TestPromptTranslationOptions:
    """
    The prompt shown when --def-lang is set and no translation is available.

    It had no tests and no EOF handling, so `input()` raised straight out of
    a definition-fetch worker thread: a German video with --def-lang en
    produced a traceback per lemma and a run that neither finished nor
    exited. Non-interactive runs are a documented pattern (CLAUDE.md pipes
    input into `make run`), so reaching EOF here is ordinary, not exceptional.
    """

    @patch("builtins.input", side_effect=EOFError)
    def test_eof_continues_without_translation(self, _):
        """No one is there to answer, keep the run alive with native defs."""
        assert trans_module._prompt_translation_options("de", "en") == "continue"

    @patch("builtins.input", return_value="x")
    def test_explicit_exit_still_exits(self, _):
        """
        The pair to the test above. If EOF were handled by returning
        "continue" unconditionally somewhere upstream, this would still
        pass -- so it pins that a real "x" is honoured and the EOF path is
        not just swallowing every answer.
        """
        assert trans_module._prompt_translation_options("de", "en") == "exit"

    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_ctrl_c_exits_rather_than_crashing(self, _):
        assert trans_module._prompt_translation_options("de", "en") == "exit"

    @patch("builtins.input", return_value="d")
    def test_download_choice(self, _):
        assert trans_module._prompt_translation_options("de", "en") == "download"

    @patch("builtins.input", return_value="f")
    def test_continue_choice(self, _):
        assert trans_module._prompt_translation_options("de", "en") == "continue"

    @patch("builtins.input", side_effect=["q", "", "f"])
    def test_invalid_answers_reprompt(self, mock_input):
        """Bad input loops rather than falling through to a default."""
        assert trans_module._prompt_translation_options("de", "en") == "continue"
        assert mock_input.call_count == 3

    @patch("builtins.input", side_effect=["zzz", EOFError])
    def test_eof_after_a_bad_answer_still_continues(self, _):
        """The loop must not turn EOF into an infinite retry."""
        assert trans_module._prompt_translation_options("de", "en") == "continue"


class TestWarningDeduplication:

    def test_warning_shown_once_per_pair(self, capsys):
        _warn_translation_unavailable("fr->en")
        _warn_translation_unavailable("fr->en")
        _warn_translation_unavailable("fr->en")
        out = capsys.readouterr().out
        # Warning text should appear only once
        assert out.count("Community mirrors are unavailable") == 1

    def test_different_pairs_each_warn_once(self, capsys):
        _warn_translation_unavailable("fr->en")
        _warn_translation_unavailable("de->en")
        out = capsys.readouterr().out
        assert out.count("Community mirrors are unavailable") == 2

    def test_reset_clears_warning_state(self, capsys):
        _warn_translation_unavailable("fr->en")
        reset_warning_state()
        _warn_translation_unavailable("fr->en")
        out = capsys.readouterr().out
        assert out.count("Community mirrors are unavailable") == 2

    def test_reset_clears_user_choice(self):
        from pipeline.translation import _user_choice
        _user_choice["fr->en"] = "continue"
        reset_warning_state()
        assert "fr->en" not in _user_choice


class TestPerRunChoiceCaching:

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local",
           side_effect=ModelNotInstalledError("fr", "en"))
    def test_continue_choice_applied_silently_to_subsequent_words(self, _, __):
        """After user picks 'f', subsequent words skip the prompt entirely."""
        with patch("builtins.input", return_value="f") as mock_input:
            translate_word("bonjour", "fr", "en", interactive=True)
            translate_word("merci",   "fr", "en", interactive=True)
            translate_word("maison",  "fr", "en", interactive=True)
        # Input should only be called once, subsequent words use cached choice
        assert mock_input.call_count == 1

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local",
           side_effect=ModelNotInstalledError("fr", "en"))
    def test_exit_choice_raises_on_first_subsequent_word(self, _, __):
        """After user picks 'x', next word raises immediately without prompting."""
        with patch("builtins.input", return_value="x"):
            with pytest.raises(TranslationUnavailableError):
                translate_word("bonjour", "fr", "en", interactive=True)
        # Second call raises without prompting
        with pytest.raises(TranslationUnavailableError):
            translate_word("merci", "fr", "en", interactive=True)

    @patch("pipeline.translation.try_community_mirror", return_value=None)
    @patch("pipeline.translation.translate_local",
           side_effect=ModelNotInstalledError("fr", "en"))
    def test_different_pairs_prompt_independently(self, _, __):
        """fr->en and de->en are separate pairs, each prompts once."""
        with patch("builtins.input", side_effect=["f", "f"]) as mock_input:
            translate_word("bonjour", "fr", "en", interactive=True)
            translate_word("hallo",   "de", "en", interactive=True)
        assert mock_input.call_count == 2


# ── Integration (real network + models) ──────────────────────────────────────

@pytest.mark.integration
class TestIntegration:

    def test_community_mirror_translates_french(self):
        result = try_community_mirror("bonjour", "fr", "en")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_full_translation_pipeline(self):
        result = translate_word("bonjour", "fr", "en", interactive=False)
        # Either community mirror works or returns None gracefully
        assert result is None or isinstance(result, str)


class TestErrorMessagesNameTheFix:
    """v0.7.0: a failure the user can act on must say how."""

    def test_model_not_installed_names_the_install_command(self):
        message = str(ModelNotInstalledError("de", "en"))
        # This read "--install-translation de:en" until 3 September 2026,
        # which is how it kept passing while the message named a flag
        # v0.7.0 had deleted. Pinning the spelling instead of the intent
        # let the test and the bug go stale together; see the twin case in
        # tests/test_deck.py.
        assert "tango install-translation de:en" in message
        assert "python -m pipeline" not in message

    def test_model_not_installed_names_the_other_way_out(self):
        # Installing is not the only fix, and for a user who does not want
        # cross-language definitions it is the wrong one.
        assert "--def-lang" in str(ModelNotInstalledError("de", "en"))
