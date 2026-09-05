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
        # "Verb" rather than "verb": the note is built with language="de", and
        # since v0.5.3 Class is written in the definition's language. Still a
        # positional check, and now also proof the label reached the right slot.
        assert at["Class"] == "Verb"
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

    def test_every_antonym_lookup_uses_the_transcript_language(self):
        # The ConceptNet antonym index (ADR-010) is the fourth thing that
        # fills a field describing the word shown, and 3.3 has been violated
        # three times by exactly that: something added beside the gate
        # rather than inside it. Both call sites, not one -- a field that is
        # gated on one path and not the other disagrees with itself.
        from pipeline import definition
        functions = (
            definition.fetch_definition,
            definition._fetch_definition_or_fallback_example,
        )
        calls = [
            block.split(")")[0]
            for function in functions
            for block in _code_only(function).split("antonym_index.lookup(")[1:]
        ]
        assert len(calls) == len(functions), (
            "CLAUDE.md 3.3: every path that fills the antonym field must go "
            f"through the same lookup. Found {len(calls)} calls across "
            f"{len(functions)} paths."
        )
        for call in calls:
            assert call.startswith("lemma, language"), (
                "CLAUDE.md 3.3: an antonym describes the word shown, so it "
                "is looked up as (lemma, language) -- never query_lemma or "
                f"target_language. Got {call!r}"
            )

    def test_the_antonym_index_cannot_store_a_cross_language_pair(self):
        # The partner, and the structural half. The call site above is
        # correct today; this asserts the index could not serve a
        # cross-language antonym even if a call site were wrong tomorrow,
        # which is the second lock pronunciation got and examples never had.
        from pipeline import antonyms
        src = _code_only(antonyms._parse)
        assert "start[2] != end[2]" in src, (
            "CLAUDE.md 3.3: the antonym index must drop any pair whose two "
            "ends are in different languages, before it is ever stored."
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
        """The function's code, without docstring or comments, see _code_only."""
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


# -- Every deliberate failure is a TangoError ---------------------------------

class TestEveryDeliberateFailureIsATangoError:
    """
    v0.9.0. `main()` has to tell a failure this project raises on purpose
    from a genuine bug, because the two need opposite messages: one says
    what to do about it, the other says "please report this".

    Without a shared base it could only catch `Exception`, so a corrupt
    database or a closed Anki was reported to the user as a bug in Tango and
    sent them to open an issue about their own environment.

    This scans for the mistake rather than listing the classes, because the
    failure mode is a *new* exception added later without the base. That is
    exactly the kind of thing prose asks a reviewer to notice and a test
    notices instead.
    """

    @staticmethod
    def _exception_classes():
        import importlib
        import inspect

        from pipeline import TangoError

        root = Path(__file__).resolve().parent.parent / "src" / "pipeline"
        found = []
        for path in sorted(root.glob("*.py")):
            if path.stem == "__init__":
                continue
            module = importlib.import_module("pipeline.%s" % path.stem)
            for name, obj in vars(module).items():
                if (inspect.isclass(obj)
                        and issubclass(obj, BaseException)
                        and obj.__module__ == module.__name__):
                    found.append((path.name, name, obj, TangoError))
        return found

    def test_the_project_defines_exceptions_at_all(self):
        # Guards the scan itself: if the discovery breaks, the two tests
        # below would pass by finding nothing.
        assert len(self._exception_classes()) >= 15

    def test_every_module_exception_inherits_tango_error(self):
        offenders = [
            "%s: %s" % (fname, name)
            for fname, name, obj, base in self._exception_classes()
            if not issubclass(obj, base)
        ]
        assert not offenders, (
            "These are raised deliberately but do not inherit TangoError, so "
            "main() will report them to the user as bugs in Tango:\n  "
            + "\n  ".join(offenders)
        )

    def test_tango_error_does_not_swallow_control_flow(self):
        # SystemExit and KeyboardInterrupt must never be TangoErrors: the
        # first carries Typer's exit codes, the second is Ctrl-C.
        from pipeline import TangoError

        assert not issubclass(SystemExit, TangoError)
        assert not issubclass(KeyboardInterrupt, TangoError)
        assert issubclass(TangoError, Exception)


# -- Every environment variable is declared -----------------------------------

class TestEnvironmentKeysAreDeclared:
    """
    v0.9.0. A setting that nothing reads is the quietest failure there is:
    it looks applied and does nothing. A real .env on 5 September 2026 held
    `SPACY_MODEL=en_core_web_sm`, which looks exactly like it chooses the
    model and is read by nothing, and `API_DELAY`, a leftover. `tango
    doctor` now reports them, which only works while the declared list is
    accurate.

    So the list is checked against the code both ways: nothing read may be
    missing from it, and nothing in it may be unread. Either direction going
    stale turns the report into noise or, worse, into a confident all-clear.
    """

    @staticmethod
    def _keys_read_in_source() -> set:
        root = Path(__file__).resolve().parent.parent / "src" / "pipeline"
        found = set()
        for path in root.glob("*.py"):
            # Comment lines are dropped first. This scan reads code, and a
            # comment that mentions getenv("NAME") to explain the scan
            # itself was picked up as a key on the first run.
            text = "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            )
            found |= set(re.findall(r'getenv\(\s*["\']([A-Z_][A-Z0-9_]*)["\']', text))
            found |= set(re.findall(r'environ\[\s*["\']([A-Z_][A-Z0-9_]*)["\']', text))
            found |= set(re.findall(
                r'_resolve_path\(\s*["\']([A-Z_][A-Z0-9_]*)["\']', text))
        return found

    def test_the_scan_finds_something(self):
        # Guards the scan itself. If the regexes stop matching, the two
        # tests below would pass by comparing empty sets.
        assert len(self._keys_read_in_source()) >= 15

    def test_every_key_the_code_reads_is_declared(self):
        from pipeline.config import KNOWN_ENV_KEYS

        missing = sorted(self._keys_read_in_source() - set(KNOWN_ENV_KEYS))
        assert not missing, (
            "These are read by the code but missing from KNOWN_ENV_KEYS, so "
            "`tango doctor` would report a user's correct setting as one "
            "that does nothing:\n  " + "\n  ".join(missing)
        )

    def test_every_declared_key_is_actually_read(self):
        from pipeline.config import KNOWN_ENV_KEYS, UNREAD_BY_DESIGN

        unread = sorted(set(KNOWN_ENV_KEYS) - UNREAD_BY_DESIGN
                        - self._keys_read_in_source())
        assert not unread, (
            "These are declared known but nothing reads them, so doctor "
            "would stay silent about a setting that does nothing:\n  "
            + "\n  ".join(unread)
        )

    def test_the_example_file_documents_the_real_names(self):
        # .env.example is what a user copies. A name in it that nothing
        # reads teaches the wrong setting; this repository shipped
        # SPACY_MODEL_SIZE_OVERRIDE correctly but had no guard against the
        # next one being wrong.
        from pipeline.config import KNOWN_ENV_KEYS

        root = Path(__file__).resolve().parent.parent
        text = (root / ".env.example").read_text(encoding="utf-8")
        documented = {
            line.split("=", 1)[0].strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#") and "=" in line
        }
        undeclared = sorted(documented - set(KNOWN_ENV_KEYS))
        assert not undeclared, (
            "Documented in .env.example but not a key this project knows:\n  "
            + "\n  ".join(undeclared)
        )


# -- The capability table matches the real mappings ---------------------------

class TestDocumentedLanguageCountsAreTrue:
    """
    The counts in the docs must match the real mapping.

    CLAUDE.md records that this file has carried a stale coverage number
    four separate times, and the language count was stale in four places at
    once: README.md, two docstrings and a comment in language.py, and the
    FAQ all said "40 languages" while the map held 45 codes. The README went
    further and advertised Arabic as supported, which spaCy has no model
    for, so a user with an Arabic deck followed the README into a run that
    could not start.

    Prose asked a reader to notice. This notices.
    """

    def _counts(self):
        from pipeline.language import LANGUAGE_MAP, get_spacy_model

        codes = set(LANGUAGE_MAP.values())
        usable = set()
        for code in codes:
            try:
                get_spacy_model(code)
            except Exception:
                continue
            usable.add(code)
        return len(codes), len(usable)

    def test_the_readme_states_the_real_counts(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "README.md").read_text(encoding="utf-8")
        recognised, usable = self._counts()
        flat = " ".join(text.split())
        assert f"recognises {recognised} language codes" in flat
        assert f"{usable} of them can produce cards" in flat

    def test_the_readme_does_not_advertise_a_language_that_cannot_work(self):
        # The specific failure this class was written for. Every language
        # named in the "can produce cards" sentence must actually resolve.
        from pipeline.language import LANGUAGE_MAP, get_spacy_model

        root = Path(__file__).resolve().parent.parent
        text = (root / "README.md").read_text(encoding="utf-8")
        flat = " ".join(text.split())      # the sentence wraps in the file
        start = flat.index("can produce cards:")
        listed = flat[start:flat.index(".", start)]
        for name in re.findall(r"[A-Z][a-z]+", listed):
            code = LANGUAGE_MAP.get(name.lower())
            if code is None:
                continue
            get_spacy_model(code)   # raises if the README is lying

    def test_every_count_written_in_language_py_is_the_real_one(self):
        """
        Every occurrence, not just the first.

        An `in` check passes while a second site stays wrong, and that is
        the exact shape of the bug: the count was stale in four places at
        once, and language.py alone states it twice.
        """
        from pipeline import language

        recognised, _ = self._counts()
        flat = " ".join(Path(language.__file__).read_text(encoding="utf-8").split())
        written = re.findall(r"recognises (\d+) (?:language )?codes", flat)
        assert written, "language.py no longer states the count at all"
        assert all(int(n) == recognised for n in written), written

    def test_every_count_written_in_the_readme_is_the_real_one(self):
        root = Path(__file__).resolve().parent.parent
        flat = " ".join((root / "README.md").read_text(encoding="utf-8").split())
        recognised, _ = self._counts()
        written = re.findall(r"recognises (\d+) (?:language )?codes", flat)
        assert written, "the README no longer states the count at all"
        assert all(int(n) == recognised for n in written), written


class TestLanguageCapabilitiesAreDerived:
    """
    v0.11.0. Every per-language resource lives in a different module and
    covers a different set: 24 spaCy models, 19 WordNet, 22 antonym
    languages, 7 part-of-speech label tables, 4 filler stoplists. Nothing
    told a user which they had until a run failed.

    `language_capabilities()` answers that, and the only way it stays true
    is by being derived. This checks it against the real mappings, in both
    directions, the same shape as the environment-key scan above.
    """

    def test_cards_means_a_spacy_model_exists(self):
        from pipeline.language import SPACY_MODELS, language_capabilities

        for code in SPACY_MODELS:
            assert language_capabilities(code)["cards"] is True, code

    def test_a_language_with_no_model_cannot_make_cards(self):
        from pipeline.language import LANGUAGE_MAP, SPACY_MODELS, language_capabilities

        modelled = set(SPACY_MODELS)
        for code in set(LANGUAGE_MAP.values()):
            base = code.split("-")[0]
            if base not in modelled and code not in modelled and base != "no":
                assert language_capabilities(code)["cards"] is False, code

    def test_wordnet_matches_the_omw_table(self):
        from pipeline.definition import _OMW_LANGUAGE_CODES
        from pipeline.language import language_capabilities

        for code in _OMW_LANGUAGE_CODES:
            assert language_capabilities(code)["wordnet"] is True, code
        # German is the case that motivated all of this.
        assert language_capabilities("de")["wordnet"] is False

    def test_pos_labels_match_their_table(self):
        from pipeline.language import POS_LABELS, language_capabilities

        for code in POS_LABELS:
            assert language_capabilities(code)["pos_labels"] is True, code

    def test_every_model_appears_in_the_report(self):
        # A model with no name in LANGUAGE_MAP was invisible in a name-based
        # listing, which is how lt, mk and sl went unreachable by deck name.
        from pipeline.language import SPACY_MODELS, capability_report

        listed = set()
        for _name, code, _cap in capability_report():
            listed.add(code)
            listed.add(code.split("-")[0])
        missing = sorted(set(SPACY_MODELS) - listed - {"nb"})
        assert not missing, "models absent from the report: %s" % missing

    def test_a_model_with_no_name_is_still_listed(self):
        # Defence for the next one. Every model has a name today, so the
        # fallback branch is not exercised by any real language and a
        # mutation removing it went unnoticed. This simulates the situation
        # it exists for: a model added without a LANGUAGE_MAP entry, which
        # is exactly how lt, mk and sl became unreachable by deck name.
        from unittest.mock import patch

        import pipeline.language as lang

        with patch.dict(lang.SPACY_MODELS, {"xx": "xx_core_news_sm"}):
            codes = {code for _n, code, _c in lang.capability_report()}
        assert "xx" in codes, "an unnamed model vanished from the report"

    def test_the_three_formerly_unreachable_languages_have_names(self):
        from pipeline.language import resolve_language_code

        for deck, expected in [("Lithuanian", "lt"), ("Macedonian", "mk"),
                               ("Slovenian", "sl")]:
            assert resolve_language_code(None, deck) == expected

    def test_traditional_chinese_says_it_uses_the_simplified_model(self):
        # It resolves to zh_core_web_sm, the only Chinese pipeline spaCy
        # ships. Silently is the problem, not the resolution.
        from pipeline.language import get_spacy_model, language_caveat

        assert get_spacy_model("zh-TW") == "zh_core_web_sm"
        assert "Simplified" in (language_caveat("zh-TW") or "")
