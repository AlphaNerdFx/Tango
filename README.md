# Tango

[![CI](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml/badge.svg)](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/tango-anki?color=orange&label=pypi)](https://pypi.org/project/tango-anki/)
[![Release](https://img.shields.io/github/v/release/AlphaNerdFx/Tango?color=orange&label=release)](https://github.com/AlphaNerdFx/Tango/releases/latest)

Turn any YouTube video into Anki flashcards, automatically.

---

## What it does

You give Tango a YouTube video ID. It gives you an Anki .apkg file ready to import.

```
YouTube video -> transcript -> spaCy NLP -> deck check -> definitions -> Anki cards
```

Between extraction and card creation, Tango:

- Resolves the target language from a flag or deck name and fetches the right subtitles
- Prefers manually created transcripts over auto-generated ones
- Filters vocabulary by part of speech (nouns, verbs, adjectives, adverbs)
- Checks your existing Anki deck for duplicates using a three-condition fuzzy match that handles morphologically rich languages
- Detects sentence-structured decks and skips fuzzy matching where it would not be meaningful
- Fetches example sentences, synonyms, and antonyms in the original transcript language
- Fetches the definition in your chosen output language (English by default, or native)
- Builds cards with up to two dictionary examples, a video transcript example, synonyms, and antonyms
- Creates minimal fallback cards for words with no definition found

---

## Quick start

Prerequisites: Python 3.10+, [Anki](https://apps.ankiweb.net/) desktop, [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on.

```bash
pip install tango-anki
tango install-model en
tango run <video-id> --deck "MyDeck"
```

The command is `tango`; the package is `tango-anki`, because `tango` on PyPI
is an unrelated project. Run `tango doctor` at any point and it reports what
is installed, what is missing, and the command that fixes each.

Then import the generated .apkg from `output/` into Anki, or say yes when it
offers to import for you.

<details>
<summary>Working on Tango itself?</summary>

```bash
git clone https://github.com/AlphaNerdFx/Tango.git
cd Tango
make all
cp .env.example .env
make run VIDEO_ID=<id> DECK="MyDeck"
```

</details>

<details>
<summary>Prefer Docker? No Python setup at all.</summary>

The Dockerfile is in the repository, and in the sdist if you would rather
not clone:

```bash
git clone https://github.com/AlphaNerdFx/Tango.git && cd Tango
docker build -t tango .
docker run --rm -v "$PWD/out:/data/output" tango run <video-id> --deck "French"
```

The English model is baked in. All state lives in `/data`, so mount it to
keep the definition cache between runs and to get the .apkg out. Anki runs on
your host, not in the container: on Docker Desktop the default
`ANKI_HOST=http://host.docker.internal:8765` already points at it, and on
native Linux add `--network host`.

</details>

**Learning a language other than English?** Build its offline dictionary
first, or every card will read "No definition found":

```bash
tango build-dictionary fr        # any code from `tango languages`
```

One large download per language (a few hundred MB), then it works offline
forever. See [Definition coverage](#definition-coverage) for why this is
needed and what it gives you.

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` (or run `tango setup` for
a guided walkthrough) and fill in what you need. Nothing here is required to
run the pipeline:

| Variable | Required | Description |
|---|---|---|
| MW_API_KEY | No | [Merriam-Webster API key](https://dictionaryapi.com/register/index.htm) (free, 1000 requests/day). Improves English definitions; dictionaryapi.dev is used automatically without one |
| PROXY_HTTP_URL, PROXY_HTTPS_URL | No | Your own proxy, only needed if YouTube starts rate-limiting your IP. See Proxy notes below before using one |
| WEBSHARE_USERNAME, WEBSHARE_PASSWORD | No | Alternative to the above if you specifically use Webshare |
| ANKI_HOST | No | AnkiConnect URL. Defaults to `http://localhost:8765`, which is right everywhere except WSL, and WSL is detected and handled without setting this |
| LIBRETRANSLATE_URL | No | Local LibreTranslate server URL for translation mode |

### Proxy notes

Most users never need a proxy. Requests come from your own residential IP by
default, which is exactly the traffic YouTube doesn't aggressively block.

Webshare's free tier was tested and made things worse, not better: transcript
extraction failed with repeated 429 errors through the proxy but succeeded
without it, because free-tier datacenter IPs get blocked more aggressively
than residential ones. This project doesn't recommend a specific provider,
free or paid. If you're actually getting rate-limited, bring your own
reputable proxy (a paid residential/mobile proxy you already trust, or a
personal VPN).

The transcript language and the definition language are not `.env` variables.
They are options on the command (`tango run <id> --deck "French" --language fr
--def-lang en`), see below. Setting them in `.env` has no effect.

### WSL

One setting, and it is in Anki rather than here: AnkiConnect binds to
127.0.0.1, which WSL cannot reach. Change it to `0.0.0.0` in Anki under
Tools, Add-ons, AnkiConnect, Config.

You no longer need to set `ANKI_HOST`. Anki runs on the Windows side and
`localhost` from inside WSL is the Linux VM, so the connection is refused;
Tango notices, retries once against the Windows host it finds in the routing
table, and stays on whichever address answered. The old advice was to paste
that address into `.env` yourself, which worked until Windows rebooted and
reassigned it.

Setting `ANKI_HOST` explicitly still overrides all of this, and is never
second-guessed.

---

## Commands

```bash
tango run <id> --deck "Deck::Name"                     run the full pipeline
tango run <id> --deck "French" --language fr           pick the subtitle language
tango run <id> --deck "French" -l fr --def-lang en     define words in English
tango run <id> --deck "Deck::Name" --force             reprocess a finished video
tango review --deck "Deck::Name"                       process deferred review.json
tango backlog --deck "Deck::Name"                      process the Anki backlog

tango doctor                                           what is installed, what is missing
tango languages                                        supported language codes
tango setup                                            guided .env walkthrough
tango install-model fr                                 spaCy model for one language
tango install-translation de:en                        translation model for one pair
tango build-dictionary fr                              offline Wiktionary index
tango build-antonyms                                   offline antonym index
tango uninstall                                        remove the indexes and caches
```

Every command takes `--help`. Run `tango doctor` first if anything behaves
oddly: it reports what is installed, what is missing, and the command that
fixes each.

<details>
<summary>Working on Tango itself? The Makefile wraps all of the above.</summary>

```bash
make run VIDEO_ID=<id> DECK="Deck::Name"   make test        unit tests
make review DECK="Deck::Name"              make test-all    with integration
make backlog DECK="Deck::Name"             make format      black
make dictionary LANGUAGE=fr                make lint        ruff
make antonyms                              make coverage    line coverage
make translate-setup                       make clean       remove venv and caches
```

`make help` lists all of them. The Makefile is a convenience for working in a
clone; it is not installed by the package.

</details>

---

## Language support

Tango resolves the target language from the deck name (a deck named "French" fetches French subtitles) or from an explicit `--language` option. The explicit option always wins.

Tango recognises 45 language codes in a deck name. 25 of them can produce
cards: Catalan, Chinese, Croatian, Danish, Dutch, English, Finnish, French,
German, Greek, Italian, Japanese, Korean, Lithuanian, Macedonian, Norwegian,
Polish, Portuguese, Romanian, Russian, Slovenian, Spanish, Swedish and
Ukrainian.

The other 20, Arabic and Hindi and Turkish among them, are recognised in a
deck name but have no spaCy model, so a run stops with a message saying so
rather than producing an empty deck. Run `tango languages` for the full
table of what each language has.

Example sentences, synonyms, and antonyms are always returned in the original transcript language. Definitions and grammatical class are returned in the `--def-lang` language if set, otherwise in the transcript language.

Translation between languages uses argostranslate locally, or community
LibreTranslate mirrors. It is an optional extra, because it is by far the
heaviest dependency:

```bash
pip install "tango-anki[translation]"
tango install-translation de:en
```

### Definition coverage

English is covered by Merriam-Webster and dictionaryapi.dev out of the box, at around 98%.

**Every other language needs `tango build-dictionary <code>`.** Without it, non-English cards show "No definition found" for essentially every word. This is not a limitation of a particular language: dictionaryapi.dev returns nothing usable for any non-English language tested, measured at 0% across French, German, Spanish, Portuguese, Japanese, Russian, Korean and Chinese.

The offline dictionary is built from Wiktionary data and works with no network access once built. Measured against real generated decks:

| language | definitions | examples | synonyms | antonyms |
|---|---|---|---|---|
| French | 95% | 92% | 83% | 20% |
| German | 91% | 84% | 60% | 51% |
| Russian | 91% | 72% | 72% | 46% |

Build it for English too, since 27 August 2026. That advice used to be the opposite, and the reversal is worth knowing: Merriam-Webster still writes better definitions and is still tried first, but it is the only source English has, it allows 1000 queries a day per key, and one 1094-word video exceeds that on its own. The index is the floor under it.

Measured on a real 1094-lemma English deck, the index supplies IPA for 96.4% of words, audio for 97.0% and an example for 90.3%, all offline. Before it, English cards carried none of those whenever dictionaryapi.dev was unreachable, which it was for the whole day this was measured. See `docs/ADR-011-english-offline-index.md`.

The antonym column above is what the Wiktionary index alone gives. Antonyms have their own optional index, built once for every language at the same time:

```bash
tango build-antonyms
```

It is a 498 MB download that is streamed rather than stored, leaving 4.3 MB on disk. Measured end to end on real decks, it takes French from 19.7% to 34.8%, German from 56.2% to 60.3% and Russian from 47.8% to 48.8%. The gain is concentrated in French because both sources extract the same Wiktionary edition with different tools, and they disagree about which words carry an antonym. The French dictionary index has one for 15,045 words and ConceptNet has 12,376, overlapping only partly; German already holds 30,616 against ConceptNet's 3,547, so it gains less. Without it, every card is exactly what it was.

---

## How duplicate detection works

Tango compares each extracted lemma against your existing deck's card fronts using three conditions that must all pass.

WRatio above 90: word already in deck, skipped.
WRatio between 60 and 90, token sort ratio above 50, and length ratio above 0.6: possible duplicate, you decide at the prompt.
Anything else: new word, definition fetched and card created.

The three-condition filter prevents false positives in morphologically rich languages. "commencer" no longer incorrectly matches "comme" even though WRatio scores it at 90.

Sentence-structured decks skip fuzzy matching entirely and use exact match only.

---

## Card fields

Each card contains:

- Word (front)
- Class (part of speech, written in the same language as the Definition)
- Definition (in the `--def-lang` language, or the word's own)
- 1st Example Sentence (from dictionary, in original language)
- 2nd Example Sentence (from dictionary, in original language)
- Example from Youtube Video (transcript sentence)
- Synonyms (in original language)
- Antonyms (in original language)
- VideoID and Source (where the card came from)
- IPA (pronunciation transcription, in the original language)
- Pronunciation (the recording itself, embedded so it plays in the card)

Everything describing the word stays in the transcript language, including
the recording. A German word defined in French is still pronounced in
German.

Fields are appended, never reordered. Indices 0-11 are what every
already-imported card is bound to. Adding one is a notetype schema change:
Tango aligns your collection's notetype before importing, and Anki will ask
for one full sync afterwards. See `CHANGELOG.md` for the v0.5.0 migration.

---

## Project structure

```
tango/
├── src/pipeline/
│   ├── config.py          config and environment variables
│   ├── language.py        language resolution and BCP-47 mapping
│   ├── translation.py     argostranslate integration and mirror fallback
│   ├── transcript.py      YouTube transcript extraction
│   ├── nlp.py             spaCy vocabulary extraction
│   ├── deck.py            AnkiConnect duplicate detection
│   ├── definition.py      definition fetching and caching
│   ├── cards.py           Anki card and package generation
│   ├── state.py           SQLite state management
│   └── __main__.py        CLI entry point
├── tests/
├── docs/
├── pyproject.toml
└── Makefile
```

---

## Roadmap

Goals are tracked per release tag in **[ROADMAP.md](docs/planning/ROADMAP.md)**, which also
records what v1.0.0 freezes and what is deliberately out of scope.

**v1.0.0 is a finished CLI:** installable from a package, running on
Windows, macOS and Linux, on low-end and high-end hardware alike.

| tag | goal | |
|---|---|---|
| v0.5.x | pronunciation on cards, in every language | done |
| v0.6.0 | card quality, what is *in* a field | done |
| v0.7.0 | the command line as a product | done |
| v0.8.0 | packaged and installable | **released, on PyPI** |
| v0.8.1 | documentation for people who can now install it | released |
| v0.8.2 | an install that looks after itself | released |
| v0.9.0 | nothing fails without saying why | next |
| v0.10.0 | runs on any operating system | |
| v0.11.0 | images on cards, gated to concrete nouns | |
| v0.12.0 | runs on modest hardware | |
| v1.0.0 | a finished CLI | |

Packaging moved forward three rungs on 27 August 2026, from v0.10.0, after
an outside review pointed out that the funnel had no top. Card quality,
portability and disk footprint all matter more once someone can run the
thing, and less than nothing before.

A browser extension, a web or desktop app, and distribution to other
language ecosystems are **out of scope** for 1.0.0, plausibly a separate
project sharing a common premise. See [ROADMAP.md](docs/planning/ROADMAP.md) §4.

Release history is in **[CHANGELOG.md](CHANGELOG.md)**.

### Where this is today

Published on PyPI as `tango-anki` since 3 September 2026, so
`pip install tango-anki` works and the command is `tango`. The pipeline is
in daily use and has generated real decks in French, German, Russian and
English; the coverage numbers in this README are measured on those decks
rather than estimated.

Known rough edges, all tracked:

- A fresh install still needs a dictionary index built per non-English
  language before definitions work. The spaCy model is offered
  automatically; the index is not, because it is a several-hundred-MB
  download per language and not something to start without being asked.
- The repository still assumes WSL2 with Anki on the Windows side in a few
  places. Reaching Anki is handled automatically now; the rest is v0.10.0.
- Everything depends on one transcript extraction path. If YouTube changes
  it, there is no fallback yet.

---

## Requirements

- Python 3.10+
- Anki desktop with the AnkiConnect add-on (code: 2055492159)
- A spaCy model for your language: `tango install-model en`
- An offline dictionary for any non-English language:
  `tango build-dictionary fr`

Optional: a free [Merriam-Webster API key](https://dictionaryapi.com/register/index.htm)
for better English definitions, and `pip install "tango-anki[translation]"`
for cross-language definitions.

`tango doctor` reports which of these you have and prints the command for
anything missing.

---

## License

MIT
