# SESSION.md — Current working state

Last updated: 18 August 2026, after the v0.5.x line was tagged and the
repository was swept for inconsistencies. Sections 3 and 7 were revised on
26 August 2026 for the torch install size; nothing else in this file was
re-verified that day, so read the rest as of the 18th.

Read this to understand exactly where development stands and what was learned.
Everything here was checked against `git log`, the working tree, and the
installed environment at the time of writing, not recalled from memory. See
6.9 for why that distinction matters in this repository.

---

## 1. Where the project is

**Tagged release:** v0.5.3, part of speech in the learner's language. The
0.5.x line is complete. Every one of the thirteen tags now has GitHub
release notes, v0.4.1 to v0.4.5 having been backfilled from CHANGELOG on
18 August 2026. The 0.5.x line ran v0.5.0
(pronunciation on cards, the migration release), v0.5.1 (pronunciation
describes the word on the card), v0.5.2 (audio plays inside the card) and
v0.5.3. `CHANGELOG.md` has an entry for each.
**In development:** v0.6.0, card quality. Four of the five items on that
rung are done: filler sounds no longer become cards, inflection pointers
resolve to a real definition in Russian as they already did in German and
French, the definition cache key carries the transcript language as well as
the definition language, and the run names the words that got no definition.
Antonyms is the one left, and the only item on the rung that needs a data
source the project does not have. `ROADMAP.md` has the ladder to v1.0.0 and
the rule that picks the number; CLAUDE.md 15 is the short form, and
CLAUDE.md 16 is the commit and tag format.
**HEAD:** ahead of `tango-origin/main` by the CPU-torch work of
26 August 2026, unpushed. Working tree clean.
**Pull requests and issues: none open.** All six Dependabot pull requests were
handled on 25 August 2026 (five merged, thinc 9.1.1 closed as unresolvable
against spaCy 3.8), and issues #1, #13 and #16 were closed against measured
evidence rather than assumption. #13's exact reported cases were re-run: the
pipeline collapses `joue`/`jouent` to one `jouer` and `sors`/`sortir` to one
`sortir`.

**Both audio behaviours are confirmed on real cards, by ear, not inferred.**
Embedded audio plays on opening a card and can be replayed. Cross-language
keeps the transcript language throughout: a German word defined in French
carries `[haʊ̯s]` and `tango-de-haus-*.mp3`, never maison's. The media
filename hashes `language:lemma`, so a French recording could only ever be
named `tango-fr-*`, and no such file appears in a German package.

**The remote is `tango-origin`, not `origin`, and there is only one.**
`origin` used to point at `AlphaNerdFx/Youtube-Anki-Flashcards`, which no
longer exists, and it was removed on 18 August 2026. Keep it that way. While
both existed, `git log origin/main..HEAD` printed nothing and read exactly
like "fully pushed", because it compares against a ref that does not
resolve; seventeen commits sat unpushed behind that. Prefer `git status -sb`,
which names the real upstream.
**Test state:** 937 passing, 0 failing, 24 integration deselected (`make test`)
**Coverage:** 88% overall, 2549 statements, 308 missed, measured 18 August
2026. Weakest: `translation.py` 71%, `__main__.py` and `transcript.py` 82%.
The 87% carried here before was stale again, taken about 1500 lines ago.
**Overall completion estimate:** roughly 85 percent toward a v1.0.0 CLI tool,
roughly 25 percent toward the full multi-surface product vision

The pipeline works end to end for English with no setup beyond the spaCy
model, and for any language with a built dictionary index. Cross-language
mode (`--def-lang`) works for all 12 pairs among de/fr/en/ru, with
translation models installed.

Start any session with:

```bash
make doctor       # or: python -m pipeline --doctor
```

It reports spaCy models, dictionary indexes and their sizes, translation
pairs, the MW key and AnkiConnect reachability, and prints the command that
fixes anything missing. It was written because nearly every failure
investigated in this project turned out to be setup rather than logic, and
none of it was visible from the failure itself.

---

## 2. What shipped in v0.4.5

67 commits. The release is a correctness release; the tag message carries the
full list. The four that mattered most:

**The deck duplicate check could not see most cards.** It read the note field
named `Front`, while the generated model's first field is named `Word`. A
deck built by this pipeline returned zero fronts, so every previously added
word came back NEW. Measured: 0 of 1036 notes visible in one deck, 305 of
2172 in a hand-built one across 12 note types. Now reads the lowest-`order`
field, which is what Anki itself treats as a note's identity.

**Cross-language definitions were broken four ways at once**, each hiding the
next: `ARGOS_PACKAGES_DIR` in `.env` pointed at an empty directory and
argostranslate reads that variable itself, so the pipeline saw no models
while any shell that skipped `.env` saw them all; the failure was a bare
`pass`; untranslated words then reached English sources, so German "je"
matched the English letter J; and the first translation of a run takes 43.5s
against a 15s per-word timeout.

**importPackage shared the 5-second timeout** meant for quick queries. Anki
rebuilds indexes before answering and that scales with the collection, so
imports that worked at 40k notes failed at 57k.

**A pasted YouTube URL aborted `make run` before the pipeline started.** The
video id was interpolated into printf's *format string*, and a URL ending in
`%3D` made printf fail the recipe.

---

## 3. What shipped after v0.4.5

**Merriam-Webster examples were parsed and then discarded** at the call site.
English example coverage went from 14% to 72% on a full video. Third instance
of the fetched-parsed-dropped shape.

**Cross-language runs violated CLAUDE.md 3.3.** Examples, synonyms and
antonyms were taken from the target-language entry, so a German video with
`--def-lang ru` shipped Russian sentences. Gated at all three call sites.
ARCHITECTURE 8.25.

**Cross-language runs now fall back to a native definition** rather than
giving up. 46 of 459 cards on a German deck said "No definition found" while
the German index had the word. ARCHITECTURE 8.26.

**`--doctor`, `--install-model`, `--install-translation`**, and `make help`
now prints the CLI equivalent of every user-facing target, so `make` is a
convenience rather than a requirement.

**`scripts/coverage_matrix.py`** sweeps every language combination and reads
the card fields back out of the generated packages.

**Sense selection by part of speech**, the item 8.28 was rejected in favour
of. The index holds one row per (word, part of speech) and the pipeline took
the first, so a video about walking defined `marcher` as a noun. Now filtered
by the tag spaCy gave the word in its own sentence, and a `name` row is never
selected. Measured on three real videos first: 60 of 1209 picks change,
roughly 39 better to 14 worse, against word-overlap's 1 to 14. ARCHITECTURE
8.29.

**The install lost 3.7 GB of CUDA nobody could call.** `argostranslate ->
stanza -> torch>=1.3.0` names no wheel variant, so pip took the CUDA build
and its `nvidia` and `triton` companions: 4.5 GB, 76% of `.tangovenv`, and
`torch.cuda.is_available()` was False here the whole time because the driver
was too old. `make translate-setup` installs the CPU build from PyTorch's own
index, and as of 26 August 2026 it also repairs an environment that already
has the CUDA build, which installing from the index alone does not do.
Applied here the same day: 5.9 GB to 2.2 GB, `make test` still green.
ARCHITECTURE 8.41.

---

## 4. Uncommitted state

None. `ADR-009 phase 1` is committed in full — the index half (8.30), the
model-ID correction (8.31), and the card half with the notetype alignment
that keeps imports merging (8.32).

**Anki housekeeping: done.** Verifying 8.32 was done in the `AI Tester`
profile, not `User 1`. It left an empty forked notetype behind
(`YT Anki Pipeline — Recognition-da2c0` at `1607392322`), since AnkiConnect
has no `deleteModel` action; that was removed by hand in the Anki UI on
15 August 2026. Re-checked afterwards: one notetype at `1607392321`, 12
fields, 207 notes intact.

`User 1` was inspected read-only first, and that inspection found the 8.33
bug before it could do damage: the alignment resolved the notetype by name,
and in that collection the plain name `YT Anki Pipeline — Recognition`
belongs to a 1134-note notetype with an older schema, while this pipeline's
2135 cards sit on `YT Anki Pipeline — Recognition-6c3a0` at 1607392321.
Resolving by name would have added six fields to the wrong notetype. Fixed
to resolve by `MODEL_ID`.

**The alignment has since been applied to `User 1`,** on 15 August 2026.
`ensure_model_fields` returned `['IPA', 'Pronunciation']`, and the result
was verified by diffing all 2135 notes before and after:

- 2135 notes before and after, identical note ids
- **0 notes with any changed field value**
- all now carry 12 fields, both new ones empty
- notetype name unchanged (no fork), collection notetype count unchanged at
  115, total notes unchanged at 28 596
- re-running the alignment returns `[]`, so it is idempotent

Anki is back on the `AI Tester` profile.

**A note on `loadProfile`, and a correction.** Two calls to switch back to
`AI Tester` returned `{"result": true}` while `getActiveProfile` kept
reporting `User 1`. This file previously recorded that as "almost certainly
the full-sync confirmation a schema change triggers, which AnkiConnect
cannot answer" — **that explanation was invented, not verified, and it is
wrong.** Anki was shut down accidentally shortly afterwards and came back on
`AI Tester`.

The field addition itself was verified live, against the collection, before
that shutdown — the 2135-note diff above. That it survived an unclean
shutdown is reported rather than re-measured, since checking it from here
would mean switching profiles again. Confirm it next time `User 1` is open:
Tools → Manage Note Types → `YT Anki Pipeline — Recognition-6c3a0` → Fields
should list `IPA` and `Pronunciation` at positions 11 and 12.

What is actually known: `loadProfile` acknowledged and did not take effect
within the window it was observed, and the profile did change by the time
Anki was restarted. The cause is unestablished. Do not build on the modal
theory. See 6.22.

---

## 5. The coverage matrix, and what it is for

```bash
python scripts/coverage_matrix.py --dry-run     # plan only
python scripts/coverage_matrix.py               # all 16 combinations, uncached
python scripts/coverage_matrix.py --langs de,fr
python scripts/coverage_matrix.py --cache       # faster, and lies (see below)
```

It runs `--no-cache` by default and refuses to start if AnkiConnect is
unreachable. Both defaults are scar tissue: the two failed attempts below
were a warm cache and a closed Anki.

16 combinations for 4 languages: 4 native plus 4×3 cross-language. It does
not import into Anki; each run generates a package and the fields are counted
from it.

It has already paid for itself twice. It found that 10 of 12 cross-language
pairs were silently producing native output for want of a translation model,
and it found the 3.3 violation above — because cross-language rows scored
*higher* on examples than native ones, which is impossible if the fields are
constrained to the transcript language.

**CORRECTED BASELINE — 13 August**, all 16 rows, cache off, after the 3.3
fix, the Merriam-Webster example fix and POS sense selection (8.29). This is
the number to compare against; the two earlier attempts are recorded below
because they failed in instructive ways.

`fell` is the share of definitions that came back in the transcript language
because the target lookup found nothing (8.26). 0% on native rows by
definition.

| combination | cards | defs | fell | ex1 | ex2 | syn | ant |
|---|---|---|---|---|---|---|---|
| de → native | 362 | 94% | – | 90% | 72% | 62% | 57% |
| fr → native | 207 | 99% | – | 97% | 75% | 85% | 23% |
| en → native | 271 | 100% | – | 97% | 92% | 92% | 26% |
| ru → native | 701 | 95% | – | 75% | 38% | 73% | 48% |
| de → en | 362 | 99% | 4% | 44% | 35% | 23% | 3% |
| de → fr | 362 | 98% | 14% | 49% | 39% | 9% | 7% |
| de → ru | 362 | 97% | 22% | 53% | 41% | 13% | 13% |
| fr → en | 207 | 99% | 1% | 57% | 41% | 81% | 0% |
| fr → de | 207 | 99% | 12% | 61% | 44% | 80% | 1% |
| fr → ru | 207 | 99% | 16% | 63% | 46% | 81% | 4% |
| en → de | 271 | 97% | 0% | 93% | 82% | 91% | 24% |
| en → fr | 271 | 94% | 0% | 94% | 83% | 90% | 24% |
| en → ru | 271 | 95% | 0% | 94% | 84% | 90% | 24% |
| ru → en | 701 | 100% | 0% | 35% | 21% | 24% | 0% |
| ru → de | 701 | 99% | 4% | 19% | 11% | 3% | 2% |
| ru → fr | 701 | 99% | 4% | 16% | 10% | 3% | 2% |

All 12 cross-language pairs translated. Definitions are now 94-100%
everywhere, native or not.

**What moved, and why:**

*English examples 72% → 97%, second example 52% → 92%.* The Merriam-Webster
fix: examples were being parsed and dropped at the call site. Largest single
gain in the table.

*en → de antonyms 59% → 24%, synonyms 95% → 90%, examples 98% → 93%.* The
3.3 fix, and **the drop is the point**. Those fields were being filled from
the German entry, so an English video was shipping German content. English
native antonyms measure 26%, so 24% is now consistent with the language the
fields are required to be in; 59% was German's rate (57%), which is what
gave the violation away. TASKS.md predicted this exact fall.

*Native rows otherwise flat* — de 91→90 / 65→62 / 59→57, ru 76→75, fr
identical. POS selection changes which row is read, so a word's synonyms can
come from a different sense. Within noise, and the sense is more often right.

**The standing finding, now measured on all 12 pairs rather than 3:
cross-language mode costs most of the synonyms and antonyms.** 3.3 keeps
those in the transcript language while the definition may change, so they
can only come from sources that cover the transcript language. German loses
62% → 9-23%, Russian 73% → 3-24%. French holds up (85% → 80-81%) because
OMW covers it; English holds up for the same reason. **Native definitions
still produce materially better cards for anyone who can read the target
language.**

*Russian is the weakest row and worth a look:* ru → de/fr examples collapse
to 16-19% against 75% native, the largest gap in the table.

**Two earlier attempts failed**, and both defaults above exist because of
them:

1. The first read stale cache rows written before the 3.3 fix (see 6.15).
   Fixed by `--no-cache`, now the default.
2. The second failed all 16 rows with "Anki is not running. All words written
   to backlog" — Anki was closed while it ran. The sweep needs AnkiConnect
   for the deck check even though it never imports. It now checks up front.

---

## 6. Reasoning history, dead ends, and mistakes

This section is deliberately unflattering. Everything here was a real error
made during development, and the reasoning is recorded so it is not repeated.

### 6.1 Rejected: Merriam-Webster as the only definition source

Free tier caps at 1000 requests per day and covers English only. Kept as
primary for English specifically, with dictionaryapi.dev as fallback.

### 6.2 Rejected: PONS API for multilingual definitions

Free tier is 1000 requests per *month*, requires registration, and provides
bilingual translation pairs rather than monolingual definitions. Recorded in
ADR-005.

### 6.3 Rejected: Helsinki-NLP transformers for translation

Would add roughly 2GB. argostranslate was chosen instead, which ironically
also pulls in PyTorch. The size problem was deferred, not avoided.

### 6.4 Rejected: cloud translation APIs

Google Translate and DeepL both require paid keys at production volumes.

### 6.5 Failed experiment: Webshare free-tier proxy

Transcript extraction failed with repeated 429s through the proxy and
succeeded without it. Free-tier datacenter IPs are blocked more aggressively
than residential ones.

### 6.6 Rejected: rotating proxies for the Wikimedia rate limit

Circumvents a rate limit rather than complying with it, and reverses the
reasoning already recorded in issue #8 and ADR-008. Downloading the data once
makes the question moot.

### 6.7 Mistake: misattributing English lemmatization to caption quality

Garbled lemmas were repeatedly explained as YouTube auto-caption noise. They
were an English spaCy model applying English morphology to French text.

Lesson: a plausible explanation that requires no verification is more
dangerous than no explanation at all.

### 6.8 Mistake: claiming dictionaryapi.dev had multilingual coverage

ADR-005 was designed on documentation rather than testing. 9 of 9 non-English
languages measured at 0% coverage.

### 6.9 Mistake: an unverified handoff document

A session handoff asserted changes existed that did not, and instructed
deleting `pipeline.db` on the basis of a model-ID change that had not
happened.

Lesson: a handoff document must be built from `git diff`, not memory.

### 6.10 Mistake: recommending the wrong resolution for the is_alpha conflict

A "conservative" recommendation that preserved a known-wrong pattern is not
conservative.

### 6.11 Lesson: fixtures can make bugs inexpressible

`_make_token()` was called with identical `text` and `lemma` in every early
test, so the surface-form-versus-lemma bug class was invisible to the suite.
Two separate bugs of that shape shipped.

**Six vacuous tests have now been found and rewritten**, all the same family:
two asserted `x is True or True`, which cannot fail; four patched a name the
test module had imported directly, so the patch missed and the real function
ran against this machine's state. One of those passed in full runs and failed
alone, because the result depended on whether an unrelated test had perturbed
the import first.

### 6.12 Lesson: the recurring bug shape in this codebase

Two values that should be the same, with nothing enforcing that they are:

- WordNet received `query_lemma` where `lemma` was meant
- `_fetch_from_dictapi` appended a language to a URL that already had one
- `_is_valid_token` guarded `token.text` where `token.lemma_` was used
- The batch loop built a cache key differently from `fetch_definition`
- The transcript search used the lemma where the surface form was needed
- The deck check read a field named `Front` where the model's first field is
  named `Word`
- Configured paths resolved against the working directory where the project
  root was meant
- The cache key was built from `target_language` before the native fallback
  could change it

None produced a crash. All produced valid-looking wrong output.

### 6.13 Lesson: fixing only the path you were looking at

A fix written into the found-definition path when the path that actually
executes is the not-found fallback. Now four occurrences: the Wiktionary
example fix, OMW synonyms, the second Wiktionary example, and the
`not_found_*` channels that review and backlog modes were dropping entirely.

### 6.14 Lesson: measure per language before generalising

An early claim that antonym coverage was "at the data ceiling" was measured
on French alone. German and Russian came in far higher.

### 6.15 Lesson: cached cards outlive the fix that corrected them

Twice a fix was verified in the code and then measured as still broken,
because the definition cache served the old assembled fields.

- 349 rows of German lemmas cached under `::en` kept returning false-friend
  English definitions after the cause was fixed
- 4532 rows written by a pre-fix sweep kept putting Russian examples on
  German cards after 8.25 gated them

The cache stores assembled fields, not inputs, so nothing about a fetch-path
fix invalidates them. **Clearing the affected rows is part of such a fix, not
a follow-up.** ARCHITECTURE 8.27.

### 6.16 Lesson: a coverage metric can reward the bug it should expose

Cross-language rows scored *higher* on examples than native ones — 87%
against 45%. That is impossible if examples are constrained to the transcript
language, and the impossibility is what exposed the 3.3 violation. The number
went up because the field was being filled with content in the wrong
language.

When a fix makes a coverage number fall, check whether the number was
measuring the defect before concluding the fix regressed something.

### 6.17 Dead end: sense selection by word overlap

Implemented, measured, reverted. It fixed the case it was written for — the
`tapa` card defining Polynesian bark cloth for a video about tapas bars — and
was net-negative on real vocabulary. 146 of 231 lemmas have more than one
sense; of the 15 picks it changed, one improved and the rest degraded
(`côté` → "Nom de famille", `gens` → "Clan familial", `fait` → a participle).

No threshold separates them: the correct `tapa` sense wins with an overlap of
2, and so does every bad pick. Full evidence in ARCHITECTURE 8.28, including
what the data says to build instead (POS filtering).

### 6.18 Mistake: verifying in the one context where the bug cannot reproduce

The `--def-lang` failure was reported four times and "fixed" three times
before the cause was found, because every check was a `python -c` invocation
that never loads `.env` — and the cause was a `.env` variable. Each fix along
the way was a real bug, but none was the reported one.

Lesson: reproduce through the same entry point the reporter used, before
reasoning about the code. The failing command was in their first paste of
actual terminal output.

### 6.19 Lesson: check whether your change moved the number before owning it

Reading the German output for the POS sense selection (8.29), the losses
looked bad enough to reconsider shipping: six of nine regressions were cards
whose definition became an inflection pointer, "1. Person Singular Indikativ
Präsens Aktiv des Verbs haben" instead of a definition.

Counting them before and after settled it in one run: de 18 -> 19, fr 3 -> 2,
ru 10 -> 10. The change does not move that number. Those cards were already
broken, and reading a changed pick alongside its old value made a standing
defect look like a new one, because the old value was visible and its own
badness was not being counted.

The inverse of 6.16, and the same discipline: 6.16 is a number improving
because the defect grew, this is a fix looking harmful because it made an
existing defect legible. In both cases the answer was to measure the specific
quantity rather than to reason from the diff. Filed the pointer glosses
separately rather than folding a second fix into the first.

### 6.20 Lesson: the rejected attempt paid for the one that worked

8.28 rejected sense selection by word overlap and recorded, from the same
measurement, that part of speech should separate the cases overlap got wrong.
8.29 built exactly that and it landed 39 better to 14 worse, against
overlap's 1 to 14.

Two things made the second attempt cheap. The measurement script from the
first run again unchanged against a different signal, so the decision took
one run rather than a rebuild. And the failure table in 8.28 — `côté` picking
"Nom de famille", `fait` picking a participle — was specific enough to point
at the signal that separated them, which a bare "rejected, net-negative"
note would not have been.

Worth keeping in mind when recording a dead end: write down what the data
said to try next, not only what failed.

### 6.21 Lesson: the number in the constraint was evidence nobody read

CLAUDE.md 3.1 has carried `1607392319` and `1607392321` side by side for
most of this project's life. The gap is **+2**. Nobody asked what the 2 was.

It was two forks. Anki bumps a notetype ID by one each time an import
arrives whose field list disagrees with the notetype already sitting at that
ID. The constraint text described the *consequence* of that mechanism
accurately enough to be frightening, and never once described the mechanism
— so the pair of numbers recording exactly how many times it had already
happened read as arbitrary constants.

Two things followed from finally testing it (8.32). The one-time ID
correction in 8.31 was correct but incomplete: shipping it alongside two new
card fields would have forked straight to `1607392322`, and the "never change
MODEL_ID" rule would have been broken by an import rather than by an edit —
which no amount of care about the constant would have caught. And the
mitigation turned out to be ordinary: add the fields to the notetype first.

Lesson: when a constraint carries specific numbers, the numbers are data.
Ask what would have to be true for them to hold. Here the answer was a
mechanism the constraint never mentioned, and reproducing it took one import
into a scratch profile.

The inverse of 6.9's failure. There the risk was trusting a document that
recorded something untrue; here it was trusting a document that recorded
something true without recording *why*, so the only actionable part had been
lost.

### 6.22 Mistake: writing a plausible cause into the record, same session

`loadProfile` returned `{"result": true}` twice while the profile did not
change. The cause recorded here was "almost certainly the full-sync
confirmation a schema change triggers; AnkiConnect cannot answer a modal."
That was reasoning, not evidence. Anki had in fact been shut down
accidentally, and came back on the requested profile with the change intact.

The tell was in the writing: *almost certainly*, attached to a mechanism
never observed, on the first reading that fit. Nothing was checked — not
whether a dialog existed, not whether the collection was actually
schema-modified, not whether `loadProfile` behaves this way at all.

This is 6.7 exactly ("a plausible explanation that requires no verification
is more dangerous than no explanation at all"), committed to SESSION.md and
a commit message within the same session that added a lesson about numbers
in constraints being unexamined evidence. Two guards would have caught it:
the confidence-marking rule in CLAUDE.md 7.3 — it should have been
`[Guessing]`, and calling it that would have made it obviously unfit to
write down — and simply stopping at "the cause is unestablished," which
costs nothing and stays true.

The rule this file needs: **an unverified cause goes in the report, never in
the record.** A handover may say what is not known. It may not guess.

### 6.23 Lesson: the diagnostic number existed, below the log level

The user reported two problems in one message. One was real and one was not,
and the useful part is that the report itself could not tell them apart.

*"All definitions in french, this is a german video."* False, and checkable
in a minute: the package for that run holds 406 notes, every one German, and
the deck Anki created for it holds exactly those. The French cards were in a
**different deck**, built earlier from video `92Mcmx5gVus` with `DEF_LANG=fr`
as the cross-language proof — 154 French definitions against 43 German ones
in the deck next to it. The feature working as designed, one deck over.

Two hypotheses were formed and both were wrong. The first — that the fixed
`DECK_ID` merged every run into whichever deck claimed it first — was
plausible enough to be worth checking and is *structurally* true (every
package this project has ever built declares `2059400110`, whatever name the
user asked for), yet it did not cause this: Anki matched by name and made a
new deck. Reading the collection took one query and killed the theory. This
is 6.22's rule paying off in the other direction — the guess was cheap to
have and cheap to discard, because it was never written down as a cause.

*"Only the audio, no images"* — correct, and deliberate (ADR-009 phase 3).
But checking it surfaced what the user had not reported and could not see:
**13 of 377 cards had embedded audio.** The rest silently linked out.

The failure was already counted. `_download_audio` logs "Audio: %d of %d
cards will play inline" on every run, and had logged `13 of 377` at `INFO`,
while the CLI configures `WARNING`. The number that names the bug was
computed, formatted, and dropped on the floor.

**A count that only appears at a log level the tool never enables is not
instrumentation.** Three of this project's worst bugs — 8.25, 8.34, and this
— were each found by someone eventually looking at a ratio that was true the
whole time. The fix is not "log more"; it is that a number worth computing to
diagnose a failure belongs where the user running the tool will see it.

---

## 7. Environment specifics

```
OS:              Windows 11 with WSL2 (Ubuntu)
Python:          3.10.12
Virtual env:     .tangovenv  (NOT .venv)
Project path:    /mnt/c/DSC/Career/Projects/Tango
Git remotes:     tango-origin -> https://github.com/AlphaNerdFx/Tango  (current)
                 origin       -> .../Youtube-Anki-Flashcards.git       (renamed, dead)
Anki:            running on Windows, AnkiConnect bound to 0.0.0.0
ANKI_HOST:       http://172.28.144.1:8765 (WSL gateway, changes on WSL restart)
```

Run `make doctor` for the live picture. At the time of writing:

- spaCy models for de, en, es, fr, ja, ko, pt, ru, zh
- Dictionary indexes for **de, fr, ru only**. Measured 26 August 2026: fr
  404 MB, de 292 MB, ru 126 MB, 820 MB in total. es, ja, ko, pt and zh have a
  spaCy model but no index, which is exactly the state that produces cards
  with no definitions. `make dictionary LANGUAGE=es`
- Translation models: de→en, en→de, fr→en, en→fr, ru→en, en→ru. Those six
  cover all 12 pairs, because argostranslate pivots through English
- `MW_API_KEY` set, AnkiConnect reachable
- **`.tangovenv` is 2.2 GB**, down from 5.9 GB on 26 August 2026 when the
  CUDA torch build was replaced by the CPU one and `nvidia` and `triton` were
  removed. `torch 2.13.0+cpu`, 725 MB, still the largest single package.
  `--doctor` reports the build, and re-running `make translate-setup` repairs
  a machine that still has the CUDA one

Known environment issues:

- **`ARGOS_PACKAGES_DIR` in `.env` points at an empty directory.** The
  pipeline now detects this and ignores the setting for the run, with a
  warning. Unsetting it in `.env` silences that. argostranslate reads the
  variable itself, which is why it broke translation invisibly.
- `gh issue list` fails because it resolves `origin`, which points at the
  pre-rename URL. Pass `--repo AlphaNerdFx/Tango`.
- `make test` takes ~80s warm; the first run after a cold boot is ~4 minutes,
  because the repository lives on `/mnt/c`.
- `pip install tango` installs an unrelated PyPI package. Never run it.

---

## 8. How to work on this project

Beyond CLAUDE.md and OPERATING_RULES.md, the habits this session proved
necessary:

**Reproduce through the user's entry point first.** See 6.18. Four rounds
were lost to verifying via `python -c` a bug that only existed under `make`
and `.env`.

**Ask for the actual terminal output early.** Every long investigation here
ended the moment real output appeared. The `printf: %3D: invalid directive`
line settled a five-round hunt instantly.

**Measure on real vocabulary, not on the case that motivated the fix.** 6.17
is the clearest example: a change that fixed the known-broken card and
degraded a dozen others.

**Verify by mutation, not by tests passing.** Revert the fix and confirm the
new tests fail. Six vacuous tests have been found in this suite; a green run
is not evidence that anything is checked.

**Clear the cache when changing what goes in a field.** 6.15.

**A hard constraint (CLAUDE.md 3.1, 3.2, 3.3) is worth a test that fails
loudly.** The 3.3 violation shipped because nothing pinned it.
