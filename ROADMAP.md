# ROADMAP.md — Tango

One goal per tag, from here to v1.0.0, and an explicit statement of what
v1.0.0 freezes.

This file exists because versioning had been decided by instinct: the
version string in `pyproject.toml` never once matched the tag it shipped
under (`0.1.0` at v0.4.3 and v0.4.4, `0.4.4` at v0.4.5), five tags carry no
release notes, and no document said what a release *was*. A number chosen
per release is a guess; a number that follows from a rule is a decision.

The goals below started as proposals from the open items in `TASKS.md` and
the gaps in `ARCHITECTURE.md` §9. Everything through v0.5.3 has shipped and
is tagged; v0.6.0 onward is still open to reordering, since the ladder
matters more than the contents of any one rung.

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

**Every tag gets GitHub release notes.** All thirteen have them as of
18 August 2026; v0.4.1 to v0.4.5 were backfilled from their CHANGELOG
entries, having previously had only a tag message and `git log`.

---

## 2. The ladder

### v0.5.0 — Pronunciation, and a notetype that merges

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

### v0.5.2 — Audio that plays in the card

v0.5.0 put a *link* in the Pronunciation field. Clicking it opens a browser,
which is not reviewing, and it does nothing useful on AnkiDroid or
AnkiMobile. The recording is downloaded at package-build time and shipped
inside the `.apkg` as `[sound:...]`.

ADR-009 rejected this on size grounds using an estimate nobody measured
("tens of megabytes"). Real Commons files are 16–30 KB, so a 240-card German
deck costs about 5 MB. That is the third ADR-009 estimate to be wrong in the
same direction — see ARCHITECTURE.md 8.35 and CLAUDE.md 3.6.

Downloads are paced, and this is the part worth remembering: the download
host rate-limits per IP, and the *first* implementation embedded 13
recordings out of 377 while reporting success. A rate limit does not fail a
run here, it quietly degrades every card past the tenth.

*Why PATCH:* no migration, no schema change. The Pronunciation field already
exists and already held content; only what goes in it changed.

### v0.5.3 — Part of speech in the learner's language

Every card carried an English tag in its Class field, because that is what
wiktextract stores no matter which Wiktionary edition the index came from.
German cards read `noun`, French ones read `adj`. Neither is a word in the
language the learner is studying, and `adj` is not a word at all.

Class now follows the definition: the transcript language normally, the
`--def-lang` language when that is set. Labels ship for de, fr, ru, es, it,
pt and en.

*Why PATCH:* nothing migrates and no existing card changes until it is
re-imported. This finishes a field that was already on the card.

### v0.6.0 — Card quality

The fields exist and are filled; this is about what is *in* them. **Released
27 August 2026, all five items.**

- Filler-word cards (`Ah`, `Bah`, `Euh`, `Tss` — 3.4% of one real French run).
  Done.
- Inflection-pointer glosses that still reach cards as definitions. Done;
  Russian was the last language still leaking them.
- Cache key carrying both languages, so a cross-language fix invalidates the
  rows it should. Done; `lemma::source::target::pos`, with the pre-existing
  rows set aside rather than re-keyed by guesswork. Issue #26.
- Words that get no definition at all, 6.9% of a real German deck. Added to
  this rung on 26 August 2026 rather than planned into it, because measuring
  the previous item surfaced it. Done; the run names them. Issue #27.
- Antonyms, the weakest field everywhere. Done, and the rung with it.
  ADR-010 was accepted and implemented on 26 August 2026: a 4.3 MB
  ConceptNet index covering 22 languages, built by `make antonyms`, filling
  the field when nothing else can. Measured end to end, French went from
  19.7% to 34.8%, German 56.2% to 60.3%, Russian 47.8% to 48.8%. The gain is
  concentrated in French because the gap was between Wiktionary editions
  rather than between sources. Issue #25.

The last one is deliberately not a filter. Three signals were measured and
none separates transcript damage from real words, so the run names them and
the learner decides. Guessing here is the expensive direction: a filler that
slips through costs one card, a word wrongly filtered is never offered and
cannot be missed.

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
820 MB.** Step 1 below shipped on 26 August 2026 and took `.tangovenv` to
2.2 GB, so read this table as the starting point rather than the current
state. Where the 5.9 GB went:

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
"roughly 1.5GB" for this; the real figure is three times that. Step 1 has
since removed `nvidia` and `triton` outright and cut torch to 725 MB.

**Why torch is there at all, traced rather than assumed:**

```
argostranslate → stanza → torch → nvidia-cudnn/cusparselt/nccl/nvshmem, triton
```

Translation *inference* does not use torch. It uses `ctranslate2` and
`sentencepiece`, together about 75 MB. `stanza` is pulled in for **sentence
boundary detection** — splitting a paragraph into sentences — and it brings
the entire CUDA stack with it. 4.5 GB to find full stops.

argostranslate already knows this: it depends on `minisbd`, a minimal
sentence boundary detector, and `translate.py` selects `MiniSBDSentencizer`
when the installed language package ships minisbd rather than stanza.

Two things block simply removing it, both verified in the installed source:

- `argostranslate/sbd.py:12` is a bare `import stanza`, unguarded — unlike
  the `import spacy` directly above it, which sits in a `try/except`. So
  uninstalling stanza breaks argostranslate at import time, not just on the
  stanza path.
- `settings.stanza_available` is referenced by `translate.py:457` but is
  **defined inside a docstring** in `settings.py`, so it is not a setting at
  all. `ARGOS_STANZA_AVAILABLE` does nothing.

So the reduction is staged, cheapest first:

| step | saves | risk |
|---|---|---|
| 1. install the CPU-only torch wheel (`--index-url .../whl/cpu`) — **done, 26 August 2026** | 3.7 GB measured: `nvidia` and `triton` gone, torch 1.1 GB to 725 MB | none — no code change, no GPU here to lose |
| 2. drop `pymupdf` | 63 MB | none — zero references in the repo |
| 3. install spaCy models on demand, not all nine | ~400 MB (`sudachidict_core` 208 MB + `zh_core_web_sm` 76 MB + others) | none for users who need one language |
| 4. guard the stanza import upstream, ship minisbd packages | the remaining ~1.1 GB of torch | needs an upstream patch or a vendored shim; verify translation quality is unchanged |

**After step 1, measured 26 August 2026, the three biggest things left are
torch, the indexes and the spaCy models**, in that order:

| | size |
|---|---|
| `torch` (CPU build) | 725 MB |
| `dictionaries/` | 820 MB for three languages: fr 404, de 292, ru 126 |
| nine spaCy models plus `sudachidict_core` | roughly 400 MB |
| `.tangovenv` total | 2203 MB |

So torch is still the largest single package even after losing 3.7 GB, which
is what step 4 is about. `dictionaries/` is larger than any one of them, but
only because three languages are installed; one index is 126 to 404 MB.
81.4% of the German index is inflected forms (8.30), which are needed for
lookup but are mostly a word plus a pointer — whether that warrants the
current row format is an open question with a real number attached to it.

A realistic floor for a one-language, no-translation install is therefore
roughly **250–300 MB of code plus one index**, and the index dominates.
Worth knowing before optimising the wrong half.

Proposed acceptance targets. Step 1 is done and measured; the rest are still
the shape of the goal rather than findings. Note what step 1 did **not**
reach: a translation install is 2.2 GB, nowhere near the 600 MB below, and it
cannot get there while torch is 725 MB of it. That is step 4, and it needs an
upstream change:

- base install, no translation, one language: **≤ 300 MB** of code
- with translation: **≤ 600 MB** of code, which step 1 alone does not reach.
  Measured after it: 2203 MB, of which torch is 725 and the nine spaCy models
  are roughly 400. It needs steps 3 and 4 as well
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
