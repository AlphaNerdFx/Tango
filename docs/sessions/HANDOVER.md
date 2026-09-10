# HANDOVER

Written 9 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main`, remote is `origin` |
| last tag | **`v0.12.0`**, tagged 10 September 2026 |
| `__version__` | `0.12.0` |
| `make check` | exit 0 |
| tests | **1402 unit, 33 integration deselected** |
| coverage | **89%**, 3710 statements, 400 missed, measured 9 September |
| PyPI | **0.11.0 published**, `pip install tango-anki` |
| Docker | **published as `yousseflarbi/tango`** |
| security | `make audit`: bandit clean at every severity, pip-audit 11, all in the translation server extra |
| mutation | `make mutation`: `cards.py` 536/729, `language.py` 212/281, measured 11 September |
| benchmark | `make benchmark`: 4 of 6 phases inside their thresholds here, all 6 on a Linux filesystem |

The v0.11.0 rung is released. The tag names `9cb0866`, which is the exact
commit the PyPI artefacts were built from, so a checkout of the tag rebuilds
what people downloaded. The image is on Docker Hub.

Since the tag, a code-quality and security audit has run against the whole
package rather than a release diff. What it found and changed is in the
section below, and the numbers behind it are in ARCHITECTURE 8.49 to 8.51.

**Working autonomously**, under two standing decisions taken 5 September
2026: commit and push freely, but **ask before any tag or PyPI upload**,
because a version number is burned permanently and a published description
cannot be edited. And where a crossroad is already settled in a document,
follow what was designed rather than asking again.

## What is in flight: v0.11.0, images on cards

The feature is built, measured and still **off by default**
(`IMAGES_ENABLED`). Four things landed on 7 and 8 September 2026, three of
them from the user's own list.

### Done: the gate was asking Wikidata the wrong question

Coverage sat at 33% of nouns, so the funnel was measured stage by stage. One
branch held almost all of the loss: **127 of 785 nouns resolved to an item
with no `instance of` claim**, which the gate refused.

The reasoning was sound and the property was wrong. A common noun *is* a
class, and a class carries `subclass of`: `fleur` is Q506 and is an instance
of nothing. `Hund` only ever worked because Q144 is an instance of a taxon.
Of those 127, **101 had both a `subclass of` and a picture**.

| | before | after |
|---|---|---|
| French | 163 of 488, 33.4% | **214, 43.9%** |
| German | 81 of 223, 36.3% | **121, 54.3%** |
| English | 16 of 74, 21.6% | **25, 33.8%** |
| overall | 260 of 785, 33.1% | **360, 45.9%** |

The permissive setting was the user's choice, taken with its cost named:
`Gedanke` now gets a painting called *Gedanken*, `Leidenschaft` and `regret`
get whatever illustrates them. ARCHITECTURE 8.46, ADR-009's third amendment.

### Done: a picture may not contradict the definition beside it

`palais` printed a photograph of a monumental building next to "paroi
supérieure qui sépare la fosse nasale de la bouche".

Letting the concept re-pick the *definition* was measured and rejected for
the second time (4 better, 2 worse, breaking `pays` and `kaffee`), which
reproduces 8.28. So the picture is dropped instead, before it is downloaded,
and only on positive evidence: the definition shares no content word with the
concept's description **while another same-part-of-speech row does**.

Measured after the wider gate: **4 of 360 imaged words**, all French, all
read by hand: anime, est, grève, palais. Verified end to end by reading the
fields out of a built `.apkg`, where those four carry no image and the other
fifteen in the deck do. ARCHITECTURE 8.47.

Two details of the comparison are load-bearing and both came from a wrong
answer: stems are matched **inside** the other string, or German compounds
never meet ("Heißgetränk" against "Aufgussgetränk", which cost `kaffee` a
correct picture), and stems are cut at **five characters**, or French endings
never meet ("religieuses" against "religieux", which cost `palais`).

### Done: the card is one screen tall

`.card` is a flex column at `min-height: 100vh`, and `.card-image` is the one
flexible row: the picture takes the height the text leaves, between a floor
of 16vh and a ceiling of 45vh, instead of a fixed 30vh that could not know
whether the text above had used a fifth of the screen or all of it. The
credit line moved inside the image box, because as a sibling it was pushed to
the bottom of the screen while the picture stayed at the top.

A pre-release review pointed out on 8 September that Anki renders the
template inside `<div id="qa">` while `.card` is the body, which would make
the picture a grandchild of the flex column and the rule inert. That could
not be checked here: this machine's Anki is a frozen build with no readable
Python. The CSS is now correct either way, with `#qa` joining the chain when
it exists and `max-height: min(100%, 45vh)` bounding the picture when a
percentage cannot resolve. **It still has not been seen working**, and
`output/card_preview.html` renders every card twice, once in each DOM shape,
so the two can be compared.

### Done: the PyPI badges, going forward only

`make dist` now pins, builds, `twine check`s and restores in one command, so
the manual step in CLAUDE.md 18.11 cannot be skipped. The pin step also drops
the CI badge (it cannot be pinned: `badge.svg` takes `?branch=`, and a tag is
not a branch), points the release badge at its own tag rather than
`/releases/latest`, and rewrites the four repository-relative links, which
resolve against pypi.org once uploaded and 404 there.

**The five pages already published cannot be corrected.** 0.8.0, 0.8.1,
0.8.2, 0.9.0 and 0.10.0 are frozen: PyPI has no way to edit a release
description and a version number cannot be re-uploaded. Their badges will
read the current version forever. This is fixed from the next upload onward
and not before.

### Done: a pre-release audit, which found nine things

Run on `v0.10.0..HEAD` on 8 September 2026 at the user's request, before the
tag. Two of the findings were live bugs shipping silently:

- **A fallback card could never get a picture** unless the word happened to
  have a Wikimedia recording: the candidate list read `not_found_audio`
  where it meant `not_found`. A card with no definition is the one a picture
  helps most.
- **Two words for one concept, and only one got the picture.** `auto` and
  `voiture` are both Q1420, and the resolver kept one lemma per item. The
  same shape one line down: the pending map was keyed by Commons filename,
  so two concepts sharing a lead image lost one.

And one security finding, the only one of the release: **a Commons credit
could put live markup on a card.** `Artist` metadata is wiki text anyone can
edit and a card is HTML in a webview, but tags were stripped before entities
were decoded, so `&lt;img src=x onerror=...&gt;` passed the stripper
untouched and unescaping made it a working tag. Now decoded, stripped, then
escaped. ARCHITECTURE 8.48, which also records the same shape in definition
and example fields, measured at 11 rows of 2.1M in French and 4 of 994k in
German, cosmetic today and carried to v0.12.0.

The rest: `IMAGES_ENABLED` in a developer's `.env` leaked into the suite and
made it network-dependent (CLAUDE.md 3.5); an unescaped `?` in a Commons
filename broke both download and credit; a lead image whose licence could not
be read is now refused rather than redistributed; and ten user-facing
messages told a pip or Docker user to run `make`, which is v0.8.1's bug
returning, now scanned for across the package.

## The audit, 8 and 9 September 2026

Ran against the whole package, using coverage, ruff, bandit and pip-audit,
plus cProfile for the one hot path. Full account in the session log; the
short version:

- **Redundant code.** Six dead declarations, a 44-line diagnostic that a
  repair beside it had made unreachable at import, sixteen unused test
  imports, and a 35 MB measurement intermediate that was inflating a release
  metric. Source is 120 lines lighter.
- **Correctness and security.** A mutable default argument, six re-raises
  that lost their cause, two bare `except Exception: pass` around schema
  migrations, an unvalidated URL scheme before `urlretrieve`, and a SHA1
  call that now says it is not cryptographic. Dependency advisories went
  from 37 to 11, and the 11 that remain are all inside the local translation
  server, which is now its own extra.
- **Performance.** The transcript search was 66% of a package build and is
  now 8.5x faster on hits and 13x on misses. ARCHITECTURE 8.51.
- **Coverage** 86% to 89%, with `images.py` 66% to 93%. Six tests that could
  not fail were rewritten to assert what their names promise.

`make audit` runs bandit and pip-audit together. Both are in the `dev` extra.

## Mutation testing and benchmarking, 10 and 11 September 2026

Two tools added, both slow enough to sit outside `make check`.

### `make benchmark`

Times each phase a user waits for, in a fresh interpreter, against a
threshold for each. Exits 1 when one is over.

It found that **`tango --version` loaded 1208 modules**, because `nlp`
imported spaCy and `cards` imported genanki at module scope. Both are used
only inside function bodies. Moving them took the command from 45.94s to
3.98s on this machine, cut the module count to 547, and shortened test
collection from about 44 seconds to 2.6.

The remaining 44 seconds are the filesystem, not the code. A virtualenv on
`/mnt/c` imports spaCy in 44 seconds; the same package on the Linux
filesystem takes 2.2. Every phase passes its threshold there. `tango doctor`
now reports this, because nothing in the symptom points at the cause.

### `make mutation`

Coverage says a line ran. This says whether anything would have failed if it
were wrong. Four rounds of writing tests for survivors:

| module | killed | raw | behaviour only |
|---|---|---|---|
| `cards.py` | 536 of 729 | 73.5% | 92.6% |
| `language.py` | 212 of 281 | 75.4% | 87.6% |

**What worked, and would work again:** assert what a function hands the thing
it calls. `fetch_audio(url, lemma, language)` had six survivors that each
dropped or nulled an argument, and one test killed all six.

**Two things to know before reading those numbers.** The behaviour-only
figure excludes survivors that change nothing but a log line or a string,
and the denominator was computed wrongly twice, both times flatteringly:
once by comparing two different categorisers, once by reading only the
changed lines of a multi-line logging call. Both corrections are in
ARCHITECTURE 8.57 with the raw scores beside them.

**Seven mutants cannot be killed**, all the same shape: a lookup shadowed by
the fallback beneath it. Each is recorded at its call site. Two tests that
could not fail were deleted rather than weakened until they passed. If a
survivor looks unkillable, run the mutation against the real function before
concluding it: one was nearly given a test on the strength of a
reimplementation in the harness that disagreed with the real code.

## What needs the user

1. **Look at the two review decks.** `output/IMGREVIEW2-de_*.apkg` (19 cards,
   18 with pictures) and `output/IMGREVIEW2-fr_*.apkg` (20 cards, 15 with
   pictures), reachable from Windows at `C:\DSC\Career\Projects\Tango\output\`.
   Both mix the concrete words the wider gate is for with the abstract ones it
   also admits, so the cost and the benefit are in the same deck. The French
   deck ends with the four the sense guard dropped, which should show no
   picture at all.
2. **Look at the layout.** `output/card_preview.html` renders the real CSS and
   template at 360x640, 768x1024 and 900x700, with a full card, a card with no
   picture and a sparse one. A scrollbar inside a frame means that card does
   not fit. Then the same judgement in Anki desktop and AnkiMobile, which is
   the only ground truth.
3. **Settled, 8 September 2026: Anki does update a notetype's styling and
   template on import** when the ID and field list match. Read back from the
   live collection after importing a review deck: today's CSS and template
   were both there, on notetype 1607392321 holding 5,311 notes, with no fork.
   No `updateModelStyling` step is needed.
4. **The measurement corpus, unchanged from the previous handover.** 2 more
   video ids each for de, fr, es, ru, pt, ja, zh, ko, en, plus 3 for Italian.
   Prefer manually captioned videos, 5 to 15 minutes.

## The exact next step

v0.12.0 is tagged and its rung is closed. Nothing is half-built.

1. **Publish v0.12.0.** The tag exists; the wheel and the Docker image do
   not. `make dist`, install the built wheel into a clean virtualenv and run
   it (CLAUDE.md 18.11), then `twine upload`, then build and push the image.
   Both steps are irreversible and each is its own confirmation.
2. **The three items under "What needs the user"**, all of which need eyes
   rather than code.
3. **Decide whether `IMAGES_ENABLED` defaults to true.** Everything the
   measurement can supply is in: coverage clears ADR-010's bar in every
   language, the runtime cost is about 24 requests for a deck, and the
   wrong-sense failure is measured and guarded.
4. Then v1.0.0, which adds no features. `docs/COMPATIBILITY.md` is written
   and enforced, so what remains is the decision to freeze rather than more
   building.

## Open decisions

| decision | why it is waiting |
|---|---|
| **The PyPI upload** | Needs the user's token; there is no `~/.pypirc` here |
| **Docker Hub** | Recipe verified from a local wheel. Publishing needs 1c first, plus an account and a namespace |
| **21 video ids** | The corpus is still three languages |
| **Publishing the Docker image** | Builds and runs. Pushing needs an account and a choice of registry |
| **French fixed expressions** | `d'accord` becomes `accord`. 7 of 1079 cards. Needs a hand-curated per-language list |
| **Transcript fallback** | Decided 8 September 2026: `youtube-transcript-api` will do for 1.0, documented as a known single point of failure rather than fixed |
| **Learned queue matching** | Nothing records what the user answers at the y/n/s prompt |
| **ruff and mypy debt** | 267 and 26 findings, both advisory, neither gating. The new `Optional[...]` annotations match the house style rather than ruff's preference |

## Known-broken

Nothing. `make check` exits 0.

Environmental notes, not this repository's bugs:

- **`make check` takes 164 seconds here**, measured 11 September. It was 145
  on 8 September, and it went up despite the import work that made the suite
  itself faster, because the suite gained 172 tests over the same days. The
  "about ten minutes" that every handover before those carried was never
  timed at all, and was reading contention from background jobs left running
  in the same session.
- **There is no pre-commit hook.** Run `make check` yourself before committing.
- **There is no `pip` script in `.tangovenv/bin`.** Use
  `.tangovenv/bin/python -m pip`.
- **pytest's summary line is suppressed here** for the full suite. Per-file
  runs do print it, so count that way: 1435 collected, 33 integration.
- **The pre-commit hook matches the literal text "git commit" in a command**,
  so writing a file whose contents mention it is blocked. Use the editor tool,
  not a shell heredoc.
- **`ca_core_news_sm` was installed while verifying the first-run offer.**
  Removable with `pip uninstall ca-core-news-sm`.

## Fixed since: the tests that reached the network

Thirteen tests opened outbound connections during a default run, breaking
CLAUDE.md 3.5. `tests/conftest.py` now has an autouse fixture that refuses
any non-loopback connect and names the offending test, so the constraint is
enforced rather than described, and the thirteen are mocked. ARCHITECTURE
8.49.

## Conventions worth not relearning

Read CLAUDE.md section 18. The ones that earned their place again:

- **Mutation-verify every new test.** Three mutations survived in this session
  and all three were the test's fault: a gate test that could not see
  `subclass of` because the single-lemma path never passes both lists, a
  fixture whose extra shared word hid whether stems were truncated, and a
  part-of-speech test whose other row agreed anyway.
- **Run the feature and watch it work.** The sense guard was driven against
  real Wikidata and the real French index before a line of test was written,
  which is how the German compound problem was found at all.
- **Measure it, and write the date next to the number.** "807 nouns, de 245"
  in the previous handover was wrong: the cache holds 1838 rows and 785 noun
  lemmas, fr 488, de 223, en 74, measured 7 September 2026.
