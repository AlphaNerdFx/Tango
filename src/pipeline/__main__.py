"""
CLI entry point for Tango, distributed on PyPI as `tango-anki`.

Usage:
    tango run VIDEO_ID --deck "Deck::Name" [--verbose]
    tango review --deck "Deck::Name"
    tango backlog --deck "Deck::Name"

Or via Makefile:
    make run VIDEO_ID=<id> DECK="<name>"
    make review DECK="<name>"
    make backlog DECK="<name>"

Commands:
    run         Full pipeline: transcript, NLP, deck check, definitions, .apkg
    review      Process review.json decisions and build .apkg for approved words
    backlog     Process the SQLite backlog left when Anki was unavailable
    doctor      Report what is installed and missing, and the command to fix each
    languages   List the language codes this pipeline supports
    setup       Guided .env setup for the optional Merriam-Webster key

    install-model, install-translation, build-dictionary, build-antonyms
                One-off installs. `tango --help` lists every option.

`tango run` is the entry point declared in [project.scripts]. `python -m
pipeline` still reaches the same place, and was the only way in before
v0.7.0 gave the project a console script.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Optional
from pathlib import Path

import typer

from pipeline import (
    TangoError,
    __version__,
    cards,
    deck as deck_module,
    definition as definition_module,
    nlp as nlp_module,
    transcript as transcript_module,
)
from pipeline.translation import reset_warning_state

from pipeline.config import MW_RATE_LIMIT, unknown_env_keys
from pipeline.config import is_wsl as config_is_wsl
from pipeline.definition import reset_circuit_breaker
from pipeline.language import (
    LanguageResolutionError,
    list_supported_languages,
    resolve_language_code,
)
from pipeline.deck import (
    AnkiNotRunningError,
    get_deck_names,
    prompt_queue,
    process_backlog,
    load_review_decisions,
)
from pipeline.state import (
    Session,
    VideoAlreadyProcessedError,
    check_video_not_processed,
    log_package,
    mark_video_processed,
    save_vocabulary,
)

# ── Colour output helpers ─────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
DIM    = "\033[2m"


def _info(msg: str)  -> None: print(f"{CYAN}{BOLD}[info]{RESET}  {msg}")
def _ok(msg: str)    -> None: print(f"{GREEN}{BOLD}[ ok ]{RESET}  {msg}")
def _warn(msg: str)  -> None: print(f"{YELLOW}{BOLD}[warn]{RESET}  {msg}")
def _err(msg: str)   -> None: print(f"{RED}{BOLD}[err ]{RESET}  {msg}", file=sys.stderr)
def _rule()          -> None: print(f"{DIM}{'─' * 60}{RESET}")


# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(name)-24s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )


# ── AnkiConnect import ────────────────────────────────────────────────────────

def _ask(prompt: str, default: str = "") -> str:
    """
    Read one line from the user, returning `default` when there is no input.

    Args:
        prompt:  Text shown to the user.
        default: Returned when stdin is exhausted or closed. Choose the
                 answer that does the least, since it is what an automated
                 run will get.

    Returns:
        The user's stripped, lower-cased answer, or `default`.

    A bare input() raises EOFError once stdin runs out, which produced a
    traceback in the recipe this project's own docs recommend:
    `echo "s" | make run ...` supplies exactly one line, the queued-word
    prompt consumes it, and the import prompt then hit EOF and killed the
    run *after* the package had been written successfully. CLAUDE.md 4.4 --
    no traceback for an expected failure, and stdin running out in a piped
    or CI run is expected.
    """
    try:
        return input(prompt).strip().lower()
    except EOFError:
        print()
        return default


# WSL detection has one implementation, in config, and this is an alias to
# it. There were briefly two, added on 4 September 2026 when deck.py needed
# the same answer: both read /proc/version, and two implementations of one
# fact are two chances to disagree. The private name is kept because it is
# what the tests patch and what _translate_wsl_path reads.
_is_wsl = config_is_wsl


def _translate_wsl_path(path: str) -> str:
    """
    Translate a WSL mount path (e.g. /mnt/c/Users/name/file.apkg) to its
    Windows equivalent (C:\\Users\\name\\file.apkg).

    No-op when not running under WSL, or when the path doesn't match the
    /mnt/<drive>/ pattern (already a native path, or a WSL-internal path
    with no Windows equivalent, e.g. under /home).

    Anki running natively on Windows under WSL2 is a common setup (see
    the WSL Setup wiki page), and AnkiConnect's importPackage action
    resolves the path on the Windows side, a Linux path is meaningless
    to it.
    """
    if not _is_wsl():
        return path
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path)
    if not match:
        return path
    drive, rest = match.group(1), match.group(2)
    return f"{drive.upper()}:\\{rest.replace('/', chr(92))}"


def _prompt_import(apkg_path: Path) -> None:
    """
    Ask the user if they want to auto-import the .apkg into Anki.

    Requires Anki to be running with AnkiConnect.
    Uses the absolute path, AnkiConnect requires this. Under WSL, the
    path is translated to its Windows equivalent first (see
    _translate_wsl_path()) since Windows-side AnkiConnect can't resolve
    a Linux /mnt/... path.

    Constraint: only works when Anki is running on the same machine
    as the pipeline. If running on a remote server, this will fail
    gracefully and the user imports manually.
    """
    print()
    _rule()
    # Default "n": an automated run must not import into a real collection
    # just because nobody was there to say no.
    answer = _ask(f"  Import {apkg_path.name} into Anki now? [y/N]: ", default="n")

    if answer != "y":
        _info(f"Skipped. Import manually: File → Import in Anki.")
        return

    # Align the collection's notetype with cards.FIELDS BEFORE importing.
    # Without this, an import whose model carries fields the collection's
    # notetype lacks does not merge -- Anki forks the notetype at a bumped
    # ID and the new cards land there, split off from every card the user
    # already has. ARCHITECTURE.md 8.32.
    #
    # Deliberately outside the import's try block, and deliberately fatal to
    # the import: if the alignment failed we do not know the notetype's
    # shape, and importing anyway is what causes the fork. Better to leave
    # the .apkg on disk than to split the user's collection.
    try:
        added = deck_module.ensure_model_fields(cards.MODEL_ID, cards.FIELDS)
    except Exception as exc:
        _warn(f"Could not align the Anki notetype: {exc}")
        _info("Import skipped, importing now could fork the notetype and")
        _info("separate new cards from your existing ones. Retry once Anki")
        _info("is reachable, or import manually after adding the fields.")
        return

    if added:
        _info(f"Added {len(added)} field(s) to the Anki notetype: {', '.join(added)}")
        _info("This is a schema change, Anki will ask for a full sync next time.")

    try:
        import requests as req
        absolute_path = _translate_wsl_path(str(apkg_path.resolve()))

        # v0.10.0. Under WSL, a path that is still POSIX after translation
        # is one Windows-side Anki cannot open: /mnt/<drive> paths convert,
        # anything under /home or /tmp does not, because it has no Windows
        # equivalent. That happens to any WSL user who clones to ~ rather
        # than /mnt/c, which is the more natural place to clone.
        #
        # Gated on which host answered, not on being under WSL alone. A WSL
        # user can run Anki inside WSL through WSLg, and then localhost is
        # the right address and POSIX paths are exactly what AnkiConnect
        # wants. Blocking those would break a setup that works. The host
        # latched by deck._anki_request is the evidence: localhost means
        # Anki is on this side of the boundary, anything else means it is
        # on the Windows side and needs a path Windows can resolve.
        #
        # Caught here rather than sent, because AnkiConnect's own answer is
        # a file-not-found that says nothing about why the file it cannot
        # find is one that plainly exists.
        anki_is_on_windows = not any(
            local in deck_module._active_host for local in ("localhost", "127.0.0.1")
        )
        if _is_wsl() and anki_is_on_windows and absolute_path.startswith("/"):
            _warn("Anki runs on the Windows side and cannot open a path "
                  "inside WSL's own filesystem.")
            _info(f"  {absolute_path}")
            _info("  The package is fine. Windows just cannot see where it "
                  "is.")
            _info("  Either import it by hand, or set OUTPUT_DIR in .env to "
                  "a path under /mnt (for example /mnt/c/Users/you/Anki) so "
                  "the next run writes somewhere Windows can reach.")
            return

        response = req.post(
            deck_module.ANKI_HOST,
            json={
                "action":  "importPackage",
                "version": 6,
                "params":  {"path": absolute_path},
            },
            # importPackage, not a quick query -- see config.ANKI_IMPORT_TIMEOUT.
            timeout=deck_module.ANKI_IMPORT_TIMEOUT,
        )
        data = response.json()
        if data.get("error"):
            _warn(f"AnkiConnect import error: {data['error']}")
            _info(f"Import manually: File → Import → select {apkg_path.name}")
        else:
            _ok("Package imported into Anki.")
            _info("Open Anki and sync to push cards to AnkiWeb.")
    except Exception as exc:
        _warn(f"Auto-import failed: {exc}")
        _info(f"Import manually: File → Import → select {apkg_path.name}")


# ── Summary block ─────────────────────────────────────────────────────────────

def _wrap_words(words: list[str], width: int = 62, cap: int = 40) -> list[str]:
    """
    Lay a word list out as comma-separated lines for the run summary.

    Args:
        words: Already-sorted words to print.
        width: Characters per line before wrapping.
        cap:   Stop after this many and say how many were left.

    Returns:
        Lines to print, or [] for no words.

    Capped because the point is triage, not a dump: a run against a badly
    transcribed video can put hundreds of words here, and a wall of them is
    read as noise and skipped.
    """
    shown, remainder = words[:cap], len(words) - cap
    lines: list[str] = []
    current = ""
    for word in shown:
        candidate = f"{current}, {word}" if current else word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if remainder > 0:
        lines.append(f"and {remainder} more")
    return lines


# The breaker keys sources by an internal short name. A user reading a run
# summary has never seen "dictapi:en" and should not have to.
_SOURCE_LABELS = {
    "mw": "Merriam-Webster",
    "dictapi:en": "dictionaryapi.dev",
}


def _print_summary(
    video_id: str,
    deck_name: str,
    apkg_path: Path,
    card_count: int,
    fallback_count: int,
    skipped_count: int,
    not_found_count: int,
    not_found_words: Optional[list[str]] = None,
    sources_stopped: Optional[list[str]] = None,
) -> None:
    _rule()
    print(f"  {GREEN}{BOLD}Done.{RESET}")
    print(f"  Video:    {video_id}")
    print(f"  Deck:     {deck_name}")
    total = card_count + fallback_count
    print(f"  Cards:    {total} total  ({card_count} standard, {fallback_count} fallback)")
    if skipped_count:
        print(f"  {YELLOW}Dropped:  {skipped_count} word(s) had no definition and no transcript example.{RESET}")
    if not_found_count:
        print(f"  {YELLOW}No definition found for {not_found_count} word(s), fallback cards created where possible.{RESET}")
        # Naming them, not just counting them. Measured on a real 406-card
        # German run, 28 words landed here and 25 of them were transcript
        # damage or names ("Bissch", "Herauszufinde", "Barack") that a
        # learner deletes on sight. Printed so they can be found and removed
        # in one pass in Anki's browser instead of one at a time during
        # review. They are NOT filtered automatically: three signals were
        # measured and none separates these from real words. See
        # ARCHITECTURE.md 8.40.
        for line in _wrap_words(sorted(not_found_words or [])):
            print(f"            {DIM}{line}{RESET}")
    # A source the breaker gave up on is the difference between a thin deck
    # and a deck that looks broken, and it used to be visible only in a log
    # line at WARNING. A real English run shipped 927 of 1094 cards with no
    # definition because Merriam-Webster stopped answering partway through,
    # and nothing in this summary said so.
    for source in sources_stopped or []:
        label = _SOURCE_LABELS.get(source, source)
        print(f"  {YELLOW}Stopped:  {label} stopped answering partway through this "
              f"run and was skipped for the rest of it.{RESET}")
        print(f"            That is why cards above are missing content, rather "
              f"than the words being unknown.")
        if source == "mw":
            print(f"            {DIM}-> Retry later; whatever already worked is "
                  f"cached and will not be refetched.{RESET}")
            print(f"            {DIM}-> Or lower MW_RATE_LIMIT (now "
                  f"{MW_RATE_LIMIT}/s) in .env.{RESET}")
        else:
            print(f"            {DIM}-> Retry later; whatever already worked is "
                  f"cached and will not be refetched.{RESET}")
    print(f"  Package:  {apkg_path}")
    _rule()


# ── Progress and timing ───────────────────────────────────────────────────────
#
# v0.7.0: a long run has to be legible. Pacing Merriam-Webster (8.43) turned
# the definition phase into minutes of silence on a large English video, and
# silence is indistinguishable from a hang.


def _duration(seconds: float) -> str:
    """Render a number of seconds the way a person would say it."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class _Progress:
    """
    A one-line progress display for a phase that takes a while.

    Redraws in place on a terminal, which keeps a thousand-word run to a
    single line. Anywhere else -- a pipe, a log file, CI -- carriage returns
    are noise, so it prints one line per decile instead and stays readable
    afterwards. `make run > run.log` is a normal thing to do here.

    The estimate is deliberately naive: elapsed divided by completed, times
    what is left. The work per word is uniform enough for that to be honest,
    and a cleverer estimate that is wrong is worse than a simple one that is
    roughly right.
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self._start = time.monotonic()
        self._tty = sys.stdout.isatty()
        self._last_decile = -1

    def update(self, done: int, total: int) -> None:
        if not total:
            return
        elapsed = time.monotonic() - self._start
        share = done / total
        # An estimate before there is anything to estimate from is a guess
        # dressed as information.
        eta = ""
        if done >= 5 and share < 1:
            eta = f", ~{_duration(elapsed / done * (total - done))} left"
        text = f"  {self._label}: {done}/{total} ({share:.0%}{eta})"

        if self._tty:
            print(f"\r{text}\033[K", end="", flush=True)
            return

        decile = int(share * 10)
        if decile > self._last_decile:
            self._last_decile = decile
            print(text, flush=True)

    def finish(self) -> None:
        """Clear the line so the phase's own result can take its place."""
        if self._tty:
            print("\r\033[K", end="", flush=True)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start


# ── First run ─────────────────────────────────────────────────────────────────
#
# v0.8.2. Before this, a fresh `pip install tango-anki` got as far as fetching
# the transcript and then stopped with "spaCy model not found. Run: ...". The
# message was correct and the timing was not: the work was already done, and
# the user was told to go and run a second command before they could see
# anything work at all.


def _ensure_spacy_model(language: str) -> None:
    """
    Make sure the spaCy model for `language` is installed before the run.

    Checked before the transcript is fetched, not after. The check is a
    lookup in the installed distribution list, so it costs nothing, and
    failing here wastes none of the user's time.

    On a terminal this offers to install the model and continues on yes.
    Anywhere else, and on no, it prints the command and exits 1 rather than
    prompting into a pipe that cannot answer.

    Args:
        language: BCP-47 language code the run resolved to.
    """
    from pipeline.language import get_spacy_model

    if nlp_module.is_model_installed(language):
        return

    try:
        model = get_spacy_model(language)
    except Exception:
        _err(f"No spaCy model exists for '{language}'.")
        _info("Run 'tango languages' to see the codes that are supported.")
        sys.exit(1)

    _warn(f"The spaCy model for '{language}' ({model}) is not installed.")
    _info("It is a one-off download of a few tens of MB, and the run needs it "
          "to find words at all.")

    if not sys.stdin.isatty():
        _info(f"Install it with: tango install-model {language}")
        sys.exit(1)

    if _ask(f"Download {model} now? [Y/n]: ", default="y") not in ("", "y", "yes"):
        _info(f"Nothing downloaded. Run 'tango install-model {language}' when ready.")
        sys.exit(1)

    if _run_install_model(language) != 0:
        sys.exit(1)


# ── Deck selection ────────────────────────────────────────────────────────────

def _select_deck(deck_arg: str | None, session: Session) -> str:
    """
    Resolve the deck name from --deck arg or interactive selection.

    If --deck is provided it is used directly, no prompt shown.
    If not provided, fetches deck list from AnkiConnect and prompts.

    Raises:
        SystemExit: if no deck selected or AnkiConnect unreachable.
    """
    if deck_arg:
        session.set_deck(deck_arg)
        return deck_arg

    # Interactive selection
    try:
        decks = get_deck_names()
    except AnkiNotRunningError:
        _err("Anki is not running. Start Anki and try again, or pass --deck directly.")
        sys.exit(1)

    if not decks:
        _err("No decks found in Anki.")
        _info("Create a deck in Anki first, or pass --deck \"<name>\" and it "
              "will be created on import.")
        sys.exit(1)

    print()
    _info("Select a deck:")
    for i, name in enumerate(decks, start=1):
        print(f"    {i}. {name}")
    print()

    while True:
        choice = _ask("  Enter number: ")
        if choice.isdigit() and 1 <= int(choice) <= len(decks):
            selected = decks[int(choice) - 1]
            session.set_deck(selected)
            return selected
        print(f"  Please enter a number between 1 and {len(decks)}.")


# ── Video ID normalisation ────────────────────────────────────────────────────

# A YouTube ID is 11 characters of base64url. The "-" and "_" matter here:
# roughly one ID in 64 starts with a hyphen. Under argparse that broke
# `--video-id -abc`, which was read as another option, and the Makefile
# passed `--video-id=<value>` to dodge it. As a positional argument the
# hazard is different rather than gone: `tango run -abc123defg` still looks
# like an option, and the escape is the conventional `tango run -- -abc`.
# This only has to recognise a well-formed ID.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# watch?v=, youtu.be/, /shorts/, /embed/, /live/: the forms people paste.
_YOUTUBE_URL_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def _normalise_video_id(value: str) -> str:
    """
    Accept either a bare video ID or a YouTube URL, and return the ID.

    ``--help`` has always said "YouTube video ID or URL", but nothing ever
    extracted an ID from a URL: the full URL was handed to the transcript
    API, which failed somewhere further down with a less obvious message.

    A value that is neither a recognisable URL nor an 11-character ID is
    returned unchanged rather than rejected. YouTube's ID format is stable
    today, but refusing to run on anything that fails a regex here would
    turn a future format change into an outage for a check that adds no
    safety, the transcript fetch reports a bad ID perfectly well.

    Args:
        value: Raw video id argument.

    Returns:
        The extracted ID when the input was a URL, otherwise the input
        stripped of surrounding whitespace.
    """
    value = value.strip()

    match = _YOUTUBE_URL_RE.search(value)
    if match:
        return match.group(1)

    # A URL we could not read an ID out of, better to say so here than to
    # let the transcript API fail on a string that is obviously not an ID.
    if "youtu" in value.lower() or value.startswith(("http://", "https://")):
        raise ValueError(
            f"Could not find a video ID in '{value}'.\n"
            "  Expected something like https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  or just the 11-character ID on its own."
        )

    return value


# ── Mode: default pipeline ────────────────────────────────────────────────────

def _run_pipeline(args: SimpleNamespace, session: Session) -> None:
    try:
        video_id = _normalise_video_id(args.video_id)
    except ValueError as exc:
        _err(str(exc))
        sys.exit(1)
    if video_id != args.video_id.strip():
        _info(f"Read video ID '{video_id}' from the URL you gave.")
    deck_name = _select_deck(args.deck, session)

    # ── 0. Resolve language ───────────────────────────────────────────────────
    try:
        language_code = resolve_language_code(
            language_flag=getattr(args, "language", None),
            deck_name=deck_name,
        )
    except LanguageResolutionError as exc:
        _err(str(exc))
        sys.exit(1)
    _ok(f"Target language: {language_code}")

    # ── 0b. Resolve definition language ──────────────────────────────────────
    def_language = getattr(args, "def_lang", None)
    if def_language and def_language != language_code:
        _info(f"Definition language: {def_language} (translation mode)")
    else:
        def_language = None  # native mode, no translation needed

    # Reset per-run translation warning state and circuit breaker state
    reset_warning_state()
    reset_circuit_breaker()

    # ── 1. Check not already processed ───────────────────────────────────────
    if args.force:
        _info(f"--force set: reprocessing '{video_id}' regardless of prior runs.")
    else:
        try:
            check_video_not_processed(video_id)
        except VideoAlreadyProcessedError as exc:
            _warn(str(exc))
            _warn("No new cards will be created. Use --force to reprocess.")
            sys.exit(0)

    # Checked here, before any work, so a missing model costs nothing.
    _ensure_spacy_model(language_code)

    # ── 2. Fetch transcript ───────────────────────────────────────────────────
    _info(f"Fetching transcript for: {video_id}")
    phase = time.monotonic()
    try:
        transcript = transcript_module.get_transcript(video_id, languages=[language_code])
        snippets   = transcript_module.get_snippets(transcript)
    except Exception as exc:
        _err(f"Transcript failed: {exc}")
        sys.exit(1)
    _ok(f"Transcript ready ({snippets['_snippet_count']} snippets, "
        f"language: {snippets['_language_code']}) in {_duration(time.monotonic() - phase)}")

    # ── 3. NLP ────────────────────────────────────────────────────────────────
    _info("Running spaCy NLP...")
    phase = time.monotonic()
    try:
        # surface_forms records how each lemma actually appeared, so the
        # transcript-example search can match "sais" for the lemma "savoir".
        surface_forms: dict = {}
        # parts_of_speech records how each lemma was actually used, so an
        # ambiguous word gets the right sense: "marcher" the verb, not
        # "marcher" the noun. See wiktdata._select_row.
        parts_of_speech: dict = {}
        vocabulary = nlp_module.process_transcript(
            snippets["_full_text"], language=language_code,
            surface_forms=surface_forms,
            parts_of_speech=parts_of_speech,
        )
    except Exception as exc:
        _err(f"NLP failed: {exc}")
        sys.exit(1)
    _ok(f"Vocabulary extracted: {len(vocabulary)} unique lemmas "
        f"in {_duration(time.monotonic() - phase)}")

    # ── 4. Save vocabulary to SQLite ──────────────────────────────────────────
    save_vocabulary(video_id, vocabulary)

    # ── 5. Deck check ─────────────────────────────────────────────────────────
    _info(f"Checking deck: {deck_name}")
    phase = time.monotonic()
    check_result = deck_module.check_vocabulary(vocabulary, deck_name)

    if not check_result.anki_available:
        _warn(
            "Anki is not running. All words written to backlog. "
            "Run 'make backlog' when Anki is available."
        )
        sys.exit(0)

    _ok(
        f"Deck check: {len(check_result.skip)} skip / "
        f"{len(check_result.queue)} queue / "
        f"{len(check_result.new)} new "
        f"in {_duration(time.monotonic() - phase)}"
    )

    # ── 6. CLI prompt for queued words ────────────────────────────────────────
    approved_lemmas: list[str] = []
    if check_result.queue:
        approved, _ = prompt_queue(check_result.queue)
        approved_lemmas = approved

    # Words going to definition fetch: confirmed new + user-approved from queue
    words_to_define = (
        [m.lemma for m in check_result.new] + approved_lemmas
    )

    if not words_to_define:
        _warn("No new words to define. Nothing to add to deck.")
        sys.exit(0)

    # ── 7. Fetch definitions ──────────────────────────────────────────────────
    _info(f"Fetching definitions for {len(words_to_define)} words...")
    tracker = _Progress("definitions")
    batch = definition_module.fetch_definitions(
            words_to_define, snippets,
            language=language_code,
            def_language=def_language,
            parts_of_speech=parts_of_speech,
            use_cache=not args.no_cache,
            progress=tracker.update,
        )
    tracker.finish()
    _ok(
        f"Definitions: {len(batch.found)} found "
        f"({len(batch.from_cache)} cached) / {len(batch.not_found)} not found "
        f"in {_duration(tracker.elapsed)}"
    )

    # ── 8. Build .apkg ────────────────────────────────────────────────────────
    _info("Building Anki package...")
    try:
        result = cards.build_package(
            video_id=video_id,
            deck_name=deck_name,
            found=batch.found,
            not_found=batch.not_found,
            snippets=snippets,
            language=language_code,
            not_found_examples=batch.not_found_examples,
            not_found_examples2=batch.not_found_examples2,
            surface_forms=surface_forms,
            not_found_synonyms=batch.not_found_synonyms,
            not_found_antonyms=batch.not_found_antonyms,
            not_found_ipa=batch.not_found_ipa,
            not_found_audio=batch.not_found_audio,
            progress=_info,
            def_language=def_language,
        )
    except ValueError as exc:
        _err(str(exc))
        sys.exit(1)

    # ── 9. Log + mark processed ───────────────────────────────────────────────
    log_package(video_id, result.path, deck_name, result.total_cards)
    mark_video_processed(
        video_id=video_id,
        deck_name=deck_name,
        card_count=result.total_cards,
        word_count=len(vocabulary),
    )

    # ── 10. Summary + import prompt ───────────────────────────────────────────
    _print_summary(
        video_id=video_id,
        deck_name=deck_name,
        apkg_path=result.path,
        card_count=result.standard_count,
        fallback_count=result.fallback_count,
        skipped_count=result.skipped_count,
        not_found_count=len(batch.not_found),
        not_found_words=batch.not_found,
        sources_stopped=batch.sources_stopped,
    )
    _prompt_import(result.path)


# ── Mode: review ──────────────────────────────────────────────────────────────

def _resolve_side_mode_language(
    args: SimpleNamespace,
    deck_name: str,
) -> tuple[str, str | None]:
    """
    Resolve the language pair for review and backlog mode.

    Both modes used to call `fetch_definitions()` and `build_package()`
    without a language at all, taking their `"en"` default, so
    `make review DECK="French"` fetched every French word from English
    sources and built cards tagged English. Nothing failed: the run
    reported success, the definitions were simply wrong, and the note GUIDs
    were computed with the wrong language, reintroducing the cross-language
    collision issue #14 closed.

    Unlike `_run_pipeline`, an unresolvable language is not fatal here.
    There the language selects the subtitle track, so a run cannot proceed
    without it. Review and backlog have no transcript, so the language only
    steers definitions, and a deck named "My Words" must keep working rather
    than start exiting non-zero.

    Args:
        args:      Parsed CLI arguments, read for --language and --def-lang.
        deck_name: Resolved deck name, used to infer the language.

    Returns:
        (language_code, def_language). def_language is None in native mode,
        which is also what a --def-lang equal to the target language means.
    """
    try:
        language_code = resolve_language_code(
            language_flag=getattr(args, "language", None),
            deck_name=deck_name,
        )
    except LanguageResolutionError:
        language_code = "en"
        _warn(
            f"Could not infer a language from deck '{deck_name}'. Defaulting to 'en'. "
            "Pass --language to fetch definitions in the deck's own language."
        )
    else:
        _ok(f"Target language: {language_code}")

    def_language = getattr(args, "def_lang", None)
    if def_language and def_language != language_code:
        _info(f"Definition language: {def_language} (translation mode)")
    else:
        def_language = None

    return language_code, def_language


def _run_review(args: SimpleNamespace, session: Session) -> None:
    deck_name = _select_deck(args.deck, session)
    language_code, def_language = _resolve_side_mode_language(args, deck_name)
    reset_circuit_breaker()

    to_add, to_skip = load_review_decisions()

    if not to_add and not to_skip:
        _warn("review.json is empty or has no decisions yet.")
        _info("Edit review.json and set each word's 'decision' to 'add' or 'skip'.")
        sys.exit(0)

    _info(f"Review decisions: {len(to_add)} to add / {len(to_skip)} to skip")

    if not to_add:
        _warn("No words marked 'add' in review.json. Nothing to build.")
        sys.exit(0)

    _info(f"Fetching definitions for {len(to_add)} approved words...")
    batch = definition_module.fetch_definitions(
        to_add,
        language=language_code,
        def_language=def_language,
        use_cache=not args.no_cache,
    )
    _ok(f"Definitions: {len(batch.found)} found / {len(batch.not_found)} not found")

    _info("Building Anki package from review decisions...")
    try:
        result = cards.build_package(
            video_id="review",
            deck_name=deck_name,
            found=batch.found,
            not_found=batch.not_found,
            language=language_code,
            not_found_examples=batch.not_found_examples,
            not_found_examples2=batch.not_found_examples2,
            not_found_synonyms=batch.not_found_synonyms,
            not_found_antonyms=batch.not_found_antonyms,
            not_found_ipa=batch.not_found_ipa,
            not_found_audio=batch.not_found_audio,
            progress=_info,
            def_language=def_language,
        )
    except ValueError as exc:
        _err(str(exc))
        sys.exit(1)

    log_package("review", result.path, deck_name, result.total_cards)

    _print_summary(
        video_id="review",
        deck_name=deck_name,
        apkg_path=result.path,
        card_count=result.standard_count,
        fallback_count=result.fallback_count,
        skipped_count=result.skipped_count,
        not_found_count=len(batch.not_found),
        not_found_words=batch.not_found,
        sources_stopped=batch.sources_stopped,
    )
    _prompt_import(result.path)


# ── Mode: backlog ─────────────────────────────────────────────────────────────

def _run_backlog(args: SimpleNamespace, session: Session) -> None:
    deck_name = _select_deck(args.deck, session)
    language_code, def_language = _resolve_side_mode_language(args, deck_name)
    reset_circuit_breaker()

    _info(f"Processing Anki backlog for deck: {deck_name}")

    try:
        check_result = process_backlog(deck_name)
    except AnkiNotRunningError:
        _err("Anki is not running. Start Anki with AnkiConnect and try again.")
        sys.exit(1)

    if not check_result.new and not check_result.queue:
        _warn("Backlog is empty or all words already in deck.")
        sys.exit(0)

    approved_lemmas: list[str] = []
    if check_result.queue:
        approved, _ = prompt_queue(check_result.queue)
        approved_lemmas = approved

    words_to_define = [m.lemma for m in check_result.new] + approved_lemmas

    if not words_to_define:
        _warn("No new words after deck check. Nothing to add.")
        sys.exit(0)

    _info(f"Fetching definitions for {len(words_to_define)} words...")
    batch = definition_module.fetch_definitions(
        words_to_define,
        language=language_code,
        def_language=def_language,
        use_cache=not args.no_cache,
    )
    _ok(f"Definitions: {len(batch.found)} found / {len(batch.not_found)} not found")

    _info("Building Anki package from backlog...")
    try:
        result = cards.build_package(
            video_id="backlog",
            deck_name=deck_name,
            found=batch.found,
            not_found=batch.not_found,
            language=language_code,
            not_found_examples=batch.not_found_examples,
            not_found_examples2=batch.not_found_examples2,
            not_found_synonyms=batch.not_found_synonyms,
            not_found_antonyms=batch.not_found_antonyms,
            not_found_ipa=batch.not_found_ipa,
            not_found_audio=batch.not_found_audio,
            progress=_info,
            def_language=def_language,
        )
    except ValueError as exc:
        _err(str(exc))
        sys.exit(1)

    log_package("backlog", result.path, deck_name, result.total_cards)

    _print_summary(
        video_id="backlog",
        deck_name=deck_name,
        apkg_path=result.path,
        card_count=result.standard_count,
        fallback_count=result.fallback_count,
        skipped_count=result.skipped_count,
        not_found_count=len(batch.not_found),
        not_found_words=batch.not_found,
        sources_stopped=batch.sources_stopped,
    )
    _prompt_import(result.path)


# ── Mode: setup wizard ────────────────────────────────────────────────────────

_ENV_PATH = Path(".env")
_ENV_EXAMPLE_PATH = Path(".env.example")


def _run_setup_wizard() -> None:
    """
    Guided .env setup for non-technical users (issue #9).

    Nothing this wizard touches is required to run the pipeline --
    dictionaryapi.dev works with zero configuration. This exists to make
    the one genuinely useful optional step, a free Merriam-Webster API key
    for better English definitions and examples, discoverable without
    hand-editing a file, and to correct the misconception (an older README
    table listed it as "Required") that it's needed at all.
    """
    from dotenv import get_key, set_key

    _rule()
    print(f"  {BOLD}Tango setup{RESET}")
    _rule()

    if not _ENV_PATH.exists():
        if _ENV_EXAMPLE_PATH.exists():
            _ENV_PATH.write_text(_ENV_EXAMPLE_PATH.read_text())
            _ok(".env created from .env.example.")
        else:
            _ENV_PATH.touch()
            _ok(".env created (empty).")
    else:
        _info(".env already exists. This only adds a Merriam-Webster key; "
              "everything else you've set stays untouched.")

    current_key = get_key(str(_ENV_PATH), "MW_API_KEY")
    if current_key:
        masked = f"...{current_key[-4:]}" if len(current_key) > 4 else "****"
        _ok(f"MW_API_KEY is already set ({masked}).")
        _info("Delete that line in .env and rerun this to change it.")
        return

    print()
    print("  Merriam-Webster gives better English definitions and example")
    print("  sentences than the free fallback. It's entirely optional --")
    print("  dictionaryapi.dev is used automatically with no key at all.")
    print()
    answer = _ask("  Add a free Merriam-Webster API key now? [y/N]: ", default="n")

    if answer != "y":
        _info("Skipping. dictionaryapi.dev will be used for definitions.")
        return

    print()
    print("  Register for a free key (1000 requests/day) at:")
    print(f"  {CYAN}https://dictionaryapi.com/register/index.htm{RESET}")
    print()
    key = _ask("  Paste your Merriam-Webster API key: ")

    if not key or any(ch.isspace() for ch in key):
        _err("That doesn't look like a valid key (empty, or contains whitespace).")
        _info("Run 'make setup' again once you have it, or edit .env directly.")
        sys.exit(1)

    set_key(str(_ENV_PATH), "MW_API_KEY", key)
    _ok("MW_API_KEY saved to .env.")


# ── Argument parser ───────────────────────────────────────────────────────────

# ── Command line ──────────────────────────────────────────────────────────────
#
# Typer rather than argparse, and subcommands rather than mode flags. The old
# surface put every mode behind a boolean flag on one parser, so `--help`
# listed sixteen options with no indication that `--review` and `--video-id`
# are different programs, and nothing stopped you passing both.
#
# Done now, immediately before v0.8.0 publishes this interface, because a
# breaking CLI change costs nothing today and costs every installed user
# afterwards. The `_run_*` functions below are deliberately untouched: they
# take an args object and keep taking one, built here. They are the least
# covered paths in the project and one of them has already shipped a real bug
# (TASKS.md, review and backlog silently defaulting to English), so this
# change moves the interface and not the behaviour.

app = typer.Typer(
    name="tango",
    help="YouTube transcripts to Anki flashcard packages.",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    """
    Print the version and exit, before Typer parses anything else.

    `raise typer.Exit()` is the documented way out of a callback and gives
    exit code 0. It is what makes this work at all: without it the callback
    returns, no subcommand was named, and the group falls through to its
    own help.

    `is_eager` is belt and braces, not load-bearing. Click already
    processes a group's own options before dispatching, so dropping it
    changes nothing today -- measured, by dropping it and watching all five
    tests still pass. It is kept because that stops being true the moment a
    second eager option is added, and no test pins it.

    The string comes from `pipeline.__version__`, the single source of
    truth (CLAUDE.md 15). Reading it back from installed metadata instead
    is stale under an editable install, which reported 0.1.0 long after the
    file said otherwise.
    """
    if value:
        print(f"tango {__version__}")
        raise typer.Exit()


@app.callback()
def _app_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """YouTube transcripts to Anki flashcard packages."""

# Options shared by the three pipeline modes, defined once so they cannot
# drift apart the way the argparse versions could.
_DECK = typer.Option(None, "--deck", "-d", help='Target Anki deck, e.g. "Language::French". Prompts if omitted.')
_LANGUAGE = typer.Option(None, "--language", "-l", help="Transcript language as a BCP-47 code. Inferred from the deck name if omitted.")
_DEF_LANG = typer.Option(None, "--def-lang", help="Write definitions in this language instead of the transcript's. Needs a translation model.")
_VERBOSE = typer.Option(False, "--verbose", "-v", help="Debug logging.")


def _args(**kwargs) -> SimpleNamespace:
    """
    Build the object the `_run_*` functions expect.

    They were written against argparse's Namespace and read attributes off
    it. Handing them an equivalent object is what keeps this migration to
    the interface layer.
    """
    kwargs.setdefault("video_id", None)
    kwargs.setdefault("deck", None)
    kwargs.setdefault("language", None)
    kwargs.setdefault("def_lang", None)
    kwargs.setdefault("force", False)
    kwargs.setdefault("no_cache", False)
    kwargs.setdefault("verbose", False)
    return SimpleNamespace(**kwargs)


@app.command()
def run(
    video_id: str = typer.Argument(..., metavar="VIDEO_ID", help="YouTube video ID or URL."),
    deck: Optional[str] = _DECK,
    language: Optional[str] = _LANGUAGE,
    def_lang: Optional[str] = _DEF_LANG,
    force: bool = typer.Option(False, "--force", "-f", help="Process a video already recorded as done."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the definition cache and refetch."),
    verbose: bool = _VERBOSE,
) -> None:
    """Turn one YouTube video into an Anki package."""
    _setup_logging(verbose)
    _run_pipeline(
        _args(video_id=video_id, deck=deck, language=language, def_lang=def_lang,
              force=force, no_cache=no_cache, verbose=verbose),
        Session(),
    )


@app.command()
def review(
    deck: Optional[str] = _DECK,
    language: Optional[str] = _LANGUAGE,
    def_lang: Optional[str] = _DEF_LANG,
    verbose: bool = _VERBOSE,
) -> None:
    """Process the words deferred to review.json."""
    _setup_logging(verbose)
    _run_review(_args(deck=deck, language=language, def_lang=def_lang, verbose=verbose), Session())


@app.command()
def backlog(
    deck: Optional[str] = _DECK,
    language: Optional[str] = _LANGUAGE,
    def_lang: Optional[str] = _DEF_LANG,
    verbose: bool = _VERBOSE,
) -> None:
    """Process the words queued in SQLite while Anki was unavailable."""
    _setup_logging(verbose)
    _run_backlog(_args(deck=deck, language=language, def_lang=def_lang, verbose=verbose), Session())


@app.command()
def languages() -> None:
    """List the supported languages and what each one can actually do."""
    from pipeline.language import capability_report, language_caveat

    rows = capability_report()
    usable = [r for r in rows if r[2]["cards"]]
    name_only = [r for r in rows if not r[2]["cards"]]

    print()
    print(f"  {len(usable)} languages can produce cards.")
    print("  A tick means the resource exists; a blank means the field it "
          "fills stays empty.")
    print()
    print(f"    {'code':<8} {'language':<22} {'words':<6} {'synonyms':<9} "
          f"{'pos':<5} {'fillers':<7}")
    print(f"    {'-' * 8} {'-' * 22} {'-' * 6} {'-' * 9} {'-' * 5} {'-' * 7}")

    def mark(on: bool) -> str:
        return "yes" if on else ""

    for name, code, cap in usable:
        print(f"    {code:<8} {name:<22} {mark(cap['cards']):<6} "
              f"{mark(cap['wordnet']):<9} {mark(cap['pos_labels']):<5} "
              f"{mark(cap['filler_sounds']):<7}")
        caveat = language_caveat(code)
        if caveat:
            print(f"             {caveat}")

    if name_only:
        print()
        print(f"  {len(name_only)} more are recognised by name but have no "
              f"spaCy model, so they")
        print("  cannot produce cards. Naming one in a deck fails at run time:")
        print()
        wrapped = ", ".join(f"{code}" for _n, code, _c in name_only)
        for line in _wrap_words(wrapped.split(", "), width=64):
            print(f"    {line}")
    print()


@app.command()
def doctor() -> None:
    """Report what is installed, what is missing, and the command that fixes it."""
    raise typer.Exit(_run_doctor())


@app.command()
def setup() -> None:
    """Guided .env setup for an optional Merriam-Webster API key."""
    _run_setup_wizard()


@app.command("install-model")
def install_model(
    language: str = typer.Argument(..., metavar="LANG", help="Language code, e.g. de."),
) -> None:
    """Download the spaCy model for one language."""
    raise typer.Exit(_run_install_model(language))


@app.command("install-translation")
def install_translation(
    pair: str = typer.Argument(..., metavar="FROM:TO", help="Language pair, e.g. de:en."),
) -> None:
    """Install translation models for one language pair."""
    raise typer.Exit(_run_install_translation(pair))


@app.command("build-dictionary")
def build_dictionary(
    language: str = typer.Argument(..., metavar="LANG", help="Language code, e.g. fr."),
) -> None:
    """Download and index the offline Wiktionary dictionary for one language."""
    _run_build_dictionary(language)


@app.command()
def uninstall(
    yes: bool = typer.Option(False, "--yes", "-y", help="Delete without asking. For scripts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted and stop."),
) -> None:
    """Report and remove the indexes, caches and packages Tango has written."""
    raise typer.Exit(_run_uninstall(assume_yes=yes, dry_run=dry_run))


@app.command("build-antonyms")
def build_antonyms() -> None:
    """Download and index ConceptNet's antonyms, for every language at once."""
    _run_build_antonyms()


# ── Mode: uninstall ───────────────────────────────────────────────────────────
#
# v0.8.2. `pip uninstall tango-anki` removes about 130 KB of Python and
# leaves everything that actually takes space: the dictionary indexes are
# 1.1 GB on the machine this was written on. That is defensible, since pip
# only owns what it installed, and it is still a surprise. Nothing else knows
# those files exist, so nothing else can offer to remove them.


def _path_size(path: Path) -> int:
    """Bytes used by a file, or by a directory tree. 0 if it is absent."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            # A file that vanishes mid-walk is not worth failing over.
            continue
    return total


def _human(size: int) -> str:
    """Render a byte count the way a person would say it."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _data_locations() -> list[tuple[str, Path, str]]:
    """
    Everything this tool writes outside its own package, largest first.

    Deliberately built from `config`, not from hardcoded names, so a user who
    redirected DICT_DIR or DB_PATH gets their own paths reported rather than
    the defaults, and nothing is missed or wrongly offered for deletion.
    """
    from pipeline import antonyms as antonyms_module
    from pipeline.config import DB_PATH, DICT_DIR, MEDIA_DIR, OUTPUT_DIR

    entries = [
        ("Dictionary and antonym indexes", DICT_DIR,
         "rebuild with 'tango build-dictionary <lang>' and 'tango build-antonyms'"),
        ("Pronunciation audio cache", MEDIA_DIR,
         "re-downloaded on demand"),
        ("Definition cache and run history", DB_PATH,
         "expensive to rebuild: it is every definition ever fetched"),
        ("Generated .apkg packages", OUTPUT_DIR,
         "your decks, if you have not imported them yet"),
    ]
    # The antonym index lives inside DICT_DIR, so it is already counted.
    _ = antonyms_module
    return [(label, path, note) for label, path, note in entries]


def _run_uninstall(assume_yes: bool, dry_run: bool) -> int:
    """
    Report, and optionally delete, the data this tool has written.

    Never touches the package, the source tree, or `.env`. Never deletes
    without either a confirmation typed at a terminal or an explicit
    `--yes`, because two of these four locations hold work the user cannot
    get back cheaply: the definition cache is every definition ever fetched,
    and OUTPUT_DIR may hold decks not yet imported.

    Returns:
        Process exit code.
    """
    found = [(label, path, note, _path_size(path))
             for label, path, note in _data_locations()]
    found = [row for row in found if row[3] > 0]

    if not found:
        _ok("No Tango data found. Nothing to remove.")
        _info("The package itself: pip uninstall tango-anki")
        return 0

    found.sort(key=lambda row: -row[3])
    total = sum(row[3] for row in found)

    _rule()
    print("  Tango data on this machine")
    _rule()
    for label, path, note, size in found:
        print(f"  {_human(size):>9}  {label}")
        print(f"             {path}")
        print(f"             {note}")
    _rule()
    print(f"  {_human(total):>9}  total")
    _rule()

    if dry_run:
        _info("Dry run, nothing deleted. Drop --dry-run to remove it.")
        return 0

    if not assume_yes:
        if not sys.stdin.isatty():
            _info("Nothing deleted. Re-run with --yes to delete without asking.")
            return 0
        _warn("This cannot be undone.")
        if _ask(f"Delete all {_human(total)}? [y/N]: ", default="n") not in ("y", "yes"):
            _info("Nothing deleted.")
            return 0

    removed = 0
    for label, path, _note, size in found:
        try:
            if path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
            removed += size
            _ok(f"Removed {label} ({_human(size)})")
        except OSError as exc:
            _err(f"Could not remove {path}: {exc}")

    _ok(f"Freed {_human(removed)}.")
    _info("The package itself is separate: pip uninstall tango-anki")
    return 0


# ── Mode: doctor ──────────────────────────────────────────────────────────────

def _report_torch_build() -> int:
    """
    Report a CUDA torch build that this machine cannot use.

    Returns:
        1 if the install is wasting space and can be shrunk, else 0.

    torch arrives through argostranslate -> stanza -> `torch>=1.3.0`, a
    constraint that names no variant, so pip takes the default PyPI wheel.
    Since torch 2.x that wheel bundles CUDA and pulls in nvidia-* and triton:
    4.5 GB measured here, 76% of the virtualenv, none of it usable without an
    NVIDIA card and a current driver.

    Worth reporting rather than assuming, because the waste is invisible from
    anything the pipeline does. It runs correctly either way, just four
    gigabytes larger, and on the machine this was found on
    `torch.cuda.is_available()` was False the whole time because the driver
    was too old.
    """
    try:
        import torch
    except ImportError:
        return 0  # Not installed at all, which is the smallest install there is.

    build = getattr(torch.version, "cuda", None)
    if not build:
        print("    torch          CPU build, which is the small one")
        return 0

    try:
        usable = torch.cuda.is_available()
    except Exception:
        usable = False

    if usable:
        print(f"    torch          CUDA {build} build, GPU available and in use")
        return 0

    print(f"    {YELLOW}torch          CUDA {build} build, but no usable GPU on this machine{RESET}")
    print("                   That costs roughly 4.5 GB in nvidia and triton")
    print("                   packages nothing can call. The CPU build is 733 MB.")
    # Spelled "python -m pip", not "pip", and with this interpreter's own
    # path. A virtualenv built by `make venv` has no pip script in bin/, only
    # python, so the bare form fails at the first line for exactly the people
    # most likely to run this.
    #
    # --no-deps is required rather than tidy: the CPU index is flat and does
    # not carry torch's other dependencies, so a full resolve against it
    # fails outright. Those packages are already installed and unaffected.
    #
    # The orphans need their own command because pip never removes them.
    # Replacing torch alone leaves 3.4 GB of nvidia and triton in place with
    # nothing able to call it, which is the worst of both.
    print(f"    -> {sys.executable} -m pip install --no-deps --force-reinstall \\")
    print("         --index-url https://download.pytorch.org/whl/cpu torch")
    print("       then remove the orphans, which pip does not do on its own:")
    print(f"       {sys.executable} -m pip uninstall -y triton {_nvidia_packages()}")
    return 1


def _nvidia_packages() -> str:
    """Space-separated names of every installed nvidia-* distribution."""
    from importlib.metadata import distributions

    names = sorted(
        d.metadata["Name"]
        for d in distributions()
        if (d.metadata["Name"] or "").lower().startswith("nvidia")
    )
    return " ".join(names)


def _run_doctor() -> int:
    """
    Report what this installation has and what it is missing.

    Written because nearly every failure investigated in this project turned
    out to be setup rather than logic, and none of it was visible: an
    ARGOS_PACKAGES_DIR pointing at an empty directory hid every translation
    model, a missing dictionary index silently produced cards with no
    definitions, and a spaCy model absent for the chosen language stopped a
    run before it began. Each printed either nothing or something that read
    like a different problem.

    Every missing item is reported with the command that fixes it.

    Returns:
        0 when nothing is missing, 1 when something optional is absent, so
        the exit code is usable in a setup script.
    """
    from pipeline.config import DICT_DIR, DB_PATH, MW_API_KEY, PROJECT_ROOT
    from pipeline.language import SPACY_MODELS

    missing = 0
    print()
    print("  Tango environment")
    print(f"    project root   {PROJECT_ROOT}")
    print(f"    database       {DB_PATH}  {'(exists)' if DB_PATH.exists() else '(will be created)'}")

    # A setting nothing reads is the quietest failure there is: it looks
    # applied and does nothing. Reported here because doctor is where
    # someone goes when the tool is not behaving as they configured it.
    stray = unknown_env_keys()
    if stray:
        print(f"    .env           {len(stray)} setting(s) that nothing reads: "
              f"{', '.join(stray)}")
        print(f"                   these have no effect. See .env.example for "
              f"the names that do.")
    print()

    # ── spaCy models: without one, a language cannot be processed at all ──
    print("  spaCy models (vocabulary extraction)")
    try:
        import spacy.util
        installed = set(spacy.util.get_installed_models())
    except Exception:
        installed = set()
    present = sorted(c for c, m in SPACY_MODELS.items() if m in installed)
    print(f"    installed for  {', '.join(present) if present else 'none'}")
    if not present:
        missing += 1
        print("    -> tango install-model <code>")
    print()

    # ── Dictionary indexes: the only source of non-English definitions ──
    print("  Offline dictionaries (native-language definitions)")
    built = sorted(p.stem.replace("wiktionary_", "") for p in DICT_DIR.glob("wiktionary_*.sqlite")) \
        if DICT_DIR.exists() else []
    if built:
        for code in built:
            size = (DICT_DIR / f"wiktionary_{code}.sqlite").stat().st_size / 1e6
            print(f"    {code:<6} {size:>7.0f} MB")
    else:
        print("    none")
    for code in present:
        if code not in built and code != "en":
            missing += 1
            print(f"    {code:<6} missing  -> make dictionary LANGUAGE={code}")
    print()

    # ── Antonym index: optional everywhere, and absent is a normal state ──
    print("  Offline antonyms (optional, ADR-010)")
    from pipeline import antonyms as antonym_index

    if antonym_index.is_available():
        size = antonym_index.index_path().stat().st_size / 1e6
        print(f"    built    {size:>5.1f} MB, every supported language")
    else:
        # Not counted as missing. A run without it produces the same cards
        # it produced before the index existed, so this is a suggestion.
        print("    absent   -> make antonyms")
        print("             Antonyms are the weakest card field: 19.7% on a")
        print("             real French deck. This takes it to 34.8%.")
    print()

    # ── Translation: only needed for --def-lang ──
    print("  Translation models (only needed for --def-lang)")
    try:
        from pipeline.translation import check_packages_dir
        from argostranslate import package as argos_pkg

        note = check_packages_dir()
        if note:
            missing += 1
            print(f"    WARNING {note}")
        pairs = [(p.from_code, p.to_code) for p in argos_pkg.get_installed_packages()]
        print(f"    installed      {', '.join(f'{a}->{b}' for a, b in pairs) if pairs else 'none'}")
        if not pairs:
            print("    -> make translate-model LANGUAGE=de DEF_LANG=en")
    except ImportError:
        print("    argostranslate not installed (optional)")
        print("    -> pip install -e '.[translation]'")
    missing += _report_torch_build()
    print()

    # ── Optional credentials and services ──
    print("  Optional")
    print(f"    MW_API_KEY     {'set' if MW_API_KEY else 'not set (English definitions fall back)'}")
    try:
        from pipeline.deck import is_anki_running
        print(f"    AnkiConnect    {'reachable' if is_anki_running() else 'not reachable (words go to the backlog)'}")
    except Exception:
        print("    AnkiConnect    could not be checked")
    print()

    if missing:
        print(f"  {missing} item(s) missing. Each is optional -- the pipeline runs without them,")
        print("  but the cards it produces will be thinner. Commands are shown above.")
    else:
        print("  Everything checked is present.")
    print()
    return 1 if missing else 0


# ── Mode: install a spaCy model ───────────────────────────────────────────────

def _run_install_model(language: str) -> int:
    """
    Download the spaCy model for one language.

    The CLI equivalent of `make spacy-model SPACY_LANG=<code>`, so that
    everything the Makefile does is reachable without make.

    Args:
        language: BCP-47 code, e.g. "de".

    Returns:
        Process exit code.
    """
    from pipeline.language import SpacyModelUnavailableError, get_spacy_model

    try:
        model = get_spacy_model(language)
    except (SpacyModelUnavailableError, KeyError):
        _err(f"No spaCy model is mapped for '{language}'.")
        _info("Run 'tango languages' to see supported codes.")
        return 1

    _info(f"Downloading spaCy model: {model}")
    result = subprocess.run([sys.executable, "-m", "spacy", "download", model])
    if result.returncode == 0:
        _ok(f"Installed {model}.")
    else:
        _err(f"spaCy download failed for {model}.")
        _info("Usually the network, or a pip that cannot reach GitHub, which "
              "is where spaCy hosts its models.")
        _info(f"Retry, or install it by hand: "
              f"{sys.executable} -m spacy download {model}")
    return result.returncode


# ── Mode: install a translation pair ──────────────────────────────────────────

def _run_install_translation(pair: str) -> int:
    """
    Install the translation models for one language pair.

    Args:
        pair: "from:to", e.g. "de:en".

    Returns:
        Process exit code.
    """
    if ":" not in pair:
        _err(f"Expected FROM:TO, for example de:en, got '{pair}'.")
        return 1
    from_code, to_code = (part.strip() for part in pair.split(":", 1))

    try:
        from pipeline.translation import install_translation
    except ImportError:
        _err("argostranslate is not installed.")
        _info("Install it with: pip install -e '.[translation]'")
        return 1

    _info(f"Installing translation for {from_code} -> {to_code}...")
    if install_translation(from_code, to_code):
        _ok(f"Translation ready: {from_code} -> {to_code}")
        return 0
    _err(f"Could not install translation for {from_code} -> {to_code}.")
    _info("argostranslate publishes no model for every pair. It pivots "
          "through English, so try the two halves separately:")
    _info(f"  tango install-translation {from_code}:en")
    _info(f"  tango install-translation en:{to_code}")
    return 1


# ── Mode: build dictionary ────────────────────────────────────────────────────

def _run_build_dictionary(language: str) -> None:
    """
    Build one language's offline Wiktionary index.

    Separated from the pipeline entirely: this is a one-time setup step
    measured in minutes, not something a normal run should ever trigger.
    """
    from pipeline import wiktdata

    language = language.strip().lower()

    reason = wiktdata.is_discouraged(language)
    if reason:
        _warn(f"Building a dictionary for '{language}' is not recommended.")
        for line in reason.splitlines():
            _info(line)
        try:
            answer = _ask("  Build it anyway? [y/N]: ", default="n")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            _info("Skipped.")
            return

    if wiktdata.is_available(language):
        _warn(f"A dictionary index for '{language}' already exists.")
        _info(f"Rebuilding it. Delete {wiktdata.index_path(language)} to skip.")

    _info(f"Building offline dictionary for '{language}'.")
    _info("This downloads several hundred MB once, then works offline.")
    try:
        count = wiktdata.build_index(language, progress=_info)
    except wiktdata.DictionaryError as exc:
        _err(str(exc))
        sys.exit(1)

    _ok(f"Indexed {count:,} entries for '{language}'.")
    _info(f"Stored at {wiktdata.index_path(language)}")
    _info("Runs in this language will now include real native-language definitions.")


def _run_build_antonyms() -> None:
    """
    Build the offline antonym index.

    One index for every language, unlike the per-language dictionaries, so
    this takes no argument. Nothing in the pipeline requires it: without it
    the antonym field is filled exactly as it is today.
    """
    from pipeline import antonyms

    if antonyms.is_available():
        _warn("An antonym index already exists.")
        _info(f"Rebuilding it. Delete {antonyms.index_path()} to skip.")

    _info("Building the offline antonym index.")
    _info("Streams a 498 MB download without storing it; the index is about 4 MB.")
    try:
        count = antonyms.build_index(progress=_info)
    except antonyms.AntonymError as exc:
        _err(str(exc))
        sys.exit(1)

    _ok(f"Indexed antonyms for {count:,} words.")
    _info(f"Stored at {antonyms.index_path()}")
    _info("Runs will now fill the antonym field from it when nothing else can.")


# ── Entry point ───────────────────────────────────────────────────────────────

# The environment variable that turns the safety net off. A developer
# chasing a bug wants the traceback, and deleting it to get one is a poor
# trade.
#
# Read as a literal at the call site rather than through this constant. A
# test scans the package for `getenv("NAME")` to keep config.KNOWN_ENV_KEYS
# honest, and an indirection makes the key invisible to it, which is how
# this one first went undeclared.
_DEBUG_ENV = "TANGO_DEBUG"


def main() -> None:
    """
    Console entry point, named in pyproject.toml's [project.scripts].

    Wraps the app in the last line of defence. Every *expected* failure is
    already a typed exception caught by the command that raised it, with a
    message naming the fix, per CLAUDE.md 4.4. This catches what is left:
    the genuine bug, which by definition nobody wrote a message for.

    A traceback is the correct thing to record and the wrong thing to show.
    It tells the user nothing they can act on and looks like the tool
    breaking rather than a case it does not handle, so it goes to the debug
    log while the terminal gets a sentence and somewhere to report it. Set
    TANGO_DEBUG=1 to get the traceback on screen instead.

    Ctrl-C is deliberately not handled here. Click catches KeyboardInterrupt
    itself and exits 130, the shell convention for SIGINT, before anything
    here sees it. Measured on click 8.4.2: a handler added at this level was
    never entered. A branch that cannot run is worse than no branch, because
    it reads as a guarantee.
    """
    try:
        app()
    except TangoError as exc:
        # A failure this project raises on purpose, which means someone
        # wrote a message for it saying what to do. Print that and stop.
        # Calling it a bug would send the user to open an issue about their
        # own disk permissions or a closed Anki.
        #
        # Most of these are already caught and reported by the command that
        # raised them; this catches the ones raised below a call site that
        # did not expect them.
        _err(str(exc))
        sys.exit(1)
    # `except Exception`, never `except BaseException`. SystemExit and
    # KeyboardInterrupt are BaseExceptions and must pass straight through:
    # the first carries Typer's own exit codes, including every typed
    # failure that already printed its message, and the second is Ctrl-C.
    # An explicit `except SystemExit: raise` was here and was removed as
    # redundant, since `except Exception` never sees one.
    except Exception as exc:
        # debug, not exception: at the default WARNING level this records
        # nothing, so the traceback does not appear above the message that
        # replaced it. Under --verbose it is emitted in full.
        logging.getLogger(__name__).debug(
            "Unhandled %s", type(exc).__name__, exc_info=True)
        if os.getenv("TANGO_DEBUG"):
            raise
        _err(f"Unexpected {type(exc).__name__}: {exc}")
        _info("This is a bug in Tango, not something you did wrong.")
        _info(f"Re-run with {_DEBUG_ENV}=1 to see the full traceback.")
        _info("Please report it: "
              "https://github.com/AlphaNerdFx/Tango/issues")
        sys.exit(1)


if __name__ == "__main__":
    main()
