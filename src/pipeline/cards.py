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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import genanki

from pipeline import media
from pipeline.definition import DefinitionResult
from pipeline.language import localise_pos

logger = logging.getLogger(__name__)

# -- Constants (moved to config.py at end of project) -------------------------
from pipeline.config import MODEL_ID, DECK_ID, OUTPUT_DIR



# -- Card CSS ------------------------------------------------------------------

CARD_CSS = """
.card {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 17px;
    color: var(--fg, #1a1a2e);
    background-color: var(--canvas, #ffffff);
    max-width: 580px;
    margin: 0 auto;
    padding: 24px 20px;
    line-height: 1.65;
}

/* Front — centered word */
.word-front {
    font-size: 36px;
    font-weight: 700;
    text-align: center;
    color: var(--fg, #0f3460);
    padding: 20px 0 10px;
    letter-spacing: 0.5px;
}

/* Back — main word */
.word-back {
    font-size: 30px;
    font-weight: 700;
    text-align: center;
    color: var(--fg, #0f3460);
    padding: 10px 0 4px;
}

/* Translated word or synonyms secondary line */
.word-secondary {
    font-size: 18px;
    text-align: center;
    color: var(--new-count, #00b4d8);
    margin-bottom: 4px;
    font-style: italic;
}

hr {
    border: none;
    border-top: 1px solid var(--border, #d1d5db);
    margin: 12px 0;
}

.pos {
    font-size: 13px;
    color: var(--slightly-grey-text, #6b7280);
    text-align: center;
    font-style: italic;
    margin-bottom: 10px;
}

.definition {
    font-size: 16px;
    color: var(--fg, #374151);
    margin-bottom: 16px;
    text-align: center;
}

.example-block {
    margin-bottom: 14px;
}

.example {
    font-style: italic;
    color: var(--fg, #4b5563);
    font-size: 15px;
    margin-bottom: 3px;
    padding-left: 14px;
    border-left: 3px solid var(--new-count, #00b4d8);
}

.example-source {
    font-size: 11px;
    color: var(--slightly-grey-text, #9ca3af);
    padding-left: 14px;
    margin-bottom: 0;
}

.section-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    color: var(--slightly-grey-text, #9ca3af);
    letter-spacing: 0.8px;
    margin: 14px 0 6px;
}

.vocab-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 6px;
}

/* Pronunciation (ADR-009 phase 1). Uses the same Anki CSS variables with
   literal fallbacks as everything above, so it reads correctly in both the
   light and dark card themes rather than only the one it was written in. */
.ipa {
    font-size: 15px;
    color: var(--fg, #0f3460);
    font-family: "Charis SIL", "Doulos SIL", "Gentium Plus", serif;
    margin-bottom: 6px;
}

.audio-link {
    font-size: 12px;
    color: var(--new-count, #00b4d8);
    text-decoration: none;
    border: 1px solid var(--new-count, #00b4d8);
    border-radius: 10px;
    padding: 2px 9px;
    display: inline-block;
}

.vocab-pill {
    background: transparent;
    color: var(--fg, #0f3460);
    border: 1px solid var(--new-count, #00b4d8);
    border-radius: 12px;
    padding: 2px 12px;
    font-size: 13px;
}

.antonym-pill {
    background: transparent;
    color: var(--fg, #92400e);
    border: 1px solid var(--fg, #92400e);
    border-radius: 12px;
    padding: 2px 12px;
    font-size: 13px;
}

.fallback-note {
    font-size: 12px;
    color: var(--slightly-grey-text, #9ca3af);
    font-style: italic;
    text-align: center;
    margin-top: 10px;
}
"""

# -- Card templates -----------------------------------------------------------

FRONT_TEMPLATE = '<div class="word-front">{{Word}}</div>'

BACK_TEMPLATE = """
<div class="word-back">{{Word}}</div>

<hr>

<div class="pos">{{Class}}</div>
<div class="definition">{{Definition}}</div>

{{#1st Example Sentence}}
<div class="example-block">
<div class="example">{{1st Example Sentence}}</div>
<div class="example-source">— Dictionary</div>
</div>
{{/1st Example Sentence}}

{{#2nd Example Sentence}}
<div class="example-block">
<div class="example">{{2nd Example Sentence}}</div>
<div class="example-source">— Dictionary</div>
</div>
{{/2nd Example Sentence}}

{{#Example from Youtube Video}}
<div class="example-block">
<div class="example">{{Example from Youtube Video}}</div>
<div class="example-source">— From video</div>
</div>
{{/Example from Youtube Video}}

{{#Synonyms}}
<div class="section-label">Synonyms</div>
<div class="vocab-row">{{Synonyms}}</div>
{{/Synonyms}}

{{#Antonyms}}
<div class="section-label">Antonyms</div>
<div class="vocab-row">{{Antonyms}}</div>
{{/Antonyms}}

{{#IPA}}
<div class="section-label">Pronunciation</div>
<div class="ipa">{{IPA}}</div>
{{/IPA}}

{{#Pronunciation}}
<div class="pronunciation">{{Pronunciation}}</div>
{{/Pronunciation}}

"""

# -- Field identity -----------------------------------------------------------

# The single source of truth for what fields exist and in what order.
#
# This tuple exists to retire a hard constraint rather than to restate it.
# genanki maps a note's values to model fields by INDEX, so for most of this
# project's life the model's field list and the two note builders were three
# separate hand-maintained sequences that had to agree. When they disagreed,
# content was written into the wrong card section silently: no error, no
# warning, and a perfectly valid .apkg. CLAUDE.md 3.2 asked reviewers to
# catch that by reading carefully, which is not a mechanism.
#
# Now the model is generated from this tuple and both builders address
# fields BY NAME through _note_fields(), so:
#   - reordering is a one-line change that cannot desync anything,
#   - appending is safe by construction,
#   - a misspelled field name raises instead of shifting every later field,
#   - an omitted field is empty instead of shifting every later field.
#
# The payload dicts the builders construct are also the natural seam for the
# planned web app and Chrome extension: they are already the card's content
# keyed by name, independent of genanki. Serializing one to JSON instead of
# to this tuple is what lets another surface consume pipeline output without
# reimplementing any of it.
FIELDS: tuple[str, ...] = (
    "Word",
    "Class",
    "Definition",
    "1st Example Sentence",
    "2nd Example Sentence",
    "Example from Youtube Video",
    "Synonyms",
    "Antonyms",
    "VideoID",
    "Source",
    "IPA",             # ADR-009 phase 1
    "Pronunciation",   # ADR-009 phase 1
)

# Anki treats a note's FIRST field as its identity: it is what deduplication
# and the deck duplicate check read (see deck.py and ARCHITECTURE.md 8.22).
# Pinned by a test so it cannot drift to the front of the tuple by accident.
IDENTITY_FIELD = "Word"

# The notetype's name in the collection. A module constant rather than a
# literal in _build_model() because the pre-import field alignment has to
# address the same notetype by name (deck.ensure_model_fields, called from
# __main__ before importPackage). Two copies of this string is the recurring
# bug shape in this codebase -- see SESSION.md 6.12.
MODEL_NAME = "YT Anki Pipeline — Recognition"

# How often the audio download reports in. Downloads are paced to respect the
# host's rate limit, so a first run into an empty deck can spend minutes here;
# without this the CLI looks hung at "Building Anki package...".
PROGRESS_EVERY = 25


def _note_fields(payload: dict[str, str]) -> list[str]:
    """
    Turn a name-keyed payload into the positional list genanki expects.

    Args:
        payload: Field name -> value. Omitted fields become empty strings,
                 which is what a card with no synonyms or no audio wants.

    Returns:
        Values ordered to match FIELDS exactly.

    Raises:
        ValueError: The payload names a field the model does not have.
            Loud on purpose. A typo used to be the worst case here -- it
            silently produced a card with everything after it shifted by
            one -- and this converts that into an immediate failure.
    """
    unknown = sorted(set(payload) - set(FIELDS))
    if unknown:
        raise ValueError(
            f"Unknown card field(s): {', '.join(unknown)}. "
            f"Valid fields are: {', '.join(FIELDS)}."
        )
    return [payload.get(name, "") for name in FIELDS]


# -- Model --------------------------------------------------------------------

def _build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        MODEL_NAME,
        fields=[{"name": name} for name in FIELDS],
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

def _format_pills(words: list[str], css_class: str, max_chars: int = 256) -> str:
    """
    Build pill HTML, skipping any individual pill that would push the
    combined length past max_chars.

    Deliberately does NOT use _truncate() here — that function looks for a
    sentence boundary (". "), which pill HTML never contains, so it always
    falls through to a blind character cut that can slice through the
    middle of a <span> tag and produce invalid, unclosed HTML (issue #11).
    Dropping a whole pill is better UX than a garbled partial one anyway.

    Skips (via `continue`) rather than stops (via `break`) at an oversized
    entry — an early entry that alone doesn't fit must not silently drop
    every entry after it too, including short ones that would fit fine
    (issue #12).
    """
    if not words:
        return ""
    pills: list[str] = []
    total = 0
    for word in words:
        pill = f'<span class="{css_class}">{word}</span>'
        added = len(pill) + (1 if pills else 0)  # +1 for the joining space
        if total + added > max_chars:
            continue
        pills.append(pill)
        total += added
    return " ".join(pills)


def _audio_field(audio_url: Optional[str], media_name: Optional[str] = None) -> str:
    """
    Render the Pronunciation field.

    Args:
        audio_url:  Remote recording URL, or None.
        media_name: Filename of a copy already downloaded into the package's
                    media. When present the card plays it inline.

    Returns:
        An Anki `[sound:...]` tag, a link, or "".

    Embedded when the file was downloaded, linked when it was not, and this
    degrades per card rather than per run. A link opens a browser, which is
    not reviewing; `[sound:]` plays inside the card, works on AnkiDroid and
    AnkiMobile, and keeps working when the source is down.

    v0.5.0 linked because ADR-009 costed embedding at "tens of megabytes" for
    a 400-card deck. Measured, that was wrong: real Commons files are 16-30 KB,
    so a 240-card German deck is about 5 MB. Wikimedia also serves them as MP3
    already, so nothing needs converting.

    The fallback is not decoration. dictionaryapi.dev served one word's audio
    and returned 502 for another in the same minute, so some cards in a real
    run will have a URL and no file.
    """
    if media_name:
        return f"[sound:{media_name}]"
    if not audio_url:
        return ""
    return f'<a class="audio-link" href="{audio_url}">&#9654; Listen</a>'


def _download_audio(
    wanted: dict[str, str],
    language: str,
    max_workers: int = 4,
    progress: Optional[Callable[[str], None]] = None,
) -> tuple[dict[str, str], list[Path]]:
    """
    Fetch every card's pronunciation audio, concurrently.

    Args:
        wanted:      lemma (lower-cased) -> remote audio URL.
        language:    Transcript language, part of each cached filename.
        max_workers: Concurrent downloads.
        progress:    Called with a status line every PROGRESS_EVERY files, so
                     a long paced download is not a silent stall.

    Returns:
        (names, paths). `names` maps lemma -> the filename to put in a
        `[sound:...]` tag, containing only lemmas whose file actually
        arrived. `paths` is what goes into the package's media list.

    A lemma missing from `names` is not an error: its card falls back to a
    link. That happens whenever a source is down, which is routine --
    dictionaryapi.dev served one word and 502'd another in the same minute.

    Nothing here can fail the run. The package is the expensive artefact and
    it is built either way.

    Fewer workers than the definition fetcher on purpose. `media` paces
    requests globally now because the download host rate-limits per IP, so
    extra threads buy nothing beyond overlapping the transfers and only make
    the queue behind the limiter longer.
    """
    if not wanted:
        return {}, []

    names: dict[str, str] = {}
    paths: list[Path] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(media.fetch_audio, url, lemma, language): lemma
            for lemma, url in wanted.items()
        }
        for future in as_completed(futures):
            lemma = futures[future]
            try:
                path = future.result()
            except Exception:
                logger.debug("Audio download raised for '%s'.", lemma, exc_info=True)
                continue
            finally:
                done += 1
                if progress and done % PROGRESS_EVERY == 0:
                    progress(f"  audio {done}/{len(wanted)} ({len(names)} embedded)")
            if path:
                names[lemma] = path.name
                paths.append(path)

    logger.info(
        "Audio: %d of %d cards will play inline; the rest link out.",
        len(names), len(wanted),
    )
    if progress:
        linked = len(wanted) - len(names)
        progress(
            f"Audio: {len(names)} of {len(wanted)} recordings embedded"
            + (f"; {linked} card(s) link out instead." if linked else ".")
        )
    return names, paths


def _build_note(
    result: DefinitionResult,
    model: genanki.Model,
    video_id: str,
    language: str = "en",
    ipa: Optional[str] = None,
    audio_url: Optional[str] = None,
    media_name: Optional[str] = None,
    pos_language: Optional[str] = None,
) -> genanki.Note:
    """
    Build a recognition card. Fields match renamed Anki model fields.

    ipa and audio_url come from the offline index (wiktdata schema v2) and
    are optional: a language with no index, or a word the index has no
    `sounds` block for, simply leaves both fields empty. Measured on the
    real German index, 341 of 342 card words carry IPA and 339 an audio URL.
    """
    synonyms_html = _format_pills(result.synonyms, "vocab-pill")
    antonyms_html = _format_pills(result.antonyms, "antonym-pill")

    return genanki.Note(
        model=model,
        fields=_note_fields({
            "Word":                       result.lemma.capitalize(),
            "Class":                      localise_pos(
                result.part_of_speech, pos_language or language
            ),
            "Definition":                 result.definition,
            "1st Example Sentence":       result.example_dict or "",
            "2nd Example Sentence":       getattr(result, "example_dict2", None) or "",
            "Example from Youtube Video": result.example_transcript or "",
            "Synonyms":                   synonyms_html,
            "Antonyms":                   antonyms_html,
            "VideoID":                    video_id,
            "Source":                     result.source,
            "IPA":                        ipa or "",
            "Pronunciation":              _audio_field(audio_url, media_name),
        }),
        # language is part of the GUID (issue #14) so the same video
        # reprocessed in a second language doesn't collide on a cognate
        # lemma ("train" in English and French both hash the same way
        # without this) and get silently dropped as an existing note.
        guid=genanki.guid_for(result.lemma, video_id, language),
        tags=["yt-anki", video_id],
    )


def _build_fallback_note(
    lemma: str,
    example_transcript: Optional[str],
    model: genanki.Model,
    video_id: str,
    language: str = "en",
    dict_example: Optional[str] = None,
    dict_example2: Optional[str] = None,
    synonyms: Optional[list[str]] = None,
    antonyms: Optional[list[str]] = None,
    ipa: Optional[str] = None,
    audio_url: Optional[str] = None,
    media_name: Optional[str] = None,
) -> genanki.Note:
    """
    Build a fallback card for a lemma with no definition from any source.

    dict_example is an optional native-language example sentence sourced
    from Wiktionary (see definition.DefinitionBatchResult.not_found_examples
    and issue #1) for languages where dictionaryapi.dev has essentially no
    coverage. When present it fills the "1st Example Sentence" field, which
    otherwise stays empty for fallback cards -- the only example a fallback
    card would have without it is whatever transcript sentence contained
    the word, if any.

    synonyms/antonyms are optional native-language words sourced from
    OMW/WordNet (see definition.DefinitionBatchResult.not_found_synonyms/
    not_found_antonyms and ADR-008) for the same no-definition-anywhere
    case dict_example covers -- fetch_definition() never reaches its own
    OMW lookup when there's no definition, so this is the only place a
    fallback card can pick them up.
    """
    return genanki.Note(
        model=model,
        fields=_note_fields({
            "Word":                       lemma.capitalize(),
            "Class":                      "",
            "Definition":                 "No definition found",
            "1st Example Sentence":       dict_example or "",
            "2nd Example Sentence":       dict_example2 or "",
            "Example from Youtube Video": example_transcript or "",
            "Synonyms":                   _format_pills(synonyms or [], "vocab-pill"),
            "Antonyms":                   _format_pills(antonyms or [], "antonym-pill"),
            "VideoID":                    video_id,
            "Source":                     "not_found",
            "IPA":                        ipa or "",
            "Pronunciation":              _audio_field(audio_url, media_name),
        }),
        guid=genanki.guid_for(lemma, video_id, language),
        tags=["yt-anki", video_id, "no-definition"],
    )

# -- Output path --------------------------------------------------------------

def _build_output_path(video_id: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / f"{video_id}_{timestamp}.apkg"

# -- Snippet sentence finder --------------------------------------------------

def _find_in_snippets(
    lemma: str,
    snippets: dict,
    surface_forms: Optional[list] = None,
) -> Optional[str]:
    """
    Return the first transcript line containing this word, or None.

    Searches the lemma first, then the forms the word actually took in the
    transcript. That second pass is what makes this work for inflected
    languages: a French transcript says "sais", the lemma is "savoir", and
    "savoir" never appears in the text at all. Before surface forms were
    threaded through, 137 of 1036 cards on a real French video had an empty
    "Example from Youtube Video" field despite the word being right there
    in the transcript in a conjugated form.

    Matching the lemma first is deliberate: it is the canonical form and,
    where it does appear, gives the cleanest sentence.
    """
    candidates = [lemma]
    for form in surface_forms or []:
        if form and form.lower() != lemma.lower():
            candidates.append(form)

    for candidate in candidates:
        pattern = re.compile(r"\b" + re.escape(candidate) + r"\w*", re.IGNORECASE)
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
    language: str = "en",
    not_found_examples: Optional[dict] = None,
    not_found_examples2: Optional[dict] = None,
    surface_forms: Optional[dict] = None,
    not_found_synonyms: Optional[dict] = None,
    not_found_antonyms: Optional[dict] = None,
    not_found_ipa: Optional[dict] = None,
    not_found_audio: Optional[dict] = None,
    progress: Optional[Callable[[str], None]] = None,
    def_language: Optional[str] = None,
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
        language:   Resolved transcript language code, folded into each
                    note's GUID (issue #14) so the same video processed in
                    two languages doesn't collide on a lemma that happens
                    to be spelled the same in both.
        not_found_examples: definition.DefinitionBatchResult.not_found_examples
                    -- Wiktionary example sentences for not_found lemmas,
                    keyed by lemma (issue #1). A fallback card whose lemma
                    has an entry here gets a real dictionary example instead
                    of an empty "1st Example Sentence" field.
        surface_forms: nlp.process_transcript()'s surface_forms output --
                    lemma -> the forms it actually took in the transcript.
                    Without it, the transcript search looks for a lemma that
                    frequently never appears literally, and the resulting
                    card has no "Example from Youtube Video" at all.
        not_found_examples2: definition.DefinitionBatchResult.not_found_examples2
                    -- the second Wiktionary example, filling the card's
                    dedicated second example field. Previously always blank
                    on fallback cards because only examples[0] was kept.
        not_found_synonyms: definition.DefinitionBatchResult.not_found_synonyms
                    -- OMW/WordNet synonyms for not_found lemmas, keyed by
                    lemma (ADR-008). Same idea as not_found_examples, for
                    the Synonyms field.
        not_found_antonyms: definition.DefinitionBatchResult.not_found_antonyms
                    -- same as not_found_synonyms, for the Antonyms field.
        not_found_ipa: definition.DefinitionBatchResult.not_found_ipa -- IPA
                    for a lemma with no definition, keyed by lemma (ADR-009
                    phase 1). A card with no definition is the one that
                    benefits most from still showing how the word is said.
        not_found_audio: definition.DefinitionBatchResult.not_found_audio --
                    the Commons recording URL, same keying.

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

    # The part of speech is written in whichever language the definition is
    # in, so a card never mixes the two: a German word defined in French
    # reads "nom", and the same word defined natively reads "Substantiv".
    # Constraint 3.3 permits Class to change language for exactly this
    # reason -- it labels the definition, it does not describe how the word
    # sounds or what it means.
    pos_language   = def_language or language
    standard_count = 0
    fallback_count = 0
    skipped_count  = 0

    # Pronunciation audio, downloaded once for the whole package before any
    # note is built, because a note needs to know whether its file exists.
    #
    # Concurrent and bounded, for the same reason definition fetching is: a
    # 240-card German deck is ~228 small files, and doing them one at a time
    # would add minutes to every run. Failures are per file -- the card falls
    # back to a link and the run continues.
    audio_wanted = {r.lemma.lower(): r.audio_url for r in found if r.audio_url}
    for lem, url in (not_found_audio or {}).items():
        audio_wanted.setdefault(lem.lower(), url)
    media_names, media_paths = _download_audio(audio_wanted, language, progress=progress)

    # Tracks every lemma that has already produced a note in this package,
    # independent of definition.fetch_definitions()'s own input-list dedup.
    # Defense in depth (see ARCHITECTURE.md 8.6): a duplicate that slips past
    # that first layer must still not reach the user's Anki deck as two cards.
    added_lemmas: set[str] = set()

    # Standard cards
    for result in found:
        key = result.lemma.lower()
        if key in added_lemmas:
            logger.debug("Skipping duplicate note for '%s'.", result.lemma)
            continue
        added_lemmas.add(key)
        if not result.example_transcript and snippets:
            # fetch_definitions() searched the lemma alone; retry with the
            # forms the word actually took before giving up on the field.
            result.example_transcript = _find_in_snippets(
                result.lemma, snippets, (surface_forms or {}).get(key)
            )
        deck.add_note(_build_note(
            result, model, video_id, language,
            ipa=result.ipa, audio_url=result.audio_url,
            media_name=media_names.get(key),
            pos_language=pos_language,
        ))
        standard_count += 1
        logger.debug("Card built: '%s' (%s)", result.lemma, result.source)

    # Fallback cards
    for lemma in not_found:
        key = lemma.lower()
        if key in added_lemmas:
            logger.debug("Skipping duplicate fallback note for '%s'.", lemma)
            continue
        transcript_example = (
            _find_in_snippets(lemma, snippets, (surface_forms or {}).get(key))
            if snippets else None
        )
        dict_example = (not_found_examples or {}).get(lemma)
        dict_example2 = (not_found_examples2 or {}).get(lemma)
        fallback_synonyms = (not_found_synonyms or {}).get(lemma)
        fallback_antonyms = (not_found_antonyms or {}).get(lemma)
        if not transcript_example and not dict_example:
            logger.warning(
                "Skipping '%s' — no definition, no dictionary example, and "
                "no transcript example.", lemma
            )
            skipped_count += 1
            continue
        added_lemmas.add(key)
        deck.add_note(
            _build_fallback_note(
                lemma, transcript_example, model, video_id, language, dict_example,
                dict_example2, fallback_synonyms, fallback_antonyms,
                ipa=(not_found_ipa or {}).get(lemma),
                audio_url=(not_found_audio or {}).get(lemma),
                media_name=media_names.get(key),
            )
        )
        fallback_count += 1
        logger.debug("Fallback card built: '%s'", lemma)

    total_cards = standard_count + fallback_count
    output_path = _build_output_path(video_id)
    package = genanki.Package(deck)
    # Anki copies these into the collection's media folder on import, which
    # is what makes [sound:...] play rather than show as literal text.
    package.media_files = [str(p) for p in media_paths]
    package.write_to_file(str(output_path))

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