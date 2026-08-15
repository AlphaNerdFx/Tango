#!/usr/bin/env python3
"""
Sweep language pairs and report card quality for each.

    python scripts/coverage_matrix.py                 # every pair it can run
    python scripts/coverage_matrix.py --langs de,fr   # just these transcript languages
    python scripts/coverage_matrix.py --native-only   # skip cross-language pairs
    python scripts/coverage_matrix.py --dry-run       # show the plan, run nothing

Why a script rather than a checklist: every language combination has been
verified by hand so far, one at a time, and each pass took long enough that
only French and German ever got done. Bugs that only appear in one
combination -- the cross-language native fallback, the review-mode language
default -- survived precisely because nothing swept the space.

It does NOT import into Anki. Each run generates a .apkg and the card fields
are read straight out of it, so a sweep leaves the collection untouched and
needs no deck management. That also means it does not exercise the import
path; `scripts/verify-release.sh` covers that for one pair at a time.

Each combination needs a real video in that transcript language. VIDEOS below
holds the ones already verified to work; add to it rather than passing ids on
the command line, so the next sweep uses the same corpus and the numbers stay
comparable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# One known-good video per transcript language. These have all been run at
# least once; a video whose transcript is missing or auto-generated garbage
# makes a language look broken when the pipeline is fine.
VIDEOS: dict[str, str] = {
    "de": "JbHUxfOQ2-U",
    "fr": "2yHn8uc5_-4",
    "en": "hOJhDBhBErI",
    "ru": "c9ghnkHZLwo",
}

# Fields read back out of each generated package, in card-model order.
FIELDS = [
    ("Word", 0),
    ("Class", 1),
    ("Definition", 2),
    ("1st Example", 3),
    ("2nd Example", 4),
    ("Video Example", 5),
    ("Synonyms", 6),
    ("Antonyms", 7),
    # ADR-009 phase 1. Only the offline index supplies these, so they are 0%
    # for any language without one -- English included, which has no index at
    # all. A 0% IPA row for de/fr/ru means something is wrong; a 0% row for
    # en is the expected result. Audio is far more variable than IPA: the
    # indexes carry a recording on 95.0% of German rows, 12.1% of French and
    # 4.4% of Russian (ARCHITECTURE.md 8.30), so a low audio number is not by
    # itself a defect.
    ("IPA", 10),
    ("Audio", 11),
]

# Source field, used to tell a real cross-language definition from one that
# quietly fell back to the transcript language. Without this a --def-lang row
# that translated nothing looks exactly like a native row.
SOURCE_IDX = 9
NATIVE_FALLBACK_SOURCE = "wiktionary-native"

PLACEHOLDER = "No definition found"


def available_languages() -> list[str]:
    """Transcript languages we have both a video and a spaCy model for."""
    from pipeline.language import SPACY_MODELS

    return [code for code in VIDEOS if code in SPACY_MODELS]


def indexed_languages() -> set[str]:
    """Languages with an offline Wiktionary index built on this machine."""
    from pipeline.config import DICT_DIR

    if not DICT_DIR.exists():
        return set()
    return {
        m.group(1)
        for p in DICT_DIR.glob("wiktionary_*.sqlite")
        if (m := re.match(r"wiktionary_(.+)\.sqlite", p.name))
    }


def anki_is_reachable() -> bool:
    """
    True if AnkiConnect answers.

    The sweep never imports, but every run still goes through the deck
    duplicate check, which needs AnkiConnect. With Anki closed the pipeline
    writes the words to the backlog and exits 0 before building a package,
    so all 16 rows fail identically with "no package produced" -- a whole
    sweep spent to learn that an application was not open.
    """
    from pipeline.deck import is_anki_running

    return is_anki_running()


def run_pair(
    transcript: str,
    definition: str | None,
    timeout: int,
    no_cache: bool = False,
) -> dict:
    """
    Run one combination and return its measured coverage.

    Args:
        transcript: Transcript language code.
        definition: Definition language, or None for native.
        timeout:    Seconds before the run is abandoned.
        no_cache:   Pass --no-cache, so the run reports what the pipeline
                    produces now rather than what it produced when the rows
                    were cached.

    Returns:
        A result dict with per-field percentages, or an "error" key.
    """
    video = VIDEOS[transcript]
    deck = f"CoverageMatrix_{transcript}_{definition or 'native'}"
    cmd = [
        sys.executable, "-u", "-m", "pipeline",
        f"--video-id={video}",
        f"--deck={deck}",
        f"--language={transcript}",
        "--force",
    ]
    if no_cache:
        cmd.append("--no-cache")
    if definition and definition != transcript:
        cmd.append(f"--def-lang={definition}")

    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, env=env, input="n\nn\n",
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}

    output = proc.stdout + proc.stderr
    # The commonest cross-language failure leaves no trace in the card data:
    # when no model exists for the pair, translate_word returns None and
    # fetch_definition resets target_language to the transcript language, so
    # the run becomes a native one and every definition records the ordinary
    # "wiktionary" source. Indistinguishable from a native run afterwards --
    # de->fr and de->native produced byte-identical source counts. The only
    # evidence is the warning emitted at the time.
    no_model = bool(re.search(r"No translation model for|did not load within", output))

    match = re.search(r"(/\S+\.apkg)", proc.stdout)
    if not match:
        tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:] or ["no output"]
        return {"error": f"no package produced ({tail[0][:70]})"}

    result = measure_package(Path(match.group(1)))
    result["seconds"] = round(time.time() - started)
    result["translated"] = not no_model if definition else None
    return result


def measure_package(apkg: Path) -> dict:
    """Read a generated .apkg and count how many cards carry each field."""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(apkg) as zf:
            name = "collection.anki21" if "collection.anki21" in zf.namelist() else "collection.anki2"
            zf.extract(name, tmp)
        con = sqlite3.connect(Path(tmp) / name)
        rows = [r[0].split("\x1f") for r in con.execute("select flds from notes")]
        con.close()

    total = len(rows)
    if not total:
        return {"error": "package contains no notes"}

    counts = {}
    for label, idx in FIELDS:
        filled = sum(
            1 for r in rows
            if len(r) > idx and r[idx].strip() and r[idx].strip() != PLACEHOLDER
        )
        counts[label] = round(100 * filled / total)
    sources: dict[str, int] = {}
    for r in rows:
        if len(r) > SOURCE_IDX:
            sources[r[SOURCE_IDX].strip()] = sources.get(r[SOURCE_IDX].strip(), 0) + 1
    counts["cards"] = total
    counts["sources"] = sources
    # Share of definitions that came back in the transcript language because
    # the target-language lookup found nothing. 0 on a native run by
    # definition; high on a cross-language run means translation is not
    # actually happening -- usually a missing model for that pair.
    counts["native_fallback"] = round(100 * sources.get(NATIVE_FALLBACK_SOURCE, 0) / total)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langs", help="comma-separated transcript languages")
    parser.add_argument("--native-only", action="store_true",
                        help="skip cross-language pairs")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and exit")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="seconds per run (default 1800)")
    parser.add_argument("--out", help="write results as JSON here")
    parser.add_argument("--cache", action="store_true",
                        help="allow the definition cache (default is --no-cache, "
                             "since a warm cache reports what the pipeline used "
                             "to produce, not what it produces now)")
    parser.add_argument("--skip-anki-check", action="store_true",
                        help="run even if AnkiConnect is unreachable")
    args = parser.parse_args()

    langs = args.langs.split(",") if args.langs else available_languages()
    unknown = [c for c in langs if c not in VIDEOS]
    if unknown:
        print(f"No sample video for: {', '.join(unknown)}. Add one to VIDEOS.")
        return 1

    indexed = indexed_languages()
    combos: list[tuple[str, str | None]] = [(c, None) for c in langs]
    if not args.native_only:
        combos += [(a, b) for a in langs for b in langs if a != b]

    print(f"{len(combos)} combinations across {len(langs)}: {', '.join(langs)}")
    print("'xlang'    = did translation actually run? NO MODEL means the pair "
          "has no\n             argostranslate model, so the run silently "
          "produced native definitions.")
    print("'fellback' = share of definitions that came back in the transcript "
          "language\n             because the target-language lookup found "
          "nothing (0% on native rows).")
    print(f"definition cache: {'ON (--cache)' if args.cache else 'OFF'}")
    print(f"offline index built for: {', '.join(sorted(indexed)) or 'none'}")
    missing = [c for c in langs if c not in indexed and c != "en"]
    if missing:
        print(f"no index for {', '.join(missing)} — expect near-zero definitions "
              f"there (make dictionary LANGUAGE=<code>)")
    print()

    if args.dry_run:
        for t, d in combos:
            print(f"  {t} -> {d or 'native'}   video {VIDEOS[t]}")
        return 0

    # Up front, not per row. A closed Anki fails every combination the same
    # way and only after each has spent its full runtime.
    if not args.skip_anki_check and not anki_is_reachable():
        print("AnkiConnect is unreachable, so every run would fail the deck check\n"
              "and exit before building a package. The sweep never imports, but\n"
              "the deck check still needs Anki.\n"
              "  Start Anki, or pass --skip-anki-check to run anyway.")
        return 1

    results: dict[str, dict] = {}
    header = (f"{'combination':<16}{'cards':>7}{'defs':>7}{'fellback':>9}{'class':>7}"
              f"{'ex1':>6}{'ex2':>6}{'video':>7}{'syn':>6}{'ant':>6}{'xlang':>8}{'secs':>7}")
    print(header)
    print("-" * len(header))

    for transcript, defn in combos:
        label = f"{transcript} -> {defn or 'native'}"
        res = run_pair(transcript, defn, args.timeout, no_cache=not args.cache)
        results[label] = res
        if "error" in res:
            print(f"{label:<16}  ERROR: {res['error']}")
            continue
        print(
            f"{label:<16}{res['cards']:>7}{res['Definition']:>6}%"
            f"{res['native_fallback']:>8}%{res['Class']:>6}%"
            f"{res['1st Example']:>5}%{res['2nd Example']:>5}%{res['Video Example']:>6}%"
            f"{res['Synonyms']:>5}%{res['Antonyms']:>5}%"
            f"{('-' if res['translated'] is None else ('yes' if res['translated'] else 'NO MODEL')):>8}"
            f"{res['seconds']:>7}"
        )

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
