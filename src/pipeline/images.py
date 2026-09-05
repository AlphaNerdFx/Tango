"""
Card images, gated to concepts that can actually be photographed.

ADR-009 phase 3, redesigned after the 5 September 2026 measurement. The ADR
proposed Wikimedia Commons *search*, which is why `laufen` returned a coin
from the town of Laufen: a text search matches a spelling, not a meaning.

This resolves a lemma to a concept instead, and asks that concept for its
image:

    lemma -> Wikipedia article (language-specific)
          -> Wikidata item      (language-independent)
          -> P18 image, else the article's lead image

The middle step is what makes German work. `Hund` and `chien` both resolve
to Q144, so one judgement about whether dogs are photographable serves every
language. German has no WordNet in OMW at all, so the concreteness gate in
definition.py cannot judge it, and that gap is why images reached 0% of
German cards.

Verbs and adjectives fall out for free, measured: `laufen` and `schwierig`
have no lead image and no article respectively. That is the failure mode
ADR-009 wanted, an empty field rather than a wrong picture.

Sources meet ADR-008's bar: free, no API key, no registration, and licensed
for redistribution. Wikipedia and Commons files are CC or public domain,
Wikidata's own data is CC0. Unsplash and Pixabay were rejected for requiring
a key, the same reason ADR-008 rejected PONS.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from pipeline import TangoError
from pipeline.config import IMAGE_DIR, IMAGE_TIMEOUT, WIKTIONARY_USER_AGENT
from pipeline.media import RateLimiter

logger = logging.getLogger(__name__)

_WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# extmetadata returns Artist as an HTML fragment, usually a link to the
# uploader's user page. The card wants the name, not the markup.
_TAG_RE = re.compile(r"<[^>]+>")

# Wikimedia rejects the default python-requests User-Agent with 403, per
# their User-Agent policy. Measured here during planning: the same request
# curl serves happily returns "Please set a user-agent and respect our robot
# policy" without one. media.py hit this first; the same identifying agent
# applies, since it is the same operator calling the same foundation.
_HEADERS = {"User-Agent": WIKTIONARY_USER_AGENT}

# Two calls per lemma against Wikimedia, so paced like the audio downloads
# in media.py rather than left to run flat out. Shared limiter class, not a
# second implementation.
_LIMITER = RateLimiter(1.0, 4)

# Commons serves originals, and they are enormous: the first real download
# during development was 9.2 MB for one photograph of a dog. The card caps
# display at 240px, so a full-resolution file is bandwidth and disk spent on
# pixels nobody sees, and a 100-image deck would have been near a gigabyte.
# 480 covers a high-DPI screen at that size with room to spare.
#
# The same mistake as ADR-009's audio estimate in 8.35, caught earlier this
# time because the download was run rather than reasoned about.
_THUMB_WIDTH = 480


class ImageUnavailableError(TangoError):
    """Raised when the image source is misconfigured, not when it has no image."""


@dataclass
class ImageResult:
    """An image for one lemma, with the attribution its licence requires."""

    url: str
    qid: str
    source: str          # "wikidata" or "wikipedia"
    filename: str
    attribution: str = ""   # rendered credit line, "" when Commons has none


# Wikidata P31 (instance of) classes that mark a concept as unphotographable.
# Measured, not guessed: Q2979 (freedom) carries Q840396 and Q1207505, and
# is the item both `Freiheit` and `liberté` resolve to. Wikipedia will
# happily hand back the Statue of Liberty for it, which is a photograph of a
# statue, not of freedom.
_ABSTRACT_CLASSES: frozenset[str] = frozenset({
    "Q840396",     # ideal
    "Q1207505",    # quality
    "Q129510955",  # type of value
    "Q151885",     # concept
    "Q9415",       # emotion
    "Q23009552",   # mental process
    "Q4198319",    # human activity
    "Q1914636",    # activity
    "Q17008256",   # occurrence
    "Q26907166",   # temporal entity
    # Added 6 September 2026 after looking at the pictures rather than the
    # rates. The first measurement admitted 36.9% of nouns, which read as a
    # success until the files were opened: `leben` got a photograph of a
    # newborn, `cowardice` the Cowardly Lion, `loi` the Palais-Bourbon, and
    # `government` a group portrait of Dutch ministers. Every one of those
    # is the wrong-association failure this module exists to prevent, and
    # each was reached through one of the classes below.
    "Q96253971",   # type of property        (leben -> a newborn)
    "Q1322005",    # natural phenomenon      (leben)
    "Q2996394",    # biological process      (leben)
    "Q33742",      # natural language        (englisch -> an 1828 spelling book)
    "Q1288568",    # modern language
    "Q34770",      # language
    "Q2393196",    # personality trait       (cowardice -> the Cowardly Lion)
    "Q17197366",   # type of organization    (government -> Dutch ministers)
    "Q33104303",   # concept in physics      (force -> a vector diagram)
    "Q15617994",   # administrative territorial entity type  (country -> a world map)
    "Q2135465",    # legal term or concept   (loi -> a building)
    "Q10541491",   # legal form              (corporation -> a painting)
})

# A disambiguation page is not a concept at all, and its "image" belongs to
# whichever sense Wikipedia listed first. `couple` reached a photograph of a
# Bolero choreography this way.
_DISAMBIGUATION = "Q4167410"


def _get(url: str, params: dict) -> Optional[dict]:
    """One paced, identified GET returning parsed JSON, or None on any failure."""
    _LIMITER.acquire()
    try:
        response = requests.get(url, params=params, headers=_HEADERS,
                                timeout=IMAGE_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        # Same posture as media.py: an image is an enhancement, and no
        # enhancement is worth failing a run that has already paid for a
        # transcript and a thousand definitions.
        logger.debug("Image lookup failed for %s: %s", params.get("titles", url), exc)
        return None


def _article(lemma: str, language: str) -> Optional[dict]:
    """
    The Wikipedia page for `lemma`, following redirects.

    Returns the page dict, or None when the article does not exist, which is
    itself a useful answer: `schwierig` has no article, so no image, so no
    wrong picture on an adjective's card.
    """
    data = _get(_WIKIPEDIA_API.format(lang=language.split("-")[0]), {
        "action": "query",
        "redirects": 1,
        "titles": lemma,
        "prop": "pageimages|pageprops",
        "piprop": "thumbnail",
        "pithumbsize": _THUMB_WIDTH,
        "ppprop": "wikibase_item",
        "format": "json",
    })
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None
    page = next(iter(pages.values()))
    return None if "missing" in page else page


def _claims(qid: str, prop: str) -> list[str]:
    """The values of one Wikidata property, as ids or strings."""
    data = _get(_WIKIDATA_API, {
        "action": "wbgetclaims", "entity": qid, "property": prop, "format": "json",
    })
    if not data:
        return []
    out: list[str] = []
    for claim in data.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and "id" in value:
            out.append(value["id"])
        elif isinstance(value, str):
            out.append(value)
    return out


def is_photographable(qid: str) -> bool:
    """
    Whether a Wikidata concept is a thing rather than an idea.

    Refuses when *any* `instance of` claim is abstract, and when there are no
    claims at all. Deliberately strict in the same way as
    definition.is_concrete_noun: an empty image field costs a learner
    nothing, a wrong one costs them the association.

    Language-independent, which is the whole point. The judgement attaches to
    the concept, so it holds for every language that reaches the same item.
    """
    classes = _claims(qid, "P31")
    if not classes:
        return False
    if _DISAMBIGUATION in classes:
        return False
    return not any(c in _ABSTRACT_CLASSES for c in classes)


def find_image(lemma: str, language: str) -> Optional[ImageResult]:
    """
    Find an image for `lemma`, or None if there is no defensible one.

    Args:
        lemma:    The word, in its own language.
        language: BCP-47 code of that language.

    Returns:
        An ImageResult, or None. None is the common and correct answer: most
        vocabulary is not photographable, and the field is meant to stay
        empty for those.
    """
    page = _article(lemma, language)
    if page is None:
        return None

    qid = page.get("pageprops", {}).get("wikibase_item")
    if not qid:
        # No Wikidata item means no way to judge the concept, and an
        # ungated image is the thing this module exists to avoid.
        return None

    if not is_photographable(qid):
        return None

    # Wikidata's own image is the curated one; the article's lead image is
    # the fallback. Both live on Commons, so both are already licensed.
    for source, url in (("wikidata", _commons_url(_claims(qid, "P18"))),
                        ("wikipedia", page.get("thumbnail", {}).get("source"))):
        if url:
            name = url.rsplit("/", 1)[-1].split("?")[0]
            return ImageResult(url=url, qid=qid, source=source, filename=name,
                               attribution=attribution(name))
    return None


def _commons_url(p18: list[str]) -> Optional[str]:
    """Turn a Wikidata P18 filename into a Commons file URL."""
    if not p18:
        return None
    name = p18[0].replace(" ", "_")
    # ?width= asks Commons for a thumbnail rather than the original.
    return (f"https://commons.wikimedia.org/wiki/Special:FilePath/{name}"
            f"?width={_THUMB_WIDTH}")


def attribution(filename: str) -> str:
    """
    The credit line a Commons file's licence requires, or "".

    Not decoration. Commons reports `AttributionRequired: true` on the images
    this module actually returns: the dog photograph for Q144 is CC BY-SA 2.0
    by Markus Trienke, and shipping it in a deck without naming them would
    breach the licence. ADR-008's bar asks for redistributable sources, and
    redistributable does not mean unconditional.

    Degrades to "" rather than raising, like everything else here, but note
    the caller must then decide: an image whose attribution could not be
    fetched is one whose licence terms are unknown.
    """
    data = _get(_COMMONS_API, {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "extmetadata",
        "format": "json",
    })
    if not data:
        return ""
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return ""
    info = next(iter(pages.values())).get("imageinfo")
    if not info:
        return ""
    meta = info[0].get("extmetadata", {})

    def field(key: str) -> str:
        raw = str(meta.get(key, {}).get("value", ""))
        return html.unescape(_TAG_RE.sub("", raw)).strip()

    artist, licence = field("Artist"), field("LicenseShortName")
    if artist and licence:
        return f"{artist}, {licence}"
    return artist or licence


def _extension(url: str) -> str:
    """The file extension Anki needs to render the image, defaulting to .jpg."""
    tail = url.rsplit("/", 1)[-1].split("?")[0].lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
        if tail.endswith(ext):
            return ext
    return ".jpg"


def fetch_image(result: ImageResult, lemma: str) -> Optional[Path]:
    """
    Download an image into the cache, returning its path or None.

    Cached by Wikidata id rather than by lemma, because the concept is what
    the image belongs to: `Hund` and `chien` resolve to Q144 and share one
    file rather than downloading it twice.

    Never raises. An image is an enhancement, and a run that has already paid
    for a transcript and a thousand definitions must not fail for want of a
    picture. Same posture as media.fetch_audio.
    """
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = IMAGE_DIR / f"{result.qid}{_extension(result.url)}"
    if path.exists():
        return path

    _LIMITER.acquire()
    try:
        response = requests.get(result.url, headers=_HEADERS,
                                timeout=IMAGE_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("Image download failed for '%s' (%s): %s", lemma, result.url, exc)
        return None

    if not response.content:
        return None

    try:
        path.write_bytes(response.content)
    except OSError as exc:
        logger.debug("Could not cache image for '%s': %s", lemma, exc)
        return None

    logger.debug("Cached %s (%d bytes) for '%s'.", path.name, len(response.content), lemma)
    return path
