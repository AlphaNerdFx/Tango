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
- [x] 418 unit tests, no external dependencies in the default run
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

- [ ] **Verify the WordNet fix is actually applied.**
      Read `src/pipeline/definition.py`. Confirm the WordNet call is gated on
      `language == "en"` and passes `lemma`, not `query_lemma`. Confirm
      `TestWordNetLanguageGuard` exists in `tests/test_definition.py`. An
      earlier session reported the file unchanged. If the fix is missing, the
      four guard tests will fail, which is correct behaviour.

- [ ] **Verify `cards.py` state against HEAD.**
      `git diff HEAD -- src/pipeline/cards.py`. Determine whether the
      `added_lemmas` deduplication guard and the outlined-pill CSS are present.
      An earlier session reported zero changes to this file.

- [ ] **Re-run Step 5 verification after the lemma fixes.**
      The last real-world run predates the `_is_valid_lemma` fix and produced a
      card with the front "E". Re-run against the same French video and confirm
      no single-letter cards. Compare the extracted lemma count against the
      previous 209 — compounds and contractions should now be admitted, so the
      count should rise.

- [ ] **Run an English-video verification.**
      The French run cannot exercise definition, example, synonym, or antonym
      paths because dictionaryapi.dev returns nothing for French. An English
      video is the only way to validate those fields. Check specifically:
      definitions are single-sentence, both dictionary example fields populate,
      synonyms and antonyms appear, all fields respect the 256-character cap.

- [ ] **Commit and tag v0.4.1.**
      Only after all of the above pass. Suggested message:
      `fix: proper noun and single-letter filtering, WordNet language guard, DICT_API_BASE URL, deduplication guards`

---

## High

- [ ] **Language-aware spaCy model selection.**
      Currently every non-English video is processed with an English model.
      This is the highest-impact correctness bug in the codebase. Full patch:

      1. Add `SPACY_MODELS` dict to `language.py` mapping BCP-47 codes to model
         names: `en_core_web_sm`, `fr_core_news_sm`, `es_core_news_sm`,
         `de_core_news_sm`, `it_core_news_sm`, `pt_core_news_sm`,
         `nl_core_news_sm`, `ru_core_news_sm`, `pl_core_news_sm`,
         `ro_core_news_sm`, `el_core_news_sm`, `da_core_news_sm`,
         `sv_core_news_sm`, `nb_core_news_sm`, `fi_core_news_sm`,
         `lt_core_news_sm`, `hr_core_news_sm`, `uk_core_news_sm`,
         `sl_core_news_sm`, `mk_core_news_sm`, `ca_core_news_sm`,
         `ja_core_news_sm`, `zh_core_web_sm`, `ko_core_news_sm`
      2. Add `get_spacy_model(language_code)` with base-code fallback so
         `fr-CA` resolves to `fr`, raising `SpacyModelUnavailableError` for
         languages spaCy does not ship a model for
      3. Change `nlp.py` to cache one model per language in `_nlp_models: dict`
         rather than a single global, and accept a `language` parameter in
         `process_transcript()`
      4. Pass `language_code` through from `__main__.py`
      5. Change the Makefile `spacy-model` target to accept `SPACY_LANG=fr`
      6. Remove the now-dead `SPACY_MODEL` constant from `config.py` and `.env`
      7. Add tests: English maps correctly, French maps correctly, regional
         variants fall back to base, Chinese variants share one model,
         unsupported languages raise, error message lists supported codes,
         model cache is per-language

- [ ] **Resolve the 404-versus-502 distinction in issue #1.**
      `fr/bonjour` returned 502 while `fr/eau`, `fr/chat`, and `fr/maison`
      returned 404. Those are different failures. 404 means the endpoint works
      and the word is absent. 502 means the upstream server broke. If 502s
      recur intermittently across different words, the French backend exists
      and is unhealthy, which is a different problem with a different fix than
      "no coverage". Retry `fr/bonjour` several times spaced out, test five
      more common French words, record the distinction in the issue.

- [ ] **Fix the flaky test.**
      `test_cache_hit_skips_fetch_definition_call` failed once in five runs.
      Probable cause: `_get_db()` opens a connection per call and `with conn:`
      commits without closing. Add `try/finally: conn.close()` or refactor to a
      context manager that closes.

- [ ] **Circuit breaker for failing API sources.**
      A run with 108 failing lookups exceeded 280 seconds — roughly 2.6 seconds
      per failure. After N consecutive failures against one source, stop
      calling it for the remainder of the run and go straight to the fallback.
      Note that async I/O does not substitute for this.

- [ ] **WSL path translation for Anki auto-import.**
      `_prompt_import` sends `/mnt/c/...` to Windows AnkiConnect, which cannot
      resolve it. Add a translation mapping `/mnt/<drive>/` to `<DRIVE>:\` when
      running under WSL, detected via `/proc/version` containing "microsoft".

---

## Medium

- [ ] **Async definition fetching.**
      Replace sequential `requests` calls with `asyncio` plus `aiohttp`.
      Expected to reduce a 100-word video from 2-12 minutes to under 3 minutes.
      Must preserve the per-source rate limiting and the SQLite cache
      behaviour.

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
