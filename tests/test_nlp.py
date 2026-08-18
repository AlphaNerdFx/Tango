"""
test_nlp.py

All tests mock the spaCy model — no model installation required to run
the unit suite.

Run unit tests:        pytest tests/test_nlp.py -m "not integration"
Run all (model needed): pytest tests/test_nlp.py
"""

from unittest.mock import MagicMock, patch
import pytest

import pipeline.nlp as nlp_module
from pipeline.nlp import (
    process_transcript,
    get_sorted_by_frequency,
    get_unique_lemmas,
    EmptyTranscriptError,
    NLPModelNotFoundError,
    ACCEPTED_POS,
    _effective_lemma,
    _is_single_cjk_character,
    _is_valid_lemma,
    _is_valid_token,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_token(text: str, lemma: str, pos: str,
                is_alpha: bool = True, is_stop: bool = False):
    """Build a mock spaCy token with the attributes nlp.py reads."""
    t = MagicMock()
    t.text     = text
    t.lemma_   = lemma
    t.pos_     = pos
    t.is_alpha = is_alpha
    t.is_stop  = is_stop
    return t


def _make_doc(tokens: list) -> MagicMock:
    """Build a mock spaCy Doc that iterates over the given tokens."""
    doc = MagicMock()
    doc.__iter__ = lambda self: iter(tokens)
    doc.__len__  = lambda self: len(tokens)
    return doc


@pytest.fixture(autouse=True)
def reset_model_cache():
    """
    Reset the lazy-loaded per-language model cache before each test so
    tests are fully isolated — one test loading a mock doesn't leak into
    the next.
    """
    original = nlp_module._nlp_models.copy()
    nlp_module._nlp_models.clear()
    yield
    nlp_module._nlp_models.clear()
    nlp_module._nlp_models.update(original)


@pytest.fixture
def mock_spacy_model():
    """
    Patch spacy.load so no real model is needed.
    Returns the mock model object for test configuration.
    """
    with patch("pipeline.nlp.spacy.load") as mock_load:
        mock_model = MagicMock()
        mock_load.return_value = mock_model
        yield mock_model


# ── Sample token sets ─────────────────────────────────────────────────────────

SAMPLE_TOKENS = [
    _make_token("running",       "run",           "VERB"),
    _make_token("quickly",       "quickly",       "ADV"),
    _make_token("through",       "through",       "ADP"),    # filtered — not in ACCEPTED_POS
    _make_token("contaminated",  "contaminate",   "VERB"),
    _make_token("water",         "water",         "NOUN"),
    _make_token("gives",         "give",          "VERB"),
    _make_token("contamination", "contamination", "NOUN"),
    _make_token("3",             "3",             "NUM",  is_alpha=False),  # filtered — not alpha
    _make_token("permanent",     "permanent",     "ADJ"),
    _make_token("run",           "run",           "VERB"),   # duplicate — frequency += 1
    _make_token("water",         "water",         "NOUN"),   # duplicate — frequency += 1
]


# ── _is_valid_token ───────────────────────────────────────────────────────────

class TestIsValidToken:

    def test_noun_is_valid(self):
        assert _is_valid_token(_make_token("water", "water", "NOUN"))

    def test_verb_is_valid(self):
        assert _is_valid_token(_make_token("run", "run", "VERB"))

    def test_adj_is_valid(self):
        assert _is_valid_token(_make_token("permanent", "permanent", "ADJ"))

    def test_adv_is_valid(self):
        assert _is_valid_token(_make_token("quickly", "quickly", "ADV"))

    def test_adposition_filtered(self):
        assert not _is_valid_token(_make_token("through", "through", "ADP"))

    def test_numeric_token_filtered(self):
        assert not _is_valid_token(_make_token("3", "3", "NUM", is_alpha=False))

    def test_non_alpha_filtered(self):
        t = _make_token("...", "...", "PUNCT", is_alpha=False)
        assert not _is_valid_token(t)

    def test_stop_word_kept(self):
        """Stop words are intentionally kept — beginners need basic vocab."""
        t = _make_token("be", "be", "VERB", is_stop=True)
        assert _is_valid_token(t)

    def test_accepted_pos_set_has_four_entries(self):
        assert ACCEPTED_POS == {"NOUN", "VERB", "ADJ", "ADV"}

    def test_empty_lemma_falls_back_to_text(self):
        # zh_core_web_sm leaves lemma_ empty for every token, confirmed
        # live. Without the fallback this is indistinguishable from an
        # invalid/empty lemma and gets filtered, silently dropping all
        # Chinese vocabulary.
        t = _make_token("人", "", "NOUN")
        assert _is_valid_token(t)

    def test_non_empty_lemma_is_not_overridden_by_text(self):
        # Companion to the test above: when lemma_ IS populated, it must
        # still be lemma_ that gets used, not silently replaced by text --
        # a single test passing here can't prove the fallback is
        # conditional rather than unconditional.
        t = _make_token("running", "run", "VERB")
        assert _effective_lemma(t) == "run"


class TestEffectiveLemma:

    def test_uses_lemma_when_present(self):
        assert _effective_lemma(_make_token("ran", "run", "VERB")) == "run"

    def test_falls_back_to_text_when_lemma_empty(self):
        assert _effective_lemma(_make_token("人", "", "NOUN")) == "人"


class TestSingleCjkCharacter:

    @pytest.mark.parametrize("char", ["人", "大", "水", "去", "了"])
    def test_common_single_chinese_characters_accepted(self, char):
        assert _is_single_cjk_character(char)

    def test_single_hiragana_accepted(self):
        assert _is_single_cjk_character("あ")

    def test_single_hangul_syllable_accepted(self):
        assert _is_single_cjk_character("가")

    def test_single_latin_letter_rejected(self):
        # The exemption is script-specific -- "e" (the original French
        # lemmatization-debris case this length check exists for) must
        # still be rejected, not accidentally let through by a check
        # that's too broad.
        assert not _is_single_cjk_character("e")

    def test_multi_character_cjk_string_rejected(self):
        # This function only exempts single characters from the length
        # minimum. A real multi-character CJK word must still pass
        # through the normal _VALID_LEMMA regex, not this exemption.
        assert not _is_single_cjk_character("人们")

    def test_single_cjk_character_passes_is_valid_lemma(self):
        assert _is_valid_lemma("人")

    def test_single_latin_letter_still_fails_is_valid_lemma(self):
        assert not _is_valid_lemma("e")


# ── process_transcript ────────────────────────────────────────────────────────

class TestProcessTranscript:

    def test_returns_dict(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        assert isinstance(result, dict)

    def test_keys_are_lowercase_lemmas(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        for key in result:
            assert key == key.lower()

    def test_frequency_counted_correctly(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        # "run" appears twice, "water" appears twice
        assert result["run"] == 2
        assert result["water"] == 2

    def test_single_occurrence_frequency_is_one(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        assert result["quickly"] == 1
        assert result["permanent"] == 1

    def test_non_accepted_pos_excluded(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        assert "through" not in result

    def test_non_alpha_tokens_excluded(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        assert "3" not in result

    def test_first_appearance_order_preserved(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        keys = list(result.keys())
        # "run" appears before "quickly" in SAMPLE_TOKENS
        assert keys.index("run") < keys.index("quickly")
        # "run" appears before "contaminate"
        assert keys.index("run") < keys.index("contaminate")

    def test_duplicate_lemma_not_reinserted(self, mock_spacy_model):
        """Second occurrence of a lemma must not change its position."""
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        result = process_transcript("some transcript text")
        keys = list(result.keys())
        # "run" should appear only once as a key
        assert keys.count("run") == 1

    def test_raises_on_empty_string(self, mock_spacy_model):
        with pytest.raises(EmptyTranscriptError):
            process_transcript("")

    def test_raises_on_whitespace_only(self, mock_spacy_model):
        with pytest.raises(EmptyTranscriptError):
            process_transcript("   \n\t  ")

    def test_raises_on_model_not_found(self):
        with patch("pipeline.nlp.spacy.load", side_effect=OSError("model not found")):
            with pytest.raises(NLPModelNotFoundError):
                process_transcript("some text")

    def test_model_loaded_once_across_calls(self, mock_spacy_model):
        """Lazy loading: spacy.load must be called exactly once."""
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        with patch("pipeline.nlp.spacy.load", return_value=mock_spacy_model) as mock_load:
            process_transcript("first call")
            process_transcript("second call")
            mock_load.assert_called_once()

    def test_defaults_to_english_model_when_language_omitted(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc([])
        with patch("pipeline.nlp.spacy.load", return_value=mock_spacy_model) as mock_load:
            process_transcript("some text")
            mock_load.assert_called_once_with("en_core_web_sm")

    def test_passes_correct_model_for_requested_language(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc([])
        with patch("pipeline.nlp.spacy.load", return_value=mock_spacy_model) as mock_load:
            process_transcript("un texte", language="fr")
            # "md", not "sm" -- French is pinned to the medium model, see
            # issue #13. Other languages still default to "sm".
            mock_load.assert_called_once_with("fr_core_news_md")

    def test_unsupported_language_raises_spacy_model_unavailable(self):
        with pytest.raises(nlp_module.SpacyModelUnavailableError):
            process_transcript("some text", language="ar")

    def test_two_languages_cached_independently(self, mock_spacy_model):
        # Regression guard for the actual bug this feature fixes: processing
        # a French transcript must not reuse an already-loaded English
        # model, and vice versa -- each language gets its own spacy.load
        # call, not a single shared global.
        mock_spacy_model.return_value = _make_doc([])
        with patch("pipeline.nlp.spacy.load", return_value=mock_spacy_model) as mock_load:
            process_transcript("some text", language="en")
            process_transcript("un texte", language="fr")
            assert mock_load.call_count == 2
            mock_load.assert_any_call("en_core_web_sm")
            mock_load.assert_any_call("fr_core_news_md")

    def test_same_language_reuses_cached_model(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc([])
        with patch("pipeline.nlp.spacy.load", return_value=mock_spacy_model) as mock_load:
            process_transcript("premier texte", language="fr")
            process_transcript("deuxième texte", language="fr")
            mock_load.assert_called_once_with("fr_core_news_md")

    def test_empty_doc_returns_empty_dict(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc([])
        result = process_transcript("some transcript text")
        assert result == {}

    def test_chinese_style_empty_lemmas_still_produce_vocabulary(self, mock_spacy_model):
        # Reproduces the real bug live-verified against zh_core_web_sm: a
        # doc where every token has lemma_ == "" (real Chinese pipeline
        # behavior, not a hypothetical) used to extract zero vocabulary
        # from a real 154-snippet transcript. Fixed via _effective_lemma
        # falling back to token.text.
        tokens = [
            _make_token("人", "", "NOUN"),
            _make_token("大", "", "VERB"),
            _make_token("人", "", "NOUN"),  # repeat, should increment frequency
        ]
        mock_spacy_model.return_value = _make_doc(tokens)
        result = process_transcript("我 大 人", language="zh")
        assert result == {"人": 2, "大": 1}


# ── get_sorted_by_frequency ───────────────────────────────────────────────────

class TestGetSortedByFrequency:

    def test_returns_dict(self):
        vocab = {"run": 3, "water": 5, "permanent": 1}
        assert isinstance(get_sorted_by_frequency(vocab), dict)

    def test_sorted_descending(self):
        vocab = {"run": 3, "water": 5, "permanent": 1}
        result = get_sorted_by_frequency(vocab)
        counts = list(result.values())
        assert counts == sorted(counts, reverse=True)

    def test_highest_frequency_first(self):
        vocab = {"run": 3, "water": 5, "permanent": 1}
        result = get_sorted_by_frequency(vocab)
        assert list(result.keys())[0] == "water"

    def test_does_not_mutate_original(self):
        vocab = {"run": 3, "water": 5}
        original_order = list(vocab.keys())
        get_sorted_by_frequency(vocab)
        assert list(vocab.keys()) == original_order

    def test_empty_input_returns_empty(self):
        assert get_sorted_by_frequency({}) == {}


# ── get_unique_lemmas ─────────────────────────────────────────────────────────

class TestGetUniqueLemmas:

    def test_returns_list(self):
        vocab = {"run": 3, "water": 5, "permanent": 1}
        assert isinstance(get_unique_lemmas(vocab), list)

    def test_preserves_insertion_order(self):
        vocab = {"run": 3, "water": 5, "permanent": 1}
        assert get_unique_lemmas(vocab) == ["run", "water", "permanent"]

    def test_no_duplicates(self):
        vocab = {"run": 3, "water": 5}
        result = get_unique_lemmas(vocab)
        assert len(result) == len(set(result))

    def test_empty_input_returns_empty_list(self):
        assert get_unique_lemmas({}) == []


# ── Integration (real spaCy model required) ───────────────────────────────────

@pytest.mark.integration
class TestIntegration:

    def test_real_model_loads(self):
        nlp_module._nlp_models.clear()
        result = process_transcript("Companies developed permanent photographic records.")
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_real_lemmatization(self):
        nlp_module._nlp_models.clear()
        result = process_transcript("running runs run")
        assert "run" in result
        assert result["run"] == 3

    def test_real_pos_filtering(self):
        nlp_module._nlp_models.clear()
        result = process_transcript("the through with and but")
        # Prepositions and conjunctions should be filtered
        assert "through" not in result

    def test_real_frequency_count(self):
        nlp_module._nlp_models.clear()
        result = process_transcript("water water water runs quickly")
        assert result["water"] == 3

    def test_real_first_appearance_order(self):
        nlp_module._nlp_models.clear()
        result = process_transcript("contamination runs through water quickly")
        keys = list(result.keys())
        assert keys.index("contamination") < keys.index("water")

# -- Single letter and proper noun filtering ----------------------------------

class TestTokenFilterExtended:

    def test_single_letter_filtered(self):
        """Single letter tokens must not appear in vocabulary."""
        t = _make_token("a", "a", "NOUN", is_alpha=True)
        assert not _is_valid_token(t)

    def test_two_letter_token_passes(self):
        """Two-letter tokens are allowed."""
        t = _make_token("be", "be", "VERB", is_alpha=True)
        assert _is_valid_token(t)

    def test_proper_noun_filtered(self):
        """PROPN tokens are excluded -- they are names, not vocabulary."""
        t = _make_token("Paris", "Paris", "PROPN", is_alpha=True)
        assert not _is_valid_token(t)

    def test_person_entity_filtered(self):
        """Named entities of type PERSON are excluded."""
        t = _make_token("Emmanuel", "Emmanuel", "PROPN", is_alpha=True)
        t.ent_type_ = "PERSON"
        assert not _is_valid_token(t)

    def test_org_entity_filtered(self):
        """Named entities of type ORG are excluded."""
        t = _make_token("Google", "Google", "PROPN", is_alpha=True)
        t.ent_type_ = "ORG"
        assert not _is_valid_token(t)

    def test_regular_noun_with_entity_type_gpe_filtered(self):
        """GPE (geopolitical entity) tokens are excluded."""
        t = _make_token("France", "France", "PROPN", is_alpha=True)
        t.ent_type_ = "GPE"
        assert not _is_valid_token(t)

    def test_surface_form_lemmatizing_to_single_char_filtered(self):
        """
        A 3-character surface form that lemmatizes to a 1-character
        lemma must be filtered. The vocabulary dict is keyed by the
        lemma, not the surface form, so a length check on token.text
        alone misses this -- e.g. a French conjugated form collapsing
        to the single-letter lemma "e".
        """
        t = _make_token("est", "e", "VERB", is_alpha=True)
        assert not _is_valid_token(t)

    def test_surface_form_lemmatizing_to_punctuation_filtered(self):
        """
        An alphabetic surface form that lemmatizes to a non-alphabetic
        string must be filtered. token.is_alpha describes the surface
        form only -- the lemma can contain characters (e.g. punctuation
        from a contraction split) the surface form did not.
        """
        t = _make_token("qu'", "'", "NOUN", is_alpha=True)
        assert not _is_valid_token(t)

    def test_short_surface_form_with_valid_lemma_passes(self):
        """
        A short surface form whose lemma is a valid word must pass.
        French 'va' (2 chars) lemmatizes to 'aller' (5 chars). Guards
        against a future refactor reintroducing a length check on
        token.text, which would silently drop short conjugated forms.
        """
        t = _make_token("va", "aller", "VERB", is_alpha=True)
        assert _is_valid_token(t)

    def test_hyphenated_compound_passes(self):
        """Real compound adjectives must not be filtered."""
        t = _make_token("semi-relevée", "semi-relevé", "ADJ", is_alpha=False)
        assert _is_valid_token(t)

    def test_trailing_hyphen_lemma_filtered(self):
        """A lemma ending in a hyphen is a tokenizer artifact, not a word."""
        t = _make_token("semi-", "semi-", "ADJ", is_alpha=False)
        assert not _is_valid_token(t)

    def test_leading_hyphen_lemma_filtered(self):
        """'-là' is a demonstrative suffix fragment, not vocabulary."""
        t = _make_token("-là", "-là", "ADV", is_alpha=False)
        assert not _is_valid_token(t)

    def test_typographic_apostrophe_permitted(self):
        """YouTube transcripts often use U+2019 rather than ASCII apostrophe."""
        t = _make_token("aujourd’hui", "aujourd’hui", "ADV", is_alpha=False)
        assert _is_valid_token(t)

    def test_digit_containing_lemma_filtered(self):
        """Alphanumeric tokens are not vocabulary."""
        t = _make_token("3d", "3d", "NOUN", is_alpha=False)
        assert not _is_valid_token(t)

    def test_punctuation_lemma_rejected_without_is_alpha_guard(self):
        """
        _is_valid_lemma is the sole gate -- token.is_alpha was removed
        deliberately because it evaluates the surface form, not the lemma
        the vocabulary dict is keyed by. This test pins that the regex
        alone still rejects pure punctuation, so nobody reinstates the
        surface-form guard as a "safety net" it never provided.
        """
        t = _make_token(">", ">", "ADJ", is_alpha=False)
        assert not _is_valid_token(t)

    def test_regular_noun_passes(self):
        """Normal nouns with no entity type still pass."""
        t = _make_token("water", "water", "NOUN", is_alpha=True)
        t.ent_type_ = ""
        assert _is_valid_token(t)

# ── Verb-lemma fallback for model lookup gaps (issue #13) ────────────────────
#
# spaCy's lemma lookup is keyed on the surface form and is not POS-aware, so
# a verb form that is also a noun gets the noun's lemma even when correctly
# tagged VERB: French "joue" (plays / cheek) stayed "joue" instead of
# becoming "jouer". These run on mock tokens so the default suite needs no
# French model; TestVerbLemmaFallbackIntegration below repeats the key cases
# against the real one.
#
# Every token here is built with text != lemma or text == lemma on purpose --
# that distinction IS the bug, so a fixture that always passed identical
# values would make it inexpressible (CLAUDE.md section 5).

class TestVerbLemmaFallback:

    # A stand-in for the model's lemma_lookup vocabulary.
    KNOWN = frozenset({"jouer", "porter", "monter", "joue", "porte", "être", "parler"})

    def test_identity_lemma_on_verb_is_repaired(self):
        # The bug: lookup returned the surface form for a tagged VERB.
        t = _make_token("joue", "joue", "VERB")
        assert nlp_module._corrected_lemma(t, "fr", self.KNOWN) == "jouer"

    def test_candidate_absent_from_vocabulary_is_rejected(self):
        # Pairs with the test above. "être" is a VERB whose lemma equals its
        # surface form, so the rule fires -- and must then be stopped by the
        # vocabulary guard, since "êtrer" is not a word. Without the guard
        # this returns "êtrer" and the pair fails in opposite directions.
        t = _make_token("être", "être", "VERB")
        assert nlp_module._corrected_lemma(t, "fr", self.KNOWN) == "être"

    def test_already_correct_lemma_is_left_alone(self):
        # text != lemma means the model did its job; nothing to repair.
        t = _make_token("joues", "jouer", "VERB")
        assert nlp_module._corrected_lemma(t, "fr", self.KNOWN) == "jouer"

    def test_non_verb_is_left_alone(self):
        # "porte" as a noun (a door) is already correct as itself. Repairing
        # it to "porter" would be actively wrong.
        t = _make_token("porte", "porte", "NOUN")
        assert nlp_module._corrected_lemma(t, "fr", self.KNOWN) == "porte"

    def test_language_without_a_fallback_entry_is_untouched(self):
        # Only languages verified against their own model get an entry.
        t = _make_token("juego", "juego", "VERB")
        assert nlp_module._corrected_lemma(t, "es", self.KNOWN) == "juego"

    def test_empty_vocabulary_disables_the_fallback(self):
        # _known_lemmas returns an empty set when the pipeline has no lookup
        # table. That must disable the rule, not let it run unvalidated.
        t = _make_token("joue", "joue", "VERB")
        assert nlp_module._corrected_lemma(t, "fr", frozenset()) == "joue"

    def test_empty_lemma_still_falls_back_to_surface_form(self):
        # Interaction with the Chinese fix (_effective_lemma): a token with
        # no lemma at all must not crash or produce "".
        t = _make_token("好", "", "VERB")
        assert nlp_module._corrected_lemma(t, "zh", frozenset()) == "好"

    def test_only_french_is_registered(self):
        # Regression guard: entries belong here only once verified against
        # that language's model. If someone adds one, this failing is the
        # prompt to show the measurements.
        assert set(nlp_module._VERB_LEMMA_FALLBACKS) == {"fr"}


@pytest.mark.integration
class TestVerbLemmaFallbackIntegration:
    """Same cases against the real fr_core_news_md model."""

    def test_real_french_verbs_are_repaired(self):
        import spacy
        nlp = spacy.load("fr_core_news_md")
        known = nlp_module._known_lemmas(nlp)
        assert known, "model exposes no lemma_lookup table"
        cases = [
            ("Il joue au foot.", "joue", "jouer"),
            ("Il porte un chapeau.", "porte", "porter"),
            ("Je monte les escaliers.", "monte", "monter"),
        ]
        for sentence, surface, expected in cases:
            for token in nlp(sentence):
                if token.text.lower() == surface:
                    assert nlp_module._corrected_lemma(token, "fr", known) == expected

    def test_real_irregular_verbs_are_not_mangled(self):
        import spacy
        nlp = spacy.load("fr_core_news_md")
        known = nlp_module._known_lemmas(nlp)
        for sentence, surface, expected in [
            ("Il est là.", "est", "être"),
            ("Elle a une pomme.", "a", "avoir"),
            ("Il fait beau.", "fait", "faire"),
        ]:
            for token in nlp(sentence):
                if token.text.lower() == surface:
                    assert nlp_module._corrected_lemma(token, "fr", known) == expected


# ── Surface-form capture ─────────────────────────────────────────────────────
#
# The lemma often does not appear in the transcript literally, so
# cards.py needs the forms the word actually took to find its example
# sentence. Out-parameter rather than a second return value, so existing
# callers are untouched.

class TestSurfaceFormCapture:

    def test_surface_forms_populated_when_requested(self):
        tokens = [
            _make_token("running", "run", "VERB"),
            _make_token("ran",     "run", "VERB"),
            _make_token("water",   "water", "NOUN"),
        ]
        forms: dict = {}
        with patch.object(nlp_module, "_get_model") as mock_model:
            mock_model.return_value = lambda _t: _make_doc(tokens)
            nlp_module.process_transcript("text", surface_forms=forms)
        assert forms["run"] == ["running", "ran"]
        assert forms["water"] == ["water"]

    def test_omitting_the_argument_changes_nothing(self):
        # Every existing caller passes nothing; that path must not break.
        tokens = [_make_token("running", "run", "VERB")]
        with patch.object(nlp_module, "_get_model") as mock_model:
            mock_model.return_value = lambda _t: _make_doc(tokens)
            result = nlp_module.process_transcript("text")
        assert result == {"run": 1}

    def test_duplicate_surface_forms_recorded_once(self):
        tokens = [_make_token("ran", "run", "VERB") for _ in range(4)]
        forms: dict = {}
        with patch.object(nlp_module, "_get_model") as mock_model:
            mock_model.return_value = lambda _t: _make_doc(tokens)
            nlp_module.process_transcript("text", surface_forms=forms)
        assert forms["run"] == ["ran"]

    def test_surface_form_list_is_capped(self):
        tokens = [
            _make_token(f"form{i}", "run", "VERB")
            for i in range(nlp_module._MAX_SURFACE_FORMS + 5)
        ]
        forms: dict = {}
        with patch.object(nlp_module, "_get_model") as mock_model:
            mock_model.return_value = lambda _t: _make_doc(tokens)
            nlp_module.process_transcript("text", surface_forms=forms)
        assert len(forms["run"]) == nlp_module._MAX_SURFACE_FORMS


# ── parts_of_speech out-parameter ─────────────────────────────────────────────

class TestPartsOfSpeechOutParameter:
    """
    ARCHITECTURE.md 8.29. definition.py needs the POS spaCy assigned each
    lemma *in its sentence* to pick the right dictionary sense, and the
    out-parameter shape follows surface_forms (8.20) so existing callers are
    untouched.
    """

    def test_records_the_pos_of_each_lemma(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        pos: dict = {}
        process_transcript("some transcript text", parts_of_speech=pos)
        assert pos["run"] == "VERB"
        assert pos["water"] == "NOUN"
        assert pos["permanent"] == "ADJ"
        assert pos["quickly"] == "ADV"

    def test_filtered_tokens_are_absent(self, mock_spacy_model):
        # "through" is ADP and "3" is NUM -- neither becomes a card, so
        # neither may leave a POS entry behind for one.
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        pos: dict = {}
        process_transcript("some transcript text", parts_of_speech=pos)
        assert "through" not in pos
        assert "3" not in pos

    def test_the_most_frequent_tag_wins_not_the_first(self, mock_spacy_model):
        # The pair that pins the choice. A word tagged once as a noun and
        # twice as a verb is a verb. Taking the first tag instead would
        # return NOUN here and satisfy any test that only counted entries.
        tokens = [
            _make_token("marche", "marche", "NOUN"),
            _make_token("marche", "marche", "VERB"),
            _make_token("marche", "marche", "VERB"),
        ]
        mock_spacy_model.return_value = _make_doc(tokens)
        pos: dict = {}
        process_transcript("text", parts_of_speech=pos)
        assert pos["marche"] == "VERB"

    def test_a_tie_resolves_to_the_earlier_tag(self, mock_spacy_model):
        # Partner to the test above: with no majority the answer must still
        # be deterministic, because this feeds a cache key.
        tokens = [
            _make_token("marche", "marche", "NOUN"),
            _make_token("marche", "marche", "VERB"),
        ]
        mock_spacy_model.return_value = _make_doc(tokens)
        pos: dict = {}
        process_transcript("text", parts_of_speech=pos)
        assert pos["marche"] == "NOUN"

    def test_omitting_it_changes_nothing(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        with_out: dict = {}
        a = process_transcript("text", parts_of_speech=with_out)
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        b = process_transcript("text")
        assert a == b

    def test_records_alongside_surface_forms(self, mock_spacy_model):
        # Both out-parameters at once, since the pipeline passes both.
        mock_spacy_model.return_value = _make_doc(SAMPLE_TOKENS)
        forms: dict = {}
        pos: dict = {}
        process_transcript("text", surface_forms=forms, parts_of_speech=pos)
        assert forms["run"] == ["running", "run"]
        assert pos["run"] == "VERB"


# ── Filler sounds ─────────────────────────────────────────────────────────────
#
# Ah, Bah, Ouai, Euh and Tss were 3.4% of one real French run. They get past
# the POS filter because the tagger calls them nouns and adverbs in the
# sentences they appear in, so the tokens below are tagged the way spaCy
# actually tagged them, not INTJ.

class TestFillerSounds:

    def test_french_filler_sounds_do_not_become_words(self, mock_spacy_model):
        tokens = [
            _make_token("Euh",     "euh",     "NOUN"),
            _make_token("Bah",     "bah",     "ADV"),
            _make_token("maison",  "maison",  "NOUN"),
        ]
        mock_spacy_model.return_value = _make_doc(tokens)
        assert process_transcript("text", language="fr") == {"maison": 1}

    def test_an_interjection_worth_learning_survives(self, mock_spacy_model):
        # The other half of the pair. A rule that dropped the tag instead of
        # the sound would pass the test above and fail this one.
        tokens = [
            _make_token("Euh",      "euh",      "NOUN"),
            _make_token("Bonsoir",  "bonsoir",  "NOUN"),
        ]
        mock_spacy_model.return_value = _make_doc(tokens)
        assert process_transcript("text", language="fr") == {"bonsoir": 1}

    def test_a_filler_in_one_language_is_kept_in_another(self, mock_spacy_model):
        # "euh" is on the French list only. An English transcript has not
        # been measured, so the word stays.
        tokens = [_make_token("Euh", "euh", "NOUN")]
        mock_spacy_model.return_value = _make_doc(tokens)
        assert process_transcript("text", language="en") == {"euh": 1}

    def test_an_elongated_filler_is_dropped(self, mock_spacy_model):
        tokens = [
            _make_token("Euuuh",   "euuuh",   "NOUN"),
            _make_token("maison",  "maison",  "NOUN"),
        ]
        mock_spacy_model.return_value = _make_doc(tokens)
        assert process_transcript("text", language="fr") == {"maison": 1}

    def test_a_filler_leaves_no_surface_form_or_pos_behind(self, mock_spacy_model):
        # It is skipped before the out-parameters are written, so nothing
        # downstream can resurrect it: definition.py reads parts_of_speech
        # and cards.py reads surface_forms.
        tokens = [
            _make_token("Euh",     "euh",     "NOUN"),
            _make_token("maison",  "maison",  "NOUN"),
        ]
        mock_spacy_model.return_value = _make_doc(tokens)
        forms: dict = {}
        pos: dict = {}
        process_transcript("text", language="fr", surface_forms=forms, parts_of_speech=pos)
        assert "euh" not in forms
        assert "euh" not in pos
        assert forms["maison"] == ["maison"]
