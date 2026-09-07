# HANDOVER

Written 7 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main`, in sync with `tango-origin` |
| last tag | **`v0.10.0`**, released 5 September 2026 |
| `__version__` | `0.10.0` |
| `make check` | exit 0 |
| tests | **1165 unit, 33 integration deselected** |
| PyPI | `pip install tango-anki` |

Twenty tags, twenty GitHub releases. 33 commits sit on `main` after v0.10.0,
all of them the v0.11.0 rung.

**Working autonomously**, under two standing decisions taken 5 September
2026: commit and push freely, but **ask before any tag or PyPI upload**,
because a version number is burned permanently and a published description
cannot be edited. And where a crossroad is already settled in a document,
follow what was designed rather than asking again.

## What is in flight: v0.11.0, images on cards

The rung is "images on cards, gated to concrete nouns". The feature is
built, measured and off by default. What is left is a visual review and a
wider measurement corpus, both of which need the user.

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
run with them disabled makes zero network calls and adds 0.17s. The
measurement that ADR-009 required is now done and it justifies the feature;
the remaining gate on the default is a person looking at real cards.

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

### Done: resolution is batched, which removed the speed objection

The per-lemma path cost four Wikimedia requests per noun at one a second, so
a 400-noun deck added about 27 minutes. All three APIs take 50 items per
request and one Wikidata call returns P31 and P18 together, so the same deck
costs about **24 requests**. Measured on 16 German words: 3.78s against
53.34s, with 16 of 16 results identical. A 10-card package with images on
builds in 4.9s.

`find_images()` is the batched entry point; `find_image()` remains for one
lookup. Resolution is batched rather than threaded because Wikimedia's
pacing is the limit, not latency. Downloads are still threaded.

### Done: the icon fallback is measured and rejected

Requested as "an icon source with icons representing a concept, not emoji".
Three FOSS sets were tested, all meeting ADR-008's bar: Material Symbols
(4,277 icons, Apache 2.0), Bootstrap Icons (2,078, MIT) and Font Awesome
Free (1,895, CC BY 4.0).

**All three matched 0 of 14 abstract words.** Against the real refused
population, Material Symbols matched 5 of 84. The cause is structural: these
are user interface icon sets, and the same set matched 14 of 14 UI concepts.
Where they do have a word it is usually a concrete noun, which already gets a
photograph. The one general-vocabulary source, the Noun Project, was already
rejected by ADR-008 for an API key and non-free licences.

Recorded in the ADR so it is not re-proposed without the numbers.

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

## A review deck is ready for you

`output/IMGREVIEW1_20260906_011806.apkg`, reachable from Windows at
`C:\DSC\Career\Projects\Tango\output\`. 60 real German nouns with their
real cached definitions, 28 of which carry an image, 1.8 MB, average image
65 KB. It imports into a deck named "Tango image review" and needs no schema
change, since the collection is already on the 14-field notetype.

Reading it before importing: the concepts are right. An earlier note in this
file claimed `Tanzen` and `Stricken` were weak; that was wrong and is
corrected here. `stricken` resolves to Q193188 knitting and gets a
photograph of a grandmother knitting stockings, one of the better cards.
`tanzen` gets Q11639 dance, a Renoir of people dancing: right concept,
rendered as a painting.

The real remaining softness is that some images are artworks or documents
rather than depictions: `sprichwort` gets a medieval manuscript page and
`politikwissenschaft` a salon painting. That is much milder than a wrong
association, and it is the thing to judge when looking at the deck.

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

## One thing found and deliberately not fixed

**Twelve tests in `test_definition.py` attempt real network connections**,
24 attempts in total, which breaks CLAUDE.md 3.5. Found by running the suite
with `socket.socket.connect` patched to raise. They are pre-existing and
unrelated to the image work, and every image and cards test passes under the
same guard, so this was reported rather than folded into an unrelated change
(CLAUDE.md 7.4). The guard is four lines and worth keeping as a test:

```python
import socket
def guard(self, addr):
    raise AssertionError(f"unit test attempted a network connection to {addr}")
socket.socket.connect = guard
```

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
