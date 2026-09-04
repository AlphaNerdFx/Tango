"""
Single source of truth for all pipeline configuration.

Every constant that affects behaviour, paths, or external service
connections lives here. Modules import from this file rather than
defining their own values.

Environment variables override defaults at runtime, set them in
your .env file (loaded by python-dotenv in __main__.py) or export
them in your shell before running the pipeline.

Presentation constants (ANSI colours, card CSS, HTML templates)
remain in their respective modules, they are not deployment config.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from pipeline import __version__

# Load .env file if present: does nothing if file doesn't exist
load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
#
# Every path below is anchored to the project root, not to the process's
# working directory. Anchoring to the working directory means running the
# pipeline from anywhere but the repository root silently uses a *different*
# set of files, and every one of those failures is silent:
#
#   DB_PATH      a second, empty database: the definition cache is gone and
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


# SQLite database: shared across state.py, definition.py, deck.py
DB_PATH: Path = _resolve_path("DB_PATH", "pipeline.db")

# Output directory for generated .apkg files
OUTPUT_DIR: Path = _resolve_path("OUTPUT_DIR", "output")

# Review file: deferred queue words written here for manual resolution
REVIEW_FILE: Path = _resolve_path("REVIEW_FILE", "review.json")

# Directory for per-language Wiktionary indexes built by wiktdata.py.
# One SQLite file per language, each built once from a bulk download and
# then read offline. Kept out of DB_PATH's database deliberately: these are
# large (hundreds of MB), rebuildable from scratch, and must never be
# confused with the definition cache, which is expensive to rebuild.
DICT_DIR: Path = _resolve_path("DICT_DIR", "dictionaries")

# Cache for pronunciation audio downloaded at package-build time, so a card
# plays the recording instead of linking out to it. Measured on real
# Wikimedia Commons files: 16-30 KB each, roughly 5 MB for a 240-card German
# deck. Kept as a cache rather than written straight into the package so a
# word appearing in a second video is not downloaded twice, and a failed
# download costs one card its audio rather than the run.
MEDIA_DIR: Path = _resolve_path("MEDIA_DIR", "media")

# Seconds to wait for one audio file. Short on purpose: these are small
# files, and the sources do go down -- dictionaryapi.dev's media host was
# returning 502 for some words while serving others -- so a slow response is
# more likely a failure than a big file.
MEDIA_TIMEOUT: int = int(os.getenv("MEDIA_TIMEOUT", "10"))

# Pacing for audio downloads, in requests per second across all threads.
#
# Measured against upload.wikimedia.org on 17 August 2026, not guessed: a
# burst of roughly ten requests succeeds and every one after that returns 429
# with `Retry-After: 11`. Going sequential did not help (10 of 40 succeeded)
# and neither did asking for the original .ogg instead of the transcoded .mp3
# (10 of 30), so this is a per-IP token bucket on the host rather than a
# concurrency cap or anything specific to on-demand transcoding. Spacing
# requests 0.7s apart sustained 18 consecutive downloads with no 429 at all.
#
# The cost of getting this wrong is silent and total: an 8-worker pool
# drained the bucket in under a second, so a real 406-card German run
# embedded 13 recordings and gave the other 364 cards a link instead. Every
# one of those URLs downloads fine on its own, which is exactly why nothing
# looked broken -- see ARCHITECTURE.md 8.35.
MEDIA_RATE_LIMIT: float = float(os.getenv("MEDIA_RATE_LIMIT", "1.25"))

# How many requests may go out back-to-back before pacing applies. Below the
# measured ceiling of ~10 on purpose, so a short run of a handful of new
# words costs nothing while a long one settles to MEDIA_RATE_LIMIT.
MEDIA_BURST: int = int(os.getenv("MEDIA_BURST", "8"))

# Retries for a 429 specifically. The bucket refills, so a rate-limited
# request is worth repeating -- unlike a 404 or a 502, where the file is
# simply not there and the card falls back to a link immediately.
MEDIA_MAX_RETRIES: int = int(os.getenv("MEDIA_MAX_RETRIES", "2"))

# Ceiling on one `Retry-After` wait, so a server answering with an hour
# cannot stall a run that has already done the expensive work.
MEDIA_MAX_RETRY_WAIT: float = float(os.getenv("MEDIA_MAX_RETRY_WAIT", "30"))

# Anki


def is_wsl() -> bool:
    """
    Whether this process is running inside the Windows Subsystem for Linux.

    Two separate things depend on it. A .apkg path handed to a Windows-side
    Anki has to be translated to drive-letter form
    (`__main__._translate_wsl_path`), and `localhost` does not reach that
    Anki from inside WSL2's default NAT network (`deck._anki_request`).

    Returns:
        True under WSL, False anywhere else, including when /proc/version
        cannot be read at all.
    """
    try:
        with open("/proc/version") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def wsl_host_ip() -> str | None:
    """
    The address that reaches the Windows side from inside WSL2, or None.

    Under WSL2's default NAT networking the Windows host sits at the other
    end of the default route, and that address is reassigned on reboot,
    which is why writing it into .env works until it silently does not.

    Read from /proc/net/route rather than by parsing `ip route`, so it is a
    file read with no subprocess and works where iproute2 is not installed.
    The gateway column is a little-endian hex word: 01901CAC is 172.28.144.1,
    least significant byte first.

    Deliberately NOT /etc/resolv.conf's nameserver, which is the usual
    recipe and is wrong. Measured on this machine, resolv.conf gives
    10.255.255.254 while the Windows host is 172.28.144.1.

    Returns:
        The gateway as a dotted quad, or None when there is no usable
        default route -- which includes WSL2 mirrored networking, where
        localhost already reaches Windows and no separate address exists.
        The caller reads None as "nothing to fall back to".
    """
    try:
        with open("/proc/net/route") as fh:
            rows = fh.read().splitlines()[1:]
    except OSError:
        return None
    for row in rows:
        fields = row.split()
        # Destination 00000000 is the default route.
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            packed = int(fields[2], 16)
        except ValueError:
            continue
        if packed == 0:
            continue
        return ".".join(str((packed >> shift) & 0xFF) for shift in (0, 8, 16, 24))
    return None


# AnkiConnect host. Change it if Anki runs on another machine or port.
#
# localhost is correct on macOS, native Linux, native Windows and on WSL2
# with mirrored networking. It is wrong on WSL2's default NAT networking,
# where Anki runs on the Windows side; deck.py retries there against
# wsl_host_ip() rather than defaulting to it, because guessing the gateway
# would break the mirrored-networking case that works today.
ANKI_HOST: str = os.getenv("ANKI_HOST", "http://localhost:8765")

# Whether the user named the host themselves. An explicit choice is never
# second-guessed, including an explicit localhost.
ANKI_HOST_EXPLICIT: bool = bool(os.getenv("ANKI_HOST"))

# AnkiConnect API version: do not change unless AnkiConnect upgrades its API
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

# genanki model ID: NEVER change after first use.
# Changing this causes Anki to treat all existing cards as belonging
# to a new model, breaking review history.
#
# Changed ONCE, on 14 August 2026, because the previous value never held
# any of this pipeline's cards. 1607392319 is the model ID in genanki's
# README example, copied along with the tutorial and paired there with a
# notetype named "Simple Model". Any collection that ever imported a deck
# built from that tutorial already has the ID taken, and Anki then refuses
# to reuse it: it forks a new notetype with a bumped ID and a suffixed
# name. Measured on a real collection: 1607392319 held a 7-field "Simple
# Model" with zero notes, while this pipeline's 2135 cards sat on
# 1607392321, the ID Anki assigned when it forked.
#
# The new value is that fork's ID, so packages now match the notetype the
# cards are actually on and imports merge instead of forking again. See
# ARCHITECTURE.md 8.31. Do not change it again.
MODEL_ID: int = int(os.getenv("ANKI_MODEL_ID", "1607392321"))

# genanki deck ID: NEVER change after first use.
# Same constraint as MODEL_ID.
DECK_ID: int = int(os.getenv("ANKI_DECK_ID", "2059400110"))

# Deck check: confidence interval thresholds

# Fuzzy match score above this → word already in deck (SKIP)
CONFIDENCE_HIGH: int = int(os.getenv("CONFIDENCE_HIGH", "90"))

# Fuzzy match score below this → brand new word (NEW)
# Between CONFIDENCE_LOW and CONFIDENCE_HIGH → needs user review (QUEUE)
CONFIDENCE_LOW: int = int(os.getenv("CONFIDENCE_LOW", "60"))

# Words shorter than this use exact match only: WRatio is unreliable
# on short tokens due to partial ratio inflation
SHORT_WORD_THRESHOLD: int = int(os.getenv("SHORT_WORD_THRESHOLD", "4"))

# Definition APIs

# Merriam-Webster Collegiate API: primary definition source
# Key required: https://dictionaryapi.com/register/index.htm (free tier: 1000/day)
# Merriam-Webster's terms, quoted from dictionaryapi.com because they shape
# the architecture rather than merely the setup:
#
#   "free as long as it is for non-commercial use, usage does not exceed
#    1000 queries per day per API key, and use is limited to two reference
#    APIs"
#
# and the key is "specific to your application". Three consequences:
#
#   1. 1000/day is a per-user ceiling that pacing cannot lift. The English
#      run that prompted MW_RATE_LIMIT below needed 1094 lookups for one
#      video, so it could not have completed on a free key however politely
#      it asked.
#   2. The key must be the user's own. Shipping one would put every user
#      through a single 1000-a-day allowance and, being application-scoped,
#      would be the wrong licence besides. `make setup` asks for it.
#   3. Commercial use is a negotiation, not a tier. Anything monetized needs
#      MW's written terms, which is why MW must stay an enhancement rather
#      than the thing English depends on. See ADR-011.
MW_API_KEY: str | None = os.getenv("MW_API_KEY")
MW_API_BASE: str = "https://www.dictionaryapi.com/api/v3/references/collegiate/json"

# Pacing for Merriam-Webster, in requests per second across all threads.
#
# Unlike MEDIA_RATE_LIMIT above, this number is NOT measured, and saying so
# matters more than the number. What is measured: a real 1094-word English
# run on 27 August 2026 pushed roughly 18 requests a second through five
# workers, MW answered 167 of them and then nothing, and the circuit breaker
# skipped it for the rest of the run. 85% of that deck shipped with "No
# definition found". Separately, ten concurrent requests succeed and six
# sequential ones succeed, so the key is neither blocked nor out of quota.
#
# 4/s is a deliberate underestimate, roughly a quarter of the rate that
# failed. It costs about four and a half minutes on a 1094-word run, which is
# the right trade against 85% of the deck being empty. Replace it with a real
# ceiling when someone measures where MW actually starts refusing: the
# measurement costs a chunk of the 1000-per-day free tier, which is why it
# has not been done yet.
MW_RATE_LIMIT: float = float(os.getenv("MW_RATE_LIMIT", "4.0"))

# Requests allowed back-to-back before pacing applies, so a short review run
# of a few words pays nothing.
MW_BURST: int = int(os.getenv("MW_BURST", "8"))

# dictionaryapi.dev: fallback, no key required
DICT_API_BASE: str = "https://api.dictionaryapi.dev/api/v2/entries"

# English Wiktionary's REST definition endpoint: supplements native-language
# example sentences for non-English lemmas when dictionaryapi.dev has none
# (see issue #1). Only the English-language edition's REST endpoint works
# reliably; other language editions returned 501 in testing. Query it with
# the foreign word anyway: an English Wiktionary page carries every language
# a word appears in as its own section, keyed by language code, so a French
# word's page still has an "fr" section with French example sentences even
# though the site itself is the English edition. No key required, but
# Wikimedia does enforce anonymous rate limits (a 429 during a burst of
# ~12 requests was observed in testing) and requests an identifying
# User-Agent: see https://meta.wikimedia.org/wiki/User-Agent_policy.
WIKTIONARY_API_BASE: str = "https://en.wiktionary.org/api/rest_v1/page/definition"
WIKTIONARY_USER_AGENT: str = os.getenv(
    "WIKTIONARY_USER_AGENT",
    f"Tango-pipeline/{__version__} (https://github.com/AlphaNerdFx/Tango)",
)

# Seconds to wait for a definition API response before timing out
API_TIMEOUT: float = float(os.getenv("API_TIMEOUT", "8"))

# Maximum number of definition lookups in flight at once. Replaces the old
# fixed API_DELAY sleep-between-calls approach: rate limiting is now done
# by bounding concurrency rather than pacing a sequential loop. See
# ARCHITECTURE.md's design-patterns section for why a thread pool was used
# instead of asyncio/aiohttp.
DEFINITION_FETCH_WORKERS: int = int(os.getenv("DEFINITION_FETCH_WORKERS", "5"))

# Consecutive server-error/timeout failures against one definition source
# before the circuit breaker stops calling it for the rest of the run.
# Does NOT count 404 ("word not found": the source is healthy, just lacks
# this word) as a failure, only 5xx/timeout/connection errors: see issue #1's
# 404-vs-502 investigation for why that distinction matters.
CIRCUIT_BREAKER_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))

# Proxy (youtube-transcript-api)
#
# Most users never need one: default traffic already comes from a
# residential IP, which is what YouTube blocks least. If you are genuinely
# rate-limited, bring your own reputable paid or VPN proxy; this project
# does not recommend a provider. A free-tier datacenter proxy made things
# measurably worse when tested (repeated 429s through it, success without
# it): see issue #8 and SESSION.md 6.5.
# Format: "http://user:pass@host:port" or "socks5://user:pass@host:port"
PROXY_HTTP_URL: str | None = os.getenv("PROXY_HTTP_URL")
PROXY_HTTPS_URL: str | None = os.getenv("PROXY_HTTPS_URL")

# Webshare-specific credentials, for anyone already using that service.
# Listed second deliberately: it is not the suggested starting point.
WEBSHARE_USERNAME: str | None = os.getenv("WEBSHARE_USERNAME")
WEBSHARE_PASSWORD: str | None = os.getenv("WEBSHARE_PASSWORD")

# ── Known environment variables ───────────────────────────────────────────────
#
# v0.9.0. A setting that does nothing is a failure with no message, which is
# this rung's whole subject. Two were found in a real .env on 5 September
# 2026: `SPACY_MODEL=en_core_web_sm`, which looks exactly like it should
# choose the model and is read by nothing (the model comes from the language
# code, via language.get_spacy_model), and `API_DELAY`, a leftover. Both had
# been sitting there being ignored.
#
# Declared rather than derived, because deriving it means scanning source at
# runtime. The list cannot drift silently: a test walks every `os.getenv`
# call in the package and fails if one is missing from here, or if a name
# here is read by nothing.

KNOWN_ENV_KEYS: frozenset[str] = frozenset({
    # Anki
    "ANKI_HOST", "ANKI_TIMEOUT", "ANKI_IMPORT_TIMEOUT",
    "ANKI_MODEL_ID", "ANKI_DECK_ID",
    # Definitions
    "MW_API_KEY", "MW_RATE_LIMIT", "MW_BURST", "API_TIMEOUT",
    "CIRCUIT_BREAKER_THRESHOLD", "DEFINITION_FETCH_WORKERS",
    # Matching
    "CONFIDENCE_HIGH", "CONFIDENCE_LOW", "SHORT_WORD_THRESHOLD",
    # Media
    "MEDIA_RATE_LIMIT", "MEDIA_BURST", "MEDIA_MAX_RETRIES",
    "MEDIA_MAX_RETRY_WAIT", "MEDIA_TIMEOUT",
    # Paths
    "DB_PATH", "DICT_DIR", "MEDIA_DIR", "OUTPUT_DIR", "REVIEW_FILE",
    # Network
    "PROXY_HTTP_URL", "PROXY_HTTPS_URL",
    "WEBSHARE_USERNAME", "WEBSHARE_PASSWORD",
    # Language and translation
    "SPACY_MODEL_SIZE_OVERRIDE", "LIBRETRANSLATE_URL", "ARGOS_PACKAGES_DIR",
    "LIBRETRANSLATE_MIRRORS",
    #Network politeness: the User-Agent Wikimedia asks callers to send
    "WIKTIONARY_USER_AGENT",
    # Debugging
    "TANGO_DEBUG",
    # Publishing. Read by twine through the shell, never by this package,
    # so they are known-but-unread and exempt from the "is it read" half of
    # the test.
    "PYPI_USER", "PYPI_API",
})

# The publishing keys above, kept separate so the test can exempt them.
UNREAD_BY_DESIGN: frozenset[str] = frozenset({"PYPI_USER", "PYPI_API"})


def unknown_env_keys(env_path: Path | None = None) -> list[str]:
    """
    Names set in a .env file that nothing in this project reads.

    Args:
        env_path: The file to inspect. Defaults to `.env` beside the
                  project root.

    Returns:
        Sorted names, excluding comments, blanks and anything in
        KNOWN_ENV_KEYS. An unreadable or absent file gives an empty list:
        this is a diagnostic, and it must never be the thing that fails.
    """
    path = env_path or (PROJECT_ROOT / ".env")
    found: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=", 1)[0].strip()
            if name and name not in KNOWN_ENV_KEYS:
                found.append(name)
    except OSError:
        return []
    return sorted(set(found))
