"""
test_hard_constraints.py

One test per hard constraint in CLAUDE.md section 3.

These exist because prose constraints decay into folklore. 3.3 was violated
and shipped -- caught by a coverage sweep noticing an impossible number, not
by review. 3.1 spent the project protecting a model ID copied out of
genanki's README that held none of this pipeline's cards. 3.2 had never been
exercised at all.

Each test below fails loudly on the specific mistake its constraint
describes, so the constraint is enforced rather than remembered.
"""

from __future__ import annotations

import inspect
import re

import pytest

from pipeline import cards
from pipeline.config import DECK_ID, MODEL_ID
from pipeline.definition import DefinitionResult

# ── 3.1 ANKI_MODEL_ID and ANKI_DECK_ID must not change ───────────────────────

class TestConstraint31StableIds:
    """
    Changing either makes Anki treat every existing card as belonging to a
    different notetype, destroying review history. There is no undo.

    MODEL_ID was changed exactly once, on 14 August 2026, because the old
    value (1607392319, genanki's README example) collided with a "Simple
    Model" notetype in real collections and so never held this pipeline's
    cards -- Anki forked a new notetype at 1607392321 instead. See
    ARCHITECTURE.md 8.31. These values must not move again.
    """

    def test_model_id_is_pinned(self):
        assert MODEL_ID == 1607392321

    def test_deck_id_is_pinned(self):
        assert DECK_ID == 2059400110

    # The two above are necessary and NOT sufficient, which mutation testing
    # is what proved: both read config.MODEL_ID, which resolves through
    # ANKI_MODEL_ID, and .env sets that variable. Changing the default in
    # config.py therefore left them green on any machine with a .env -- the
    # test was pinning the environment rather than the source. Same shape as
    # ARCHITECTURE.md 8.21 (a documented install always takes the override
    # branch and never reaches the default) and SESSION.md 6.18 (verifying
    # in the one context where the bug cannot reproduce).
    #
    # These two read the literal out of the source instead, so the default a
    # fresh clone would use is pinned whatever this machine's .env says.

    @staticmethod
    def _source_default(variable: str) -> str:
        from pipeline import config
        match = re.search(
            rf'{variable}",\s*"(\d+)"\s*\)', inspect.getsource(config)
        )
        assert match, f"could not find the {variable} default in config.py"
        return match.group(1)

    def test_model_id_default_in_source_is_pinned(self):
        assert self._source_default("ANKI_MODEL_ID") == "1607392321"

    def test_deck_id_default_in_source_is_pinned(self):
        assert self._source_default("ANKI_DECK_ID") == "2059400110"

    def test_the_generated_model_actually_uses_the_pinned_id(self):
        # Pinning the constant is not enough if the model stops reading it.
        assert cards._build_model().model_id == MODEL_ID


# ── 3.2 Card field order is positional and must match exactly ────────────────

class TestConstraint32FieldOrder:
    """
    genanki maps values to fields by index. A mismatch writes content into
    the wrong card section with no error and a valid .apkg.

    Since the FIELDS refactor this is enforced by construction rather than
    by review: the model is generated from FIELDS and both builders address
    fields by name. These tests pin that mechanism in place.
    """

    @staticmethod
    def _result(**kw):
        base = dict(
            lemma="glaube", definition="religioes sein", example_dict="Ex1.",
            example_dict2="Ex2.", example_transcript="Aus dem Video.",
            synonyms=["meinen"], antonyms=["zweifeln"],
            part_of_speech="verb", source="wiktionary",
        )
        base.update(kw)
        return DefinitionResult(**base)

    def test_model_fields_come_from_the_single_source(self):
        names = [f["name"] for f in cards._build_model().fields]
        assert tuple(names) == cards.FIELDS

    def test_word_is_the_first_field(self):
        # Anki treats a note's first field as its identity, and deck.py's
        # duplicate check reads the lowest-order field (8.22). Moving Word
        # would silently break duplicate detection for every deck.
        assert cards.FIELDS[0] == cards.IDENTITY_FIELD == "Word"

    def test_both_builders_emit_one_value_per_field(self):
        model = cards._build_model()
        standard = cards._build_note(self._result(), model, "vid", "de")
        fallback = cards._build_fallback_note("wort", "Satz.", model, "vid", "de")
        assert len(standard.fields) == len(cards.FIELDS)
        assert len(fallback.fields) == len(cards.FIELDS)

    def test_values_land_in_their_named_positions(self):
        model = cards._build_model()
        note = cards._build_note(
            self._result(), model, "vid42", "de",
            ipa="[ipa]", audio_url="https://example.invalid/a.mp3",
        )
        at = dict(zip(cards.FIELDS, note.fields, strict=True))
        assert at["Word"] == "Glaube"
        assert at["Class"] == "verb"
        assert at["Definition"] == "religioes sein"
        assert at["VideoID"] == "vid42"
        assert at["Source"] == "wiktionary"
        assert at["IPA"] == "[ipa]"
        assert "example.invalid/a.mp3" in at["Pronunciation"]

    def test_an_unknown_field_name_raises_instead_of_shifting(self):
        # The failure this constraint exists to prevent. Before the refactor
        # a mistyped field silently displaced every field after it.
        with pytest.raises(ValueError, match="Unknown card field"):
            cards._note_fields({"Word": "x", "Definiton": "typo"})

    def test_an_omitted_field_is_empty_and_shifts_nothing(self):
        # The partner. A sparse payload must pad, never slide.
        out = cards._note_fields({"Word": "x", "Source": "s"})
        assert len(out) == len(cards.FIELDS)
        assert out[cards.FIELDS.index("Word")] == "x"
        assert out[cards.FIELDS.index("Source")] == "s"
        assert out[cards.FIELDS.index("Definition")] == ""

    def test_new_fields_are_appended_never_inserted(self):
        # The first ten fields are what every already-imported card in every
        # user's collection is bound to. ADR-009 adds at 10+; nothing may be
        # inserted before them.
        assert cards.FIELDS[:10] == (
            "Word", "Class", "Definition", "1st Example Sentence",
            "2nd Example Sentence", "Example from Youtube Video",
            "Synonyms", "Antonyms", "VideoID", "Source",
        )


# ── 3.3 Examples, synonyms and antonyms stay in the transcript language ──────

class TestConstraint33FieldLanguage:
    """
    Definitions and grammatical class may change language under --def-lang.
    Examples, synonyms and antonyms may not -- learners need the word in
    native context. Violated once and shipped: a German video with
    --def-lang ru put Russian sentences on the cards (ARCHITECTURE 8.25).

    The gate lives at three call sites in definition.py. These tests pin the
    gate itself rather than any one call site, by asserting on the source
    text: only the transcript-language branch may fill those three fields.
    """

    @staticmethod
    def _gated_blocks() -> list[str]:
        """Source of every block that copies index fields onto a card."""
        from pipeline import definition
        src = inspect.getsource(definition)
        return [b for b in src.split("entry.example1") if "native_ex1" in b]

    def test_the_cross_language_call_site_is_gated(self):
        # The target-language lookup must sit behind `target_language ==
        # language` before it may touch examples/synonyms/antonyms.
        from pipeline import definition
        src = inspect.getsource(definition.fetch_definition)
        target_branch = src.split("wiktdata.lookup(query_lemma")[1]
        before_examples = target_branch.split("native_ex1 = entry.example1")[0]
        assert "target_language == language" in before_examples, (
            "CLAUDE.md 3.3: the target-language index entry may only fill "
            "examples/synonyms/antonyms when it IS the transcript language."
        )

    def test_definitions_are_not_gated(self):
        # The partner, and the reason the gate is narrow: the DEFINITION is
        # allowed to change language. A gate that also blocked definitions
        # would silently disable --def-lang.
        from pipeline import definition
        src = inspect.getsource(definition.fetch_definition)
        target_branch = src.split("wiktdata.lookup(query_lemma")[1]
        before_definition = target_branch.split("definition     = entry.definition")[0]
        assert "target_language == language" not in before_definition

    def test_wordnet_receives_the_original_lemma_not_the_translation(self):
        # Same constraint, different path. Passing query_lemma here writes
        # the target language's synonyms into a native field -- the original
        # WordNet bug (SESSION.md 6.12).
        from pipeline import definition
        src = inspect.getsource(definition.fetch_definition)
        call = src.split("_wordnet_synonyms_antonyms(")[1].split(")")[0]
        assert call.startswith("lemma"), (
            f"CLAUDE.md 3.3: expected the original lemma, got {call!r}"
        )
