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
│   ├── Prototype Diagram.pdf
│   ├── ADR-008-per-language-dictionary-sources.md
│   ├── ADR-009-card-media-enrichment.md
│   └── ADR-010-conceptnet-antonyms.md
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
│       ├── media.py
│       ├── wiktdata.py
│       ├── antonyms.py
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
│   ├── test_antonyms.py
│   ├── test_media.py
│   ├── test_config.py
│   ├── test_hard_constraints.py
│   ├── test_state.py
│   ├── conftest.py
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

The SQLite `definitions` table uses a composite `lemma::language::pos`
primary key, so the same word is cached separately per definition language
and per part of speech. The `pos` segment is omitted when no part of speech
resolves, which is why `_cache_key()` has two return shapes.

### 3.7 wiktdata.py

The offline Wiktionary index, and the only source of non-English definitions.

Built from wiktextract output published per language by kaikki.org, from
that language's own Wiktionary edition, so glosses come back in the target
language rather than as an English gloss of a foreign word. One SQLite file
per language in `dictionaries/`, 131 to 404 MB each, built once by
`make dictionary LANGUAGE=<code>` and read offline afterwards.

```sql
entries (
    word TEXT, pos TEXT, definition TEXT,
    example1 TEXT, example2 TEXT, synonyms TEXT, antonyms TEXT,
    ipa TEXT, audio_url TEXT, form_of TEXT
)
```

Why bulk data rather than the live API, which is ADR-008 and issue #16:
Wikimedia rate-limits anonymous requests to roughly 8 to 10 before a 429
regardless of pacing, and one video needs 100 to 1000 lookups. Downloading
once sidesteps the limit rather than circumventing it.

Three things in `lookup()` are worth knowing. It picks between a word's
senses using the part of speech spaCy gave that word in its own sentence,
because the index holds one row per (word, part of speech) and taking the
first row defined `marcher` as a noun in a video about walking (8.29). It
follows inflection pointers, so `glaube` borrows `glauben`'s meaning while
keeping its own spelling and pronunciation (8.30, 8.38). And it never
raises: a missing, corrupt or outdated index returns None and the pipeline
falls back to its other sources, which is how every optional source in this
project behaves.

Connections are thread-local, because `definition.py` fetches through a
thread pool and one sqlite3 connection is not safe to share.

### 3.8 antonyms.py

The offline antonym index, called by `definition.py` and by nothing else.

One SQLite file for every language rather than one per language, 4.3 MB
covering 22, built by `make antonyms` from ConceptNet's assertions dump. The
dump is 498 MB and is streamed through a filter rather than stored.

```sql
antonyms (lang TEXT, word TEXT, pos TEXT, antonyms TEXT)
```

It is the last source tried for one field, after Merriam-Webster,
dictionaryapi.dev, the Wiktionary index and WordNet/OMW have all left it
empty. It exists because that happened on four cards in five for French.
8.42 has the measurements and ADR-010 the evaluation.

**Constraint 3.3 is enforced in the build rather than at the call site.**
Only pairs whose two ends are in the same language are stored, so a
cross-language antonym cannot reach a card even when a caller asks for one.
That is deliberate: 3.3 has been violated three times, each time by
something added beside the gate rather than inside it, and a filter in the
data cannot be walked around by the next call site.

Optional in the strong sense. Without the index every card is exactly what
it was before the index existed, and `--doctor` reports it as absent rather
than missing.

### 3.9 translation.py

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

### 3.10 cards.py

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

### 3.11 media.py

Downloads and caches the pronunciation audio a card embeds, called by
`cards.build_package()`.

Its own module because `cards.py` is otherwise entirely offline and
therefore trivially testable, and definition fetching happens long before a
package is built. Downloading belongs to neither.

Why download at all when v0.5.0 shipped a link: a link opens a browser,
which is not reviewing, and does nothing useful on AnkiDroid or AnkiMobile.
An embedded file plays inside the card and keeps working when the source is
down. ADR-009 rejected embedding on size grounds using an estimate nobody
measured; real Commons files are 16 to 30 KB, so a 240-card German deck
costs about 5 MB rather than "tens of megabytes".

**The part worth remembering is the pacing.** Wikimedia rate-limits per IP,
and the first implementation embedded 13 recordings out of 377 while
reporting success. A rate limit does not fail a run here, it quietly
degrades every card past the tenth. Downloads now go through a leaky bucket
and honour `Retry-After` on a 429. 8.35 has the measurements.

Files are named by hashing `language:lemma`, so a French recording can only
ever be named `tango-fr-*` and cannot turn up in a German package. That
naming is also what makes the cache safe to share across runs.

### 3.12 state.py

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

### 3.13 __main__.py

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

argostranslate pulls in PyTorch, which is why it is an optional dependency
group rather than a base dependency. This section said "roughly 1.5GB" for
months; the CUDA wheel pip took by default is 4.5 GB with its `nvidia` and
`triton` companions, and the CPU build `make translate-setup` now installs is
733 MB. See 8.41.

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

The `definitions` table uses `lemma::language::pos` as the primary key so the
same word is cached separately per definition output language and per part of
speech; the `pos` segment is dropped when none resolves. A bug where
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
### 8.25 Cross-language mode: which fields may change language, and which may not

CLAUDE.md 3.3 allows the definition and part of speech to be in the
definition language while requiring examples, synonyms and antonyms to stay
in the transcript language. Enforcing that is not automatic, because the
sources are keyed by language and it is easy to take a whole entry.

Three call sites populate those fields and each needed the same gate:

| Source | Definition | Examples / synonyms / antonyms |
|---|---|---|
| Merriam-Webster | target language | only when target == transcript |
| Offline index, target language | target language | only when target == transcript |
| Offline index, transcript language | transcript (8.26 fallback) | always allowed |

Two of the three were missing the gate and were caught by the first full
coverage sweep, not by tests:

```
de --def-lang ru   Frage      -> "Вопросы по пройденному материалу есть?"
en --def-lang de   Comment    -> "Comment ça va ?"
```

A German learner was being shown Russian sentences. The second case has an
extra edge worth knowing: a language's index carries entries for foreign
words too -- German Wiktionary documents French -- so a French-looking token
matched a French entry inside the German index.

**The metric was rewarding the violation.** Cross-language rows scored higher
on examples than native ones (de->ru 87% against de->en 45%), which is
impossible if the fields are constrained to the transcript language, and that
impossibility is what exposed it. A coverage number that improves when a
constraint is broken will hide the break; the sweep is only useful because
the numbers sat next to each other and could be compared.

Expect cross-language example and synonym columns to fall on the next sweep.
That is the measurement becoming honest.

### 8.26 A native definition beats no definition in cross-language mode

In --def-lang mode every lookup runs against the target language, and the
index consulted is the target's. There is normally no English index, so when
Merriam-Webster and dictionaryapi.dev both miss the translated word the card
used to ship with "No definition found" -- while the transcript language's
own index had the word.

Measured on a real German->English deck: 46 of 459 cards, and not obscure
ones (Spracherwerb, Intensivkurs, Spaziergang, Abschluss, Ende, Sehen, Auch).
A native definition is now the last resort before giving up. It records
"wiktionary-native" as its source so the mixture is visible in the data
rather than silent, and the coverage matrix reports the share as "fellback".

Recovers 19 of 30 sampled failures; the rest are transcription noise and
productive German compounds Wiktionary does not carry.

### 8.27 A card-quality fix does not reach cards that are already cached

Twice now a fix has been verified in the code and then measured as still
broken, because the definition cache served the old data.

The false-friend case: German lemmas cached under `lemma::en` during the runs
where translation was silently unavailable, so `je` kept returning "the 9th
letter of the English alphabet" long after the cause was fixed. 349 rows.

The cross-language case: after 8.25 gated examples to the transcript
language, the corrected sweep still showed Russian sentences on German cards.
The code was right -- a direct call with `use_cache=False` returned a Russian
definition with no example and no synonyms, exactly as intended -- while the
sweep read `kopf::ru` rows written by the previous sweep, example included.
4532 rows.

The cache stores the assembled fields, not the inputs, so nothing about a
fetch-path fix invalidates them. Two consequences worth building on:

- **Any change to what goes in a field needs the affected rows cleared**, and
  the clearing is part of the fix rather than a follow-up. Neither of these
  was noticed until a measurement disagreed with the code.
- **A measurement run should be able to bypass the cache.** The sweep exists
  to tell the truth about the pipeline, and a warm cache is exactly what
  stops it doing so. `fetch_definition` already takes `use_cache`; the CLI
  does not expose it.

The cache key itself is `lemma::target_language::pos`, which does not record
the transcript language, so a contaminated row is not identifiable from the key
alone -- it has to be reconstructed by joining the vocabulary table back to
the video's language. A key carrying both languages would make invalidation a
one-line delete, and is the obvious fix if this happens a third time.

### 8.28 Sense selection by word overlap: tried, measured, rejected

The pipeline takes the first index row for a word. Wiktionary orders senses
by its own conventions, so that is frequently the wrong sense for a given
video -- the recorded case is a French video about tapas bars producing a
card defining "tapa" as Polynesian bark cloth, with the food sense one row
below.

The obvious fix is to score each candidate against the sentence the word came
from, counting content words shared between the context and the entry's own
definition and examples. It was implemented and it fixed the case it was
written for:

```
without context : "Étoffe faite d'écorce battue ... de Polynésie"
with the video  : "Petite portion d'un aliment ... dans les bars en Espagne"
```

Measured on a real video's vocabulary rather than the case that motivated it,
it is net-negative. Of 231 lemmas, 146 have more than one sense -- ambiguity
is the norm here, not an edge case -- and context changed the pick on 15 of
them:

| lemma | first row | chosen by overlap | |
|---|---|---|---|
| heu | "Petit bateau d'environ 300 tonneaux" | "Interjection ... exprime le doute" | better |
| côté | "Région des côtes" | "Nom de famille." | worse |
| gens | "Personnes en nombre indéterminé" | "Clan familial." | worse |
| fait | "Réalisé ; construit" | "Participe passé masculin singulier" | worse |
| surtout | "Excès" | "Sorte de vêtement fort large" | worse |

One improvement against several regressions. **No threshold separates them**,
which is the part worth keeping: the correct "tapa" sense wins with an
overlap of 2, and so do "Nom de famille", "Clan familial" and the obsolete
garment. Raising the bar to 3 discards tapa while keeping "Participe passé"
(3) and a questionable "obligé" (4). The signal and the noise have the same
magnitude, so tuning cannot fix it.

Reverted rather than shipped. It would have been an improvement measured on
the one case already known to be broken and a regression everywhere else,
which is the shape SESSION.md 6.6 records as the most dangerous kind of fix.

**What the data says should work instead: part of speech.** spaCy already
determines the POS of each lemma *in its sentence*, and every index row
carries its own `pos`. The rows the overlap heuristic got wrong are mostly
separable that way:

```
fait    adj | noun | verb     spaCy knows which one this sentence uses
côté    noun | name           "Nom de famille." is tagged `name`
tapa    noun | noun | verb    POS narrows to two, and those two differ in topic
marche  noun | noun | verb    same
```

A `name` row should never be selected at all, since proper nouns are already
filtered upstream by the NLP stage. Filtering candidates by POS first, and
only then breaking ties, uses a signal the pipeline already computes and
does not depend on shared vocabulary between a definition and a sentence.
`gens` stays unsolved -- all three of its senses are nouns -- so this is a
narrowing, not a solution.

### 8.29 Sense selection by part of speech: measured, and shipped

What 8.28 said to build instead, built. Two rules in `wiktdata._select_row`:
prefer a row whose `pos` matches the tag spaCy gave the word *in its own
sentence*, and never take a `name` row while any other exists.

**Measured before it was written, on three real videos rather than on the
case that motivated it** -- the discipline 8.28 exists to enforce. Of 1209
lemmas found in the index, it changes 60 picks:

| language | lemmas in index | >1 row | picks changed | better | worse |
|---|---|---|---|---|---|
| fr | 204 | 130 | 16 | 13 | 3 |
| de | 342 | 159 | 34 | 18 | 9 |
| ru | 663 | 115 | 10 | 8 | 2 |

The reverse of the word-overlap result, which went 1 better to 14 worse. A
sample of what it fixes, all confirmed in real generated cards:

```
super      Supercarburant.              -> Très ; extrêmement.
marcher    Déplacement ... ; démarche.  -> Se déplacer par un mouvement alternatif...
portable   Que l'on peut porter         -> Téléphone portable ; téléphone cellulaire.
mal (de)   Buch Maleachi                -> einmal, zu einem gewissen Zeitpunkt
вид (ru)   река в Германии              -> внешний облик
близкий    остров в Карском море        -> находящийся недалеко
```

The last two are the `name` rule: Russian led with a river in Germany and an
island in the Kara Sea. Proper nouns are filtered upstream by
`nlp._is_valid_token`, so a `name` row is never why a word is on a card.

**Ambiguity is the norm, not an edge case.** 404 of those 1209 lemmas have
more than one row, so this is reaching a third of all cards even though it
changes 5% of them.

**A word whose POS has no row keeps its current card.** Falling back to the
first row rather than to nothing was deliberate: 11 French, 25 German and 6
Russian lemmas match no row, mostly function words and interjections that
spaCy tagged ADV. Dropping them would trade a wrong sense for no card.

**What it does not fix.** German regressions cluster in two classes, both
pre-existing limitations this exposes rather than creates:

- *Inflection-pointer glosses.* German Wiktionary indexes every inflected
  form, so `glaube` can resolve to "1. Person Singular Indikativ Präsens
  Aktiv des Verbs haben" -- a pointer, not a definition. Counted before and
  after: de 18 -> 19, fr 3 -> 2, ru 10 -> 10. The POS filter does not move
  this number, so it is a separate problem. wiktextract tags these senses
  `form-of`, which the index does not store, so fixing it is a schema change
  plus a rebuild. Filed in TASKS.md.
- *Archaic first senses.* `aber` -> "abermals", `egal` -> "fortwährend". The
  index stores only the first sense per (word, pos), so choosing the right
  POS cannot rescue a row whose own first sense is obsolete.

`gens` stays unsolved as predicted -- all its senses are nouns.

**The cache had to move with it.** ARCHITECTURE 8.27 records twice that a
card-quality fix does not reach rows already written, because the cache
stores assembled fields. The key is now `lemma::language::pos`, so the same
lemma read as a noun and as a verb are different rows and the old
two-segment rows are simply missed rather than served stale. Self-
invalidating, so no delete step to forget. Only a POS the index can
distinguish enters the key -- one that cannot change the selected row must
not split the cache into rows differing in nothing. `_cache_key()` is a
single function because `fetch_definitions()` and `fetch_definition()` built
this string separately once and drifted (SESSION.md 6.12).

The tag is the one the word carried *most* often, not first: spaCy tags per
sentence, and a word used ten times as a verb and once as a noun is a verb.
Ties resolve to the earlier tag, which keeps it deterministic -- it feeds a
cache key.

### 8.30 Index schema v2: pronunciation, and following inflection pointers

ADR-009 phase 1's index half. Two changes in one schema bump, because a bump
costs a full re-download per language (288 MB de, 682 MB fr, 278 MB ru) and
both sets of columns were already sitting in the same source records.

**Pronunciation.** `ipa` and `audio_url` are extracted from each entry's
`sounds` block. The ADR's coverage estimates were verified rather than
trusted, and the real German build beats them: IPA on **99.7%** of rows
against a claimed 91.5%, audio on **95.0%** against 65.7%.

**German is not representative, and the ADR's single figure hid that.**
Measured across all three built indexes:

| language | rows | ipa | audio | form_of |
|---|---|---|---|---|
| de | 993,774 | 99.7% | **95.0%** | 81.4% |
| fr | 2,108,227 | 85.1% | **12.1%** | 73.3% |
| ru | 470,784 | 83.3% | **4.4%** | 2.5% |

IPA is dependable everywhere (83-99%). **Audio is not**: German has a
recording for almost every entry, French for one in eight, Russian for one
in twenty-three. So the "Listen" link is a German feature that degrades
gracefully elsewhere, not a general one, and any future decision to embed
audio rather than link it (see `cards._audio_field`) should be costed on
German alone — it is the only language where most cards would carry a file.

`form_of` splits the same way. The inflection-pointer fix matters enormously
for German and French, where 81% and 73% of rows are inflected forms, and is
nearly inert for Russian at 2.5%. SESSION.md 6.14 is the standing lesson
here: measure per language before generalising. This table exists because
that lesson was about to be repeated from a German-only sample.

**Inflection pointers turned out to be the larger finding.** German indexes
every case, number and tense form as its own entry -- **81.4%** of the real
index -- so a card could read "1. Person Singular Indikativ Präsens Aktiv des
Verbs glauben", which is a pointer, not a definition.

A 20 000-entry sample had put this at 4%. The sample was drawn from the head
of the file and landed in a cluster of Latin taxonomy lemmas. *A sample from
an ordered file is not a random sample* -- the same lesson as 8.14, arrived
at from the opposite direction.

Fixed at two levels. Within a record, a real gloss now beats a pointer
sitting in front of it. Across records, v2 stores the word the pointer names
(`form_of`), so it can be followed: `glaube` resolves to `glauben` and the
card gets a real definition. **One hop only** -- a chain, or a missing
target, keeps the original row, because losing the card is worse than a poor
definition.

Pronunciation deliberately does **not** follow the pointer. The learner sees
`glaube`, so the card carries glaube's own IPA while borrowing glauben's
meaning. A mutation that takes the whole row from the pointer target fails a
test.

Measured on the real German video, 342 lemmas:

| | before | after |
|---|---|---|
| pointer definitions | 18 | 1 |
| IPA | 0 | 341 |
| audio | 0 | 339 |

with no change in how many lemmas resolve at all.

### 8.31 The model ID was protecting the wrong integer

`MODEL_ID` moved from `1607392319` to `1607392321` on 14 August 2026, the
only time it may ever move.

`1607392319` is the model ID in **genanki's README example**, copied along
with the tutorial and paired there with a notetype named `Simple Model`. Any
collection that ever imported a deck built from that tutorial already has the
ID taken, and Anki will not reuse it -- it forks a new notetype with a bumped
ID and a suffixed name.

Measured on the real collection (the `User 1` profile): `1607392319` held a
7-field `Simple Model` with **zero** notes, while this pipeline's **2135**
cards sat on `1607392321`, the ID Anki assigned when it forked. Five separate
forked notetypes had accumulated that way.

So the constraint was real, and for most of this project's life it was
guarding an integer that held none of our cards. Packages now declare the
notetype the cards are actually on, and imports merge into it instead of
forking again.

Note the ID resolves through `ANKI_MODEL_ID`, so `.env` overrides the
default. **Pinning only the resolved value is not enough** -- a test that
does so passes on any machine with a `.env` while the source default drifts.
That gap was found by mutation, and the tests now pin the literal in
`config.py`'s source too. Same shape as 8.21 (a documented install always
takes the override branch and never reaches the default) and SESSION.md 6.18.

### 8.32 Appending a field forks the notetype unless the collection is aligned first

8.31 explains why the ID had already moved twice. This is the mechanism that
moved it, met head-on while shipping ADR-009 phase 1's two new card fields.

**Anki matches an incoming notetype by ID. When that ID already exists with a
different field list, it does not merge -- it forks.** New notetype, ID bumped
by one, name suffixed, existing notes left behind on the old one, new cards
written to the fork.

Measured against a real collection holding the 10-field notetype at
`1607392321`, importing the 12-field package:

| | result |
|---|---|
| notetype created | `YT Anki Pipeline — Recognition-da2c0` at **1607392322** |
| notes on `1607392321` | 207, untouched, still 10 fields |
| notes on the fork | 1 — the new card, with IPA and Pronunciation |

That is `+1` from `1607392321`, exactly as `1607392321` is `+2` from
genanki's `1607392319`. The gap in 8.31 was never a mystery; it was two prior
runs of this same event.

**The fix is to make the schemas match before the import.**
`deck.ensure_model_fields()` reads the collection's field list and adds any
name in `cards.FIELDS` that is missing, at its canonical index.
`__main__._prompt_import()` calls it before `importPackage`.

Adding a field to a live notetype is the safe direction, and this was
verified rather than assumed -- all **207** notes came through with
**byte-identical field values** and two new empty fields. Re-importing then
produced no fork, and a genuinely new word landed on `1607392321` carrying
both fields. It is still a schema change, so Anki asks for a full sync
afterwards; the CLI says so when it adds anything.

A failed alignment **cancels the import**. If we could not confirm the
notetype's shape, importing is the thing that splits the collection, and
leaving an `.apkg` on disk is the cheaper failure.

**Two limits worth knowing.** The alignment runs on the AnkiConnect
auto-import path only -- importing by hand via File → Import still forks,
because nothing has aligned the notetype first. And the import never updates
an existing note: across two imports where three GUIDs matched existing
notes, not one field value changed. Pronunciation therefore reaches new cards
only, which is consistent with the pipeline shipping just the words the deck
check called NEW, but it does mean already-imported cards will not gain these
fields without a separate back-fill.

### 8.33 The notetype must be found by ID, because the name is not stable

8.32's alignment shipped resolving the notetype by **name**. That is wrong
on any collection with history, and the reason is 8.32's own mechanism:
**when Anki forks a notetype it suffixes the name.** So the fork takes the
ID and a *decorated* name, and the plain name is left on whatever was there
before.

Found by a read-only inspection of the real collection before applying
anything to it. What was actually there:

| id | notes | name | field list |
|---|---|---|---|
| 1607392319 | 0 | `Simple Model` | genanki's tutorial, 7 fields |
| 1607392320 | 0 | `Simple Model++++++++` | 4 fields |
| 1782849352300 | 1134 | **`YT Anki Pipeline — Recognition`** | `PartOfSpeech`, `ExampleDict`, `FallbackNote` … |
| 1783884301416 | 409 | `YT Anki Pipeline — Recognition+` | 12 fields, `WordSecondary` |
| 1784324984520 | 907 | `YT Anki Pipeline — Recognition++` | 11 fields, `FallbackNote` |
| 1784390271313 | 1773 | `YT Anki Pipeline — Recognition+++` | the canonical 10 |
| **1607392321** | **2135** | **`YT Anki Pipeline — Recognition-6c3a0`** | **the canonical 10** |

The notetype this pipeline actually writes to is the *suffixed* one, and the
plain name belongs to a different notetype with an incompatible schema from
an older version of the card model.

Resolving by name therefore picked `1782849352300` and would have added six
fields — `Class`, the three example fields, `IPA`, `Pronunciation` — to a
notetype holding 1134 notes that the pipeline does not write to, while the
import forked anyway because the ID still disagreed. Dry-run on the real
collection, by ID against by name:

```
by ID    -> 'YT Anki Pipeline — Recognition-6c3a0'   would add: [IPA, Pronunciation]
by name  -> 'YT Anki Pipeline — Recognition'         would add: [Class, 1st Example
            Sentence, 2nd Example Sentence, Example from Youtube Video, IPA,
            Pronunciation]
```

`ensure_model_fields()` now takes `config.MODEL_ID` and inverts
`modelNamesAndIds`. The ID is what Anki matches on when importing, so the ID
is what the alignment must read.

**Why the 8.32 verification did not catch it.** That was run in a scratch
profile containing exactly one pipeline notetype, created fresh, whose name
and ID agreed. The bug is not expressible there — the same shape as
SESSION.md 6.18 and 6.11: a fixture that cannot represent the failure.
The `+`, `++`, `+++` names in the table above are the audit trail of every
earlier schema change, and they were sitting in the collection the whole
time.

### 8.34 Pronunciation describes the word on the card, so it has one source

v0.5.0 shipped pronunciation that could belong to a different word than the
one printed on the card.

In `fetch_definition()`, the cross-language branch looks the word up in the
**target** language, using `query_lemma` — the translation. It assigned:

```python
definition     = entry.definition
ipa            = ipa or entry.ipa          # ← outside the gate
audio_url      = audio_url or entry.audio_url
if target_language == language:            # ← the gate
    native_ex1 = entry.example1            # examples/synonyms/antonyms
```

Examples, synonyms and antonyms were already protected. Pronunciation was
added immediately above that gate in v0.5.0 and never joined it. Measured on
real index data, a German video with `--def-lang fr`:

| field | value | correct? |
|---|---|---|
| Word | `Haus` | |
| Definition | `Bâtiment servant de logis…` | yes — 3.3 permits this |
| IPA | `\me.zɔ̃\` | **no — that is *maison*** |
| Pronunciation | a French recording | **no** |

The learner is told `Haus` sounds like /mɛ.zɔ̃/, which is worse than the empty
field English gets: confidently wrong rather than absent.

**The fix is structural, not a fourth gate.** Pronunciation is now resolved
exactly once, by `_resolve_pronunciation(lemma, language, pos)`, after the
definition is settled and independent of which source supplied it. No
definition branch touches `ipa` or `audio_url`. The branches differ in which
language they hold; pronunciation must not, and one assignment cannot
disagree with itself.

That also fixed English for free. `_resolve_pronunciation` falls through to
dictionaryapi.dev when a language has no index, and the pipeline already
called that API — it returns real IPA (`/haʊs/`) and a complete audio URL in
a `phonetics` block that nothing ever parsed. The sixth instance of
fetched-parsed-discarded. English cards now carry pronunciation even when
Merriam-Webster supplied the definition, which the old per-branch structure
could not express, because MW is tried first and the dictapi branch only ran
when it missed.

**The lesson is about how the constraint was written.** 3.3 named three
fields, and was violated three times — each by someone adding a *fourth*
thing beside the gate. A constraint stated as a list invites that; stated as
a question — *does this describe the word shown?* — it does not. 3.3 is now
written that way, and pronunciation's single-source structure is pinned by
`test_pronunciation_has_exactly_one_source`, which fails if any branch reads
`entry.ipa` again.

---

### 8.35 Audio downloads are paced, because the host rate-limits per IP

Embedding the audio worked. Downloading 400 of them did not.

A real run — `VIDEO_ID=loqocHC9aAU DECK="Test" LANGUAGE=de`, 406 cards, 377
of them with a pronunciation URL — embedded **13** recordings. The other 364
fell back to a link. No error, no warning, a valid `.apkg`, and every one of
those URLs downloading fine when tried on its own.

`upload.wikimedia.org` rate-limits per IP. Measured 17 August 2026:

| how the requests were sent | result |
|---|---|
| 8 concurrent workers (what shipped) | 10 × 200, then 429 |
| strictly sequential, no delay | 10 × 200, then 30 × 429 |
| original `.ogg` instead of transcoded `.mp3` | 10 × 200, then 20 × 429 |
| sequential, 0.7s apart | **18 × 200, no 429** |

Each 429 carried `Retry-After: 11`. Sequential made no difference, so this is
a token bucket on requests, not a cap on concurrency, and it is not specific
to the on-demand transcoding path. The bucket is roughly ten deep and refills
around once a second.

Two independent mistakes made this total rather than partial:

1. **The pool drained the bucket in under a second.** Eight workers against a
   ten-token bucket exhausts it on the first breath, so the failure began at
   card 14 and never recovered.
2. **A 429 was treated as permanent.** `raise_for_status()` raises on 429
   exactly as it does on 404, and the handler returned `None` for both. The
   one status code that specifically means *ask again shortly* was the one
   read as *give up*.

**The fix is a leaky bucket in `media`, not a sleep in the loop.** A
module-level `_RateLimiter` hands out time slots `1/MEDIA_RATE_LIMIT` apart
across every thread, allowing `MEDIA_BURST` of them back-to-back after an
idle spell so a five-word run pays nothing. Threads hold the lock only long
enough to claim a slot and sleep outside it, so transfers still overlap — the
limiter caps the request *rate*, it does not serialise the downloads. A 429
is now retried up to `MEDIA_MAX_RETRIES` times, waiting the period the server
asked for, clamped by `MEDIA_MAX_RETRY_WAIT`; every other status still fails
the card immediately, because a 404 means the file is genuinely absent and
waiting it out would cost minutes across a deck.

A cache hit takes no slot, which is what keeps a re-run instant.

**This is the same failure shape as 8.25 and 8.34: valid-looking output, no
exception, and a number nobody looked at.** `_download_audio` had logged
"Audio: 13 of 377 cards will play inline" the whole time, at `INFO`, while
the CLI's default log level is `WARNING`. The line that would have given it
away was written and then discarded — so the count now also goes to the
`progress` callback the CLI actually prints, alongside a running tally, since
a paced download of a few hundred files is minutes of otherwise silent work.

---

### 8.36 The part of speech is a label, so it follows the definition

`wiktdata` stores whatever wiktextract wrote, and wiktextract normalises the
part of speech to an English tag regardless of which Wiktionary edition an
index was built from. Counted across the German, French and Russian builds,
those tags are `noun`, `verb`, `adj`, `adv`, `name`, `phrase`, `abbrev` and
about fifteen rarer ones. That is the right thing to store and the wrong
thing to show: a German card read `noun`, and `adj` is not a word in any
language.

Which language should the label be in? Constraint 3.3 answers it by asking
whether the field describes the word shown. `Class` does not describe the
word, it labels the definition sitting beside it, which is why 3.3 always
listed it as free to change language. So it goes in the definition's
language, and the rule is one line in `build_package()`:

```python
pos_language = def_language or language
```

A German word defined natively reads `Substantiv`. The same word under
`--def-lang fr` reads `nom`, matching the French text under it rather than
contradicting it.

Three details worth keeping:

- **Unknown tags pass through unchanged.** The French index contains
  `typographic variant`. Mapping missing keys to `""` would empty the field
  and nobody would notice, which is this codebase's favourite kind of bug.
- **A language with no table falls back to English**, whose job there is to
  expand `adj` into `adjective` rather than to translate. The fallback is
  therefore an improvement everywhere, not a placeholder.
- **The Russian index spells it `onomatopeia`**, 71 entries of it. Aliases
  handle that rather than a correction to the index, which would be undone
  by the next rebuild.

Tables cover de, fr, ru, es, it, pt and en. `test_no_table_is_a_copy_of_the_
english_one` fails if a language is added by copying the English row and
leaving it untranslated, which is the way this would quietly stop working.

---

### 8.37 Filler sounds are a stoplist, because the tag they carry is not reliable

`Ah`, `Bah`, `Ouai`, `Euh` and `Tss` were 3.4% of the cards in one real
French run. They are the cards that look broken to a user: there is nothing
to learn on them, and their presence suggests the rest of the deck was not
checked either.

The obvious fix is wrong twice over. spaCy tags these INTJ, and `ACCEPTED_POS`
never included INTJ, so a POS rule is already in place and these words got
past it anyway: the tagger reads `Euh` as a noun and `Bah` as an adverb in
the sentences they actually appear in, and a tag that is wrong is not a tag
you can filter on. Going the other way and dropping every INTJ costs real
vocabulary, because `Bonsoir` carries the same tag and belongs on a card.

So it is a stoplist of sounds, per language, in `language.FILLER_SOUNDS`,
checked by `language.is_filler()`. Four decisions in it are worth keeping:

- **Sounds only.** A word with a dictionary entry a learner would want is
  not filler however filler-ish it sounds in speech. French `bon`, German
  `na` and Russian `ну` are all common discourse markers, all carry meaning,
  and are all deliberately absent. The cost of a wrong entry here is a word
  the learner never sees and no message saying so.
- **Nothing is shared between languages.** `ah` is noise in French and noise
  in German, but one language's list must never decide what another filters.
  A language with no table filters nothing, which is the state of 20 of the
  24 codes in `SPACY_MODELS`.
- **Elongation is collapsed, at three characters and not two.** A transcript
  spells a drawn-out hesitation with as many letters as it likes, so `euuuh`
  and `ahhhh` are folded onto `euh` and `ah` before the lookup. Runs of two
  are left alone on purpose: English `err` is a real verb and would become
  `er`, a hesitation, under a two-run rule.

  That asymmetry has a consequence the first version of this table got
  wrong. Collapsing at three means `tsss` folds onto `ts` and not onto
  `tss`, so a sound is only reachable from its elongations when the
  single-letter-run spelling is listed as well. English, French and German
  happened to list both (`um` and `umm`, `ts` and `tss`, `pf` and `pff`).
  Russian listed `тсс`, `мм` and `ээ` and not their short forms, so `тссс`,
  `ммм` and `эээ` were cards. Every list looked complete on its own terms,
  which is why reading them found nothing.

  So the short forms are derived rather than authored. `FILLER_SOUNDS` is
  built from `_FILLER_SOUNDS_AUTHORED` by adding each sound with every run
  reduced to one character, and a language added later cannot reintroduce
  the gap. `test_every_sound_is_reachable_from_its_elongated_spelling`
  stretches each authored sound and fails if the lookup misses it.
- **The test is "would a course teach it", not "is it said without
  thinking".** The first version of these lists failed that on eleven
  entries, all dictionary words: French `hein`, `bof`, `ouais`, `ben`,
  German `tja`, `ach`, Russian `эй`, `ага`, `ой`, `эх`, English `yikes`.
  They were removed and are pinned by `test_a_real_word_is_never_filtered`.

  The two directions of error are not symmetric, which is why the list errs
  towards keeping words. A filler that slips through is one bad card the
  user deletes on sight. A word wrongly listed here is a word they are never
  offered, never see, and have no way to notice is missing.

  One asymmetry is deliberate. `ouai` is listed and `ouais` is not, though
  they are the same word: `ouais` is the dictionary spelling of something
  worth learning, while `Ouai` is one of the five spellings a real French run
  actually put on a card. The measurement is kept, the word is not.

- **The filter runs in `process_transcript`, not `_is_valid_token`.** It
  needs the language, which that function does not receive, and the
  corrected lemma, which does not exist until later. Skipping there also
  keeps the sound out of `surface_forms` and `parts_of_speech`, so nothing
  downstream can resurrect it.

The French list is the measured one. English, German and Russian are the
standard written spellings of the same kinds of sound and have not been
counted against a real run, which is the next thing to do rather than
something to assume. The run reports `Filler sounds skipped: N tokens` so
the effect can be checked instead of guessed at.

---

### 8.38 An inflection pointer's target is not spelled like the headword

8.30 shipped `form_of` and `_follow_form_of`, and it worked: measured on the
real indexes, 299 of 300 sampled German inflected forms and 297 of 300 French
now resolve to a real definition instead of printing a pointer. The task that
asked for this was still open, because nobody re-measured after the ADR-009
schema landed.

Russian did not do as well: 286 of 300, one card in twenty-two still reading
"дательный падеж от кома" instead of a definition. The cause is that the
pointer's target is not always spelled the way the index stores the headword,
and there are exactly two shapes:

| target as stored | headword | why it misses |
|---|---|---|
| `толк#(существительное I)` | `толк` | homograph disambiguator in the pointer |
| `кома#I` | `кома` | same |
| `нача́ло` | `начало` | combining stress mark |

595 of the Russian index's 11836 pointers carry a `#`, and 244 carry a stress
mark. German and French carry neither, across 2.3 million pointers between
them.

`_form_of_spellings()` returns the literal target first and then those two
normalisations, so it can only add a hit and never move one that already
worked. Russian went from 286 to 298 of 300; German and French are unchanged.

**The stress strip is scoped to Cyrillic, and that restriction is the fix's
real content.** On Cyrillic a combining acute is stress notation and carries
no meaning. The identical codepoint on Latin spells a different word: `a` and
`à` are separate French entries, as are `eleve` and `élève`. The first
implementation stripped everywhere and appeared to *improve* French by one
card, which was the tell. That card had resolved to the wrong word. Scoping
it to Cyrillic put French back to its true 297 and the wrong match went away.
`test_a_french_accent_is_never_stripped` pins it.

The lesson is the one 8.25 and 8.35 already record. A number moving the right
way is not evidence on its own, and an unexplained improvement in a language
the change was not supposed to touch is a bug report.

---

### 8.39 CI was green on a requirements file it never installed

Dependabot raised six pull requests. Five were fine. One bumped `thinc` from
8.3.13 to 9.1.1 in `requirements.txt`, passed CI on Python 3.10, 3.11 and
3.12, and produced a file that cannot be installed:

```
ERROR: ResolutionImpossible
    The user requested thinc==9.1.1
    spacy 3.8.14 depends on thinc<8.4.0 and >=8.3.12
```

`thinc` is spaCy's own tensor library, pinned here only for reproducibility,
so it cannot move ahead of what spaCy allows. Nothing about that is subtle.
The reason it looked mergeable is:

**CI installs `pip install -e ".[dev]"`, which reads `pyproject.toml`.
Nothing installed `requirements.txt` or `requirements-dev.txt` at all.**

So every check was honestly reporting on a file the pull request had not
touched. The three green ticks were true statements about something else.

The two files also drift for the same reason. `pyproject.toml` declares
ranges (`black>=24.0`, `mypy>=1.9`) and the pin files record exact versions,
and since only the ranges are ever installed, the pins quietly fall behind:
measured before this work, the venv had black 26.5.1 and mypy 2.1.0 against
pins of 25.1.0 and 1.15.0.

The `pins` job now resolves both files with `pip install --dry-run` and runs
`scripts/check_pins.py`, which catches the failure pip cannot. A pin that
resolves on its own can still contradict the declared range: `spacy==4.1.0`
installs happily and silently disagrees with `spacy>=3.8,<4.0`, meaning the
two files describe different projects. The job takes 16 seconds.

**The general shape is 8.35 again.** A number or a signal that is technically
correct about the wrong thing is worse than a missing one, because it stops
anybody looking. There the count was logged below the level the CLI prints;
here the check ran against a file the change did not touch.

---

### 8.40 The cache key recorded the wrong language

`_cache_key()` built `lemma::target_language::pos`. The target language is
the language the definition is written in, and it is not what decides the
row's contents.

Constraint 3.3 keeps examples, synonyms and antonyms in the **transcript**
language, so a German video run with `--def-lang en` writes a row holding
English definitions and German sentences. Keyed only by target, that row is
what an English video gets back for the same spelling. `hand`, `arm`, `band`,
`wild`, `blind` and `gift` are words in both languages, and the English card
would have received German examples with nothing raised.

Measured on the real 5408-row cache: **265 rows keyed `::en` already hold
German example sentences.** None has collided yet, only because no English
run has met one of those spellings. The bug was latent, not absent.

The key is now `lemma::source::target::pos`, four segments always, with `-`
where the part of speech did not resolve. The placeholder is not cosmetic: a
three-segment key for unresolved rows would escape a
`LIKE '%::de::en::%'` and invalidation would silently miss exactly the rows
nobody chose a sense for.

That query is the second half of the point. Both cache-poisoning incidents
needed the vocabulary table joined back to each video's language to find the
affected rows, and no table records that language. Invalidating a pairing is
now one `DELETE`.

**Old rows are renamed, not rewritten, and that is the interesting decision.**
Rewriting means knowing each row's source language, which means knowing which
video it came from, which nothing stores. Deck names are the only hint and
only 6 of 25 videos have one a language can be inferred from. Guessing native
for the remaining 76% would re-key cross-language rows as native, which is
precisely the collision the new key prevents: the migration would preserve
the bug while looking like it had fixed it. So `definitions` becomes
`definitions_v0`, nothing is lost, and the live cache refills as words are
met again.

**The failure it caused on the way in is worth keeping.** Two tests spelled
the old key out as a literal, and when the key changed they stopped matching,
missed the cache, and fell through to a real network call with no mock: the
suite hung rather than failed. Those tests now derive their key through
`_cache_key()`, which is the same reason that function exists. It also means
a unit test could reach the network when a fixture missed, which constraint
3.5 forbids and which nothing currently detects.

---

### 8.41 Four and a half gigabytes of CUDA nobody can call

`.tangovenv` measured 5.9 GB. `nvidia` was 2.7 GB of it, `torch` 1.1 GB and
`triton` 689 MB: 4.5 GB, 76% of the whole environment, and none of it
declared anywhere in `pyproject.toml`.

The chain is three links and the last one is the problem:

```
argostranslate  ->  stanza==1.10.1  ->  torch>=1.3.0
```

`torch>=1.3.0` names a version and not a variant. Since torch 2.x the default
PyPI wheel is the CUDA build, so pip installs it and its `nvidia-*` and
`triton` companions on every machine, whatever hardware is there.

**Measured on the developer machine, which does have an NVIDIA card:**

```
torch 2.13.0+cu130   built with CUDA 13.0   cuda available: False
```

The driver was too old, so the 4.5 GB had never been used once. On an AMD
card, an integrated one, or a machine with no GPU it can never be used at
all.

Installing torch from PyTorch's CPU index before argostranslate makes pip see
the requirement as satisfied. Verified in a clean virtualenv rather than
reasoned about:

| | size |
|---|---|
| `.tangovenv` before | 5993 MB |
| torch + nvidia + triton within it | 4515 MB |
| CPU-only torch replacing all three | 733 MB |
| **`.tangovenv` after** | **2211 MB** |

A saving of 3.8 GB, 63%, with `nvidia` and `triton` absent entirely and
argostranslate and stanza importing normally.

**The last row was projected until 26 August 2026, when the repair was
applied to this machine's own environment and it came out at 2.2 GB.** What
was replaced: `torch 2.13.0+cu130` by `torch 2.13.0+cpu`, a 191.8 MB wheel
that installs to 725 MB, and sixteen `nvidia-*` distributions plus
`triton 3.7.1` removed. 3.7 GB freed. `torch` computes, `stanza` and
`argostranslate` import, `make test` exits 0.

**Installing from the CPU index fixes a fresh machine and nothing else.**
Where torch is already present, pip answers "already satisfied" and the CUDA
build stays, so the target read as the fix while changing nothing for anyone
who had run it once before. Repairing an existing environment takes two
commands rather than one:

```
pip install --no-deps --force-reinstall --index-url .../whl/cpu torch
pip uninstall -y triton <every installed nvidia-*>
```

`--no-deps` is required rather than tidy: the CPU index is flat and a full
resolve against it fails, and torch's other dependencies are already
installed. The second command is required because pip does not remove
orphans, and replacing torch alone would leave 3.4 GB of `nvidia` and
`triton` behind with nothing able to call it.

`make translate-setup` now runs both when it finds a CUDA build with no
usable GPU, and `--doctor` prints both, spelled `python -m pip` with the
interpreter's own path because a virtualenv built by `make venv` has no `pip`
script in `bin/`. The condition is "CUDA build **and** no usable GPU", not
"CUDA build": someone with a working card chose that install, and neither
the target nor the advice may undo it.

**This cannot be expressed in `pyproject.toml`.** Choosing a wheel variant
means choosing an index, and PEP 508 requirements have no way to say that. So
it belongs in the install command, which is why `make translate-setup` does
it in two steps rather than one.

Anyone who does have working hardware can install the CUDA or ROCm build over
the top; nothing prevents it. `--doctor` reports which build is present and
whether the GPU is usable, because the waste is otherwise invisible: the
pipeline behaves identically either way, just four gigabytes larger.

**This is the same shape as 8.35 and 8.39.** The install worked, every test
passed, and nothing anywhere was wrong enough to look at. CLAUDE.md 3.6 said
PyTorch adds "roughly 1.5 GB" for months while the real figure was three
times that, because nobody re-measured a number that had no date on it.

---

### 8.42 The antonym gap was between two extractions of one edition

Antonyms were the weakest field on a card by a wide margin: 19.7% on a real
1054-lemma French deck, against 98.6% for definitions. TASKS.md had already
established that the index was not throwing anything away, since 0 of 40000
kaikki French entries carry sense-level antonyms and all 1682 top-level ones
are read. The conclusion drawn from that was that 22.7% is Wiktionary's
ceiling for French.

It is the ceiling of **one extraction** of Wiktionary, not of Wiktionary.

**This section first said "one edition", and that was wrong.** The claim was
that Tango's index is built from the English Wiktionary's entries for each
language, so ConceptNet was supplying an edition never read here. One query
disproves it: `maison` in the French index glosses as "Bâtiment servant de
logis, d'habitation, de demeure", which is the French Wiktionary's own
wording rather than an English gloss. `wiktdata.py` has said so in its
docstring since it was written. Recorded rather than quietly fixed, because
this is 6.22's shape again: a plausible cause written into the record in the
same session that found the effect.

What actually differs is the extractor. kaikki runs wiktextract; ConceptNet
ran wikiparsec in 2019. On the same edition they disagree about which words
carry an antonym, and the gain tracks how large ConceptNet's antonym
vocabulary is relative to the index's:

| language | words the index has an antonym for | words ConceptNet has |
|---|---|---|
| French | 15 045 | 12 376 |
| German | 30 616 | 3 547 |
| Russian | 23 747 | 1 857 |

For German and Russian the index holds eight to thirteen times more, so
ConceptNet mostly hands back what is already there. For French the two are
comparable and overlap only partly, so the union is much larger than either.
French is also where the extraction is thinnest in absolute terms: 0.78% of
its 1.93 million words carry an antonym, against 3.25% for German and 5.28%
for Russian. Measured end to end after building the index:

| deck | before | after |
|---|---|---|
| French | 19.7% | 34.8% |
| German | 56.2% | 60.3% |
| Russian | 47.8% | 48.8% |

`antonyms.py` holds it: one 4.3 MB SQLite for 22 languages, built by
streaming a 498 MB dump through a filter without ever storing it.
`definition.py` calls it last, only when nothing else filled the field, at
both call sites that fill it.

**Constraint 3.3 is enforced in the build rather than at the call site.**
Only pairs whose two ends are in the same language are stored, so a
cross-language antonym cannot reach a card even if a caller asks for one.
That is deliberate: 3.3 has been violated three times, every time by
something added beside the gate rather than inside it, and a filter in the
data cannot be walked around by the next person to add a call site.

Two things this does not fix. Antonyms remain the weakest field: French at
34.8% is still below German, and the union of both sources is still a long
way from the definition field. And the filter removes self-references,
cross-language leakage and other relations, but not a wrong antonym asserted
as an antonym: `es/grande` still lists `irrelevante` beside the correct
`pequeño`.

ADR-010 has the full evaluation, including the alternative that was measured
and not taken: reading the French edition directly is 3.17 GB uncompressed
for one language, against 498 MB once for all of them.

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

734 unit tests across eleven test files, 24 more marked integration and
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
