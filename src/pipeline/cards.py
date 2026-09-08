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
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import genanki

from pipeline import images, media, wiktdata
from pipeline.definition import DefinitionResult
from pipeline.language import localise_pos

logger = logging.getLogger(__name__)

# -- Constants (moved to config.py at end of project) -------------------------
from pipeline import TangoError
from pipeline.config import MODEL_ID, DECK_ID, OUTPUT_DIR



# -- Card CSS ------------------------------------------------------------------

CARD_CSS = """
/* Every size here is clamp(floor, viewport-relative, ceiling).
   The ceiling is what a desktop card has always shown, so nothing looks
   different on a large screen. The floor is where text stops being worth
   reading. Between them the card scales with the viewport it lands in.

   The reason is a full card: word, part of speech, definition, three
   example blocks, synonyms, antonyms, IPA, audio, an image and its credit.
   Measured at the old fixed sizes that stack is roughly 850-900px, which
   scrolls on a phone and on most desktop reviewer windows.

   vh rather than vw, because the thing running out is height, and because a
   narrow phone would otherwise shrink text that had room to be larger. */
/* Anki renders the template inside its own wrapper, `<div id="qa">`, while
   `.card` lands on the body. So the picture is a grandchild of the flex
   column, not a child of it, and `flex` on it would do nothing at all.
   Making the wrapper a flex item too restores the chain. The rule is inert
   where the wrapper does not exist, which is the case in the card layout
   preview and in this project's own preview page.

   This was missed until a review pointed at it on 8 September 2026, and it
   is not verifiable from here: no test can see it, because a stylesheet
   assertion cannot tell whether a rule had an effect. It needs the person
   looking at a deck in Anki that CLAUDE.md §1 already asks for. */
#qa {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
}

.card {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: clamp(14px, 2.1vh, 17px);
    color: var(--fg, #1a1a2e);
    background-color: var(--canvas, #ffffff);
    max-width: 580px;
    margin: 0 auto;
    padding: clamp(10px, 2vh, 24px) 20px;
    line-height: 1.5;
    /* One screen tall, and a column, so the picture can take the height the
       text leaves instead of a share of the viewport decided in advance.

       border-box because the padding has to live inside the 100vh rather
       than on top of it. Without it a card that exactly fills the screen
       scrolls by its own padding, which is the most annoying kind of
       scrolling: a centimetre of nothing under a card that looked fine. */
    box-sizing: border-box;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
}

/* Front, centered word */
.word-front {
    font-size: clamp(26px, 5vh, 36px);
    font-weight: 700;
    text-align: center;
    color: var(--fg, #0f3460);
    padding: clamp(8px, 2vh, 20px) 0 clamp(4px, 1vh, 10px);
    letter-spacing: 0.5px;
}

/* Back, main word */
.word-back {
    font-size: clamp(21px, 4vh, 30px);
    font-weight: 700;
    text-align: center;
    color: var(--fg, #0f3460);
    padding: 10px 0 4px;
}

hr {
    border: none;
    border-top: 1px solid var(--border, #d1d5db);
    margin: clamp(5px, 1.2vh, 12px) 0;
}

.pos {
    font-size: clamp(11px, 1.6vh, 13px);
    color: var(--slightly-grey-text, #6b7280);
    text-align: center;
    font-style: italic;
    margin-bottom: clamp(4px, 1.2vh, 10px);
}

.definition {
    font-size: clamp(13px, 2vh, 16px);
    color: var(--fg, #374151);
    margin-bottom: clamp(6px, 1.8vh, 16px);
    text-align: center;
}

.example-block {
    margin-bottom: 14px;
}

.example {
    font-style: italic;
    color: var(--fg, #4b5563);
    font-size: clamp(12px, 1.8vh, 15px);
    margin-bottom: 3px;
    padding-left: 14px;
    border-left: 3px solid var(--new-count, #00b4d8);
}

/* Image (ADR-009 phase 3). Conditional on the field, so a card without one
   looks exactly as it did before: most vocabulary is not photographable and
   the field is meant to stay empty.

   This is the card's one flexible row, and everything above it is text at
   its natural height. The picture takes what the text leaves: most of the
   screen on a sparse card, a strip on a full one, and either way the card
   ends at the bottom of the screen instead of past it. That replaces a
   fixed 30vh, which could not know whether the text above it had used 20%
   of the screen or 95%.

   Between a floor and a ceiling, both deliberate. Below a sixth of the
   screen a photograph stops teaching anything, so the card scrolls rather
   than showing a sliver: the one place this trades the no-scrolling goal
   for legibility. Above 45vh a sparse card would show a wall-sized
   photograph and the deck would stop looking consistent card to card,
   which is why the box was fixed in the first place on 6 September.

   The credit sits inside this box rather than after it. As a sibling it
   was pushed to the bottom of the screen while the picture stayed at the
   top, leaving the licence line floating alone under empty space. */
.card-image {
    margin-top: 16px;
    flex: 1 1 auto;
    min-height: 16vh;
    max-height: 45vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    text-align: center;
}

/* Sized to the box above, which is sized to what is left of the screen.

   object-fit: contain letterboxes inside the box, so every aspect ratio
   survives. cover would fill it by cropping, and cropping a photograph
   chosen to show one thing can cut that thing out of frame. */
.card-image img {
    /* Two bounds in one, and both are needed. The percentage tracks the box
       when the box has a definite height, which is what makes the picture
       yield to the text. The viewport half is what survives when it does
       not: a percentage against an indefinite height computes to `none`, and
       an unbounded 480px photograph is exactly the overflow this was meant
       to remove. */
    max-height: min(100%, 45vh);
    max-width: 100%;
    width: auto;
    height: auto;
    object-fit: contain;
    border-radius: 6px;
}

/* On a short screen, tighter lines.

   The picture already yields continuously, so it needs nothing here. What
   is left is the text, and 1.4 buys back a line or two on a phone without
   touching any font size. Anki's clients are webviews, so a height media
   query is available and needs no JavaScript, which this project has never
   shipped in a template and could not verify on every device. */
@media (max-height: 640px) {
    .card { line-height: 1.4; }
}

.attribution {
    font-size: clamp(9px, 1.3vh, 11px);
    opacity: 0.6;
    margin-top: 4px;
    text-align: center;
}

.example-source {
    font-size: clamp(9px, 1.3vh, 11px);
    color: var(--slightly-grey-text, #9ca3af);
    padding-left: 14px;
    margin-bottom: 0;
}

.section-label {
    font-size: clamp(10px, 1.4vh, 11px);
    font-weight: 700;
    text-transform: uppercase;
    color: var(--slightly-grey-text, #9ca3af);
    letter-spacing: 0.8px;
    margin: clamp(5px, 1.5vh, 14px) 0 clamp(2px, 0.6vh, 6px);
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
    font-size: clamp(12px, 1.8vh, 15px);
    color: var(--fg, #0f3460);
    font-family: "Charis SIL", "Doulos SIL", "Gentium Plus", serif;
    margin-bottom: 6px;
}

/* Used by the template at the Pronunciation section and, until now, never
   defined, so the audio control sat unstyled. */
.pronunciation {
    text-align: center;
    margin-bottom: clamp(2px, 0.8vh, 6px);
}

.audio-link {
    font-size: clamp(10px, 1.5vh, 12px);
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
    font-size: clamp(11px, 1.6vh, 13px);
}

.antonym-pill {
    background: transparent;
    color: var(--fg, #92400e);
    border: 1px solid var(--fg, #92400e);
    border-radius: 12px;
    padding: 2px 12px;
    font-size: clamp(11px, 1.6vh, 13px);
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

{{#Image}}
<div class="card-image">{{Image}}
{{#Attribution}}<div class="attribution">{{Attribution}}</div>{{/Attribution}}
</div>
{{/Image}}

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
    "Image",           # ADR-009 phase 3
    "Attribution",     # ADR-009 phase 3, the licence obligation for Image
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

    Deliberately does NOT use _truncate() here, that function looks for a
    sentence boundary (". "), which pill HTML never contains, so it always
    falls through to a blind character cut that can slice through the
    middle of a <span> tag and produce invalid, unclosed HTML (issue #11).
    Dropping a whole pill is better UX than a garbled partial one anyway.

    Skips (via `continue`) rather than stops (via `break`) at an oversized
    entry, an early entry that alone doesn't fit must not silently drop
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


def _image_field(image_name: Optional[str]) -> str:
    """
    Render the Image field.

    Args:
        image_name: Filename of an image already downloaded into the
                    package's media, or None.

    Returns:
        An `<img>` tag, or "".

    No link fallback, deliberately, and this is the one place images differ
    from audio. A missing recording still leaves a word worth reviewing, so
    _audio_field degrades to a link. A picture that has to be clicked is not
    a picture on a card: it interrupts the review it was meant to help. An
    empty field is the better failure, and the gate in images.py means most
    cards get one anyway.
    """
    if not image_name:
        return ""
    return f'<img src="{image_name}">'


def _download_images(
    wanted: list[str],
    language: str,
    progress: Optional[Callable[[str], None]] = None,
    max_workers: int = 4,
    senses: Optional[dict[str, tuple[str, str]]] = None,
    definition_language: Optional[str] = None,
) -> tuple[dict[str, str], dict[str, str], list[Path]]:
    """
    Resolve and download an image per lemma.

    Args:
        wanted:   Lemmas to try. A list, not a {lemma: url} map like
                  _download_audio takes, because there is no URL yet:
                  resolving the lemma to a concept is most of the work.
        language: BCP-47 code, used to pick which Wikipedia to ask.
        progress: Optional callback for status lines.
        senses:   {lemma: (definition, part of speech)} for the cards about
                  to be built. Given these, a picture whose concept
                  describes a different sense than its card is dropped
                  before it is downloaded. Omitting them keeps every
                  picture the gate admitted.
        definition_language: The language `senses` is written in, which is
                  the definition's language rather than the transcript's.

    Returns:
        ({lemma: filename}, {lemma: credit line}, [paths for the package])

    Resolution is batched and downloads are threaded, which are two different
    problems. Resolution is rate-limited by Wikimedia's pacing rather than by
    latency, so threads buy nothing and 50-item requests buy everything: a
    400-noun deck costs about 24 requests instead of 1600. Downloads are
    per-file and independent, so they thread like the audio ones.

    A lemma absent from the result is the normal case, not an error. The gate
    in images.py refuses most words on purpose, and refusing is the feature.

    Nothing here can fail the run, matching _download_audio: the package is
    the expensive artefact and it is built either way.
    """
    if not wanted:
        return {}, {}, []

    try:
        found = images.find_images(wanted, language,
                                   description_language=definition_language or language)
    except Exception:
        logger.debug("Image resolution raised for %s.", language, exc_info=True)
        return {}, {}, []

    # A picture and a definition are chosen by two routes that never speak to
    # each other, so they can describe different senses of the same spelling:
    # `palais` gets a photograph of a building beside the roof of the mouth.
    # Dropping the picture is the cheaper half of that disagreement to fix,
    # and it happens before the download rather than after.
    if senses:
        contradicted = [
            lemma for lemma, result in found.items()
            if wiktdata.describes_other_sense(
                lemma, definition_language or language,
                senses.get(lemma, ("", ""))[0], result.description,
                senses.get(lemma, ("", ""))[1] or None,
            )
        ]
        for lemma in contradicted:
            logger.info("Image for '%s' shows another sense (%s), dropped.",
                        lemma, found[lemma].description)
            del found[lemma]
        # Named, not counted. A run that says "4 dropped" tells nobody
        # whether the rule is working; the same run saying "anime, est,
        # grève, palais" can be checked against the cards in a second.
        # v0.6.0 made the definition phase name its misses for this reason.
        if contradicted and progress:
            progress(f"  images {len(contradicted)} dropped, the picture showed "
                     f"another sense: {', '.join(sorted(contradicted))}")

    if progress:
        progress(f"  images {len(found)}/{len(wanted)} words have one, fetching")

    names: dict[str, str] = {}
    credits: dict[str, str] = {}
    paths: list[Path] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(images.fetch_image, result, lemma): lemma
                   for lemma, result in found.items()}
        for future in as_completed(futures):
            lemma = futures[future]
            try:
                path = future.result()
            except Exception:
                logger.debug("Image download raised for '%s'.", lemma, exc_info=True)
                continue
            if path:
                names[lemma] = path.name
                credits[lemma] = found[lemma].attribution
                paths.append(path)

    logger.info("Images: %d of %d candidate words got one.", len(names), len(wanted))
    return names, credits, paths


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
    image_name: Optional[str] = None,
    attribution: Optional[str] = None,
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
            "Image":                      _image_field(image_name),
            "Attribution":                attribution or "",
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
    image_name: Optional[str] = None,
    attribution: Optional[str] = None,
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
            "Image":                      _image_field(image_name),
            "Attribution":                attribution or "",
        }),
        guid=genanki.guid_for(lemma, video_id, language),
        tags=["yt-anki", video_id, "no-definition"],
    )

# -- Output path --------------------------------------------------------------

class PackageWriteError(TangoError):
    """
    Raised when the .apkg cannot be created or written.

    v0.9.0. This is the worst place in the pipeline to fail with a
    traceback, because it is the last: the transcript is fetched, every
    definition is looked up and paid for, the audio is downloaded, and then
    a full disk or a read-only output directory threw a bare OSError and
    took the whole run with it.
    """


def _build_output_path(video_id: str) -> Path:
    """
    Build the output path, creating the directory if it is missing.

    Raises:
        PackageWriteError: The output directory cannot be created.
    """
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PackageWriteError(
            f"Cannot create the output directory '{OUTPUT_DIR}': {exc}\n"
            f"  Check the path is writable, or set OUTPUT_DIR in .env to "
            f"somewhere it is."
        ) from exc
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

    # The snippet list is built once, not once per candidate. `snippets`
    # carries three string keys alongside the float-keyed lines, so the
    # isinstance filter used to run for every candidate of every word:
    # 83,772 of them on a 400-card deck.
    #
    # Each text is lowercased once here and used as a cheap pre-filter below.
    # casefold rather than lower for the pre-filter, and deliberately: it is
    # the more aggressive folding, so it matches in strictly more cases. A
    # pre-filter is only safe if it never skips something the regex would
    # have found, and German is the example that matters here, where
    # casefold maps "straße" and "STRASSE" together while lower does not.
    lines = [(val.get("text", ""), val.get("text", "").casefold())
             for key, val in snippets.items() if isinstance(key, float)]

    for candidate in candidates:
        # A substring test before the regex. The regex is the authority,
        # because it anchors on a word boundary and `\w*` lets "wort" match
        # "worten"; but a word that is not present as a substring cannot
        # match it, and that is the overwhelmingly common case. This skips
        # both the search and the compile for those, and compiling was
        # measured at 30% of this function's time.
        needle = candidate.casefold()
        if not any(needle in lowered for _, lowered in lines):
            continue
        pattern = re.compile(r"\b" + re.escape(candidate) + r"\w*", re.IGNORECASE)
        for text, lowered in lines:
            if needle in lowered and pattern.search(text):
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
        skipped_count:   Lemmas dropped entirely, no definition AND no
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
    images_enabled: Optional[bool] = None,
) -> PackageResult:
    """
    Build an Anki .apkg package from definition results.

    Args:
        video_id:   YouTube video ID, used in filename, GUID, and tags.
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
        images_enabled: True or False to decide pictures for this package,
                    None to let IMAGES_ENABLED decide. That is what `--images`
                    and `--no-images` pass, and the third state is the point:
                    a user with IMAGES_ENABLED=true still needs a way to
                    build one deck without them.

    Returns:
        PackageResult with the output path and accurate card counts.
        total_cards = standard_count + fallback_count (these are NOT
        additive with skipped_count, skipped words produce no card
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

    # Images are off unless this run asks for them. The gate in images.py
    # costs a network round trip per 50 words to say "no" to most of them,
    # and a picture roughly doubles a deck that already carries audio (4.8 MB
    # of 10.1 MB, measured on a real 299-card run), so it is not a cost to
    # impose on somebody who did not ask.
    image_names: dict[str, str] = {}
    image_credits: dict[str, str] = {}
    # Imported here rather than at module scope so a test (and a user's
    # .env) can change it without reimporting this module. Same pattern and
    # same reason as transcript._build_proxy().
    from pipeline.config import IMAGES_ENABLED

    # Three states, not two: `--images` and `--no-images` both override the
    # environment for one run, and no flag at all leaves IMAGES_ENABLED to
    # decide. Without the third state a user who put IMAGES_ENABLED=true in
    # .env would have no way to turn them off for a single deck.
    use_images = IMAGES_ENABLED if images_enabled is None else images_enabled

    if use_images:
        candidates = [r.lemma.lower() for r in found]
        # `not_found`, the lemmas that get a fallback card, and not
        # `not_found_audio`, which is the Commons recording map. Copying the
        # audio block above cost every fallback card without a recording its
        # chance at a picture, silently, since a missing image and a refused
        # one look identical from outside.
        candidates += [lem.lower() for lem in not_found]
        # What each card will actually say, so a picture of another sense can
        # be dropped before it is downloaded. A fallback card has no
        # definition to disagree with and so is not listed here.
        senses = {r.lemma.lower(): (r.definition, r.part_of_speech) for r in found}
        image_names, image_credits, image_paths = _download_images(
            sorted(set(candidates)), language, progress=progress,
            senses=senses, definition_language=pos_language,
        )
        media_paths = media_paths + image_paths

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
            image_name=image_names.get(key),
            attribution=image_credits.get(key),
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
                "Skipping '%s', no definition, no dictionary example, and "
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
                image_name=image_names.get(key),
                attribution=image_credits.get(key),
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
    try:
        package.write_to_file(str(output_path))
    except OSError as exc:
        # Everything expensive has already been paid for by this point, so
        # the message says what was lost and what to do about it rather
        # than only what failed.
        raise PackageWriteError(
            f"Cannot write the package to '{output_path}': {exc}\n"
            f"  {total_cards} cards were built and are not saved. Free some "
            f"space or set OUTPUT_DIR in .env to a writable path, then "
            f"re-run with --force."
        ) from exc

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
