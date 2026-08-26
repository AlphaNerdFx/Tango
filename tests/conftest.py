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
