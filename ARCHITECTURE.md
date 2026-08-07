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
│       ├── wiktdata.py
│       └── state.py
├── tests/
│   ├── test_transcript.py
│   ├── test_language.py
│   ├── test_nlp.py
│   ├── test_deck.py
│   ├── test_definition.py
│   ├── test_translation.py
│   ├── test_cards.py
│   ├── test_wiktdata.py
│   ├── test_state.py
│   └── test_main.py
├── output/                         generated .apkg files, gitignored
├── dictionaries/                   offline Wiktionary indexes, gitignored
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
DICT_DIR                  offline Wiktionary indexes, default "dictionaries"
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
`fr_core_news_md` (French is the one language pinned to the medium model
rather than small, see section 8.14). `get_spacy_model(language_code)`
resolves a code against it: exact match first, then `_SPACY_CODE_ALIASES`
for known BCP-47/spaCy naming mismatches (Norwegian's macrolanguage code
`no` needs the Bokmål model `nb`), then the base code for regional variants
like `zh-CN`. No match raises `SpacyModelUnavailableError` naming the
languages that are supported. Closes #3; before this, every video
regardless of language was tokenized with the English model, silently
corrupting non-English lemmatization. See section 3.4 for how `nlp.py`
caches the loaded models this returns.

`SPACY_MODEL_SIZE_OVERRIDE`, an optional environment variable, replaces the
resolved model's size suffix for every language uniformly (e.g. `md` turns
whatever `SPACY_MODELS` returns into its medium-model equivalent). Unset by
default, so nobody's download or processing time changes without opting
in. Exists as a general escape hatch for the same class of problem French
hit in section 8.14, without us having to verify a size upgrade for all 24
languages ourselves before anyone can try one.

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
a dictionary keyed by `_effective_lemma(token).lower()` with frequency
counts as values. Python dictionary insertion order is guaranteed since
3.7, which is how first-appearance ordering is preserved.

`_effective_lemma(token)` returns `token.lemma_ or token.text`. Not every
spaCy pipeline populates `lemma_` -- `zh_core_web_sm` leaves it empty for
every token, since Chinese has no inflectional morphology to normalize
away. Without this fallback, Chinese vocabulary extraction silently
returns nothing. See section 8.17.

`_is_valid_token(token)` is the filter:

```python
if not _is_valid_lemma(_effective_lemma(token)): return False
if token.pos_ not in ACCEPTED_POS:                return False
if token.pos_ == "PROPN":                         return False
if token.ent_type_ in NAMED_ENTITY_TYPES:          return False
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

The two-character minimum is exempted for a single character in the CJK
Unified Ideographs, Hiragana, Katakana, or Hangul syllable Unicode ranges
(`_is_single_cjk_character`), so real one-character words like 人, 大, 水
pass while a single Latin letter like `e` still does not. See section 8.17
for why the exemption is script-specific rather than a blanket length
change.

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
--setup                 guided .env setup for an optional MW API key, then exit
--force                 reprocess a video even if already marked processed
--verbose               DEBUG logging
```

`--review` and `--process-backlog` are mutually exclusive. `--list-languages`
and `--setup` are standalone informational/setup modes: both exit before the
`--video-id` requirement check, since neither processes a video (see 8.15 for
a real ordering bug this used to have).

Three dispatch modes: `_run_pipeline`, `_run_review`, `_run_backlog`.

`_run_pipeline` sequence: resolve language, resolve definition language, reset
translation warning state, check video not already processed (skipped
entirely when `--force` is set, see TASKS.md's `--force` entry for why that's
safe -- `mark_video_processed` upserts on `video_id`), fetch transcript, run
NLP, save vocabulary to SQLite, check deck, prompt for queued words, fetch
definitions, build package, log package, mark video processed, print summary,
offer Anki import.

`_prompt_import(apkg_path)` sends an `importPackage` request to AnkiConnect
with the absolute path. Under WSL this path is translated from `/mnt/c/...`
to `C:\...` first, since Windows-side AnkiConnect cannot resolve a Linux
path directly -- see 8.11 for the fix and live verification. Native Linux
and macOS paths pass through untouched.

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
`python -m nltk.downloader wordnet omw-2.0` (not `omw-1.4` -- see 8.18).

Used to supplement empty synonym lists for English plus 18 other languages
via Open Multilingual Wordnet (see 8.18). Antonyms remain English-only;
OMW does not carry reliable antonym relations for non-English synsets.

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

### 8.14 Per-language spaCy model size, verified rather than assumed

Issue #13 traced the "Sortir"/"Sors" duplicate-card bug to
`fr_core_news_sm`'s POS tagger: it inconsistently misclassified common
conjugated verb forms as NOUN or ADV instead of VERB depending on sentence
context, and when the POS is wrong the lemmatizer never attempts verb
normalization, so the surface form becomes its own lemma instead of
collapsing to the infinitive.

Direct comparison against `fr_core_news_md` on the issue's 3 real
reproduction sentences: `md` got all 3 right, consistently; `sm` got 2 of 3
wrong. `SPACY_MODELS["fr"]` now points at the medium model. Live-verified:
reprocessing the same video, "Sortir" now appears once and "Sors" does not
appear at all.

This is not assumed to generalize to the other 23 supported languages, and
a parallel test showed it does not automatically: Spanish's analogous
"juego" (play, a verb/noun homograph like "sors") went from misclassified
NOUN in `sm` to misclassified PROPN in `md`, a different wrong answer, not
a fix. Model size versus lemmatization accuracy is a per-language, per-
model training-data question. `SPACY_MODEL_SIZE_OVERRIDE` (an env var
checked in `get_spacy_model()`) exists so anyone hitting a similar problem
in another language can test a larger model for themselves without a code
change, rather than us guessing which of the other 23 languages would
actually benefit from a default we have not verified.

A separate, smaller bug survives model size entirely: "joue" (play,
present tense) never normalizes to "jouer" in either `sm` or `md`, despite
spaCy correctly tagging it VERB with complete
`Mood=Ind|Tense=Pres|VerbForm=Fin` morphology. The model has everything it
needs and still gets the lemma wrong, which points at a gap in the
lemmatizer's own lookup table for that specific word form, not a POS or
model-size problem. Spanish shows the same category of bug independently
("cocino" stays "cocino" instead of "cocinar" in both sizes). Neither a
bigger model nor more of our own code fixes this; it is the underlying
spaCy language pipeline's own lemmatizer data. Issue #13 stays open for
this half.

### 8.15 Guided setup instead of a hand-edited .env

Issue #9 asked for a non-technical onboarding path for the one genuinely
optional credential, MW_API_KEY. Investigating it surfaced that the
onboarding step it was worried about was already broken for an unrelated
reason: README's `cp .env.example .env` had nothing to copy.
`.env.example` never existed in this repository, despite `.gitignore`
carrying an explicit `!.env.example` rule to keep it trackable. This had
been broken since the very first commit.

Created a real `.env.example`, built from grepping every actual
`os.getenv()` call across `config.py`, `language.py`, and `translation.py`
rather than copying an existing (and, it turned out, partly stale) local
`.env`. The wiki's own config page still described `SPACY_MODEL` as a
single global setting with `en_core_web_sm`/`_md`/`_lg` options, a value
that stopped existing the moment #3 replaced it with the per-language
`SPACY_MODELS` mapping in `language.py`; nobody had gone back to update
the doc once the underlying config shape changed.

The question of whether MW_API_KEY is actually required doesn't need a
judgment call: `_fetch_from_mw` returns `None` immediately when the key is
unset, and dictionaryapi.dev is the always-on fallback, so nothing in the
pipeline has ever required any API key to run. README's own table said
"Required: Yes" for it. A separate, real error in the same table: it
listed `LANGUAGE` and `DEF_LANG` as `.env` variables, but they are
`make run` command arguments consumed before `.env` is ever loaded by the
Python process, so setting either in `.env` silently does nothing.

`--setup` (`make setup`) is the actual guided wizard: creates `.env` from
the template if missing, detects an already-set key and skips the prompt
entirely, otherwise explains the key is optional, links registration, and
validates the pasted value before writing it with `python-dotenv`'s
`set_key` (already a dependency, so no new one added) rather than
hand-rolled text parsing.

Placing the new flag correctly surfaced two more, unrelated real bugs in
`main()`'s dispatch: `--list-languages` (and now `--setup`) were
unreachable, because the `--video-id` requirement check ran before either
of them and exited first for every standalone mode, not just the default
one; and `make help` crashed partway through with a shell syntax error
(two `@printf` lines concatenated onto one Makefile line with a raw tab
instead of a newline, plus a stray trailing quote on the next line) that
nobody had noticed because it only crashes once printing reaches that
specific line. Both fixed. All three wizard paths (decline, accept and
write, already-set detection) verified live against real `.env` files
before being called done, not just via mocked tests.

### 8.16 SQLite cache resilience under real thread concurrency

`fetch_definitions()`'s thread pool (section 8.8) means several lemmas can
read and write the definition cache at the same instant, each through its
own `_get_db()` connection. Found live, during a multi-language testing
pass, not in a synthetic test: a concurrent English run hit
`sqlite3.OperationalError: database is locked` inside `_cache_set_key`,
called as the last step of `fetch_definition()` right before `return
result`. Since that call could raise, the exception propagated out of an
otherwise-successful lookup, and `fetch_definitions()`'s executor loop
caught it and recorded the word as not-found -- a definition that had
already been fetched successfully, discarded by a caching side effect.

Root cause was `_get_db()` running full schema setup (`CREATE TABLE IF NOT
EXISTS`, an `ALTER TABLE` migration attempt) on every single call, not
once. DDL statements take a stronger lock than a plain read or write, so
every one of several concurrent connections was contending for a heavier
lock than the actual cache traffic needed. Fixed three ways: schema setup
now runs once per `DB_PATH` (guarded by a lock, keyed by path so tests
using a fresh tmp database per test still initialize correctly),
connections request a 30 second busy timeout instead of sqlite3's 5
second default, and `_cache_get`/`_cache_set_key` both fail soft on a
`sqlite3.Error`, logging and continuing rather than propagating. Caching
is an optimization; a lock timeout must never turn an already-successful
lookup into a lost one.

### 8.17 Chinese vocabulary extraction: the lemma fallback and CJK exemption

Found in the same multi-language testing pass: a real Chinese video with
154 confirmed transcript snippets produced zero unique lemmas. Not
degraded output, total silent failure, no error raised anywhere to say
so. Confirmed directly against `zh_core_web_sm`: it leaves `token.lemma_`
empty for every single token, since Chinese has no inflectional
morphology for a lemmatizer to normalize away. `_is_valid_token` and the
vocabulary loop both keyed off `token.lemma_` directly, so
`_is_valid_lemma("")` failed its length check for every token in the
transcript.

Added `_effective_lemma(token)`, returning `token.lemma_ or token.text`,
used consistently everywhere a lemma is read so the fallback can't drift
out of sync between the validity check and the dict key.

A second, related gap surfaced in the same investigation:
`_is_valid_lemma`'s two-character minimum (added to reject Indo-European
lemmatization debris, a botched French lemma collapsing to a single
letter like "e", see 8.4) isn't a universal rule. Chinese has many
legitimate one-character words -- 人 (person), 大 (big), 水 (water), 好
(good), 不 (not) -- that the same length check was rejecting even before
the empty-lemma issue is considered. Added `_is_single_cjk_character`,
exempting a single character from the length minimum only when it falls
in the CJK Unified Ideographs, Hiragana, Katakana, or Hangul syllable
Unicode ranges. A single Latin letter is unaffected and still rejected.

Live-verified: the same video went from 0 to 552 unique lemmas after the
fix, including 67 manually-confirmed real single-character words. Closes
#15.

### 8.18 Open Multilingual Wordnet synonyms for non-English languages

ADR-008 evaluated real alternatives to dictionaryapi.dev for non-English
definitions/synonyms/antonyms (see 9.1). OMW's `lang=` parameter gives
real, genuine native-language synonym words for 18 languages beyond
English -- confirmed directly, not from vendor claims -- via
`_OMW_LANGUAGE_CODES` in `definition.py`. It does not give real
native-language definitions: `.definition()`/`.examples()` on every
matched synset stay in English regardless of `lang=`, since OMW only
translates the lemma-to-synset mapping layer, not the synset's own text.
Antonyms are likewise not extended past English -- `lemma.antonyms()`
returns empty for non-English lemmas in every case tested.

Requires the `omw-2.0` NLTK package, not `omw-1.4`: this NLTK version's
`wn._omw_reader` looks for `omw-2.0` specifically, so `omw-1.4` downloads
successfully but its data is silently never used. Both discovered while
building this and CLAUDE.md's setup instructions have been corrected
accordingly.

Same class of bug as 8.13's Wiktionary example fix, found the same way:
the first implementation only called the OMW lookup from inside
`fetch_definition()`'s found-definition branch, near the very end of the
function. Since dictionaryapi.dev has ~0% definition coverage for most
non-English languages (9.1), that branch almost never executes, so a real
pipeline run showed zero synonyms even though the unit tests, which call
the function directly, all passed. Fixed the same way as 8.13: threaded
the same OMW lookup into `_fetch_definition_or_fallback_example()`, and
added `DefinitionBatchResult.not_found_synonyms`/`not_found_antonyms`
mappings alongside the existing `not_found_examples` one, consumed by
`build_package()`/`_build_fallback_note()` the same way.

Live-verified against the French video from 9.1: fallback cards with real
French synonyms went from 0 to 767 of 972.

Two further defects surfaced only by reading that run's actual card
output, both silent, neither visible to a unit test that called the
function once on one word:

**Ranking.** Pooled synonyms were sorted alphabetically before the
five-item cap, so spelling decided which survived rather than relevance.
WordNet returns senses most-common-first, so the alphabetical cut
systematically discarded the useful words: 207 of 972 cards were
affected, "aujourd'hui" losing "maintenant" while keeping "de notre
temps", and "faire" losing "mettre"/"organiser" while keeping the vulgar
"caguer"/"déféquer". Synset order is now preserved through the cap.
The ordering is load-bearing, not cosmetic -- the cap discards whatever
does not fit, so ordering by anything but relevance throws away the best
results.

**Thread safety.** nltk's WordNet reader seeks and reads a shared file
handle, and loads each language's data lazily on first use. Neither is
safe under `DEFINITION_FETCH_WORKERS` threads: concurrent lookups raised
`AssertionError`, which the function's own `except` swallowed into "no
synonyms". The only symptom was a different subset of cards missing
synonyms on every run of the same video (767/764/730 across three).
Warm-up now happens under the lock, per language, and the reads
themselves are serialized too -- warming alone was necessary but not
sufficient. Serializing is free here: these are local, already-warm
lookups that measured ~6x faster serialized than contended (11ms vs 68ms
per 80 lookups), and the thread pool exists for network I/O, which this
is not. Synonym coverage is now 89% (107/120) and stable run to run,
against a varying ~78% before.

**Sense mixing, and why English is treated differently.** A *synset* is
WordNet's unit of meaning: one sense of a word, together with every word
sharing that sense. A word with several meanings belongs to several
synsets, so the number consulted decides how many distinct meanings get
pooled onto one card. "prêt" has three: ready (adjective), loan (noun),
quick (adjective).

Consulting all three merged unrelated meanings and crossed parts of
speech, putting "emprunt" (a loan, noun) beside "rapide" (quick,
adjective) on the same adjective's card. Non-English now reads the top
two. The cost is real and was measured across 933 French cards before
choosing:

| synsets consulted | cards with any synonym | avg synonyms per card |
|---|---|---|
| 1 | 595/933 (64%) | 2.51 |
| 2 | 701/933 (75%) | 3.31 |
| 3 | 733/933 (79%) | 3.69 |

Two is the shipped setting and is explicitly provisional. It bounds the
problem without solving it: "prêt" still returns "emprunt", a noun on an
adjective's card, because that is its second sense. What two does buy is
excluding third-sense noise ("second" -> "2d"/"2e") while holding 75%
coverage, four points off the widest setting. One sense is genuinely
coherent but drops 138 more cards to no synonyms at all, and its top
sense is not reliably the useful one -- "petit" reduces to "guère"
("hardly"), an adverb sense, because WordNet orders senses by English
frequency rather than by usefulness in the target language.

Expected to be revisited once a real per-language dictionary source
lands (issue #16). The limit is one line in `_wordnet_synonyms_antonyms`,
and a test pins it from both sides so changing it fails loudly rather
than silently altering card content.

English keeps three. The sense mixing was diagnosed on OMW's non-English
data; English reads Princeton WordNet directly and was never the
complaint. Narrowing it too measured a straight regression, 14/15 to
9/15 words with any synonym at all, because many common English words
("happy", "large", "quick", "house", "think") have a top synset
containing only the word itself.

**What this does not fix.** OMW carries genuinely wrong entries that no
amount of synset selection repairs, because they sit in the top synset:
"fat" -> "file allocation table", "second" -> "2d", "mec" -> "Guy". OMW
also tags some plainly English words as French ("aller" -> "go", with
`lang=fra` in OMW's own data), which is a standing risk to the
transcript-language constraint in CLAUDE.md 3.3. Left as-is for now; the
fallback if it becomes intolerable is to stop treating OMW as a
native-language source for the affected languages.

### 8.19 Offline Wiktionary index as the non-English definition source

The gap in 9.1 -- no non-English definitions from any online source --
is closed by a local index built from bulk Wiktionary data, in
`wiktdata.py`.

Source is wiktextract output published per language by kaikki.org, built
from *that language's own* Wiktionary edition. That distinction is the
whole point: the Wiktionary REST endpoint already used for example
sentences (8.13) returns an English gloss of the foreign word, which is
why it never fixed definitions. The per-language extracts carry real
native-language glosses.

**Why bulk data rather than the live API.** ADR-008 evaluated querying
Wiktionary's raw wikitext API per word. It works, but Wikimedia
rate-limits anonymous requests to roughly 8-10 before a 429 regardless of
pacing, and one video needs 100-1000+ lookups. Downloading once sidesteps
the limit rather than circumventing it, needs no proxies, and makes
lookups offline and instant. It also skips the two hardest parts of
parsing raw wikitext -- recursive template stripping, and detecting
extractions that silently returned template syntax like `{{семантика|`
as though they had succeeded -- because wiktextract resolved those
upstream.

**Layered with OMW, not replacing it.** Measured against a real 958-lemma
French deck, the index covers synonyms at 49% against OMW's 76%. Reading
that number the other way round would have traded better data for worse,
so OMW keeps first claim on synonyms and the index fills in behind it.
The index does supply antonyms, which OMW cannot for any non-English
language at all (8.18).

Live-verified on the French test video:

| field | before | after |
|---|---|---|
| definition | 0% | 95% |
| class / part of speech | 0% | 95% |
| 1st example | 41% | 93% |
| 2nd example | 27% | 71% |
| synonyms | 76% | 83% |
| antonyms | 0% | 20% |

Cards rose 958 -> 1036 and words dropped for having nothing to show fell
83 -> 5. The ~5% that still miss are transcription noise and proper nouns
(`bermiduamedo`, `ashkabat`), so this is near the practical ceiling for
the transcript rather than a number with easy headroom.

**Verified beyond French.** Measured against real deck vocabulary from
previously generated videos, not sampled words:

| language | lemmas | definitions | examples | synonyms | antonyms |
|---|---|---|---|---|---|
| French | 958 | 95% | 92% | 49% (OMW 76% wins) | 20% |
| German | 384 | 91% | 84% | **60%** | **51%** |
| Russian | 420 | 420 -> 91% | 72% | **72%** | **46%** |
| English | 274 | 100% | 91% | 43% | 31% |

German and Russian matter most here: OMW covers neither (8.18), so before
this they had no synonyms at all. Both also carry far richer antonym data
than French -- 51% and 46% against French's 20%. Any claim that antonym
coverage is "at the data ceiling" is French-specific and does not
generalise.

**English is the exception and should not be built.** A live English run
produced 273 of 274 definitions from Merriam-Webster and consulted the
index zero times. The single MW miss was "Momentic", a brand name the
index does not have either, so the index contributed nothing on real
vocabulary while costing a 475 MB download and 194 MB on disk. Its
first-sense definitions are also frequently archaic, because English
Wiktionary orders senses historically rather than by frequency: "may" ->
"To be strong; to have power (over)", "bill" -> "A written list or
inventory. (Now obsolete...)", "water" -> "A hamlet in Manaton parish,
Devon". The existing ordering already prevents harm -- the index is only
consulted when no definition was found -- but there is no reason to build
it.

**Operational notes.** One SQLite file per language under `DICT_DIR`,
gitignored, built by `make dictionary LANGUAGE=<code>`. French is 676 MB
compressed to download and 309 MB indexed, from 7.4M source lines of
which 2.1M are French; German 287 MB -> 152 MB, Russian 276 MB -> 111 MB,
English 475 MB -> 194 MB -- the per-language extract documents *other*
languages in that language, so the `lang_code` filter discards roughly
70% of the file. Reads use one connection per thread, since the pool in
`fetch_definitions()` would otherwise share a single sqlite3 connection
across threads.

Entirely optional. With no index built, behaviour is exactly what it was
before. That property is load-bearing enough to be pinned by a test
fixture: the index lives on disk, so without one tests would pass in CI
and fail on any machine where a developer had run the build.

Lookup normalises apostrophes in both directions. Wiktionary stores
French elisions with a typographic apostrophe, so `aujourd'hui` typed
with an ASCII quote silently missed an entry that was definitely present
-- on one of the most common words in the language.

### 8.20 Transcript examples are found by surface form, not lemma

The "Example from Youtube Video" field searched the transcript for the
lemma. Transcripts contain inflected forms, so for any language with real
morphology the search frequently looked for a string that is not in the
text: a French video says "sais", the lemma is "savoir", and "savoir"
appears nowhere. The word was extracted *from* the transcript and then
failed to find the sentence it came from -- 137 of 1036 cards on a real
run, all of them infinitives.

8.19's lemma fix made this marginally worse, since correcting "joue" to
"jouer" removed a coincidental match the uncorrected form had.

`process_transcript()` now optionally records, per lemma, the forms it
actually took. It is an out-parameter rather than a second return value
so existing callers and their tests are unaffected.
`_find_in_snippets()` tries the lemma first -- where it does appear it is
the canonical form and gives the cleanest sentence -- then those forms.
Standard cards are backfilled too, not only fallback ones, since
`fetch_definitions()` performs its own lemma-only search earlier.

Live-verified: coverage 87% -> 100% (1040 of 1041), words dropped for
having nothing to show 5 -> 0.

This also answers a question worth recording, since the shape of the bug
invited it: cards without a transcript example were not fabricated. Every
field is sourced -- vocabulary from the transcript via spaCy, definitions
and examples from the Wiktionary index, synonyms from OMW. The empty
field was a matching failure, and "savoir" now carries "déjà me faites
pas croire que vous savez" straight from the video.

### 8.21 Configured paths anchor to the project root, not the working directory

`DB_PATH`, `DICT_DIR`, `REVIEW_FILE`, and `OUTPUT_DIR` were resolved
relative to whatever directory the process happened to start in. Running
`python -m pipeline` from anywhere but the repository root therefore used a
different set of files, and every one of those failures is silent:

| Path | Consequence of resolving elsewhere |
|---|---|
| `DB_PATH` | A second, empty database. The definition cache is gone, and so is the record of which videos have been processed, so `check_video_not_processed` passes for a video already done. |
| `DICT_DIR` | An empty index directory. `wiktdata` treats a missing index as "not built", which is a supported state, so every non-English card loses its definition and the run reports success. |
| `REVIEW_FILE` | Words deferred to one `review.json` while `--process-review` reads another, reporting the queue as empty. |
| `OUTPUT_DIR` | The `.apkg` written somewhere other than where the docs and the Makefile say to look. |

None raises. This is 8.4 and 6.12's recurring shape again -- two values that
should be the same with nothing enforcing it -- with the working directory
as the unenforced half.

`config._project_root()` resolves the root from `config.py`'s own location:
the file sits at `<root>/src/pipeline/config.py`, so the root is three
levels up. `pyproject.toml` is the marker confirming it really is a project
root. A non-editable install puts the package under `site-packages`, where
three levels up is not a root and is the wrong place to write a database;
that case falls back to the working directory, which is exactly the old
behaviour. The documented install is `pip install -e .`, where the marker
is present.

**Anchoring the defaults alone would have fixed nothing.** `.env.example`
ships `DB_PATH=pipeline.db`, `OUTPUT_DIR=output`, and
`REVIEW_FILE=review.json`, so anyone who followed the documented setup has
a relative override in `.env` and never reaches the default. A relative
value is therefore anchored wherever it came from. An absolute path is
honoured as given, and `~` is expanded first so `~/tango.db` counts as
absolute instead of becoming a literal `~` directory inside the repository.

Verified by mutation rather than by the tests passing: reverting to the
unanchored version fails five of the new tests, and the narrower "anchor
the default only" version still fails the relative-override pair and the
three module constants this environment's own `.env` sets. Both matched
pairs are in `tests/test_config.py` per CLAUDE.md section 5.

### 8.22 The duplicate check reads a note's first field, not one named "Front"

`get_card_fronts()` read `fields["Front"]["value"]`. The model this project
generates names its first field `Word` (`cards.py`), so a deck built by this
pipeline returned **no fronts at all**, `_check_single` took its
empty-fronts branch, and every word added by an earlier run came back NEW.
Anki does not catch it downstream either: the GUID includes the video ID
(8.12), so the same lemma extracted from a second video is a different note
and imports as a duplicate rather than merging.

Measured against the real collection, the blindness was never limited to
this pipeline's own cards -- most real decks use something other than the
stock Basic type:

| Deck | Notes | Fronts seen before | After |
|---|---|---|---|
| `LangTest_fr2` | 1036 | 0 | 1036 |
| `French_to_English_Test` | 1074 | 0 | 1074 |
| `English_Test` | 1041 | 0 | 1041 |
| `French` (hand-built, 12 note types) | 2172 | 305 | 2172 |

End to end, reprocessing the video whose cards are already in
`LangTest_fr2` went from 1054 NEW -- a wholly duplicate run -- to 1050
SKIP, 4 QUEUE, 0 NEW.

Confirmed afterwards with two real pipeline runs into a purpose-made empty
deck, rather than only by replaying decisions against fetched fronts:

```
run 1, empty deck      Deck check: 0 skip / 0 queue / 207 new
                       imported 207 notes
run 2, --force, same   Deck check: 207 skip / 0 queue / 0 new
```

The second run created nothing. Before this fix it would have re-fetched
207 definitions and generated 207 duplicate cards. A snapshot of all 64
decks taken before and after confirms only the test deck changed.

The fix reads the field with the lowest `order`, which is what Anki itself
treats as a note's identity for its own duplicate detection. That makes the
check note-type agnostic rather than trading one hardcoded field name for
two. When `order` is absent (older AnkiConnect responses, and every test
fixture written before this change) it falls back to `Front` then `Word`,
and gives up rather than guessing at an unrecognised note type: feeding an
arbitrary field into the matcher would put definitions or audio filenames
in the candidate pool as if they were headwords.

**HTML stripping came with it, and was measured before being trusted.**
Anki stores fields as HTML and hand-made cards are full of it -- one real
front is `abominable<br><br>*qui inspire l'horreur`. Since this change is
what introduces those notes into the candidate pool, leaving the markup
would have been introducing the noise too. The concern was that stripping
raises word counts and tips a vocabulary deck over
`_is_sentence_structured_deck`'s threshold, silently disabling fuzzy
matching. Measured across 7453 real notes it does the opposite: French's
average falls from 2.11 to 1.93 words, because tag soup was being counted
as words. No deck changed its verdict.

This is 6.11 again, exactly. Every fixture for this function named the
field `Front`, so no test could express a note type that does not have one,
and the bug was invisible to a suite that covered the function heavily.

Known limitation, not addressed: some hand-made note types put the word and
its definition in one field, so the front is `abominable *qui inspire
l'horreur` even after stripping. The length-ratio guard rejects those, so
they stay effectively invisible -- 152 of 2172 notes in the `French` deck.
Recovering them needs a first-segment heuristic, which is a different
decision.

### 8.23 Measured card quality, per field, on real French cards

Coverage numbers elsewhere in this document count what a *source* returned.
This is what actually reaches a card, read back out of Anki after import —
207 French cards from video `2yHn8uc5_-4`, deck `Tango_Verify_20260807`,
with the offline index built:

| Field | Filled | Notes |
|---|---|---|
| Word | 100% | |
| Example from Youtube Video | 100% | 8.20's surface-form matching holding at ceiling |
| VideoID / Source | 100% | provenance, always written |
| Class (POS) | 98.6% | from the index, same 3 misses as Definition |
| Definition | 98.6% | 3 of 207 unresolved, up from 0% pre-index |
| 1st Example Sentence | 97.1% | |
| 2nd Example Sentence | 74.9% | Wiktionary often has only one |
| Synonyms | 85.0% | OMW first, index behind it |
| **Antonyms** | **23.2%** | the weakest field by a wide margin |

Definitions are effectively solved for a language with an index: 98.6%
here against the 95% recorded on the larger 1036-card run, and the residual
is transcription noise and proper nouns.

Antonyms are the one field still visibly thin, and it is a data problem
rather than a plumbing one. OMW returns no antonyms for any non-English
language (8.18), so the index is the only source, and Wiktionary entries
carry antonyms far less often than definitions. German and Russian measure
better than French here (51% and 46% against 20-23%), so this is
per-language rather than a global ceiling — an earlier claim that it was
"at the data ceiling" was measured on French alone and did not generalise.

### 8.24 Review and backlog modes resolve a language like the video path does

`_run_review` and `_run_backlog` called `fetch_definitions(words)` and
`build_package(...)` with no `language` argument, so both took the `"en"`
default in their signatures. `make review DECK="French"` fetched every
French word from English sources and built cards tagged English, and
reported success either way.

The GUID half is the worse half. `build_package`'s language feeds
`guid_for(lemma, video_id, language)` (8.12), so a French word added
through review mode carried an `"en"` GUID and collided with the English
card for the same spelling — the collision class issue #14 closed for the
video path and left open on these two.

Both modes now resolve the language through the same
`resolve_language_code()` the video path uses, via
`_resolve_side_mode_language()`, which also honours `--def-lang`.

**An unresolvable language is deliberately not fatal here**, unlike in
`_run_pipeline`. There the language selects the subtitle track and the run
cannot proceed without one, so it exits 1. Review and backlog have no
transcript — the language only steers definitions — so a deck named
"My Words" warns and falls back to `"en"` rather than starting to exit
non-zero on decks that work today. Both branches verified live.

The same two call sites were also dropping every `not_found_*` channel, so
review and backlog cards lost the Wiktionary examples, synonyms, and
antonyms the video path has carried since 8.19. Fixed in the same pass: it
is the same defect, at the same call sites.

Worth recording for how it was found. This was not spotted by reading the
module, and it had survived every previous session. It fell out of writing
the run-mode tests: 5 of the first 22 failed immediately, all of them this,
while the other 17 passed and confirmed the video path's wiring was sound.
`__main__.py` went 55% to 82% line coverage in the same commit, and the
suite 83% to 88%.

---

## 9. Known architectural gaps

### 9.1 dictionaryapi.dev has no meaningful non-English coverage

Documented in GitHub issue #1, originally with French-only curl evidence.
A later multi-language testing pass (section 8.17) confirmed this is not a
French-specific gap: 9 of 9 non-English languages tested came back at 0%
definition coverage against real videos -- French 0/1047, German 0/419,
Spanish 0/279, Portuguese 0/126, Japanese 0/32, Russian 0/701, Korean
0/119, Chinese 0/552 -- against English's 269/274 (98%). Confirmed
independently of our code with direct requests: `de/Auto` 502, `es/casa`
502, `pt/casa` 404, `fr/maison` 404, `ja/水` 404, `en/house` 200. The
dual-source architecture in ADR-005 is correct as designed; the premise
that dictionaryapi.dev has usable non-English data does not hold for any
language tested so far, not just French.

Section 8.13 closes the example-sentence half of this via Wiktionary, and
8.18 closes the synonym half for 18 languages via OMW. Definitions, part
of speech, and antonyms remain unaffected by either fix and are this
gap's open remainder for every non-English language. A verification run
against a French video produced 1047 words with 0 definitions found
either before or after those fixes (`fetch_definition` still requires a
real definition to return a "found" result), but fallback cards now carry
a real dictionary example or real synonyms far more often than an empty
field.

**Status: closed for any language with a built index (see 8.19).** The
offline Wiktionary index takes French definitions from 0% to 95%. What
follows is the evaluation that led there.

ADR-008 (`docs/ADR-008-per-language-dictionary-sources.md`) evaluated real
alternatives for the remaining gap. Wiktionary's raw wikitext API
(`action=parse&prop=wikitext`, distinct from the REST endpoint 8.13 uses)
is the only one confirmed to return real native-language definitions --
tested against real vocabulary, not single spot-checked words: French
10/15, German 8/14, Russian 10/14. Not shipped: a production version needs
a rate-limit backoff strategy (Wikimedia 429s after roughly 8-10
unauthenticated requests, independent of per-request delay), garbage-output
detection (some entries return leftover template syntax, e.g. Russian
`{{семантика|`, as if it were a successful extraction), and a recursive
template stripper (nested templates like `{{пример|...{{выдел|...}}...}}`
break single-pass regex removal). Tracked as issue #16, not folded into
8.18's pass given how much heavier this turned out to be than the ADR's
original single-word spot checks suggested.

---

## 10. Test architecture

692 unit tests across eleven test files, 24 more marked integration and
deselected by default. All run without network, Anki, or installed models.
Integration tests use `@pytest.mark.integration` and are excluded by the
default `addopts` in `pyproject.toml`.

Line coverage, measured with `make coverage` (88% overall, 1963 statements):

| Module | Cover | What is untested |
|---|---|---|
| `cards.py` | 100% | — |
| `config.py` | 100% | — |
| `state.py` | 100% | — |
| `language.py` | 99% | one branch |
| `nlp.py` | 93% | model-load failure paths |
| `deck.py` | 92% | the AnkiConnect transport itself |
| `wiktdata.py` | 91% | download and build error paths |
| `definition.py` | 88% | scattered source-specific branches |
| `transcript.py` | 82% | proxy and fetch-failure paths |
| `translation.py` | 68% | the argostranslate path |
| `__main__.py` | 82% | the interactive prompts and the setup wizard |

`__main__.py` was 55% when first measured, with the three run modes
untested. Writing those tests (8.24) took it to 82% and immediately found a
real bug — review and backlog silently processing every word as English.
That is the pattern worth remembering: 8.21, 8.22 and 8.24 were all wiring
rather than logic, none was findable by a unit test of a module in
isolation, and the modules they sat in reported 92% and 100% at the time.

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
