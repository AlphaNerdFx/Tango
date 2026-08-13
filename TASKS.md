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
      rather than trusting a hardcoded number here. CONTRIBUTING.md's stale
      "411" claim was recorded here as "corrected during this session" — it
      was not, and still said 411 until 8 August. The note claiming the fix
      outlived the fix it claimed, which is the trailing-edge pattern in its
      purest form.)
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

- [x] **The deck duplicate check could not see most cards, including every
      card this pipeline creates.** `get_card_fronts()` read the note field
      literally named `Front`; the generated model's first field is named
      `Word`, so a deck built by Tango returned zero fronts and every word
      from an earlier run came back NEW — definitions re-fetched, duplicate
      cards generated, and Anki importing rather than merging them because
      the GUID carries the video ID.

      Not limited to our own cards. Measured against the real collection:
      `LangTest_fr2` 0 of 1036 notes visible, `English_Test` 0 of 1041, and
      the hand-built `French` deck 305 of 2172 across 12 note types — most
      real decks are not on Anki's stock Basic type. Reprocessing the video
      already imported into `LangTest_fr2` went from 1054 NEW to 1050 SKIP /
      4 QUEUE / 0 NEW.

      Now reads the lowest-`order` field, which is what Anki itself uses as
      a note's identity, so it is note-type agnostic rather than hardcoding
      a second name. HTML stripping came with it and was measured first:
      across 7453 real notes it lowers French's average word count 2.11 →
      1.93, so no deck tips into `_is_sentence_structured_deck` and silently
      loses fuzzy matching. See ARCHITECTURE.md 8.22.

      Found while starting the hyphenated-fuzzy-match item below, which is
      downstream of this function and was worth doing in this order.

Everything below was done for v0.4.1. See CLAUDE.md/ARCHITECTURE.md for
current state.

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

- [x] **ADR-008: per-language dictionary sources, evidence-based candidate
      evaluation.** Followed up on issue #1's 9/9 non-English 0%-coverage
      finding above by evaluating real alternatives instead of continuing to
      reason from dictionaryapi.dev alone: PONS (rejected, bilingual pairs
      and a 1000-req/month cap), Open Multilingual Wordnet (real synonyms
      for 18 languages, but `.definition()`/`.examples()` stay in English
      regardless of the `lang=` parameter, and antonyms are effectively
      empty for non-English), and Wiktionary's raw wikitext API (real
      native-language definitions confirmed for French/German/Russian, but
      needs a per-language-edition parser). Full evidence and candidate
      writeups in `docs/ADR-008-per-language-dictionary-sources.md`.

      **Option A shipped: OMW synonym/antonym supplementation.** Extended
      `_wordnet_synonyms_antonyms()` to call OMW's `lang=` parameter for the
      18 covered languages, gated by a new `_OMW_LANGUAGE_CODES` map.
      Same class of bug as the earlier Wiktionary-example fix on issue #1:
      the first implementation only reached the found-definition code path,
      which almost never executes for a language dictionaryapi.dev can't
      find anything for. Fixed by threading the same OMW lookup into the
      not-found fallback path. Live-verified against the French video from
      issue #1: fallback cards with real synonyms went from 0 to 767 of 972.

      **Option B prototyped, not shipped.** Real native-language
      definitions are retrievable (French 10/15, German 8/14, Russian
      10/14 on real vocabulary, not single spot-checked words), but three
      problems surfaced that need real design work first: Wikimedia's
      anonymous rate limit triggers a 429 after ~8-10 requests regardless
      of per-request delay; some entries return leftover template syntax as
      a "successful" extraction rather than failing cleanly; and nested
      templates break single-pass regex stripping. Filed as issue #16
      rather than folded into this pass.

- [x] **Three follow-up defects in the shipped OMW synonyms.** All found by
      reading a real run's card output rather than by any test, and all
      silent -- the pipeline reported success in every case.

      **Synonyms were ranked alphabetically.** The pooled list was sorted
      before the five-item cap, so spelling decided which survived instead
      of relevance. WordNet returns senses most-common-first, so the cut
      systematically discarded the useful words: 207 of 972 cards affected,
      "aujourd'hui" losing "maintenant" while keeping "de notre temps", and
      "faire" losing "mettre"/"organiser" while keeping the vulgar
      "caguer"/"déféquer". Now preserves synset order through the cap.

      **nltk's WordNet reader is not thread-safe.** It seeks and reads a
      shared file handle and loads each language lazily on first use.
      Under `DEFINITION_FETCH_WORKERS` threads, concurrent lookups raised
      `AssertionError`, which the function's own `except` swallowed into
      "no synonyms" -- so the only symptom was a different subset of cards
      missing synonyms on every run of the same video (767/764/730 across
      three). Warm-up now happens under the lock per language, and the
      reads are serialized too; warming alone fixed 9 of 10 words but not
      the 10th. Serializing measured ~6x faster than the contended version
      (11ms vs 68ms per 80 lookups), since these are local lookups and the
      thread pool exists for network I/O.

      **Senses were mixed.** Pooling three synsets merged unrelated
      meanings and crossed parts of speech ("prêt" -> "emprunt", a loan
      noun, beside "rapide", quick adjective). Non-English now reads the
      top two senses: a provisional setting, measured at 64% / 75% / 79%
      card coverage for 1 / 2 / 3 senses. English deliberately stays at
      three -- narrowing it was a straight regression (14/15 -> 9/15 words
      with any synonym), since many English words have a top synset
      containing only the word itself. Expected to be revisited when a
      real per-language source lands (issue #16); a test pins the setting
      from both sides so changing it fails loudly.

- [x] **Per-word "No definition found" flooded the log.** Closes #17.
      dictionaryapi.dev has no usable non-English data, so every word
      missed and every miss logged its own WARNING -- 1047 consecutive
      identical lines for one French video, burying the errors that
      mattered (the SQLite lock bug above was exactly that shape).
      Non-English misses now log at DEBUG and the batch emits one WARNING
      naming the language and cause; English keeps its per-word warning,
      where a miss is genuinely unusual. Verified on 60 real French words:
      60 per-word warnings became 0 plus 1 summary, leaving the
      dictionaryapi 429s and the circuit-breaker trip visible rather than
      buried.

- [x] **Transcript examples were searched by lemma, not surface form.**
      The "Example from Youtube Video" field looked for the lemma, but
      transcripts contain inflected forms: a French video says "sais", the
      lemma is "savoir", and "savoir" appears nowhere in the text. So a
      word extracted *from* the transcript then failed to find the very
      sentence it came from -- 137 of 1036 cards, all infinitives. The #13
      lemma fix had slightly worsened it, since correcting "joue" to
      "jouer" removed a coincidental match.

      `process_transcript()` now optionally records the forms each lemma
      actually took, as an out-parameter so existing callers and their
      tests are untouched. `_find_in_snippets()` tries the lemma first,
      since where it does appear it gives the cleanest sentence, then
      falls back to those forms. Standard cards are backfilled too, not
      just fallback ones. Live-verified: 87% -> 100% (1040 of 1041), words
      dropped for having nothing to show 5 -> 0.

      Worth recording because the shape of the bug invited the wrong
      conclusion: those cards were not fabricated. Every field is sourced
      -- vocabulary from the transcript via spaCy, definitions and
      examples from the index, synonyms from OMW. It was a matching
      failure, and "savoir" now carries "déjà me faites pas croire que
      vous savez" straight from the video.

- [x] **Verified the dictionary generalises past French, and found where it
      does not.** Measured against real deck vocabulary from previously
      generated videos rather than sampled words:

      | language | lemmas | definitions | examples | synonyms | antonyms |
      |---|---|---|---|---|---|
      | German | 384 | 91% | 84% | 60% | 51% |
      | Russian | 420 | 91% | 72% | 72% | 46% |
      | English | 274 | 100% | 91% | 43% | 31% |

      German and Russian matter most: OMW covers neither, so they had no
      synonyms at all before. Both also carry far richer antonyms than
      French -- 51% and 46% against 20%. An earlier claim in this session
      that antonym coverage was "at the data ceiling" was measured on
      French alone and does not generalise; that was corrected.

      **English is the exception and should not be built.** A live run
      produced 273 of 274 definitions from Merriam-Webster and consulted
      the index zero times; the single MW miss was "Momentic", a brand
      name the index lacks too. Its first-sense definitions are also often
      archaic, since English Wiktionary orders senses historically:
      "may" -> "To be strong; to have power (over)". `--build-dictionary
      en` now warns and asks for confirmation rather than silently
      downloading 475 MB to change nothing. Advisory, not a refusal.

      Also required a per-language download URL override: kaikki publishes
      most languages under a uniform path but English, its primary
      extraction, lives elsewhere and the uniform path 404s for it.

- [x] **Interrupted dictionary builds left truncated archives behind.**
      Found by killing a French rebuild mid-download, which orphaned a
      partial file; a real one would be hundreds of MB. Failed and
      interrupted downloads now clean up after themselves, and
      `KeyboardInterrupt` is handled separately since it is not an
      `OSError` and Ctrl-C during a multi-minute download is ordinary
      usage. The same interrupt confirmed the atomic index swap works --
      all three built indexes stayed intact and queryable.

      README also corrected in the same pass: it claimed "Other languages
      use dictionaryapi.dev natively", describing a source measured at 0%
      for every non-English language tested. It now documents the
      dictionary as a required non-English setup step with the measured
      per-language coverage table.

- [x] **Offline Wiktionary dictionary: non-English definitions, finally.**
      Closes the core of #1 and supersedes #16's API-plus-proxy plan.
      dictionaryapi.dev has no usable non-English data, so every
      non-English card shipped with "No definition found" -- 0 of 1047
      French words, 0 for all 9 non-English languages tested.

      Built from wiktextract output published per language by kaikki.org,
      from *that language's own* Wiktionary edition, so the glosses are
      native rather than the English glosses the REST endpoint returns
      (which is why 8.13 never fixed definitions).

      **Bulk data, not the live API, and not proxies.** ADR-008 scoped
      querying the raw wikitext API per word. Wikimedia 429s after roughly
      8-10 anonymous requests regardless of pacing, and a video needs
      100-1000+ lookups. The proposal to route around that with rotating
      proxies was rejected: it circumvents a rate limit rather than
      complying with it, against Wikimedia's bot policy, and reverses the
      reasoning already recorded in #8 and ADR-008 for rejecting scraping
      sources. Downloading once makes the question moot, and additionally
      skips recursive template stripping and garbage-extraction detection
      because wiktextract resolved those upstream.

      **Layered with OMW rather than replacing it.** Measured on a real
      958-lemma French deck, the index covers synonyms at 49% against
      OMW's 76%, so OMW keeps first claim and the index fills in behind.
      Reading that number the other way would have traded better data for
      worse. The index does supply antonyms, which OMW cannot for any
      non-English language.

      Live-verified on the French test video: definitions 0% -> 95%,
      class/POS 0% -> 95%, 1st example 41% -> 93%, 2nd example 27% -> 71%,
      synonyms 76% -> 83%, antonyms 0% -> 20%. Cards 958 -> 1036, words
      dropped for having nothing to show 83 -> 5. The residual ~5% are
      transcription noise and proper nouns, near the practical ceiling.

      Optional by design: with no index, behaviour is exactly as before,
      pinned by a test fixture because the index lives on disk and would
      otherwise make tests pass in CI and fail locally. Indexes are
      gitignored (323 MB for French) and rebuilt with
      `make dictionary LANGUAGE=<code>`.

- [x] **Second Wiktionary example was fetched then discarded.** The card
      model has two example fields but the fallback path kept only
      examples[0], so the second was always blank while the data had
      already been parsed -- same class of bug as the original Wiktionary
      miss on #1. Wiktionary had a second example for 25 of 45 real French
      words. Now populated on 27% of cards, up from 0%.

- [x] **Verb lemmas spaCy's POS-blind lookup got wrong.** Closes #13.
      spaCy's lemma lookup is keyed on the surface form and is not
      POS-aware, so verb forms that are also nouns took the noun's lemma
      despite being tagged VERB: "joue" (plays / cheek) stayed "joue", and
      the OMW lookup keyed on it returned synonyms for *cheek*. Measured
      at 5 of 14 common regular -er verbs. Not fixable with a bigger model
      (md fails identically), spaCy's own rules (no "e" -> "er" entry) or
      a different lemmatizer (simplemma scores identically) -- all three
      tested rather than assumed. Fixed with a per-language fallback table
      that fires only when the model returned the surface form for a VERB
      and validates the candidate against the model's own vocabulary, so
      "être" cannot become "êtrer". French is the only verified entry.

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

## High — next up

Ordered by value. The first three are card quality, which is where the
remaining user-visible value is; the rest is measurement and hygiene.

- [x] **Sense selection by part of speech.** Shipped. See ARCHITECTURE.md
      8.29 for the full measurement; 8.28 is the word-overlap attempt this
      replaces.

      Measured on three real videos before anything was written: of 1209
      lemmas found in the index it changes 60 picks, roughly 39 better
      against 14 worse (fr 13/3, de 18/9, ru 8/2) — the reverse of
      word-overlap's 1 better to 14 worse. 404 of the 1209 have more than
      one row, so ambiguity affects a third of all cards.

      `super` no longer defines "Supercarburant" for a video using it to
      mean "very", `marcher` is the verb rather than "démarche", and the
      `name` rule stopped Russian `вид` defining a river in Germany and
      `близкий` an island in the Kara Sea. Verified in real generated cards,
      not only in tests.

      The cache key moved with it (`lemma::language::pos`), because 8.27's
      lesson is that cached rows outlive the fix that corrected them.
      Verified by mutation: five separate reversions each fail a test.

- [ ] **Inflection-pointer glosses reach cards as definitions.** Found while
      measuring 8.29, filed rather than folded into it. German Wiktionary
      indexes every inflected form as its own entry, so a card can read
      "1. Person Singular Indikativ Präsens Aktiv des Verbs haben" — a
      pointer to another word, not a definition. Measured on real videos:
      18 of 342 German cards, 10 of 663 Russian, 3 of 204 French.

      Not caused by POS selection and not fixed by it — the count barely
      moves either way (de 18→19, fr 3→2, ru 10→10), which is why it is a
      separate concern. wiktextract tags these senses `form-of` and records
      the word they point at, so the fix is robust and language-agnostic
      rather than a per-language regex over gloss text: store the flag, then
      prefer a real definition among the POS-matching rows.

      Costs a schema bump plus a rebuild per language, so it pairs naturally
      with ADR-009 phase 1 (IPA and audio), which needs the same. Better
      still, `form_of` names the target word, so `glaube` could instead
      resolve to `glauben`'s definition rather than being skipped.

- [ ] **Antonyms, the weakest field everywhere.** Native German 59%, French
      23%, and cross-language collapses to 3% because 3.3 keeps them in the
      transcript language. ConceptNet's bulk dump is the evaluated candidate:
      free, no key, CC BY-SA, one 475 MB download covering every language,
      real French antonyms for `grand` (petit, court, faible, minime...), and
      9 of 12 languages sampled returned data. Its live API is 502 on every
      endpoint, so bulk is the only route — which is what the rate-limit
      constraint required anyway. Needs the OMW-style filtering: drop
      self-references, cross-language leakage and junk.

- [ ] **Filler-word cards.** `Ah`, `Bah`, `Ouai`, `Euh`, `Tss` — 3.4% of
      cards, and the ones that look broken to a user. Needs a per-language
      stoplist of filler sounds, not a POS rule: `Bonsoir` is also `INTJ` and
      is worth learning.

- [ ] **ADR-009 phase 1: IPA and Commons pronunciation audio.** 91.5% of
      German entries carry IPA and 65.7% carry a Wikimedia Commons audio URL,
      in data `wiktdata.py` already downloads. The index stores neither, so
      it is a schema change plus a rebuild rather than a new source. Highest
      value per unit of work in the whole media proposal. Fields must be
      APPENDED at index 10+ (3.2 is positional), and keeping MODEL_ID while
      changing the field list is a notetype schema change that must be tested
      against a collection with real review history.

- [ ] **The sweep needs Anki, and does not say so.** All 16 rows failed with
      "Anki is not running. All words written to backlog" because Anki was
      closed. The deck check needs AnkiConnect even though the sweep never
      imports. Either check up front and fail with a clear message, or bypass
      the deck check for measurement runs.

- [ ] **Add a `--no-cache` flag for measurement runs.** The sweep exists to
      tell the truth about the pipeline and a warm cache is what stops it: a
      corrected sweep still showed Russian examples on German cards because
      it read rows written before the fix. `fetch_definition` already takes
      `use_cache`; nothing exposes it. ARCHITECTURE.md 8.27.

- [ ] **Consider a cache key carrying both languages.** Today it is
      `lemma::target_language`, so a row contaminated by a cross-language run
      cannot be identified from the key — invalidation had to reconstruct it
      by joining the vocabulary table back to each video's language. Both
      cache-poisoning incidents would have been a one-line delete.

- [ ] **Build the missing dictionary indexes.** `make doctor` reports es, ja,
      ko, pt and zh as having a spaCy model but no index, which is exactly
      the state that yields definition-less cards. `make dictionary
      LANGUAGE=es` each. Cheapest large win available per language.

- [ ] **A test per hard constraint.** The 3.3 violation shipped because
      nothing pinned it, and it was caught by a coverage sweep noticing an
      impossible number. 3.1 (MODEL_ID/DECK_ID), 3.2 (field order) and 3.3
      (field language) should each fail loudly rather than rely on review.

- [ ] **Re-run the sweep and record the corrected baseline.** Blocked on the
      two items above it; the numbers in SESSION.md section 5 predate the 3.3
      fix and overstate cross-language example and synonym coverage.



- [x] **Cross-language mode violated CLAUDE.md 3.3.** Examples, synonyms and
      antonyms were taken from the target-language entry, so a German video
      with `--def-lang ru` shipped Russian example sentences. Found by the
      first full coverage sweep, not by tests. Gated at all three call sites;
      see ARCHITECTURE.md 8.25. The measured example coverage on
      cross-language rows will DROP on the next sweep — the metric had been
      rewarding the violation.

- [x] **Cross-language runs gave up instead of falling back.** 46 of 459
      cards on a German->English deck shipped "No definition found" while the
      German index had the word. ARCHITECTURE.md 8.26.

- [x] **Merriam-Webster examples were parsed and discarded.** English example
      coverage 14% -> 72% on a full video. Third instance of the
      fetched-parsed-dropped shape.

- [x] **Coverage sweep across language combinations.**
      `scripts/coverage_matrix.py`, 16 combinations for 4 languages. It found
      the 3.3 violation above and the fact that 10 of 12 cross-language pairs
      had been silently producing native output for want of a translation
      model.

- [ ] **ADR-009: card media enrichment (audio, IPA, images).** Written and
      proposed; see `docs/ADR-009-card-media-enrichment.md`. Four phases in
      dependency order, each independently shippable.

      Phase 1 is the clear first move: 91.5% of German entries in the kaikki
      data already carry IPA and 65.7% carry a Wikimedia Commons audio URL —
      real human pronunciation, already licensed, in data we already
      download. The index does not store either field, so it is a schema
      change plus a rebuild, not a new source.

      Phase 2 TTS (Piper, offline, optional group) for example sentences.
      Phase 3 images from Commons, gated to concrete nouns — measured 2 of 5
      usable, with "laufen" returning a coin from the town of Laufen, so a
      relevance gate is mandatory rather than optional.
      Phase 4 video audio needs a ToS decision before any code: downloading
      YouTube audio is contrary to YouTube's terms whatever the local
      copyright position, and it brings yt-dlp plus an ffmpeg system
      dependency. Accepting a user-supplied audio file reuses all the slicing
      work without the download.

      Two constraints the ADR flags for whoever implements: new fields append
      at index 10+ and may never be inserted (3.2 is positional), and keeping
      MODEL_ID while changing the field list is a notetype schema change that
      must be verified against a collection with real review history before
      shipping.

- [x] **Sense selection by part of speech.** Shipped; duplicate of the entry
      under "High — next up" above. ARCHITECTURE.md 8.29.

- [ ] **Add a --no-cache flag for measurement runs.** The sweep exists to tell
      the truth about the pipeline and a warm cache is what stops it: the
      first corrected sweep still showed Russian examples on German cards
      because it read rows written before the fix. `fetch_definition` already
      takes `use_cache`; nothing exposes it. See ARCHITECTURE.md 8.27.

- [ ] **Consider a cache key carrying both languages.** Today it is
      `lemma::target_language`, so a row contaminated by a cross-language run
      cannot be identified from the key -- invalidation had to reconstruct it
      by joining the vocabulary table back to each video's language. Both
      cache-poisoning incidents would have been a one-line delete.

- [ ] **Re-run the sweep and record the corrected baseline.** The numbers in
      hand were taken before the 3.3 fix and overstate cross-language example
      and synonym coverage.



- [x] **Test the three CLI run modes.** Done, and it paid for itself
      immediately: 5 of the first 22 tests failed, all of them one real bug.

      `_run_review` and `_run_backlog` called `fetch_definitions()` and
      `build_package()` with no language at all, taking their `"en"`
      default, so `make review DECK="French"` fetched every French word
      from English sources and built cards tagged English — silently, with
      the run reporting success. The GUID half is worse: `build_package`'s
      language feeds `guid_for()`, so a French review card collided with
      the English card for the same spelling, the collision class issue #14
      closed for the video path and left open on these two. The same call
      sites were also dropping every `not_found_*` channel, so review and
      backlog cards lost the Wiktionary examples, synonyms and antonyms the
      video path has carried since 8.19.

      Both modes now resolve a language the same way the video path does,
      falling back to `"en"` with a warning rather than exiting when a deck
      name carries no language — review has no subtitle track to select, so
      failing hard would break decks that work today. Both branches checked
      live. `__main__.py` 55% → 82%, suite 83% → 88%, 692 passing.
      See ARCHITECTURE.md 8.24.

- [ ] **Antonyms, the one visibly thin field.** Measured on real cards read
      back out of Anki: 23.2% against Definition's 98.6% and Synonyms' 85%.
      German and Russian measure 51% and 46%, so this is per-language, not a
      global ceiling.

      **Fact-checked the "no worthwhile antonym source" claim.** It was
      right about the sources it had actually tested, and wrong as a general
      statement, because one obvious candidate had never been evaluated.

      Verified true: OMW returns essentially nothing for non-English — 5
      French words yield 469 lemmas and **2** antonyms, against English's 238
      lemmas and 25. Verified true: Datamuse works for English (`big` →
      little, small) and returns **empty** for its Spanish vocabulary, so it
      raises the language that needs it least.

      Verified false, and this one was my own theory: that the index was
      discarding sense-level antonyms, since `_joined()` reads only
      `raw["antonyms"]` at the entry's top level. Measured against 40000 real
      kaikki French entries — **0** carry sense-level antonyms; all 1682 are
      top-level and already read. The indexer leaves nothing on the table,
      and 23.2% is Wiktionary's genuine ceiling for French.

      Not evaluated before, and the one that fits: **ConceptNet's bulk
      assertions dump.** Free, no key, CC BY-SA (same licence family as the
      Wiktionary data already shipped), and one 475 MB download covers every
      language at once rather than one download per language — more
      generalizable than the current per-language dictionary, not less.
      Real French antonyms for `grand`: petit, court, faible, minime,
      minuscule, modeste, médiocre, réduit, exigu, bref. Breadth checked
      across 12 languages: 9 returned real antonyms (de, ru, es, ja, zh, pt,
      ko, it, fr); Arabic came back empty on the one word tried.

      Two things to know before building it. Its **live API is currently
      returning 502 on every endpoint including its root**, so an API
      integration is not viable regardless — which is fine, because bulk is
      what the rate-limit constraint demanded anyway, and is why this is not
      a repeat of the Wikimedia 429 problem that killed ADR-008 Option B.
      And the data is **noisy**: `grande` lists itself and `irrelevante` as
      antonyms, `大` lists `郵局`. It needs the same filtering OMW synonyms
      got — drop self-references, cross-language leakage, and cap the list.

- [x] **`make install` was broken.** Both it and `translate-setup` called
      `$(VENV_DIR)/bin/pip`, which does not exist in this venv. The earlier
      note here framed it as "whether the venv or the Makefile is wrong is a
      setup decision" — the diagnosis settles that, and it is the Makefile.
      pip is installed and healthy; only its console script is missing,
      removed by an interrupted `pip install --upgrade pip` that also left
      the orphaned `~ip` and `~ip-26.1.2.dist-info` behind, which is what
      emits the "Ignoring invalid distribution -ip" warning on every pip
      call since. `VENV_PIP` is now `$(VENV_PYTHON) -m pip`, which works
      whenever pip is importable rather than depending on a script an
      installer may or may not have written. The `~ip*` orphans are left in
      place — deleting things inside someone's venv is not a call to make
      while they are away. `rm -rf .tangovenv/lib/python3.10/site-packages/~ip*`
      clears the warning.

- [x] **Stale packaging metadata and a stale proxy recommendation.**
      `pyproject.toml` still declared `version = "0.1.0"` against a v0.4.4
      tag, still told users to download `omw-1.4` (silently ignored by this
      nltk version, corrected in the setup docs back in d657834), and still
      set both lint targets to `py39`, dropped in b1bdb9e once spaCy's thinc
      stopped publishing 3.9 wheels. `config.py` still called Webshare the
      "recommended provider" directly above the variables, long after issue
      #8 established the opposite and corrected the README and wiki.

      All four are the same trailing-edge pattern, frequent enough now to be
      worth naming: a fix updates the places its author remembered, not
      every place the claim lives. The outlined-pill entry and the 524-test
      count were the same shape.

      Raising ruff's target was checked rather than assumed, since it can
      activate more pyupgrade rules: the finding breakdown is identical
      before and after, with only UP045's fixes moving from unsafe to
      safe-fixable.

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
      The "empty on roughly 99 percent of cards" figure this entry used to
      carry predates OMW and the offline index, and is no longer true.
      Measured antonym coverage now: German 51%, Russian 46%, English 31%,
      French 20%. Synonyms are 60-83%. Still worth a dedicated source —
      French in particular lags, and the index is the only antonym source
      for any non-English language, so a language without a built index
      still gets none. Investigate Datamuse API — free, no key, has an
      `rel_ant` parameter for antonyms. **Tested, and rejected on the
      evidence:** `rel_ant=big` returns little/small, `rel_ant=hot` returns
      cold/cool, and the same query against its Spanish vocabulary (`v=es`)
      returns an empty list. It is an English-only answer to a problem whose
      English case is already the best-covered one. See the ConceptNet
      finding under High, which is the generalizable alternative.

- [ ] **Dockerfile.**
      For cloud deployment and reproducible environments. Base
      `python:3.11-slim`, layer system deps, pip install, spaCy model download.

- [ ] **Dependency size reduction.**
      Base install should not pull PyTorch. Verify the `[translation]` optional
      group actually isolates argostranslate. Consider three tiers: core
      (~100MB), `[nlp]` adding spaCy (~600MB), `[translation]` adding
      argostranslate (~2.5GB).

- [x] **Outlined pill CSS.** Already shipped; this entry was stale.
      `.vocab-pill` and `.antonym-pill` both carry `background: transparent`
      with a `1px solid` coloured border (`cards.py:147-163`), which is the
      intended design. It went in with the issue #2 fix recorded under
      Critical above, and this duplicate entry under Medium was never
      cleared — it still asked to "verify whether the change was ever
      applied to `cards.py`" long after the Critical entry recorded that it
      had been, and verified with two live runs.

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

- [x] **Hyphenated compound handling in the deck fuzzy match.** Tested, and
      the concern does not reproduce — the matcher already handles them, so
      this closes with regression tests rather than a fix. Verified against
      the real French deck, which holds `semi-relevé` and `avant-garde` as
      actual fronts: `semi-relevé`, the word from the original bug report,
      scores 100 against its own front. `week-end`/`weekend` match in both
      directions, `porte-monnaie` matches `porte monnaie`, and the guards
      hold the other way — `semi-relevé` does not collide with `relevé`
      (length ratio), and `arc-en-ciel` does not collide with `arc` or
      `ciel` (short-front exclusion).

      Two boundary cases worth knowing. A typographic apostrophe
      (`aujourd'hui` vs `aujourd’hui`) passes at 91, one point above
      CONFIDENCE_HIGH — narrow, not comfortable. An accent difference
      (`après-midi` vs `apres-midi`) scores exactly 90 and QUEUEs, since
      SKIP requires strictly above; asking is the right answer there. Both
      are pinned by tests so a threshold change fails loudly.

      Could only be tested properly after the Critical item above: the
      matcher was being fed an empty front list for most decks, so any
      earlier test would have been measuring nothing.
- [x] **`DB_PATH` should default to an absolute path so running from different
      directories does not create multiple database files.** Done, and wider
      than the entry described. `DICT_DIR`, `REVIEW_FILE`, and `OUTPUT_DIR`
      had the identical bug, and `DICT_DIR`'s consequence is worse than the
      database's: a missing index is a supported state, so a run from the
      wrong directory silently drops every non-English definition and still
      reports success.

      Anchoring the defaults alone would have fixed nothing in practice.
      `.env.example` ships `DB_PATH=pipeline.db`, `OUTPUT_DIR=output`, and
      `REVIEW_FILE=review.json`, so a documented install always takes the
      override branch and never reaches the default — the local `.env` here
      does exactly that. Relative values are now anchored whatever their
      source; absolute ones are honoured as given, with `~` expanded first.

      Verified by mutation, not by the new tests passing: the unanchored
      version fails 5 of the 14, and the narrower defaults-only version
      still fails the relative-override pair plus three module constants.
      See ARCHITECTURE.md 8.21.
- [x] **Investigate whether `token.is_alpha` removal admits junk in languages
      other than French.** Measured across every language processed so far,
      not one video: of 4128 distinct (deck, lemma) pairs in `pipeline.db`,
      exactly **12** are lemmas `str.isalpha()` rejects — that is, the
      complete set the removed filter used to block.

      | deck | count | lemmas |
      |---|---|---|
      | `LangTest_de` | 5 | `gibt's`, `gibt'sn`, `kriegt'sn`, `u-bahnfahrer`, `war's` |
      | `LangTest_fr2` | 4 | `au-dessus`, `aujourd'hui`, `bien-être`, `peut-être` |
      | `LangTest_zh` | 2 | `bye-bye`, `i'm` |
      | `Tango_Verify_20260807` | 1 | `semi-relevé` |

      0.29% of all vocabulary, and most of it is real: every French entry is
      a common word, `u-bahnfahrer` is an ordinary compound, and `gibt's` /
      `war's` are standard spoken German contractions. Three are marginal —
      `gibt'sn` and `kriegt'sn` are dialectal transcription artifacts, and
      `i'm` is English code-switching inside a Chinese video.

      The trade is decisively worth it in the other direction: restoring
      `is_alpha` to block those three would also throw away `aujourd'hui`,
      `peut-être`, `bien-être` and `au-dessus`, four of the most common words
      in French. No action needed.
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
