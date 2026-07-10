"""
Responsible for:
  1. Downloading argostranslate language pair models with a progress bar
  2. Translating a word from source language to target language locally
  3. Probing LibreTranslate community mirrors as a faster fallback
  4. Managing the three-tier resolution:
       Tier 1: community mirror (fast, no install)
       Tier 2: local argostranslate (reliable, requires one-time model download)
       Tier 3: degrade gracefully — warn user, offer options

Translation is only used when DEF_LANG is set and differs from LANGUAGE.
When LANGUAGE == DEF_LANG or DEF_LANG is absent, this module is not called.

Dependencies:
    argostranslate (installed via pip as part of libretranslate)
    requests

Constants (moved to config.py at end of project):
    LIBRETRANSLATE_MIRRORS
    LIBRETRANSLATE_TIMEOUT
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

LIBRETRANSLATE_MIRRORS = [
    "https://translate.argosopentech.com",
    "https://libretranslate.de",
]
LIBRETRANSLATE_LOCAL   = os.getenv("LIBRETRANSLATE_URL", "http://localhost:5000")
LIBRETRANSLATE_TIMEOUT = 5  # seconds for mirror probe

# Approximate model sizes in MB — used to inform the user before download
# Updated when argostranslate package index reports sizes
_MODEL_SIZE_HINT_MB = 150


# ── Custom exceptions ─────────────────────────────────────────────────────────

class TranslationUnavailableError(Exception):
    """
    Raised when no translation source is available and the user chose to exit.
    The pipeline catches this and exits cleanly.
    """


class ModelNotInstalledError(Exception):
    """
    Raised when the required argostranslate model is not installed.
    Caller should offer to download it.
    """
    def __init__(self, from_code: str, to_code: str) -> None:
        self.from_code = from_code
        self.to_code   = to_code
        super().__init__(
            f"No local translation model installed for {from_code} -> {to_code}."
        )


# ── Tier 1: Community mirror ──────────────────────────────────────────────────

def _probe_mirror(mirror_url: str) -> bool:
    """Return True if the LibreTranslate mirror is reachable."""
    try:
        r = requests.get(f"{mirror_url}/languages", timeout=LIBRETRANSLATE_TIMEOUT)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False


def _translate_via_mirror(
    word: str,
    from_code: str,
    to_code: str,
    mirror_url: str,
) -> Optional[str]:
    """
    Translate a word using a LibreTranslate community mirror.

    Returns the translated string, or None on failure.
    """
    try:
        r = requests.post(
            f"{mirror_url}/translate",
            json={"q": word, "source": from_code, "target": to_code, "format": "text"},
            timeout=LIBRETRANSLATE_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("translatedText", "").strip() or None
    except requests.exceptions.RequestException as exc:
        logger.debug("Mirror translation failed: %s", exc)
    return None


def try_community_mirror(
    word: str,
    from_code: str,
    to_code: str,
) -> Optional[str]:
    """
    Try each community mirror in order. Return translation or None if all fail.
    """
    for mirror in LIBRETRANSLATE_MIRRORS:
        if _probe_mirror(mirror):
            result = _translate_via_mirror(word, from_code, to_code, mirror)
            if result:
                logger.debug("Community mirror '%s' translated '%s'.", mirror, word)
                return result
    return None


# ── Tier 2: Local argostranslate ──────────────────────────────────────────────

def is_model_installed(from_code: str, to_code: str) -> bool:
    """Return True if the argostranslate model for this pair is installed."""
    try:
        from argostranslate import package as pkg
        installed = pkg.get_installed_packages()
        return any(
            p.from_code == from_code and p.to_code == to_code
            for p in installed
        )
    except Exception:
        return False


def download_model(from_code: str, to_code: str) -> bool:
    """
    Download and install the argostranslate model for a language pair.

    Shows a progress bar during download. Returns True on success.

    The download is done via requests streaming (not argostranslate's
    internal downloader) so we can track and display progress.
    """
    try:
        from argostranslate import package as pkg
        pkg.update_package_index()
        available = pkg.get_available_packages()
    except Exception as exc:
        logger.error("Failed to fetch argostranslate package index: %s", exc)
        return False

    # Find the matching package
    match = [
        p for p in available
        if p.from_code == from_code and p.to_code == to_code
    ]
    if not match:
        print(
            f"\n  [{from_code}->{to_code}] No translation model available "
            f"for this language pair."
        )
        return False

    package = match[0]
    url     = package.links[0]
    pair    = f"{from_code}->{to_code}"

    print(
        f"\n  Downloading translation model: {pair} "
        f"(~{_MODEL_SIZE_HINT_MB}MB, one-time download)\n"
    )

    # Stream download with progress bar
    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()

        total     = int(r.headers.get("Content-Length", 0))
        received  = 0
        bar_width = 40

        with tempfile.NamedTemporaryFile(
            suffix=".argosmodel", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    tmp.write(chunk)
                    received += len(chunk)

                    # Draw progress bar
                    if total:
                        pct  = received / total
                        done = int(bar_width * pct)
                        bar  = "█" * done + "░" * (bar_width - done)
                        mb_done  = received / 1024 / 1024
                        mb_total = total    / 1024 / 1024
                        sys.stdout.write(
                            f"\r  [{bar}]  {mb_done:.1f} / {mb_total:.1f} MB"
                        )
                        sys.stdout.flush()
                    else:
                        mb_done = received / 1024 / 1024
                        sys.stdout.write(f"\r  Downloaded: {mb_done:.1f} MB")
                        sys.stdout.flush()

        sys.stdout.write("\n")
        print("  Installing model...")

        from argostranslate.package import install_from_path
        install_from_path(str(tmp_path))
        tmp_path.unlink(missing_ok=True)

        print(f"  Model installed: {pair}\n")
        return True

    except requests.exceptions.RequestException as exc:
        sys.stdout.write("\n")
        logger.error("Model download failed: %s", exc)
        return False
    except Exception as exc:
        sys.stdout.write("\n")
        logger.error("Model installation failed: %s", exc)
        return False


def translate_local(word: str, from_code: str, to_code: str) -> Optional[str]:
    """
    Translate a word using the locally installed argostranslate model.

    Returns the translated string, or None if the model is not installed
    or translation fails.

    Raises:
        ModelNotInstalledError: Model not installed for this pair.
    """
    if not is_model_installed(from_code, to_code):
        raise ModelNotInstalledError(from_code, to_code)

    try:
        from argostranslate import translate
        translation = translate.get_translation_from_codes(from_code, to_code)
        result = translation.translate(word).strip()
        logger.debug("Local translation: '%s' (%s->%s) -> '%s'", word, from_code, to_code, result)
        return result or None
    except Exception as exc:
        logger.warning("Local translation failed for '%s': %s", word, exc)
        return None


# ── Main entry point ──────────────────────────────────────────────────────────

def translate_word(
    word: str,
    from_code: str,
    to_code: str,
    interactive: bool = True,
) -> Optional[str]:
    """
    Translate a word from source to target language using the best available source.

    Resolution order:
        1. Community mirror (fast, no install required)
        2. Local argostranslate model (reliable, requires one-time download)
        3. User prompt — offer to download, continue without, or exit

    Args:
        word:        Lemma to translate.
        from_code:   BCP-47 source language code (e.g. "fr").
        to_code:     BCP-47 target language code (e.g. "en").
        interactive: If False, skip prompts and return None on failure.
                     Used in tests and batch processing.

    Returns:
        Translated word string, or None if unavailable and user chose to continue.

    Raises:
        TranslationUnavailableError: User chose to exit.
    """
    pair = f"{from_code}->{to_code}"

    # ── Tier 1: Community mirror ──────────────────────────────────────────────
    result = try_community_mirror(word, from_code, to_code)
    if result:
        return result

    # ── Tier 2: Local model ───────────────────────────────────────────────────
    try:
        result = translate_local(word, from_code, to_code)
        if result:
            return result
    except ModelNotInstalledError:
        pass  # fall through to prompt

    # ── Tier 3: User prompt ───────────────────────────────────────────────────
    if not interactive:
        return None

    _warn_translation_unavailable(pair)
    choice = _prompt_translation_options(from_code, to_code)

    if choice == "download":
        success = download_model(from_code, to_code)
        if success:
            return translate_local(word, from_code, to_code)
        print(f"  Download failed. Continuing without translation for this run.")
        return None

    elif choice == "continue":
        return None

    else:  # exit
        raise TranslationUnavailableError(
            f"Translation unavailable for {pair}. Exiting."
        )


# ── Internal prompt helpers ───────────────────────────────────────────────────

# Track whether the unavailability warning has been shown this run
_warned_this_run: set[str] = set()


def _warn_translation_unavailable(pair: str) -> None:
    """Print the translation unavailability warning once per language pair per run."""
    if pair in _warned_this_run:
        return
    _warned_this_run.add(pair)

    from_code, to_code = pair.split("->")
    print(f"\n  {'─' * 56}")
    print(f"  [warn]  Translation mode: {from_code} -> {to_code}")
    print(f"          Community mirrors are unavailable.")
    print(f"          No local model installed for {pair}.")
    print(f"  {'─' * 56}\n")


def _prompt_translation_options(from_code: str, to_code: str) -> str:
    """
    Prompt the user for how to proceed when translation is unavailable.

    Returns:
        "download" — user wants to install the model now
        "continue" — user wants to continue without translation
        "exit"     — user wants to exit
    """
    pair = f"{from_code}->{to_code}"
    print("  Options:")
    print(f"    [d] Download translation model now ({pair}, ~{_MODEL_SIZE_HINT_MB}MB, one-time)")
    print(f"    [f] Continue without translation (native {from_code} definitions instead)")
    print(f"    [x] Exit\n")

    while True:
        choice = input("  Choice [d/f/x]: ").strip().lower()
        if choice == "d":
            return "download"
        elif choice == "f":
            return "continue"
        elif choice == "x":
            return "exit"
        else:
            print("  Please enter d, f, or x.")


def reset_warning_state() -> None:
    """Reset the per-run warning tracker. Called at pipeline start and in tests."""
    _warned_this_run.clear()