# Code and documentation audit, September 2026

Run 8 and 9 September 2026 against the whole package, not a release diff.
Tools: `coverage`, `ruff`, `mypy`, `bandit`, `pip-audit`, `cProfile`, and a
socket guard written during the audit to catch what none of the others can
see.

This is the record of what was found, in the order it was found, with the
numbers each conclusion rests on. Decisions that came out of it are in
ADR-012, ADR-013 and ADR-014. The mechanisms are in ARCHITECTURE 8.49
to 8.52.

## Where things ended up

| | before | after |
|---|---|---|
| coverage | 86%, 3627 statements, 494 missed | **89%, 3654 statements, 401 missed** |
| unit tests | 1204 | **1306** across 16 files, 33 integration deselected |
| ruff | 267 | **202**, every actionable category cleared |
| mypy | 26 | 25 |
| bandit | 1 HIGH, 1 MEDIUM, 2 LOW | **0 at every severity** |
| dependency advisories | 37 | **11**, all inside the translation server extra |
| transcript search, hit | 85.6 ms | **10.1 ms** |
| transcript search, miss | 131.4 ms | **9.9 ms** |
| settings that could crash a run | 18 | **0** |
| documented settings that do nothing | 1 | **0** |

## Two numbers that were wrong before anything was measured

Both would have made the rest of this document wrong.

1. **`make check` was documented as "about ten minutes"** in every handover
   the project has. Timed: **145 seconds**. The old figure was reading
   contention from background jobs left running in the same session, never a
   clean timing. Corrected in HANDOVER.
2. **A coverage run read 69%.** Source had been edited while it was running.
   Re-run clean: **86%**. That is the baseline everything here is measured
   against. Recorded because the mistake is easy to repeat and the number
   looked plausible.

CLAUDE.md 18.1 says a figure in this repository with no date should be
treated as unmeasured. Both of these had dates. Neither had been re-taken.

## Part 1: redundant code, in the order found

| when | what | size |
|---|---|---|
| 8 Sep 19:28 | `dictionaries/conceptnet_antonym_edges.tsv`, a measurement intermediate living inside a directory whose size is a v0.12.0 acceptance target | 35 MB |
| 8 Sep 19:28 | `libretranslate` required by the translation extra, so anyone translating through a mirror paid for a server they never start (ADR-014) | 134 MB, 36 packages |
| 8 Sep 23:23 | Six dead declarations across five modules. A seventh that ruff flagged was a false positive and was kept | 6 lines |
| 8 Sep 23:50 | `translation.check_packages_dir()`, unreachable: `_repair_packages_dir()` runs at import and pops the environment variable, so the diagnostic could never find the problem it was written to report | 44 lines |
| 9 Sep 00:03 | `definition.fetch_definitions` looped on `l`, which reads as `1` | readability |
| 9 Sep 00:39 | Sixteen unused imports across nine test files, three lambdas taking `l` | 19 lines |
| 9 Sep 00:39 | `typing.Callable` in four modules, deprecated since Python 3.9 | 4 lines |
| 9 Sep 02:10 | `translation.LIBRETRANSLATE_LOCAL`, read from a documented setting and used nowhere since the day it was added on 10 July 2026 (ADR-012) | 1 line, 1 setting |

Statement count moved 3627 to 3654 while 97 tests were added, so the
redundancy came out roughly as fast as the new code went in.

## Part 2: correctness and security, in the order found

- **A mutable default argument.** `transcript.fetch_transcript(languages=["en"])`.
  The list is built once at definition time and shared by every call that
  omits it.
- **Six re-raises that lost their cause.** `raise TangoError(...)` inside an
  `except` with no `from exc`, so the original traceback was dropped.
- **Two `except Exception: pass` around `ALTER TABLE`.** A locked or corrupt
  database looked exactly like a column that already existed. Replaced by
  `_add_column_if_missing()`, which can tell them apart.
- **An unvalidated URL scheme before `urlretrieve`.** The URL is built from a
  constant template so it was always https, but `urlretrieve` opens `file://`
  and `ftp://` happily. Now an enforced invariant rather than one a reader
  has to reconstruct.
- **A SHA1 call with no stated intent.** It names a cache file. Marked
  `usedforsecurity=False` so a reader and a scanner can both tell it apart
  from a cryptographic use.
- **A credit line could put live markup on a card.** Commons `Artist`
  metadata is wiki text anyone can edit, and tags were stripped before
  entities were decoded, so an escaped tag survived the stripper and
  unescaping made it work. Now decoded, stripped, then escaped.
  ARCHITECTURE 8.48.
- **Eighteen settings could stop the program at startup.** A blank numeric
  value in `.env` raised `ValueError` during `import config`, which happens
  before `main()` exists to turn it into a message. ADR-012, ARCHITECTURE 8.52.

Dependency advisories went from 37 to 11. All eleven remaining sit in the
local translation server: six in werkzeug, two in waitress, one each in
flask, stanza and nltk. None is in a package a normal install pulls.

Bandit's three surviving findings were each read and are each correct as
written, so they carry an annotation in the source giving the reason rather
than being left to be rediscovered: the `urlretrieve` whose https guard is
three lines above it, and the `spacy download` subprocess, which is list
form with a value from a fixed table, so no shell parses it and no argument
boundary can be crossed.

## Part 3: the optimization, and what it actually bought

Profiling `cards.build_package` on 400 cards over a 300-line transcript put
**`_find_in_snippets` at 66% of the build**. Three problems, each
multiplying the one above it:

1. The `isinstance` filter separating transcript lines from metadata ran
   inside the candidate loop, so once per candidate per word.
2. A regex was compiled for every candidate before anything checked whether
   the word was in the transcript at all: 984 compiles, most for words that
   were not there.
3. The transcript was casefolded once per word: 400 words times 300 lines is
   120,000 casefolds where 300 would do.

Fixed in that order. Measured on the search alone, seven runs, median:

| | before | after | |
|---|---|---|---|
| every word present | 85.6 ms | 10.1 ms | 8.5x |
| no word present | 131.4 ms | 9.9 ms | **13.3x** |
| regex compiles | 984 | 108 | |
| casefolds | 120,000 | 300 | |

The miss case was the worse of the two before, because an absent word
compiled its regex and scanned every line without ever returning early. It
now costs the same as a hit.

**One number is corrected downward.** cProfile reported the whole package
build 44% faster. Wall clock says **28%**, 176 ms to 126 ms. cProfile
amplifies per-call overhead and this change removes 480,000 calls, so it
over-credited itself. The full build is bounded by genanki's own SQLite
write at a fixed 100 ms, which is third-party.

**And one detail was nearly recorded wrong.** The substring pre-filter is
only safe if it never skips a line the regex would have matched. The first
comment written claimed German eszett as the case requiring `casefold` over
`lower`. Checked against `re` directly: false, `re.IGNORECASE` does not
match "strasse" against "straße" either, so there is nothing to lose there.
The real case is the long s, U+017F: `re.IGNORECASE` does match it against
"s", `lower()` leaves it alone, `casefold()` maps it to "s". The test now
asserts both premises before asserting the behaviour, because the reasoning
that picked the wrong example would have been just as confident about the
wrong fold. ARCHITECTURE 8.51.

## Part 4: tests

102 added, 1204 to 1306, across 16 files.

- **The socket guard**, which is the highest-value change here. CLAUDE.md
  3.5 forbids network access in the default run and had been prose only.
  An autouse fixture now refuses any non-loopback connection and names the
  test. It immediately caught **thirteen tests making real requests**. Not
  one was written badly: each mocked every source its function had at the
  time, and stopped being complete when the source list grew. ADR-013.
- **`images.py` from 66% to 93%**, which was the coverage floor for the
  whole package. The response parsers and the fetch path had never run under
  mocks.
- **The packages-dir repair**, which runs at import and had never had a test.
- **Six tests that could not fail**, rewritten to assert what their names
  promise. `test_fallback_field_empty_on_standard_note` became
  `test_a_standard_note_is_not_marked_as_a_fallback`.
- **A test pinning a spelling instead of an intent.** `IMAGES_ENABLED`'s
  default was pinned by asserting the literal string
  `os.getenv("IMAGES_ENABLED", "")` appeared in the source. It broke when
  config moved to a helper while the behaviour it names was byte-for-byte
  unchanged, verified input by input. Rewritten to read whatever default the
  assignment carries. CLAUDE.md 18.5.
- **`zip(..., strict=True)`** on a concurrency test, where a dropped result
  would have silently shortened the comparison and the test would still have
  passed.
- **A test that would have caught the `.env.example` drift** below, and did
  not exist. Three checks already asked whether the template's names were
  real and complete. None asked whether its values agreed with the code.

Every new test was mutation-verified. One survived its first mutation: an
equivalence test compared a fast and a slow code path on three cases whose
matches all happened to be in line 0 of the fixture, so a fast path
truncated to `lines[:1]` agreed with the slow one every time. Fixed by
adding middle-line, last-line and absent cases. That is CLAUDE.md 18.4
earning its place again.

## Part 5: the documentation audit

Run 9 September 2026, checking every claim against the thing it describes:
code comments against code, markdown against code, `.env.example` against
`config.py`, and the repository against what PyPI and Docker Hub actually
serve.

### Contradictions found and fixed

| where | what it said | what was true |
|---|---|---|
| `.env.example` | `MW_RATE_LIMIT=2`, `MW_BURST=5` | code defaults are 4.0 and 8, argued at length in `config.py`. Copying the template halved the throughput the project had settled on, with nothing saying so |
| `.env.example`, `translation.py` | "Set LIBRETRANSLATE_URL to your own instance and it is used first" | the constant was read and never used, since 10 July 2026 |
| `tests/conftest.py` | "ARCHITECTURE 18.7" | 18.7 is a CLAUDE.md section. ARCHITECTURE has no section 18 |
| `ARCHITECTURE.md` | a bare "(18.7)" | reads as a local section inside ARCHITECTURE, which does not exist |
| `TASKS.md` | "Migrate CLI from argparse to Typer" open | shipped in v0.7.0 |
| `TASKS.md` | "Dockerfile" open, base `python:3.11-slim` | shipped in v0.8.2, published 8 September, base is `python:3.10-slim` |
| `TASKS.md` | "Phase 3, images, is deliberately not built" | shipped in v0.11.0 |
| `ARCHITECTURE.md` §10 | "734 unit tests", "88% overall, 1963 statements", per-module table | 1306, 89%, 3654, and eleven of the fifteen module figures wrong |
| `SESSION.md`, `HANDOVER.md` | 1230 passing, 86%, 1237 collected | 1306, 89%, 1339 |
| `README.md` | `pip install "tango-anki[translation,translation-server]"` | that extra does not exist in the published 0.11.0. Verified against the PyPI JSON API |
| Docker Hub | nothing | the page had no description at all, short or full |

### Checks that came back clean

Worth recording, because a check that finds nothing is still evidence:

- **Version agreement.** `__init__.py`, the git tag, CLAUDE.md, CHANGELOG,
  the Dockerfile and the published PyPI metadata all say 0.11.0.
- **Cross-references.** Every `ARCHITECTURE n.n` cited anywhere in the
  repository resolves to a section that exists, after the two fixes above.
- **Make targets.** Every `make <target>` named in any document exists.
- **Badge pinning.** The published 0.11.0 page carries pinned `v0.11.0`
  badges and no CI badge, which is what CLAUDE.md 18.11 specifies. This is
  the first confirmation that the pinning works in production rather than
  only in `make dist`.
- **`src/` holds only the importable package.** The egg-info beside it is
  gitignored, so the claim is about tracked content and is true.
- **Every setting the code reads is in `KNOWN_ENV_KEYS`, and every key there
  is read.** Both directions, enforced by tests that already existed.

## What is left, and why

ruff's remaining 202 findings are cosmetic and were left deliberately:

| | count | why |
|---|---|---|
| UP045 | 152 | `Optional[X]` over `X \| None`. House style, a recorded decision |
| I001 | 37 | Import block sorting. Churns every file header for nothing |
| E402 | 10 | Imports under a "moved to config.py at end of project" banner. No benefit, non-zero cycle risk |
| UP031 | 4 | Percent formatting |

Coverage's weakest modules are `translation.py` at 76%, `__main__.py` at
83%, `antonyms.py` at 84% and `transcript.py` at 87%. Most of what is
uncovered in `translation.py` is the interactive download prompt.

## The pattern underneath all of it

Three of the largest findings here are the same shape, and it is worth
naming because it will happen again.

**A thing that was correct when written, and stopped being correct without
anyone touching it.** The thirteen networked tests each mocked every source
that existed at the time. `LIBRETRANSLATE_URL` was documented as working and
never was. `.env.example` shipped MW pacing that matched the code until the
code moved. ARCHITECTURE's test figures were right in August.

None of these is a mistake anyone made. Each is a claim that was true, and
then quietly was not, because nothing was watching the gap between the claim
and the thing it described. That is what the socket guard, the template
drift test and the cross-reference check are for: not to catch a careless
author, but to notice when the ground moves under a correct statement.
