"""
CLI entry point for the yt-anki-pipeline.

Usage:
    python -m pipeline --video-id VIDEO_ID --deck "Deck::Name" [--verbose]
    python -m pipeline --review --deck "Deck::Name"
    python -m pipeline --process-backlog --deck "Deck::Name"

Or via Makefile:
    make run VIDEO_ID=<id> DECK="<name>"
    make review DECK="<name>"
    make backlog DECK="<name>"

Modes:
    default         — full pipeline: transcript → NLP → deck check → definitions → .apkg
    --review        — process review.json decisions and build .apkg for approved words
    --process-backlog — process SQLite backlog when Anki was previously unavailable
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

from pipeline import (
    cards,
    deck as deck_module,
    definition as definition_module,
    nlp as nlp_module,
    state,
    transcript as transcript_module,
)
from pipeline.translation import reset_warning_state
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


def _is_wsl() -> bool:
    """
    Detect whether this process is running inside WSL (Windows Subsystem
    for Linux). Under WSL, paths under /mnt/<drive>/ need translation to
    Windows drive-letter paths before Windows-side AnkiConnect can resolve
    them — see _translate_wsl_path().
    """
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except FileNotFoundError:
        return False


def _translate_wsl_path(path: str) -> str:
    """
    Translate a WSL mount path (e.g. /mnt/c/Users/name/file.apkg) to its
    Windows equivalent (C:\\Users\\name\\file.apkg).

    No-op when not running under WSL, or when the path doesn't match the
    /mnt/<drive>/ pattern (already a native path, or a WSL-internal path
    with no Windows equivalent, e.g. under /home).

    Anki running natively on Windows under WSL2 is a common setup (see
    the WSL Setup wiki page), and AnkiConnect's importPackage action
    resolves the path on the Windows side — a Linux path is meaningless
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
    Uses the absolute path — AnkiConnect requires this. Under WSL, the
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
        _info("Import skipped — importing now could fork the notetype and")
        _info("separate new cards from your existing ones. Retry once Anki")
        _info("is reachable, or import manually after adding the fields.")
        return

    if added:
        _info(f"Added {len(added)} field(s) to the Anki notetype: {', '.join(added)}")
        _info("This is a schema change — Anki will ask for a full sync next time.")

    try:
        import requests as req
        absolute_path = _translate_wsl_path(str(apkg_path.resolve()))
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


def _print_summary(
    video_id: str,
    deck_name: str,
    apkg_path: Path,
    card_count: int,
    fallback_count: int,
    skipped_count: int,
    not_found_count: int,
    not_found_words: Optional[list[str]] = None,
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
        print(f"  {YELLOW}No definition found for {not_found_count} word(s) — fallback cards created where possible.{RESET}")
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
    print(f"  Package:  {apkg_path}")
    _rule()


# ── Deck selection ────────────────────────────────────────────────────────────

def _select_deck(deck_arg: str | None, session: Session) -> str:
    """
    Resolve the deck name from --deck arg or interactive selection.

    If --deck is provided it is used directly — no prompt shown.
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
# roughly one ID in 64 starts with a hyphen, and those are the ones that
# break a naive `--video-id -abc` on the command line, since argparse reads
# the value as another option. The Makefile passes --video-id=<value> for
# that reason; this only has to recognise a well-formed ID.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# watch?v=, youtu.be/, /shorts/, /embed/, /live/ — the forms people paste.
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
    safety — the transcript fetch reports a bad ID perfectly well.

    Args:
        value: Raw --video-id argument.

    Returns:
        The extracted ID when the input was a URL, otherwise the input
        stripped of surrounding whitespace.
    """
    value = value.strip()

    match = _YOUTUBE_URL_RE.search(value)
    if match:
        return match.group(1)

    # A URL we could not read an ID out of — better to say so here than to
    # let the transcript API fail on a string that is obviously not an ID.
    if "youtu" in value.lower() or value.startswith(("http://", "https://")):
        raise ValueError(
            f"Could not find a video ID in '{value}'.\n"
            "  Expected something like https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  or just the 11-character ID on its own."
        )

    return value


# ── Mode: default pipeline ────────────────────────────────────────────────────

def _run_pipeline(args: argparse.Namespace, session: Session) -> None:
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
        def_language = None  # native mode — no translation needed

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

    # ── 2. Fetch transcript ───────────────────────────────────────────────────
    _info(f"Fetching transcript for: {video_id}")
    try:
        transcript = transcript_module.get_transcript(video_id, languages=[language_code])
        snippets   = transcript_module.get_snippets(transcript)
    except Exception as exc:
        _err(f"Transcript failed: {exc}")
        sys.exit(1)
    _ok(f"Transcript ready ({snippets['_snippet_count']} snippets, language: {snippets['_language_code']})")

    # ── 3. NLP ────────────────────────────────────────────────────────────────
    _info("Running spaCy NLP...")
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
    _ok(f"Vocabulary extracted: {len(vocabulary)} unique lemmas")

    # ── 4. Save vocabulary to SQLite ──────────────────────────────────────────
    save_vocabulary(video_id, vocabulary)

    # ── 5. Deck check ─────────────────────────────────────────────────────────
    _info(f"Checking deck: {deck_name}")
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
        f"{len(check_result.new)} new"
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
    batch = definition_module.fetch_definitions(
            words_to_define, snippets,
            language=language_code,
            def_language=def_language,
            parts_of_speech=parts_of_speech,
            use_cache=not args.no_cache,
        )
    _ok(
        f"Definitions: {len(batch.found)} found "
        f"({len(batch.from_cache)} cached) / {len(batch.not_found)} not found"
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
    )
    _prompt_import(result.path)


# ── Mode: review ──────────────────────────────────────────────────────────────

def _resolve_side_mode_language(
    args: argparse.Namespace,
    deck_name: str,
) -> tuple[str, str | None]:
    """
    Resolve the language pair for review and backlog mode.

    Both modes used to call `fetch_definitions()` and `build_package()`
    without a language at all, taking their `"en"` default, so
    `make review DECK="French"` fetched every French word from English
    sources and built cards tagged English. Nothing failed: the run
    reported success, the definitions were simply wrong, and the note GUIDs
    were computed with the wrong language — reintroducing the cross-language
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


def _run_review(args: argparse.Namespace, session: Session) -> None:
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
    )
    _prompt_import(result.path)


# ── Mode: backlog ─────────────────────────────────────────────────────────────

def _run_backlog(args: argparse.Namespace, session: Session) -> None:
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

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="YouTube transcript to Anki flashcard pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python -m pipeline --video-id LV_NoD2M54w --deck "Language::English"
  python -m pipeline --review --deck "Language::English"
  python -m pipeline --process-backlog --deck "Language::English"
  python -m pipeline --video-id LV_NoD2M54w --deck "Language::English" --verbose
  python -m pipeline --video-id LV_NoD2M54w --deck "Language::English" --force
  python -m pipeline --setup
        """,
    )

    # Mode flags — mutually exclusive
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--review",
        action="store_true",
        help="Process review.json decisions and build .apkg for approved words.",
    )
    mode.add_argument(
        "--process-backlog",
        action="store_true",
        dest="process_backlog",
        help="Process the Anki backlog (requires Anki to be running).",
    )

    # Required for default mode
    parser.add_argument(
        "--video-id",
        dest="video_id",
        metavar="VIDEO_ID",
        help="YouTube video ID or URL to process.",
    )

    # Common
    parser.add_argument(
        "--language",
        metavar="LANG_CODE",
        help=(
            "BCP-47 language code for subtitle selection (e.g. fr, de, ja). "
            "If omitted, inferred from deck name. "
            "Run 'python -m pipeline --list-languages' to see all supported codes."
        ),
    )
    parser.add_argument(
        "--def-lang",
        dest="def_lang",
        metavar="LANG_CODE",
        help=(
            "BCP-47 code for definition output language. "
            "Defaults to the transcript language (native definitions). "
            "Set to 'en' to get English definitions of non-English words via translation. "
            "Example: --language fr --def-lang en"
        ),
    )
    parser.add_argument(
        "--list-languages",
        action="store_true",
        dest="list_languages",
        help="Print all supported language names and their BCP-47 codes, then exit.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Guided .env setup for an optional Merriam-Webster API key, then exit. "
             "Nothing it configures is required to run the pipeline.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Report what is installed and what is missing, with the command "
             "to fix each, then exit. Start here when something is not working.",
    )
    parser.add_argument(
        "--install-model",
        metavar="LANG",
        dest="install_model",
        help="Download the spaCy model for a language, then exit. "
             "e.g. --install-model de",
    )
    parser.add_argument(
        "--install-translation",
        metavar="FROM:TO",
        dest="install_translation",
        help="Install translation models for a language pair, then exit. "
             "Needed only for --def-lang. e.g. --install-translation de:en",
    )
    parser.add_argument(
        "--build-dictionary",
        metavar="LANG",
        dest="build_dictionary",
        help="Download and index the offline Wiktionary dictionary for a "
             "language, then exit. Large one-time download (hundreds of MB) "
             "that gives non-English runs real native-language definitions, "
             "which no online source provides. e.g. --build-dictionary fr",
    )
    parser.add_argument(
        "--build-antonyms",
        action="store_true",
        dest="build_antonyms",
        help="Download and index ConceptNet's antonyms, then exit. One 498 MB "
             "download, streamed rather than stored, producing a 4.3 MB index "
             "that covers every supported language at once. Antonyms are the "
             "weakest card field; see ADR-010.",
    )
    parser.add_argument(
        "--deck",
        metavar="DECK_NAME",
        help='Target Anki deck. Supports sub-decks: "Language::English::Vocabulary". '
             "If omitted, an interactive selection prompt is shown.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging output.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess a video even if pipeline.db has it marked as already "
             "processed. Words already in the target deck are still skipped "
             "by the normal deck duplicate check.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Neither read nor write the definition cache. For measuring what "
             "the pipeline currently produces: cached rows hold assembled "
             "card fields, so a warm cache serves definitions chosen before "
             "whatever you are trying to measure. Much slower.",
    )

    return parser


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
        print("    -> python -m pipeline --install-model <code>")
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
        _info("Run 'python -m pipeline --list-languages' to see supported codes.")
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
        _err(f"Expected FROM:TO, for example de:en — got '{pair}'.")
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
    _info(f"  python -m pipeline --install-translation {from_code}:en")
    _info(f"  python -m pipeline --install-translation en:{to_code}")
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

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    _setup_logging(args.verbose)
    session = Session()

    # Standalone informational/setup modes exit before the --video-id
    # requirement below -- neither one processes a video.
    if args.setup:
        _run_setup_wizard()
        sys.exit(0)

    if args.doctor:
        sys.exit(_run_doctor())

    if args.install_model:
        sys.exit(_run_install_model(args.install_model))

    if args.install_translation:
        sys.exit(_run_install_translation(args.install_translation))

    if args.build_dictionary:
        _run_build_dictionary(args.build_dictionary)
        sys.exit(0)

    if args.build_antonyms:
        _run_build_antonyms()
        sys.exit(0)

    if args.list_languages:
        langs = list_supported_languages()
        print()
        print("  Supported languages:")
        print()
        for name, code in langs:
            print(f"    {code:<10} {name}")
        print()
        sys.exit(0)

    # Validate: default mode requires --video-id
    if not args.review and not args.process_backlog:
        if not args.video_id:
            _err("--video-id is required for the default pipeline mode.")
            _info("Run 'python -m pipeline --help' for usage.")
            sys.exit(1)

    # Dispatch
    if args.review:
        _run_review(args, session)
    elif args.process_backlog:
        _run_backlog(args, session)
    else:
        _run_pipeline(args, session)


if __name__ == "__main__":
    main()