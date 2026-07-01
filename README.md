
<div align="center">

---

## What it does

You give Tango a YouTube video ID. It gives you an Anki `.apkg` file ready to import.

```
YouTube video → transcript → spaCy NLP → deck check → definitions → Anki cards
```

Between extraction and card creation, Tango:

- Filters vocabulary by part of speech (nouns, verbs, adjectives, adverbs)
- Checks your existing Anki deck for duplicates using fuzzy matching
- Detects sentence-structured decks and skips meaningless fuzzy comparisons
- Fetches definitions from Merriam-Webster with dictionaryapi.dev as fallback
- Pulls example sentences from both the dictionary and the original video
- Builds cards with definition, two examples, synonyms, and antonyms
- Creates fallback cards (word + transcript sentence) when no definition is found

---

## Quick start

**Prerequisites:** Python 3.9+, [Anki](https://apps.ankiweb.net/) desktop, [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on.

```bash
git clone https://github.com/youssefea/tango.git
cd tango/yt-anki-pipeline
make all                         # create venv, install deps, download spaCy model
cp .env.example .env             # fill in your API keys
make run VIDEO_ID=<id> DECK="MyDeck"
```

Then import the generated `.apkg` from `output/` into Anki.

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` and fill in:

| Variable              | Required | Description                                                                             |
| --------------------- | -------- | --------------------------------------------------------------------------------------- |
| `MW_API_KEY`        | Yes      | [Merriam-Webster API key](https://dictionaryapi.com/register/index.htm) (free, 1000/day) |
| `WEBSHARE_USERNAME` | No       | Webshare proxy username — needed if YouTube rate-limits your IP                        |
| `WEBSHARE_PASSWORD` | No       | Webshare proxy password                                                                 |
| `ANKI_HOST`         | No       | AnkiConnect URL. WSL users: set to your Windows host IP (e.g.`http://172.x.x.x:8765`) |

WSL users: AnkiConnect must bind to `0.0.0.0` (not `127.0.0.1`) to be reachable from WSL. Change this in Anki → Tools → Add-ons → AnkiConnect → Config.

---

## Commands

```bash
make run VIDEO_ID=<id> DECK="Deck::Name"   # run full pipeline
make review DECK="Deck::Name"              # process deferred review.json words
make backlog DECK="Deck::Name"             # process backlog when Anki was unavailable
make test                                  # unit tests (no network, no Anki needed)
make test-all                              # full suite including integration tests
make format                                # auto-format with black
make lint                                  # lint with ruff
make clean                                 # remove venv, output, cache
```

---

## Project structure

```
yt-anki-pipeline/
├── src/pipeline/
│   ├── config.py        # all constants and environment config
│   ├── transcript.py    # YouTube transcript extraction
│   ├── nlp.py           # spaCy vocabulary extraction
│   ├── deck.py          # AnkiConnect duplicate detection
│   ├── definition.py    # MW + dictionaryapi.dev fetching
│   ├── cards.py         # genanki card and package generation
│   ├── state.py         # SQLite state management
│   └── __main__.py      # CLI entry point
├── tests/
├── pyproject.toml
├── Makefile
└── .env
```

---

## How duplicate detection works

Tango compares each extracted lemma against your existing deck's card fronts:

- **Score > 90** → word already in deck, skip
- **Score 60–90** → possible duplicate, prompt you to decide
- **Score < 60** → new word, fetch definition and create card

Short words (under 4 characters) use exact matching only — fuzzy scoring on short tokens produces false positives. Sentence-structured decks (where fronts are questions or example sentences rather than single words) skip fuzzy matching entirely and use exact match only.

---

## Roadmap

- [X] Core pipeline — transcript → NLP → deck check → definitions → Anki cards
- [ ] Language filter — auto-detect deck language and filter subtitles accordingly
- [ ] Real-time mode — surface new words as a video plays
- [ ] User profiles — vocabulary level modelling from Anki review history
- [ ] Video recommender — suggest videos based on vocabulary domain and level
- [ ] Web UI

---

## Requirements

- Python 3.9+
- Anki desktop with AnkiConnect add-on (`2055492159`)
- Merriam-Webster API key (free tier)
- spaCy model: `python -m spacy download en_core_web_sm`

---

## License

MIT