"""
cards.py
--------
Responsible for:
  1. Defining the genanki Model (fields, card template, CSS)
  2. Building genanki Notes from DefinitionResult objects
  3. Assembling a Deck and writing a timestamped .apkg file

Card type: Recognition only (Front -> Back).
  Front: the word
  Back:  definition, part of speech, two examples, synonyms, antonyms

Fallback card (no definition found):
  Front: the word
  Back:  [Note: no definition found] + transcript example sentence

Sub-deck naming via '::' is supported natively by genanki.
Output filename: {video_id}_{YYYYMMDD_HHMMSS}.apkg

Dependencies:
    genanki

Constants (moved to config.py at end of project):
    MODEL_ID, DECK_ID, OUTPUT_DIR
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import genanki

from pipeline.definition import DefinitionResult

logger = logging.getLogger(__name__)

# -- Constants (moved to config.py at end of project) -------------------------
from pipeline.config import MODEL_ID, DECK_ID, OUTPUT_DIR



# -- Card CSS ------------------------------------------------------------------

CARD_CSS = """
.card {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 18px;
    color: #1a1a2e;
    background-color: #f8f9fa;
    max-width: 560px;
    margin: 0 auto;
    padding: 20px;
    line-height: 1.6;
}
.word {
    font-size: 28px;
    font-weight: bold;
    color: #0f3460;
    text-align: center;
    margin-bottom: 6px;
    letter-spacing: 0.5px;
}
.pos {
    font-size: 13px;
    color: #6b7280;
    text-align: center;
    font-style: italic;
    margin-bottom: 16px;
}
hr { border: none; border-top: 1px solid #d1d5db; margin: 14px 0; }
.definition { font-size: 16px; color: #374151; margin-bottom: 14px; }
.examples-label, .vocab-label {
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    color: #9ca3af;
    letter-spacing: 0.8px;
    margin-bottom: 4px;
}
.example {
    font-style: italic;
    color: #4b5563;
    font-size: 14px;
    margin-bottom: 6px;
    padding-left: 10px;
    border-left: 3px solid #00b4d8;
}
.example-source { font-size: 11px; color: #9ca3af; margin-bottom: 10px; padding-left: 10px; }
.vocab-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.vocab-pill { background: #e8f4f8; color: #0f3460; border-radius: 12px; padding: 2px 10px; font-size: 13px; }
.antonym-pill { background: #fef3c7; color: #92400e; border-radius: 12px; padding: 2px 10px; font-size: 13px; }
.fallback-note { font-size: 12px; color: #9ca3af; font-style: italic; text-align: center; margin-bottom: 10px; }
"""

# -- Card templates -----------------------------------------------------------

FRONT_TEMPLATE = "{{Word}}"

BACK_TEMPLATE = """
{{FrontSide}}
<hr>
<div class="pos">{{PartOfSpeech}}</div>
<div class="definition">{{Definition}}</div>

{{#ExampleDict}}
<div class="examples-label">Examples</div>
<div class="example">{{ExampleDict}}</div>
<div class="example-source">— Dictionary</div>
{{/ExampleDict}}

{{#ExampleTranscript}}
<div class="example">{{ExampleTranscript}}</div>
<div class="example-source">— From video</div>
{{/ExampleTranscript}}

{{#Synonyms}}
<div class="vocab-label">Synonyms</div>
<div class="vocab-row">{{Synonyms}}</div>
{{/Synonyms}}

{{#Antonyms}}
<div class="vocab-label">Antonyms</div>
<div class="vocab-row">{{Antonyms}}</div>
{{/Antonyms}}

{{#FallbackNote}}
<div class="fallback-note">{{FallbackNote}}</div>
{{/FallbackNote}}
"""

# -- Model --------------------------------------------------------------------

def _build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "YT Anki Pipeline — Recognition",
        fields=[
            {"name": "Word"},
            {"name": "PartOfSpeech"},
            {"name": "Definition"},
            {"name": "ExampleDict"},
            {"name": "ExampleTranscript"},
            {"name": "Synonyms"},
            {"name": "Antonyms"},
            {"name": "FallbackNote"},
            {"name": "VideoID"},
            {"name": "Source"},
        ],
        templates=[
            {
                "name": "Recognition",
                "qfmt": FRONT_TEMPLATE,
                "afmt": BACK_TEMPLATE,
            }
        ],
        css=CARD_CSS,
    )

# -- Note builders ------------------------------------------------------------

def _format_pills(words: list[str], css_class: str) -> str:
    if not words:
        return ""
    return " ".join(f'<span class="{css_class}">{w}</span>' for w in words)


def _build_note(
    result: DefinitionResult,
    model: genanki.Model,
    video_id: str,
) -> genanki.Note:
    return genanki.Note(
        model=model,
        fields=[
            result.lemma.capitalize(),
            result.part_of_speech,
            result.definition,
            result.example_dict          or "",
            result.example_transcript    or "",
            _format_pills(result.synonyms, "vocab-pill"),
            _format_pills(result.antonyms, "antonym-pill"),
            "",
            video_id,
            result.source,
        ],
        guid=genanki.guid_for(result.lemma, video_id),
        tags=["yt-anki", video_id],
    )


def _build_fallback_note(
    lemma: str,
    example_transcript: Optional[str],
    model: genanki.Model,
    video_id: str,
) -> genanki.Note:
    return genanki.Note(
        model=model,
        fields=[
            lemma.capitalize(),
            "", "", "",
            example_transcript or "",
            "", "",
            "Note: no definition found — example from video",
            video_id,
            "not_found",
        ],
        guid=genanki.guid_for(lemma, video_id),
        tags=["yt-anki", video_id, "no-definition"],
    )

# -- Output path --------------------------------------------------------------

def _build_output_path(video_id: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / f"{video_id}_{timestamp}.apkg"

# -- Snippet sentence finder --------------------------------------------------

def _find_in_snippets(lemma: str, snippets: dict) -> Optional[str]:
    pattern = re.compile(r"\b" + re.escape(lemma) + r"\w*", re.IGNORECASE)
    for key, val in snippets.items():
        if not isinstance(key, float):
            continue
        text = val.get("text", "")
        if pattern.search(text):
            return text.strip()
    return None

# -- Main entry point ---------------------------------------------------------

@dataclass
class PackageResult:
    """
    Result of building an Anki package.

    Attributes:
        path:            Path to the written .apkg file.
        total_cards:     Total cards actually written to the package.
        standard_count:  Cards built from a found DefinitionResult.
        fallback_count:  Cards built from a not_found lemma with a
                         transcript example (word + transcript sentence only).
        skipped_count:   Lemmas dropped entirely — no definition AND no
                         transcript example found. These produce no card.
    """
    path:           Path
    total_cards:    int
    standard_count: int
    fallback_count: int
    skipped_count:  int


def build_package(
    video_id: str,
    deck_name: str,
    found: list[DefinitionResult],
    not_found: list[str],
    snippets: Optional[dict] = None,
) -> PackageResult:
    """
    Build an Anki .apkg package from definition results.

    Args:
        video_id:   YouTube video ID — used in filename, GUID, and tags.
        deck_name:  Full deck name, supports '::' sub-deck notation.
                    e.g. "Language::English::Vocabulary"
        found:      DefinitionResult list from definition.fetch_definitions().
        not_found:  Lemmas with no definition from either API.
        snippets:   Output of transcript.get_snippets(). Used for fallback
                    card transcript sentences.

    Returns:
        PackageResult with the output path and accurate card counts.
        total_cards = standard_count + fallback_count (these are NOT
        additive with skipped_count — skipped words produce no card
        and are not part of total_cards).

    Raises:
        ValueError: If found and not_found are both empty.
    """
    if not found and not not_found:
        raise ValueError(
            "No words to build cards from. "
            "Ensure deck check produced NEW words before calling build_package()."
        )

    model          = _build_model()
    deck           = genanki.Deck(DECK_ID, deck_name)
    standard_count = 0
    fallback_count = 0
    skipped_count  = 0

    # Standard cards
    for result in found:
        deck.add_note(_build_note(result, model, video_id))
        standard_count += 1
        logger.debug("Card built: '%s' (%s)", result.lemma, result.source)

    # Fallback cards
    for lemma in not_found:
        transcript_example = _find_in_snippets(lemma, snippets) if snippets else None
        if not transcript_example:
            logger.warning(
                "Skipping '%s' — no definition and no transcript example.", lemma
            )
            skipped_count += 1
            continue
        deck.add_note(_build_fallback_note(lemma, transcript_example, model, video_id))
        fallback_count += 1
        logger.debug("Fallback card built: '%s'", lemma)

    total_cards = standard_count + fallback_count
    output_path = _build_output_path(video_id)
    genanki.Package(deck).write_to_file(str(output_path))

    logger.info(
        "Package written: %s | %d cards (%d standard, %d fallback, %d skipped)",
        output_path.name, total_cards, standard_count, fallback_count, skipped_count,
    )

    return PackageResult(
        path=output_path,
        total_cards=total_cards,
        standard_count=standard_count,
        fallback_count=fallback_count,
        skipped_count=skipped_count,
    )