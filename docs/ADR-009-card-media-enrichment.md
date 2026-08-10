# ADR-009: Card media enrichment — audio, pronunciation, and images

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

The pedagogical case is not in dispute — hearing a word in the speaker's own
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

The index does not currently store either field — `_joined()` reads only
definitions, examples, synonyms and antonyms — so capturing them requires an
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
for memorisation — it teaches the wrong association — so any image feature
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

- `yt-dlp` plus an `ffmpeg` binary — the first is a Python package that needs
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

### 2. Audio of the two dictionary example sentences

No corpus supplies audio for arbitrary sentences, so this is text-to-speech.

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
rejected in ADR-008 and issue #8 — it is an unofficial endpoint that would
have us engineering around someone else's limits.

### 3. Isolated audio of the word

Two tiers, in order:

1. **Commons audio via the index** — 65.7% of German entries, real human
   speakers, already licensed, one URL fetch per word at build time
2. **Piper TTS** for the remaining third, sharing whatever is built for item 2

Plus **IPA as a text field** at 91.5% coverage, which is free the moment the
index stores it and is arguably as useful as the audio for pronunciation.

### 4. Image

- **Wikimedia Commons** — free, no key, licensed, and measured above: good for
  concrete nouns, noise otherwise
- **Openverse** — aggregates CC images, no key, same relevance problem
- **Unsplash / Pixabay** — better relevance, require API keys and have request
  caps, which puts them in the category ADR-008 already rejected

The relevance problem is the deciding factor, not the source. A defensible
rule: attempt an image **only** for concrete nouns — `Class` is already
recorded per card, so `noun` plus an entry in a concreteness list is a cheap
gate — and leave the field empty otherwise. Better an empty field than a photo
of a coin on the card for "to run".

---

## Decision

Proposed, in dependency order, each independently shippable:

**Phase 1 — IPA and Commons pronunciation.** Extend the index schema to store
`ipa` and the audio URL from the `sounds` block, rebuild the indexes, add two
card fields. Highest value per unit of work by a wide margin: 91.5% and 65.7%
coverage from data already downloaded, no new runtime dependency, no new
service, no licence question beyond attribution.

**Phase 2 — TTS for example sentences and for words lacking Commons audio.**
Piper in an optional dependency group, voices installed per language like
spaCy models and dictionaries.

**Phase 3 — images, gated to concrete nouns.** Commons, with the POS gate and
an attribution field. Ship it disabled by default until the relevance gate is
measured on real vocabulary rather than five hand-picked words.

**Phase 4 — video audio, or not at all.** Requires an explicit decision on the
ToS position first. If built, option (b) or (c) above, never on by default.

---

## Consequences

**Card fields may only be appended.** CLAUDE.md 3.2 makes field order
positional: genanki maps values to fields by index, and a mismatch writes
content into the wrong section silently, producing a valid `.apkg`. New fields
therefore take indices 10 and upward, and `_build_note()` and
`_build_fallback_note()` must both grow in the same order. Nothing may be
inserted between existing fields.

**Adding fields to the existing model is a schema change, and MODEL_ID must
not move.** 3.1 forbids changing `MODEL_ID`, which is right — it would orphan
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
anyone distributing generated decks — which, for a project whose output is
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
