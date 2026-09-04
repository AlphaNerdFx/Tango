# HANDOVER

Written 5 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main`, in sync with `tango-origin` |
| last tag | **`v0.9.0`**, released 5 September 2026 |
| `__version__` | `0.9.0` |
| `make check` | exit 0 |
| tests | **1059 passing, 24 integration deselected** |
| PyPI | `pip install tango-anki` |

v0.9.0 is released and on PyPI. Nineteen tags, nineteen GitHub releases.

**Working autonomously from here**, under two standing decisions taken
5 September 2026: commit and push freely, but **ask before any tag or PyPI
upload**, because a version number is burned permanently and a published
description cannot be edited. And where a crossroad is already settled in a
document, follow what was designed rather than asking again.

## What v0.9.0 is

**Nothing fails without saying why.** The rung was "runs on any operating
system"; that moved to v0.10.0 to make room, because error handling is the
larger user-facing win and needs no CI runners this project does not have.

Every release so far fixed failures one at a time, as someone tripped over
them. This rung looks for them instead. Five found so far, each reproduced
before being fixed:

1. **An unexpected exception was a raw traceback.** Now a message, a
   statement that it is a bug rather than the user's fault, and a link to
   the issue tracker. `TANGO_DEBUG=1` restores the traceback.
2. **A corrupt or unwritable `pipeline.db` raised bare `sqlite3` errors.**
   Both reproduced. Now typed, with the cause and the fix named separately
   for "not a database", "cannot write here" and "locked by another run".
3. **Twenty typed exceptions had no common base**, so the entry point could
   not tell an expected failure from a bug. Without `TangoError`, fix 1
   would have made fix 2 worse: a user with a corrupt database would have
   been told "this is a bug in Tango, please report it".
4. **Two implementations of WSL detection**, both reading `/proc/version`.
   Introduced earlier the same day. Collapsed to one.
5. **`_TranslationTimeoutError` was dead code**, defined and documented and
   never raised or caught. Found by the scanning test while it was being
   written.

## Also done

- **CLAUDE.md section 18, "What has worked".** Eleven practices, each with
  the date and the specific find that earned it. Added as a new section
  rather than inserted, because sections are cited by number throughout the
  code and renumbering would break every reference.
- **`src/` holds only the importable package.** `src/images/` held nine
  icons for a UI that does not exist, never imported and never packaged.
  Checked against PyPA guidance and six well-known repositories before
  moving anything. They and two loose root diagrams are in `docs/assets/`.
- **The ladder was re-sequenced** and every cross-reference chased across
  six files.
- **Documentation pass.** `SECURITY.md` claimed v0.4.x was the latest
  supported version, four minor versions behind, and now points at PyPI
  rather than naming one. `CONTRIBUTING.md` said 692 tests and never
  mentioned `pip install tango-anki`. ADR-009 records that its phase 3 is
  now scheduled.

## The ladder now

| tag | goal |
|---|---|
| v0.9.0 | nothing fails without saying why (in progress) |
| v0.10.0 | runs on any operating system |
| v0.11.0 | images on cards, gated to concrete nouns |
| v0.12.0 | runs on modest hardware |
| v0.13.0 | freeze candidate |
| v1.0.0 | a finished CLI |

**Card images** were designed in ADR-009 on 17 August and left unbuilt,
which made them invisible to anyone reading only the roadmap. They now have
a rung. The whole problem is the relevance gate: measured on five German
words, Commons returned a dog for `Hund` and a house for `Haus`, then a tram
for `Freiheit`, a disk crash for `schwierig`, and a coin from the town of
Laufen for `laufen`. A wrong image teaches a wrong association, so the rule
is concrete nouns only, with the gate measured on a real deck as an
acceptance target.

## The exact next step

**v0.10.0, runs on any operating system.** The `ANKI_HOST` half shipped in
v0.8.0. What is left:

1. `_translate_wsl_path()` and `_is_wsl()` exist because AnkiConnect
   resolves paths Windows-side. macOS and native Linux need neither; native
   Windows needs different path handling.
2. The Makefile is the documented entry point for 23 targets and GNU make is
   not reasonable on Windows. `make help` already prints the CLI equivalent
   of every user-facing target, so the work is making the CLI primary.
3. **CI on Linux, macOS and Windows.** Decided 5 September 2026: add the
   matrix and let GitHub Actions be the evidence. This machine is Linux
   only, so any claim about the other two would be an assertion. Read the
   real run results, and treat failures on those runners as the actual
   work.

Both v0.8.2 follow-ups are closed: `MANIFEST.in` ships the Dockerfile in the
sdist, and the README says where the Dockerfile comes from.

`requirements.txt` is **not** an open decision, and an earlier version of
this file wrongly said it was. ARCHITECTURE 8.39 settled it: keep both
files, guarded by the `pins` CI job and `scripts/check_pins.py`, which
resolves them and catches a pin that contradicts pyproject's declared range.
Verified passing on 5 September 2026.

## Open decisions

| decision | why it is waiting |
|---|---|
| **Publishing the Docker image** | Builds and runs. Pushing to a registry needs an account and a choice of one |
| **French fixed expressions** | `d'accord` becomes `accord`. 7 of 1079 cards, six legitimate words. Needs a hand-curated per-language list |
| **Transcript fallback** | The whole pipeline depends on one extraction path. A user-supplied subtitle file is the cheap half |
| **Learned queue matching** | Nothing records what the user answers at the y/n/s prompt, so that training data is discarded every run |
| **ruff and mypy debt** | 230 and 24 findings, both advisory, neither gating |

## Known-broken

Nothing. `make check` exits 0.

Environmental notes, not this repository's bugs:

- **`make check` takes about ten minutes here** and has been killed twice
  when run as a background job. The pre-commit hook runs it anyway, so
  committing both gates and lands the change.
- **There is no `pip` script in `.tangovenv/bin`.** Use
  `.tangovenv/bin/python -m pip`.
- **The pre-commit hook matches the literal text "git commit" in a
  command**, so writing a file whose contents mention it is blocked. Use
  the editor tool, not a shell heredoc.
- **`ca_core_news_sm` was installed while verifying the first-run offer.**
  Removable with `pip uninstall ca-core-news-sm`.

## Conventions worth not relearning

Read CLAUDE.md section 18. The short version, and the two that matter most:

- **Run the feature and watch it work before writing tests for it**, and
  exercise the real path, not one that returns early.
- **Mutation-verify every new test.** A surviving mutation means the test is
  wrong. This caught three vacuous tests in one session, including one that
  structurally could not fail: it checked `capsys` for logging output, and
  pytest installs a root handler so that output never reaches `capsys`.

Also: `make check` before every commit, one commit per file, no em dashes,
update CLAUDE.md section 1 before tagging rather than after, and measure
before quoting a number.
