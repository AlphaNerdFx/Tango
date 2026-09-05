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

import logging
from dataclasses import dataclass
from typing import Optional

import requests

from pipeline import TangoError
from pipeline.config import IMAGE_TIMEOUT, WIKTIONARY_USER_AGENT
from pipeline.media import RateLimiter

logger = logging.getLogger(__name__)

_WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"

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


class ImageUnavailableError(TangoError):
    """Raised when the image source is misconfigured, not when it has no image."""


@dataclass
class ImageResult:
    """An image for one lemma, with the attribution its licence requires."""

    url: str
    qid: str
    source: str          # "wikidata" or "wikipedia"
    filename: str


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
})


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
        "piprop": "original",
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
                        ("wikipedia", page.get("original", {}).get("source"))):
        if url:
            return ImageResult(url=url, qid=qid, source=source,
                               filename=url.rsplit("/", 1)[-1].split("?")[0])
    return None


def _commons_url(p18: list[str]) -> Optional[str]:
    """Turn a Wikidata P18 filename into a Commons file URL."""
    if not p18:
        return None
    name = p18[0].replace(" ", "_")
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{name}"
