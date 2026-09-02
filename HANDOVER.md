# HANDOVER

Written 3 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main` |
| last tag | **`v0.7.0`**, released 3 September 2026 |
| `__version__` | `0.7.0`, matching the tag and CLAUDE.md section 1 |
| `make check` | **exit 0** |
| tests | **1020 passing, 24 integration deselected** |
| distribution | **`tango-anki`**, command still `tango` |

No background jobs are running.

## What shipped this session

**v0.7.0 released**, "The command line as a product". The rung was already
complete and pushed; this session made the release. Two decisions were taken
first, both yours: tag v0.7.0 rather than fold it into v0.8.0, and name the
distribution `tango-anki`.

The CHANGELOG was missing the progress-and-timing item entirely, though it
shipped in `fcc9352`/`b358e21`. Its link references had also skipped 0.6.0 and
still compared Unreleased from v0.5.3. Both fixed in the release commits.

**Then most of v0.8.0:**

- **The distribution is `tango-anki`**, with the PyPI metadata it had none of:
  authors, keywords, classifiers, URLs, SPDX licence. Build floor moved to
  `setuptools>=77` for the SPDX form. Verified by a real editable install.
- **`tango --version` / `-V`**, reading `pipeline.__version__` rather than
  installed metadata, which is stale under an editable install.
- **WSL reaches Anki with nothing configured** (ARCHITECTURE 8.45).
- **`nltk` moved to a `[wordnet]` extra**; `dev` depends on
  `tango-anki[wordnet]` so the suite still exercises it.
- **Eight user-facing messages named flags v0.7.0 had deleted.** Fixed, with a
  test that scans every module so the next one fails the suite.

## Three documented facts that were wrong

All three had been repeated across several files, and none would have surfaced
without measuring.

- **`ANKI_HOST` never defaulted to a WSL gateway IP.** ROADMAP and CLAUDE.md
  both said it did. `config.py` has always said `http://localhost:8765`; the
  gateway IP was in an uncommitted `.env` and someone read a local override as
  the shipped default. The real problem was the mirror image of that, and is
  now fixed by retrying rather than by changing the default.
- **The default install already had no torch and no translation.** ROADMAP
  asked for what it already had. Measured in a clean venv instead: **334 MB
  across 58 packages**, of which spacy plus its numeric stack is 236 MB, 74%.
  That is the floor while spacy is the NLP engine.
- **A test passed for a week while the code it covered was broken.** It
  asserted the literal string `--doctor` rather than the intent, so when
  v0.7.0 deleted that flag the test and the bug went stale as a matched pair.
  There were two of these, in `test_deck.py` and `test_translation.py`. Both
  now assert the command that exists and that `python -m pipeline` is absent.
  This is the concrete case CLAUDE.md section 5 warns about, so it is worth
  re-reading that section before writing the next assertion.

## The exact next step

Continue v0.8.0:

1. Model and index downloads as a first-run step rather than a README.
2. Dockerfile for the "just run it" case.
3. Uninstall that actually removes the 800 MB of indexes.
4. Publish to PyPI. Needs an account and an API token from you; nothing in the
   repo can do this step alone. `build` and `twine` are not yet in `dev`.

## Open decisions

| decision | why it is waiting |
|---|---|
| **When to publish to PyPI** | Needs your account and token. The name is decided but unclaimed, and an unclaimed name can be taken |
| **French fixed expressions** | `d'accord` becomes `accord`. 7 of 1079 cards, six of them legitimate words. Needs a hand-curated per-language list. In TASKS.md, not started |
| **Transcript fallback** | The whole pipeline depends on one extraction path. Accepting a user-supplied subtitle file is the cheap half; Whisper collides with the install-size goal. No ADR yet |
| **Learned queue matching and proficiency levels** | Parked beyond v1.0.0. One part is cheap and time-sensitive: nothing records what you answer at the y/n/s prompt, so that training data is discarded every run |
| **ruff and mypy debt** | 230 and 24 findings. Both advisory in `check`, neither gating. Pre-existing, not from this session. Worth a rung of its own |

## Known-broken

Nothing. `make check` exits 0 and no test is skipped or xfailed beyond the 24
integration tests excluded by default.

Environmental notes that are not this repository's bugs:

- **`make check` takes about ten minutes here, not the ~90s the last handover
  said.** Several checks were competing on this machine at once, including one
  from another Claude session in a different project and one from the
  pre-commit hook. The suite is fine; `/mnt/c` is slow under contention.
- **There is no `pip` script in `.tangovenv/bin`.** Use
  `.tangovenv/bin/python -m pip`. Related to the `~ip` leftovers in
  site-packages that print `WARNING: Ignoring invalid distribution -ip`.
- **The pre-commit hook matches the literal text "git commit" in a command**,
  so writing a file whose *contents* mention it is blocked. Write such files
  with the editor tool rather than a shell heredoc.

## Conventions worth not relearning

- `make check` must exit 0 before any commit; a hook enforces it on every one.
- One commit per file, subject line only, conventional prefix.
- Write the code, then the tests. Mutation-verify every new test by breaking
  the target and confirming the right test fails. When a mutation is *not*
  caught, fix the claim rather than leaving it standing: that happened once
  this session, with `is_eager`, and both the comment and the test now say
  what is actually pinned.
- Docs ship with the change, not on a second request.
- Measure before quoting a number, and date it.
