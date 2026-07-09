<div align="center">

# Tango

[![CI](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml/badge.svg)](https://github.com/AlphaNerdFx/Tango/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-v0.2.0--beta-orange)](https://github.com/youssefea/tango/releases/tag/v0.2.0)

Turn any YouTube video into Anki flashcards, automatically.

</div>

---

## What it does

You give Tango a YouTube video ID. It gives you an Anki .apkg file ready to import.

```
YouTube video -> transcript -> spaCy NLP -> deck check -> definitions -> Anki cards
```

Between extraction and card creation, Tango:

- Resolves the target language from a flag or deck name, covering 40 languages
- Prefers manually created subtitles over auto-generated ones
- Filters vocabulary by part of speech (nouns, verbs, adjectives, adverbs)
- Checks your existing Anki deck for duplicates using fuzzy matching with a confidence score
- Detects sentence-structured decks and skips meaningless fuzzy comparisons automatically
- Fetches definitions from Merriam-Webster with dictionaryapi.dev as fallback
- Pulls example sentences from both the dictionary and the original video transcript
- Builds cards with definition, two examples, synonyms, and antonyms
- Creates fallback cards (word + transcript sentence) when no definition is found

---

## Quick start

Prerequisites: Python 3.9+, [Anki](https://apps.ankiweb.net/) desktop, [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on.

```bash
git clone https://github.com/youssefea/tango.git
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

WSL users: AnkiConnect must bind to `0.0.0.0` instead of `127.0.0.1` to be reachable from WSL. Change this in Anki -> Tools -> Add-ons -> AnkiConnect -> Config.

---

## Commands

```bash
make run VIDEO_ID=<id> DECK="Deck::Name"             # run full pipeline
make run VIDEO_ID=<id> DECK="Deck::Name" LANGUAGE=fr # explicit language code
make run VIDEO_ID=<id> DECK="Deck::Name" --verbose   # debug logging
python -m pipeline --list-languages                  # show all supported language codes
make review DECK="Deck::Name"                        # process deferred review.json words
make backlog DECK="Deck::Name"                       # process backlog when Anki was unavailable
make test                                            # unit tests, no network or Anki needed
make test-all                                        # full suite including integration tests
make format                                          # auto-format with black
make lint                                            # lint with ruff
make clean                                           # remove venv, output, and cache
```

---

## Language support

Tango resolves the target language from the LANGUAGE flag or by reading the deck name.

```bash
make run VIDEO_ID=<id> DECK="French"          # inferred from deck name
make run VIDEO_ID=<id> DECK="MyDeck" LANGUAGE=fr  # explicit flag, always wins
```

If neither the flag nor the deck name resolves to a known language, the pipeline exits with a message explaining how to fix it.

Run `python -m pipeline --list-languages` to see all 40 supported languages and their codes.

---

## How duplicate detection works

Tango compares each extracted lemma against your existing deck's card fronts.

Score above 90: word already in deck, skipped.
Score between 60 and 90: possible duplicate, you decide at the prompt.
Score below 60: new word, definition fetched and card created.

Short words under 4 characters use exact matching only since fuzzy scoring on short tokens produces false positives. Sentence-structured decks (where fronts are questions or definitions rather than single words) skip fuzzy matching entirely.

---

## Project structure

```
tango/
├── src/pipeline/
│   ├── config.py        config and environment variables
│   ├── language.py      BCP-47 language resolution and transcript selection
│   ├── transcript.py    YouTube transcript extraction
│   ├── nlp.py           spaCy vocabulary extraction
│   ├── deck.py          AnkiConnect duplicate detection
│   ├── definition.py    definition fetching and caching
│   ├── cards.py         Anki card and package generation
│   ├── state.py         SQLite state management
│   └── __main__.py      CLI entry point
├── tests/
├── docs/
├── pyproject.toml
└── Makefile
```

---

## Roadmap

### In progress

- [x] Core pipeline: transcript to Anki cards
- [x] Language filter: subtitle selection by language code or deck name inference
- [ ] Multi-language definition APIs: expand beyond English-only sources to cover all 40 supported languages
- [ ] Fuzzy matching improvements: language-aware duplicate detection for non-English decks

### Requires frontend first

- [ ] Real-time mode: surface new words as a video plays (planned as a browser extension synced with YouTube playback)
- [ ] User vocabulary profiles from Anki review history
- [ ] Video recommendations based on vocabulary domain and level

### Frontend and backend

- [ ] Web UI
- [ ] Browser extension

---

## Known limitations

Definition APIs (Merriam-Webster and dictionaryapi.dev) are English-only. Non-English vocabulary will produce fallback cards (word + transcript sentence) rather than full definition cards until language-specific dictionary APIs are added.

Fuzzy duplicate detection produces false positives on non-English decks where card fronts use sentence structures. This is a known issue being tracked for a future fix.

---

## Requirements

- Python 3.9+
- Anki desktop with AnkiConnect add-on (code: 2055492159)
- Merriam-Webster API key (free tier)
- spaCy model: `python -m spacy download en_core_web_sm`

---

## License

MIT