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

# Paths

# SQLite database — shared across state.py, definition.py, deck.py
DB_PATH: Path = Path(os.getenv("DB_PATH", "pipeline.db"))

# Output directory for generated .apkg files
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "output"))

# Review file — deferred queue words written here for manual resolution
REVIEW_FILE: Path = Path(os.getenv("REVIEW_FILE", "review.json"))

# Anki

# AnkiConnect host — change if running Anki on a non-default port
ANKI_HOST: str = os.getenv("ANKI_HOST", "http://localhost:8765")

# AnkiConnect API version — do not change unless AnkiConnect upgrades its API
ANKI_VERSION: int = 6

# Seconds to wait for AnkiConnect to respond before timing out
ANKI_TIMEOUT: int = int(os.getenv("ANKI_TIMEOUT", "5"))

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

# Webshare proxy credentials — recommended provider for youtube-transcript-api
# Leave unset to run without a proxy (may trigger IP blocks on heavy use)
WEBSHARE_USERNAME: str | None = os.getenv("WEBSHARE_USERNAME")
WEBSHARE_PASSWORD: str | None = os.getenv("WEBSHARE_PASSWORD")

# Generic proxy URLs — alternative to Webshare
# Format: "http://user:pass@host:port" or "socks5://user:pass@host:port"
PROXY_HTTP_URL:  str | None = os.getenv("PROXY_HTTP_URL")
PROXY_HTTPS_URL: str | None = os.getenv("PROXY_HTTPS_URL")