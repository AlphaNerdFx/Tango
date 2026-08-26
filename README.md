# Tango

[![CI](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml/badge.svg)](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-v0.5.3-orange)](https://github.com/AlphaNerdFx/Tango/releases/tag/v0.5.3)

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
git clone https://github.com/AlphaNerdFx/Tango.git
cd tango
make all
cp .env.example .env
# fill in your API keys in .env
make run VIDEO_ID=<id> DECK="MyDeck"
```

Then import the generated .apkg from `output/` into Anki.

**Learning a language other than English?** Build its offline dictionary
first, or every card will read "No definition found":

```bash
make dictionary LANGUAGE=fr        # any code from --list-languages
```

One large download per language (a few hundred MB), then it works offline
forever. See [Definition coverage](#definition-coverage) for why this is
needed and what it gives you.

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` (or run `make setup` for
a guided walkthrough) and fill in what you need. Nothing here is required to
run the pipeline:

| Variable | Required | Description |
|---|---|---|
| MW_API_KEY | No | [Merriam-Webster API key](https://dictionaryapi.com/register/index.htm) (free, 1000 requests/day). Improves English definitions; dictionaryapi.dev is used automatically without one |
| PROXY_HTTP_URL, PROXY_HTTPS_URL | No | Your own proxy, only needed if YouTube starts rate-limiting your IP. See Proxy notes below before using one |
| WEBSHARE_USERNAME, WEBSHARE_PASSWORD | No | Alternative to the above if you specifically use Webshare |
| ANKI_HOST | No | AnkiConnect URL. WSL users: set to your Windows host IP |
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

`LANGUAGE` and `DEF_LANG` are not `.env` variables. They're `make run` command
arguments (`make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr DEF_LANG=en`), see
below. Setting them in `.env` has no effect.

WSL users: AnkiConnect must bind to 0.0.0.0 instead of 127.0.0.1. Change this in Anki -> Tools -> Add-ons -> AnkiConnect -> Config. Set ANKI_HOST to your WSL gateway IP (find it with: ip route | grep default).

---

## Commands

```bash
make run VIDEO_ID=<id> DECK="Deck::Name"              run full pipeline
make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr       specify subtitle language
make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr DEF_LANG=en   English definitions
make review DECK="Deck::Name"                          process deferred review.json
make backlog DECK="Deck::Name"                         process Anki backlog
make translate-setup                                   install translation model
make test                                              unit tests, no network needed
make test-all                                          full suite with integration tests
make format                                            auto-format with black
make lint                                              lint with ruff
make clean                                             remove venv, output, and cache
```

To list all supported language codes:

```bash
python -m pipeline --list-languages
```

---

## Language support

Tango resolves the target language from the deck name (a deck named "French" fetches French subtitles) or from an explicit LANGUAGE flag. The explicit flag always wins.

40 languages are supported including French, Spanish, German, Japanese, Arabic, Russian, Chinese, Korean, and more.

Example sentences, synonyms, and antonyms are always returned in the original transcript language. Definitions and grammatical class are returned in DEF_LANG if set, otherwise in the transcript language.

Translation between languages uses argostranslate locally or community LibreTranslate mirrors. Run make translate-setup to install the local model for your language pair.

### Definition coverage

English is covered by Merriam-Webster and dictionaryapi.dev out of the box, at around 98%.

**Every other language needs `make dictionary LANGUAGE=<code>`.** Without it, non-English cards show "No definition found" for essentially every word. This is not a limitation of a particular language: dictionaryapi.dev returns nothing usable for any non-English language tested, measured at 0% across French, German, Spanish, Portuguese, Japanese, Russian, Korean and Chinese.

The offline dictionary is built from Wiktionary data and works with no network access once built. Measured against real generated decks:

| language | definitions | examples | synonyms | antonyms |
|---|---|---|---|---|
| French | 95% | 92% | 83% | 20% |
| German | 91% | 84% | 60% | 51% |
| Russian | 91% | 72% | 72% | 46% |

Do not build it for English. English already scores 98% from Merriam-Webster, whose definitions are better curated; the index would add nothing and costs a 475 MB download.

The antonym column above is what the Wiktionary index alone gives. Antonyms have their own optional index, built once for every language at the same time:

```bash
make antonyms
```

It is a 498 MB download that is streamed rather than stored, leaving 4.3 MB on disk. Measured end to end on real decks, it takes French from 19.7% to 34.8%, German from 56.2% to 60.3% and Russian from 47.8% to 48.8%. The gain is concentrated in French because the data comes from the English and French Wiktionary editions, and the dictionary index above is built from the English one. Without it, every card is exactly what it was.

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
- Definition (in DEF_LANG or native language)
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

Goals are tracked per release tag in **[ROADMAP.md](ROADMAP.md)**, which also
records what v1.0.0 freezes and what is deliberately out of scope.

**v1.0.0 is a finished CLI** — installable from a package, running on
Windows, macOS and Linux, on low-end and high-end hardware alike.

| tag | goal |
|---|---|
| v0.5.0 | pronunciation on cards, and a notetype that merges *(current)* |
| v0.5.1 | pronunciation for every language, starting with English |
| v0.6.0 | card quality |
| v0.7.0 | the command line as a product |
| v0.8.0 | runs on any operating system |
| v0.9.0 | runs on modest hardware |
| v0.10.0 | packaged and installable |
| v1.0.0 | a finished CLI |

A browser extension, a web or desktop app, and distribution to other
language ecosystems are **out of scope** for 1.0.0 — plausibly a separate
project sharing a common premise. See [ROADMAP.md](ROADMAP.md) §4.

Release history is in **[CHANGELOG.md](CHANGELOG.md)**.

---

## Requirements

- Python 3.10+
- Anki desktop with AnkiConnect add-on (code: 2055492159)
- Merriam-Webster API key (free tier)
- spaCy model: python -m spacy download en_core_web_sm

---

## License

MIT
