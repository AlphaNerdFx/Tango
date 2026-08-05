# ARCHITECTURE.md — Tango

Complete system documentation. Read alongside `CLAUDE.md`.

---

## 1. System overview

Tango is a single-machine Python pipeline. It runs as a command-line tool,
processes one YouTube video per invocation, and writes one `.apkg` file per run.
All state is stored in a local SQLite database. There is no server component and
no async I/O. Definition lookups are the one place with concurrency, a bounded
thread pool, described in the design patterns section below.

---

## 2. Repository layout

```
Tango/
├── .github/
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       ├── feature_request.md
│       ├── language_coverage.md
│       └── config.yml
├── docs/
│   ├── ADR_v0.4.0.pdf
│   ├── SAD_v0.4.0.pdf
│   ├── SRD_v0.4.0.pdf
│   ├── PRD_v0.4.0.pdf
│   ├── Code_Walkthrough.pdf
│   ├── Initial Python Libraries and APIs.pdf
│   └── Prototype Diagram.pdf
├── src/
│   ├── images/                     UI icon assets, unused by the pipeline
│   └── pipeline/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── language.py
│       ├── transcript.py
│       ├── nlp.py
│       ├── deck.py
│       ├── definition.py
│       ├── translation.py
│       ├── cards.py
│       └── state.py
├── tests/
│   ├── test_transcript.py
│   ├── test_language.py
│   ├── test_nlp.py
│   ├── test_deck.py
│   ├── test_definition.py
│   ├── test_translation.py
│   ├── test_cards.py
│   ├── test_state.py
│   └── test_main.py
├── output/                         generated .apkg files, gitignored
├── .tangovenv/                     virtual environment, gitignored
├── .env                            secrets and config, gitignored
├── .gitignore
├── pipeline.db                     SQLite state, gitignored
├── review.json                     deferred queue words, gitignored
├── Makefile
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── CLAUDE.md
├── SESSION.md
├── TASKS.md
└── ARCHITECTURE.md
```

The `src/` layout is deliberate. Without it, Python can import the package from
the working directory even when it is not installed, so tests pass locally
against an uninstalled package and fail for real users. `src/` forces an install
before import.

`src/images/` contains icon assets for a future UI. Nothing in the pipeline
references them.

---

## 3. Module responsibilities

### 3.1 config.py

Single source of truth for every constant. Calls `load_dotenv()` at import,
then reads each value with `os.getenv()` and assigns it to a module constant.

Every other module imports from `config` rather than calling `os.getenv()`
directly. This makes the system testable — a test can monkeypatch a config
constant without touching environment variables — and means there is exactly
one place to look to find what a setting is.

Constants defined here:

```
DB_PATH                   SQLite database path, default "pipeline.db"
OUTPUT_DIR                .apkg output directory, default "output"
REVIEW_FILE               deferred queue file, default "review.json"
ANKI_HOST                 AnkiConnect URL, default "http://localhost:8765"
ANKI_VERSION              AnkiConnect API version, fixed at 6
ANKI_TIMEOUT              seconds before AnkiConnect timeout, default 5
MODEL_ID                  genanki model ID, 1607392319 — NEVER CHANGE
DECK_ID                   genanki deck ID, 2059400110 — NEVER CHANGE
CONFIDENCE_HIGH           fuzzy score above which a word is SKIP, default 90
CONFIDENCE_LOW            fuzzy score below which a word is NEW, default 60
SHORT_WORD_THRESHOLD      words shorter than this use exact match only, default 4
MW_API_KEY                Merriam-Webster API key, from environment
MW_API_BASE               MW Collegiate API base URL
DICT_API_BASE             "https://api.dictionaryapi.dev/api/v2/entries"
API_TIMEOUT               seconds before definition API timeout, default 8
DEFINITION_FETCH_WORKERS  max concurrent definition lookups, default 5
CIRCUIT_BREAKER_THRESHOLD consecutive failures before a source is skipped, default 5
WEBSHARE_USERNAME         proxy credentials, optional
WEBSHARE_PASSWORD         proxy credentials, optional
PROXY_HTTP_URL            generic proxy alternative, optional
PROXY_HTTPS_URL           generic proxy alternative, optional
```

Per-language spaCy model selection is not a config constant. It lives in
`language.py`'s `SPACY_MODELS` mapping, described in section 3.2.

### 3.2 language.py

Resolves the BCP-47 language code for a run and selects the correct transcript.

`LANGUAGE_MAP` is a dictionary of roughly 200 lowercase language-name keys
mapping to about 40 distinct BCP-47 codes. Multiple keys map to each language
because users name decks differently — French is reachable as `french`,
`français`, `francais`, `frances`, and `französisch`.

`resolve_language_code(language_flag, deck_name)` implements the precedence:
the explicit `--language` flag always wins; if absent, the deck name is parsed;
if neither resolves, `LanguageResolutionError` is raised with a message telling
the user to rename the deck or pass the flag.

`_infer_from_deck_name(deck_name)` flattens `::` sub-deck separators,
lowercases, tokenises on whitespace, then checks each word against
`LANGUAGE_MAP`. It also checks multi-word phrases for cases like
"Traditional Chinese".

`resolve_transcript(transcript_list, language_code)` adds partial BCP-47
matching that `youtube-transcript-api` does not provide. That library uses
exact dictionary key lookup internally, so requesting `fr` will not match a
transcript labelled `fr-FR`. This function iterates all available transcripts,
finds those whose code starts with the requested base code, and among them
prefers manually created transcripts over auto-generated ones.

`SPACY_MODELS` maps 24 BCP-47 codes to their spaCy model name, e.g. `fr` to
`fr_core_news_sm`. `get_spacy_model(language_code)` resolves a code against
it: exact match first, then `_SPACY_CODE_ALIASES` for known BCP-47/spaCy
naming mismatches (Norwegian's macrolanguage code `no` needs the Bokmål
model `nb`), then the base code for regional variants like `zh-CN`. No match
raises `SpacyModelUnavailableError` naming the languages that are supported.
Closes #3; before this, every video regardless of language was tokenized
with the English model, silently corrupting non-English lemmatization. See
section 3.4 for how `nlp.py` caches the loaded models this returns.

### 3.3 transcript.py

Wraps `youtube-transcript-api`.

`_build_proxy()` reads `WEBSHARE_USERNAME`/`WEBSHARE_PASSWORD` or
`PROXY_HTTP_URL`/`PROXY_HTTPS_URL` from config and returns the appropriate
proxy config object, or `None` for no proxy.

`get_transcript(video_id, languages)` instantiates the API with the proxy
config, calls `api.list(video_id)`, and delegates transcript selection to
`language.resolve_transcript()`. Every library exception is caught and
re-raised as a typed module exception with an actionable message.

`get_snippets(transcript)` calls `transcript.fetch()`, iterates the snippets,
cleans each with `_clean()`, and returns a dictionary keyed by timestamp
floats. It also stores the joined cleaned text under the string key
`_full_text`, plus `_language_code` and `_snippet_count`.

`_clean(text)` decodes HTML entities, strips annotation tags like `[Music]`
and `[Applause]` via regex, and collapses whitespace.

The snippet dictionary's mixed key types are load-bearing. Float keys are
timestamps with text content; string keys prefixed with underscore are
metadata. Consumers must check `isinstance(key, float)` before treating an
entry as a snippet.

### 3.4 nlp.py

Extracts vocabulary from transcript text using spaCy.

Models are loaded lazily, not at module import, and cached in `_nlp_models`,
a dict keyed by resolved model name rather than by language code. Loading
takes about a second, so importing at module level would slow every test
that imports `nlp.py`, even tests that never process text. Keying the cache
by model name rather than code means language codes that share a model,
such as `zh-CN` and `zh-TW` both resolving to the same Chinese pipeline,
only load it once.

`process_transcript(text, language="en")` resolves the model via
`language.get_spacy_model(language)`, passes the full text through it,
iterates the resulting tokens, calls `_is_valid_token()` on each, and builds
a dictionary keyed by `token.lemma_.lower()` with frequency counts as
values. Python dictionary insertion order is guaranteed since 3.7, which is
how first-appearance ordering is preserved.

`_is_valid_token(token)` is the filter. Current logic after this session's
fixes:

```python
if not _is_valid_lemma(token.lemma_):    return False
if token.pos_ not in ACCEPTED_POS:       return False
if token.pos_ == "PROPN":                return False
if token.ent_type_ in NAMED_ENTITY_TYPES: return False
return True
```

`ACCEPTED_POS` is `{"NOUN", "VERB", "ADJ", "ADV"}`. Prepositions,
conjunctions, articles, and pronouns are excluded as unlikely to be useful
vocabulary. Stop words are deliberately kept — beginners need basic words.

`_is_valid_lemma(lemma)` is a regex predicate permitting Unicode letter runs
joined by internal hyphens or apostrophes, requiring at least two characters,
rejecting digits and underscores:

```python
_VALID_LEMMA = re.compile(r"^[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*$", re.UNICODE)
```

Permits `semi-relevé`, `week-end`, `arc-en-ciel`, `aujourd'hui`.
Rejects `e`, `-`, `->`, `-là`, `qu'`, `3d`, `semi-`.

`token.is_alpha` was deliberately removed as a filter. See section 8.4.

`get_sorted_by_frequency(vocabulary)` and `get_unique_lemmas(vocabulary)` are
convenience helpers for consumers that need ranked or key-only views.

### 3.5 deck.py

Communicates with AnkiConnect and performs duplicate detection.

`_anki_request(action, **params)` builds the AnkiConnect JSON payload, posts to
`ANKI_HOST`, checks the response error field, and returns the result. Raises
`AnkiNotRunningError` on connection refusal or timeout, `AnkiConnectError` on
an error string in the response.

`get_deck_names()` calls the `deckNames` action and returns a sorted list.

`get_card_fronts(deck_name)` is a two-step operation: `findNotes` with a
`deck:"name"` query returns note IDs, then `notesInfo` with those IDs returns
field data. The `Front` field of each note is extracted, lowercased, and
returned. Two steps are necessary because AnkiConnect has no single action that
returns field data filtered by deck.

`_is_sentence_structured_deck(fronts, threshold=3.0)` computes the average word
count across all card fronts. Above the threshold the deck is classified as
sentence-structured. Such decks contain questions or example sentences as
fronts rather than single words, and fuzzy matching against them produces
meaningless results because any short lemma appears as a substring of some
unrelated sentence.

`_check_single(lemma, fronts, skip_fuzzy)` classifies one word:

1. Exact match against any front returns `SKIP` with score 100.
2. If `skip_fuzzy` is True (sentence-structured deck), return `NEW`.
3. If the lemma is shorter than `SHORT_WORD_THRESHOLD`, return `NEW` — fuzzy
   scoring on short tokens is unreliable.
4. Filter fronts shorter than `SHORT_WORD_THRESHOLD` out of the candidate pool
   for the same reason in reverse.
5. Run `rapidfuzz.process.extractOne` with `WRatio` and a score cutoff.
6. Apply two secondary filters: `token_sort_ratio >= 50` and
   `length_ratio >= 0.6` where length ratio is shorter divided by longer.
7. Score above `CONFIDENCE_HIGH` returns `SKIP`, otherwise `QUEUE`.

The three-condition filter exists because `WRatio` alone produced systematic
false positives on morphologically rich languages. See section 8.3.

`check_vocabulary(vocabulary, deck_name)` orchestrates the above across a whole
vocabulary dictionary, detects the deck structure once, and returns a
`DeckCheckResult` with `skip`, `queue`, and `new` lists plus an
`anki_available` flag. When AnkiConnect is unreachable it writes every word to
the SQLite backlog and returns early with `anki_available=False`.

`prompt_queue(queue)` presents each queued word in the terminal with its
closest deck match and confidence score, reading `y`, `n`, or `s`. `y` approves
the word for card creation. `n` defers it to `review.json`. `s` defers the
current word and all remaining words without further prompting.

`_write_review_file`, `load_review_decisions`, `_write_backlog`,
`get_backlog`, `clear_backlog`, and `process_backlog` handle the deferred-word
and offline-word persistence paths.

### 3.6 definition.py

Fetches definitions using a dual-source strategy.

The strategy: example sentences, synonyms, and antonyms always come from
`dictionaryapi.dev/{transcript_language}/`. The definition and part of speech
come from the target output language — Merriam-Webster first when the target is
English, `dictionaryapi.dev/{target_language}/` otherwise or as fallback.

Rationale: a French learner benefits from French example sentences even when
they want an English explanation of the word's meaning.

`fetch_definition(lemma, snippets, use_cache, language, def_language)` is the
main entry point. Sequence:

1. Compute cache key `f"{lemma}::{target_language}"` and check SQLite.
2. Fetch native-language data from `dictionaryapi.dev/{language}/{lemma}`.
3. If that returns nothing, retry with an accent-stripped lemma variant.
4. If `def_language` differs from `language`, translate the lemma via
   `translation.translate_word()`.
5. Fetch the definition from MW (if target is English) or dictionaryapi.dev.
6. Supplement empty synonym and antonym lists from WordNet, gated on
   `language == "en"` and using the original `lemma`, never the translated
   `query_lemma`.
7. Truncate the definition at the first sentence boundary.
8. Cap every text field at 256 characters at a sentence boundary.
9. Build and cache a `DefinitionResult`.

`fetch_definitions(lemmas, snippets, max_workers, language, def_language)`
batches the above. It deduplicates the lemma list case-insensitively while
preserving order, resolves cache hits sequentially, then fetches the
remaining lemmas concurrently through a `ThreadPoolExecutor` bounded by
`max_workers`. Results are reassembled in original first-appearance order
regardless of which thread finishes first. See section 8.8 for why a thread
pool was used instead of `asyncio`.

`_parse_mw_response` navigates Merriam-Webster's nested structure. The
definition is in the flat `shortdef` list. Examples are buried in
`def -> sseq -> sense -> dt -> vis` where `vis` means verbal illustrations.
Synonyms come from an optional `syns` field. All MW markup tokens (`{bc}`,
`{it}`, `{sx|word||}`) are stripped by `_strip_mw_markup`.

`_parse_dictapi_response` handles the flatter dictionaryapi.dev structure,
collecting up to two example sentences across all definitions in the first
meaning.

`_find_transcript_sentence(lemma, snippets)` searches the snippet dictionary
for the first sentence containing the lemma or an inflected form, using a
word-boundary regex.

The SQLite `definitions` table uses a composite `lemma::language` primary key
so the same word can be cached separately per definition language.

### 3.7 translation.py

Translates a lemma when `DEF_LANG` differs from `LANGUAGE`.

Three-tier resolution in `translate_word()`:

1. Community LibreTranslate mirrors — `translate.argosopentech.com`,
   `libretranslate.de`. Probed with a `/languages` GET before use.
2. Locally installed argostranslate model.
3. Interactive prompt offering download, continue-without, or exit.

`translate_local()` wraps the argostranslate call in a
`ThreadPoolExecutor` with a 15-second timeout. argostranslate on CPU loads
Stanza's sentence boundary detection model on first use and can take 30 to 60
seconds per word. The timeout prevents the pipeline appearing frozen.

`download_model(from_code, to_code)` fetches the argostranslate package index,
finds the `.argosmodel` URL for the language pair, and streams the download
with `requests(stream=True)` so a progress bar can be rendered from
`Content-Length` and received-byte counts. `install_from_path()` then installs
it permanently.

Module-level state `_warned_this_run`, `_warned_slow`, and `_user_choice` track
which warnings have been shown and what the user decided, so the prompt fires
once per run per language pair rather than once per word.
`reset_warning_state()` clears them and is called at pipeline start.

`ARGOS_PACKAGES_DIR` is read from the environment and set before any
argostranslate import, because argostranslate resolves its model directory at
import time. On WSL the default user cache path resolves differently between
sessions, causing repeated 150MB downloads.

### 3.8 cards.py

Builds the Anki package.

`_build_model()` returns a `genanki.Model` with `MODEL_ID`, ten named fields,
one card template, and the CSS. The template uses Anki conditional sections
(`{{#FieldName}}...{{/FieldName}}`) so empty fields render nothing rather than
empty headings.

The CSS uses Anki's own variables — `var(--fg)`, `var(--canvas)`,
`var(--border)`, `var(--slightly-grey-text)` — with hardcoded fallbacks. This
makes cards adapt automatically to the user's light or dark theme.

`_build_note(result, model, video_id, language)` constructs one note. The GUID
comes from `genanki.guid_for(result.lemma, video_id, language)`, which is
deterministic: re-running the pipeline on the same video in the same language
produces identical GUIDs and Anki silently skips duplicates on import.
`language` is part of the hash so that reprocessing the same video in a
different language does not collide on a lemma spelled the same in both. See
section 8.12.

`_build_fallback_note(lemma, example_transcript, model, video_id, language)`
builds a minimal note for words with no definition. The `Definition` field
contains the literal string "No definition found" and only the transcript
example is populated. Tagged `no-definition` for filtering in Anki. Same
language-aware GUID as `_build_note`.

`_truncate(text, max_chars=256)` cuts at the last `". "` before the limit if
that point is past the halfway mark, otherwise hard-cuts and appends an
ellipsis.

`build_package(video_id, deck_name, found, not_found, snippets)` iterates both
lists, maintains an `added_lemmas` set to prevent duplicates within a single
package, writes the `.apkg` with a timestamped filename, and returns a
`PackageResult` dataclass with `path`, `total_cards`, `standard_count`,
`fallback_count`, and `skipped_count`.

`skipped_count` counts words that had neither a definition nor a transcript
sentence — they produce no card at all.

### 3.9 state.py

Owns pipeline-level SQLite tables and the in-memory session.

Tables:

```sql
processed_videos (
    video_id     TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    deck_name    TEXT NOT NULL,
    card_count   INTEGER NOT NULL DEFAULT 0,
    word_count   INTEGER NOT NULL DEFAULT 0
)

generated_packages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    deck_name  TEXT NOT NULL,
    card_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)

vocabulary (
    lemma          TEXT    NOT NULL,
    video_id       TEXT    NOT NULL,
    frequency      INTEGER NOT NULL DEFAULT 1,
    position       INTEGER NOT NULL,
    part_of_speech TEXT,
    added_at       TEXT    NOT NULL,
    PRIMARY KEY (lemma, video_id)
)
```

The `vocabulary` composite key means the same word appearing in different
videos produces separate rows. This is the foundation for future data science
work — it records which video introduced which word, how often it appeared, and
where in the transcript it first occurred.

`state.py` deliberately does NOT own the `definitions` table (owned by
`definition.py`) or the `anki_backlog` table (owned by `deck.py`). Each module
manages its own persistence so a schema problem in one does not break the
others. See ADR-002.

`check_video_not_processed(video_id)` raises `VideoAlreadyProcessedError` if
the video is already in `processed_videos`. The CLI catches this, warns, and
exits without creating cards, unless `--force` is passed, in which case the
check is skipped entirely and the video is reprocessed. `mark_video_processed`
already upserts on `video_id`, so a forced rerun updates the existing record
rather than failing on a primary key conflict.

The `Session` class is an in-memory container for the selected deck name. It is
not persisted. When the process exits, the session ends.

### 3.10 __main__.py

CLI entry point using `argparse`.

Flags:

```
--video-id VIDEO_ID     YouTube video ID or URL
--deck DECK_NAME        Anki deck, supports :: sub-decks
--language LANG         BCP-47 subtitle language code
--def-lang LANG         BCP-47 definition output language
--review                process review.json decisions
--process-backlog       process SQLite backlog
--list-languages        print supported languages and exit
--verbose               DEBUG logging
```

`--review` and `--process-backlog` are mutually exclusive.

Three dispatch modes: `_run_pipeline`, `_run_review`, `_run_backlog`.

`_run_pipeline` sequence: resolve language, resolve definition language, reset
translation warning state, check video not already processed, fetch transcript,
run NLP, save vocabulary to SQLite, check deck, prompt for queued words, fetch
definitions, build package, log package, mark video processed, print summary,
offer Anki import.

`_prompt_import(apkg_path)` sends an `importPackage` request to AnkiConnect with
the absolute path. This fails on WSL because a Linux `/mnt/c/...` path is not
resolvable by Windows AnkiConnect. Known limitation, manual import is the
workaround.

Colour output helpers `_info`, `_ok`, `_warn`, `_err`, `_rule` use raw ANSI
codes.

---

## 4. Data flow

```
CLI args
  |
  v
language.resolve_language_code(flag, deck_name) -> "fr"
  |
  v
state.check_video_not_processed(video_id) -> raises or continues
  |
  v
transcript.get_transcript(video_id, ["fr"]) -> Transcript
transcript.get_snippets(transcript)         -> {0.0: {...}, "_full_text": "..."}
  |
  v
nlp.process_transcript(snippets["_full_text"]) -> {"lemma": count, ...}
  |
  v
state.save_vocabulary(video_id, vocabulary)
  |
  v
deck.check_vocabulary(vocabulary, deck_name) -> DeckCheckResult(skip, queue, new)
  |
  v
deck.prompt_queue(result.queue) -> (approved, deferred)
  |
  v
definition.fetch_definitions(new + approved, snippets, language, def_language)
  -> DefinitionBatchResult(found, not_found, from_cache)
  |
  v
cards.build_package(video_id, deck_name, found, not_found, snippets)
  -> PackageResult(path, total_cards, standard_count, fallback_count, skipped_count)
  |
  v
state.log_package(...)
state.mark_video_processed(...)
  |
  v
summary output + optional AnkiConnect import
```

---

## 5. Dependency graph

The module dependency graph is a directed acyclic graph. Arrows point
downward only.

```
                    __main__.py
                         |
     +---------+---------+---------+---------+
     |         |         |         |         |
transcript   nlp      deck    definition   cards    state
     |                             |
     |                        translation
     |                             |
     +---------+-------------------+
                    |
                 config.py
              (imports nothing)
```

`config.py` is a leaf — it imports nothing from the package, so it can never
create a cycle despite every module importing it.

`definition.py` imports `translation.py` lazily inside the function body to
avoid loading argostranslate and PyTorch at module import time.

No pipeline module imports another pipeline module except that one edge and
`transcript.py` importing `language.resolve_transcript` lazily.

This acyclic property is why `nlp.py` can be tested without a network, without
Anki, and without mocking three levels of dependency.

---

## 6. External services and APIs

### 6.1 youtube-transcript-api

Unofficial third-party wrapper around YouTube's internal timedtext endpoint. No
authentication. No published rate limit, but YouTube blocks IPs that make
repeated requests, returning HTTP 429 or connection timeouts.

Proxy support via `WebshareProxyConfig` (account-level username and password,
not per-IP credentials) or `GenericProxyConfig` (HTTP/HTTPS/SOCKS URLs).

`find_transcript(language_codes)` prefers manually created transcripts over
auto-generated ones natively. It uses exact dictionary key lookup, so partial
BCP-47 matching is implemented in `language.resolve_transcript`.

### 6.2 Merriam-Webster Collegiate API

`https://www.dictionaryapi.com/api/v3/references/collegiate/json/{word}?key=KEY`

Free tier: 1000 requests per day. Requires registration at
dictionaryapi.com. English only.

Returns a list. If the first element is a string, the word was not found and the
strings are spelling suggestions.

Response uses proprietary markup: `{bc}` is a bold colon, `{it}text{/it}` is
italics, `{sx|word||}` is a synonym cross-reference. All stripped before use.

### 6.3 dictionaryapi.dev

`https://api.dictionaryapi.dev/api/v2/entries/{language}/{word}`

No authentication. No published rate limit.

**Coverage is effectively English-only.** Verified during this session with
direct curl tests: `entries/fr/eau`, `entries/fr/chat`, and `entries/fr/maison`
all return 404 while `entries/en/water`, `entries/en/cat`, and
`entries/en/house` return 200. `entries/fr/bonjour` returned 502, which is a
different failure mode and remains uninvestigated.

This invalidates the premise of ADR-005. Tracked as issue #1.

### 6.4 AnkiConnect

`http://localhost:8765`, JSON POST, API version 6.

Requires Anki desktop running with the AnkiConnect add-on (code 2055492159)
installed and Anki restarted after installation.

Actions used: `version`, `deckNames`, `findNotes`, `notesInfo`,
`importPackage`.

WSL note: AnkiConnect binds to `127.0.0.1` by default, which WSL2 cannot reach
because it runs in a separate network namespace. Fix requires setting
`webBindAddress` to `0.0.0.0` in the AnkiConnect config and pointing
`ANKI_HOST` at the WSL gateway IP found via `ip route | grep default`. That IP
can change between WSL sessions.

### 6.5 argostranslate and LibreTranslate

Community mirrors: `translate.argosopentech.com`, `libretranslate.de`. No
authentication, no guaranteed uptime.

Local models downloaded from `argos-net.com`, roughly 100 to 200MB per language
pair. Installed permanently via `install_from_path`.

argostranslate pulls in PyTorch, adding roughly 1.5GB to the environment. This
is why it is an optional dependency group rather than a base dependency.

### 6.6 WordNet via NLTK

Local corpus, no network at runtime. Downloaded once via
`python -m nltk.downloader wordnet omw-1.4`.

English only. Used exclusively to supplement empty synonym and antonym lists
when the transcript language is English.

---

## 7. Libraries and why they exist

```
youtube-transcript-api   Transcript extraction. No browser, no API key.
spacy                    Tokenization, lemmatization, POS, NER.
rapidfuzz                WRatio and token_sort_ratio for duplicate detection.
requests                 HTTP client for MW, dictionaryapi.dev, AnkiConnect.
genanki                  .apkg generation without Anki running.
python-dotenv            .env file loading.
nltk                     WordNet synonym and antonym supplementation.
argostranslate           Optional. Local neural translation.
libretranslate           Optional. Local translation server.
pytest                   Test runner.
black, ruff, mypy        Formatting, linting, type checking.
```

---

## 8. Design patterns and decisions

### 8.1 Lazy loading

The spaCy model loads on first call, not at import. Same for the
`translation.py` import inside `definition.py`. Rationale: importing a module
should be cheap. Paying a one-second model load to import `nlp.py` in a test
that never processes text is waste multiplied by hundreds of tests.

### 8.2 Typed exceptions per module

Every module defines its own exception hierarchy rather than raising generic
exceptions. Library exceptions are caught at the module boundary and re-raised
as module exceptions with actionable messages. Callers catch the specific types
they can handle.

### 8.3 Three-condition fuzzy matching

`WRatio` alone produced systematic false positives. `commencer` scored 90
against `comme` because WRatio's partial-ratio component finds substring
overlaps regardless of semantic relationship. `puis` scored 90 against
`puissiez`. `attend` scored 90 against `n'attendions pas`.

The fix requires three conditions to pass simultaneously: `WRatio` above the
confidence threshold, `token_sort_ratio >= 50`, and `length_ratio >= 0.6`.
Validated against nine real French pairs from production output: all false
positives eliminated, all legitimate morphological pairs preserved.

### 8.4 Validate the lemma, not the surface form

The vocabulary dictionary is keyed by `token.lemma_.lower()`. Every filter must
therefore inspect the lemma.

This was violated twice. First with `len(token.text) < 2`, which allowed
three-character French conjugations that lemmatize to a single character,
producing a card with the front "E". Second with `token.is_alpha`, which
returns False for any token containing a hyphen or apostrophe and therefore
excluded legitimate compounds like `semi-relevé` and `week-end`.

Both are fixed. `_is_valid_lemma()` is now the sole gate and `token.is_alpha`
has been removed entirely.

The reasoning for removing `token.is_alpha` rather than keeping it as a
defensive layer: the two checks ask the same question about two different
strings, and only one of those strings is used downstream. Keeping the
surface-form check adds no protection `_is_valid_lemma` does not already
provide, while blocking the exact category the regex was written to admit.

### 8.5 Dual-source definitions

Examples, synonyms, and antonyms from the native transcript language.
Definition and class from the target output language. Rationale in ADR-005.

The premise that dictionaryapi.dev provides usable non-English coverage turned
out to be false. See section 9.1.

### 8.6 Deduplication at two layers

`fetch_definitions` deduplicates the lemma list before any API call.
`build_package` maintains an `added_lemmas` set. Defence in depth — a duplicate
that slips past the first layer cannot produce two cards.

### 8.7 Composite cache keys

The `definitions` table uses `lemma::language` as the primary key so the same
word can be cached separately for each definition output language. A bug where
the batch loop used the bare lemma while `fetch_definition` used the composite
key caused every cache lookup to miss, which manifested as repeated cards for
the same word.

### 8.8 Bounded thread pool for definition fetching, not asyncio

`fetch_definitions` used to call `fetch_definition` one lemma at a time, with
a fixed delay between calls. Two ways to make this concurrent were on the
table: rewrite the definition and translation code on `asyncio` and `aiohttp`,
or keep the existing synchronous `requests` calls and dispatch them across a
bounded thread pool.

The pool was chosen. Every downstream piece this touches, MW response
parsing, dictionaryapi response parsing, the circuit breaker, the WordNet
lookup, and the translation module's interactive terminal prompt, is
synchronous code written and tested as synchronous code. An `asyncio` rewrite
would need an async HTTP client and either an async rewrite of all of that
code or `run_in_executor` calls wrapping the synchronous version anyway, which
is most of the work of a thread pool with none of its benefit. A
`ThreadPoolExecutor` gets the same overlapping I/O with a much smaller change
to the existing call graph.

`DEFINITION_FETCH_WORKERS` (default 5) bounds how many lookups run at once,
which keeps a burst of new vocabulary from opening dozens of simultaneous
connections to a single dictionary API. Cache hits are still resolved
sequentially before the pool starts, since a local SQLite read has nothing to
gain from a worker thread. Results are collected into a dict keyed by lemma
and reassembled into the batch in original first-appearance order afterward,
since completion order across threads is not the same as input order and the
rest of the pipeline assumes deterministic, first-appearance ordering.

Real OS threads, unlike `asyncio` tasks on a single event loop, can genuinely
run at the same instant on different cores, so anything they share needs an
actual lock rather than relying on cooperative scheduling. Two places needed
one: the circuit breaker's failure counters in `definition.py`, which do a
read-modify-write on a shared dict, and the translation module's Tier 3
interactive prompt, which reads and writes a shared per-pair choice cache and
calls `input()`. Both are now wrapped in a `threading.Lock`.

A live comparison against 15 uncached English words went from 34.6 seconds
at `max_workers=1` to 5.3 seconds at the default of 5, a 6.5x speedup, with
the same 15 of 15 words found in both runs.

### 8.9 Language-aware spaCy model selection

Until v0.4.3, `config.SPACY_MODEL` was a single static value, default
`en_core_web_sm`, and `nlp.py` loaded it regardless of the resolved
transcript language. Every French, Spanish, German, and other non-English
video ever processed by this pipeline had been tokenized, lemmatized,
POS-tagged, and NER-tagged with an English statistical model.

Evidence in production output: `toujours` lemmatized to `toujour`, `allons`
to `allon`, `venons` to `venon`, `reprenons` to `reprenon`. All are the
English plural-stripping rule applied to French verb conjugations. Earlier
in development these were misattributed to YouTube auto-caption quality.
Wrong lemmas meant wrong dictionary lookups, so some fraction of "no
definition found" results were lookups for words that did not exist. The
proper-noun filter was also unvalidated for non-English, since `token.pos_`
and `token.ent_type_` came from an English model reading foreign text.

Fixed in three commits: `SPACY_MODELS` added to `language.py` with
`get_spacy_model()` resolving codes to model names, `nlp.py`'s single
`_nlp_model` global replaced with the `_nlp_models` per-model-name cache
described in section 3.4, and `__main__.py` wired to pass the resolved
language through to `process_transcript()`. Closes #3.

### 8.10 Circuit breaker for failing definition sources

Failed API lookups used to cost nearly as much as successful ones. A run
with 108 failing lookups exceeded 280 seconds, roughly 2.6 seconds per
failure, paying a full network timeout every time even though the source
had already shown it was down.

After `CIRCUIT_BREAKER_THRESHOLD` (default 5) consecutive failures against
one source, `definition.py` stops calling it for the rest of the run and
goes straight to the fallback. Only server errors, timeouts, and connection
failures count as a failure. A 404 does not, since it means the source is
reachable and simply lacks that word, which is a healthy outcome, not a
failure, and would otherwise trip the breaker on any source with genuinely
sparse coverage for a language after only a few words. See issue #1's
404-versus-502 investigation for the evidence behind that distinction.
Closes #4.

### 8.11 WSL path translation for Anki auto-import

`_prompt_import` builds the `.apkg` path with `Path.resolve()`, which under
WSL produces a Linux-style path like `/mnt/c/Users/.../output/file.apkg`.
AnkiConnect on the Windows side of a WSL setup cannot resolve that path,
since it runs as a native Windows process with no knowledge of the WSL
mount namespace.

`_is_wsl()` checks `/proc/version` for the string "microsoft".
`_translate_wsl_path()` converts `/mnt/<drive>/rest/of/path` to
`<DRIVE>:\rest\of\path` when that check passes, and leaves the path
untouched on native Linux or macOS. Verified live: an actual AnkiConnect
import through this translation succeeded where the untranslated path
failed. Closes #5.

### 8.12 Language folded into the card GUID

`_build_note` and `_build_fallback_note` used to compute each note's GUID as
`genanki.guid_for(lemma, video_id)`. That is stable across repeat runs of
the same video, which is the intended behavior, but it has no way to tell
apart two runs of the same video in different languages. French and English
share a number of cognates with identical spelling: `train`, `solution`,
`simple`, `machine`, `sandwich`, `page`, `change`. Reprocessing a video in a
second language produced the exact same GUID for any of those lemmas as the
first language's run, and Anki's own duplicate-note detection then treated
the second run's card as already existing and dropped it, silently, with no
error or warning anywhere in the pipeline.

Found while live-verifying the `--force` flag: an English rerun of a video
previously processed in French came up 8 cards short of its 130-word
vocabulary list. Both decks looked correct in isolation; the missing cards
only showed up by comparing note timestamps and word lists between the two
decks directly. Fixed by adding the resolved language to the hash input,
`genanki.guid_for(lemma, video_id, language)`. This does not change the
GUID of any note already in a user's collection, since within a single
language nothing about the hash changed; it only prevents new collisions
between different languages of the same video going forward. Closes #14.

### 8.13 Wiktionary as a non-English example-sentence source

Issue #1 established that dictionaryapi.dev has essentially no non-English
coverage: 0 successes across 18 common French words in direct testing.
Wiktionary's REST definition endpoint looked like an obvious fix, but its
own per-language editions turned out not to work: `fr.wiktionary.org`,
`de.wiktionary.org`, and `es.wiktionary.org` all returned `501 Internal
error` for the same endpoint that works fine on `en.wiktionary.org`.

Querying the English edition for a foreign word works anyway. An English
Wiktionary page carries every language that word appears in as its own
section in the response, keyed by language code, so `en.wiktionary.org`'s
entry for `chat` includes a real `fr` section with genuine French example
sentences, even though the site itself is the English edition. The
`definition` text in that section is an English gloss of the word (`chat`
-> `cat`), not a French definition, so `_parse_wiktionary_examples` reads
only the `examples` array and discards the rest; CLAUDE.md 3.3 does not
allow an English gloss into a native-mode Definition field.

This closes only the example-sentence half of issue #1. Definitions, part
of speech, synonyms, and antonyms for non-English languages are unaffected
since this endpoint provides none of them. A first implementation wired
Wiktionary into `fetch_definition()`'s existing "definition found but no
example" branch and looked correct in isolation and under mocked tests, but
a live run against issue #1's original French video showed zero change: 0
found / 209 not found before and after, because `fetch_definition()`
returns `None` before that branch is ever reached when there is no
definition at all, which is the common case for a near-zero-coverage
language, not the rare one. `_fetch_definition_or_fallback_example()` is
the actual fix: it wraps `fetch_definition()` per lemma in the thread pool,
and when there is no definition anywhere, tries Wiktionary once more and
stores any example in `DefinitionBatchResult.not_found_examples`, keyed by
lemma, for the resulting fallback card to use.

Wikimedia enforces anonymous rate limits on this endpoint -- a burst of
about a dozen rapid manual requests during testing hit a `429` -- so a 429
counts as a circuit-breaker failure, distinct from a 404 which means the
word genuinely has no entry. In the live verification run, at 5 concurrent
workers with MW and dictionaryapi.dev calls interleaved between Wiktionary
ones, no 429 was ever hit; the pipeline's natural pacing turned out to stay
under Wikimedia's limit without any extra throttling.

Verified against the same French video used in issue #1's original report:
words dropped for having no definition, no dictionary example, and no
transcript match went from 30 to 9, and 111 of the resulting 200 fallback
cards carry a real French example sentence that was previously blank.
Closes the example-sentence portion of #1.

---

## 9. Known architectural gaps

### 9.1 dictionaryapi.dev has no meaningful non-English coverage

Documented in GitHub issue #1 with curl evidence. The dual-source architecture
in ADR-005 is correct as designed but produces essentially no definitions for
non-English languages, since dictionaryapi.dev's coverage for them is
near-zero. Section 8.13 closes the example-sentence half of this via
Wiktionary; definitions, part of speech, synonyms, and antonyms are
unaffected by that fix and remain this gap's open remainder.

A verification run against a French video produced 209 words with 0
definitions found either before or after the Wiktionary fix (fetch_definition
still requires a real definition to return a "found" result), but fallback
cards improved from 0 to 111 (of 200) carrying a real dictionary example
instead of an empty field, and dropped words (nothing to show at all) fell
from 30 to 9.

Fixing definitions, synonyms, and antonyms requires per-language dictionary
sources (Larousse for French, DWDS for German, RAE for Spanish, or similar).
Needs its own ADR given the per-language maintenance burden.

---

## 10. Test architecture

524 unit tests across nine test files, 22 more marked integration and
deselected by default. All run without network, Anki, or installed models.
Integration tests use `@pytest.mark.integration` and are excluded by the
default `addopts` in `pyproject.toml`.

Mocking strategy: `unittest.mock.patch` and `MagicMock` with pytest as the
runner. `unittest.mock` is used because there is no pytest-native equivalent —
`pytest-mock` is a thin wrapper around it.

Fixture pattern for spaCy tokens:

```python
def _make_token(text, lemma, pos, is_alpha=True, is_stop=False):
    t = MagicMock()
    t.text = text
    t.lemma_ = lemma
    t.pos_ = pos
    t.is_alpha = is_alpha
    t.is_stop = is_stop
    return t
```

Early tests called this with identical `text` and `lemma` values, which made
the surface-form-versus-lemma bug impossible to express in a test. Any test
touching that distinction must pass different values.

`tmp_path` and `monkeypatch` fixtures redirect `DB_PATH`, `OUTPUT_DIR`, and
`REVIEW_FILE` so no test writes to the real filesystem.
