# TASKS.md — Tango

Prioritised remaining work. Checkboxes reflect state at the end of the v0.4.1
verification session.

---

## Completed

- [x] Core pipeline: transcript to Anki cards
- [x] Language resolution from `--language` flag or deck name
- [x] 40-language BCP-47 mapping with name variants
- [x] Partial BCP-47 matching (`fr` matches `fr-FR`)
- [x] Manual transcript preference over auto-generated
- [x] spaCy lazy loading with per-process cache
- [x] AnkiConnect deck check with three-condition fuzzy match
- [x] Sentence-structured deck detection
- [x] SQLite backlog when Anki is unavailable
- [x] Dual-source definition strategy (native examples, target definitions)
- [x] Merriam-Webster primary with dictionaryapi.dev fallback
- [x] SQLite definition cache with composite `lemma::language` key
- [x] Translation mode via community mirrors and local argostranslate
- [x] Per-word translation timeout to prevent CPU hangs
- [x] Per-run translation choice caching (prompt once, not per word)
- [x] genanki card generation with stable GUIDs
- [x] Card template with Anki CSS variables for light and dark mode
- [x] Ten-field card model with `FallbackNote` removed
- [x] 256-character caps with sentence-boundary truncation
- [x] Two dictionary example sentences plus transcript example
- [x] WordNet synonym and antonym supplementation for English
- [x] Deduplication guards in both `definition.py` and `cards.py`
- [x] SQLite state tracking for videos, packages, and vocabulary
- [x] CLI with default, review, and backlog modes
- [x] `--list-languages` flag
- [x] Makefile with setup, run, test, format, lint, clean targets
- [x] 400+ unit tests, no external dependencies in the default run
      (exact count drifts as tests are added; check `make test` output
      rather than trusting a hardcoded number here — see CONTRIBUTING.md's
      own stale "411" claim, corrected during this session)
- [x] GitHub Actions CI on push and pull request
- [x] Documentation set: PRD, SAD, SRD, ADR, code walkthrough
- [x] GitHub community files: code of conduct, contributing, security policy
- [x] Issue templates: bug, feature, language coverage
- [x] Project wiki: installation, configuration, troubleshooting, WSL, FAQ
- [x] Single-letter card filter via lemma validation
- [x] Proper noun and named entity filtering
- [x] `DICT_API_BASE` double-language-code URL bug
- [x] `token.is_alpha` removal in favour of `_is_valid_lemma`
- [x] `requirements.txt` version ranges for Python 3.10 compatibility
- [x] `ARGOS_PACKAGES_DIR` support for stable model storage on WSL
- [x] GitHub issue #1 filed for the ADR-005 coverage problem

---

## Critical

All done — v0.4.1 tagged. See CLAUDE.md/ARCHITECTURE.md for current state.

- [x] **Verify the WordNet fix is actually applied.**
      Confirmed: gated on `language == "en"`, passes `lemma` not `query_lemma`
      (`definition.py:645`). `TestWordNetLanguageGuard` existed but 3 of its 4
      tests mocked both definition sources dead, so they never actually
      reached the code they claimed to guard — passed vacuously. Fixed
      separately from the guard itself; see commit history.

- [x] **Verify `cards.py` state against HEAD.**
      Was actually zero diff from HEAD, as an earlier session suspected —
      neither the `added_lemmas` guard nor the outlined-pill CSS existed.
      Filed as issue #2, fixed, verified with 2 live runs (English bypass +
      full CLI via `--process-backlog`), closed.

- [x] **Re-run Step 5 verification after the lemma fixes.**
      No single-letter cards in the real generated `.apkg` (confirmed via its
      embedded SQLite). Lemma count held at 209, not higher as predicted —
      but the *set* changed as expected: the pre-fix run's single-letter `'e'`
      lemma is gone, `'semi-relevé'` is newly admitted. Net zero on count,
      both intended effects confirmed via `vocabulary` table timestamps.

- [x] **Run an English-video verification.**
      39/39 real MW definitions, 0 duplicates, all fields under 256 chars,
      synonyms on 38/39, antonyms on 11/39. Manually opened the result in
      real Anki — this is how issue #10 (MW synonym parsing breaks on
      Synonym Discussion words like "ask"/"shy") was found; SQLite-level
      checks alone didn't catch it.

- [x] **Commit and tag v0.4.1.**
      Tagged. Commit message expanded from the original suggestion to
      document what was actually verified, not just what changed.

---

## High

- [x] **Language-aware spaCy model selection.** Closes #3, shipped in v0.4.3
      across three commits (b5b0247, cf2dd90, d794211). Verified live: `--language
      fr` alone now selects `fr_core_news_sm` automatically, no manual
      `SPACY_MODEL` override needed; confirmed against real generated cards that
      `allons`->`aller` and `toujours`->`toujours` now resolve correctly (were
      `allon`/`toujour`). Surfaced a separate, real gap during verification —
      `fr_core_news_sm`'s own verb-lemmatization accuracy is inconsistent on some
      conjugated forms (e.g. "sors" vs "sortir") — filed as #13, not part of this
      fix's scope.

- [x] **Resolve the 404-versus-502 distinction in issue #1.**
      Investigated: retried `fr/bonjour` 5x (502, 404, 502, 502, 502) and swept
      18 common French words plus an English control. Finding is more nuanced
      than either original hypothesis: the 502s are **not** French-specific —
      English showed the same flakiness in a comparable sample (3/5 failed) — so
      this is a general dictionaryapi.dev reliability issue, not a broken French
      backend specifically. But filtering the 502 noise out, the coverage gap is
      still real and confirmed: across those 18 French words, every non-502
      response was 404 (0 successes), while English's non-502 responses were 200
      both times. Full data recorded on issue #1. Practical implication: the
      circuit-breaker work below is now relevant to *all* languages, not just
      non-English ones, since the 502 flakiness hits English too.

- [x] **Fix the flaky test.**
      Was misdiagnosed here as SQLite connection leakage. Actual root cause,
      found during the v0.4.1 verification pass: `test_cache_hit_skips_fetch_definition_call`
      seeded the cache via `_cache_set()` (bare-lemma key) while
      `fetch_definitions()` reads via a composite `lemma::language` key — a
      deterministic mismatch, not flakiness (confirmed via 3/3 reproduction in
      isolation, which the connection-leak theory couldn't explain since each
      test already gets an isolated tmp_path DB). Fixed in 332b8fd.

- [ ] **Circuit breaker for failing API sources.**
      A run with 108 failing lookups exceeded 280 seconds — roughly 2.6 seconds
      per failure. After N consecutive failures against one source, stop
      calling it for the remainder of the run and go straight to the fallback.
      Note that concurrent fetching does not substitute for this — five
      threads hitting a dead source at once still waste five timeouts.

- [ ] **WSL path translation for Anki auto-import.**
      `_prompt_import` sends `/mnt/c/...` to Windows AnkiConnect, which cannot
      resolve it. Add a translation mapping `/mnt/<drive>/` to `<DRIVE>:\` when
      running under WSL, detected via `/proc/version` containing "microsoft".

---

## Medium

- [x] **Concurrent definition fetching.**
      Implemented with a `ThreadPoolExecutor` rather than `asyncio` plus
      `aiohttp`. The definition APIs are called through the synchronous
      `requests` library, and every call site downstream of them (MW parsing,
      dictionaryapi parsing, the circuit breaker, WordNet lookups, and the
      translation module's interactive prompt) is also synchronous. Moving to
      `asyncio` would mean either rewriting all of that on an async HTTP
      client or wrapping the existing synchronous code in `run_in_executor`
      calls anyway, which is most of the work of a thread pool with none of
      the benefit. A bounded thread pool gets the same concurrent I/O with a
      much smaller diff: a lemma still results in one `fetch_definition`
      call, just dispatched to a worker thread instead of run inline.
      `DEFINITION_FETCH_WORKERS` (default 5) caps how many run at once, cache
      hits are still resolved sequentially first since a local SQLite read
      has nothing to gain from a thread, and the circuit breaker's shared
      state and the translation module's interactive prompt were both
      audited and locked for real thread safety, not just async-safety. A
      live timing run against 15 uncached English words went from 34.6s at
      `max_workers=1` to 5.3s at the default of 5, a 6.5x speedup, with no
      change in which words were found. See ARCHITECTURE.md's design
      patterns section for the full writeup.

- [ ] **`--force` flag to reprocess a video.**
      Currently `check_video_not_processed` warns and exits with no override.
      The CLI already tells the user `--force` exists. Either implement it or
      remove the message.

- [ ] **Migrate CLI from argparse to Typer.**
      Better help output, automatic type validation, clean subcommands:
      `tango run`, `tango review`, `tango backlog`, `tango languages`. Do this
      after the functional fixes so the diff is not mixed with behaviour
      changes.

- [ ] **Additional dictionary sources for non-English languages.**
      Requires its own ADR. Candidates: Wiktionary raw MediaWiki API (not the
      REST definition endpoint, which is English-only and experimental),
      Larousse for French, DWDS for German, RAE for Spanish. Accept that some
      languages will remain transcript-only.

- [ ] **Separate synonym and antonym API source.**
      Antonyms are empty on roughly 99 percent of cards. WordNet helps for
      English only. Investigate Datamuse API — free, no key, has an
      `rel_ant` parameter for antonyms.

- [ ] **Dockerfile.**
      For cloud deployment and reproducible environments. Base
      `python:3.11-slim`, layer system deps, pip install, spaCy model download.

- [ ] **Dependency size reduction.**
      Base install should not pull PyTorch. Verify the `[translation]` optional
      group actually isolates argostranslate. Consider three tiers: core
      (~100MB), `[nlp]` adding spaCy (~600MB), `[translation]` adding
      argostranslate (~2.5GB).

- [ ] **Outlined pill CSS.**
      Synonyms and antonyms currently render as filled pills. The intended
      design is transparent background with a coloured border. Verify whether
      the change was ever applied to `cards.py`.

---

## Low

- [ ] Hyphenated compound handling in the deck fuzzy match — `semi-relevé` is
      now admitted by the NLP filter but the fuzzy matcher has not been tested
      against hyphenated fronts.
- [ ] `DB_PATH` should default to an absolute path so running from different
      directories does not create multiple database files.
- [ ] Investigate whether `token.is_alpha` removal admits any junk in languages
      other than French. Only one video was tested.
- [ ] Audio on cards. Requires a TTS source and genanki media file handling.
- [ ] User vocabulary profiles from Anki review history. Requires at least 30
      days of review data before the model has anything to learn from.
- [ ] Video recommendations by vocabulary domain and level. Depends on the
      profile work above.
- [ ] Web UI and FastAPI backend.
- [ ] Google Chrome extension for real-time word surfacing during playback.
- [ ] Support for spaced repetition systems other than Anki. The card
      generation layer in `cards.py` is already isolated enough that swapping
      the output format is a single-module change.
