"""
Tango, YouTube transcripts to Anki flashcard packages.

The version lives here and nowhere else. `pyproject.toml` reads it from this
attribute (`[tool.setuptools.dynamic]`), so the package metadata and the
running code cannot disagree.

Two copies is one too many, and this project has now been bitten by it three
ways. `pyproject.toml` carried a hand-written version that never once matched
its tag -- 0.1.0 at both v0.4.3 and v0.4.4, 0.4.4 at v0.4.5. The User-Agent
sent to Wikimedia carried a third copy, still reading 0.4 at v0.5.2. And
reading the version back from installed metadata is stale in yet another way:
an editable install records whatever the version was when it was installed,
which reported 0.1.0 long after the file said otherwise.
"""

__version__ = "0.8.2"


class TangoError(Exception):
    """
    Base class for every failure this project raises deliberately.

    Added in v0.9.0 so the entry point can tell an expected failure from a
    bug. Before it, `main()` could only catch `Exception`, so a corrupt
    database or a missing model reached the user under "This is a bug in
    Tango, please report it", which is both wrong and a waste of their time.

    Every module keeps its own specific exception types and its own
    messages. This only adds a shared ancestor, so `except AnkiConnectError`
    and every other existing handler behave exactly as before.

    The rule for raising one: if a message can tell the user what to do
    about it, it is a TangoError. If the only honest message is "this
    should not have happened", it is not, and the top-level handler should
    treat it as the bug it is.
    """
