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
from pathlib import Path

import pytest

from pipeline import cards
from pipeline.config import DECK_ID, MODEL_ID
from pipeline.definition import DefinitionResult


def _code_only(fn) -> str:
    """
    A function's CODE, with docstring and comments stripped.

    Several constraints here are checked by reading source, and the code that
    implements a constraint tends to *explain* the mistake it avoids. A plain
    source scan then finds the explanation and reports the warning as the
    defect: `_is_valid_token`'s docstring argues at length against checking
    `token.is_alpha`, and `fetch_definition`'s comments discuss `entry.ipa`
    for the same reason. Round-tripping through ast keeps only what executes.
    """
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    if ast.get_docstring(node):
        node.body = node.body[1:]
    return ast.unparse(node)

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

    def test_the_merriam_webster_call_site_is_gated(self):
        # The first of the three call sites, and one the tests above never
        # looked at: they slice the source from `wiktdata.lookup(query_lemma`
        # onwards, which begins AFTER the Merriam-Webster block. Deleting
        # this gate went completely undetected in a mutation run.
        #
        # It matters because MW is an English dictionary. Ungated, a German
        # video with --def-lang en puts English MW sentences on the card --
        # the same defect as 8.25, from a different source.
        from pipeline import definition
        src = inspect.getsource(definition.fetch_definition)
        mw_block = (
            src.split('_actual_source = "merriam-webster"')[1]
               .split("native_ex1 = mw.example_dict")[0]
        )
        assert "target_language == language" in mw_block, (
            "CLAUDE.md 3.3: Merriam-Webster examples may only be used when "
            "the target language IS the transcript language."
        )

    def test_the_transcript_language_call_site_is_not_gated(self):
        # The third call site, and the partner to the two gate assertions.
        # This entry IS in the transcript language, so it is the one source
        # allowed to fill these fields unconditionally. Gating it too would
        # satisfy every other test here while silently emptying the fields
        # on exactly the cards the native fallback exists to rescue (8.26).
        from pipeline import definition
        src = inspect.getsource(definition.fetch_definition)
        native_block = (
            src.split('_actual_source = "wiktionary-native"')[1]
               .split("native_syns = entry.synonyms")[0]
        )
        assert "target_language == language" not in native_block, (
            "CLAUDE.md 3.3: the transcript-language entry is the one source "
            "that may fill examples/synonyms/antonyms unconditionally."
        )

    def test_pronunciation_is_resolved_from_the_transcript_language(self):
        # Pronunciation joined this constraint in v0.5.1, after shipping the
        # violation: `ipa = ipa or entry.ipa` sat next to the gate without
        # being inside it, and `entry` in that branch is the TRANSLATED
        # word's index row. A German video with --def-lang fr put maison's
        # /mɛ.zɔ̃/ on a card reading "Haus". ARCHITECTURE 8.34.
        from pipeline import definition
        src = inspect.getsource(definition.fetch_definition)
        call = src.split("_resolve_pronunciation(")[1].split(")")[0]
        assert call.startswith("lemma, language"), (
            "CLAUDE.md 3.3: pronunciation must describe the word printed on "
            f"the card -- expected (lemma, language), got {call!r}"
        )

    def test_pronunciation_has_exactly_one_source(self):
        # The partner, and the structural half. The bug was not a wrong
        # argument, it was pronunciation being assigned per definition
        # branch at all -- the branches differ in which language they hold.
        # One assignment cannot disagree with itself.
        from pipeline import definition
        # Comments stripped first. This function's comments *discuss*
        # entry.ipa at length, explaining why it must not be read there --
        # a plain source scan finds the explanation and reports it as the
        # defect. Same trap as the 3.4 tests above.
        src = _code_only(definition.fetch_definition)
        assert "entry.ipa" not in src and "entry.audio_url" not in src, (
            "CLAUDE.md 3.3: fetch_definition must not read pronunciation off "
            "a definition-source entry. Use _resolve_pronunciation()."
        )
        assert src.count("_resolve_pronunciation(") == 1

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


# ── 3.4 Validate the lemma, not the surface form ─────────────────────────────

class TestConstraint34ValidateTheLemma:
    """
    The vocabulary dict is keyed by `token.lemma_.lower()`, so every filter
    in _is_valid_token() must inspect the lemma. Violated twice: a length
    check on token.text produced single-letter cards, and an is_alpha check
    on token.text blocked hyphenated compounds.

    test_nlp.py pins the *behaviour* with the matched pair CLAUDE.md 5
    describes (a long surface form with a short lemma must be rejected; a
    short surface form with a real lemma must pass). This pins the
    *constraint* at the source, which is what catches the check being
    reintroduced somewhere the fixture pair happens not to reach.
    """

    @staticmethod
    def _source() -> str:
        """The function's code, without docstring or comments — see _code_only."""
        from pipeline import nlp
        return _code_only(nlp._is_valid_token)

    def test_no_length_check_on_the_surface_form(self):
        src = self._source()
        assert not re.search(r"len\(\s*token\.text", src), (
            "CLAUDE.md 3.4/8: a length check on token.text is the bug that "
            "produced single-letter cards. Check the lemma."
        )

    def test_no_alphabetic_check_on_the_surface_form(self):
        src = self._source()
        assert not re.search(r"token\.(is_alpha|text\.isalpha)", src), (
            "CLAUDE.md 3.4/8: an is_alpha check on the surface form rejects "
            "legitimate hyphenated compounds. Check the lemma."
        )

    def test_validity_is_actually_decided_on_the_lemma(self):
        # The partner. The two above pass trivially on a function that
        # checks nothing at all, so one of them has to assert the positive.
        src = self._source()
        assert "_effective_lemma(token)" in src, (
            "CLAUDE.md 3.4: _is_valid_token must decide validity on the "
            "lemma the vocabulary dict is keyed by."
        )


# ── 3.5 Unit tests must not require external services ────────────────────────

class TestConstraint35NoExternalServicesByDefault:
    """
    Nothing in the default run may need network, a running Anki, or an
    installed spaCy/translation model. The mechanism is the `integration`
    marker plus the default deselection in pyproject.toml -- so these test
    the mechanism, not the individual tests.
    """

    @staticmethod
    def _pytest_config() -> dict:
        try:
            import tomllib
        except ModuleNotFoundError:            # Python 3.10, this project
            import tomli as tomllib
        root = Path(__file__).resolve().parent.parent
        with open(root / "pyproject.toml", "rb") as fh:
            return tomllib.load(fh)["tool"]["pytest"]["ini_options"]

    def test_the_default_run_deselects_integration_tests(self):
        addopts = self._pytest_config()["addopts"]
        assert re.search(r"-m\s+['\"]?not integration", addopts), (
            "CLAUDE.md 3.5: pyproject's addopts is what keeps network- and "
            f"Anki-dependent tests out of the default run. Got: {addopts!r}"
        )

    def test_the_integration_marker_is_registered(self):
        markers = self._pytest_config()["markers"]
        assert any(m.startswith("integration:") for m in markers)

    def test_every_marker_used_in_the_suite_is_a_real_one(self):
        # The failure this exists for: the integration marker misspelled
        # with a letter dropped. pytest accepts an unknown marker silently,
        # the deselection expression never matches it, and a test that needs
        # the network joins the default run looking exactly like a unit test.
        #
        # The misspelling is described rather than written out, because this
        # scan reads the test files as text and would find its own example.
        # It did, on the first run.
        registered = {m.split(":")[0] for m in self._pytest_config()["markers"]}
        builtin = {
            "parametrize", "skip", "skipif", "xfail",
            "usefixtures", "filterwarnings",
        }
        allowed = registered | builtin

        used: dict[str, set[str]] = {}
        for path in Path(__file__).resolve().parent.glob("test_*.py"):
            for name in re.findall(r"@pytest\.mark\.(\w+)", path.read_text(encoding="utf-8")):
                used.setdefault(name, set()).add(path.name)

        unknown = {n: sorted(f) for n, f in used.items() if n not in allowed}
        assert not unknown, (
            f"CLAUDE.md 3.5: unregistered pytest marker(s) {unknown}. A typo "
            "here silently puts the test into the default run."
        )


# ── 3.6 No new heavy runtime dependencies in the base install ────────────────

class TestConstraint36NoHeavyBaseDependencies:
    """
    PyTorch already costs roughly 1.5GB through argostranslate. Anything of
    that weight belongs in an optional group, so `pip install -e .` stays
    something a new contributor can run.
    """

    # Packages that pull in a multi-hundred-MB tree, by name. Deliberately a
    # denylist of known offenders rather than an install-size measurement:
    # this has to fail in a unit run with no network and nothing installed.
    HEAVY = {
        "torch", "pytorch", "tensorflow", "jax", "transformers",
        "argostranslate", "libretranslate", "ctranslate2", "sentencepiece",
        "onnxruntime", "scipy", "sklearn", "scikit-learn", "pandas",
    }

    @staticmethod
    def _project() -> dict:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib
        root = Path(__file__).resolve().parent.parent
        with open(root / "pyproject.toml", "rb") as fh:
            return tomllib.load(fh)["project"]

    @staticmethod
    def _names(specs: list[str]) -> set[str]:
        return {re.split(r"[><=!~\[; ]", s.strip())[0].lower() for s in specs}

    def test_base_install_carries_nothing_heavy(self):
        base = self._names(self._project()["dependencies"])
        offenders = sorted(base & self.HEAVY)
        assert not offenders, (
            f"CLAUDE.md 3.6: {offenders} in the base dependencies. Heavy "
            "packages belong in [project.optional-dependencies]."
        )

    def test_translation_stack_is_still_optional(self):
        # The concrete case the constraint was written about. argostranslate
        # is the 1.5GB, and it must stay in the optional group it lives in.
        optional = self._project()["optional-dependencies"]
        assert "argostranslate" in self._names(optional["translation"])
        assert "argostranslate" not in self._names(self._project()["dependencies"])
