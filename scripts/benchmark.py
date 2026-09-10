#!/usr/bin/env python3
"""
Time what a user waits for, phase by phase, against a threshold each.

    python scripts/benchmark.py                # every stage
    python scripts/benchmark.py --stage nlp    # just one
    python scripts/benchmark.py --runs 7       # more repetitions

Companion to `scripts/measure_footprint.py`, which asks what a run costs in
memory. This asks what it costs in time, and it asks it the way a user
experiences it rather than the way a profiler does.

Method, and the two things that make the numbers honest.

**Each stage runs in a fresh interpreter.** Import cost is not overhead to
be subtracted here, it is the first thing a user waits for, and a benchmark
that imports once and then loops reports a number nobody experiences.

**The median of several runs, not the mean or the best.** A single run on a
laptop catches whatever else the machine was doing. The best-of is the
number a vendor quotes; the median is the number a user gets.

The thresholds are what a person tolerates before a command feels broken,
not what this machine happens to manage. They are deliberately loose: the
point is to catch a regression that makes something unusable, not to pin
today's hardware.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Seconds. A stage over its threshold is reported OVER and the run exits 1,
# so this is usable in CI.
THRESHOLDS = {
    "startup": 5.0,
    "help": 5.0,
    "nlp": 30.0,
    "cards": 10.0,
    "search": 5.0,
    "index": 10.0,
}

STAGES: dict[str, str] = {
    "startup": """
        # The floor under every single command. `tango --version` does the
        # least work the CLI can do, so whatever this costs is paid by
        # `run`, `doctor` and everything else before they begin.
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "pipeline", "--version"],
                       capture_output=True)
        report("tango --version, the floor under every command")
    """,
    "help": """
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "pipeline", "--help"],
                       capture_output=True)
        report("tango --help, what someone types first")
    """,
    "nlp": """
        from pipeline import nlp
        # A 10-minute video is roughly 1500 words.
        text = ("Der Hund lauft schnell durch den grossen Garten und "
                "bellt laut. ") * 150
        vocab = nlp.process_transcript(text, "de")
        report(f"spaCy loaded and {len(text.split())} words tokenised")
    """,
    "cards": """
        import tempfile
        from pathlib import Path
        from pipeline import cards
        from pipeline.definition import DefinitionResult
        cards.OUTPUT_DIR = Path(tempfile.mkdtemp())
        found = [DefinitionResult(f"wort{i}", "eine Definition", "Ein Satz.",
                                  "Noch einer.", None, ["syn"], ["ant"],
                                  "noun", "test")
                 for i in range(400)]
        cards.build_package("bench", "Benchmark", found, [], language="de")
        report("a 400-card package built and written")
    """,
    "search": """
        from pipeline import cards
        # The transcript search, which was 66% of a package build before it
        # was fixed. Both directions: every word present, and none.
        snippets = {float(i): {"end": i + 1.0,
                               "text": f"Satz {i} mit wort{i} und anderen."}
                    for i in range(300)}
        snippets["_full_text"] = "x"
        lines = cards._fold_snippets(snippets)
        for word in [f"wort{i}" for i in range(400)]:
            cards._find_in_snippets(word, snippets, None, lines=lines)
        for word in [f"fehlt{i}" for i in range(400)]:
            cards._find_in_snippets(word, snippets, None, lines=lines)
        report("800 transcript searches, half hits and half misses")
    """,
    "index": """
        from pipeline import wiktdata
        built = [c for c in ("de", "fr", "en", "ru") if wiktdata.is_available(c)]
        if not built:
            report("no index built, skipped", skipped=True)
        else:
            code = built[0]
            words = ["haus", "hund", "gehen", "wasser", "schnell"] * 40
            for word in words:
                wiktdata.lookup(word, code, pos="NOUN")
            report(f"{len(words)} lookups against the {code} index")
    """,
}

_PREAMBLE = """
import json, sys, time
sys.path.insert(0, {root!r})
_start = time.perf_counter()

def report(what, skipped=False):
    print("__RESULT__" + json.dumps(
        {{"seconds": round(time.perf_counter() - _start, 3),
          "what": what, "skipped": skipped}}))
"""


def run_stage(name: str) -> dict:
    """Run one stage in a fresh interpreter and return its measurement."""
    script = (_PREAMBLE.format(root=str(ROOT / "src"))
              + textwrap.dedent(STAGES[name]))
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, env=env, cwd=ROOT, timeout=900)
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    return {"seconds": 0.0, "what": "failed to run", "skipped": True,
            "error": proc.stderr[-300:]}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=sorted(STAGES),
                        help="measure one stage instead of all of them")
    parser.add_argument("--runs", type=int, default=3,
                        help="repetitions per stage, median reported")
    parser.add_argument("--out", help="write the numbers to this JSON file")
    args = parser.parse_args()

    names = [args.stage] if args.stage else list(STAGES)
    print(f"Wall-clock per phase, fresh interpreter each time, "
          f"median of {args.runs}.")
    print(f"{'stage':<10}{'median':>9}{'best':>9}{'limit':>8}  {'':4} what it does")
    print("-" * 96)

    results, failed = {}, 0
    for name in names:
        runs, detail = [], {}
        for _ in range(args.runs):
            detail = run_stage(name)
            if detail.get("skipped"):
                break
            runs.append(detail["seconds"])

        limit = THRESHOLDS.get(name)
        if detail.get("skipped"):
            verdict, median, best = "skip", 0.0, 0.0
        else:
            median, best = statistics.median(runs), min(runs)
            verdict = "ok" if median <= limit else "OVER"
            if verdict == "OVER":
                failed += 1
        results[name] = {"median": median, "best": best, "limit": limit,
                         "what": detail["what"], "verdict": verdict}
        print(f"{name:<10}{median:>8.2f}s{best:>8.2f}s{limit:>7.0f}s  "
              f"{verdict:<4} {detail['what']}")
        if detail.get("error"):
            last = str(detail["error"]).strip().splitlines()[-1:] or [""]
            print(f"          {last[0][:80]}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.out}")

    print(f"\n{failed} stage(s) over the threshold.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
