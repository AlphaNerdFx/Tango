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

### v0.8.0 — Install it anywhere

- Dependency size: PyTorch via argostranslate is ~1.5GB in an optional group
  that is easy to install by accident
- Dockerfile
- Drop the assumptions this repo has about WSL2 + Windows Anki

### v0.9.0 — Freeze candidate

- Write the compatibility document: every item in §3 below, pinned
- Deprecation policy — what a `0.9 → 1.0` break costs a user
- Backfill GitHub release notes for v0.4.1–v0.4.5
- Coverage sweep green across all 16 language pairs, including the new
  pronunciation columns

### v1.0.0 — Stable CLI

No new features. §3 is frozen, documented, and tested.

**Explicitly not in 1.0.0:** the FastAPI backend, the web app, and the
Chrome extension. Those are the multi-surface product, and 1.0.0 is the CLI
that feeds them. The name-keyed payload dicts in `cards.py` are the seam
they will consume.

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

## 4. Keeping this honest

Each tag should leave, at minimum:

- `pyproject.toml`, the git tag, and `CLAUDE.md`'s "Current tag" in agreement
- GitHub release notes naming what changed and any migration
- `SESSION.md` §1 updated with the real test count and coverage, re-measured
  rather than carried forward — that number has gone stale four times
- `ROADMAP.md` — this file — with the shipped rung marked and the next one
  still describing real work
