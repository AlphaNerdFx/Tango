"""
Shared fixtures.

Currently one, and it exists for a reason worth stating: CLAUDE.md 3.5 says
no test in the default run may require an external service or an installed
model. An optional on-disk index is the same problem wearing different
clothes. A test whose result depends on whether the developer happened to
run `make antonyms` is not a unit test, it is a coin flip that passes on CI
and fails on a well-equipped laptop, or the reverse.

That is not hypothetical. Three tests in `test_definition.py` asserting an
empty antonym field passed for months, then failed the moment the ConceptNet
index (ADR-010) was built on this machine, because they mock `wiktdata` and
had nothing to say about a source that did not exist when they were written.
"""

from __future__ import annotations

import pytest

import pipeline.antonyms as antonyms
import pipeline.config as config
import pipeline.deck as deck
import pipeline.wiktdata as wiktdata


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """
    Keep the developer's `.env` out of every test.

    `config` calls `load_dotenv()` at import, so the whole suite inherits
    whatever happens to be in the local `.env`. CI has none. That means the
    same commit can pass on all six CI jobs and fail on the machine the
    project is developed on, which is the worst kind of test result: one
    that is honestly reporting on something other than what the reader
    thinks it is.

    Found 5 September 2026. Three tests in `TestPromptImport` did exactly
    that. This machine's `ANKI_HOST` points at the Windows gateway, so
    `_prompt_import` correctly refused to send a `/tmp` path to a
    Windows-side Anki, and three tests about import *ordering* failed for a
    reason that had nothing to do with ordering.

    Two halves. The environment variables go, so anything reading `getenv`
    at call time sees a default. And the module-level constants already
    computed from them at import are reset, because deleting the variable
    afterwards cannot reach a value that was read once at startup.

    A test that wants a particular setting patches it itself, which
    overrides this.
    """
    for key in config.KNOWN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    # Constants read at import, so the delenv above cannot reach them.
    monkeypatch.setattr(config, "ANKI_HOST", "http://localhost:8765", raising=False)
    monkeypatch.setattr(config, "ANKI_HOST_EXPLICIT", False, raising=False)
    monkeypatch.setattr(deck, "ANKI_HOST", "http://localhost:8765", raising=False)
    monkeypatch.setattr(deck, "ANKI_HOST_EXPLICIT", False, raising=False)
    monkeypatch.setattr(deck, "_active_host", "http://localhost:8765", raising=False)
    # The WSL fallback latches per run; a test must not inherit the last
    # test's decision about it.
    monkeypatch.setattr(deck, "_wsl_fallback_tried", False, raising=False)


@pytest.fixture(autouse=True)
def isolated_dictionary_indexes(tmp_path, monkeypatch):
    """
    Point the Wiktionary indexes at an empty directory for every test.

    Same reasoning as the antonym fixture below, and added the moment the
    English index stopped being hypothetical. `nlp.process_transcript` now
    consults the index to fold inflected forms, so without this a test's
    result depends on which languages the developer happens to have built.
    A test that wants an index builds one in `tmp_path` and patches this
    attribute itself, which overrides this fixture.
    """
    monkeypatch.setattr(wiktdata, "DICT_DIR", tmp_path / "no-dictionary-index")
    if hasattr(wiktdata._local, "conns"):
        for conn in wiktdata._local.conns.values():
            if conn is not None:
                conn.close()
        del wiktdata._local.conns
    yield
    if hasattr(wiktdata._local, "conns"):
        for conn in wiktdata._local.conns.values():
            if conn is not None:
                conn.close()
        del wiktdata._local.conns


@pytest.fixture(autouse=True)
def isolated_antonym_index(tmp_path, monkeypatch):
    """
    Point the antonym index at an empty directory for every test.

    A test that wants the index builds its own into `tmp_path`, which
    overrides this because it patches the same attribute afterwards. A test
    that has never heard of it sees the field behave as it did before the
    index existed, which is what its assertions were written against.
    """
    monkeypatch.setattr(antonyms, "DICT_DIR", tmp_path / "no-antonym-index")
    antonyms.close()
    yield
    antonyms.close()
