# HANDOVER

Written 8 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main` |
| last tag | **`v0.10.0`**, released 5 September 2026 |
| `__version__` | `0.10.0` |
| `make check` | exit 0 |
| tests | **1204 unit, 33 integration deselected** |
| PyPI | `pip install tango-anki`, latest published 0.10.0 |

Everything after v0.10.0 is the v0.11.0 rung, images on cards.

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

**This is the one piece that has not been seen working**, and there are two
open questions for the user, both below.

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
3. **Settle whether a CSS change reaches an existing collection.** Anki
   matches a notetype by ID on import; whether it then updates the styling is
   unverified here, and if it does not, the new layout only reaches a fresh
   collection and the pipeline needs an AnkiConnect `updateModelStyling` step
   next to `ensure_model_fields`. Anki was not running while this was written.
4. **The measurement corpus, unchanged from the previous handover.** 2 more
   video ids each for de, fr, es, ru, pt, ja, zh, ko, en, plus 3 for Italian.
   Prefer manually captioned videos, 5 to 15 minutes.

## The exact next step

1. The three items above, all of which need eyes rather than code.
2. Then decide whether `IMAGES_ENABLED` defaults to true. Everything the
   measurement can supply is now in: coverage clears ADR-010's bar in every
   language, the runtime cost is about 24 requests for a deck, and the
   wrong-sense failure is measured and guarded.
3. `tango doctor` reports image coverage but not the sense guard. Worth a line
   once the default is decided.

## Open decisions

| decision | why it is waiting |
|---|---|
| **`IMAGES_ENABLED` default** | Needs the two review decks looked at |
| **CSS reaching an existing collection** | Unverified, Anki was not running |
| **21 video ids** | The corpus is still three languages |
| **Publishing the Docker image** | Builds and runs. Pushing needs an account and a choice of registry |
| **French fixed expressions** | `d'accord` becomes `accord`. 7 of 1079 cards. Needs a hand-curated per-language list |
| **Transcript fallback** | The whole pipeline depends on one extraction path |
| **Learned queue matching** | Nothing records what the user answers at the y/n/s prompt |
| **ruff and mypy debt** | 262 and 26 findings, both advisory, neither gating. The new `Optional[...]` annotations match the house style rather than ruff's preference |

## Known-broken

Nothing. `make check` exits 0.

Environmental notes, not this repository's bugs:

- **`make check` takes about ten minutes here.**
- **There is no pre-commit hook.** Run `make check` yourself before committing.
- **There is no `pip` script in `.tangovenv/bin`.** Use
  `.tangovenv/bin/python -m pip`.
- **pytest's summary line is suppressed here** for the full suite. Per-file
  runs do print it, so count that way: 1237 collected, 33 integration.
- **The pre-commit hook matches the literal text "git commit" in a command**,
  so writing a file whose contents mention it is blocked. Use the editor tool,
  not a shell heredoc.
- **`ca_core_news_sm` was installed while verifying the first-run offer.**
  Removable with `pip uninstall ca-core-news-sm`.

## One thing found and deliberately not fixed

**Twelve tests in `test_definition.py` attempt real network connections**, 24
attempts in total, which breaks CLAUDE.md 3.5. Found by running the suite with
`socket.socket.connect` patched to raise. Pre-existing and unrelated to the
image work, so reported rather than folded into it (CLAUDE.md 7.4).

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
