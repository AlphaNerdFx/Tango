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

- [x] **Multi-language testing pass.** Real pipeline runs, real videos, across 9
      never-before-tested languages (German, Spanish, Portuguese, Japanese,
      Russian, Korean, Chinese, plus fresh French and English runs), rather
      than continuing to reason from French alone. Found two real bugs and
      one major escalation of a known issue.

      **dictionaryapi.dev confirmed broken for every non-English language,
      not just French.** 9/9 non-English languages tested came back at 0%
      definition coverage (French 0/1047, German 0/419, Spanish 0/279,
      Portuguese 0/126, Japanese 0/32, Russian 0/701, Korean 0/119, Chinese
      0/552), against English's 269/274 (98%). Confirmed independently of
      our code with direct requests: `de/Auto` 502, `es/casa` 502,
      `pt/casa` 404, `fr/maison` 404, `ja/水` 404, `en/house` 200. Posted
      to issue #1, which now reads as "only works for English," not
      "coverage varies by language."

      **SQLite cache lock contention was discarding successful lookups.**
      A concurrent English run hit `sqlite3.OperationalError: database is
      locked` inside `_cache_set_key`, which propagated out of
      `fetch_definition()` after it had already built a valid result, so
      `fetch_definitions()`'s executor loop recorded the word as not-found.
      Root cause: schema setup ran on every `_get_db()` call rather than
      once, and DDL takes a stronger lock than a plain write, so
      `DEFINITION_FETCH_WORKERS` concurrent connections were contending
      harder than the actual cache traffic needed. Fixed: schema runs once
      per `DB_PATH`, connections use a 30s busy timeout instead of
      sqlite3's 5s default, and both cache functions fail soft on a
      `sqlite3.Error` instead of propagating.

      **Chinese vocabulary extraction returned zero words.** Closes #15.
      `zh_core_web_sm` leaves `token.lemma_` empty for every token, since
      Chinese has no inflectional morphology for a lemmatizer to normalize
      away. The vocabulary dict is keyed by the lemma, so every token
      failed `_is_valid_lemma`'s length check and the entire language
      silently produced nothing, no error raised anywhere. Added
      `_effective_lemma()`, falling back to `token.text` when `lemma_` is
      empty. Found a second, related gap in the same investigation: the
      two-character minimum (added to reject French lemmatization debris)
      isn't universal -- Chinese has many legitimate one-character words
      (人, 大, 水, 好, 不). Added a CJK Unicode-range exemption for single
      characters specifically, while still rejecting single Latin letters.
      Live-verified: the same video went from 0 to 552 unique lemmas after
      the fix, including 67 confirmed-real single-character words.

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

- [x] **Circuit breaker for failing API sources.** Closes #4, shipped in v0.4.4.
      A run with 108 failing lookups exceeded 280 seconds — roughly 2.6 seconds
      per failure. After N consecutive failures against one source, stop
      calling it for the remainder of the run and go straight to the fallback.
      Note that concurrent fetching does not substitute for this — five
      threads hitting a dead source at once still waste five timeouts.

- [x] **WSL path translation for Anki auto-import.** Closes #5, shipped in v0.4.4.
      `_prompt_import` sends `/mnt/c/...` to Windows AnkiConnect, which cannot
      resolve it. Added a translation mapping `/mnt/<drive>/` to `<DRIVE>:\` when
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

- [x] **`--force` flag to reprocess a video.** Closes #6.
      `check_video_not_processed` used to warn and exit with no override, even
      though the CLI's own warning message told the user `--force` existed.
      Added the flag: when set, `_run_pipeline` skips the
      `check_video_not_processed` call entirely and logs that it is
      reprocessing. `mark_video_processed` already upserts on `video_id`, so
      the second run's record replaces the first rather than conflicting.
      Deck-level duplicate detection is unaffected, so words already in the
      target deck are still skipped; `--force` only bypasses the video-level
      "already ran this one" guard.

- [x] **Card GUIDs collide across languages for cognate lemmas.** Closes #14.
      `cards.py` built every note's stable GUID from `(lemma, video_id)` only.
      Reprocessing the same video in a second language silently dropped a
      card whenever a lemma happened to be spelled the same in both
      languages (French and English share several: `train`, `solution`,
      `simple`, `machine`, `sandwich`, `page`, `change`), because Anki
      matched the new note's GUID to an existing note from the earlier
      language's run and treated it as already present. Found while
      verifying the `--force` fix live: an 08-05 English rerun of a video
      previously processed in French on 07-19 came up 8 cards short of its
      130-word vocabulary list, with no error or warning. Fixed by folding
      the resolved language into the GUID: `guid_for(lemma, video_id,
      language)`. Existing cards keep their current GUIDs; this only
      prevents new collisions going forward.

- [ ] **Migrate CLI from argparse to Typer.**
      Better help output, automatic type validation, clean subcommands:
      `tango run`, `tango review`, `tango backlog`, `tango languages`. Do this
      after the functional fixes so the diff is not mixed with behaviour
      changes.

- [x] **Additional dictionary source for non-English example sentences.** Closes #1's
      example-sentence gap (partially -- see below), also closed #14 along the way.
      The earlier assumption that Wiktionary's REST definition endpoint was
      "English-only and experimental" turned out to only be half right: the
      endpoint itself only works reliably against the English Wiktionary
      edition (French, German, and Spanish editions all returned 501 in
      testing), but querying the ENGLISH edition for a foreign word still
      works, because an English Wiktionary page carries every language that
      word appears in as its own section. A French word's page has a real
      "fr" section with genuine French example sentences, even though the
      site itself is the English edition.

      This only fixes example sentences, not definitions, part of speech,
      synonyms, or antonyms. The endpoint's "definition" text is an English
      gloss (`chat` -> `cat`), which CLAUDE.md 3.3 does not permit writing
      into a native-mode Definition field, so that text is discarded; only
      the `examples` array is used. dictionaryapi.dev's near-zero coverage
      for non-English definitions (issue #1's core finding) is unchanged.

      Wired into `fetch_definitions()` two ways: `fetch_definition()` tries
      Wiktionary when a definition IS found but has no example (a real but
      narrow case), and the new `_fetch_definition_or_fallback_example()`
      wrapper tries it again when NO definition is found anywhere, storing
      the result in `DefinitionBatchResult.not_found_examples` so the
      resulting fallback card gets a real dictionary example instead of an
      empty field. The second path is the one that matters in practice --
      near-zero-coverage languages fail with "no definition" far more often
      than "definition found, example missing."

      Live-verified against the same French video from issue #1's original
      report (`2yHn8uc5_-4`): dropped words (no definition, no dictionary
      example, no transcript match) went from 30 to 9, and of the 200
      resulting fallback cards, 111 now carry a real French example sentence
      that previously would have been blank. No Wiktionary rate limiting was
      observed during the real run despite earlier manual testing hitting a
      429 after roughly a dozen rapid requests -- the pipeline's natural
      pacing (MW and dictionaryapi.dev calls interleaved with Wiktionary
      ones, 5 concurrent workers) turned out not to trigger it in practice.

      A genuine per-language dictionary source (Larousse for French, DWDS
      for German, RAE for Spanish) remains the only way to fix definitions,
      part of speech, synonyms, and antonyms for non-English languages, and
      would need its own ADR given the per-language maintenance burden.
      Not attempted here.

- [x] **French verb lemmatization accuracy (#13), partially fixed.**
      `fr_core_news_sm`'s POS tagger inconsistently misclassified common
      conjugated verbs depending on sentence context (`sors` tagged NOUN or
      ADV instead of VERB), and a wrong POS meant the lemmatizer never
      attempted verb normalization, producing duplicate cards for the same
      verb ("sortir" and "sors" as two separate cards).

      Confirmed directly with real spaCy calls: `fr_core_news_md` got all 3
      of issue #13's original reproduction sentences right, consistently,
      where `sm` got 2 of 3 wrong. Pinned French to `md` in `SPACY_MODELS`.
      Live-verified against the same video: "Sortir" now appears once,
      "Sors" no longer appears at all.

      Does not generalize automatically to the other 23 supported
      languages. A parallel test against Spanish's analogous "juego" (play,
      verb/noun homograph) showed `md` trade one wrong POS for a different
      one (NOUN in `sm` became PROPN in `md`, still wrong) rather than
      fixing it -- the size/accuracy tradeoff is a per-language-model
      training-data question, not something one global rule answers for
      every language. Added `SPACY_MODEL_SIZE_OVERRIDE` as a general,
      language-agnostic env var so anyone hitting a similar problem in
      another language can test a larger model without a code change,
      instead of us guessing which of the other 23 would actually benefit.

      Does not fix everything issue #13 reported. "Joue" (play, present
      tense) still doesn't normalize to "jouer" even with `md`, despite
      spaCy correctly tagging it VERB with complete
      `Mood=Ind|Tense=Pres|VerbForm=Fin` morphology -- confirmed this is a
      lemmatizer lookup-table gap independent of POS tagging, and confirmed
      the same category of bug in Spanish ("cocino" stays "cocino" instead
      of "cocinar" in both `sm` and `md`). Not something a bigger model or
      more of our own code fixes; it's the underlying spaCy language
      pipeline's own lemmatizer data. Issue #13 stays open for this half.

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

- [x] **CI Python version matrix.** Closes #7. `.github/workflows/ci.yml` ran a
      single job on Python 3.10 only, while CONTRIBUTING.md claimed a 3.9-3.12
      matrix that had never actually existed in this repo's history. Added
      `strategy.matrix.python-version` with `fail-fast: false` so one failing
      version doesn't hide results from the others.

      Initially matrixed `["3.9", "3.10", "3.11", "3.12"]`. Verified 3.10,
      3.11, and 3.12 locally first (529 passed, 22 deselected, identically,
      on all three) since 3.9 wasn't installable in this sandbox without
      root, then pushed and let the real CI confirm 3.9. It failed for a
      real reason, not a fluke: spaCy 3.8's `thinc` dependency has published
      no wheel for Python 3.9 since `thinc>=8.3.10` (all of them declare
      `Requires-Python >=3.10`), so `spacy>=3.8,<4.0` cannot install on 3.9
      at all. `pyproject.toml`'s `requires-python = ">=3.9"` and the
      README's "Python 3.9+" were never actually true once spaCy moved past
      that point; this had simply never been tested before. Dropped 3.9:
      `requires-python` is now `>=3.10`, matrix is `["3.10", "3.11",
      "3.12"]`, README/CONTRIBUTING/wiki corrected to match. Confirmed via
      the real CI run: all three remaining versions pass.

- [x] **Guided API key setup for non-technical users.** Closes #9. Onboarding
      told users to run `cp .env.example .env`, but `.env.example` had never
      existed in this repo despite `.gitignore` explicitly un-ignoring it
      (`!.env.example`) -- that command has been broken since the project's
      first commit. Created a real, accurate `.env.example` covering every
      environment variable actually read by `config.py`/`language.py`/
      `translation.py` (several current ones, like `DEFINITION_FETCH_WORKERS`
      and `SPACY_MODEL_SIZE_OVERRIDE`, were never documented anywhere; a
      couple of documented ones, like the wiki's `SPACY_MODEL`, described a
      config value that no longer exists since the #3 language-aware fix).

      The design question the issue flagged, whether MW_API_KEY is actually
      required, resolves from the code directly: `_fetch_from_mw` returns
      `None` immediately when the key is unset, dictionaryapi.dev is the
      always-on fallback, and nothing in the pipeline requires any API key to
      run. README's table said "Required: Yes" for MW_API_KEY; corrected to
      "No" there and in the wiki, along with fixing a separate, real
      misconception in both places that `LANGUAGE`/`DEF_LANG` belong in
      `.env` -- they're `make run` command arguments, read before `.env` is
      ever loaded, so setting them in `.env` silently does nothing.

      Added `--setup` (`make setup`) as a real guided wizard: creates `.env`
      from the template if missing, detects an already-set key and skips
      straight past the prompt, otherwise explains the key is optional,
      links registration, and validates the pasted value (rejects empty or
      whitespace-containing input, a common paste mistake) before writing it
      with `python-dotenv`'s `set_key` rather than hand-rolled text editing.

      Found two more real, unrelated bugs while placing the new flag
      correctly: `--list-languages` (and now `--setup`) were unreachable,
      since the `--video-id` requirement check ran before them and exited
      first for every standalone mode; and `make help` crashed with a shell
      syntax error partway through (`Makefile:300: Unterminated quoted
      string`) from two `@printf` lines missing a newline between them,
      never noticed because nobody looks past the point their own command
      appears in the list. Both fixed. Live-verified all three wizard paths
      (decline, accept-and-write, already-set-detection) with real `.env`
      files, not just mocked tests.

- [x] **Stop implying a specific proxy is recommended.** Closes #8. SESSION.md
      6.5 already recorded that a Webshare free-tier proxy made transcript
      extraction worse, not better (repeated 429s through the proxy,
      succeeded without it, since free-tier datacenter IPs get blocked more
      aggressively than residential ones), but README and the wiki's config
      page still documented `WEBSHARE_USERNAME`/`WEBSHARE_PASSWORD` first and
      pointed at webshare.io's signup page, only mentioning the generic
      `PROXY_HTTP_URL`/`PROXY_HTTPS_URL` variables as an afterthought. The
      wiki's troubleshooting page already had this right from an earlier
      session; config and README hadn't been brought in line with it.

      Reordered both so the generic proxy variables come first with the
      actual guidance (most users never need one, since default traffic
      already comes from a residential IP; bring your own reputable
      paid/VPN proxy if you're genuinely rate-limited; this project doesn't
      recommend a specific provider), and Webshare-specific variables are
      now framed as "if that's what you're already using," not the
      suggested starting point.

      Not attempted: the issue's third, explicitly optional idea, a fallback
      path to paste/upload a transcript file directly and skip
      `youtube-transcript-api` entirely for anyone blocked outright. That's
      a real feature (new CLI flag, `transcript.py` changes, tests), not a
      documentation fix, and deserves its own decision rather than being
      bundled into a docs-only issue.

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
