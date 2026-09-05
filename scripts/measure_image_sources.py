#!/usr/bin/env python3
"""
Measure what card images would actually add to real decks.

    python scripts/measure_image_sources.py                      # every default deck
    python scripts/measure_image_sources.py --decks de:703B19Uz7Fk
    python scripts/measure_image_sources.py --limit 60           # fewer lookups per language
    python scripts/measure_image_sources.py --icons              # also size the icon fallback

This exists for the same reason measure_antonym_sources.py does: ADR-009
phase 3 needs a number before images become a default, and the last two
numbers this project asserted about images without measuring were both
wrong. ADR-009 costed embedded audio at "tens of megabytes" when the real
figure was 16-30 KB per file, and its own image plan used Commons text
search, which returned a coin from the town of Laufen for the verb `laufen`.

What it reports, per language:

  nouns         lemmas the deck recorded as nouns, the only candidates
  article       nouns with a Wikipedia article in that language
  concept       of those, ones carrying a Wikidata item
  admitted      of those, ones the P31 gate calls photographable
  with image    of those, ones that actually have a picture to show

`with image` over `nouns` is the number that decides whether two card
fields are justified. ADR-010 shipped the antonym index on +12.5 points for
French, and a comparable bar applies here.

The lemmas come from `pipeline.db`, so this reads what real runs produced
rather than a word list someone picked. Nothing is written, and nothing is
cached beyond the image cache the pipeline would populate anyway.

Slow on purpose. images.py paces itself at one request a second because
Wikimedia asks callers to, and each lemma costs two or three requests, so a
400-noun language is roughly twenty minutes. Use --limit for a quick look.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline import images  # noqa: E402
from pipeline.definition import is_concrete_noun  # noqa: E402

# The languages the definition cache actually holds, measured 5 September
# 2026: fr 488 nouns, de 245, en 74. Every other language this project has
# run end to end predates the v0.6.0 cache key, so its definitions are not
# recoverable per language. Widening this is exactly what the extra video
# ids in the v0.11.0 plan are for.
DEFAULT_LANGUAGES = ("fr", "de", "en")


def cached_nouns(language: str) -> list[str]:
    """
    The noun lemmas real runs produced for one language.

    Args:
        language: BCP-47 code, matched against the cache key's source half.

    Returns:
        Distinct lowercase lemmas the definition cache calls nouns.

    Read from `definitions`, not `vocabulary`, for two reasons found by
    looking rather than assuming. `vocabulary.part_of_speech` is NULL for
    all 11,482 rows, so the obvious query returns nothing. And
    `definitions.lemma` is not a lemma: since v0.6.0 it is a composite cache
    key, `word::srclang::deflang::pos`, which is what carries the language
    here at all. A plain join between the two tables matches zero rows.

    Raises:
        FileNotFoundError: If `pipeline.db` is absent.
    """
    if not Path("pipeline.db").exists():
        raise FileNotFoundError("pipeline.db not found; run from the repository root")

    connection = sqlite3.connect("file:pipeline.db?mode=ro", uri=True)
    try:
        keys = [r[0] for r in connection.execute("SELECT lemma FROM definitions")]
    finally:
        connection.close()

    found = set()
    for key in keys:
        parts = key.split("::")
        if len(parts) != 4:
            continue
        word, source_language, _, pos = parts
        if source_language == language and pos == "noun":
            found.add(word.lower())
    return sorted(found)


def measure(language: str, limit: int, seed: int = 0) -> dict:
    """
    Walk the gate one stage at a time for one language.

    Counting only the final answer hides which stage is doing the refusing,
    and that is the thing worth knowing: a low rate because articles are
    missing is a different problem from a low rate because the gate refuses.
    """
    nouns = cached_nouns(language)
    if limit and len(nouns) > limit:
        # Sampled rather than truncated: an alphabetical prefix is not a
        # random sample of a vocabulary.
        random.Random(seed).shuffle(nouns)
        nouns = nouns[:limit]

    counts = {"nouns": len(nouns), "article": 0, "concept": 0,
              "admitted": 0, "with_image": 0}
    hits: list[tuple[str, str]] = []
    refused: list[str] = []

    for lemma in nouns:
        page = images._article(lemma, language)
        if page is None:
            refused.append(lemma)
            continue
        counts["article"] += 1

        qid = page.get("pageprops", {}).get("wikibase_item")
        if not qid:
            refused.append(lemma)
            continue
        counts["concept"] += 1

        if not images.is_photographable(qid):
            refused.append(lemma)
            continue
        counts["admitted"] += 1

        url = images._commons_url(images._claims(qid, "P18")) \
            or page.get("thumbnail", {}).get("source")
        if url:
            counts["with_image"] += 1
            hits.append((lemma, qid))

    return {"language": language, "counts": counts,
            "hits": hits, "refused": refused}


def wordnet_agreement(language: str, refused: list[str]) -> tuple[int, int]:
    """
    How often the older WordNet gate agrees, where it can judge at all.

    Reported because ADR-009's original design used only that gate, and it
    reaches 0% of German cards: OMW has no German WordNet. Anywhere the two
    disagree is worth a look before either is trusted alone.
    """
    judged = agreed = 0
    for lemma in refused:
        try:
            concrete = is_concrete_noun(lemma, language)
        except Exception:
            continue
        judged += 1
        if not concrete:
            agreed += 1
    return judged, agreed


def report(result: dict, samples: int = 10) -> None:
    """Print one language's table and a sample to eyeball."""
    c = result["counts"]
    n = c["nouns"] or 1
    print(f"\n  {result['language']}  ({c['nouns']} nouns from the definition cache)")
    for stage in ("article", "concept", "admitted", "with_image"):
        print(f"    {stage:<12} {c[stage]:>5}  {c[stage] / n:>6.1%}")

    if result["hits"]:
        print("    sample of what a learner would see:")
        for lemma, qid in result["hits"][:samples]:
            print(f"      {lemma:<22} {qid}")

    if result["refused"]:
        print(f"    refused ({len(result['refused'])}), a sample:")
        print("      " + ", ".join(result["refused"][:samples]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--languages", help="codes, comma separated")
    parser.add_argument("--limit", type=int, default=120,
                        help="nouns sampled per language (0 for all)")
    parser.add_argument("--icons", action="store_true",
                        help="also size the icon fallback over refused nouns")
    args = parser.parse_args()

    languages = DEFAULT_LANGUAGES
    if args.languages:
        languages = tuple(args.languages.split(","))

    print("Card image coverage, measured against the definition cache in pipeline.db.")
    print(f"Sampling up to {args.limit or 'all'} nouns per language.")

    totals = {"nouns": 0, "with_image": 0}
    for language in languages:
        try:
            result = measure(language, args.limit)
        except FileNotFoundError as exc:
            print(f"  {exc}")
            return 1
        report(result)
        totals["nouns"] += result["counts"]["nouns"]
        totals["with_image"] += result["counts"]["with_image"]

        if args.icons:
            judged, agreed = wordnet_agreement(language, result["refused"])
            if judged:
                print(f"    WordNet could judge {judged} of the refused, "
                      f"and agreed with {agreed} ({agreed / judged:.0%})")
            else:
                print("    WordNet could judge none of the refused "
                      "(no OMW entry for this language)")

    if totals["nouns"]:
        print(f"\n  overall: {totals['with_image']}/{totals['nouns']} nouns "
              f"({totals['with_image'] / totals['nouns']:.1%}) would show an image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
