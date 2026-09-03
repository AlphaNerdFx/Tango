# HANDOVER

Written 3 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main`, clean, in sync with `tango-origin` |
| last tag | **`v0.8.1`**, released 3 September 2026 |
| `__version__` | `0.8.1`, matching the tag and CLAUDE.md section 1 |
| `make check` | **exit 0** |
| tests | **1021 passing, 24 integration deselected** |
| PyPI | **published: `pip install tango-anki`** |

No background jobs are running.

## What shipped this session

**Three releases: v0.7.0, v0.8.0 and v0.8.1.** Seventeen tags, seventeen
GitHub releases, all with notes.

**Tango is on PyPI as `tango-anki`.** Verified end to end: a clean
virtualenv running `pip install tango-anki` gets the package and
`tango --version` answers. The distribution is `tango-anki` because `tango`
is an unrelated project; the command is still `tango`.

- https://pypi.org/project/tango-anki/
- https://github.com/AlphaNerdFx/Tango/releases/tag/v0.8.1

In v0.8.0: `tango --version`, a WSL AnkiConnect fallback needing no
configuration (ARCHITECTURE 8.45), `nltk` moved to a `[wordnet]` extra, and
repairs to eight messages naming flags v0.7.0 had deleted.

**v0.8.1 fixed the shop window.** The README is the PyPI description, and
every command in it assumed a cloned repository, so a page published that
morning told pip users to run `make`. PyPI freezes a description at upload
time, so the fix cost a release. It also unstuck a version badge hardcoded
at v0.5.3 for five releases, corrected a roadmap table still showing the
pre-27-August ordering, and untracked a committed `.pyc` that slipped past a
`.gitignore` naming two `__pycache__` directories instead of anchoring the
pattern.

**All markdown except six files moved under `docs/`**, split by purpose.
`CHANGELOG.md` and `SECURITY.md` were moved and then deliberately moved
back: tools expect the changelog at the root, and GitHub only recognises a
security policy at the root, `docs/`, or `.github/`. CLAUDE.md section 9
records why, so a later tidy-up does not move them again.

## Publishing

Both done and verified. `.env` now holds a project-scoped token in `PYPI_API`
and the literal `__token__` in `PYPI_USER`, which is what PyPI requires when
the password is a `pypi-` token. An account name there is rejected with a 403
that does not say why, and that is what tripped the first upload.
`.env.example` documents both keys with a placeholder.

`.env` is gitignored, untracked, and no token has ever been committed. That
was checked across all history.

To release: bump `__version__`, update CHANGELOG, ROADMAP and CLAUDE.md
section 1, `make check`, commit per file, tag, push, then:

```bash
rm -rf dist build src/tango_anki.egg-info
.tangovenv/bin/python -m build
.tangovenv/bin/python -m twine check dist/*
export TWINE_USERNAME="$(grep -oP '(?<=^PYPI_USER=).*' .env)"
export TWINE_PASSWORD="$(grep -oP '(?<=^PYPI_API=).*' .env)"
.tangovenv/bin/python -m twine upload dist/*
```

Check the sdist for secrets before every upload. A PyPI version number is
burned permanently, even if the file is deleted.

## Three documented facts that were wrong

All had been repeated across several files, and none would have surfaced
without measuring.

- **`ANKI_HOST` never defaulted to a WSL gateway IP.** ROADMAP and CLAUDE.md
  both said it did. `config.py` has always said `http://localhost:8765`; the
  gateway IP was in an uncommitted `.env` read as the shipped default.
- **The default install already had no torch and no translation.** ROADMAP
  asked for what it already had. Measured instead: **334 MB across 58
  packages**, of which spacy and its numeric stack is 236 MB, 74%.
- **Two tests passed for a week while the code they covered was broken.**
  Both asserted a flag spelling rather than the intent, so when v0.7.0
  deleted the flags, test and bug went stale together. Fixed, and a scanner
  test now checks every module so the next one fails the suite.

## The exact next step

v0.8.2, an install that looks after itself. Three items cut from v0.8.0 so
the name could be claimed, renumbered when v0.8.1 became a documentation
fix:

1. Model and index downloads as a first-run step rather than a README. A
   fresh `pip install` currently runs `tango run` and is told it has no
   spaCy model: a correct message and a poor welcome.
2. Dockerfile for the "just run it" case.
3. Uninstall that removes the 800 MB of indexes. `pip uninstall` takes the
   package and leaves the data.

## Open decisions

| decision | why it is waiting |
|---|---|
| **French fixed expressions** | `d'accord` becomes `accord`. 7 of 1079 cards, six of them legitimate words. Needs a hand-curated per-language list. In TASKS.md, not started |
| **Transcript fallback** | The whole pipeline depends on one extraction path. Accepting a user-supplied subtitle file is the cheap half; Whisper collides with the install-size goal. No ADR yet |
| **Learned queue matching and proficiency levels** | Parked beyond v1.0.0. One part is cheap and time-sensitive: nothing records what the user answers at the y/n/s prompt, so that training data is discarded every run |
| **ruff and mypy debt** | 230 and 24 findings. Both advisory in `check`, neither gating. Pre-existing. Worth a rung of its own |

## Known-broken

Nothing. `make check` exits 0, nothing skipped or xfailed beyond the 24
integration tests excluded by default.

Environmental notes that are not this repository's bugs:

- **`make check` takes about ten minutes here.** Several checks compete on
  this machine, including other sessions and the pre-commit hook. `/mnt/c`
  is slow under contention.
- **There is no `pip` script in `.tangovenv/bin`.** Use
  `.tangovenv/bin/python -m pip`.
- **The pre-commit hook matches the literal text "git commit" in a command**,
  so writing a file whose *contents* mention it is blocked. Write such files
  with the editor tool, not a shell heredoc.

## Conventions worth not relearning

- `make check` must exit 0 before any commit; a hook enforces it on each one.
- One commit per file, subject line only, conventional prefix, kept short.
- **Run the feature and watch it work before writing tests for it.** Not an
  adjacent path: the WSL fallback was first exercised only through the
  branch that declines it, so the retry was never seen working until it was
  driven against a real Anki. Doing that found a log message that told the
  user to hardcode an address while saying that address goes stale.
- Mutation-verify every new test. When a mutation is not caught, fix the
  claim rather than leaving it standing.
- Docs ship with the change, not on a second request.
- Measure before quoting a number, and date it.
- No em dashes, anywhere: prose, headings, code comments, CLI output.
- Update CLAUDE.md section 1 *before* tagging. `__version__`, the tag and
  that line must agree at the moment of tagging, and v0.8.1 was tagged with
  section 1 still reading v0.8.0.
