#!/usr/bin/env python3
"""
Measure what a candidate antonym source would add to real decks.

    python scripts/measure_antonym_sources.py --fetch          # download and filter, then measure
    python scripts/measure_antonym_sources.py                  # measure using a filtered file already on disk
    python scripts/measure_antonym_sources.py --decks fr:A0rJNx4lGDo,de:loqocHC9aAU
    python scripts/measure_antonym_sources.py --build out.sqlite   # write the artifact and report its size

This exists because ADR-010 needed a number, and because every antonym claim
this project has made without one has been wrong. TASKS.md carried 59% and
23% for German and French; both were wrong. It carried "cross-language
collapses to 3%"; it is 17.3%. The fix is not to be more careful in prose,
it is to make the measurement cheap enough to repeat.

What it measures, per language:

  index today     lemmas in a real deck that the offline Wiktionary index
                  already has an antonym for
  candidate       lemmas the candidate source has a same-language antonym for
  union           either of the above, which is what the card would show
  adds            candidate minus index, the only column that justifies work

The deck lemmas come from `pipeline.db`, so this reads what real runs
actually produced rather than a word list someone chose. Nothing is written
unless --build is passed.

The candidate source is ConceptNet 5.7.0's assertions dump, filtered to
antonym edges. --fetch streams the 498 MB gzip through the filter without
storing it: what lands on disk is roughly 36 MB of antonym edges. Rerunning
without --fetch reuses that file.
"""

from __future__ import annotations

import argparse
import collections
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline.language import SPACY_MODELS  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────────────

DUMP_URL = (
    "https://s3.amazonaws.com/conceptnet/downloads/2019/edges/conceptnet-assertions-5.7.0.csv.gz"
)

# The relations worth keeping. /r/Antonym is the one; /r/DistinctFrom is
# collected alongside it so the difference can be measured rather than
# assumed, and is excluded from every count below.
RELATIONS = ("/r/Antonym", "/r/DistinctFrom")

DEFAULT_EDGES = Path("dictionaries/conceptnet_antonym_edges.tsv")

# The decks ADR-010's numbers came from. One per language, each the largest
# real run in `pipeline.db` for that language, so a rerun is comparable.
DEFAULT_DECKS = {
    "fr": "A0rJNx4lGDo",
    "de": "loqocHC9aAU",
    "ru": "c9ghnkHZLwo",
}


# ── Fetching ─────────────────────────────────────────────────────────────────


def fetch_edges(destination: Path) -> None:
    """
    Stream the ConceptNet dump through a filter, keeping only antonym edges.

    Args:
        destination: File to write the filtered TSV to.

    Raises:
        RuntimeError: If the pipeline exits non-zero or writes nothing.

    The dump is 498 MB and this project has a disk-size release goal, so it
    is never stored whole. curl, zcat and grep do the work in flight; python
    would hold the same bytes for no benefit.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    pattern = "|".join(RELATIONS)
    command = f"curl -sL {DUMP_URL!r} | zcat | grep -E $'\\t({pattern})\\t'"

    print(f"Streaming {DUMP_URL}")
    print("  (498 MB, filtered in flight, nothing else touches disk)")
    with destination.open("wb") as handle:
        result = subprocess.run(["bash", "-c", command], stdout=handle, stderr=subprocess.PIPE)

    if result.returncode != 0 or destination.stat().st_size == 0:
        raise RuntimeError(
            f"Fetch failed (exit {result.returncode}): "
            f"{result.stderr.decode(errors='replace')[:400]}"
        )
    size_mb = destination.stat().st_size / 1e6
    print(f"  wrote {destination} ({size_mb:.1f} MB)")


# ── Reading the candidate ────────────────────────────────────────────────────


def load_candidate(edges: Path, language: str) -> dict[str, set[str]]:
    """
    Read same-language antonym pairs for one language.

    Args:
        edges:    Filtered TSV from fetch_edges().
        language: BCP-47 code, matched against the ConceptNet URI's language
                  segment.

    Returns:
        Mapping of term to its antonym terms, both directions, underscores
        replaced by spaces.

    Three filters, and each one is load-bearing rather than tidy. Only
    /r/Antonym, because /r/DistinctFrom is a different claim. Only pairs
    whose two ends share a language, because constraint 3.3 keeps anything
    describing the word in the transcript language and a cross-language edge
    is exactly how that gets violated. And never a term against itself,
    which the dump does contain.
    """
    pairs: dict[str, set[str]] = collections.defaultdict(set)

    with edges.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5 or parts[1] != "/r/Antonym":
                continue
            start, end = parts[2].split("/"), parts[3].split("/")
            if len(start) < 4 or len(end) < 4:
                continue
            if start[2] != language or end[2] != language:
                continue
            left, right = start[3], end[3]
            if left == right:
                continue
            pairs[left].add(right.replace("_", " "))
            pairs[right].add(left.replace("_", " "))

    return pairs


def normalise(lemma: str) -> str:
    """Return `lemma` in the shape ConceptNet keys its terms by."""
    return lemma.lower().replace(" ", "_")


# ── Reading what the project already has ─────────────────────────────────────


def index_has_antonyms(language: str) -> set[str]:
    """
    Return the lowercased words the offline index already has an antonym for.

    Args:
        language: BCP-47 code, naming `dictionaries/wiktionary_<code>.sqlite`.

    Raises:
        FileNotFoundError: If that index has not been built.
    """
    path = Path(f"dictionaries/wiktionary_{language}.sqlite")
    if not path.exists():
        raise FileNotFoundError(f"{path} not built; run: make dictionary LANGUAGE={language}")

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT word FROM entries WHERE antonyms IS NOT NULL AND antonyms != ''"
        )
        return {word.lower() for (word,) in rows}
    finally:
        connection.close()


def deck_lemmas(video_id: str) -> list[str]:
    """
    Return the distinct lemmas one real run produced.

    Args:
        video_id: YouTube id, as `pipeline.db` records it.

    Raises:
        FileNotFoundError: If `pipeline.db` is absent.
    """
    if not Path("pipeline.db").exists():
        raise FileNotFoundError("pipeline.db not found; run from the repository root")

    connection = sqlite3.connect("file:pipeline.db?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT DISTINCT lemma FROM vocabulary WHERE video_id = ?", (video_id,)
        )
        return [lemma for (lemma,) in rows]
    finally:
        connection.close()


# ── Reporting ────────────────────────────────────────────────────────────────


def report(language: str, video_id: str, edges: Path, examples: int = 8) -> None:
    """
    Print the coverage comparison for one language, and some of what it adds.

    Args:
        language: BCP-47 code.
        video_id: The deck to measure against.
        edges:    Filtered TSV from fetch_edges().
        examples: How many added words to list.
    """
    lemmas = deck_lemmas(video_id)
    if not lemmas:
        print(f"\n{language}: no vocabulary rows for {video_id}")
        return

    indexed = index_has_antonyms(language)
    candidate = load_candidate(edges, language)

    from_index = {lemma for lemma in lemmas if lemma.lower() in indexed}
    from_candidate = {lemma for lemma in lemmas if normalise(lemma) in candidate}
    added = from_candidate - from_index
    total = len(lemmas)

    print(f"\n{language}  ({video_id}, {total} lemmas)")
    print(f"  index today   {len(from_index):5}  {len(from_index) / total:6.1%}")
    print(f"  candidate     {len(from_candidate):5}  {len(from_candidate) / total:6.1%}")
    union = from_index | from_candidate
    print(f"  union         {len(union):5}  {len(union) / total:6.1%}")
    print(f"  adds          {len(added):5}  {len(added) / total:+6.1%} of the deck")

    for lemma in sorted(added)[:examples]:
        antonyms = ", ".join(sorted(candidate[normalise(lemma)])[:5])
        print(f"      {lemma:24} -> {antonyms}")


def build_artifact(edges: Path, destination: Path) -> None:
    """
    Write the index that would actually ship, and report its size.

    Args:
        edges:       Filtered TSV from fetch_edges().
        destination: SQLite file to create, replacing any existing one.

    Only languages Tango has a spaCy model for are kept. The rest are real
    data but nothing can tokenize a transcript in them, so they would be
    weight with no path to a card.
    """
    if destination.exists():
        destination.unlink()

    connection = sqlite3.connect(destination)
    connection.execute("CREATE TABLE antonyms (lang TEXT, word TEXT, antonyms TEXT)")

    rows = 0
    for language in sorted(SPACY_MODELS):
        for word, antonyms in load_candidate(edges, language).items():
            connection.execute(
                "INSERT INTO antonyms VALUES (?, ?, ?)",
                (language, word.replace("_", " "), "|".join(sorted(antonyms))),
            )
            rows += 1
    connection.execute("CREATE INDEX idx_antonyms ON antonyms (lang, word)")
    connection.commit()
    connection.close()

    print(f"\n{destination}: {rows} words, {destination.stat().st_size / 1e6:.2f} MB")


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--fetch", action="store_true", help="download and filter the dump first")
    parser.add_argument("--edges", type=Path, default=DEFAULT_EDGES, help="filtered TSV to use")
    parser.add_argument("--decks", help="comma-separated lang:video_id pairs")
    parser.add_argument("--build", type=Path, help="write the shippable index here and size it")
    args = parser.parse_args()

    if args.fetch:
        fetch_edges(args.edges)

    if not args.edges.exists():
        print(f"{args.edges} not found. Run once with --fetch.", file=sys.stderr)
        return 1

    decks = DEFAULT_DECKS
    if args.decks:
        decks = dict(pair.split(":", 1) for pair in args.decks.split(","))

    for language, video_id in decks.items():
        try:
            report(language, video_id, args.edges)
        except FileNotFoundError as error:
            print(f"\n{language}: skipped, {error}")

    if args.build:
        build_artifact(args.edges, args.build)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
