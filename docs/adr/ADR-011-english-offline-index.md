# ADR-011: An offline index for English

Status: Accepted, 27 August 2026

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

That is the whole case for the outage half. Every language with an index
scored around 90% on definitions and examples that day and was untouched by
either outage, because it never made a request. English is the only language
with no floor under it.

## The licence, which turned out to matter more than the outage

Merriam-Webster's own terms, which were read after this ADR was first
drafted and which change its conclusion from "worth doing" to "has to be
done":

> free as long as it is for non-commercial use, usage does not exceed 1000
> queries per day per API key, and use is limited to two reference APIs

and the key is "specific to your application". Commercial use is not a paid
tier, it is a case-by-case negotiation with Merriam-Webster.

**1000 a day is a per-user ceiling, and one video can exceed it.** The run
that prompted all of this needed 1094 lookups. Paced perfectly, on a key
with a completely unused allowance, it still could not have finished. No
amount of rate limiting fixes a daily cap, and no amount of caching helps
the first run.

**And it makes MW a licensing dependency.** Tango is a CLI a user installs
with their own key, so today's arrangement is sound: non-commercial use by
one person, their allowance, their key. But a product that is ever monetized
cannot ship English support that depends on MW without a negotiated licence,
and that is a strange thing for the *base* behaviour of one language to hang
on when every other language reads from a file.

The index does not replace MW. It puts a floor under English so MW is an
enhancement: better curated definitions when the key is present and the
allowance holds, and a working card when it is not.

## What it cost, and what it delivered

Built on 27 August 2026, so these are measurements rather than estimates:

| | |
|---|---|
| download | **502 MB** gzipped, not the 475 MB in the warning |
| on disk | **236 MB**, the smallest of the four despite the largest download |
| entries | 1 486 439 |
| build | one streamed pass, no unpacking |
| runtime | none. Read offline like every other index |

**Coverage, and a trap worth recording.** Read as a share of index rows,
English looks useless for the field this ADR was written about:

| | share of the 1.49M rows | share of 1094 real deck lemmas |
|---|---|---|
| IPA | 9.4% | **96.4%** |
| audio | 8.6% | **97.0%** |
| example | 18.1% | **90.3%** |
| found at all |, | 99.8% |

The row percentage is the wrong denominator and nearly cost this decision.
An index of 1.49 million entries is mostly rare, archaic and inflected
forms, 35.9% of it inflections alone, and none of those carry a recording.
The words a transcript actually contains are the common ones, and those are
covered almost completely. The draft of this ADR asserted "83 to 99% in
every language built so far" by extrapolating from German, French and
Russian, which is SESSION.md 6.14's standing lesson (measure per language
before generalising) being relearned for the price of one query.

## What it would and would not change

**Pronunciation: the real win, and now measured.** English cards went from
0% IPA and 0% audio, because the only source was down, to 96.4% and 97.0%
available offline on the same 1094-lemma deck.

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

**Candidate 1, accepted 27 August 2026.** Build the index, keep MW ahead of
it for definitions, and fix the fallthrough in the same change.

`_DISCOURAGED["en"]` is removed rather than reworded, because a prompt
asking "build it anyway?" is now asking the wrong question. The half of its
reasoning that survives is kept as a comment in its place: MW's definitions
are better curated, which is why `fetch_definition` still tries MW first.

The deciding argument is not the outage, which might have been a bad day. It
is that English is the only language whose base behaviour depends on a
third-party allowance of 1000 queries a day and a licence that does not
cover a commercial product. Every other language reads from a file it owns.
That asymmetry is defensible in a prototype and not in a finished CLI, which
is what 1.0.0 is meant to mean.
