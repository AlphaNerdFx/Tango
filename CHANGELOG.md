# Changelog

All notable changes to Tango are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While at `0.x`, a MINOR bump means new capability **or anything requiring a
migration**; see `CLAUDE.md` §15 and `ROADMAP.md` §1 for the rule that picks
the number, and `ROADMAP.md` §2 for the goal attached to each planned tag.

Entries for v0.4.5 and earlier were reconstructed from tag messages and
`git log` when this file was created at v0.5.0. They summarise each release
rather than list every change.

## [Unreleased]

Working toward v0.8.0, packaged and installable.

## [0.7.0] — 2026-09-03

The command line as a product: the first release aimed at someone who did not
write it. Nothing here changes a card or asks anything of an existing
collection. It changes what you type, what the run tells you while it works,
and what a failure says when it stops.

### Added

- **A real command, with subcommands.** `tango run <id> --deck "..."`,
  plus `review`, `backlog`, `languages`, `doctor`, `setup`, `install-model`,
  `install-translation`, `build-dictionary` and `build-antonyms`. The old
  surface put every mode behind a boolean flag on one parser, so `--help`
  listed sixteen options without indicating that `--review` and `--video-id`
  are different programs, and nothing stopped you passing both.

  There is also a console entry point for the first time: the project had no
  `[project.scripts]` at all, so even an editable install gave you
  `python -m pipeline` rather than a verb. **This is a breaking CLI change**,
  made deliberately now, while the interface has no installed users, rather
  than after v0.8.0 publishes it.

- **A long run says what it is doing, and how long it took.** Pacing
  Merriam-Webster (8.43) turned the definition phase into minutes of silence
  on a large English video, and silence is indistinguishable from a hang.
  Every phase now reports its own elapsed time, and the definition phase
  carries a progress line with a completion estimate.

  The line redraws in place on a terminal, so a thousand-word run stays one
  line rather than a thousand. Redirected anywhere else it prints one line
  per decile instead: carriage returns in a log file are noise, and
  `tango run ... > run.log` is a normal thing to do here. The estimate is
  elapsed divided by completed, which is honest because the work per word is
  uniform, and a cleverer estimate that is wrong is worse than a simple one
  that is roughly right.

- **English can have an offline index, and should.** `make dictionary
  LANGUAGE=en` used to warn you off. That advice was measured on 7 August,
  a week before IPA and Pronunciation became card fields, and it has been
  reversed. On a real 1094-lemma English deck the index supplies IPA for
  96.4% of words, audio for 97.0% and an example for 90.3%, all offline,
  where before English carried none of those whenever dictionaryapi.dev was
  unreachable.

  Merriam-Webster is still tried first for definitions, because they are
  better written. What changed is that it is no longer the only thing
  holding English up: its free tier allows 1000 queries a day per key and
  one 1094-word video exceeds that on its own, and its licence does not
  cover a commercial product. 502 MB to download, 236 MB on disk, the
  smallest of the four indexes. ADR-011.

### Fixed

- **Merriam-Webster is paced, and a source that stops is reported.** A real
  1094-word English run shipped 167 cards with a definition and 927 without,
  and said "Done". MW answered the first 167 and then nothing: five workers
  were pushing about 18 requests a second, five consecutive failures tripped
  the circuit breaker, and a tripped breaker skips its source for the rest of
  the run while nothing in the summary says so.

  MW now goes through the same leaky bucket `media.py` has used since v0.5.2,
  at a deliberately conservative 4 requests a second (`MW_RATE_LIMIT`), and
  the run summary names any source the breaker gave up on, says that it
  explains the missing content, and gives the two ways out. ARCHITECTURE 8.43.

- **One word no longer becomes two cards.** A 1079-card French deck carried
  `voyez` beside `voir` and `soit` beside `être`: 15 cards, 1.4%, each an
  inflected form of another card in the same deck, because spaCy returned the
  surface form as the lemma. The offline index already records the base form,
  so an inflected form is now folded onto it, keeping its count and its
  surface forms. Only when the base form is in the same run, so a word met
  only in an inflected form is still taught as the learner met it.
  ARCHITECTURE 8.44.

- **An index miss no longer costs English its pronunciation.**
  `_resolve_pronunciation` consults the index first for every language and
  returned early on a miss, which was free while English had no index. With
  one, a word the index does not carry would have gone from "sometimes has
  audio" to "never has audio". It falls through to dictionaryapi.dev now.

- **`make doctor` no longer reports `Error 1`.** The report's own last line
  says every missing item is optional, and make printed a failure directly
  underneath it. The CLI keeps its exit code, which setup scripts branch on;
  the make target no longer propagates it.

- **Failures name the fix.** Seven messages stopped the run while stating
  only the outcome, leaving the user to find the flag in `--help` or guess
  that one exists. Now: an already-processed video names `--force` and shows
  the exact command; a missing translation model names
  `--install-translation de:en` and the alternative of dropping `--def-lang`;
  an AnkiConnect error carries the action that failed and points at
  `--doctor`; an AnkiConnect timeout names the modal dialog that causes it
  nearly every time; an empty transcript says the video's captions are empty
  instead of naming a private function and a private dict key; and the three
  CLI paths that could fail without a next step now print one.

## [0.6.0] — 2026-08-27

Card quality: what is *in* the fields, rather than which fields exist.

### Added

- **The run summary names the words that got no definition**, instead of only
  counting them. On a real 406-card German run that is 28 words, 6.9% of the
  deck, and most are transcript damage or names (`Bissch`, `Herauszufinde`,
  `Barack`) that you would delete on sight. Naming them means finding and
  deleting them in one pass rather than meeting them during review.

  They are deliberately not filtered out. Three signals were measured against
  that deck and none separates them from real vocabulary: index absence drops
  real German compounds like `Rüberbringen`, prefix matching flags `Barack`
  as a truncation of `Baracke`, and 63% of words that *do* get a definition
  also appear only once. See issue #27.

- **Antonyms have an offline source of their own.** The field was the
  weakest on the card by a wide margin: 19.7% on a real 1054-lemma French
  deck against 98.6% for definitions. `make antonyms` builds a 4.3 MB index
  from ConceptNet covering 22 languages at once, and the field is filled
  from it when nothing else can.

  Measured end to end: French 19.7% to 34.8%, German 56.2% to 60.3%,
  Russian 47.8% to 48.8%. The asymmetry is the point. ConceptNet's
  antonyms are Wiktionary's own, re-extracted by a different tool: kaikki
  runs wiktextract, ConceptNet ran wikiparsec, and on the same edition they
  disagree about which words carry an antonym. The French index has one for
  15 045 words and ConceptNet has 12 376, comparable and overlapping only
  partly, so the union is much larger. German and Russian already hold eight
  to thirteen times more than ConceptNet does, so there it mostly hands back
  what is there. ADR-010.

  Entirely optional. Without the index every card is exactly what it was,
  and `--doctor` reports it as absent rather than missing.

### Changed

- **Translation setup installs CPU-only torch, cutting the install by 3.8 GB.**
  `argostranslate` needs `stanza`, which asks for `torch>=1.3.0` without
  naming a variant, so pip took the CUDA wheel and its `nvidia` and `triton`
  companions on every machine: 4.5 GB, 76% of the virtualenv, unusable
  without an NVIDIA card and a current driver. On the machine this was found
  on, which has an NVIDIA card, `torch.cuda.is_available()` was False the
  whole time because the driver was too old.

  Verified in a clean virtualenv: 733 MB instead of 4515 MB, with `nvidia`
  and `triton` absent entirely, and then applied to this machine's own
  environment, which went from 5.9 GB to 2.2 GB. A working GPU can still
  have the CUDA or ROCm build installed over the top, and neither the target
  nor the advice will replace one that is actually being used.
  ARCHITECTURE.md 8.41.

- **`make translate-setup` repairs an environment that already has the CUDA
  build**, instead of only helping a fresh one. Installing from the CPU
  index does nothing when torch is already present: pip answers "already
  satisfied" and the 4.5 GB stays. The target now detects a CUDA build with
  no usable GPU, replaces it with `--force-reinstall --no-deps`, and removes
  the `nvidia-*` and `triton` orphans that pip leaves behind on its own.

- **`--doctor` prints the repair commands in a form that runs.** They were
  spelled `pip`, which is not on `PATH` in a virtualenv built by `make venv`,
  and they replaced torch without removing the 3.4 GB of orphans, which is
  the worst of both. They now use the running interpreter's own path and
  name every installed `nvidia-*` distribution.

### Fixed

- **The definition cache could serve one language's examples to another.**
  The cache key recorded the language a definition was written in, not the
  language of the video it came from, and constraint 3.3 means those choose
  different content: a German run with `--def-lang en` writes a row holding
  German sentences. An English video looking up a spelling both languages
  share (`hand`, `arm`, `band`, `wild`) would have read that row back.
  Measured on a real 5408-row cache, 265 rows keyed `::en` already hold
  German examples; none had collided only because no English run had met one.

  The key is now `lemma::source::target::pos`, which also makes invalidating
  a language pairing a single `DELETE ... LIKE '%::de::en::%'` instead of a
  reconstruction through the vocabulary table.

  **Your existing cache is kept, not deleted.** Old rows move to a
  `definitions_v0` table and the live cache refills as words are met again.
  They cannot be rewritten, because recovering a row's source language means
  knowing which video it came from and nothing records that. See
  ARCHITECTURE.md 8.40.

- **CI checked a requirements file it never installed.** A Dependabot bump of
  `thinc` to 9.1.1 passed on all three Python versions while making
  `requirements.txt` unresolvable, because CI installs `pip install -e
  ".[dev]"` from `pyproject.toml` and never read the pinned files. A `pins`
  job now resolves both of them and checks each pin against the range
  `pyproject.toml` declares, which catches the case pip cannot: a pin that
  installs cleanly on its own but contradicts the project's own metadata.
  ARCHITECTURE.md 8.39.

### Dependencies

- spacy 3.8.14 to 3.8.15, requests 2.33.1 to 2.34.2, actions/checkout 4 to 7,
  actions/setup-python 5 to 7, and the dev group (black, ruff, mypy,
  types-requests) brought up to what the environment already ran.
- thinc stays at 8.3.13. spaCy 3.8 requires `thinc<8.4.0`, so it moves when
  spaCy does.

### Changed

- **Filler sounds no longer become cards.** `Ah`, `Bah`, `Ouai`, `Euh` and
  `Tss` were 3.4% of one real French run. spaCy tags them INTJ, which the
  pipeline already drops, but it tags them NOUN and ADV often enough in real
  sentences that they reached cards anyway.

  A per-language stoplist in `language.FILLER_SOUNDS` rather than a rule on
  the tag, because `Bonsoir` is also INTJ and is worth learning. Only sounds
  are listed: French `bon`, German `na` and Russian `ну` are common in speech,
  carry meaning, and are deliberately absent. Elongated spellings (`euuuh`,
  `ahhhh`) are folded onto the listed one, at runs of three characters rather
  than two so English `err` survives. Because folding at three maps `tsss`
  onto `ts` rather than `tss`, each sound's short spelling is derived from
  the table rather than written by hand, so no list can be complete-looking
  and still miss its own elongations.

  Lists ship for en, fr, de and ru, and only the French one has been counted
  against a real run. A language with no list filters nothing. Each run logs
  `Filler sounds skipped: N tokens`. See ARCHITECTURE.md 8.37.

  The bar for listing a sound is whether a course would teach it, not
  whether a speaker says it without thinking. Words that fail that bar are
  kept out on purpose, because the two mistakes are not equally cheap: a
  filler that slips through is one card you delete, while a word listed here
  by accident is one you are never offered and cannot tell is missing.

- Existing cards are untouched. The filter applies to the next run, not to a
  deck already imported.

- **Russian inflected words get a real definition.** A card could read
  "дательный падеж от кома", which points at another word instead of
  defining this one. Following the pointer already worked for German and
  French; Russian failed because the target carries a homograph
  disambiguator (`толк#(существительное I)`) or a stress mark (`нача́ло`),
  and neither matches a headword. Measured on the real index over 300
  sampled inflected forms, Russian went from 286 resolved to 298. German and
  French were already at 299 and 297 and are unchanged. ARCHITECTURE.md 8.38.

## [0.5.3] — 2026-08-17

The part of speech is written in the learner's language.

### Changed

- **Class is localised.** wiktextract normalises the part of speech to an
  English tag whichever Wiktionary edition an index was built from, so a
  German card read `noun` and a French one read `adj`. Cards now read
  `Substantiv` and `adjectif`.

  The label follows the definition, so it never disagrees with the text
  beside it: with `--def-lang fr` a German word reads `nom`, and without it
  the same word reads `Substantiv`. Constraint 3.3 already allowed `Class`
  to change language, and this is the reason it does.

  Labels for de, fr, ru, es, it, pt and en are in `language.POS_LABELS`.
  A language with no table falls back to English, which still expands `adj`
  into `adjective`, and a tag with no entry is shown unchanged rather than
  dropped. Adding a language is one row.

- Existing cards keep whatever they were imported with. Re-import to update
  them; nothing needs migrating and no review history is affected.

## [0.5.2] — 2026-08-17

Pronunciation audio plays inside the card instead of linking out.

### Added

- **Embedded audio.** Recordings are downloaded when the package is built and
  shipped inside the `.apkg`, so the Pronunciation field is now
  `[sound:...]` and plays in place. A link opened a browser, which is not
  reviewing; embedded audio also works on AnkiDroid and AnkiMobile and keeps
  working when the source is down.

  ADR-009 rejected this on size grounds and the estimate was wrong. Measured
  on real Commons files — De-Haus 16 KB, De-Spaziergang 30 KB — a 240-card
  German deck costs about 5 MB, not the "tens of megabytes" assumed.
  Wikimedia already serves them as MP3, so nothing is converted.

- `media.py`, with an on-disk cache (`MEDIA_DIR`, default `media/`) so a word
  met in a second video is not downloaded twice.

- **A link fallback per card.** When a download fails the card keeps the
  linked URL rather than losing the audio entirely. This is routine, not
  defensive padding: dictionaryapi.dev served one word's audio and returned
  502 for another in the same minute.

- **Paced downloads.** Audio requests are spread across the run at
  `MEDIA_RATE_LIMIT` per second (default 1.25) after a burst of
  `MEDIA_BURST`, and a `429` is retried for the period the server asks for.

  Without this the feature above barely worked. `upload.wikimedia.org`
  rate-limits per IP — about ten requests, then `429` with `Retry-After: 11`
  — so an 8-worker pool drained the allowance in under a second. A real
  406-card German run embedded **13** recordings and linked the other 364,
  with no error and a valid package. Going sequential did not help; only
  spacing the requests did. ARCHITECTURE.md 8.35.

- **Progress output while audio downloads.** Paced downloading of a few
  hundred files takes minutes, and the CLI previously sat silent at
  "Building Anki package...". The embedded-versus-linked count was already
  being logged at `INFO`, below the default `WARNING` level, so the one
  number that revealed the bug above was written and discarded.

### Fixed

- **The documented non-interactive recipe crashed.** `echo "s" | make run`
  supplies one line; the queued-word prompt consumed it and the import prompt
  then hit `EOFError`, ending the run with a traceback *after* the package had
  been written. All prompts now treat exhausted stdin as "no input" and take
  a safe default. CLAUDE.md 4.4.

### Known issues

- Images are ADR-009 **phase 3** and deliberately not built: sampling
  measured 2 of 5 usable, with `laufen` returning a coin from the town of
  Laufen. They need a relevance gate before they are worth having.

## [0.5.1] — 2026-08-16

Pronunciation now describes the word on the card, in every mode and in
English. No migration: the fields added in v0.5.0 are unchanged, and this
only alters what goes into them.

### Fixed

- **Cross-language mode put the wrong word's pronunciation on the card.**
  With `--def-lang`, `ipa` and `audio_url` were read from the *translated*
  word's index entry, so a German video with `--def-lang fr` showed `Haus`
  carrying maison's `\me.zɔ̃\` and a French recording. Examples, synonyms
  and antonyms were already gated against exactly this; pronunciation had
  been added beside the gate without being inside it.

  Pronunciation is now resolved once, by `_resolve_pronunciation(lemma,
  language)`, independent of which source supplied the definition. No
  definition branch touches it. See `ARCHITECTURE.md` 8.34.

### Added

- **English pronunciation.** dictionaryapi.dev returns real IPA (`/haʊs/`)
  and a complete audio URL in a `phonetics` block, on a call the pipeline
  already made and never parsed. English cards now carry pronunciation even
  when Merriam-Webster supplied the definition.
- Pronunciation on English fallback cards, through the same resolver.

### Changed

- `CLAUDE.md` §3.3 is now stated as a question — *does this field describe
  the word shown?* — rather than a list of three field names. Written as a
  list, it was violated three times, each by someone adding a fourth thing
  beside the gate.

## [0.5.0] — 2026-08-15

Pronunciation on cards, and a notetype that merges instead of forking.

> **Migration required.** This release adds two fields to the Anki notetype.
> `deck.ensure_model_fields()` runs automatically before the auto-import and
> adds them; a failed alignment cancels the import rather than risk a fork.
> Adding a field is non-destructive — verified on a real 2135-note
> collection with zero changed field values — but it is a schema change, so
> **Anki will ask for one full sync afterwards**. Importing by hand via
> File → Import bypasses the alignment and will fork the notetype.

### Added

- `IPA` and `Pronunciation` fields on cards, appended at indices 10 and 11
  (ADR-009 phase 1). Sourced from the offline Wiktionary index, which schema
  v2 extended with `ipa`, `audio_url` and `form_of` columns.
- Pronunciation on fallback cards — those with no definition from any
  source, which are the cards that benefit from it most.
- `deck.ensure_model_fields()`: aligns the collection's notetype with
  `cards.FIELDS` before importing, so appending a field merges rather than
  forking.
- `cards.FIELDS` as the single source of truth for card fields. The model is
  generated from it and both note builders address fields by name, so a
  misspelled field now raises instead of silently shifting every later one.
- `tests/test_hard_constraints.py` — one mutation-verified test per hard
  constraint in `CLAUDE.md` §3.
- `ROADMAP.md`: one goal per tag to v1.0.0, and an explicit list of what
  v1.0.0 freezes.
- Inflection-pointer following in the index, so `glaube` resolves to
  `glauben` for a definition while keeping its own pronunciation. On a real
  German video this cut pointer-glosses-as-definitions from 18 to 1.

### Changed

- **`ANKI_MODEL_ID` moved from `1607392319` to `1607392321`**, once and
  never again. The old value is the model ID from genanki's README example
  and never held any of this project's cards — Anki had already forked away
  from it. See `ARCHITECTURE.md` §8.31.
- `pyproject.toml` version now tracks the tag. It never has before: it read
  `0.1.0` at both v0.4.3 and v0.4.4, and `0.4.4` at v0.4.5.
- The coverage sweep (`scripts/coverage_matrix.py`) reports the two new
  fields; it previously measured indices 0–7 and stopped.

### Fixed

- The notetype was resolved by **name**, which is wrong on any collection
  with history: Anki suffixes the name when it forks, so the ID and the plain
  name come apart. On a real collection this would have added six fields to
  an unrelated 1134-note notetype. Now resolved by ID.
- Cross-language runs could take examples from the Merriam-Webster entry
  without the language gate — the one call site of three that
  `CLAUDE.md` §3.3's tests never inspected.

### Known issues

- English, and any language without a built index, gets no pronunciation.
  Fix identified and scheduled for v0.5.1.
- Audio coverage varies sharply by language: 95.0% of German index rows
  carry a recording, against 12.1% French and 4.4% Russian. IPA is
  dependable everywhere (83–99%).

## [0.4.5] — 2026-08-09

Correctness release, 67 commits.

- The deck duplicate check could not see most cards: it read a field named
  `Front` while the generated model's first field is `Word`. Measured at 0
  of 1036 notes visible in one real deck. Now reads the lowest-`order`
  field, which is what Anki treats as a note's identity.
- Cross-language definitions were broken four ways at once, each hiding the
  next — including `ARGOS_PACKAGES_DIR` pointing at an empty directory,
  which argostranslate reads itself.
- `importPackage` shared the 5-second timeout meant for quick queries, so
  imports that worked at 40k notes failed at 57k.
- A pasted YouTube URL aborted `make run` before the pipeline started: the
  video id was interpolated into printf's *format string*.

## [0.4.4] — 2026-08-05

- Circuit breaker for definition sources.
- WSL auto-import path translation, so AnkiConnect on the Windows side can
  resolve a package generated under `/mnt/c`.

## [0.4.3] — 2026-08-04

- Language-aware spaCy model selection (closes #3). An English model
  applying English morphology to French text had been misattributed to
  caption quality.

## [0.4.2] — 2026-08-04

- Synonym and antonym pill rendering fixes.

## [0.4.1] — 2026-08-04

- Card-quality and bug-fix release.

## [0.4.0] — 2026-07-19

- Multilingual definitions via the offline Wiktionary index, card redesign,
  adaptive CSS for Anki's light and dark themes, fuzzy matching improvements.

## [0.3.0] — 2026-07-13

- Card template redesign, multilingual definition fixes, translation timeout.

## [0.2.0] — 2026-07-08

- Language filter: subtitle selection by language code or deck name.

## [0.1.0] — 2026-07-02

- Initial working pipeline: YouTube transcript to Anki cards, 323 unit tests.

[Unreleased]: https://github.com/AlphaNerdFx/Tango/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.3...v0.6.0
[0.5.3]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.5...v0.5.0
[0.4.5]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AlphaNerdFx/Tango/releases/tag/v0.1.0
