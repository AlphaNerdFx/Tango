# Tango

## v0.1.0: Initial working pipeline

First end-to-end release of the Tango pipeline.

### What's included

- YouTube transcript extraction via `youtube-transcript-api` with Webshare proxy support
- spaCy NLP vocabulary extraction: lemmatization, POS filtering, first-appearance ordering
- AnkiConnect duplicate detection with fuzzy confidence interval (WRatio)
- Automatic sentence-deck detection: skips fuzzy matching on question/sentence-front decks
- Merriam-Webster primary definitions with dictionaryapi.dev fallback and SQLite caching
- genanki card generation: definition, two examples, synonyms, antonyms, fallback cards
- SQLite state tracking: processed videos, vocabulary, generated packages
- Full CLI via `make run`, `make review`, `make backlog`
- 323 unit tests, 0 failures

### Known limitations

- Requires Anki desktop running locally with AnkiConnect
- WSL users must set `webBindAddress: 0.0.0.0` in AnkiConnect config and use Windows host IP
- No language filtering yet — transcript language is not matched against deck language
- Auto-generated captions produce occasional garbled lemmas (upstream YouTube ASR issue)

### Not yet built

Phase 2 (containerization, multi-user), Phase 3 (ML vocabulary profiling, video recommender)