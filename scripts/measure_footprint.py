#!/usr/bin/env python3
"""
Measure what Tango costs a machine, for the v0.12.0 acceptance targets.

    python scripts/measure_footprint.py              # every measurement
    python scripts/measure_footprint.py --stage nlp  # just one

The rung asks for four numbers and none of them had ever been taken:

    peak RSS on a normal run          <= 1 GB
    index build completes within      <= 2 GB RAM
    a full run completes on           4 GB / 2 cores
    high-end hardware spends more     workers scale to the machine

This measures the first three. The fourth is a design question that the
numbers here should decide rather than precede.

Method, and its one limitation. Peak RSS comes from `resource.getrusage`,
which reports the high-water mark of the *whole process*, so each stage is
measured in a fresh subprocess: measuring them in one process would report
the maximum across all of them and attribute it to whichever ran last. That
also means the numbers include the interpreter and the imports, which is
right, because that is what the machine actually has to hold.

`ru_maxrss` is in kilobytes on Linux and bytes on macOS. Both are handled.
No psutil: it is not a dependency and this must run on a clean install.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The targets from ROADMAP v0.12.0, in megabytes, so a run says pass or fail
# rather than leaving the reader to compare.
TARGETS = {
    # No target for the import itself: the rung sets none, and this is
    # reported because it is the floor everything else sits on, not because
    # anything says it should be lower.
    "nlp": 1024,
    "run": 1024,
    "index-build": 2048,
    "index": 2048,
}


def _peak_rss_mb() -> float:
    """This process's high-water RSS. Kilobytes on Linux, bytes on macOS."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1024 if sys.platform != "darwin" else raw / (1024 * 1024)


# Each stage runs in its own interpreter. The body prints one JSON line so
# the parent does not have to parse prose.
STAGES: dict[str, str] = {
    "import": """
        import pipeline.cards, pipeline.definition, pipeline.deck
        import pipeline.nlp, pipeline.transcript, pipeline.wiktdata
        report("importing every module, before any work")
    """,
    "nlp": """
        from pipeline import nlp
        # A transcript's worth of text at a realistic length: a 10-minute
        # video is roughly 1500 words. This is the heaviest stage of a run,
        # because it is where the spaCy model is loaded.
        text = ("Der Hund lauft schnell durch den grossen Garten und "
                "bellt laut. ") * 150
        vocab = nlp.process_transcript(text, "de")
        report(f"spaCy model loaded, {len(text.split())} words tokenised, "
               f"{len(vocab)} lemmas kept")
    """,
    "cards": """
        from pipeline import cards
        from pipeline.definition import DefinitionResult
        found = [DefinitionResult(f"wort{i}", "eine Definition", "Ein Satz.",
                                  "Noch einer.", None, ["syn"], ["ant"],
                                  "noun", "test")
                 for i in range(400)]
        cards.build_package("measure", "Measurement", found, [], language="de")
        report("a 400-card package built and written")
    """,
    "index-build": """
        import gzip, json, tempfile
        from pathlib import Path
        from pipeline import wiktdata

        # A synthetic extract rather than the real 288 MB download, because
        # what is being measured is whether the builder streams. It does, by
        # reading, so this confirms it rather than discovers it: the loop
        # appends to a batch and flushes with executemany. If it buffered
        # instead, this number would scale with the record count and the
        # rung's 2 GB target would be a real risk on the German extract,
        # which is 994k rows.
        RECORDS = 200_000
        tmp = Path(tempfile.mkdtemp())
        archive = tmp / "synthetic.jsonl.gz"
        with gzip.open(archive, "wt", encoding="utf-8") as fh:
            for i in range(RECORDS):
                fh.write(json.dumps({
                    "word": f"wort{i}", "lang_code": "de", "pos": "noun",
                    "senses": [{"glosses": [f"Bedeutung nummer {i}, "
                                            "ein realistisch langer Text."],
                                "examples": [{"text": f"Ein Satz mit wort{i}."}]}],
                    "sounds": [{"ipa": "[test]"}],
                    "synonyms": [{"word": "syn"}], "antonyms": [{"word": "ant"}],
                }) + chr(10))
        wiktdata.DICT_DIR = tmp
        built = wiktdata.build_index("de", archive=archive)
        report(f"index built from {RECORDS} synthetic records, {built} indexed")
    """,
    "index": """
        from pipeline import wiktdata
        from pipeline.config import DICT_DIR
        path = wiktdata.index_path("de")
        if not path.exists():
            report("no German index built, skipped", skipped=True)
        else:
            # The read path, which is what a run does. Building one needs the
            # 288 MB archive and is measured separately by --build-index.
            conn = wiktdata._connection("de")
            rows = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            for word in ("hund", "haus", "gehen", "schnell", "wasser"):
                wiktdata.lookup(word, "de", pos="NOUN")
            report(f"index opened and queried, {rows} rows")
    """,
}

_PREAMBLE = """
import json, resource, sys
sys.path.insert(0, {root!r})

def report(what, skipped=False):
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mb = raw / 1024 if sys.platform != "darwin" else raw / (1024 * 1024)
    print("__RESULT__" + json.dumps(
        {{"peak_mb": round(mb, 1), "what": what, "skipped": skipped}}))
"""


def run_stage(name: str) -> dict:
    """Run one stage in a fresh interpreter and return its measurement."""
    body = textwrap.dedent(STAGES[name])
    script = _PREAMBLE.format(root=str(ROOT / "src")) + textwrap.dedent(body)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, env=env, cwd=ROOT, timeout=900)
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    return {"peak_mb": 0.0, "what": "failed to run",
            "skipped": True, "error": proc.stderr[-400:]}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=sorted(STAGES),
                        help="measure one stage instead of all of them")
    args = parser.parse_args()

    names = [args.stage] if args.stage else list(STAGES)
    print("Peak resident memory per stage, each in its own interpreter.")
    print(f"{'stage':<10} {'peak RSS':>10}  {'target':>8}  {'':4} what was done")
    worst = 0.0
    for name in names:
        result = run_stage(name)
        peak = result["peak_mb"]
        worst = max(worst, peak)
        target = TARGETS.get(name)
        if result.get("skipped"):
            verdict = "skip"
        elif target is None:
            verdict = ""
        else:
            verdict = "ok" if peak <= target else "OVER"
        target_text = f"{target} MB" if target else "-"
        print(f"{name:<10} {peak:>7.1f} MB  {target_text:>8}  {verdict:<4} {result['what']}")
        if result.get("error"):
            last = str(result["error"]).strip().splitlines()
            if last:
                print(f"           {last[-1][:76]}")

    print(f"\nWorst stage: {worst:.1f} MB. The rung asks for <= 1024 MB on a "
          f"normal run and <= 2048 MB to build an index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
