# ROADMAP.md — Tango

One goal per tag, from here to v1.0.0, and an explicit statement of what
v1.0.0 freezes.

This file exists because versioning had been decided by instinct: the
version string in `pyproject.toml` never once matched the tag it shipped
under (`0.1.0` at v0.4.3 and v0.4.4, `0.4.4` at v0.4.5), five tags carry no
release notes, and no document said what a release *was*. A number chosen
per release is a guess; a number that follows from a rule is a decision.

**Draft.** The goals below are proposed from the open items in `TASKS.md`
and the gaps in `ARCHITECTURE.md` §9. Reorder freely — the ladder matters
more than the contents of any one rung.

---

## 1. The versioning rule

Tango follows [Semantic Versioning 2.0.0](https://semver.org): `MAJOR.MINOR.PATCH`.

While the project is at major version zero, SemVer says the public API is
not stable and anything may change. That is permission, not guidance, so
this project uses the ordinary 0.x working convention:

| bump | when | example from this project |
|---|---|---|
| `0.x.PATCH` | fixes, and completing something already shipped | the deck duplicate check reading the wrong field (v0.4.5) |
| `0.MINOR.0` | new capability, **or anything requiring a migration** | v0.5.0 — two new notetype fields, so every collection's notetype must be altered before import |
| `1.0.0` | the public API below is frozen and will not break without a `2.0.0` | not yet |

**The test that decides it:** does an existing user have to *do* something,
or does something they already have change shape? If yes, it is at least a
MINOR. v0.5.0 is a MINOR because `deck.ensure_model_fields()` has to alter
the notetype before the first import, and Anki then demands a full sync.
Card content getting better is not a migration; the notetype gaining a
field is.

**Version drift is a release bug.** `pyproject.toml`, the git tag, and
`CLAUDE.md`'s "Current tag" must agree at the moment of tagging. They never
have. `scripts/verify-release.sh` is where that check belongs.

**Every tag gets GitHub release notes.** v0.4.1–v0.4.5 have none, so the
only record of what shipped is the tag message and `git log`.

---

## 2. The ladder

### v0.5.0 — Pronunciation, and a notetype that merges  ← this release

- IPA and Commons audio on cards, sourced from the offline index (ADR-009
  phase 1)
- Index schema v2: pronunciation columns and inflection-pointer following
- `MODEL_ID` corrected to the notetype this pipeline's cards actually live
  on, once and never again
- `deck.ensure_model_fields()` — aligns the collection's notetype before
  import, so appending a field merges instead of forking it
- All six hard constraints enforced by mutation-verified tests

**Migration:** adds two fields to the notetype. Non-destructive — verified
on 2135 real notes with zero changed values — but Anki will ask for one
full sync afterwards.

### v0.5.1 — Pronunciation for every language

Today `en` gets no pronunciation at all, because it has no offline index and
that is the only source wired up. Three groups, one goal:

- **English:** dictionaryapi.dev already returns true IPA (`/haʊs/`) and a
  ready-made audio URL, and the pipeline already calls it — `phonetics` is
  simply never parsed. Merriam-Webster is the wrong source here: it returns
  its own respelling (`ˈhau̇s`), not IPA. Needs the dictapi call to happen
  for English even when MW supplied the definition.
- **es, ja, ko, pt, zh:** have a spaCy model and no index. `make dictionary
  LANGUAGE=<code>` already does this; nobody has run it.
- **de, fr, ru:** done in v0.5.0.

*Why PATCH and not MINOR:* no migration and no schema change — the fields
already exist and are empty. This completes a promise v0.5.0 made rather
than making a new one. A stricter reading would call a new parse path a
MINOR; if that reading wins, this becomes v0.6.0 and everything below
shifts.

### v0.6.0 — Card quality

The fields exist and are filled; this is about what is *in* them.

- Filler-word cards (`Ah`, `Bah`, `Euh`, `Tss` — 3.4% of one real French run)
- Antonyms, the weakest field everywhere
- Inflection-pointer glosses that still reach cards as definitions
- Cache key carrying both languages, so a cross-language fix invalidates the
  rows it should

### v0.7.0 — The command line as a product

The first release aimed at someone who did not write it.

- Migrate argparse → Typer
- Error messages that name the fix, everywhere (`make doctor` already does
  this; the pipeline itself does not)
- Progress and timing that make a long run legible

### v0.8.0 — Runs on any operating system

Today the repo assumes WSL2 with Anki on the Windows side. That assumption
is load-bearing in more places than it looks.

- `_translate_wsl_path()` and `_is_wsl()` exist because AnkiConnect resolves
  paths on the Windows side. macOS and native Linux need neither; native
  Windows needs no translation but different path handling
- `ANKI_HOST` is a WSL gateway IP that changes on every WSL restart. On any
  other platform it is `localhost` and never moves
- The Makefile is the documented entry point for 23 targets, and GNU make is
  not a reasonable requirement on Windows. `make help` already prints the CLI
  equivalent of every user-facing target — the remaining work is making the
  CLI the primary path and the Makefile a convenience
- CI on Linux, macOS and Windows, because "should work" is not evidence

### v0.9.0 — Runs on modest hardware

**Measured, 15 August 2026: `.tangovenv` is 5.9 GB and `dictionaries/` is
820 MB.** Where the 5.9 GB goes:

| package | size | why it is there |
|---|---|---|
| `nvidia` | 2.7 GB | CUDA runtime, pulled in by torch |
| `torch` | 1.1 GB | pulled in by argostranslate |
| `triton` | 689 MB | pulled in by torch |
| `sudachidict_core` | 208 MB | Japanese tokenizer for spaCy |
| `spacy` | 110 MB | core dependency |
| `ctranslate2.libs` | 75 MB | argostranslate |
| `pymupdf` | 63 MB | **nothing — zero references in `src/`, `scripts/` or `tests/`** |

**torch + nvidia + triton is 4.5 GB, 76% of the install, and none of it is
declared** — it all arrives through `argostranslate`. The CUDA stack lands
on machines that will never have a GPU. CLAUDE.md 3.6 has been saying
"roughly 1.5GB" for this; the real figure is three times that.

The largest single lever is therefore not code: installing the CPU-only
torch wheel should remove most of 3.4 GB without changing a line of the
pipeline.

Proposed acceptance targets. **None of these are measured yet** — the 5.9 GB
above is the only real number here, and the rest are the shape of the goal
rather than findings:

- base install, no translation: **≤ 500 MB**
- with translation, CPU-only torch: **≤ 1 GB**
- peak RSS on a normal run: **≤ 1 GB**
- index build completes within **2 GB RAM** (currently the most memory-hungry
  step by far, and unmeasured)
- a full run completes on a **4 GB / 2-core** machine
- per-language index: currently 131–423 MB, and worth asking what could be
  dropped or compressed

High-end hardware should be able to spend more, not merely avoid crashing:
worker counts, batch sizes and cache behaviour should scale to what the
machine has rather than being fixed at defaults chosen on one laptop.

### v0.10.0 — Packaged and installable

- A published package, so the install is one command and not a git clone.
  **The name needs deciding: `tango` on PyPI is an unrelated project** —
  `pyproject.toml` currently says `yt-anki-pipeline`
- Model and index downloads as a first-run step rather than a README
- Dockerfile for the "just run it" case
- Uninstall that actually removes the 800 MB of indexes

### v0.11.0 — Freeze candidate

- Write the compatibility document: every item in §3 below, pinned
- Deprecation policy — what a `0.9 → 1.0` break costs a user
- Backfill GitHub release notes for v0.4.1–v0.4.5
- Coverage sweep green across all 16 language pairs, including the new
  pronunciation columns

### v1.0.0 — A finished CLI

A fully-fledged, optimized command-line tool that installs from a package,
runs on Windows, macOS and Linux, and works on low-end and high-end hardware
alike. §3 is frozen, documented and tested. No new features — everything
above is done or explicitly deferred.

---

## 3. What v1.0.0 freezes

This is the "public API" SemVer talks about. Nothing here may break without
a `2.0.0`. **None of it is currently written down anywhere else, which is
why `MODEL_ID` could be described as sacred for a year while holding none of
this project's cards.**

1. **The notetype.** `MODEL_ID`, `DECK_ID`, and the field names and order in
   `cards.FIELDS`. Fields may be **appended** (with a migration); indices
   0–9 are what every already-imported card is bound to.
2. **The CLI surface.** Flag names and semantics: `--video-id`, `--deck`,
   `--language`, `--def-lang`, `--review`, `--process-backlog`, `--doctor`,
   `--build-dictionary`, `--list-languages`, `--force`, `--no-cache`.
3. **Configuration keys.** Every `ANKI_*`, `DEF_LANG`, `MW_API_KEY`,
   `DB_PATH`, `DICT_DIR`, and the rest of `.env.example`.
4. **On-disk schemas.** `pipeline.db` (definition cache, vocabulary, runs,
   backlog) and the dictionary index (currently v2). A schema bump costs a
   full re-download per language — 288 MB de, 682 MB fr, 278 MB ru — so it
   is a real cost to a real user, not an internal detail.
5. **Output.** The `.apkg` filename pattern `{video_id}_{YYYYMMDD_HHMMSS}`
   and the guarantee that a package imports without forking a notetype.

Not frozen, deliberately: card *content* (definitions, examples and their
sources may improve), log output, and anything private (`_`-prefixed).

---

## 4. Out of scope, and why that is a decision rather than a deferral

Tango 1.0.0 is a CLI. The following are **not** on the ladder above and
should not quietly acquire rungs on it:

- a Google Chrome extension surfacing vocabulary during playback
- a desktop or web application, and the FastAPI backend behind it
- deep-learning work beyond the existing spaCy/argostranslate use
- distribution to other language ecosystems — npm, crates.io — alongside PyPI

These are plausibly a **separate expansion project sharing a common premise**
with this one, not later versions of this one. Keeping them off the ladder
is what lets 1.0.0 mean "finished" instead of "paused".

**What that costs now: almost nothing. What it requires now: one thing.**
The seam those surfaces would consume is the card payload — the name-keyed
dicts `cards._build_note()` and `_build_fallback_note()` construct before
`_note_fields()` flattens them for genanki. That structure is already
independent of genanki and already keyed by name.

This matters most for the cross-ecosystem idea, because that one is easy to
mis-scope. A Rust or JavaScript implementation would not reuse this Python;
what travels between ecosystems is the **format**, not the code. So the only
pre-1.0 obligation is to keep that payload clean and, at freeze time,
specify it — a documented JSON shape for one card. Everything else is the
other project's problem, and specifying the format is cheap precisely
because §3 freezes the field list anyway.

The one thing to avoid before 1.0.0 is letting genanki concepts leak back
into that payload, which would quietly make the format unusable to anyone
not generating `.apkg` files.

## 5. Keeping this honest

Each tag should leave, at minimum:

- `pyproject.toml`, the git tag, and `CLAUDE.md`'s "Current tag" in agreement
- GitHub release notes naming what changed and any migration
- `SESSION.md` §1 updated with the real test count and coverage, re-measured
  rather than carried forward — that number has gone stale four times
- `ROADMAP.md` — this file — with the shipped rung marked and the next one
  still describing real work
