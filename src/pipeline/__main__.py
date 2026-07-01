"""
__main__.py
-----------
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

def _prompt_import(apkg_path: Path) -> None:
    """
    Ask the user if they want to auto-import the .apkg into Anki.

    Requires Anki to be running with AnkiConnect.
    Uses the absolute path — AnkiConnect requires this.

    Constraint: only works when Anki is running on the same machine
    as the pipeline. If running on a remote server, this will fail
    gracefully and the user imports manually.
    """
    print()
    _rule()
    answer = input(
        f"  Import {apkg_path.name} into Anki now? [y/N]: "
    ).strip().lower()

    if answer != "y":
        _info(f"Skipped. Import manually: File → Import in Anki.")
        return

    try:
        import requests as req
        absolute_path = str(apkg_path.resolve())
        response = req.post(
            deck_module.ANKI_HOST,
            json={
                "action":  "importPackage",
                "version": 6,
                "params":  {"path": absolute_path},
            },
            timeout=deck_module.ANKI_TIMEOUT,
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

def _print_summary(
    video_id: str,
    deck_name: str,
    apkg_path: Path,
    card_count: int,
    fallback_count: int,
    skipped_count: int,
    not_found_count: int,
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
        sys.exit(1)

    print()
    _info("Select a deck:")
    for i, name in enumerate(decks, start=1):
        print(f"    {i}. {name}")
    print()

    while True:
        choice = input("  Enter number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(decks):
            selected = decks[int(choice) - 1]
            session.set_deck(selected)
            return selected
        print(f"  Please enter a number between 1 and {len(decks)}.")


# ── Mode: default pipeline ────────────────────────────────────────────────────

def _run_pipeline(args: argparse.Namespace, session: Session) -> None:
    video_id  = args.video_id
    deck_name = _select_deck(args.deck, session)

    # ── 1. Check not already processed ───────────────────────────────────────
    try:
        check_video_not_processed(video_id)
    except VideoAlreadyProcessedError as exc:
        _warn(str(exc))
        _warn("No new cards will be created. Use --force to reprocess (not yet implemented).")
        sys.exit(0)

    # ── 2. Fetch transcript ───────────────────────────────────────────────────
    _info(f"Fetching transcript for: {video_id}")
    try:
        transcript = transcript_module.get_transcript(video_id)
        snippets   = transcript_module.get_snippets(transcript)
    except Exception as exc:
        _err(f"Transcript failed: {exc}")
        sys.exit(1)
    _ok(f"Transcript ready ({snippets['_snippet_count']} snippets, language: {snippets['_language_code']})")

    # ── 3. NLP ────────────────────────────────────────────────────────────────
    _info("Running spaCy NLP...")
    try:
        vocabulary = nlp_module.process_transcript(snippets["_full_text"])
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
    batch = definition_module.fetch_definitions(words_to_define, snippets)
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
    )
    _prompt_import(result.path)


# ── Mode: review ──────────────────────────────────────────────────────────────

def _run_review(args: argparse.Namespace, session: Session) -> None:
    deck_name = _select_deck(args.deck, session)

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
    batch = definition_module.fetch_definitions(to_add)
    _ok(f"Definitions: {len(batch.found)} found / {len(batch.not_found)} not found")

    _info("Building Anki package from review decisions...")
    try:
        result = cards.build_package(
            video_id="review",
            deck_name=deck_name,
            found=batch.found,
            not_found=batch.not_found,
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
    )
    _prompt_import(result.path)


# ── Mode: backlog ─────────────────────────────────────────────────────────────

def _run_backlog(args: argparse.Namespace, session: Session) -> None:
    deck_name = _select_deck(args.deck, session)

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
    batch = definition_module.fetch_definitions(words_to_define)
    _ok(f"Definitions: {len(batch.found)} found / {len(batch.not_found)} not found")

    _info("Building Anki package from backlog...")
    try:
        result = cards.build_package(
            video_id="backlog",
            deck_name=deck_name,
            found=batch.found,
            not_found=batch.not_found,
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
    )
    _prompt_import(result.path)


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

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    _setup_logging(args.verbose)
    session = Session()

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