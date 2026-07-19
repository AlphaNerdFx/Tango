# Tango

[![CI](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml/badge.svg)](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-v0.4.0--beta-orange)](https://github.com/youssefea/tango/releases/tag/v0.4.0)

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

Prerequisites: Python 3.9+, [Anki](https://apps.ankiweb.net/) desktop, [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on.

```bash
git clone https://github.com/AlphaNerdFx/Tango.git
cd tango
make all
cp .env.example .env
# fill in your API keys in .env
make run VIDEO_ID=<id> DECK="MyDeck"
```

Then import the generated .apkg from `output/` into Anki.

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` and fill in:

| Variable | Required | Description |
|---|---|---|
| MW_API_KEY | Yes | [Merriam-Webster API key](https://dictionaryapi.com/register/index.htm) (free, 1000 requests/day) |
| WEBSHARE_USERNAME | No | Webshare proxy username, needed if YouTube rate-limits your IP |
| WEBSHARE_PASSWORD | No | Webshare proxy password |
| ANKI_HOST | No | AnkiConnect URL. WSL users: set to your Windows host IP |
| DEF_LANG | No | Target language for definitions (e.g. en). Defaults to transcript language |
| LIBRETRANSLATE_URL | No | Local LibreTranslate server URL for translation mode |

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

Note: definition APIs have varying coverage by language. English vocabulary has the best coverage via Merriam-Webster. Other languages use dictionaryapi.dev natively.

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
- Class (part of speech)
- Definition (in DEF_LANG or native language)
- 1st Example Sentence (from dictionary, in original language)
- 2nd Example Sentence (from dictionary, in original language)
- Example from Youtube Video (transcript sentence)
- Synonyms (in original language)
- Antonyms (in original language)

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

- [x] Core pipeline: transcript to Anki cards
- [x] Language filter: subtitle selection by language code or deck name
- [x] Multilingual definitions: native language examples and synonyms
- [x] Translation mode: English definitions of non-English words
- [x] Fuzzy matching improvements for morphologically rich languages
- [ ] Single-letter and proper noun filtering
- [ ] Additional synonym and antonym APIs
- [ ] Async definition fetching for faster processing
- [ ] Full CLI tool with Typer, proper flags, and error handling
- [ ] Dockerfile for cloud deployment
- [ ] User vocabulary profiles from Anki review history
- [ ] Video recommendations based on vocabulary domain and level
- [ ] Web UI and browser extension

---

## Requirements

- Python 3.9+
- Anki desktop with AnkiConnect add-on (code: 2055492159)
- Merriam-Webster API key (free tier)
- spaCy model: python -m spacy download en_core_web_sm

---

## License

MIT
