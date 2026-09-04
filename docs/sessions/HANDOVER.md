# HANDOVER

Written 4 September 2026.

## Where the tree is

| | |
|---|---|
| branch | `main`, clean, in sync with `tango-origin` |
| last tag | **`v0.8.2`**, released 4 September 2026 |
| `__version__` | `0.8.2`, matching the tag and CLAUDE.md section 1 |
| `make check` | **exit 0** |
| tests | **1040 passing, 24 integration deselected** |
| PyPI | `pip install tango-anki` |

No background jobs are running.

## What shipped

**Four releases: v0.7.0, v0.8.0, v0.8.1 and v0.8.2.** Eighteen tags,
eighteen GitHub releases, all with notes.

- v0.7.0, the command line as a product
- v0.8.0, packaged and installable, and the first release on PyPI
- v0.8.1, documentation, because the README is the PyPI page and every
  command in it assumed a cloned repository
- v0.8.2, an install that looks after itself

**v0.8.2 in detail.** A missing spaCy model is offered before the run rather
than reported after the transcript is fetched. `tango uninstall` reports and
removes what `pip uninstall` leaves, measured at 1.2 GB here. A Dockerfile,
built and run before release. And something other than AnkiConnect answering
on port 8765 is now a typed error rather than a raw `JSONDecodeError`.

**Every markdown file except six moved under `docs/`**, and 490 em dashes
were replaced with ordinary punctuation across 41 files.

## Two follow-ups from the v0.8.2 release

Neither is urgent, both are small, and both were found after the tag, so
fixing them would have made the artifact disagree with it.

1. **The Dockerfile is not in the sdist.** setuptools ships only what it
   knows about. A `MANIFEST.in` with `include Dockerfile` fixes it. It
   matters because the Dockerfile stands alone: it pip-installs from PyPI
   and needs no other file from the repository.
2. **The README's Docker block does not say where the Dockerfile comes
   from.** It opens with `docker build -t tango .`, which assumes you have
   the repository, and on the PyPI page that assumption is invisible. One
   line pointing at the repo, or the `MANIFEST.in` above, closes it.

## Things that were wrong, and how they were found

Worth reading, because the pattern repeats: none came from a test failure.

- **`ANKI_HOST` never defaulted to a WSL gateway IP.** ROADMAP and CLAUDE.md
  both said it did. An uncommitted `.env` override had been read as the
  shipped default.
- **The default install already had no torch.** ROADMAP asked for what it
  already had. Measured instead: 334 MB across 58 packages, 74% of it spacy.
- **Three tests passed while what they covered was broken or unpinned.** Two
  asserted a deleted flag's spelling rather than the intent. A third claimed
  to pin the uninstall dry-run guard but was satisfied by a different guard,
  which only showed up under mutation.
- **A wrong service on port 8765 produced a traceback.** Found by the user
  reading the code.

## The exact next step

v0.9.0, runs on any operating system. The `ANKI_HOST` half is already done.
What is left:

1. `_translate_wsl_path()` and `_is_wsl()` exist because AnkiConnect
   resolves paths Windows-side. macOS and native Linux need neither; native
   Windows needs different path handling.
2. The Makefile is the documented entry point for 23 targets, and GNU make
   is not reasonable on Windows. `make help` already prints the CLI
   equivalent of every user-facing target, so the work is making the CLI
   primary and the Makefile a convenience.
3. CI on Linux, macOS and Windows, because "should work" is not evidence.

Do the two follow-ups above in the same release.

## Open decisions

| decision | why it is waiting |
|---|---|
| **Publishing the Docker image** | The Dockerfile is in the repo and builds. Pushing it to a registry needs an account and a decision about which one |
| **French fixed expressions** | `d'accord` becomes `accord`. 7 of 1079 cards, six of them legitimate words. Needs a hand-curated per-language list |
| **Transcript fallback** | The whole pipeline depends on one extraction path. A user-supplied subtitle file is the cheap half; Whisper collides with the install-size goal |
| **Learned queue matching** | Parked beyond v1.0.0. One part is cheap and time-sensitive: nothing records what you answer at the y/n/s prompt, so that training data is discarded every run |
| **ruff and mypy debt** | 230 and 24 findings, both advisory, neither gating. Pre-existing. Worth a rung of its own |

## Publishing

`.env` holds `PYPI_USER=__token__` and a project-scoped token in `PYPI_API`.
The username must be the literal `__token__`; an account name is rejected
with a 403 that does not say why.

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

## Known-broken

Nothing. `make check` exits 0, nothing skipped or xfailed beyond the 24
integration tests excluded by default.

Environmental notes that are not this repository's bugs:

- **`make check` takes about ten minutes here.** Several checks compete on
  this machine. `/mnt/c` is slow under contention.
- **There is no `pip` script in `.tangovenv/bin`.** Use
  `.tangovenv/bin/python -m pip`.
- **The pre-commit hook matches the literal text "git commit" in a
  command**, so writing a file whose *contents* mention it is blocked. Use
  the editor tool, not a shell heredoc.
- **`ca_core_news_sm` was installed while verifying the first-run offer.**
  Harmless, and removable with `pip uninstall ca-core-news-sm`.

## Conventions worth not relearning

- `make check` must exit 0 before any commit; a hook enforces it on each one.
- One commit per file, subject line only, conventional prefix, kept short.
  The one deliberate exception was the 41-file em dash sweep, a single
  mechanical concern.
- **Run the feature and watch it work before writing tests for it**, and
  exercise the actual path rather than an adjacent one.
- Mutation-verify every new test. When a mutation is not caught the test is
  wrong: fix it rather than leaving the claim standing. That happened twice
  in one session.
- **No em dashes** anywhere: prose, headings, code comments, CLI output. The
  Anki notetype name and the card attribution line are the exceptions, and
  they are data rather than writing.
- Update CLAUDE.md section 1 *before* tagging, not after.
- Docs ship with the change, not on a second request.
- Measure before quoting a number, and date it.
