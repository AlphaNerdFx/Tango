# SESSION.md — Current working state

Last updated: end of the v0.4.1 verification session, 2 August 2026.

Read this to understand exactly where development stands and what was learned.

---

## 1. Where the project is

**Tagged release:** v0.4.0
**HEAD:** several commits past the v0.4.0 tag, uncommitted work in the tree
**Working toward:** v0.4.1, a card-quality and bug-fix release
**Test state:** 418 passing, 19 deselected, as of the last full run
**Overall completion estimate:** roughly 65 percent toward a v1.0.0 CLI tool,
roughly 20 percent toward the full multi-surface product vision

The pipeline works end to end for English. For non-English languages it
produces cards, but with empty definition, example, synonym, and antonym fields
because of two compounding issues documented below.

---

## 2. What was done in the most recent session

The session was scoped to verifying an unverified batch of changes before
tagging v0.4.1. It found four bugs, two of which were introduced by the batch
under verification, and two of which were long-standing and previously
misdiagnosed.

### 2.1 Verified and confirmed working

- Environment install with relaxed version pins in `requirements.txt`
- NLTK WordNet data download
- Full unit test suite: 418 passing
- No duplicate cards in generated packages
- `FallbackNote` field confirmed absent from the model schema, verified by
  reading the actual SQLite collection inside a generated `.apkg`
- Transcript-sourced example sentences are correctly in French

### 2.2 Bugs found and fixed during the session

**`DICT_API_BASE` had `/en` baked into the base URL.**
`config.py` defined the base as `.../api/v2/entries/en` while
`_fetch_from_dictapi` appended `/{language}/{lemma}`, producing malformed paths
like `.../entries/en/fr/dire`. Every non-English dictionary lookup was hitting a
404 URL. Fixed by removing the trailing `/en`. Confirmed working — URLs now
correctly render as `entries/fr/donc`.

**Single-letter cards from a surface-form length check.**
`_is_valid_token` checked `len(token.text) < 2` but the vocabulary dictionary is
keyed by `token.lemma_.lower()`. A French conjugation with a multi-character
surface form that lemmatizes to a single character passed the filter, producing
a card with the front "E". Fixed by validating the lemma. Two matched
regression tests added.

**`token.is_alpha` blocking legitimate compounds.**
The fix for the previous bug added `lemma.isalpha()`, which rejects hyphenated
compounds. `semi-relevée` — a real French adjective — was excluded. So would
`week-end`, `arc-en-ciel`, `après-midi`, `porte-monnaie`. Replaced with a
Unicode-aware regex permitting internal hyphens and apostrophes.

A follow-on problem was then discovered: `token.is_alpha` sitting above the new
regex made the regex's hyphen and apostrophe branches unreachable dead code.
Resolved by removing `token.is_alpha` entirely, with the reasoning that both
checks ask the same question about two different strings and only the lemma is
used downstream.

**WordNet injecting English synonyms into non-English cards.**
`_wordnet_synonyms_antonyms(query_lemma)` was called with `query_lemma`, which
holds the *translated* word when `DEF_LANG` differs from `LANGUAGE`. English
synonyms could therefore be written into `native_syns` and appear on French
cards, violating a documented hard constraint. Fixed by gating the WordNet call
on `language == "en"` and passing the original `lemma`.

Four regression tests were written for this. Verify they are present in
`tests/test_definition.py` — they may not have been applied.

### 2.3 Bugs found and NOT fixed

**spaCy model is not language-aware.** See `ARCHITECTURE.md` section 9.1.
Deliberately deferred as out of scope for a verification session. A full patch
is written and recorded in `TASKS.md`.

**dictionaryapi.dev has no non-English coverage.** GitHub issue #1 filed with
curl evidence. Requires new API sources and its own ADR. Out of scope.

**Flaky test.** `test_cache_hit_skips_fetch_definition_call` failed once in five
full-suite runs and did not reproduce. Diagnosed as probable SQLite connection
leakage — `_get_db()` opens a new connection per call and the `with conn:`
context manager commits but never closes, so under WAL mode there may be
occasional cross-test contention. Pre-existing, not introduced by this session's
changes. Fix is a `try/finally: conn.close()`.

---

## 3. Verification results from the last real-world run

Video `2yHn8uc5_-4`, French, `SPACY_MODEL=fr_core_news_sm`, `--language fr`,
after the `DICT_API_BASE` fix.

```
209 unique lemmas extracted
3 skip / 39 queue / 167 new
39 queued words deferred to review.json (0 approved, non-interactive run)
Definitions: 0 found / 167 not found
Cards: 142 total (0 standard, 142 fallback), 25 dropped
Package: output/2yHn8uc5_-4_20260802_084609.apkg
```

Checklist outcome:

| Check | Result |
|---|---|
| No single-letter cards | FAIL at time of run — card "E" existed. Fix applied after. Needs re-verification. |
| No proper-noun cards | Inconclusive — no counterexample surfaced in this video's vocabulary |
| No duplicate cards | PASS — 142 words, 0 case-insensitive duplicates |
| Example sentences in French | Partial — transcript field correct, dictionary fields empty |
| Definitions single-sentence | Not testable — all cards show the "No definition found" placeholder |
| Synonyms/antonyms as pills | Structurally present but CSS renders filled, not outlined. Unrelated to this diff. |
| FallbackNote field absent | PASS — verified against the model schema in the actual `.apkg` |

**This verification is incomplete.** The French run cannot exercise the
definition, example, synonym, or antonym paths because the API returns nothing.
An equivalent run against an English video is required to validate those fields.

---

## 4. Uncommitted state

At the time of writing, the following were modified but not committed:

```
Makefile                     nltk download added to spacy-model target
pyproject.toml               nltk dependency added
requirements.txt             exact pins relaxed to ranges, nltk added
src/pipeline/config.py       DICT_API_BASE trailing /en removed
src/pipeline/definition.py   WordNet fix, dedup guard, cache key fix,
                             accent fallback, definition truncation
src/pipeline/nlp.py          lemma validation, _is_valid_lemma regex,
                             token.is_alpha removed
tests/test_nlp.py            TestTokenFilterExtended class
tests/test_definition.py     TestWordNetLanguageGuard class (verify present)
```

**Uncertain:** whether `src/pipeline/cards.py` contains the outlined-pill CSS
change and the `added_lemmas` deduplication guard. An earlier session reported
`git diff --stat` showing zero changes to `cards.py`. Verify against HEAD before
assuming either is present.

**Confirmed false:** an earlier handoff document claimed `MODEL_ID` had changed
from 1607392320 to 1607392321. It has not. The value in `config.py` is
1607392319 and always has been. Do not delete `pipeline.db` on the basis of a
model ID change.

---

## 5. Environment specifics

```
OS:              Windows 11 with WSL2 (Ubuntu)
Python:          3.10
Virtual env:     .tangovenv  (NOT .venv)
Project path:    /mnt/c/DSC/Career/Projects/Youtube Anki Flashcards
Git remote:      tango-origin -> https://github.com/AlphaNerdFx/Tango
gh CLI:          authenticated as AlphaNerdFx
Anki:            running on Windows, AnkiConnect bound to 0.0.0.0
ANKI_HOST:       http://172.28.144.1:8765 (WSL gateway, may change on restart)
ARGOS_PACKAGES_DIR: /mnt/c/Users/youssef/.argos-translate
spaCy models:    en_core_web_sm installed, fr_core_news_sm may need installing
```

Known environment issues:

- `numpy==2.4.4` requires Python 3.11+, incompatible with this 3.10 venv. All
  pins in `requirements.txt` were relaxed to ranges.
- `libretranslate 1.9.6` pins `requests==2.31.0` while `requests==2.33.1` is
  installed. Pre-existing warning, not blocking.
- WSL gateway IP for `ANKI_HOST` changes between WSL restarts. Re-check with
  `ip route | grep default` if AnkiConnect stops responding.
- `pip install tango` installs an unrelated PyPI package. Never run it.

---

## 6. Reasoning history, dead ends, and mistakes

This section is deliberately unflattering. Everything here was a real error made
during development, and the reasoning is recorded so it is not repeated.

### 6.1 Rejected: Merriam-Webster as the only definition source

Considered and rejected because the free tier caps at 1000 requests per day and
covers English only. Kept as primary for English specifically, with
dictionaryapi.dev as fallback.

### 6.2 Rejected: PONS API for multilingual definitions

Investigated as a solution for non-English coverage. Rejected because the free
tier is 1000 requests per *month*, it requires registration, and it provides
bilingual translation pairs rather than monolingual definitions — which breaks
the "definition in the same language by default" design. Japanese has no direct
English pair at all. Recorded in ADR-005.

### 6.3 Rejected: Helsinki-NLP transformers for translation

Rejected because PyTorch plus transformers would add roughly 2GB. argostranslate
was chosen instead, which ironically also pulls in PyTorch. The size problem was
not actually avoided, only deferred to an optional dependency group.

### 6.4 Rejected: cloud translation APIs

Google Translate and DeepL rejected because both require paid keys at
production volumes. LibreTranslate community mirrors plus local argostranslate
chosen instead, accepting the uptime and latency costs.

### 6.5 Failed experiment: Webshare free-tier proxy

A Webshare free account was set up to work around YouTube IP rate limiting.
Result: the proxy made things worse. Transcript extraction failed with
`too many 429 error responses` through the proxy but succeeded without it. The
free tier provides datacenter IPs, which YouTube blocks more aggressively than
residential IPs. Proxy support remains in the code as optional but is not
currently used.

### 6.6 Mistake: misattributing English lemmatization to caption quality

Garbled lemmas — `toujour`, `allon`, `venon`, `transpi`, `dedan`, `longtemp`,
`ête` — were repeatedly explained during development as YouTube auto-caption
noise. They were not. They were an English spaCy model applying English
morphology rules to French text. This was only discovered when an agent with
filesystem access checked which spaCy models were actually installed.

Lesson: a plausible explanation that requires no verification is more dangerous
than no explanation at all.

### 6.7 Mistake: claiming dictionaryapi.dev had multilingual coverage

The dual-source definition architecture in ADR-005 was designed on the premise
that dictionaryapi.dev supports native-language queries via the path parameter.
This was based on reading the API documentation, not testing it. Direct curl
tests later showed French returns 404 for words as common as `eau`, `chat`, and
`maison`.

The architecture is internally correct and structurally useless for
non-English. Recorded in issue #1.

Lesson: verify an external dependency's actual behaviour before building
architecture on top of it.

### 6.8 Mistake: recommending the wrong resolution for the is_alpha conflict

When `token.is_alpha` was found to make the new regex's hyphen branches
unreachable, the recommendation given was to keep `is_alpha` and simplify the
regex, on the grounds that removing a filter was an unmeasured behaviour change.

That reasoning was wrong. `is_alpha` and `_is_valid_lemma` check the same
property on two different strings, and only the lemma is used downstream.
Keeping `is_alpha` was the identical mistake as the `len(token.text)` bug fixed
an hour earlier. The correct resolution — remove `is_alpha` entirely — was
argued for and adopted.

Lesson: a "conservative" recommendation that preserves a known-wrong pattern is
not conservative.

### 6.9 Mistake: an unverified handoff document

A session handoff document asserted that `cards.py` and `translation.py`
contained specific changes and that `MODEL_ID` had changed. Neither was true —
the changes existed only as generated files that had never been copied into the
repository. The document also instructed deleting `pipeline.db` on the basis of
the non-existent model ID change, which would have destroyed a valid cache.

Lesson: a handoff document must be built from `git diff`, not from memory of
what was written.

### 6.10 Lesson: fixtures can make bugs inexpressible

The mock helper `_make_token()` was called with identical `text` and `lemma`
values in every early test. That pattern meant no test could express the
surface-form-versus-lemma distinction, so the bug class was invisible to the
suite. Two separate bugs of that exact shape shipped.

### 6.11 Lesson: the recurring bug shape in this codebase

Four bugs found in one session share one structure: two values that should be
the same, with nothing enforcing that they are.

- WordNet received `query_lemma` where `lemma` was meant
- `_fetch_from_dictapi` appended a language to a URL that already had one
- `_is_valid_token` guarded `token.text` where `token.lemma_` was used
- The batch loop built a cache key differently from `fetch_definition`

None produced a crash. All produced valid-looking wrong output. This is the
failure mode to watch for in this codebase specifically.
