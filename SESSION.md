# SESSION.md — Current working state

Last updated: 13 August 2026, at the close of the v0.4.5 release and
post-release development session.

Read this to understand exactly where development stands and what was learned.
Everything here was checked against `git log`, the working tree, and the
installed environment at the time of writing, not recalled from memory. See
6.9 for why that distinction matters in this repository.

---

## 1. Where the project is

**Tagged release:** v0.4.5
**HEAD:** `755bc67`, 12 commits past the tag, working tree clean, level with
`tango-origin/main`
**Test state:** 734 passing, 0 failing, 24 integration deselected (`make test`)
**Coverage:** 88% overall, `__main__.py` 82% (`make coverage`)
**Overall completion estimate:** roughly 85 percent toward a v1.0.0 CLI tool,
roughly 25 percent toward the full multi-surface product vision

The pipeline works end to end for English with no setup beyond the spaCy
model, and for any language with a built dictionary index. Cross-language
mode (`--def-lang`) works for all 12 pairs among de/fr/en/ru, with
translation models installed.

Start any session with:

```bash
make doctor       # or: python -m pipeline --doctor
```

It reports spaCy models, dictionary indexes and their sizes, translation
pairs, the MW key and AnkiConnect reachability, and prints the command that
fixes anything missing. It was written because nearly every failure
investigated in this project turned out to be setup rather than logic, and
none of it was visible from the failure itself.

---

## 2. What shipped in v0.4.5

67 commits. The release is a correctness release; the tag message carries the
full list. The four that mattered most:

**The deck duplicate check could not see most cards.** It read the note field
named `Front`, while the generated model's first field is named `Word`. A
deck built by this pipeline returned zero fronts, so every previously added
word came back NEW. Measured: 0 of 1036 notes visible in one deck, 305 of
2172 in a hand-built one across 12 note types. Now reads the lowest-`order`
field, which is what Anki itself treats as a note's identity.

**Cross-language definitions were broken four ways at once**, each hiding the
next: `ARGOS_PACKAGES_DIR` in `.env` pointed at an empty directory and
argostranslate reads that variable itself, so the pipeline saw no models
while any shell that skipped `.env` saw them all; the failure was a bare
`pass`; untranslated words then reached English sources, so German "je"
matched the English letter J; and the first translation of a run takes 43.5s
against a 15s per-word timeout.

**importPackage shared the 5-second timeout** meant for quick queries. Anki
rebuilds indexes before answering and that scales with the collection, so
imports that worked at 40k notes failed at 57k.

**A pasted YouTube URL aborted `make run` before the pipeline started.** The
video id was interpolated into printf's *format string*, and a URL ending in
`%3D` made printf fail the recipe.

---

## 3. What shipped after v0.4.5

**Merriam-Webster examples were parsed and then discarded** at the call site.
English example coverage went from 14% to 72% on a full video. Third instance
of the fetched-parsed-dropped shape.

**Cross-language runs violated CLAUDE.md 3.3.** Examples, synonyms and
antonyms were taken from the target-language entry, so a German video with
`--def-lang ru` shipped Russian sentences. Gated at all three call sites.
ARCHITECTURE 8.25.

**Cross-language runs now fall back to a native definition** rather than
giving up. 46 of 459 cards on a German deck said "No definition found" while
the German index had the word. ARCHITECTURE 8.26.

**`--doctor`, `--install-model`, `--install-translation`**, and `make help`
now prints the CLI equivalent of every user-facing target, so `make` is a
convenience rather than a requirement.

**`scripts/coverage_matrix.py`** sweeps every language combination and reads
the card fields back out of the generated packages.

---

## 4. Uncommitted state

None. The working tree is clean and `main` is level with `tango-origin/main`.

---

## 5. The coverage matrix, and what it is for

```bash
python scripts/coverage_matrix.py --dry-run     # plan only
python scripts/coverage_matrix.py               # all 16 combinations
python scripts/coverage_matrix.py --langs de,fr
```

16 combinations for 4 languages: 4 native plus 4×3 cross-language. It does
not import into Anki; each run generates a package and the fields are counted
from it.

It has already paid for itself twice. It found that 10 of 12 cross-language
pairs were silently producing native output for want of a translation model,
and it found the 3.3 violation above — because cross-language rows scored
*higher* on examples than native ones, which is impossible if the fields are
constrained to the transcript language.

**Last good numbers** (10 August, after all models were installed but before
the 3.3 fix, so cross-language example and synonym figures are overstated):

| combination | cards | defs | ex1 | ex2 | syn | ant |
|---|---|---|---|---|---|---|
| de → native | 362 | 94% | 91% | 73% | 65% | 59% |
| fr → native | 207 | 99% | 97% | 75% | 85% | 23% |
| en → native | 271 | 100% | 72% | 52% | 94% | 30% |
| ru → native | 701 | 95% | 76% | 40% | 73% | 48% |
| de → en | 362 | 99% | 45% | 36% | 24% | 3% |
| fr → en | 207 | 99% | 44% | 31% | 73% | 3% |
| ru → en | 701 | 100% | 35% | 21% | 24% | 0% |
| en → de | 271 | 97% | 98% | 83% | 95% | 59% |

The consistent finding across every language: **cross-language mode costs
most of the synonyms and antonyms**, because 3.3 requires those to stay in
the transcript language while the definition may change. Native definitions
produce materially better cards for anyone who can read the target language.

**A corrected baseline is still outstanding.** Two attempts failed:

1. The first read stale cache rows written before the 3.3 fix (see 6.15).
2. The second failed all 16 rows with "Anki is not running. All words written
   to backlog" — Anki was closed while it ran. The sweep needs AnkiConnect
   for the deck check even though it never imports, and it does not check
   for that up front. Filed.

---

## 6. Reasoning history, dead ends, and mistakes

This section is deliberately unflattering. Everything here was a real error
made during development, and the reasoning is recorded so it is not repeated.

### 6.1 Rejected: Merriam-Webster as the only definition source

Free tier caps at 1000 requests per day and covers English only. Kept as
primary for English specifically, with dictionaryapi.dev as fallback.

### 6.2 Rejected: PONS API for multilingual definitions

Free tier is 1000 requests per *month*, requires registration, and provides
bilingual translation pairs rather than monolingual definitions. Recorded in
ADR-005.

### 6.3 Rejected: Helsinki-NLP transformers for translation

Would add roughly 2GB. argostranslate was chosen instead, which ironically
also pulls in PyTorch. The size problem was deferred, not avoided.

### 6.4 Rejected: cloud translation APIs

Google Translate and DeepL both require paid keys at production volumes.

### 6.5 Failed experiment: Webshare free-tier proxy

Transcript extraction failed with repeated 429s through the proxy and
succeeded without it. Free-tier datacenter IPs are blocked more aggressively
than residential ones.

### 6.6 Rejected: rotating proxies for the Wikimedia rate limit

Circumvents a rate limit rather than complying with it, and reverses the
reasoning already recorded in issue #8 and ADR-008. Downloading the data once
makes the question moot.

### 6.7 Mistake: misattributing English lemmatization to caption quality

Garbled lemmas were repeatedly explained as YouTube auto-caption noise. They
were an English spaCy model applying English morphology to French text.

Lesson: a plausible explanation that requires no verification is more
dangerous than no explanation at all.

### 6.8 Mistake: claiming dictionaryapi.dev had multilingual coverage

ADR-005 was designed on documentation rather than testing. 9 of 9 non-English
languages measured at 0% coverage.

### 6.9 Mistake: an unverified handoff document

A session handoff asserted changes existed that did not, and instructed
deleting `pipeline.db` on the basis of a model-ID change that had not
happened.

Lesson: a handoff document must be built from `git diff`, not memory.

### 6.10 Mistake: recommending the wrong resolution for the is_alpha conflict

A "conservative" recommendation that preserved a known-wrong pattern is not
conservative.

### 6.11 Lesson: fixtures can make bugs inexpressible

`_make_token()` was called with identical `text` and `lemma` in every early
test, so the surface-form-versus-lemma bug class was invisible to the suite.
Two separate bugs of that shape shipped.

**Six vacuous tests have now been found and rewritten**, all the same family:
two asserted `x is True or True`, which cannot fail; four patched a name the
test module had imported directly, so the patch missed and the real function
ran against this machine's state. One of those passed in full runs and failed
alone, because the result depended on whether an unrelated test had perturbed
the import first.

### 6.12 Lesson: the recurring bug shape in this codebase

Two values that should be the same, with nothing enforcing that they are:

- WordNet received `query_lemma` where `lemma` was meant
- `_fetch_from_dictapi` appended a language to a URL that already had one
- `_is_valid_token` guarded `token.text` where `token.lemma_` was used
- The batch loop built a cache key differently from `fetch_definition`
- The transcript search used the lemma where the surface form was needed
- The deck check read a field named `Front` where the model's first field is
  named `Word`
- Configured paths resolved against the working directory where the project
  root was meant
- The cache key was built from `target_language` before the native fallback
  could change it

None produced a crash. All produced valid-looking wrong output.

### 6.13 Lesson: fixing only the path you were looking at

A fix written into the found-definition path when the path that actually
executes is the not-found fallback. Now four occurrences: the Wiktionary
example fix, OMW synonyms, the second Wiktionary example, and the
`not_found_*` channels that review and backlog modes were dropping entirely.

### 6.14 Lesson: measure per language before generalising

An early claim that antonym coverage was "at the data ceiling" was measured
on French alone. German and Russian came in far higher.

### 6.15 Lesson: cached cards outlive the fix that corrected them

Twice a fix was verified in the code and then measured as still broken,
because the definition cache served the old assembled fields.

- 349 rows of German lemmas cached under `::en` kept returning false-friend
  English definitions after the cause was fixed
- 4532 rows written by a pre-fix sweep kept putting Russian examples on
  German cards after 8.25 gated them

The cache stores assembled fields, not inputs, so nothing about a fetch-path
fix invalidates them. **Clearing the affected rows is part of such a fix, not
a follow-up.** ARCHITECTURE 8.27.

### 6.16 Lesson: a coverage metric can reward the bug it should expose

Cross-language rows scored *higher* on examples than native ones — 87%
against 45%. That is impossible if examples are constrained to the transcript
language, and the impossibility is what exposed the 3.3 violation. The number
went up because the field was being filled with content in the wrong
language.

When a fix makes a coverage number fall, check whether the number was
measuring the defect before concluding the fix regressed something.

### 6.17 Dead end: sense selection by word overlap

Implemented, measured, reverted. It fixed the case it was written for — the
`tapa` card defining Polynesian bark cloth for a video about tapas bars — and
was net-negative on real vocabulary. 146 of 231 lemmas have more than one
sense; of the 15 picks it changed, one improved and the rest degraded
(`côté` → "Nom de famille", `gens` → "Clan familial", `fait` → a participle).

No threshold separates them: the correct `tapa` sense wins with an overlap of
2, and so does every bad pick. Full evidence in ARCHITECTURE 8.28, including
what the data says to build instead (POS filtering).

### 6.18 Mistake: verifying in the one context where the bug cannot reproduce

The `--def-lang` failure was reported four times and "fixed" three times
before the cause was found, because every check was a `python -c` invocation
that never loads `.env` — and the cause was a `.env` variable. Each fix along
the way was a real bug, but none was the reported one.

Lesson: reproduce through the same entry point the reporter used, before
reasoning about the code. The failing command was in their first paste of
actual terminal output.

---

## 7. Environment specifics

```
OS:              Windows 11 with WSL2 (Ubuntu)
Python:          3.10.12
Virtual env:     .tangovenv  (NOT .venv)
Project path:    /mnt/c/DSC/Career/Projects/Tango
Git remotes:     tango-origin -> https://github.com/AlphaNerdFx/Tango  (current)
                 origin       -> .../Youtube-Anki-Flashcards.git       (renamed, dead)
Anki:            running on Windows, AnkiConnect bound to 0.0.0.0
ANKI_HOST:       http://172.28.144.1:8765 (WSL gateway, changes on WSL restart)
```

Run `make doctor` for the live picture. At the time of writing:

- spaCy models for de, en, es, fr, ja, ko, pt, ru, zh
- Dictionary indexes for **de (159 MB), fr (323 MB), ru (116 MB) only**.
  es, ja, ko, pt and zh have a spaCy model but no index, which is exactly the
  state that produces cards with no definitions. `make dictionary LANGUAGE=es`
- Translation models: de→en, en→de, fr→en, en→fr, ru→en, en→ru. Those six
  cover all 12 pairs, because argostranslate pivots through English
- `MW_API_KEY` set, AnkiConnect reachable

Known environment issues:

- **`ARGOS_PACKAGES_DIR` in `.env` points at an empty directory.** The
  pipeline now detects this and ignores the setting for the run, with a
  warning. Unsetting it in `.env` silences that. argostranslate reads the
  variable itself, which is why it broke translation invisibly.
- `gh issue list` fails because it resolves `origin`, which points at the
  pre-rename URL. Pass `--repo AlphaNerdFx/Tango`.
- `make test` takes ~80s warm; the first run after a cold boot is ~4 minutes,
  because the repository lives on `/mnt/c`.
- `pip install tango` installs an unrelated PyPI package. Never run it.

---

## 8. How to work on this project

Beyond CLAUDE.md and OPERATING_RULES.md, the habits this session proved
necessary:

**Reproduce through the user's entry point first.** See 6.18. Four rounds
were lost to verifying via `python -c` a bug that only existed under `make`
and `.env`.

**Ask for the actual terminal output early.** Every long investigation here
ended the moment real output appeared. The `printf: %3D: invalid directive`
line settled a five-round hunt instantly.

**Measure on real vocabulary, not on the case that motivated the fix.** 6.17
is the clearest example: a change that fixed the known-broken card and
degraded a dozen others.

**Verify by mutation, not by tests passing.** Revert the fix and confirm the
new tests fail. Six vacuous tests have been found in this suite; a green run
is not evidence that anything is checked.

**Clear the cache when changing what goes in a field.** 6.15.

**A hard constraint (CLAUDE.md 3.1, 3.2, 3.3) is worth a test that fails
loudly.** The 3.3 violation shipped because nothing pinned it.
