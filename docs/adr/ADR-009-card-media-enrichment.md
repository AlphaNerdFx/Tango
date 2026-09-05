# ADR-009: Card media enrichment: audio, pronunciation, and images

**Status:** proposed
**Date:** 10 August 2026
**Supersedes:** nothing
**Related:** ADR-008 (per-language dictionary sources), ARCHITECTURE.md 8.19
(offline Wiktionary index), CLAUDE.md 3.1, 3.2, 3.6

---

## Context

Cards today are text only: word, class, definition, two dictionary examples,
the sentence from the video, synonyms, antonyms. The request is to add four
kinds of media:

1. Audio of the sentence **as spoken in the video**
2. Audio of the two dictionary example sentences
3. Isolated audio of the word alone, for pronunciation
4. An image representing the word

The pedagogical case is not in dispute, hearing a word in the speaker's own
voice, and again in isolation, is most of what separates recognition from
production. This ADR is about which sources can supply that, what each costs,
and which of the four can be built without breaking constraints this project
has already committed to.

The four are deliberately assessed separately. They have almost nothing in
common: different sources, different licences, different failure modes, and
one of them has a legal question the others do not.

---

## Evidence gathered

All figures below were measured, not estimated.

### Pronunciation audio and IPA already exist in data we ship

Sampling 20,000 German entries from the kaikki extract already used to build
the offline index (`wiktdata.py`):

| | share |
|---|---|
| entries with any `sounds` data | 91.5% |
| entries with an IPA transcription | 91.5% |
| entries with an audio file URL | 65.7% |

A representative entry:

```
"Hallo" -> ipa "[haˈloː]"
           ogg_url https://commons.wikimedia.org/wiki/Special:FilePath/De-Hallo.ogg
           mp3_url https://upload.wikimedia.org/.../De-Hallo.ogg.mp3
```

This is decisive for item 3. Two thirds of words have **real human
pronunciation** already linked from the data the index is built from, and
91.5% have IPA. No text-to-speech, no API key, no per-word request to a third
party at card-build time, and the licence is the Commons licence rather than
a scraped file of unknown provenance.

The index does not currently store either field, `_joined()` reads only
definitions, examples, synonyms and antonyms, so capturing them requires an
index rebuild, not a new source.

### Transcript timing is already available

`transcript.get_snippets()` keys snippets by `snippet.start` and records
`start + duration`. Slicing the audio for a given sentence is therefore an
arithmetic problem, not a search problem. What is missing is the audio.

### Images: free and licensed, but only for concrete nouns

Wikimedia Commons search, no API key required, five German words:

| word | result | licence |
|---|---|---|
| Hund | photo of a dog | CC BY-SA 4.0 |
| Haus | photo of a house | CC BY-SA 3.0 |
| Freiheit | a tram in Berlin | Attribution |
| laufen | a 1920 coin from the town of Laufen | Public domain |
| schwierig | a hard disk head crash | CC BY-SA 3.0 |

Concrete nouns return usable images. Abstract nouns, verbs and adjectives
return noise, and `laufen` shows the specific failure mode: the search matched
a **place name** spelled the same way. A wrong image is worse than no image
for memorisation, it teaches the wrong association, so any image feature
needs a relevance gate, not just a search call.

---

## Candidates per item

### 1. Audio of the sentence from the video

The only source is the video itself. That means downloading its audio with
`yt-dlp` and cutting it with `ffmpeg` at the transcript timestamps.

**This is the one item with a legal question rather than an engineering one.**
Downloading audio from YouTube is contrary to YouTube's Terms of Service,
regardless of what the audio is used for afterwards. That is a statement about
the terms, not about what any individual's local copyright law permits.

It also carries the heaviest engineering cost of the four:

- `yt-dlp` plus an `ffmpeg` binary, the first is a Python package that needs
  frequent updating to keep working, the second is a system dependency this
  project has never required
- Downloads the whole audio track to extract a few hundred clips of a few
  seconds each
- yt-dlp breaks regularly when YouTube changes, which makes it a recurring
  maintenance cost, not a one-off

Given CLAUDE.md 3.6 already resists heavy runtime dependencies, and given the
ToS position, this item cannot be recommended on the same terms as the other
three. Options, in order of preference:

- **(a) Do not build it.** Items 2-4 deliver most of the pedagogical value.
- **(b) Build it as a clearly-labelled opt-in** behind an optional dependency
  group and a flag that is off by default, with the ToS position stated in
  the CLI at the point of use, so the choice is the user's and is informed.
- **(c) Accept a user-supplied audio file** for a video they already have,
  which sidesteps the download entirely and reuses all the slicing work.

Option (c) is worth noting because it costs almost nothing once (b) exists:
the slicing, the media naming and the genanki wiring are identical, and only
the acquisition step differs.

### 2. Audio of the example sentences

The framing here changed after measurement, and the change matters.

The obvious reading is "we have a sentence, now synthesise audio for it",
which is text-to-speech. The better reading is the reverse: **take the
sentence from a source that already has a recording of it.** Real human audio
for the exact sentence beats synthesised audio for our sentence, and it
removes the TTS dependency from the common case entirely.

**Tatoeba** supplies exactly that, a CC-licensed sentence corpus with
native-speaker recordings. Measured against the live API, German, `Haus`:

```
370 German sentences with audio for that one word
  "Hau rein."        audio 695264  by MisterTrouser
  "Hau ab!"          audio 42296   by peschiber
  "Geh nach Hause."  audio 470082  by moskytoo
```

And it is bulk-downloadable, which is the pattern this project already uses
for dictionaries rather than querying per word:

```
sentences_with_audio.tar.bz2      6.4 MB   (audio index, all languages)
per_language/deu/deu_sentences    12.0 MB
```

That is small, three orders of magnitude below the Wiktionary extracts, so
the index build cost is negligible.

The cost is a design consequence rather than an engineering one: the card's
example sentence would come from Tatoeba when a recording exists, and from
Wiktionary otherwise. Two sources for one field, chosen per card by whether
audio is available. That is a legitimate trade, a heard sentence is worth
more than a marginally better-chosen written one, but it must be a decision
taken deliberately, not a side effect of adding audio.

Caveat found while testing: per-recording licence metadata came back empty on
all three samples above. Tatoeba sentences are CC BY 2.0 FR, but audio
licensing is per contributor and needs checking against the bulk export's own
licence columns before anything is redistributed.

**TTS remains the fallback**, for sentences with no recording:

| option | licence / cost | offline | multilingual | notes |
|---|---|---|---|---|
| Piper | MIT, local models | yes | ~30 languages | small, fast on CPU, per-language voice download |
| espeak-ng | GPL, local | yes | 100+ | robotic; adequate for drilling, unpleasant to listen to |
| Coqui TTS | MPL, local | yes | many | heavy; pulls torch, which is already a sore point |
| gTTS | unofficial endpoint | no | many | no key but undocumented and rate-limited; a scraped API |

Piper is the fit: genuinely offline, genuinely free, small enough not to
offend 3.6 if it lives in an optional group, and its voices are per-language
downloads that mirror the pattern `make dictionary` already establishes.

gTTS should be rejected for the same reason the Wikimedia rate limit was
rejected in ADR-008 and issue #8, it is an unofficial endpoint that would
have us engineering around someone else's limits.

### 3. Isolated audio of the word

To be explicit, since the phrasing invites confusion: the 65.7% figure is
**real recorded audio**, actual `.ogg` and `.mp3` files of human speakers,
hosted on Wikimedia Commons and linked from the entry (`De-Hallo.ogg`). It is
not the IPA. The IPA is the separate 91.5%, useful as a text field beside the
audio rather than instead of it.

Three tiers, in order:

1. **Commons audio via the index**, 65.7% of German entries, real human
   speakers, already licensed, one URL fetch per word at build time
2. **Tatoeba word-level recordings** where a single-word sentence exists,
   using whatever is built for item 2, free, no key, already downloaded
3. **Piper TTS** for whatever remains

Rejected for tier 2: **Forvo**, which has the largest pronunciation corpus by
some distance, because its API requires a key and caps requests on the free
tier, the same grounds on which ADR-008 rejected PONS and this document
rejects gTTS. Worth revisiting only if tiers 1-3 leave a gap that matters,
measured on real vocabulary rather than assumed.

Plus **IPA as a text field** at 91.5% coverage, which is free the moment the
index stores it and is arguably as useful as the audio for pronunciation.

### 4. Image

- **Wikimedia Commons**, free, no key, licensed, and measured above: good for
  concrete nouns, noise otherwise
- **Openverse**, aggregates CC images, no key, same relevance problem
- **Unsplash / Pixabay**, better relevance, require API keys and have request
  caps, which puts them in the category ADR-008 already rejected

The relevance problem is the deciding factor, not the source. A defensible
rule: attempt an image **only** for concrete nouns, `Class` is already
recorded per card, so `noun` plus an entry in a concreteness list is a cheap
gate, and leave the field empty otherwise. Better an empty field than a photo
of a coin on the card for "to run".

---

## Decision

Proposed, in dependency order, each independently shippable:

**Phase 1: IPA and Commons pronunciation.** Extend the index schema to store
`ipa` and the audio URL from the `sounds` block, rebuild the indexes, add two
card fields. Highest value per unit of work by a wide margin: 91.5% and 65.7%
coverage from data already downloaded, no new runtime dependency, no new
service, no licence question beyond attribution.

**Phase 2: Tatoeba sentence audio.** Bulk index (6.4 MB, negligible beside
the dictionaries), real human recordings, and the example sentence taken from
the recording rather than a recording made for the sentence. TTS via Piper
becomes the fallback for what Tatoeba does not cover, in an optional
dependency group with voices installed per language like spaCy models.

**Phase 3: images, gated to concrete nouns.** Commons, with the POS gate and
an attribution field. Ship it disabled by default until the relevance gate is
measured on real vocabulary rather than five hand-picked words.

*Scheduled 4 September 2026 as ROADMAP v0.11.0, with that measurement as an
explicit acceptance target. It had been designed and deliberately unbuilt
since 17 August, which made it invisible to anyone reading only the roadmap.
The sequencing warning below still stands in part: sense selection by part of
speech shipped in v0.5.x (ARCHITECTURE 8.29), but 8.25 and the `Tapa` card
record that the pipeline still takes the first dictionary entry rather than
the sense matching the video.*

---

## Amendment, 5 September 2026: the gate is not available everywhere

Phase 3's gate was designed as cheap: "`Class` is already recorded per card,
so `noun` plus an entry in a concreteness list is a cheap gate". That
assumed a concreteness list exists for each language. Measured against 835
real nouns from this project's own definition cache, across the three
languages it has real decks in, it does not.

The signal tried was WordNet's lexicographer files, which are already in the
stack through nltk and are exactly a concreteness distinction:
`noun.artifact`, `noun.animal`, `noun.food`, `noun.substance` against
`noun.cognition`, `noun.feeling`, `noun.state`, `noun.communication`.

| language | nouns | no WordNet entry | strict gate admits | approx. cards with an image |
|---|---|---|---|---|
| French | 491 | 12.2% | 18.5% | ~9% |
| English | 72 | 4.2% | 8.3% | ~4% |
| German | 272 | **100%** | **0%** | 0% |

**German has no WordNet in OMW at all.** Not a loading failure: `wn.langs()`
returns 32 languages and `deu` is not among them, and
`wn.synsets("Hund", lang="deu")` raises "Language deu is not supported".
German is 39.5% of this project's cached definitions.

Two other things the measurement showed.

**The reach is small even where it works.** French is the best case, and it
is roughly one card in eleven. That buys a new card field, image downloads,
storage and an attribution obligation.

**The sense-selection warning above is visible in the data, not theoretical.**
`planète` returns `noun.cognition` as its first sense and `enfant` returns a
*verb*. A gate reading the first sense would put an image of the wrong thing
on a card, which is the loud error this ADR warns about. The gate must
therefore require **every** noun sense to be concrete, not the first and not
the majority.

### Decision

Ship images for the languages where a concreteness list exists, and leave
the field empty elsewhere. This follows ADR-011's precedent: that ADR
reversed 8.19's exclusion of an English index once the measurement justified
it, rather than holding the whole feature back for uniformity.

`tango doctor` reports which languages support images, so a German user is
told the field is unavailable rather than left wondering why it is empty.

The alternatives were considered and are recorded rather than dismissed: a
multilingual gate built from ConceptNet's `IsA` edges, which is plausible
because ADR-010 already streams that dump for 22 languages but is unmeasured;
and translating the lemma to English to gate on the best-covered WordNet,
which inherits every translation error as a possibly wrong image.

**Phase 4: dropped as originally posed.** Downloading YouTube audio is
contrary to YouTube's terms, and that is settled rather than weighed. What the
request actually wanted, the word heard in real speech, is delivered by
phases 1-3 from sources that exist to be redistributed. The `Example from
Youtube Video` field stays as text, which is still the card's most reliable
field at 100% coverage.

The user-supplied-audio path (option (c)) remains available if someone wants
the video's own audio for a file they already hold, and costs little once the
slicing work exists. It is not scheduled.

---

## Amendment, 6 September 2026: the source was wrong, not the gate

The amendment above concluded that images should ship only where a
concreteness list exists, leaving German out entirely. That conclusion was
correct about the *gate it tested* and wrong about the feature, because both
it and the original decision assumed the source had to be Wikimedia Commons
**text search**. A text search matches a spelling, not a meaning, which is
why `laufen` returned a coin from the town of Laufen.

Resolving the lemma to a **concept** behaves completely differently:

```
lemma -> Wikipedia article (language specific)
      -> Wikidata item      (language independent)
      -> P31 gate, then P18 image or the article lead image
```

The middle step is what fixes German. `Hund` and `chien` both resolve to
Q144, so one judgement about whether dogs can be photographed serves every
language, and no German WordNet is needed. Verbs and adjectives fall out for
free: `laufen` has no lead image and `schwierig` has no article at all.

### What a coverage number could not see

Measured against 374 real nouns from the definition cache, the first version
of this gate admitted **36.9%**, which read as a success. Then the files
were opened:

| word | image returned | what the card would teach |
|---|---|---|
| `leben` | HumanNewborn.JPG | Leben means baby |
| `englisch` | Webster_Orthography_1828 | a page of an 1828 spelling book |
| `cowardice` | Cowardly_lion2.jpg | the Wizard of Oz lion |
| `government` | a group portrait of Dutch ministers | these specific men |
| `loi` | the Palais-Bourbon | law is a building |
| `couple` | a Bolero choreography | (a disambiguation page) |

Every one is the wrong-association failure this ADR was written to prevent,
and the admit rate was blind to all of them. This is the specific reason the
verification step for images says to look at the cards.

Each was reached through an identifiable Wikidata class, so each is now
refused by name: type of property, natural phenomenon, biological process,
natural language, personality trait, type of organization, concept in
physics, administrative territorial entity type, legal term, legal form, and
Wikimedia disambiguation page. `tests/test_images.py` carries one case per
class, plus the matched pair asserting that a denylist wide enough to refuse
`government` still admits a dog, a house, an aeroplane and oil.

### The measurement

Tightening cost 3.7 points of coverage and removed the failures above:

| language | nouns | WordNet gate (5 Sept) | this gate | change |
|---|---|---|---|---|
| French | 150 | ~9% | **33.3%** | +24 |
| German | 150 | **0%** | **38.0%** | +38 |
| English | 74 | ~4% | **23.0%** | +19 |
| overall | 374 | | **33.2%** | |

ADR-010 shipped the ConceptNet antonym index on +12.5 points for French.
Every language here clears that bar, and German clears it from zero.

### Decision

**Images ship, for every language, gated on Wikidata rather than WordNet.**
This reverses the amendment above, which restricted them to languages with a
concreteness list. That restriction was a consequence of the source, and the
source changed.

**The WordNet gate is not used as a second opinion**, which the plan for
this work had proposed. Measured: `is_concrete_noun` refuses `door` and
`blood`, because it requires every noun sense to be concrete and was
designed to be the only gate. Requiring both to agree would lose good cards
and gain nothing, since the Wikidata gate already refuses everything WordNet
refuses in this sample. It remains useful as a cross-check, and agreed with
86% to 100% of refusals where OMW covers the language.

**Two card fields, not one.** `Image` is field 12 and `Attribution` field 13.
The second is not decoration: Commons reports `AttributionRequired: true` on
the images this actually returns, and the dog photograph is CC BY-SA 2.0 by
Markus Trienke, so shipping a deck without the credit would breach the
licence.

**Thumbnails, not originals.** The first real download was 9.2 MB for one
photograph, for a card that displays it at 240px. Both routes now request
480px, and the same photograph is 46 KB. That is the same error as this
ADR's own audio estimate, which costed embedded audio at "tens of megabytes"
when real files are 16 to 30 KB, caught earlier this time only because the
download was run rather than reasoned about.

**The runtime cost was real and is now mostly gone.** The per-lemma path
cost four Wikimedia requests per noun, paced at one a second because
Wikimedia asks callers to pace, so a 400-noun deck added about 27 minutes to
a run. That was a fair reason not to enable images by default, and it was
also an artefact of asking one word at a time.

All three APIs accept 50 items per request, and one Wikidata call returns
P31 and P18 together, so the same deck costs about **24 requests instead of
1600**. Measured 6 September 2026 on 16 German words: 3.78s batched against
53.34s one at a time, a 14x speedup on a small sample and larger on a real
deck, with **16 of 16 results identical**. A 10-card package with images on
now builds in 4.9s.

`find_images()` is the batched entry point and `find_image()` remains for a
single lookup. Resolution is batched rather than threaded because it is
limited by Wikimedia's pacing, not by latency; the file downloads are still
threaded, because those are independent and per-file.

**`IMAGES_ENABLED` stays false pending one thing the measurement cannot
supply: a person looking at a deck of these cards in Anki.** The numbers now
justify the feature and the speed no longer argues against it, but the
failure this ADR guards against is visual, and the last time a coverage
number was trusted without opening the files it was hiding a newborn on the
card for `leben`. That review is the remaining gate on the default.

## Consequences

**Card fields may only be appended.** CLAUDE.md 3.2 makes field order
positional: genanki maps values to fields by index, and a mismatch writes
content into the wrong section silently, producing a valid `.apkg`. New fields
therefore take indices 10 and upward, and `_build_note()` and
`_build_fallback_note()` must both grow in the same order. Nothing may be
inserted between existing fields.

**Adding fields to the existing model is a schema change, and MODEL_ID must
not move.** 3.1 forbids changing `MODEL_ID`, which is right, it would orphan
every existing card's review history. But keeping the ID while changing the
field list means Anki sees a modified notetype on import and updates it. That
is supported, and existing notes gain empty fields rather than losing data.
It should be verified against a real collection with real review history
before shipping, not assumed, because the failure mode if it is wrong is the
one thing this project has said it will never risk.

**Deck size stops being trivial.** A 400-card deck today is a few hundred
kilobytes. With word audio, two sentence audios and an image per card it
plausibly becomes 50-150 MB. That affects import time, sync time for
AnkiWeb users, and whether a deck can be shared at all. Media should be
opt-in per type, and the CLI should report the size it is about to produce.

**Attribution becomes a requirement, not a courtesy.** Commons content is
CC BY-SA or similar. A deck that ships those files needs the attribution and
licence recorded on the card. That is a new field and a new obligation on
anyone distributing generated decks, which, for a project whose output is
meant to be shareable, is a design consideration rather than a footnote.

**The pipeline acquires a network fetch per card at build time**, where today
it fetches per *lemma* against text APIs and an offline index. Media fetches
are larger and slower, and Commons has its own rate expectations. The existing
circuit breaker and bounded thread pool apply, but the sizing was chosen for
text.

**Sense selection becomes more visible.** ARCHITECTURE 8.25 and the `Tapa`
card record that the pipeline takes the first dictionary entry rather than the
sense matching the video. A wrong definition is a quiet error; a wrong
*image* is a loud one. Phase 3 should not ship before sense selection
improves, or it will make an existing weakness conspicuous.
