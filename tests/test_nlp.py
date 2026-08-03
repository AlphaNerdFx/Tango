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
    Reset the lazy-loaded model cache before each test so tests are
    fully isolated — one test loading a mock doesn't leak into the next.
    """
    original = nlp_module._nlp_model
    nlp_module._nlp_model = None
    yield
    nlp_module._nlp_model = original


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

    def test_empty_doc_returns_empty_dict(self, mock_spacy_model):
        mock_spacy_model.return_value = _make_doc([])
        result = process_transcript("some transcript text")
        assert result == {}


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
        nlp_module._nlp_model = None
        result = process_transcript("Companies developed permanent photographic records.")
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_real_lemmatization(self):
        nlp_module._nlp_model = None
        result = process_transcript("running runs run")
        assert "run" in result
        assert result["run"] == 3

    def test_real_pos_filtering(self):
        nlp_module._nlp_model = None
        result = process_transcript("the through with and but")
        # Prepositions and conjunctions should be filtered
        assert "through" not in result

    def test_real_frequency_count(self):
        nlp_module._nlp_model = None
        result = process_transcript("water water water runs quickly")
        assert result["water"] == 3

    def test_real_first_appearance_order(self):
        nlp_module._nlp_model = None
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