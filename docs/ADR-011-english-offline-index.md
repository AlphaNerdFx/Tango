# ADR-011: An offline index for English

Status: Proposed

Continues the numbering from ADR-010. This one argues against a decision
already recorded in ARCHITECTURE 8.19 and enforced in
`wiktdata._DISCOURAGED`, so it opens with that decision rather than with the
proposal.

## The decision this reverses

`make dictionary LANGUAGE=en` warns you off, in these words:

> English already gets ~98% definition coverage from Merriam-Webster and
> dictionaryapi.dev, which are better curated.
> A measured English run used this index zero times, so it costs a 475 MB
> download to change nothing.

That was correct when it was written and it is not a guess: someone ran a
real English video and counted. The reasoning fails now for a reason that
has nothing to do with the measurement being sloppy.

**It is dated.** From `git log`:

| date | what landed |
|---|---|
| 7 August 2026 | English index discouraged, "used this index zero times" |
| 14 August 2026 | IPA and Pronunciation become card fields (ADR-009 phase 1) |
| 16 August 2026 | `_resolve_pronunciation` consults the index first, for every language |

The claim was measured against a card that had no pronunciation on it, one
week before pronunciation existed. It says the index would be used zero
times; today `_resolve_pronunciation` would consult it on every English word
before anything else. Two of the fields it would fill were not fields yet.

## What English actually looks like now

Measured on 27 August 2026 by reading the fields back out of a real
1094-card English package, alongside the other decks built the same day:

| deck | definition | examples | synonyms | antonyms | IPA | audio |
|---|---|---|---|---|---|---|
| French | 91% | 87% | 78% | 34% | 90% | 88% |
| German | 90% | 88% | 61% | 55% | 90% | 90% |
| **English** | **15%** | **15%** | 88% | 63% | **0%** | **0%** |

Two separate things went wrong that day and they should not be conflated.

**Definitions: a rate limit, now fixed.** Merriam-Webster answered 167 words
and then stopped, because five workers were pushing about 18 requests a
second. That is 8.43, and MW is paced now, so the honest expectation for
English definitions is back around 98%. **This is not an argument for the
index**, and using the 15% to argue for it would be arguing from a bug that
has already been fixed.

**Pronunciation and examples: a single point of failure, not fixed.**
dictionaryapi.dev returned HTTP 522 for the entire day. It is the only
source English has for IPA, for audio, and for example sentences. There is
no pacing fix for a host that is down.

That is the whole case. Every language with an index scored around 90% on
definitions and examples that day and was untouched by either outage,
because it never made a request. English is the only language with no floor
under it.

## What it would cost

| | |
|---|---|
| download | **502 MB** gzipped, measured, not the 475 MB in the warning |
| on disk | unmeasured. The other three landed at 131, 305 and 423 MB |
| build time | one pass, comparable to the others |
| runtime | none. It is read offline like every other index |

## What it would and would not change

**Pronunciation: the real win.** English is the only language where the
Pronunciation and IPA fields are structurally empty whenever one host is
unwell. The index carries IPA on 83 to 99% of rows in every language built
so far.

**Examples: a second source where there is currently one.**

**Definitions: no change, by design.** `fetch_definition` tries MW first for
English and would keep doing so, so the "better curated" half of 8.19's
reasoning survives intact. The index would sit behind MW, which is exactly
how it sits behind OMW for synonyms today (8.19's own layering argument).

**Synonyms and antonyms: no change.** They are already the two best fields
on an English card, at 88% and 63%, from WordNet and ConceptNet.

## One thing that must be fixed with it, not after

`_resolve_pronunciation` reads:

```python
if wiktdata.is_available(language):
    entry = wiktdata.lookup(lemma, language, pos=pos)
    if entry:
        return entry.ipa or None, entry.audio_url or None
    return None, None          # <- no fallthrough
if language == "en":
    ...dictionaryapi.dev
```

Building an English index therefore **takes pronunciation away from
dictionaryapi.dev entirely**, including for words the index does not have.
For those words English would go from "sometimes has audio" to "never has
audio", which is a regression bought with a 502 MB download.

The fix is small: fall through to dictapi when the index misses, rather than
returning early. It is listed here because it is the kind of thing that gets
found after the download rather than before it.

## Candidates

**1. Build the index, keep MW ahead of it for definitions.** Fixes the
pronunciation and example single points of failure. Costs 502 MB for anyone
learning English. Needs the fallthrough fix above.

**2. Add a second online pronunciation source instead.** Cheaper in disk,
but it keeps English dependent on the network for a field every other
language reads offline, and every candidate source is another host that can
have a bad day.

**3. Do nothing.** Defensible if the 522 was a one-off. It was not
investigated beyond observing that it lasted all day.

## Decision

Not made. This ADR exists to put the timeline in front of the person who has
to choose, because the recorded decision is not wrong so much as it is
answering a question about a card that no longer exists.

The recommendation is candidate 1, with the fallthrough fixed in the same
change, and `_DISCOURAGED["en"]` rewritten rather than deleted: the "better
curated" point is still true and still the reason MW stays first.
