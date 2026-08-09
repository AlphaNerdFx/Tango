"""
Single source of truth for all pipeline configuration.

Every constant that affects behaviour, paths, or external service
connections lives here. Modules import from this file rather than
defining their own values.

Environment variables override defaults at runtime — set them in
your .env file (loaded by python-dotenv in __main__.py) or export
them in your shell before running the pipeline.

Presentation constants (ANSI colours, card CSS, HTML templates)
remain in their respective modules — they are not deployment config.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present — does nothing if file doesn't exist
load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
#
# Every path below is anchored to the project root, not to the process's
# working directory. Anchoring to the working directory means running the
# pipeline from anywhere but the repository root silently uses a *different*
# set of files, and every one of those failures is silent:
#
#   DB_PATH      a second, empty database — the definition cache is gone and
#                so is the record of which videos have already been processed
#   DICT_DIR     an unbuilt index directory, so every non-English card loses
#                its definition and the run still reports success
#   REVIEW_FILE  words deferred to one review.json, `--process-review`
#                reading another
#   OUTPUT_DIR   the .apkg written somewhere other than where the docs and
#                the Makefile say to look for it
#
# Valid-looking wrong output with nothing raised is this codebase's
# characteristic failure mode; see SESSION.md 6.12.


def _project_root(root: Path | None = None) -> Path:
    """
    Absolute path to the repository root, resolved from this file's location.

    ``config.py`` lives at ``<root>/src/pipeline/config.py``, so the root is
    three levels up. That holds for the documented install, ``pip install -e .``,
    which leaves the package in the source tree.

    A non-editable install puts the package under ``site-packages``, where
    three levels up is not a project root and is the wrong place to write a
    database. ``pyproject.toml`` is the marker that tells the two apart; when
    it is missing, this falls back to the working directory, which is the
    behaviour every path had before anchoring.

    Args:
        root: Override for the computed root. Exists so the marker-missing
            branch is testable without a second install layout on disk.

    Returns:
        The project root if it looks like one, otherwise the current
        working directory.
    """
    candidate = root if root is not None else Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else Path.cwd()


PROJECT_ROOT: Path = _project_root()


def _resolve_path(var: str, default: str, root: Path | None = None) -> Path:
    """
    Resolve a configurable path against the project root.

    Anchoring the *default* alone would not have fixed anything: the shipped
    ``.env.example`` sets ``DB_PATH=pipeline.db``, ``OUTPUT_DIR=output``, and
    ``REVIEW_FILE=review.json``, so anyone who followed the documented setup
    has a relative override in their ``.env`` and never reaches the default.
    A relative value is therefore anchored too, whether it came from the
    environment or from the default here.

    An absolute path is honoured exactly as given, and ``~`` is expanded
    first so ``~/tango.db`` counts as absolute rather than becoming a
    directory named ``~`` inside the project.

    Args:
        var: Environment variable name to read.
        default: Value to use when the variable is unset or empty.
        root: Directory to anchor relative paths to. Defaults to PROJECT_ROOT.

    Returns:
        An absolute path, unless the project root itself is relative
        (possible only in the working-directory fallback above).
    """
    value = os.getenv(var) or default
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (root if root is not None else PROJECT_ROOT) / path


# SQLite database — shared across state.py, definition.py, deck.py
DB_PATH: Path = _resolve_path("DB_PATH", "pipeline.db")

# Output directory for generated .apkg files
OUTPUT_DIR: Path = _resolve_path("OUTPUT_DIR", "output")

# Review file — deferred queue words written here for manual resolution
REVIEW_FILE: Path = _resolve_path("REVIEW_FILE", "review.json")

# Directory for per-language Wiktionary indexes built by wiktdata.py.
# One SQLite file per language, each built once from a bulk download and
# then read offline. Kept out of DB_PATH's database deliberately: these are
# large (hundreds of MB), rebuildable from scratch, and must never be
# confused with the definition cache, which is expensive to rebuild.
DICT_DIR: Path = _resolve_path("DICT_DIR", "dictionaries")

# Anki

# AnkiConnect host — change if running Anki on a non-default port
ANKI_HOST: str = os.getenv("ANKI_HOST", "http://localhost:8765")

# AnkiConnect API version — do not change unless AnkiConnect upgrades its API
ANKI_VERSION: int = 6

# Seconds to wait for AnkiConnect to respond before timing out. Fine for the
# quick calls -- deckNames, findNotes, notesInfo -- which answer immediately.
ANKI_TIMEOUT: int = int(os.getenv("ANKI_TIMEOUT", "5"))

# importPackage is not one of those. Anki writes every note, builds cards and
# rebuilds indexes before it answers, and how long that takes scales with the
# collection, not just the package: a 382-card import into a 57k-note
# collection blew well past 5s, the request timed out on our side, and the
# user saw an empty deck with no error -- while the same import had worked
# weeks earlier on a smaller collection. Generous by design; it is a ceiling
# for a hung server, not a target.
ANKI_IMPORT_TIMEOUT: int = int(os.getenv("ANKI_IMPORT_TIMEOUT", "300"))

# genanki model ID — NEVER change after first use.
# Changing this causes Anki to treat all existing cards as belonging
# to a new model, breaking review history.
MODEL_ID: int = int(os.getenv("ANKI_MODEL_ID", "1607392319"))

# genanki deck ID — NEVER change after first use.
# Same constraint as MODEL_ID.
DECK_ID: int = int(os.getenv("ANKI_DECK_ID", "2059400110"))

# Deck check — confidence interval thresholds

# Fuzzy match score above this → word already in deck (SKIP)
CONFIDENCE_HIGH: int = int(os.getenv("CONFIDENCE_HIGH", "90"))

# Fuzzy match score below this → brand new word (NEW)
# Between CONFIDENCE_LOW and CONFIDENCE_HIGH → needs user review (QUEUE)
CONFIDENCE_LOW: int = int(os.getenv("CONFIDENCE_LOW", "60"))

# Words shorter than this use exact match only — WRatio is unreliable
# on short tokens due to partial ratio inflation
SHORT_WORD_THRESHOLD: int = int(os.getenv("SHORT_WORD_THRESHOLD", "4"))

# Definition APIs

# Merriam-Webster Collegiate API — primary definition source
# Key required: https://dictionaryapi.com/register/index.htm (free tier: 1000/day)
MW_API_KEY: str | None = os.getenv("MW_API_KEY")
MW_API_BASE: str = "https://www.dictionaryapi.com/api/v3/references/collegiate/json"

# dictionaryapi.dev — fallback, no key required
DICT_API_BASE: str = "https://api.dictionaryapi.dev/api/v2/entries"

# English Wiktionary's REST definition endpoint — supplements native-language
# example sentences for non-English lemmas when dictionaryapi.dev has none
# (see issue #1). Only the English-language edition's REST endpoint works
# reliably; other language editions returned 501 in testing. Query it with
# the foreign word anyway: an English Wiktionary page carries every language
# a word appears in as its own section, keyed by language code, so a French
# word's page still has an "fr" section with French example sentences even
# though the site itself is the English edition. No key required, but
# Wikimedia does enforce anonymous rate limits (a 429 during a burst of
# ~12 requests was observed in testing) and requests an identifying
# User-Agent — see https://meta.wikimedia.org/wiki/User-Agent_policy.
WIKTIONARY_API_BASE: str = "https://en.wiktionary.org/api/rest_v1/page/definition"
WIKTIONARY_USER_AGENT: str = os.getenv(
    "WIKTIONARY_USER_AGENT",
    "Tango-pipeline/0.4 (https://github.com/AlphaNerdFx/Tango)",
)

# Seconds to wait for a definition API response before timing out
API_TIMEOUT: float = float(os.getenv("API_TIMEOUT", "8"))

# Maximum number of definition lookups in flight at once. Replaces the old
# fixed API_DELAY sleep-between-calls approach — rate limiting is now done
# by bounding concurrency rather than pacing a sequential loop. See
# ARCHITECTURE.md's design-patterns section for why a thread pool was used
# instead of asyncio/aiohttp.
DEFINITION_FETCH_WORKERS: int = int(os.getenv("DEFINITION_FETCH_WORKERS", "5"))

# Consecutive server-error/timeout failures against one definition source
# before the circuit breaker stops calling it for the rest of the run.
# Does NOT count 404 ("word not found" — the source is healthy, just lacks
# this word) as a failure, only 5xx/timeout/connection errors — see issue #1's
# 404-vs-502 investigation for why that distinction matters.
CIRCUIT_BREAKER_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))

# Proxy (youtube-transcript-api)
#
# Most users never need one — default traffic already comes from a
# residential IP, which is what YouTube blocks least. If you are genuinely
# rate-limited, bring your own reputable paid or VPN proxy; this project
# does not recommend a provider. A free-tier datacenter proxy made things
# measurably worse when tested (repeated 429s through it, success without
# it) — see issue #8 and SESSION.md 6.5.
# Format: "http://user:pass@host:port" or "socks5://user:pass@host:port"
PROXY_HTTP_URL: str | None = os.getenv("PROXY_HTTP_URL")
PROXY_HTTPS_URL: str | None = os.getenv("PROXY_HTTPS_URL")

# Webshare-specific credentials, for anyone already using that service.
# Listed second deliberately: it is not the suggested starting point.
WEBSHARE_USERNAME: str | None = os.getenv("WEBSHARE_USERNAME")
WEBSHARE_PASSWORD: str | None = os.getenv("WEBSHARE_PASSWORD")