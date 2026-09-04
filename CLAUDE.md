# CLAUDE.md: Tango

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

**Tango is a CLI, and v1.0.0 is a finished CLI**, installable from a
package, running on Windows, macOS and Linux, on low-end and high-end
hardware alike.

A Chrome extension, a web or desktop app, deep-learning work, and
distribution to other ecosystems (npm, crates.io) are **out of scope**:
plausibly a separate expansion project sharing a common premise, not later
versions of this one. ROADMAP.md §4 records what that costs, almost
nothing now, provided the card payload in `cards.py` stays independent of
genanki, because what travels between ecosystems is the format, not the
code.

**Repository:** https://github.com/AlphaNerdFx/Tango
**Virtual environment:** `.tangovenv`, do NOT create `.venv`, it is the wrong name
**Python:** 3.10. Developed under WSL2 on Windows 11, but that is the
current environment, not a target: cross-platform support is a v0.10.0 goal.
The `ANKI_HOST` half of that is done, and was not what this file said it
was: the default has always been `http://localhost:8765`, and the gateway
IP someone read as the shipped default lived in an uncommitted `.env`. WSL
is now detected and handled by retrying rather than by a different default
(ARCHITECTURE 8.45). The `/mnt/c` path translation in `__main__.py` is
already conditional on `is_wsl()`.
**Current tag:** v0.9.0, nothing fails without saying why, released
5 September 2026. Every release before it fixed failures one at a time as
someone tripped over them; this one went looking. An unexpected error is a
message rather than a traceback, a corrupt or unwritable database is a typed
error naming the fix, the .apkg write is guarded at the one place in the
pipeline where failing costs the whole run, and `TangoError` gives the entry
point a way to tell a failure someone wrote a message for from a genuine
bug. `tango doctor` reports settings in `.env` that nothing reads, after two
were found doing nothing in a real one. `src/` now holds only the importable
package.

v0.8.2 before it was an install that looks after itself, released
4 September 2026. A missing spaCy model is offered before the run rather
than reported after the transcript is fetched; `tango uninstall` reports and
removes the indexes and caches that `pip uninstall` leaves behind (1.2 GB on
the development machine); there is a Dockerfile, built and run before
release; and something other than AnkiConnect answering on port 8765 is a
typed error rather than a traceback.

v0.8.1 was documentation for the people who can now install
it, released 3 September 2026. The README is also the PyPI description, and
every command in it assumed a cloned repository, so the shop window for a
package published that morning told its first visitors to run `make`. PyPI
freezes a description at upload time, so correcting the published page costs
a release. It also unstuck a version badge frozen at v0.5.3 for five
releases, and untracked a committed `.pyc`.

v0.8.0 the same day was packaged and installable, and **the first release on
PyPI: `pip install tango-anki`**. The
distribution is `tango-anki` because `tango` is an unrelated project; the
command a user types is still `tango`. It carries the name and its metadata,
`tango --version`, a WSL AnkiConnect fallback that needs no configuration
(ARCHITECTURE 8.45), `nltk` moved to a `[wordnet]` extra, and repairs to
eight messages that named flags v0.7.0 had deleted. Three planned items were
cut to v0.8.1 so the name could be claimed: first-run downloads, a
Dockerfile, and an uninstall that removes the indexes.

v0.7.0 before it was the command line as a product, released the same day.
It carries three items, none of which touches a card:
every mode is a subcommand (`tango run`, `review`, `backlog`, `languages`,
`doctor`, `setup`, `install-model`, `install-translation`,
`build-dictionary`, `build-antonyms`) behind a real console entry point, the
seven failures that stopped a run without naming a next step now name one,
and a long run reports per-phase timing plus a definition-phase progress
line. No migration: the notetype is unchanged and an existing collection
needs nothing done to it. The old flag surface is gone, broken deliberately
while the interface still has no installed users.

v0.6.0 before it was card quality, five items about what is *in* a field
rather than which fields exist: filler sounds no longer become cards,
inflection pointers no longer reach cards as definitions, the definition
cache key carries both languages, the run names the words that got no
definition instead of only counting them, and the antonym field has an
offline source of its own (ADR-010, which took a real French deck from 19.7%
to 34.8%). The 0.5.x line ran v0.5.0 (pronunciation on cards, the one
release needing a migration), v0.5.1 (pronunciation describes the word on
the card), v0.5.2 (audio plays inside the card) and v0.5.3 (part of speech
in the learner's language). Every tag has GitHub release notes.
**Also on main, and not part of either rung:** the translation install no
longer pulls 4.5 GB of CUDA nobody can call, which took `.tangovenv` from
5.9 GB to 2.2 GB. It shipped inside v0.6.0 rather than waiting for v0.12.0,
where the rest of the size work lives.
**Working toward:** v0.10.0, runs on any operating system. The
packaging rung moved forward three places on 27 August 2026, from v0.10.0:
it had sat behind cross-platform support and install size, which is the
ordering of a project polishing for users who cannot install it. Goals per
tag through v1.0.0 are in `ROADMAP.md`; the rule that picks the number is in
section 15; the commit and tag format is in section 16; release history is
in `CHANGELOG.md`.

---

## 2. Architecture summary

Thirteen modules. Nine are stages in a linear pipeline; `media.py` is called
by `cards.py` and `antonyms.py` by `definition.py` rather than being stages
of their own, `config.py` holds configuration, and `__main__.py` is the CLI.
One video per invocation. One `.apkg` per run. All state in local SQLite. No
server component. No async.

```
YouTube video ID
  -> language.py       resolve BCP-47 code from flag or deck name
  -> transcript.py     fetch subtitles, clean text
  -> nlp.py            spaCy tokenize, lemmatize, POS filter, drop fillers
  -> deck.py           AnkiConnect duplicate check, fuzzy match
  -> definition.py     dual-source definition and example fetch
       -> antonyms.py  offline ConceptNet antonym index, last source for that field
  -> wiktdata.py       offline Wiktionary index, non-English definitions
  -> translation.py    (optional) translate lemma for cross-language definitions
  -> cards.py          genanki .apkg generation
       -> media.py     download and cache pronunciation audio, paced
  -> state.py          SQLite run tracking
```

Full detail in `ARCHITECTURE.md`.

---

## 3. Hard constraints: never violate these

> **Enforced, not just documented.** All six constraints in this section now
> have tests in `tests/test_hard_constraints.py` that fail on the specific
> mistake each one describes, and every one of those tests has been
> mutation-verified. Prose asked reviewers to notice; the tests notice. If
> you change something here, change the test with it and re-run the mutation.
>
> 3.4 is pinned twice over: `test_nlp.py` pins the *behaviour* with the
> matched pair described in section 5, and `test_hard_constraints.py` pins
> the *constraint* by reading `_is_valid_token`'s source. 3.5 and 3.6 test
> the mechanism rather than each test or package, the marker deselection in
> `pyproject.toml`, that no marker in the suite is misspelled into the
> default run, and that nothing heavy sits in the base dependency list.

### 3.1 ANKI_MODEL_ID and ANKI_DECK_ID must not change

Current values, verified against the actual repository:

```
MODEL_ID = 1607392321
DECK_ID  = 2059400110
```

`MODEL_ID` was changed once, on 14 August 2026, and this is the only time it
may ever move. The previous value, `1607392319`, is the model ID in
**genanki's README example**, copied along with the tutorial and paired
there with a notetype named `Simple Model`. Any collection that ever
imported a deck built from that tutorial already has the ID taken, and Anki
will not reuse it, it forks a new notetype with a bumped ID and a suffixed
name. Measured on a real collection: `1607392319` held a 7-field
`Simple Model` with **zero** notes, while this pipeline's 2135 cards sat on
`1607392321`, the ID Anki assigned when it forked. Five separate forked
notetypes had accumulated that way. The constraint was real; it was
protecting an ID that held none of our cards. See ARCHITECTURE.md 8.31.

Note the ID resolves through `ANKI_MODEL_ID`, so `.env` overrides the
default. Pinning only the resolved value is not enough, a test doing that
passes on any machine with a `.env` while the source default drifts. That
gap was found by mutation and the tests now pin the source literal too.

genanki uses these integers to identify the card model and deck across Anki
imports. Changing either makes Anki treat every existing user card as belonging
to a different template, permanently destroying review history. There is no
undo.

If a task appears to require changing them, stop and ask.

### 3.2 Card fields have one source of truth: `cards.FIELDS`

genanki still maps note fields to model fields by index, and a mismatch
still writes content into the wrong card section with no error, no warning,
and a valid `.apkg`. What changed on 14 August 2026 is that there is no
longer anything to keep in sync by hand.

`cards.FIELDS` is the single ordered tuple of field names. `_build_model()`
generates its field list from it, and both note builders pass a **name-keyed
dict** through `_note_fields()`, which does the positional mapping once:

```
0  Word                         6  Synonyms
1  Class                        7  Antonyms
2  Definition                   8  VideoID
3  1st Example Sentence         9  Source
4  2nd Example Sentence        10  IPA             (ADR-009 phase 1)
5  Example from Youtube Video  11  Pronunciation   (ADR-009 phase 1)
```

What this buys, and why the old rule is retired:

- A **misspelled** field name raises `ValueError` instead of silently
  shifting every later field.
- An **omitted** field becomes `""` instead of shifting every later field.
- Reordering is one edit in one place and cannot desync the builders.
- The model can never disagree with the builders, because it is derived
  from the same tuple.

Two things that still hold. `Word` must stay at index 0, Anki treats a
note's first field as its identity, and `deck.py`'s duplicate check reads
the lowest-`order` field (ARCHITECTURE.md 8.22). And new fields are still
**appended**: indices 0-11 are what every already-imported card in every
user's collection is bound to. That range was 0-9 until v0.5.0 shipped IPA
and Pronunciation and collections were migrated onto the 12-field notetype,
so a new field is index 12.

**Appending is safe for the indices. It is not, by itself, safe for the
notetype.** Anki matches an incoming notetype by ID, and when that ID
already exists with a *different* field list it does not merge, it forks
a new notetype at a bumped ID with a suffixed name, leaves every existing
note behind on the old one, and puts the new cards on the fork. Measured,
not reasoned: importing the 12-field package into a collection holding the
10-field notetype at `1607392321` produced `YT Anki Pipeline,
Recognition-da2c0` at `1607392322` and stranded all 207 existing notes.
That is the same mechanism that moved this project's ID off genanki's
`1607392319` in the first place, twice, which is why the gap is +2.

So adding a field is a **two-part** change: append it to `cards.FIELDS`,
and let `deck.ensure_model_fields()` add it to the collection's notetype
before the import. `__main__._prompt_import()` already calls that, and a
failed alignment deliberately cancels the import rather than risking the
fork. Adding a field to a live notetype is the safe direction, verified,
all 207 notes kept byte-identical values and gained two empty fields, but
it is a schema change, so Anki will want a full sync afterwards.
ARCHITECTURE.md 8.32.

The payload dicts the builders construct are also the intended seam for the
planned web app and Chrome extension, card content keyed by name and
independent of genanki, so another surface can consume pipeline output
without reimplementing it.

### 3.3 Anything describing the word shown stays in the transcript language

**The rule is about the word on the card, not a list of field names.** The
card's `Word` is the transcript-language lemma. Every field that *describes
that word* must be in the transcript language:

| field | must match the word shown | may change language |
|---|---|---|
| Example sentences | ✅ | |
| Synonyms, Antonyms | ✅ | |
| IPA, Pronunciation | ✅ | |
| Definition | | ✅ under `--def-lang` |
| Class (part of speech) | | ✅ follows the definition |

Learners need the word in native context, and a pronunciation that belongs
to a different word is worse than none.

`Class` is a label on the definition, not a description of the word, so it
is written in whichever language the definition is written in: the
transcript language normally, and the `--def-lang` language when that is
set. A German word defined in French reads `nom`; the same word defined
natively reads `Substantiv`. Never `noun`, which is what wiktextract stores
and what every card showed until v0.5.3.

One line does this, in `cards.build_package()`:

```python
pos_language = def_language or language
```

The labels live in `language.POS_LABELS`, keyed by the tags the indexes
actually contain. A language with no table falls back to English, and a tag
with no entry is shown unchanged rather than dropped. Adding a language is
one row.

**Stated as a list of three fields, this constraint was violated three
times**, each time by someone adding a *fourth* thing beside the gate
without putting it inside:

1. The WordNet bug, passing `query_lemma` where `lemma` was meant (`SESSION.md`).
2. Examples, synonyms and antonyms taken from the target-language entry, so a
   German video with `--def-lang ru` shipped Russian sentences. Caught by a
   coverage sweep noticing an impossible number, not by a test.
   ARCHITECTURE.md 8.25.
3. **Pronunciation**, added directly above the gate in v0.5.0 and not inside
   it, so `--def-lang fr` put maison's `/mɛ.zɔ̃/` on a card reading `Haus`.
   ARCHITECTURE.md 8.34.

So the mechanism now matters more than the list. Pronunciation is resolved
**once**, by `_resolve_pronunciation(lemma, language)`, and never inside a
definition branch, the branches differ in which language they hold, and one
assignment cannot disagree with itself. Examples, synonyms and antonyms are
still gated per call site: Merriam-Webster, the target-language index, and
the transcript-language index, of which only the last may fill them
unconditionally.

Before adding any new field to a card, answer one question: **does it
describe the word shown?** If yes, it belongs on the transcript-language
side of this table and needs a test in `TestConstraint33FieldLanguage`.

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

New heavy dependencies go in optional groups in `pyproject.toml`, not the
base `dependencies` list.

This rule used to say PyTorch adds "roughly 1.5GB" via argostranslate.
**Measured 15 August 2026, that is a threefold understatement:**
`.tangovenv` is **5.9 GB**, of which `torch` is 1.1 GB, `nvidia` (the CUDA
runtime torch pulls in) is 2.7 GB and `triton` a further 689 MB, 4.5 GB,
76% of the install, none of it declared anywhere in `pyproject.toml` and
none of it useful on a machine without a GPU. `dictionaries/` adds 820 MB
on top.

**Fixed 26 August 2026, and the cause was one missing word.** The chain is
`argostranslate -> stanza -> torch>=1.3.0`, and that constraint names a
version but not a variant, so pip takes the default PyPI wheel, which since
torch 2.x is the CUDA build. `make translate-setup` now installs torch from
PyTorch's CPU index first, so pip sees the requirement satisfied.

**`.tangovenv` is now 2.2 GB, measured after the repair, not projected.**
The same day it was applied to this machine's existing environment:
`torch 2.13.0+cu130` replaced by `torch 2.13.0+cpu` (725 MB installed), and
`triton` plus sixteen `nvidia-*` distributions removed, freeing 3.7 GB.
`make test` passes and argostranslate and stanza import normally on the CPU
build.

Installing from the CPU index only helps a machine that has no torch yet. On
one that already has the CUDA build, pip reports "already satisfied" and
changes nothing, which is why `make translate-setup` now checks for a CUDA
build with no usable GPU and replaces it with `--force-reinstall --no-deps`,
then removes the orphans pip leaves behind. A CUDA build with a *working* GPU
is left alone. `--doctor` prints the same two commands for anyone repairing
an environment by hand.

A variant cannot be chosen in `pyproject.toml`, because choosing one means
choosing an index and PEP 508 has no way to say that. It belongs in the
install command. ARCHITECTURE.md 8.41.

Three things still follow. The optional group is doing less than it appears:
it keeps the dependency undeclared, not uninstalled, once anyone runs
`make translate-setup`. Nothing pins the variant, so a later plain
`pip install torch`, or any resolve of stanza's `torch>=1.3.0` in an
environment without torch, brings the CUDA wheel and its 3.7 GB straight
back. And a number in this file that nobody re-measured was wrong by 3x for
months, so see ROADMAP.md v0.12.0, where shrinking this is a release goal
with acceptance targets.

**The base install, measured 3 September 2026 in a clean venv: 334 MB
across 58 packages.** No torch, no translation stack. spacy and the numeric
stack it needs are 236 MB of that, 74%: spacy 119, numpy 41, blis 34,
numpy.libs 27, thinc 15. That is the floor while spacy is the NLP engine,
and spacy stays a base dependency because an install that cannot run
`tango run` is not an install.

`nltk` moved out on the same day, into a `wordnet` extra. It only
supplements the synonym and antonym fields, every import of it in
`definition.py` sits inside a function inside a `try/except` returning
empty lists, and that was verified rather than assumed: blocking the import
and calling both entry points returns `([], [])` rather than raising. So an
install without it is a working install with thinner synonyms. `dev`
depends on `tango-anki[wordnet]`, because dropping it from the base install
must not drop it from the test run.

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

**When fixing a bug caused by checking the wrong variable, write two tests**,
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

Run this first in any new environment, and whenever something behaves oddly,
nearly every failure investigated in this project has been setup rather than
logic, and none of it was visible from the failure itself:

```bash
make doctor                   # or: tango doctor
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
pip install -e ".[wordnet]"                   # optional: WordNet synonyms and antonyms
python -m nltk.downloader wordnet omw-2.0    # needs the line above; not omw-1.4, this NLTK version ignores it
make dictionary LANGUAGE=fr                  # offline Wiktionary definitions, see below
```

Non-English definitions require the offline Wiktionary index. Without it,
every non-English card shows "No definition found" -- no online source has
this data (issue #1). Large one-time download per language, then offline:

```bash
make dictionary LANGUAGE=fr           # or any code in language.SPACY_MODELS
tango build-dictionary fr
```

Antonyms are the weakest card field and have their own optional index, one
file for every language rather than one per language. Without it the field
is filled exactly as it was before v0.6.0. See ADR-010:

```bash
make antonyms                         # 498 MB streamed, 4.3 MB stored
tango build-antonyms
```

### Run

```bash
make run VIDEO_ID=<id> DECK="<deck name>"
make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr
make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr DEF_LANG=en
make review DECK="<deck name>"          # process review.json decisions
make backlog DECK="<deck name>"         # process SQLite backlog
tango languages                 # list supported language codes
tango --help                    # every command
tango run --help                # one command's options
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

Expected: 1059 passing, 24 deselected. The count drifts as tests are added,
trust `make test` over this number, and update it here when it moves.

```bash
make coverage      # unit tests plus a per-module line-coverage report
```

Measured 18 August 2026: 88% overall, 2549 statements, 308 missed. The
weakest modules are `translation.py` at 71%, `__main__.py` and
`transcript.py` at 82%. Every bug found by coverage work so far has been
wiring between modules rather than logic inside one. See ARCHITECTURE.md
section 10.

Re-measure before quoting it. This file has carried a stale coverage number
four separate times, and the 87% sitting here until today was stale again:
roughly 1500 lines landed after it was taken. The lesson is not that the
number moves, it is that nobody notices when it does, so treat any figure
here without a date as unmeasured.

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
7. Commit per file, with conventional-commit prefixes. Format in section 16.

---

## 8. Things to avoid

- Do not change `MODEL_ID` or `DECK_ID`.
- Do not reorder card fields.
- Do not add a length or alphabetic check on `token.text` in `_is_valid_token`.
- Do not pass `query_lemma` to any function expecting the original lemma.
- Do not add tests that require network, Anki, or installed models to the
  default run.
- Do not run `pip install tango`, that is an unrelated PyPI package. Install
  this project with `pip install -e .` from the repo root.
- Do not search the filesystem broadly when a file is not found. First
  hypothesis should be that it does not exist.
- Do not delete `pipeline.db` without a stated reason. It contains a definition
  cache that is expensive to rebuild.
- Do not commit `.env`, `pipeline.db`, `review.json`, `output/`, or `.tangovenv/`.

---

## 9. Reference documents

Documents moved under `docs/` on 3 September 2026, split by what each one
is for. Six markdown files stay at the repository root, and the last two are
there because something outside this repository looks for them by path:

```
README.md                           Front page
CLAUDE.md                           This file
CODE_OF_CONDUCT.md                  Contributor conduct
CONTRIBUTING.md                     Setup and workflow for outside contributors
CHANGELOG.md                        What shipped in each release
SECURITY.md                         How to report a vulnerability
```

`CHANGELOG.md` is the conventional path tools expect and the one the
published GitHub release notes link to. `SECURITY.md` is recognised by
GitHub only at the root, `docs/`, or `.github/`; anywhere deeper and the
"Report a vulnerability" link stops working. Both were moved into `docs/`
during the reorganisation and moved straight back for those reasons.

```
docs/architecture/ARCHITECTURE.md   This repo, full system detail
docs/planning/ROADMAP.md            One goal per tag to v1.0.0, and what 1.0.0 freezes
docs/planning/TASKS.md              Prioritised remaining work
docs/sessions/SESSION.md            Current working state
docs/sessions/HANDOVER.md           State for the next session
docs/history/OPERATING_RULES.md     Superseded by this file; kept for its tone

docs/adr/ADR-008-per-language-dictionary-sources.md
docs/adr/ADR-009-card-media-enrichment.md
docs/adr/ADR-010-conceptnet-antonyms.md
docs/adr/ADR-011-english-offline-index.md

docs/ADR_v0.4.0.pdf                 Architecture decisions with rationale
docs/SAD_v0.4.0.pdf                 System architecture
docs/SRD_v0.4.0.pdf                 Software requirements, CLI spec, schemas
docs/PRD_v0.4.0.pdf                 Product requirements, user stories
docs/Code_Walkthrough.pdf           Function-by-function explanation
```

Code comments still cite documents by bare name, `ARCHITECTURE.md 8.45` and
so on. The names are unique in the repository, so a search finds them, and
rewriting several hundred citations into paths would cost more than it
explains.

The four markdown ADRs are the live ones and are cited throughout the code;
the v0.4.0 PDF set is the historical record. ADR-010 was accepted and
implemented on 26 August 2026. ADR-011 was accepted on 27 August 2026: it
reverses 8.19's decision against an English index, which was measured a week
before the two card fields that would have used it existed.

## 10. Pre-commit gate
Never commit unless `make check` exits 0. Run it as a bare command (`make check`), do NOT pipe to `tail`, `head`, or any filter, since pipes mask the real exit code. If output is long, redirect to a file and grep it: `make check > /tmp/check.log 2>&1; echo "exit=$?"; tail -50 /tmp/check.log`.

## 11. Destructive git commands
Never run `git checkout -- <file>`, `git restore`, `git reset --hard`, or `git clean` on a dirty working tree without first showing me `git status` + `git diff` and getting explicit confirmation. If a revert is needed, prefer `git stash push -m "<reason>"` so the work is recoverable.

## 12. Test quality bar
Every new test must fail if the code under test is broken. After writing tests, verify by mutating the target function (flip a condition, return a wrong constant) and confirming the test fails, then revert the mutation. Vacuous tests (asserting on constants, no-op assertions, tests that pass on an empty implementation) are not acceptable.

## 13. Autonomous/long-running sessions
When working autonomously: commit in small, verified increments; keep a running HANDOVER.md at the repo root with current state, what's in flight, background jobs (PID/log path), and open decisions for me. Pause and use AskUserQuestion for any irreversible or architectural choice rather than guessing.

## 14. Docs edits
Before inserting a new section into a Markdown doc, print the existing heading outline (`grep -n '^#' <file>`) and state which heading the new section goes after. Match the surrounding heading level and ordering convention.

## 15. Versioning
[SemVer 2.0.0](https://semver.org). While at `0.x`: **PATCH** for fixes and for completing something already shipped; **MINOR** for new capability *or anything requiring a migration*. The deciding question is whether an existing user must do something, or whether something they already have changes shape, if yes, it is at least a MINOR. v0.5.0 is a MINOR because the notetype gains two fields and every collection must be altered before importing.

Do not pick the number by feel. Full ladder to v1.0.0, and the list of what v1.0.0 freezes, in `ROADMAP.md`.

The version lives in **`src/pipeline/__init__.py`** and nowhere else; `pyproject.toml` reads it from there via `[tool.setuptools.dynamic]`. At the moment of tagging, `__version__`, the git tag, and section 1's "Current tag" must agree. They never once did while the version was hand-copied. `pyproject.toml` read `0.1.0` at both v0.4.3 and v0.4.4, `0.4.4` at v0.4.5, and the Wikimedia User-Agent still said `0.4` at v0.5.2. Treat a mismatch as a release bug. Every tag also gets GitHub release notes, and as of 3 September 2026 all nineteen have them; v0.4.1 to v0.4.5 were backfilled from their CHANGELOG entries.

## 16. Commit and tag format

### Commits

One commit per file. If a change touches six files, that is six commits, not
one. Order them so the reasoning reads top to bottom: source, then its
tests, then the docs that describe it.

Subject line only, 256 characters at the very most and far shorter when it
can be. No body, no bullet list, no trailing footer. Conventional-commit
prefix: `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`.

Say what changed and why in one line:

```
fix: pace audio downloads so Wikimedia stops returning 429
test: cover the 429 retry and the leaky bucket
docs: record the rate limit measurements in 8.35
```

The long explanation belongs in `ARCHITECTURE.md` or `SESSION.md`, where it
can be found later. A commit message is an index entry, not a report.

### Tags

The tag name is the version and nothing else:

```
git tag -a v0.5.3 -m "Part of speech in the learner's language"
```

`v0.5.3`, never `v0.5.3, Part of speech...`. GitHub shows the tag name in
the release list, so anything after the number turns into visual noise
repeated down the page. The title goes in the tag message and in the GitHub
release title, where there is a field for it.

## 17. Writing style

Applies to everything written for a person to read: replies, commit
messages, release notes, docs, comments, CLI output.

- Plain words over jargon. Use a technical term when it is the accurate one
  and there is no shorter way to say it, not to sound precise.
- Write like a person explaining something to a colleague. Full sentences,
  no telegraphese, no marketing tone.
- Never use an em dash. Use a comma, a colon, a full stop, or brackets.
- Bold almost nothing. Reserve it for something a reader would suffer for
  missing, roughly once per document. A page of bold has no emphasis in it.
- Prefer a short sentence to a long one, and a concrete number to an
  adjective.
## 18. What has worked

Practices that earned their place by catching something real. Each one below
has a date and a specific find, because a methodology with no evidence is
just a preference.

### 18.1 Measure it, and write the date next to the number

Every unmeasured number in this project has been wrong, several by a lot.
`torch` was described as "roughly 1.5GB" for months and was 4.5 GB. The
uninstall rung said "800 MB of indexes"; measured, it is 1.2 GB. ROADMAP
asked for a default install with "no translation, no torch" that already had
neither, and the real figure, 334 MB across 58 packages, had never been
taken.

So: run the command, read the number, write it down with the date. A figure
in this repository with no date should be treated as unmeasured.

### 18.2 Hardcode nothing that has a live source

The README's version badge was hardcoded and sat at `v0.5.3` for five
releases. It now reads from PyPI and the GitHub releases API, so it cannot
go stale. `--version` reads `pipeline.__version__` rather than installed
metadata, which under an editable install reports whatever it was at install
time. `_data_locations()` reads paths from `config`, so a user who
redirected `DICT_DIR` gets their own.

Before writing a literal, ask what happens when the thing it describes
changes. If the answer is "nothing, and nobody notices", derive it instead.

### 18.3 Run the feature and watch it work before writing tests for it

Tests written before the code has been seen working encode what the author
hoped would happen. The WSL AnkiConnect fallback was first exercised only
through the branch that *declines* it, so the retry had never been observed;
driving it against a real Anki found a log message telling the user to
hardcode an address while saying that address goes stale.

Exercise the real path, not an adjacent one. Reaching an early return is not
the same as reaching the feature.

### 18.4 A mutation that survives means the test is wrong

Break the code deliberately and confirm the intended test fails. When it does
not, fix the test rather than leaving the claim standing. Twice on
3 September 2026: an `is_eager` comment claimed a test pinned it and nothing
did, and `test_dry_run_deletes_nothing` passed with the dry-run guard
removed, because a *different* guard was catching it.

### 18.5 Pin the intent, never the spelling

Two tests asserted the literal strings `--doctor` and
`--install-translation de:en`. When v0.7.0 deleted those flags, the messages
became wrong and both tests kept passing, so the test and the bug went stale
as a matched pair. Assert what must be true (`"tango doctor" in message`,
and `"python -m pipeline" not in message`), and add a scan across the
package where one case implies a class of them.

### 18.6 Handle the whole failure surface, not the expected one

A port answering is not the same as the right service answering. Anything
can sit on 8765, and `response.json()` was unguarded, so a stray web server
produced `Expecting value: line 1 column 1 (char 0)`. Three shapes needed
handling, not one: a non-JSON body, an HTTP error status, and valid JSON
with the wrong schema.

For any external call, ask what happens when it succeeds and returns
something unexpected, not only when it fails.

### 18.7 Reproduce before fixing, and keep the reproduction

Stand up the failure first. A `http.server` on a spare port reproduced the
port bug in seconds and then verified all three messages afterwards. A fix
for a bug never seen is a guess.

### 18.8 Prefer a fallback over changing a default

When a default is right for most and wrong for some, add a path for the some
rather than moving the default. `ANKI_HOST` stays `localhost`, correct on
macOS, native Linux, native Windows and WSL2 mirrored networking, and a
refused connection retries once against the Windows host. Defaulting to the
gateway under WSL would have broken the mirrored-networking users who work
today.

An explicit setting from the user is never second-guessed.

### 18.9 In a mechanical sweep, separate data from writing

Replacing 490 em dashes would have renamed the Anki notetype
(`YT Anki Pipeline — Recognition`, which every existing collection matches
against) and changed the attribution line printed on every card. Sample the
matches first, protect the ones that are data, and re-read the diff for
places where the mechanical answer reads wrong.

### 18.10 Check a convention against real projects, not memory

Before moving files, `src/images/` was checked against the PyPA guidance and
six well-known repositories on 4 September 2026: `src/` holds importable
code, `psf/requests` has exactly `src/requests/`, and none of the six keeps a
loose image at the repository root. That took two minutes and replaced an
opinion with evidence.

### 18.11 A published artifact is frozen; verify before you upload

PyPI burns a version number permanently and will not let a description be
edited afterwards. Before uploading: build from the tagged tree, run
`twine check`, inspect the sdist for secrets and stray files, and install
the built wheel into a clean virtualenv and run it. v0.8.1 exists only
because the README shipped `make` instructions to people who had installed
with pip, and the published page could not be corrected any other way.

The same applies to anything with an audience. A Dockerfile is built and run
before it ships, not written and hoped for.
