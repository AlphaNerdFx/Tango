# ROADMAP.md: Tango

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
| `0.MINOR.0` | new capability, **or anything requiring a migration** | v0.5.0, two new notetype fields, so every collection's notetype must be altered before import |
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

### v0.5.0: Pronunciation, and a notetype that merges

- IPA and Commons audio on cards, sourced from the offline index (ADR-009
  phase 1)
- Index schema v2: pronunciation columns and inflection-pointer following
- `MODEL_ID` corrected to the notetype this pipeline's cards actually live
  on, once and never again
- `deck.ensure_model_fields()`, aligns the collection's notetype before
  import, so appending a field merges instead of forking it
- All six hard constraints enforced by mutation-verified tests

**Migration:** adds two fields to the notetype. Non-destructive, verified
on 2135 real notes with zero changed values, but Anki will ask for one
full sync afterwards.

### v0.5.1: Pronunciation for every language

Today `en` gets no pronunciation at all, because it has no offline index and
that is the only source wired up. Three groups, one goal:

- **English:** dictionaryapi.dev already returns true IPA (`/haʊs/`) and a
  ready-made audio URL, and the pipeline already calls it, `phonetics` is
  simply never parsed. Merriam-Webster is the wrong source here: it returns
  its own respelling (`ˈhau̇s`), not IPA. Needs the dictapi call to happen
  for English even when MW supplied the definition.
- **es, ja, ko, pt, zh:** have a spaCy model and no index. `make dictionary
  LANGUAGE=<code>` already does this; nobody has run it.
- **de, fr, ru:** done in v0.5.0.

*Why PATCH and not MINOR:* no migration and no schema change, the fields
already exist and are empty. This completes a promise v0.5.0 made rather
than making a new one. A stricter reading would call a new parse path a
MINOR; if that reading wins, this becomes v0.6.0 and everything below
shifts.

### v0.5.2: Audio that plays in the card

v0.5.0 put a *link* in the Pronunciation field. Clicking it opens a browser,
which is not reviewing, and it does nothing useful on AnkiDroid or
AnkiMobile. The recording is downloaded at package-build time and shipped
inside the `.apkg` as `[sound:...]`.

ADR-009 rejected this on size grounds using an estimate nobody measured
("tens of megabytes"). Real Commons files are 16–30 KB, so a 240-card German
deck costs about 5 MB. That is the third ADR-009 estimate to be wrong in the
same direction, see ARCHITECTURE.md 8.35 and CLAUDE.md 3.6.

Downloads are paced, and this is the part worth remembering: the download
host rate-limits per IP, and the *first* implementation embedded 13
recordings out of 377 while reporting success. A rate limit does not fail a
run here, it quietly degrades every card past the tenth.

*Why PATCH:* no migration, no schema change. The Pronunciation field already
exists and already held content; only what goes in it changed.

### v0.5.3: Part of speech in the learner's language

Every card carried an English tag in its Class field, because that is what
wiktextract stores no matter which Wiktionary edition the index came from.
German cards read `noun`, French ones read `adj`. Neither is a word in the
language the learner is studying, and `adj` is not a word at all.

Class now follows the definition: the transcript language normally, the
`--def-lang` language when that is set. Labels ship for de, fr, ru, es, it,
pt and en.

*Why PATCH:* nothing migrates and no existing card changes until it is
re-imported. This finishes a field that was already on the card.

### v0.6.0: Card quality

The fields exist and are filled; this is about what is *in* them. **Released
27 August 2026, all five items.**

- Filler-word cards (`Ah`, `Bah`, `Euh`, `Tss`, 3.4% of one real French run).
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
  concentrated in French because kaikki and ConceptNet extract the same
  Wiktionary edition with different tools, and French is where wiktextract's
  antonym capture is thinnest. Issue #25.

The last one is deliberately not a filter. Three signals were measured and
none separates transcript damage from real words, so the run names them and
the learner decides. Guessing here is the expensive direction: a filler that
slips through costs one card, a word wrongly filtered is never offered and
cannot be missed.

### v0.7.0: The command line as a product

The first release aimed at someone who did not write it. **Released
3 September 2026, all three items.** No migration: nothing about a card or a
collection changed, only what you type and what the run says back.

- Migrate argparse → Typer. Done 28 August 2026: `tango run`, `review`,
  `backlog`, `languages`, `doctor`, `setup`, `install-model`,
  `install-translation`, `build-dictionary`, `build-antonyms`. A console
  entry point exists for the first time, so an install gives you a verb
  rather than `python -m pipeline`. The old flag surface is gone, broken
  deliberately now while the interface has no installed users.
- Error messages that name the fix, everywhere (`make doctor` already did
  this; the pipeline itself did not). Done 27 August 2026: the seven that
  stopped a run without naming a next step now name one.
- Progress and timing that make a long run legible. Done 27 August 2026:
  per-phase elapsed, and a definition-phase progress line that redraws on a
  terminal and prints one line per decile into a log file.

### v0.8.0: Packaged and installable

**Moved here from v0.10.0 on 27 August 2026.** It was three rungs back,
behind cross-platform support and install size, which is the ordering of a
project polishing for users who cannot install it. An outside review put it
plainly: the funnel has no top. Card quality, portability and disk footprint
all matter more once someone can run the thing, and less than nothing before.

Two things moved with it, because a published package that fails on first
contact is worse than no package: reaching Anki on the user's own platform,
and not making everyone pay for translation.

**Two of the three descriptions below were wrong when they were written, and
both were corrected on 3 September 2026 by measuring rather than reading.**
They are left visible because the pattern is the point: a claim nobody
re-checks is worth less than no claim.

- **Decide the name.** Done 3 September 2026: **`tango-anki`**. `tango` on
  PyPI is an unrelated project, so the distribution and the command cannot
  be the same string; the command stays `tango`, which is the one anybody
  types twice.
- **A console entry point.** Done in v0.7.0. There was no
  `[project.scripts]` at all, so even an editable install gave you
  `python -m pipeline` rather than a verb.
- **A thin default install.** Was written as "one language, no translation,
  no torch". Translation and torch were already optional, so that part was
  asking for what it already had. Measured instead, in a clean venv:
  `pip install tango-anki` is **334 MB across 58 packages**, of which 236 MB
  (74%) is spacy and the numeric stack it needs. That is the floor while
  spacy is the NLP engine, and spacy is not going in an extra: an install
  that cannot run `tango run` is not an install. What did move is `nltk`,
  13 MB plus a corpus download, which only supplements synonyms and
  antonyms and now lives in `pip install tango-anki[wordnet]`.
- **`ANKI_HOST` that works off WSL.** Was written as "it defaults to a
  gateway IP that is meaningless on macOS, native Linux and native
  Windows". It does not, and never did: `config.py` defaults to
  `http://localhost:8765`. The gateway IP was in one developer's `.env`.
  The real problem is the mirror image of that: localhost is right
  everywhere *except* WSL2's default NAT network, where Anki is on the
  Windows side. Fixed by retrying once against the default route after a
  refusal, rather than by changing the default, because defaulting to the
  gateway under WSL would break WSL2 mirrored networking, where localhost
  is correct. ARCHITECTURE 8.45.
- **`--version`.** Added with the packaging work. The first question about
  any bug report is which build produced it.

**Released 3 September 2026, and published to PyPI as `tango-anki`.** That
is the goal of the rung: the funnel now has a top, and
`pip install tango-anki` is a thing a stranger can type.

Three items were cut from this rung rather than delaying the name claim,
and are listed under v0.8.1 below. None of them stops anyone installing or
running the tool; all three make an existing install pleasanter. An
unclaimed distribution name can be taken by anyone, and that risk was worth
more than the polish.

### v0.8.1: Documentation for the people who can now install it

Released 3 September 2026, the same day as v0.8.0 and for a reason worth
recording. The README is the PyPI description, and every command in it
assumed a cloned repository: `make run`, `make dictionary`, `make antonyms`.
None of that exists after `pip install tango-anki`. The shop window for a
package published that morning was telling its first visitors to run a build
tool they did not have.

PyPI freezes a description at upload time and will not let it be edited, so
correcting the published page costs a release. That is the whole of this
one, plus a version badge that had been stuck at v0.5.3 for five releases
because it was hardcoded, and a `.pyc` committed because `.gitignore` named
two `__pycache__` directories instead of anchoring the pattern.

The lesson is the same one this project keeps relearning: a thing nobody
re-checks drifts, and publishing makes the drift public.

### v0.8.2: The install looks after itself

Cut from v0.8.0 on 3 September 2026 so the name could be claimed, then
renumbered when v0.8.1 became a documentation fix. **Released 4 September
2026, all three items, plus one gap a reader found in the code.**

Each was about an install that already works.

- **The spaCy model is offered, not reported.** Done. The check moved to
  before the transcript fetch, where it costs nothing, and on a terminal it
  downloads the model and carries the run on. The dictionary index is
  deliberately still manual: it is a several-hundred-MB download per
  language, which is not something to start without being asked.
- **Dockerfile for the "just run it" case.** Done, and built and run before
  release rather than written and hoped for: 663 MB, English model baked in,
  all state in a `/data` volume, non-root so the files it writes are
  deletable by the host user.
- **Uninstall that removes what pip leaves.** Done, and the number in this
  line was wrong. It said 800 MB; measured on the development machine it is
  1.2 GB, of which 1.1 GB is dictionary indexes. `tango uninstall` reports
  every location with its size and what losing it costs, then asks.

Also fixed here, and not planned: **something other than AnkiConnect
answering on port 8765** produced a raw `JSONDecodeError` rather than a
message. Found by a reader looking at the code, which is the second time
this rung a real gap came from reading rather than from a failure.

### v0.9.0: Nothing fails without saying why

Every release so far has fixed failures one at a time, as they were met: the
seven messages in v0.7.0, the eight stale flag names in v0.8.0, the port
8765 traceback in v0.8.2. Each was found by someone tripping over it or
reading the code, never by looking for it.

That is the wrong order. CLAUDE.md 4.4 already says the pipeline must not
produce a traceback for any expected failure, and it has been enforced by
noticing rather than by checking. This rung checks.

- **Audit every failure path**, module by module, rather than waiting for
  reports. For each external call and each `raise`: does it produce a typed
  exception, a message naming the fix, and a non-zero exit, or does it
  produce a traceback?
- **The three kinds that keep recurring**, all seen in v0.8.2: a call that
  fails, a call that succeeds and returns the wrong shape, and a resource
  that is present but not what was expected
- **A test that proves the absence of tracebacks**, not one example of it.
  The stale-flag scan in `tests/test_main.py` is the pattern: check the
  class of mistake across the package, not one instance
- **Structure**, which is a correctness question for anyone reading the
  repository. Done 4 September 2026: `src/` now holds only the importable
  package, as PyPA intends and as `psf/requests` and `pallets/flask` do, and
  the nine unused UI icons plus two loose root diagrams moved to
  `docs/assets/`
- **Every document current**, ADRs included

Cross-platform support moved to v0.10.0 to make room. Error handling is the
larger user-facing win and needs no CI runners this project does not have.

**Released 5 September 2026.** Seven things found by looking rather than by
tripping over them:

1. An unexpected exception was a raw traceback. Now a message, a statement
   that it is a bug rather than the user's fault, and the issue tracker.
   `TANGO_DEBUG=1` restores the traceback.
2. A corrupt or unwritable run database raised bare `sqlite3` errors. Both
   reproduced before fixing; each cause now names its own fix.
3. The .apkg write was unguarded, at the one point in the pipeline where
   failing costs everything already paid for.
4. Twenty typed exceptions had no common base, so the entry point could not
   tell an expected failure from a bug. Without `TangoError`, fix 1 would
   have made fix 2 worse: a corrupt database would have been reported as a
   bug in Tango.
5. Two settings in a real `.env` did nothing and said nothing.
   `tango doctor` reports them now.
6. Two implementations of WSL detection, both reading `/proc/version`.
7. `_TranslationTimeoutError` was dead code, never raised or caught.

Two of these were found by tests while those tests were being written, which
is the argument for scanning for a class of mistake rather than fixing the
instance.

### v0.10.0: Runs on any operating system

The `ANKI_HOST` half of this moved into v0.8.0, since a package that cannot
reach Anki on the user's own platform is not installable in any useful
sense. What is left is the rest of the portability work.

Today the repo assumes WSL2 with Anki on the Windows side. That assumption
is load-bearing in more places than it looks.

- `_translate_wsl_path()` and `_is_wsl()` exist because AnkiConnect resolves
  paths on the Windows side. macOS and native Linux need neither; native
  Windows needs no translation but different path handling
- The Makefile is the documented entry point for 23 targets, and GNU make is
  not a reasonable requirement on Windows. `make help` already prints the CLI
  equivalent of every user-facing target, the remaining work is making the
  CLI the primary path and the Makefile a convenience
- CI on Linux, macOS and Windows, because "should work" is not evidence

**All three items done 5 September 2026, and the CI item paid for itself on
its first run.** macOS passed. Windows failed five tests in three classes:

1. **A real portability bug.** An interrupted index build unlinked the
   partial `.building` file while its SQLite connection was still open.
   POSIX allows that; Windows refuses with `WinError 32` and the partial
   file survives for the next run to inherit. Both `wiktdata.py` and
   `antonyms.py` had it.
2. **Three tests comparing paths against POSIX literals**, when `str(Path)`
   uses the native separator. They were testing the platform.
3. **One test patching `Path.resolve` to a `/mnt/c` path**, which on Windows
   is a drive-relative `WindowsPath` the WSL regex cannot match.

Only the first could reach a user. The other four had simply never run
anywhere but Linux, which is the argument for the job in one sentence.

A second gap came out of the same work: a WSL user who clones to `~` rather
than `/mnt/c` writes the package somewhere Windows-side Anki cannot open,
and saw only AnkiConnect's file-not-found. That now explains itself, gated
on which host answered rather than on being under WSL, since WSLg users run
Anki inside WSL where POSIX paths are correct.

And a third, recorded in TASKS.md rather than fixed here: **the suite reads
the developer's `.env`**, so three tests passed on all six CI jobs and
failed on the machine the project is developed on. ARCHITECTURE 8.39's shape
for the third time.

### v0.11.0: Images on cards, gated to concrete nouns

ADR-009 phase 3, designed on 17 August 2026 and deliberately unbuilt since.
Scheduled here on 4 September 2026 rather than left invisible.

The feature is easy and the gate is the whole problem. Measured on five
German words, Wikimedia Commons returned two usable images: `Hund` a dog,
`Haus` a house, and then `Freiheit` a tram in Berlin, `schwierig` a hard
disk head crash, and `laufen` a 1920 coin from the **town of Laufen**, which
is the failure mode in one example: the search matched a place name spelled
the same way.

A wrong image is worse than an empty field. It teaches the wrong
association, and unlike a wrong definition it is not quiet.

- Attempt an image **only** for concrete nouns. `Class` is already on every
  card, so `noun` plus a concreteness list is a cheap gate
- Wikimedia Commons, which needs no key and is already licensed for
  redistribution, with an attribution field
- **Acceptance target: the gate measured on a real deck, not five
  hand-picked words.** Done 5 September 2026, on 835 real nouns from this
  project's own cache, and it changed the plan:

  | language | nouns | no WordNet entry | gate admits | approx. cards |
  |---|---|---|---|---|
  | French | 491 | 12.2% | 18.5% | ~9% |
  | English | 72 | 4.2% | 8.3% | ~4% |
  | German | 272 | **100%** | **0%** | 0% |

  **German has no WordNet in OMW at all**, and is 39.5% of the cached
  definitions. So images ship for the languages where a concreteness list
  exists and the field stays empty elsewhere, following ADR-011's precedent
  rather than holding the feature back for uniformity. `tango doctor`
  reports which languages support it.

  The gate requires **every** noun sense to be concrete. Reading the first
  is unreliable: OMW returns `noun.cognition` first for `planète` and a verb
  first for `enfant`. See the ADR-009 amendment.
- Appended as a new field, so index 12, under the rule in CLAUDE.md 3.2

ADR-009 also warns this should not ship before sense selection improves,
since a wrong image makes an existing weakness conspicuous. Sense selection
by part of speech shipped in v0.5.x (ARCHITECTURE 8.29), which lifts part of
that objection but not all of it: 8.25 and the `Tapa` card record that the
pipeline still takes the first dictionary entry rather than the sense
matching the video.

**Superseded on 6 September 2026, and the table above is the old plan.** The
problem was never the gate, it was the source. Commons *text search* matches
a spelling rather than a meaning, which is the whole reason `laufen` found a
coin from the town of Laufen. Resolving the lemma to a **concept** instead,
Wikipedia article to Wikidata item to image, makes the judgement
language-independent: `Hund` and `chien` are both Q144, so no German WordNet
is needed and German goes from 0% to 38%.

| language | nouns | old WordNet gate | shipped gate |
|---|---|---|---|
| French | 150 | ~9% | **33.3%** |
| German | 150 | **0%** | **38.0%** |
| English | 74 | ~4% | **23.0%** |
| overall | 374 | | **33.2%** |

So images ship for **every** language, not only the 19 in OMW, which
reverses the restriction above. Both card fields are appended: `Image` at
12 and `Attribution` at 13, the second because Commons reports
`AttributionRequired: true` on the files this actually returns.

Two corrections that came from running it rather than reasoning about it.
Commons serves originals and the first real download was 9.2 MB for a
photograph displayed at 240px, so both routes request a 480px thumbnail and
the same file is 46 KB. And resolution is batched at 50 items per request,
about 24 requests for a deck instead of 1600, which removed the runtime
argument against enabling it.

The **icon fallback for abstract words was measured and rejected**: three
FOSS icon sets, 8,250 icons between them, matched 0 of 14 abstract words,
because a UI icon set is built to label buttons rather than vocabulary. See
the ADR-009 amendment.

**Amended 7 and 8 September 2026, and this is the shipped state.**

The gate was asking Wikidata the wrong property. `instance of` refused 127
of 785 nouns, because a common noun *is* a class and a class carries
`subclass of`: `fleur` is Q506 and is an instance of nothing. Reading
`subclass of` when `instance of` is absent took coverage from 33.1% to
**45.9%** (French 43.9, German 54.3, English 33.8). Permissively, so some
abstract words get an illustrative picture, which is the named cost.

A picture whose concept describes a **different sense** than the card's
definition is now dropped before it is downloaded, on positive evidence
only: 4 of 360 imaged words, all French, each read by hand. Re-picking the
*definition* from the concept was measured and rejected for the second time,
reproducing 8.28. ARCHITECTURE 8.46 and 8.47.

Counting the credits found a licence bug: the Wikipedia lead-image route
returns a thumbnail URL, so the credit lookup asked Commons about
`500px-Scout_Girl.jpg` and got nothing, and 4 of 15 pictures in a review
deck shipped uncredited, two of them CC BY. Fixed; every picture in a real
299-card run now carries its licence line.

The card is one screen tall, with the picture taking the height the text
leaves between 16vh and 45vh. Verified against the live collection: Anki
updates the notetype styling and template on import when the ID and field
schema match, and merged 5311 notes without forking.

**Images stay off by default, and are asked for per run with `--images`.**
`IMAGES_ENABLED` still sets the default for every run, and `--no-images`
overrides it the other way. The reasoning is cost rather than doubt: a
picture roughly doubles a deck that already carries audio (4.8 MB of 10.1 MB
on a real 299-card run) and adds about 24 requests, so it is a choice to
offer rather than a cost to impose. Trying it is now one flag instead of an
edit to `.env`.

### v0.12.0: Runs on modest hardware

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
| `pymupdf` | 63 MB | **nothing, zero references in `src/`, `scripts/` or `tests/`** |

**torch + nvidia + triton is 4.5 GB, 76% of the install, and none of it is
declared**, it all arrives through `argostranslate`. The CUDA stack lands
on machines that will never have a GPU. CLAUDE.md 3.6 has been saying
"roughly 1.5GB" for this; the real figure is three times that. Step 1 has
since removed `nvidia` and `triton` outright and cut torch to 725 MB.

**Why torch is there at all, traced rather than assumed:**

```
argostranslate → stanza → torch → nvidia-cudnn/cusparselt/nccl/nvshmem, triton
```

Translation *inference* does not use torch. It uses `ctranslate2` and
`sentencepiece`, together about 75 MB. `stanza` is pulled in for **sentence
boundary detection**, splitting a paragraph into sentences, and it brings
the entire CUDA stack with it. 4.5 GB to find full stops.

argostranslate already knows this: it depends on `minisbd`, a minimal
sentence boundary detector, and `translate.py` selects `MiniSBDSentencizer`
when the installed language package ships minisbd rather than stanza.

Two things block simply removing it, both verified in the installed source:

- `argostranslate/sbd.py:12` is a bare `import stanza`, unguarded, unlike
  the `import spacy` directly above it, which sits in a `try/except`. So
  uninstalling stanza breaks argostranslate at import time, not just on the
  stanza path.
- `settings.stanza_available` is referenced by `translate.py:457` but is
  **defined inside a docstring** in `settings.py`, so it is not a setting at
  all. `ARGOS_STANZA_AVAILABLE` does nothing.

**And a third, traced on 8 September 2026, which ends this line of attack:
`minisbd` itself requires `stanza==1.10.1`.** Swapping to it removes
nothing. The dependency graph in the installed environment reads:

```
argostranslate → minisbd → stanza==1.10.1 → torch
argostranslate → stanza==1.10.1
```

So step 4 below cannot be done the way it was written. torch leaves only if
argostranslate stops depending on stanza upstream, or if this project
vendors a sentence splitter of its own, which is a much larger change than
the rung describes. **Decided 8 September 2026: torch stays for v0.12.0**,
and the size goal is met from the other two directions below.

So the reduction is staged, cheapest first:

| step | saves | risk |
|---|---|---|
| 1. install the CPU-only torch wheel (`--index-url .../whl/cpu`), **done, 26 August 2026** | 3.7 GB measured: `nvidia` and `triton` gone, torch 1.1 GB to 725 MB | none, no code change, no GPU here to lose |
| 2. ~~drop `pymupdf`~~ **split `libretranslate` out of the translation extra** | 63 MB of pymupdf, plus lxml and bs4 | small: anyone running a local LibreTranslate server installs it explicitly instead |
| 3. ~~install spaCy models on demand, not all nine~~ **nothing to do for a user; clean the contributor venv instead** | 0 MB for a user, ~450 MB for a developer | none |
| 4. ~~guard the stanza import upstream, ship minisbd packages~~ **not possible as written, see above** | 0 | `minisbd` requires `stanza` |

**Steps 2, 3 and 4 were all wrong, and were corrected on 8 September 2026 by
tracing the installed environment rather than reading this table.** Each is
worth recording, because the same mistake produced all three: this table was
written from the development venv and read as though it described a user's
install.

- **`pymupdf` is not a stray with "zero references".** Nothing in `src/`
  imports it, which is what the old note measured, but it arrives through
  `argos-translate-files`, which arrives through **`libretranslate`**. It is
  the cost of running a local translation *server*, and the way to drop it is
  to stop requiring that server for anyone who translates through
  argostranslate or a community mirror.
- **The spaCy models are a developer's cost, not a user's.**
  `pyproject.toml` declares no model at all and `make all` installs exactly
  one. The twelve on this machine, including both `sm` and `md` for es and
  fr, came from testing. A user was never paying for them.
- **Step 4 cannot work**, because `minisbd` depends on `stanza`.

So the user-facing weights are three, and only three: the base install
(**334 MB**, measured 3 September 2026 in a clean venv), **one dictionary
index per language** (125–423 MB), and **torch at 725 MB** for anyone who
installs translation. The indexes are the largest of the three for anyone
who is not translating, and are where this rung's remaining effort goes.

**After step 1, measured 26 August 2026, the three biggest things left are
torch, the indexes and the spaCy models**, in that order:

| | 26 August 2026 | 8 September 2026 |
|---|---|---|
| `torch` (CPU build) | 725 MB | 725 MB |
| `dictionaries/` | 820 MB, three languages | **1083 MB**, four: fr 423, de 305, en 236, ru 131 |
| spaCy models plus `sudachidict_core` | roughly 400 MB, nine | roughly 450 MB, **twelve** |
| `.tangovenv` total | 2203 MB | 2.3 GB |

`dictionaries/` grew because ADR-011 added the English index, and the model
count grew through testing. Neither is a regression; both are the
development machine drifting, which is precisely why the row above it about
a *user's* install has to be measured in a clean venv instead.

So torch is still the largest single package even after losing 3.7 GB, which
is what step 4 is about. `dictionaries/` is larger than any one of them, but
only because three languages are installed; one index is 126 to 404 MB.
81.4% of the German index is inflected forms (8.30), which are needed for
lookup but are mostly a word plus a pointer, whether that warrants the
current row format is an open question with a real number attached to it.

A realistic floor for a one-language, no-translation install is therefore
roughly **250–300 MB of code plus one index**, and the index dominates.
Worth knowing before optimising the wrong half.

Proposed acceptance targets. Step 1 is done and measured; the rest are still
the shape of the goal rather than findings. Note what step 1 did **not**
reach: a translation install is 2.2 GB, nowhere near the 600 MB below, and it
cannot get there while torch is 725 MB of it. That is step 4, and it needs an
upstream change:

- base install, no translation, one language: **≤ 300 MB** of code.
  **Not met, and the target is wrong rather than the work.** Measured
  10 September 2026 in a clean virtual environment from the published
  package: **316 MB across 55 packages**, 16 over. spaCy is 119 MB of it and
  numpy 41 MB, and CLAUDE.md 3.6 already records the numeric stack as 236 MB
  and as the floor while spaCy is the NLP engine. That leaves 64 MB for
  everything else, so the target was set below what the architecture allows.
  Hitting it means changing NLP engine, which is not a v1.0.0 activity and
  would cost far more than 16 MB is worth. Carried to a future rung as a
  question about the engine, not as a size cleanup.
- with translation: **≤ 600 MB** of code, which step 1 alone does not reach.
  Measured after it: 2203 MB, of which torch is 725 and the nine spaCy models
  are roughly 400. It needs steps 3 and 4 as well
- ~~peak RSS on a normal run: **≤ 1 GB**~~ **met, 564 MB**
- ~~index build completes within **2 GB RAM**~~ **met, 38 MB**
- ~~a full run completes on a **4 GB / 2-core** machine~~ **met**
- per-language index: currently 131–423 MB, and worth asking what could be
  dropped or compressed. **Still open, and now the only open item on this
  rung's size half.**

**Measured 8 September 2026 with `scripts/measure_footprint.py`, and the
memory half of this rung was already finished before anyone looked.** Each
stage runs in its own interpreter, because `ru_maxrss` reports a high-water
mark for the whole process and measuring them together would attribute the
worst to whichever ran last:

| stage | peak RSS | target | |
|---|---|---|---|
| import every module, before any work | 268 MB | none | the floor |
| spaCy loaded, 1650 words tokenised | **447 MB** | 1024 MB | the heaviest stage |
| a 400-card package built and written | 33 MB | none | |
| index built from 200k records | **38 MB** | 2048 MB | |
| index opened and queried, 994k rows | 23 MB | 2048 MB | |
| **a real run end to end, images on** | **564 MB** | 1024 MB | 185 cards, 4m04s |

The real run is the one that counts: `2yHn8uc5_-4` with `--force --images`,
185 cards, 60 pictures, 564 MB peak. A 4 GB machine has room for that six
times over.

**And the parenthesis in the old third bullet was wrong.** The index build
was called "the most memory-hungry step by far"; it is 38 MB, the lightest
substantial stage there is, because `build_index` streams the archive line
by line and flushes batches with `executemany` rather than reading it in.
The heavy stage is spaCy, at 447 MB, and that is the model itself rather
than anything this project controls.

That is the fourth item on this rung to survive contact with a measurement
badly, after pymupdf, minisbd and the spaCy models. The common cause is the
same each time: the rung was written from reasoning about the code rather
than from running it.

High-end hardware should be able to spend more, not merely avoid crashing:
worker counts, batch sizes and cache behaviour should scale to what the
machine has rather than being fixed at defaults chosen on one laptop.

#### The freeze work, folded in here on 8 September 2026

This was a rung of its own, v0.13.0, until the README was read against this
file and did not have one: it went v0.12.0, v1.0.0. Rather than add a tag to
the README, the tag was removed from here, because none of what it held is a
capability. It is a document, a policy and a sweep, and a release exists to
deliver something a user can run.

- ~~**The compatibility document**: every item in §3 below, pinned and
  tested.~~ **Done**, `docs/COMPATIBILITY.md`. It is not prose:
  `tests/test_compatibility.py` reads its tables and compares them against
  the running code in both directions, so a surface that drifts fails the
  build. The unmade-promise direction matters as much as the broken-promise
  one, and was the gap: the existing command test asserted a subset, so
  `uninstall` and `repair-images` shipped without ever being frozen.
  It gains `--images/--no-images`, added to §3 on 8 September 2026.
- ~~**A deprecation policy**: what a `0.x` to `1.0` break costs a user.~~
  **Done**, section 8 of the same document, written from what this project
  actually broke rather than from a template.
- ~~**The coverage sweep**, across the languages a user can actually have an
  index for, de/fr/ru/en, twelve pairs.~~ **Done**, 10 September 2026, all
  sixteen combinations. ARCHITECTURE 8.55. It could not have been run before:
  the sweep script had invoked the flag surface v0.7.0 deleted since
  3 September, so every combination failed and the only tool that would have
  noticed was the broken one. es/ja/ko/pt/zh are reported honestly as usable for cards but
  sparse until the user builds an index, which is what `tango doctor` and
  `tango languages` already say. Decided 8 September 2026, against building
  five more indexes at roughly 1.5 GB, which would have fought this rung's
  own goal.
- ~~**The transcript single point of failure, documented** rather than
  fixed.~~ **Done**, in the README under "Known limitations", where a user
  who hits it will look, rather than only in a handover.
  `youtube-transcript-api` is the only extraction path and stays that way for
  1.0, decided 8 September 2026.

Dropped from the old rung: backfilling GitHub release notes for v0.4.1 to
v0.4.5, which was done on 3 September 2026. All twenty-one tags have notes.

### v0.12.1: Documentation that matches the repository

A patch rung, and it exists for one reason: **the README is also the PyPI
description, and PyPI freezes it at upload.** v0.12.0 was tagged and, before
it was published, an audit found four ADR citations pointing at `docs/`
rather than `docs/adr/`, a structure tree still listing three documents at
the repository root five months after they moved, a module list ten of
fifteen long, and a line promising the `translation-server` extra "arrives in
v0.12.0" on the page that would have announced v0.12.0.

Publishing that would have burned every one of them onto a page nobody can
edit. v0.8.1 exists for exactly this reason and cost a release; this cost a
patch number instead.

The worst finding was not in a document at all. A user-facing error told
people to read a `languages.txt` file under `docs/`, which has never existed
in this repository. That is
the v0.7.0 flag-removal class again: an instruction that cannot be followed,
with nothing failing when it stopped working.

Two guards came out of it, both in `test_hard_constraints.py`:

- a document or message naming a file that is not there fails the build, and
  it caught a mistake in the corrected structure tree within a minute;
- the README's roadmap table must agree with `__version__`, because a "next"
  marker is a live claim on a frozen page. The published v0.11.0 page still
  says v0.9.0 is next and always will.

---

### v1.0.0: A finished CLI

**Released 11 September 2026.**

A fully-fledged, optimized command-line tool that installs from a package,
runs on Windows, macOS and Linux, and works on low-end and high-end hardware
alike. §3 is frozen, documented and tested. No new features, everything
above is done or explicitly deferred.

Two things closed on the day, and one of them bends the rule above.

**The declared floor was tested on one platform of three.** CI ran 3.10,
3.11 and 3.12 on Linux but only 3.12 on macOS and Windows, so "runs on
Windows, macOS and Linux" at the declared minimum was a promise nothing
checked. Both now run the floor as well. That is test coverage of an
existing claim rather than a feature.

**Shell completion is the one addition, and it is an argued exception.**
`add_completion=False` sat in the Typer app with no comment, no ADR and no
recorded reason. Typer supplies `--install-completion` and
`--show-completion` for nothing, and a twelve-command CLI is exactly the
kind that wants them. Calling a CLI finished while it cannot complete its
own subcommand names read worse than the exception does. It is frozen in
§3 along with everything else, so it is the last.

Enabling it also exposed a gap in the guard: `tests/test_compatibility.py`
checked the options of four subcommands and never the top level, so two
options joined the public surface and every compatibility test still
passed. That direction, a promise nobody made, is the one the test exists
for. It now covers `tango` itself.

**What is deliberately not in 1.0.0**, and is recorded rather than
forgotten: the base install is 316 MB against a 300 MB target that was set
below what the architecture allows (spaCy and numpy are 160 MB of it), and
the dictionary index could shed roughly 20% of its bytes at the cost of a
full re-download per language. The second is a real decision with a real
price and is not being taken to make a deadline.

---

## 3. What v1.0.0 freezes

This is the "public API" SemVer talks about. Nothing here may break without
a `2.0.0`. **None of it is currently written down anywhere else, which is
why `MODEL_ID` could be described as sacred for a year while holding none of
this project's cards.**

1. **The notetype.** `MODEL_ID`, `DECK_ID`, and the field names and order in
   `cards.FIELDS`. Fields may be **appended** (with a migration); indices
   0–13 are what every already-imported card is bound to. This said 0–9
   until 9 September 2026, which predated IPA and Pronunciation (v0.5.0) and
   Image and Attribution (v0.11.0). A freeze list that names the wrong range
   freezes the wrong thing, so `cards.FIELDS` is the authority and a test
   now pins CLAUDE.md 3.2's table to it.
2. **The CLI surface.** Command names and their options: `run`, `review`,
   `backlog`, `languages`, `doctor`, `setup`, `install-model`,
   `install-translation`, `build-dictionary`, `build-antonyms`,
   `uninstall`, `repair-images`, and the options `--deck`, `--language`,
   `--def-lang`, `--force`, `--no-cache`, `--images/--no-images`,
   `--verbose`. `uninstall`, `repair-images` and `--verbose` shipped after
   this list was written and were missing from it.
3. **Exit codes.** `0` success, `1` a failure the tool has a message for,
   `2` a usage error from Click, `3` a package was written but not one card
   got a definition. Added 9 September 2026, after a run that produced 97
   definition-less cards exited 0 and nothing scriptable could tell that deck
   from a good one. ARCHITECTURE 8.53.

4. **Configuration keys.** Every `ANKI_*`, `MW_API_KEY`, `DB_PATH`,
   `DICT_DIR`, and the rest of `.env.example`, which is the authoritative
   list and is checked against the code by a test. `DEF_LANG` was named here
   and is not a setting at all: it is the `--def-lang` flag, already frozen
   by item 2. The same mistake was in the wiki and is fixed there too.
5. **On-disk schemas.** `pipeline.db` (definition cache, vocabulary, runs,
   backlog) and the dictionary index (currently v2). A schema bump costs a
   full re-download per language, 288 MB de, 682 MB fr, 278 MB ru, so it
   is a real cost to a real user, not an internal detail.
6. **Output.** The `.apkg` filename pattern `{video_id}_{YYYYMMDD_HHMMSS}`
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
- distribution to other language ecosystems, npm, crates.io, alongside PyPI

These are plausibly a **separate expansion project sharing a common premise**
with this one, not later versions of this one. Keeping them off the ladder
is what lets 1.0.0 mean "finished" instead of "paused".

**Raised 27 August 2026 and parked here undecided:** learning from the user's
own y/n/s answers so the pipeline stops asking, and a proficiency level so
common words never become cards. The second half needs no model at all, only
a frequency-ranked word list, and it belongs on the ladder rather than here:
it is a filter and a smaller download, which is v0.12.0's goal. The first half
is a classifier trained on user decisions, which is what the third bullet
above excludes. So the two halves land on opposite sides of this line and
should not be built as one thing. TASKS.md has the full note, including the
one part that is cheap, in scope and time-sensitive: nothing currently
records what the user answers, so the training data for the first half is
being discarded on every run.

**What that costs now: almost nothing. What it requires now: one thing.**
The seam those surfaces would consume is the card payload, the name-keyed
dicts `cards._build_note()` and `_build_fallback_note()` construct before
`_note_fields()` flattens them for genanki. That structure is already
independent of genanki and already keyed by name.

This matters most for the cross-ecosystem idea, because that one is easy to
mis-scope. A Rust or JavaScript implementation would not reuse this Python;
what travels between ecosystems is the **format**, not the code. So the only
pre-1.0 obligation is to keep that payload clean and, at freeze time,
specify it, a documented JSON shape for one card. Everything else is the
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
  rather than carried forward, that number has gone stale four times
- `ROADMAP.md`, this file, with the shipped rung marked and the next one
  still describing real work
