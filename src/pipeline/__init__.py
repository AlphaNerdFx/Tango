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

__version__ = "0.8.1"
