# CLAUDE.md — Tango

Read this file first in every session. It is the authoritative description of
the project, its constraints, and how to work on it.

---

## 1. Project objective

Tango is a Python command-line pipeline that converts YouTube video transcripts
into Anki flashcard packages for language learners.

A user supplies a YouTube video ID and a target Anki deck name. The pipeline
extracts the transcript, identifies vocabulary, checks the user's existing deck
for duplicates, fetches definitions and example sentences, and produces an
importable `.apkg` file.

The long-term goal is a multi-surface product: this CLI, a FastAPI backend, a
web application, and a Google Chrome extension that surfaces new vocabulary as
a video plays. Only the CLI exists today.

**Repository:** https://github.com/AlphaNerdFx/Tango
**Virtual environment:** `.tangovenv` — do NOT create `.venv`, it is the wrong name
**Python:** 3.10, running in WSL2 on Windows 11
**Current tag:** v0.4.5
**Working toward:** v0.4.6

---

## 2. Architecture summary

Ten modules in a linear pipeline. One video per invocation. One `.apkg` per
run. All state in local SQLite. No server component. No async.

```
YouTube video ID
  -> language.py       resolve BCP-47 code from flag or deck name
  -> transcript.py     fetch subtitles, clean text
  -> nlp.py            spaCy tokenize, lemmatize, POS filter
  -> deck.py           AnkiConnect duplicate check, fuzzy match
  -> definition.py     dual-source definition and example fetch
  -> wiktdata.py       offline Wiktionary index, non-English definitions
  -> translation.py    (optional) translate lemma for cross-language definitions
  -> cards.py          genanki .apkg generation
  -> state.py          SQLite run tracking
```

Full detail in `ARCHITECTURE.md`.

---

## 3. Hard constraints — never violate these

### 3.1 ANKI_MODEL_ID and ANKI_DECK_ID must not change

Current values, verified against the actual repository:

```
MODEL_ID = 1607392319
DECK_ID  = 2059400110
```

genanki uses these integers to identify the card model and deck across Anki
imports. Changing either makes Anki treat every existing user card as belonging
to a different template, permanently destroying review history. There is no
undo.

If a task appears to require changing them, stop and ask.

### 3.2 Card field order is positional and must match exactly

genanki maps note fields to model fields by index, not by name. A mismatch
between the field list in `_build_model()` and the values list in
`_build_note()` silently writes content into the wrong card section with no
error, no warning, and a valid `.apkg` file.

The canonical order, ten fields:

```
0  Word
1  Class
2  Definition
3  1st Example Sentence
4  2nd Example Sentence
5  Example from Youtube Video
6  Synonyms
7  Antonyms
8  VideoID
9  Source
```

`_build_fallback_note()` must produce the same ten values in the same order.

### 3.3 Examples, synonyms, and antonyms stay in the transcript language

Definitions and grammatical class may be in a different language when
`DEF_LANG` is set. Example sentences, synonyms, and antonyms must always be in
the original transcript language. This is the core pedagogical decision —
learners need to see words in native context.

This constraint has been violated twice. The WordNet bug (see `SESSION.md`),
and cross-language mode taking examples, synonyms and antonyms from the
target-language index entry, so a German video with `--def-lang ru` shipped
Russian sentences. The second shipped and was caught by a coverage sweep
noticing an impossible number, not by a test. See ARCHITECTURE.md 8.25.

Three call sites populate these fields and each needs the same gate:
Merriam-Webster, the target-language index, and the transcript-language
index. Only the last may fill them unconditionally.

### 3.4 Validate the lemma, not the surface form

The vocabulary dictionary produced by `nlp.py` is keyed by
`token.lemma_.lower()`. Every filter in `_is_valid_token()` must therefore
inspect `token.lemma_`, not `token.text`.

This constraint has been violated twice. A length check on `token.text`
produced single-letter cards. An `is_alpha` check on `token.text` blocked
legitimate hyphenated compounds. Both are fixed. Do not reintroduce either.

### 3.5 Unit tests must not require external services

No test in the default run may require network access, a running Anki instance,
or an installed spaCy or translation model. Tests requiring those use
`@pytest.mark.integration` and are excluded by default via `pyproject.toml`.

### 3.6 No new heavy runtime dependencies in the base install

PyTorch already adds roughly 1.5GB via argostranslate. New heavy dependencies
go in optional groups in `pyproject.toml`, not the base `dependencies` list.

---

## 4. Coding standards

### 4.1 Naming

- Modules: lowercase, single word where possible (`transcript.py`, `deck.py`)
- Private functions: leading underscore (`_build_note`, `_is_valid_token`)
- Constants: UPPER_SNAKE at module level, defined in `config.py` where they are
  configuration rather than implementation detail
- Custom exceptions: descriptive, ending in `Error`
  (`AnkiNotRunningError`, `LanguageResolutionError`)
- Dataclasses for structured returns (`DefinitionResult`, `MatchResult`,
  `PackageResult`, `TranscriptResult`, `DeckCheckResult`)

### 4.2 Formatting

- `black` with default settings, line length 100
- `ruff` for linting, config in `pyproject.toml`
- Section separators in long modules using a comment rule:
  `# ── Section name ─────────────────────────`
- Type hints on all public function signatures
- `from __future__ import annotations` at the top of every module

### 4.3 Documentation

Every public function has a docstring covering what it does, its arguments,
its return value, and every exception it raises.

Non-obvious decisions get an inline comment explaining *why*, not *what*. The
code already says what. Examples of comments worth keeping:

```python
# Validate the LEMMA, not the surface form. The vocabulary dict is
# keyed by token.lemma_.lower(), so that is the string that must
# pass every filter. A 3-char surface form can lemmatize to 1 char.
```

### 4.4 Error handling

The pipeline must not produce a Python traceback for any expected failure. API
errors, missing models, unreachable services, and invalid input all produce a
formatted CLI message and a non-zero exit code.

Every module defines its own exception types rather than raising generic
`Exception`. Library exceptions are caught and re-raised as typed module
exceptions with actionable messages that tell the user what to do.

---

## 5. Testing philosophy

**Tests encode intent, not implementation.** A test that fails after a refactor
either caught a regression or encoded an assumption that changed. Determine
which before editing either the test or the code.

**Never edit a test to make it pass** without first confirming the test itself
was wrong. Changing a test to match broken code hides bugs.

**When fixing a bug caused by checking the wrong variable, write two tests** —
one that fails if you check the wrong one, one that fails if you check neither.
A single test can be satisfied accidentally. A matched pair pins the intent.

Example from this project:

```python
def test_surface_form_lemmatizing_to_single_char_filtered(self):
    t = _make_token("est", "e", "VERB")     # long text, short lemma -> reject
    assert not _is_valid_token(t)

def test_short_surface_form_with_valid_lemma_passes(self):
    t = _make_token("va", "aller", "VERB")  # short text, long lemma -> accept
    assert _is_valid_token(t)
```

**Watch for fixtures that make bugs inexpressible.** The mock helper
`_make_token()` was called with identical `text` and `lemma` values in every
early test. That fixture pattern made the surface-form-versus-lemma bug
impossible to write a failing test for. Any new test touching that distinction
must pass different values for the two arguments.

---

## 6. Commands

### Setup

Run this first in any new environment, and whenever something behaves oddly —
nearly every failure investigated in this project has been setup rather than
logic, and none of it was visible from the failure itself:

```bash
make doctor                   # or: python -m pipeline --doctor
```

It reports spaCy models, dictionary indexes, translation pairs, the MW key
and AnkiConnect reachability, and prints the command that fixes anything
missing.

```bash
make all                      # venv + install + spaCy model + NLTK data
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python -m spacy download fr_core_news_md     # French is pinned to "md", see issue #13
python -m spacy download es_core_news_sm     # or any other code in language.SPACY_MODELS
python -m nltk.downloader wordnet omw-2.0     # not omw-1.4 -- this NLTK version silently ignores it
make dictionary LANGUAGE=fr                  # offline Wiktionary definitions, see below
```

Non-English definitions require the offline Wiktionary index. Without it,
every non-English card shows "No definition found" -- no online source has
this data (issue #1). Large one-time download per language, then offline:

```bash
make dictionary LANGUAGE=fr           # or any code in language.SPACY_MODELS
python -m pipeline --build-dictionary fr
```

### Run

```bash
make run VIDEO_ID=<id> DECK="<deck name>"
make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr
make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr DEF_LANG=en
make review DECK="<deck name>"          # process review.json decisions
make backlog DECK="<deck name>"         # process SQLite backlog
python -m pipeline --list-languages     # list supported language codes
python -m pipeline --help
```

Non-interactive run (defers all queued words to `review.json`):

```bash
echo "s" | make run VIDEO_ID=<id> DECK="<deck>" LANGUAGE=fr
```

### Test

```bash
make test          # unit only, no external deps, ~90s
make test-all      # includes integration tests
PYTHONPATH=src python -m pytest tests/test_nlp.py -q
PYTHONPATH=src python -m pytest tests/ -m "not integration" -q
```

Expected: 734 passing, 24 deselected. The count drifts as tests are added —
trust `make test` over this number, and update it here when it moves.

```bash
make coverage      # unit tests plus a per-module line-coverage report
```

Currently 88% overall, `__main__.py` at 82%. Every bug found by coverage
work so far has been wiring between modules rather than logic inside one.
See ARCHITECTURE.md section 10.

### Quality

```bash
make format        # black
make lint          # ruff
make typecheck     # mypy
```

### Maintenance

```bash
make clean                 # remove venv, output, caches (prompts first)
make translate-setup       # install argostranslate + libretranslate
make translate-stop        # kill local LibreTranslate server
rm -f pipeline.db          # ONLY when a schema change requires it
```

---

## 7. Preferred workflow

1. Read the file before changing it. Do not infer behaviour from names.
2. Before an architectural change, read `docs/ADR_v0.4.0.pdf`. If the proposal
   contradicts a recorded decision, say so explicitly and argue why the original
   reasoning no longer holds.
3. State confidence on every claim about behaviour: `[Certain]` if you read the
   code, `[Likely]` if inferring, `[Guessing]` if unverified.
4. One concern per change. If you notice a second problem while fixing the
   first, report it and ask before addressing it.
5. Run the relevant tests after every change. Paste the real output. Never
   report a change as working because it should work.
6. When a test fails, diagnose before fixing.
7. Commit with conventional-commit prefixes: `feat:`, `fix:`, `docs:`, `chore:`.
   One concern per commit.

---

## 8. Things to avoid

- Do not change `MODEL_ID` or `DECK_ID`.
- Do not reorder card fields.
- Do not add a length or alphabetic check on `token.text` in `_is_valid_token`.
- Do not pass `query_lemma` to any function expecting the original lemma.
- Do not add tests that require network, Anki, or installed models to the
  default run.
- Do not run `pip install tango` — that is an unrelated PyPI package. Install
  this project with `pip install -e .` from the repo root.
- Do not search the filesystem broadly when a file is not found. First
  hypothesis should be that it does not exist.
- Do not delete `pipeline.db` without a stated reason. It contains a definition
  cache that is expensive to rebuild.
- Do not commit `.env`, `pipeline.db`, `review.json`, `output/`, or `.tangovenv/`.

---

## 9. Reference documents

```
docs/ADR_v0.4.0.pdf              Architecture decisions with rationale
docs/SAD_v0.4.0.pdf              System architecture
docs/SRD_v0.4.0.pdf              Software requirements, CLI spec, schemas
docs/PRD_v0.4.0.pdf              Product requirements, user stories
docs/Code_Walkthrough.pdf        Function-by-function explanation
ARCHITECTURE.md                  This repo, full system detail
SESSION.md                       Current working state
TASKS.md                         Prioritised remaining work
```
