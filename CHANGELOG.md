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

Nothing yet.

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

[Unreleased]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.1...HEAD
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
