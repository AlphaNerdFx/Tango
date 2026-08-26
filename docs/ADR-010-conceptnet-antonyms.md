# ADR-010: ConceptNet as a supplementary antonym source

Status: Accepted, 26 August 2026, and implemented the same day

Continues the numbering from ADR-009. Written after a measurement spike on
26 August 2026, so the numbers below are measured on this repository's own
data rather than estimated. Reproduce them with
`scripts/measure_antonym_sources.py`.

## Context

Antonyms are the weakest field on a Tango card and have been since the field
existed. Re-measured on 25 August 2026 by reading fields back out of real
decks:

| field | German | French |
|---|---|---|
| Definition | 93.1% | 98.6% |
| Synonyms | 63.1% | 84.5% |
| Antonyms | 53.9% | 22.7% |

It is the last open item on the v0.6.0 rung (issue #25). Everything else has
already been ruled out with evidence, recorded in TASKS.md:

- Open Multilingual WordNet returns essentially nothing outside English. Five
  French words yield 469 lemmas and 2 antonyms, against English's 238 lemmas
  and 25.
- Datamuse works for English and returns empty for its Spanish vocabulary, so
  it raises the language that needs it least.
- The offline index is not discarding anything. Of 40000 real kaikki French
  entries, 0 carry sense-level antonyms; all 1682 are top level and already
  read. 22.7% is the ceiling of the data the index is built from.

That last finding is the one this ADR turns out to be about, and it was
stated slightly too broadly. The ceiling belongs to the *edition* the index
is built from, not to Wiktionary.

## Evidence gathered

### What the dump costs

ConceptNet 5.7.0 assertions, `conceptnet-assertions-5.7.0.csv.gz`:

| | |
|---|---|
| download | 498 MB, once, every language in one file |
| antonym edges after streaming the gzip through a filter | 65 910 |
| same-language, non-self pairs in languages Tango supports | 49 416 |
| words those cover | 52 156 across 22 languages |
| **shipped artifact** | **4.3 MB SQLite** |
| edges carrying a part of speech | 88.5% |

That last row was 3.06 MB while this ADR was a proposal, measured from a
spike that merged every sense of a word into one row. The shipped index
keeps one row per part of speech, because preferring the sense the word was
used in turned out to be worth having, and that costs 1.2 MB. Corrected here
rather than left to be discovered: a stale number in this repository has
usually been a number nobody re-measured after the thing it described
changed.

The dump never has to touch disk: `curl | zcat | grep` filters it in flight,
which matters given ROADMAP v0.9.0 and a repository whose largest single
package is 725 MB. What ships afterwards is 3 MB, against 131 to 404 MB for
one language's Wiktionary index.

### What it adds, measured on the decks the baselines came from

| deck | lemmas | index today | ConceptNet | union | ConceptNet adds |
|---|---|---|---|---|---|
| French, `A0rJNx4lGDo` | 1054 | 22.5% | 32.8% | **35.0%** | +12.5 points, 132 words |
| German, `loqocHC9aAU` | 406 | 54.2% | 24.1% | **57.9%** | +3.7 points, 15 words |
| Russian, `c9ghnkHZLwo` | 701 | 50.2% | 9.4% | **50.8%** | +0.6 points, 4 words |

The "index today" column reproduces the 25 August baselines (53.9% German,
22.7% French) to within half a point, which is the check that the
measurement is counting the same thing those figures counted.

Words it adds, from the French deck: `accord` gets `désaccord`, `arrivée`
gets `départ`, `apparaître` gets `disparaître`, `apprécier` gets `déprécier`
and `détester`. From the German deck: `mehr` gets `weniger` and `ankommen`
gets `abfahren`. From the Russian deck: `вопрос` gets `ответ`.

### Why the three languages differ by 20x

This is the part worth keeping. ConceptNet's antonyms are not a new kind of
data. They are Wiktionary's, re-extracted, from exactly two editions:

| dataset | antonym edges |
|---|---|
| English Wiktionary | 40 719 |
| French Wiktionary | 19 806 |
| verbosity (a word game, English only) | 5 385 |

Broken down for the three languages measured, counting same-language edges:

| language | from the English edition | from the French edition |
|---|---|---|
| French | 875 | **10 635** |
| German | 2 526 | 869 |
| Russian | 980 | 330 |

Tango's index is built from `kaikki.org/dictionary/downloads/{lang}`, which
is the English edition's entries for that language. So for German and
Russian, ConceptNet is mostly handing back data the index already has, and
the small gain is real but incidental. For French it is handing over the
French Wiktionary's own antonyms, which this project has never read.

**The gap is between Wiktionary editions, not between Wiktionary and
ConceptNet.** That reframes the item: ConceptNet is the cheap way to reach a
second edition, not a second source.

### Quality and licence

Eighteen French pairs sampled at random, all eighteen genuine antonyms:
`dessous`/`dessus`, `militarisme`/`pacifisme`, `calomnie`/`éloge`,
`diversification`/`uniformisation`, `canonique`/`apocryphe`.

TASKS.md warned the data is noisy and named two cases. Both were re-checked
against the filtered set rather than assumed, and they do not behave the
same way. `大` listing `郵局` is gone: within `/r/Antonym` it has only
`ja/大 -> ja/小` and `zh/大 -> zh/小`, so that example came from mixing
relations. `grande` listing `irrelevante` survives, as `es/grande ->
es/irrelevante` alongside the correct `es/pequeño`. So the filter removes
self-references, cross-language leakage and other relations, and does not
remove a wrong antonym asserted as an antonym. Capping the list and
preferring the part of speech the card carries are the remaining levers;
neither makes the field clean, and a thin antonym list is the failure this
project prefers.

19.2% of French targets are multiword (`céréale_d’hiver`, `dans_le_temps`).
Underscores become spaces on the way into the index, and that is not quite
enough on its own: ConceptNet writes an elided apostrophe as a separator
too, so `s’_en_retourner` comes out as `s’ en retourner` rather than
`s’en retourner`. A French-aware join is a small piece of the implementation
rather than a surprise to find later.

Every edge carries `"license": "cc:by-sa/4.0"`, the same licence family as
the Wiktionary data the project already ships.

## Candidates

**1. ConceptNet antonym index.** 498 MB downloaded once and discarded, 3 MB
shipped, all 22 languages at once. Measured lift above. Nothing about the
card pipeline changes except which sources fill one field.

**2. kaikki native-edition extracts.** Read the French Wiktionary directly
rather than through ConceptNet, which would also improve definitions,
examples and synonyms rather than antonyms alone. Measured: the French
edition's French extract is **3.17 GB uncompressed**, one download per
language per edition, against 715 MB gzipped for the current source. ADR-008
already recorded the other cost, that each edition uses its own section and
template naming and therefore needs its own parser. The lift is unmeasured.

**3. Do nothing.** Antonyms stay at 22.7% for French. Defensible, since a
missing antonym costs a thinner card rather than a wrong one, and this
project's rule is that guessing is the expensive direction.

## Decision

Proposed: candidate 1, with candidate 2 recorded as the larger question it
points at rather than folded into it.

The reasoning is proportion. Candidate 1 buys the single worst language 12.5
points for a 3 MB artifact and a build step that looks exactly like
`make dictionary`, which already exists. Candidate 2 might buy more across
four fields, but it is a 3.17 GB per-language download and a second parser,
and nobody has measured what it would actually return. Doing 1 does not
block 2, and the measurement script written for this ADR is what 2 would be
evaluated with.

What this decision does **not** claim: that antonyms are solved. French would
sit at 35.0%, still the weakest field on the card and still below German's
current 53.9%. This closes about half of French's gap to German, and leaves
German and Russian effectively where they are.

## Measured after building it

The prediction above was made from a spike. These are the numbers the
shipped code produces, which is the only version that matters:

| deck | before | after | |
|---|---|---|---|
| French, 1054 lemmas | 19.7% | **34.8%** | +15.1 points |
| German, 406 lemmas | 56.2% | 60.3% | +4.2 points |
| Russian, 701 lemmas | 47.8% | 48.8% | +1.0 points |

French landed at 34.8% against 35.0% predicted. The baselines moved by a
couple of points in both directions because these read the index through
`wiktdata.lookup` with the part of speech the word was used in, the way a
real run does, rather than asking whether any row for that spelling has an
antonym. That is the more honest baseline and it was worth the difference.

The real index holds 72 858 rows across 22 languages and takes 4.3 MB. It
was built end to end with `make antonyms`, and `grand` in French returns
bref, court, exigu, faible and minime.

**One thing this broke, worth recording.** Three tests in
`test_definition.py` asserted an empty antonym field. They mock `wiktdata`,
so they had nothing to say about a source that did not exist when they were
written, and they began failing the moment the index was built on this
machine while passing anywhere it had not been. A unit test whose result
depends on whether someone ran a setup command is the thing CLAUDE.md 3.5
exists to prevent, so `tests/conftest.py` now points the index at an empty
directory for every test that does not build its own.

## Consequences

- A new optional build step, per the `make dictionary` pattern, and a
  `--doctor` line reporting whether the antonym index is present. A missing
  index must degrade to today's behaviour, not fail a run.
- **Constraint 3.3 applies directly.** Antonyms describe the word shown, so
  they stay in the transcript language. The same-language filter is what
  enforces it, and it is exactly the kind of fourth thing added beside the
  gate that 3.3 has already been violated by three times. It needs a test in
  `TestConstraint33FieldLanguage`, not a comment.
- The definition cache stores assembled fields, so rows written before this
  lands keep their empty antonym lists. That is ARCHITECTURE.md 8.27's
  recurring shape. The four-segment cache key makes the invalidation a query
  now, which is what it was added for.
- CC BY-SA attribution has to appear wherever the Wiktionary attribution
  already does.
- The per-language gain must be quoted per language. An average across three
  decks would read as +5.6 points and would describe none of them.
