# HANDOVER

Written 6 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main`, in sync with `tango-origin` |
| last tag | **`v0.10.0`**, released 5 September 2026 |
| `__version__` | `0.10.0` |
| `make check` | exit 0 |
| tests | **1141 unit, 24 integration deselected** |
| PyPI | `pip install tango-anki` |

Twenty tags, twenty GitHub releases. 33 commits sit on `main` after v0.10.0,
all of them the v0.11.0 rung.

**Working autonomously**, under two standing decisions taken 5 September
2026: commit and push freely, but **ask before any tag or PyPI upload**,
because a version number is burned permanently and a published description
cannot be edited. And where a crossroad is already settled in a document,
follow what was designed rather than asking again.

## What is in flight: v0.11.0, images on cards

The rung is "images on cards, gated to concrete nouns". Four parts were
planned; three are done and the fourth is waiting on the user.

### Done: the image source was wrong, not the gate

ADR-009 designed phase 3 around Wikimedia Commons **text search**, which is
why `laufen` returned a coin from the town of Laufen: a text search matches
a spelling, not a meaning.

`src/pipeline/images.py` resolves a lemma to a *concept* instead:

```
lemma -> Wikipedia article (language specific)
      -> Wikidata item      (language independent)
      -> P31 gate, then P18 image or the article lead image
```

The middle step is what fixes German. `Hund` and `chien` both resolve to
Q144, so one judgement about whether dogs are photographable serves every
language. This matters because the 5 September amendment to ADR-009 measured
the WordNet gate at **0% for German**, since OMW has no German WordNet at
all, and German is 39.5% of the cached definitions.

Measured 5 September on 20 German nouns: **55% got an image**, against 0%
for the WordNet-only gate. Refusals were `gedanke`, `privileg`,
`konstellation`, all correctly abstract.

### Done: two things found by running it rather than reasoning

**Commons serves originals and they are enormous.** The first real download
was **9.2 MB** for one photograph of a dog, for a card that displays it at
240px. Both routes now request a 480px thumbnail: the same photograph is
**46 KB**, a 200x reduction. This is the same class of error as ADR-009's
audio estimate in ARCHITECTURE 8.35, caught earlier only because the
download was actually run.

**Attribution is a licence obligation, not decoration.** Commons reports
`AttributionRequired: true` on the images this actually returns. The dog
photograph is CC BY-SA 2.0 by Markus Trienke, and shipping a deck without
naming them would breach it. `images.attribution()` fetches the credit and
it travels with the result, so the two cannot disagree about which file.

### Done: the fields, and the migration

`Image` and `Attribution` are fields **12 and 13**, appended per CLAUDE.md
3.2. Verified against the live collection before committing:

```
notes 4773 (was 4773) | field count 14 (was 12)
forked?: NO FORK, still 1607392321
original field values changed: 0
```

That reproduces ARCHITECTURE 8.32 at 23x its original scale. Anki will want
a full sync afterwards, which is expected for a schema change.

**Images are off by default** (`IMAGES_ENABLED`, default false). Verified: a
run with them disabled makes zero network calls and takes 0.17s longer than
none. ADR-009 requires the measurement below before that default moves.

### Done: the language counts were wrong in four places

`tango languages` and the README said "40 languages". The real figure is
**45 codes recognised**, of which **25 can produce cards**. Worse, the README
advertised Arabic as supported, and spaCy has no Arabic model, so a user
with an Arabic deck followed the README into a run that could not start.

Corrected in `README.md`, `language.py` (twice), and `wiki/[FAQ].md`, and
`TestDocumentedLanguageCountsAreTrue` in `tests/test_hard_constraints.py`
now fails if any of them drifts again. That test checks **every** occurrence
rather than presence: an earlier version passed while one of language.py's
two sites was stale, which is the exact shape of the original bug.

### Waiting on the user: the measurement corpus

`scripts/measure_image_sources.py` exists and runs. It reads the definition
cache in `pipeline.db`, which holds **807 noun lemmas across three
languages: fr 488, de 245, en 74**. Every other language this project has
run end to end predates the v0.6.0 composite cache key, so its definitions
cannot be split by language.

**The ask, unchanged from the plan: 2 more video ids each for de, fr, es,
ru, pt, ja, zh, ko, en, plus 3 for Italian. 21 in total.** Two constraints,
both learned from the existing corpus: prefer manually captioned videos, as
auto-generated ones produced the `Bissch` and `Herauszufinde` damage in
issue #27, and aim for 5 to 15 minutes, since the 32-lemma Japanese video is
too short to measure anything and the 1094-lemma English one exhausted
Merriam-Webster's free tier in a single run (ARCHITECTURE 8.43).

## The exact next step

1. **Run the full measurement** and record the result in ADR-009 as a second
   amendment, including a rejection if the numbers do not justify the two
   card fields. ADR-010 shipped on +12.5 points for French and a comparable
   bar applies. A partial run is already in hand: 55% for German.
2. **The icon fallback (Part A2) is measured before it is built**, exactly
   as ADR-010 did for ConceptNet. Wikidata cannot serve it: checked live,
   `P2910` is empty on every item tried and `P487` has the inverse of the
   coverage needed, since `Q144` dog has an emoji and `Q2979` freedom does
   not. Reading the refused French nouns, they split three ways: an icon is
   defensible for `musique` and `théâtre`, dishonest for `nuance` and
   `phénomène`, and `va` and `commu` are transcript damage that should never
   have been cards. Forcing a match across all three reproduces the failure
   the gate exists to prevent.
3. **Only then** decide whether `IMAGES_ENABLED` defaults to true.

## Open decisions

| decision | why it is waiting |
|---|---|
| **21 video ids** | The measurement corpus covers three languages; the user offered ids for all |
| **Publishing the Docker image** | Builds and runs. Pushing to a registry needs an account and a choice of one |
| **French fixed expressions** | `d'accord` becomes `accord`. 7 of 1079 cards, six legitimate words. Needs a hand-curated per-language list |
| **Transcript fallback** | The whole pipeline depends on one extraction path. A user-supplied subtitle file is the cheap half |
| **Learned queue matching** | Nothing records what the user answers at the y/n/s prompt, so that training data is discarded every run |
| **ruff and mypy debt** | 256 and 26 findings, both advisory, neither gating |

## Known-broken

Nothing. `make check` exits 0.

Environmental notes, not this repository's bugs:

- **`make check` takes about ten minutes here.**
- **There is no pre-commit hook**, despite an earlier version of this file
  saying one gates every commit. Run `make check` yourself before committing.
- **There is no `pip` script in `.tangovenv/bin`.** Use
  `.tangovenv/bin/python -m pip`.
- **pytest's summary line is suppressed here.** The progress dots and the
  exit code are reliable; the "N passed" line does not appear for the full
  suite. Count with `--collect-only -q`, which prints per-file totals.
- **The pre-commit hook matches the literal text "git commit" in a
  command**, so writing a file whose contents mention it is blocked. Use
  the editor tool, not a shell heredoc.
- **`ca_core_news_sm` was installed while verifying the first-run offer.**
  Removable with `pip uninstall ca-core-news-sm`.

## Conventions worth not relearning

Read CLAUDE.md section 18. The three that earned their place again this
session:

- **Run the feature and watch it work before writing tests for it.** The
  9.2 MB download and the licence obligation were both found this way, and
  neither was visible from the code.
- **Mutation-verify every new test.** Three tests in this session passed
  while the thing they named was broken. One claimed images are never
  counted as missing by `doctor`; the mutation survived twice before the
  test was rewritten to compare the reported count, because `_run_doctor`
  returns `1 if missing else 0` and an exit code cannot see one extra item.
- **Measure it, and write the date next to the number.** Four separate
  documents said "40 languages" and the real figure was 45.

Also: `make check` before every commit, one commit per file, no em dashes,
and update CLAUDE.md section 1 before tagging rather than after.
