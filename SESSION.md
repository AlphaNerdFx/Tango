# SESSION.md — Current working state

Last updated: 7 August 2026, after the multi-language dictionary verification
session.

Read this to understand exactly where development stands and what was learned.
Everything here was checked against `git log`, the working tree, and the
installed environment at the time of writing, not recalled from memory. See
6.9 for why that distinction matters in this repository.

---

## 1. Where the project is

**Tagged release:** v0.4.4
**HEAD:** `b6a59eb`, 35 commits past the v0.4.4 tag, working tree clean,
pushed to `tango-origin/main`
**Working toward:** v0.4.5, untagged — the commits since v0.4.4 are shipped
and verified but have not been cut as a release
**Test state:** 640 passing, 0 failing, 24 integration deselected (`make test`,
79s). CLAUDE.md and ARCHITECTURE.md both still said 524/22 before this session;
corrected in the same pass.
**Overall completion estimate:** roughly 80 percent toward a v1.0.0 CLI tool,
roughly 25 percent toward the full multi-surface product vision

The estimate moved from 65 percent because the thing that made it 65 percent is
fixed. Non-English runs used to produce cards with empty definition, example,
synonym, and antonym fields. With a built dictionary index they now produce
real ones — French measured at 95 percent definition coverage, German and
Russian at 91 percent. What remains before v1.0.0 is packaging and interface
work (Typer CLI, Dockerfile, dependency size), not correctness.

The pipeline works end to end for English with no setup beyond the spaCy model.
For non-English it needs one extra step, `make dictionary LANGUAGE=<code>`,
documented in CLAUDE.md section 6 and the README. Without an index a
non-English run still works but produces no definitions — it falls back to OMW
synonyms, Wiktionary REST examples, and transcript examples.

---

## 2. What has shipped since the v0.4.4 tag

Thirty-five commits. Grouped by what they changed rather than listed
individually; `git log v0.4.4..HEAD` has the full sequence, and TASKS.md
records the measured result of each.

### 2.1 The non-English definition gap, closed

**Offline Wiktionary index (`73139d5`, `wiktdata.py`).** Built from
wiktextract output published per language by kaikki.org, taken from *that
language's own* Wiktionary edition so the glosses are native rather than the
English glosses the REST endpoint returns. This is why the earlier REST-based
fix improved examples but never touched definitions.

Bulk download, not the live API. Wikimedia 429s after roughly 8-10 anonymous
requests regardless of pacing, and a video needs 100-1000+ lookups. A proposal
to route around that with rotating proxies was rejected as circumventing a
rate limit rather than complying with it.

Layered with OMW rather than replacing it: measured on a real 958-lemma French
deck, the index covers synonyms at 49 percent against OMW's 76 percent, so OMW
keeps first claim and the index fills in behind. The index does supply
antonyms, which OMW cannot for any non-English language.

Optional by design. With no index, behaviour is exactly as before — pinned by
a test fixture, because the index lives on disk and would otherwise make tests
pass in CI and fail locally.

**Multi-language verification and the English exception (`3f02c04`,
`84d0eae`).** Measured against real deck vocabulary from previously generated
videos, not sampled words. German and Russian matter most: OMW covers neither,
so they had no synonyms at all before, and both carry far richer antonyms than
French. English is the exception and should not be built —
`--build-dictionary en` now warns and asks for confirmation rather than
silently downloading 475 MB to change nothing.

**Interrupted builds no longer orphan partial archives (`84d0eae`).** Found by
killing a French rebuild mid-download. `KeyboardInterrupt` is handled
separately from `OSError`, since Ctrl-C during a multi-minute download is
ordinary usage rather than an error.

### 2.2 Card content fixes

- **Second Wiktionary example was fetched then discarded (`5efd90b`).** The
  card model has two example fields but the fallback path kept only
  `examples[0]`. Now populated on 27 percent of cards, up from 0.
- **Transcript examples were searched by lemma, not surface form
  (`0ebdc79`).** A French video says "sais", the lemma is "savoir", and
  "savoir" appears nowhere in the text — so a word extracted *from* the
  transcript failed to find the sentence it came from. 137 of 1036 cards, all
  infinitives. 87 percent to 100 percent.
- **Verb lemmas spaCy's POS-blind lookup gets wrong (`7c50a72`).** Per-language
  fallback table that fires only when the model returned the surface form for a
  VERB, and validates the candidate against the model's own vocabulary so
  "être" cannot become "êtrer". French is the only verified entry.

### 2.3 Earlier in the same stream

OMW synonym supplementation for 18 languages and its three follow-up defects
(alphabetical ranking, nltk's non-thread-safe WordNet reader, mixed senses);
the per-word "No definition found" log flood; SQLite cache lock contention
discarding successful lookups; Chinese vocabulary extraction returning zero
words; `--force`; language folded into the card GUID; the guided `--setup`
wizard; the CI Python matrix and the removal of never-working 3.9 support.

---

## 3. Verification results from the last real runs

French video `2yHn8uc5_-4`, before and after the offline index:

| Field | Before | After |
|---|---|---|
| Definition | 0% | 95% |
| Class / POS | 0% | 95% |
| 1st example | 41% | 93% |
| 2nd example | 27% | 71% |
| Synonyms | 76% | 83% |
| Antonyms | 0% | 20% |
| Cards generated | 958 | 1036 |
| Words dropped with nothing to show | 83 | 5 |

The residual ~5 percent are transcription noise and proper nouns, near the
practical ceiling for this source.

Index coverage measured against real deck vocabulary, per language:

| Language | Lemmas | Definitions | Examples | Synonyms | Antonyms |
|---|---|---|---|---|---|
| German | 384 | 91% | 84% | 60% | 51% |
| Russian | 420 | 91% | 72% | 72% | 46% |
| English | 274 | 100% | 91% | 43% | 31% |

English's numbers are the reason not to build its index: a live run produced
273 of 274 definitions from Merriam-Webster and consulted the index zero times.
Its first-sense glosses are also often archaic, since English Wiktionary orders
senses historically — "may" resolves to "To be strong; to have power (over)".

---

## 4. Uncommitted state

None. The working tree is clean and `main` is level with `tango-origin/main`.

Every item the previous revision of this file listed as uncommitted —
the `DICT_API_BASE` fix, the WordNet language guard, the lemma validation
regex, the relaxed `requirements.txt` pins — has long since shipped. That list
was five days and thirty-five commits out of date, which is the specific
failure 6.9 warns about, recurring in the document that warns about it.

---

## 5. Environment specifics

```
OS:              Windows 11 with WSL2 (Ubuntu)
Python:          3.10.12
Virtual env:     .tangovenv  (NOT .venv)
Project path:    /mnt/c/DSC/Career/Projects/Tango
Git remotes:     tango-origin -> https://github.com/AlphaNerdFx/Tango  (current)
                 origin       -> .../Youtube-Anki-Flashcards.git       (renamed, dead)
gh CLI:          authenticated as AlphaNerdFx
Anki:            running on Windows, AnkiConnect bound to 0.0.0.0
ANKI_HOST:       http://172.28.144.1:8765 (WSL gateway, may change on restart)
ARGOS_PACKAGES_DIR: /mnt/c/Users/youssef/.argos-translate
```

Installed spaCy models:

```
de_core_news_sm  en_core_web_sm  es_core_news_sm  es_core_news_md
fr_core_news_sm  fr_core_news_md  ja_core_news_sm  ko_core_news_sm
pt_core_news_sm  ru_core_news_sm  zh_core_web_sm
```

French resolves to `md`, not `sm` — pinned in `language.SPACY_MODELS` after
`sm` was measured getting 2 of 3 of issue #13's reproduction sentences wrong.

NLTK data present: `wordnet`, `omw-2.0`, `omw-1.4`, `extended_omw`. Only
`omw-2.0` matters; this NLTK version silently ignores `omw-1.4`.

Built dictionary indexes (gitignored, rebuild with `make dictionary`):

```
dictionaries/wiktionary_fr.sqlite   309M
dictionaries/wiktionary_de.sqlite   152M
dictionaries/wiktionary_ru.sqlite   111M
```

Known environment issues:

- `gh issue list` fails with "Could not resolve to a Repository" because it
  resolves `origin`, which points at the pre-rename URL. Pass
  `--repo AlphaNerdFx/Tango` explicitly, or reorder the remotes.
- `numpy==2.4.4` requires Python 3.11+, incompatible with this 3.10 venv. All
  pins in `requirements.txt` are ranges rather than exact versions.
- `libretranslate 1.9.6` pins `requests==2.31.0` while a newer version is
  installed. Pre-existing warning, not blocking.
- WSL gateway IP for `ANKI_HOST` changes between WSL restarts. Re-check with
  `ip route | grep default` if AnkiConnect stops responding.
- `make test` takes roughly four minutes here, not the ~90s CLAUDE.md
  suggests — the repository lives on `/mnt/c`, and WSL2's filesystem bridge is
  the cost.
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

### 6.6 Rejected: rotating proxies for the Wikimedia rate limit

Proposed while designing the non-English definition source, since Wikimedia
429s after roughly 8-10 anonymous requests. Rejected: it circumvents a rate
limit rather than complying with it, it is against Wikimedia's bot policy, and
it reverses the reasoning already recorded in issue #8 and ADR-008 for
rejecting scraping sources. Downloading the data once makes the question moot,
and additionally skips recursive template stripping and garbage detection
because wiktextract resolved both upstream.

### 6.7 Mistake: misattributing English lemmatization to caption quality

Garbled lemmas — `toujour`, `allon`, `venon`, `transpi`, `dedan`, `longtemp`,
`ête` — were repeatedly explained during development as YouTube auto-caption
noise. They were not. They were an English spaCy model applying English
morphology rules to French text. This was only discovered when an agent with
filesystem access checked which spaCy models were actually installed.

Lesson: a plausible explanation that requires no verification is more dangerous
than no explanation at all.

### 6.8 Mistake: claiming dictionaryapi.dev had multilingual coverage

The dual-source definition architecture in ADR-005 was designed on the premise
that dictionaryapi.dev supports native-language queries via the path parameter.
This was based on reading the API documentation, not testing it. Direct curl
tests later showed French returns 404 for words as common as `eau`, `chat`, and
`maison`. A later multi-language pass confirmed it: 9 of 9 non-English
languages at 0 percent coverage, against English's 98 percent.

The architecture is internally correct and structurally useless for
non-English. Recorded in issue #1.

Lesson: verify an external dependency's actual behaviour before building
architecture on top of it.

### 6.9 Mistake: an unverified handoff document

A session handoff document asserted that `cards.py` and `translation.py`
contained specific changes and that `MODEL_ID` had changed. Neither was true —
the changes existed only as generated files that had never been copied into the
repository. The document also instructed deleting `pipeline.db` on the basis of
the non-existent model ID change, which would have destroyed a valid cache.

Lesson: a handoff document must be built from `git diff`, not from memory of
what was written.

### 6.10 Mistake: recommending the wrong resolution for the is_alpha conflict

When `token.is_alpha` was found to make the new lemma regex's hyphen branches
unreachable, the recommendation given was to keep `is_alpha` and simplify the
regex, on the grounds that removing a filter was an unmeasured behaviour
change.

That reasoning was wrong. `is_alpha` and `_is_valid_lemma` check the same
property on two different strings, and only the lemma is used downstream.
Keeping `is_alpha` was the identical mistake as the `len(token.text)` bug fixed
an hour earlier.

Lesson: a "conservative" recommendation that preserves a known-wrong pattern is
not conservative.

### 6.11 Lesson: fixtures can make bugs inexpressible

The mock helper `_make_token()` was called with identical `text` and `lemma`
values in every early test. That pattern meant no test could express the
surface-form-versus-lemma distinction, so the bug class was invisible to the
suite. Two separate bugs of that exact shape shipped.

The same shape appeared again in `TestWordNetLanguageGuard`: 3 of its 4 tests
mocked both definition sources dead, so they never reached the code they
claimed to guard and passed vacuously.

### 6.12 Lesson: the recurring bug shape in this codebase

Two values that should be the same, with nothing enforcing that they are:

- WordNet received `query_lemma` where `lemma` was meant
- `_fetch_from_dictapi` appended a language to a URL that already had one
- `_is_valid_token` guarded `token.text` where `token.lemma_` was used
- The batch loop built a cache key differently from `fetch_definition`
- The transcript search used the lemma where the surface form was needed

None produced a crash. All produced valid-looking wrong output. This is the
failure mode to watch for in this codebase specifically.

### 6.13 Lesson: fixing only the path you were looking at

A second recurring shape, distinct from 6.12. Three times now, a fix was
written into the found-definition code path when the path that actually
executes for the affected languages is the not-found fallback:

- The Wiktionary example fix on issue #1
- OMW synonym supplementation (ADR-008 Option A)
- The second Wiktionary example, kept in one path and discarded in the other

Each shipped looking correct and changed almost nothing in a real run, because
a language dictionaryapi.dev cannot find anything for almost never reaches the
found path. When touching definition assembly, check both paths.

### 6.14 Lesson: measure per language before generalising

An early claim that antonym coverage was "at the data ceiling" was measured on
French alone (20 percent). German and Russian came in at 51 and 46 percent
against the same source. Similarly, pinning French to `fr_core_news_md` fixed
its verb tagging, but the same upgrade for Spanish traded one wrong POS for a
different one. The size/accuracy tradeoff is a per-language-model
training-data question; one global rule does not answer it for 24 languages.
