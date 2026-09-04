# Contributing to Tango

Thank you for your interest in contributing. This document covers how to get set up, what to work on, and how to submit changes.

## Getting started

Fork the repository and clone your fork locally.

```bash
git clone https://github.com/your-username/tango.git
cd tango
make all
```

This creates the virtual environment, installs all dependencies, and downloads the spaCy model. Run the unit test suite to confirm everything works before making changes.

```bash
make test
```

All tests should pass. The count moves as tests are added, so trust
`make test` over any number written down here. If any fail on your machine
before you change anything, open an issue before proceeding.

Users install the published package instead, which needs none of the above:

```bash
pip install tango-anki
tango doctor
```

`tango doctor` reports what is installed, what is missing, and the command
that fixes each. It is the fastest way to tell a broken environment from a
broken change.

## Project structure

```
src/pipeline/     the importable package, and the only thing in src/
tests/            mirrors the module structure
scripts/          one-off measurement scripts, not part of the package
docs/adr/         architecture decisions
docs/architecture/  ARCHITECTURE.md, the full system detail
docs/planning/    ROADMAP.md and TASKS.md
docs/sessions/    working state between sessions
docs/assets/      diagrams and icons
```

`src/` holds the importable package and nothing else. That is the
src-layout convention, and it is enforced by a test: assets and scripts
belong outside it. Each module has a single responsibility, described in
the code walkthrough PDF in `docs/`.

## Before you open a pull request

`make check` must exit 0. A hook enforces it on every commit.

Read `CLAUDE.md` section 18 first. It records the practices that have
actually caught bugs in this project, and two of them will save you a
review round:

- **Run the feature and watch it work before writing tests for it**, and
  exercise the real path rather than one that returns early.
- **Mutation-verify every new test.** Break the code deliberately and
  confirm your test fails. If it still passes, the test is wrong. This has
  caught vacuous tests three times in a single session.

Section 3 lists six hard constraints that must never be violated. All six
have tests that fail on the specific mistake each one describes.

## What to work on

Open issues are the best place to start. Issues labelled `good first issue` are self-contained and well-scoped. Issues labelled `help wanted` are higher priority but may require more context.

If you want to work on something not covered by an existing issue, open one first and describe what you plan to do. This avoids duplicated effort and lets the maintainer flag any concerns before you write code.

## Making changes

Create a branch for your work.

```bash
git checkout -b feat/your-feature-name
```

Branch names should follow the pattern `feat/`, `fix/`, `docs/`, or `chore/` followed by a short description.

Write tests for any new behaviour. The test suite uses pytest. All tests must pass without network access, a running Anki instance, or installed translation models. If your change requires network or Anki, use the `@pytest.mark.integration` marker and ensure the default test run excludes it.

Run the formatter and linter before committing.

```bash
make format
make lint
make coverage    # per-module line coverage
```

### Verifying a release

`make test` cannot check that a real run produces the cards it claims to, it
has no Anki, no network, and no spaCy model by design. For that there is a
release script:

```bash
bash scripts/verify-release.sh <VIDEO_ID> <LANGUAGE>
```

It runs a video into a fresh deck, imports it, re-runs the same video to prove
duplicate detection works, and reads the resulting cards back out of Anki to
report per-field coverage. It needs a running Anki with AnkiConnect, network,
and `jq`, and **it writes to your real collection**. Use a video you have not
processed before: Anki dedups notes by GUID, so re-importing one you already
have updates those notes in place rather than filling the new deck.

## Commit messages

Follow the conventional commits format used throughout the project.

```
feat: add WordNet antonym supplementation
fix: remove FallbackNote field from card model
docs: update README with v0.4.0 card fields
chore: add nltk to pyproject.toml dependencies
```

Each commit should do one thing. Avoid commits that mix unrelated changes.

## Pull requests

Open a pull request against the `main` branch. The title should follow the same conventional commits format as your commit messages. In the description, explain what the change does and why. Reference any related issues.

The CI pipeline must pass before a pull request can be merged. It runs the unit test suite on Python 3.10, 3.11, and 3.12. Python 3.9 isn't supported: spaCy 3.8 (a required dependency) has no compatible wheel for it.

## What not to change

Do not change `ANKI_MODEL_ID` or `ANKI_DECK_ID` in `config.py` or `cards.py` without a clear migration plan. These values are baked into existing Anki decks and changing them without a migration breaks review history for all existing users.

Do not add new runtime dependencies without discussion. The goal is to keep the base install small. New heavy dependencies should go in optional groups in `pyproject.toml`.