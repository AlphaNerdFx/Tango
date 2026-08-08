"""
translation.py
--------------
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
import threading
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Both public mirrors this list used to carry are gone: translate.argosopentech.com
# no longer resolves, and libretranslate.de 301-redirects to the commercial
# libretranslate.com, which requires an API key. Probing them cost a timeout per
# run and never once produced a translation, while printing "community mirrors
# are unavailable" as though that were a temporary condition.
#
# Empty by default, therefore. Set LIBRETRANSLATE_URL to your own instance
# (`make translate-setup` installs one you can run locally) and it is used
# first; otherwise translation goes straight to local argostranslate models,
# which need no key, no network at query time, and no one else's uptime.
LIBRETRANSLATE_MIRRORS: list[str] = [
    m for m in os.getenv("LIBRETRANSLATE_MIRRORS", "").split(",") if m.strip()
]
LIBRETRANSLATE_LOCAL       = os.getenv("LIBRETRANSLATE_URL", "http://localhost:5000")
LIBRETRANSLATE_TIMEOUT     = 5    # seconds for mirror probe
LOCAL_TRANSLATION_TIMEOUT  = 15   # seconds max per word for local argostranslate
                                   # argostranslate on CPU can take 30-60s/word
                                   # with Stanza SBD — abort and warn if exceeded

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
    """
    Return True if a *direct* argostranslate package for this pair is installed.

    Direct only. Use is_translation_available() to ask whether translation can
    actually happen, which is a weaker and more useful question — see there.
    """
    try:
        from argostranslate import package as pkg
        installed = pkg.get_installed_packages()
        return any(
            p.from_code == from_code and p.to_code == to_code
            for p in installed
        )
    except Exception:
        return False


def is_translation_available(from_code: str, to_code: str) -> bool:
    """
    Return True if argostranslate can translate this pair, pivots included.

    This is deliberately a different question from is_model_installed().
    argostranslate publishes 100 packages: 49 into English, 49 out of it, and
    exactly two (pt<->es) involving no English at all. So a direct package for
    de->fr does not exist and never will, but argostranslate composes one
    itself: get_installed_languages() walks the installed set and builds a
    CompositeTranslation for every pair reachable through an intermediate.
    With de->en and en->fr installed, de->fr works.

    Gating translation on a direct package therefore refused pairs that would
    have worked, which is most non-English pairs — the whole point of
    --def-lang for a learner whose own language is not English.

    Args:
        from_code: BCP-47 source code.
        to_code:   BCP-47 target code.

    Returns:
        True if a translation object exists for the pair.
    """
    if from_code == to_code:
        return True
    try:
        from argostranslate import translate
        return translate.get_translation_from_codes(from_code, to_code) is not None
    except Exception:
        # Raised when no route exists, and when argostranslate is absent.
        return False


def get_translation_route(from_code: str, to_code: str) -> list[tuple[str, str]] | None:
    """
    Return the packages that must be installed to serve this pair.

    Args:
        from_code: BCP-47 source code.
        to_code:   BCP-47 target code.

    Returns:
        [(from, to)] when a direct package exists, [(from, "en"), ("en", to)]
        when the pair must pivot through English, or None when neither is
        possible. Pairs already covered still return their route — the caller
        decides what is missing.
    """
    if from_code == to_code:
        return []

    try:
        from argostranslate import package as pkg
        available = pkg.get_available_packages()
    except Exception as exc:
        logger.warning("Could not fetch the argostranslate package index: %s", exc)
        return None

    pairs = {(p.from_code, p.to_code) for p in available}

    if (from_code, to_code) in pairs:
        return [(from_code, to_code)]

    # Pivot. English is the hub for all but pt<->es, so this is the only
    # intermediate worth trying rather than a general graph search.
    if (from_code, "en") in pairs and ("en", to_code) in pairs:
        return [(from_code, "en"), ("en", to_code)]

    return None


def install_translation(from_code: str, to_code: str) -> bool:
    """
    Install everything needed to translate this pair, pivots included.

    A direct package is one download. A pivot pair is two, and both must
    succeed for the pair to work — argostranslate only composes de->fr once
    de->en and en->fr are both present.

    Args:
        from_code: BCP-47 source code.
        to_code:   BCP-47 target code.

    Returns:
        True when the pair can be translated afterwards.
    """
    if is_translation_available(from_code, to_code):
        return True

    route = get_translation_route(from_code, to_code)
    if route is None:
        print(
            f"\n  [{from_code}->{to_code}] argostranslate publishes no model for "
            f"this pair, and no route through English either."
        )
        return False

    if len(route) > 1:
        hops = " -> ".join([route[0][0]] + [hop[1] for hop in route])
        print(
            f"\n  No direct {from_code}->{to_code} model exists; "
            f"translating via {hops} instead ({len(route)} downloads)."
        )

    for hop_from, hop_to in route:
        if is_model_installed(hop_from, hop_to):
            continue
        if not download_model(hop_from, hop_to):
            return False

    # argostranslate caches the installed-language graph, and it is that graph
    # the composite pivot is built from. Without clearing it, a model
    # installed in this process stays invisible until the next one.
    try:
        from argostranslate import translate
        translate.get_installed_languages.cache_clear()
    except Exception:  # pragma: no cover — cache is an implementation detail
        pass

    return is_translation_available(from_code, to_code)


def download_model(from_code: str, to_code: str) -> bool:
    """
    Download and install one argostranslate package for a language pair.

    Shows a progress bar during download. Returns True on success.

    Handles a single direct package only. Use install_translation() for a
    pair that may need to pivot through English.

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


class _TranslationTimeoutError(Exception):
    """Internal: local translation exceeded LOCAL_TRANSLATION_TIMEOUT."""


def translate_local(word: str, from_code: str, to_code: str) -> Optional[str]:
    """
    Translate a word using the locally installed argostranslate model.

    Enforces LOCAL_TRANSLATION_TIMEOUT per word. argostranslate with the
    Stanza sentence boundary model can take 30-60s per word on CPU — this
    timeout prevents the pipeline hanging silently.

    Returns the translated string, or None on failure or timeout.

    Raises:
        ModelNotInstalledError: Model not installed for this pair.
    """
    # Asks whether translation is possible, not whether a direct package
    # exists: argostranslate composes de->en->fr itself, and requiring a
    # direct package refused every pair it publishes no model for, which is
    # every non-English pair except pt<->es.
    if not is_translation_available(from_code, to_code):
        raise ModelNotInstalledError(from_code, to_code)

    import concurrent.futures

    def _do_translate() -> str:
        from argostranslate import translate
        translation = translate.get_translation_from_codes(from_code, to_code)
        return translation.translate(word).strip()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_translate)
            result = future.result(timeout=LOCAL_TRANSLATION_TIMEOUT)
            logger.debug(
                "Local translation: '%s' (%s->%s) -> '%s'",
                word, from_code, to_code, result,
            )
            return result or None
    except concurrent.futures.TimeoutError:
        logger.warning(
            "Local translation timed out after %ds for '%s'. "
            "argostranslate is too slow on this machine for per-word translation. "
            "Consider using a community mirror or setting DEF_LANG to match LANGUAGE.",
            LOCAL_TRANSLATION_TIMEOUT, word,
        )
        return None
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
    # Warn once if local translation is about to run — it can be slow on CPU
    _warn_local_translation_slow(pair)
    try:
        result = translate_local(word, from_code, to_code)
        if result:
            return result
    except ModelNotInstalledError:
        pass  # fall through to prompt

    # ── Tier 3: User prompt ───────────────────────────────────────────────────
    if not interactive:
        return None

    # definition.py now fetches words concurrently across several threads
    # (see fetch_definitions()), and more than one of them can need
    # translation for the same language pair at the same time. Everything
    # from here down, including the interactive input() call and the
    # check-then-set on _user_choice, is serialized under one lock so only
    # one thread ever prompts for a given run, and every other thread that
    # arrives here simply picks up the decision the first one made instead
    # of racing on _user_choice or overlapping prompts on the same terminal.
    with _prompt_lock:
        # If user already made a choice for this pair this run, honour it silently
        if pair in _user_choice:
            stored = _user_choice[pair]
            if stored == "continue":
                return None
            elif stored == "exit":
                raise TranslationUnavailableError(
                    f"Translation unavailable for {pair}. Exiting."
                )
            # "download" — model should now be installed, try again
            try:
                return translate_local(word, from_code, to_code)
            except ModelNotInstalledError:
                return None

        _warn_translation_unavailable(pair)
        choice = _prompt_translation_options(from_code, to_code)
        _user_choice[pair] = choice

        if choice == "download":
            # install_translation, not download_model: the pair may need two
            # hops through English, and one of them alone leaves it unusable.
            success = install_translation(from_code, to_code)
            if success:
                result = translate_local(word, from_code, to_code)
                return result
            print(f"  Download failed. Continuing without translation for this run.")
            _user_choice[pair] = "continue"
            return None

        elif choice == "continue":
            return None

        else:  # exit
            raise TranslationUnavailableError(
                f"Translation unavailable for {pair}. Exiting."
            )


# ── Internal prompt helpers ───────────────────────────────────────────────────

# Track warning state and user choice per run, per language pair
_warned_this_run:    set[str]   = set()
_warned_slow:        set[str]   = set()
_user_choice:        dict[str, str] = {}  # pair -> "download" | "continue" | "exit"

# Serializes the Tier 3 interactive prompt in translate_word() -- see the
# comment at that call site for why this exists.
_prompt_lock = threading.Lock()


def _warn_local_translation_slow(pair: str) -> None:
    """
    Warn once per run when local argostranslate is about to be used.
    argostranslate on CPU with Stanza SBD can take 30-60s per word —
    this sets the user's expectation before the first translation starts.
    """
    if pair in _warned_slow:
        return
    _warned_slow.add(pair)
    msg = (
        "\n  [info]  Local translation active: " + pair + "\n"
        "          argostranslate on CPU can take 10-60s per word.\n"
        "          Results will be cached after first run.\n"
    )
    print(msg)


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
        try:
            choice = input("  Choice [d/f/x]: ").strip().lower()
        except EOFError:
            # No one is there to answer. Non-interactive runs are a documented
            # pattern (CLAUDE.md section 6 pipes input into `make run`), and a
            # piped run whose input is exhausted lands here, as does any use
            # from a script or CI.
            #
            # This used to raise straight out of a definition-fetch worker
            # thread, so `--def-lang` with no translation model produced a
            # traceback per lemma and a run that neither finished nor exited.
            # Continuing without translation is the honest default: it keeps
            # the run alive and gives native-language definitions, which is
            # what the [f] option does and what a user with no model would
            # almost certainly pick.
            print("\n  No input available — continuing with native definitions.")
            return "continue"
        except KeyboardInterrupt:
            # Ctrl-C at a prompt means stop, not crash.
            print("\n  Interrupted — exiting.")
            return "exit"

        if choice == "d":
            return "download"
        elif choice == "f":
            return "continue"
        elif choice == "x":
            return "exit"
        else:
            print("  Please enter d, f, or x.")


def reset_warning_state() -> None:
    """Reset per-run warning and choice state. Called at pipeline start and in tests."""
    _warned_this_run.clear()
    _warned_slow.clear()
    _user_choice.clear()