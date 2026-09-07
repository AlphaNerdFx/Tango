"""
Card images, gated to concepts that can actually be photographed.

ADR-009 phase 3, redesigned after the 5 September 2026 measurement. The ADR
proposed Wikimedia Commons *search*, which is why `laufen` returned a coin
from the town of Laufen: a text search matches a spelling, not a meaning.

This resolves a lemma to a concept instead, and asks that concept for its
image:

    lemma -> Wikipedia article (language-specific)
          -> Wikidata item      (language-independent)
          -> P31 gate, or P279 when the item has no P31
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
from urllib.parse import unquote, urlsplit

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
    # The concept's one-line Wikidata description, in whichever language was
    # asked for. Carried so the caller can ask whether the picture and the
    # definition on the card describe the same sense: `palais` resolves to a
    # building while the card's first index row is the roof of the mouth.
    # Empty when Wikidata has no description in that language.
    description: str = ""


# Wikidata classes that mark a concept as unphotographable. Read from
# `instance of`, and from `subclass of` when an item has no `instance of`,
# which is the usual shape for a common noun. See _admits.
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


def _admits(p31: list[str], p279: list[str]) -> bool:
    """
    The gate itself, over one item's classes. Shared by both lookup paths.

    `instance of` is asked first and `subclass of` only when it is empty,
    which is not a fallback for tidiness but for how Wikidata models
    vocabulary. A common noun is a *class*: `fleur` is Q506, flower, and a
    class is described by what it is a subclass of, not by what it is an
    instance of. Measured 7 September 2026 over the definition cache, 127 of
    785 nouns had no `instance of` at all, and 101 of those had both a
    `subclass of` and a picture, among them bonbon, coussin, apfelsaft,
    kühlschrank and frühstück. That was the single largest recoverable loss
    in the funnel, larger than the denylist and larger than missing files.

    The same denylist applies to whichever list is used, so an abstract
    superclass refuses the item exactly as an abstract class does.

    Language-independent, which is the whole point. The judgement attaches to
    the concept, so it holds for every language that reaches the same item.
    """
    # A disambiguation page is not a concept, so it is refused before
    # anything else: its "image" belongs to whichever sense Wikipedia
    # happened to list first.
    if _DISAMBIGUATION in p31:
        return False
    classes = p31 or p279
    if not classes:
        return False
    return not any(c in _ABSTRACT_CLASSES for c in classes)


def is_photographable(qid: str) -> bool:
    """
    Whether a Wikidata concept is a thing rather than an idea.

    Refuses when *any* class is abstract, and when the item carries no
    classes at all. Deliberately strict in the same way as
    definition.is_concrete_noun: an empty image field costs a learner
    nothing, a wrong one costs them the association.

    Costs a second request only for the items that need it, since
    `subclass of` is fetched only when `instance of` is empty.
    """
    p31 = _claims(qid, "P31")
    return _admits(p31, [] if p31 else _claims(qid, "P279"))


def _description(qid: str, language: str) -> str:
    """One item's short description in `language`, or "" when it has none."""
    data = _get(_WIKIDATA_API, {
        "action": "wbgetentities", "ids": qid, "props": "descriptions",
        "languages": language.split("-")[0], "format": "json",
    })
    if not data:
        return ""
    entity = data.get("entities", {}).get(qid, {})
    value = entity.get("descriptions", {}).get(language.split("-")[0], {}).get("value", "")
    return value if isinstance(value, str) else ""


def find_image(lemma: str, language: str,
               description_language: Optional[str] = None) -> Optional[ImageResult]:
    """
    Find an image for `lemma`, or None if there is no defensible one.

    Args:
        lemma:    The word, in its own language.
        language: BCP-47 code of that language.
        description_language: Which language to fetch the concept's
                  description in, for a caller comparing it against a
                  definition. Defaults to `language`; pass the definition's
                  language under --def-lang, since the two differ there.

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
            name = _commons_filename(url)
            return ImageResult(url=url, qid=qid, source=source, filename=name,
                               attribution=attribution(name),
                               description=_description(
                                   qid, description_language or language))
    return None


def _commons_filename(url: str) -> str:
    """
    The Commons file a URL refers to, not the name of a rendition of it.

    Wikidata's route gives a file URL and the last segment is the file. The
    Wikipedia lead-image route gives a *thumbnail* URL, whose last segment is
    the rendition:

        .../thumb/c/c8/Scout_Girl.jpg/500px-Scout_Girl.jpg

    Asking Commons about `500px-Scout_Girl.jpg` finds no page and returns no
    credit, so a picture shipped with an empty Attribution field. Measured
    7 September 2026 on a real review deck: 4 of 15 pictures, every one of
    them from this route, and the files are CC BY-SA. The credit is a licence
    obligation, so this is a licence bug rather than an untidy card.

    Same failure as the underscores in `_attributions`, a different cause,
    and the reason both are tested: an empty credit looks like "Commons has
    none" from the outside.

    Percent-escapes are decoded, because the API wants a title and not a URL
    path: `%E5%9B%A0...` is a real file name here, not an encoding artefact.
    """
    parts = [p for p in unquote(urlsplit(url).path).split("/") if p]
    if not parts:
        return ""
    return parts[-2] if "thumb" in parts and len(parts) >= 2 else parts[-1]


def _commons_url(p18: list[str]) -> Optional[str]:
    """Turn a Wikidata P18 filename into a Commons file URL."""
    if not p18:
        return None
    name = p18[0].replace(" ", "_")
    # ?width= asks Commons for a thumbnail rather than the original.
    return (f"https://commons.wikimedia.org/wiki/Special:FilePath/{name}"
            f"?width={_THUMB_WIDTH}")


def _credit_line(meta: dict) -> str:
    """
    Build one card-safe credit line from a Commons extmetadata block.

    Shared by the single and batched paths so the two cannot disagree about
    what a credit looks like.

    Commons Artist fields are HTML and are not always short: a derivative
    work credits every source it was built from, and `Wasser` returns four
    lines naming three photographers. A newline inside a card field breaks
    the layout, so whitespace is collapsed and a very long credit is cut.
    Trimming a credit is acceptable where dropping it is not: the licence
    asks for attribution, and a truncated name still attributes.
    """
    def field(key: str) -> str:
        raw = str(meta.get(key, {}).get("value", ""))
        text = html.unescape(_TAG_RE.sub("", raw))
        return " ".join(text.split())

    artist, licence = field("Artist"), field("LicenseShortName")
    if len(artist) > 120:
        artist = artist[:117].rstrip(" ,;:") + "..."
    if artist and licence:
        return f"{artist}, {licence}"
    return artist or licence


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
    return _credit_line(info[0].get("extmetadata", {}))


# What a file actually is, read from its first bytes. Anki picks a renderer
# from the extension, so a name that disagrees with the content shows a
# broken-image icon and nothing else.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"<svg", ".svg"),
    (b"<?xml", ".svg"),
)

# Content-Type is the cheaper answer when the server sends a usable one.
_CONTENT_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def _extension(url: str) -> str:
    """
    The extension implied by a URL, which is a guess and not the answer.

    Kept for the cache-hit path, where there are no bytes to inspect yet.
    Anything that has actually been downloaded should be named by
    `_extension_for()` instead, because the URL routinely lies: see there.
    """
    tail = url.rsplit("/", 1)[-1].split("?")[0].lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
        if tail.endswith(ext):
            return ext
    return ".jpg"


def _extension_for(content: bytes, content_type: str, url: str) -> str:
    """
    Name a downloaded file after what it is, not after where it came from.

    The URL is the least reliable of the three, and this is measured rather
    than cautious. Commons *rasterises* an SVG when a width is requested, so
    `Special:FilePath/Procent-teken.svg?width=480` answers
    `Content-Type: image/png` with PNG bytes. Naming that file `.svg` from
    the URL is why the `pourcent` card showed a broken-image icon: Anki read
    the extension, chose an SVG renderer, and got PNG.

    Order: the magic bytes, which cannot be wrong; then Content-Type; then
    the URL as a last resort.
    """
    for signature, extension in _MAGIC:
        if content.startswith(signature):
            return extension

    base = (content_type or "").split(";")[0].strip().lower()
    if base in _CONTENT_TYPES:
        return _CONTENT_TYPES[base]

    return _extension(url)


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

    # Any extension, because the file is named after its content and the URL
    # cannot predict that. Globbing the id is what makes a cache hit work
    # when the URL says .svg and the bytes on disk are .png.
    for cached in sorted(IMAGE_DIR.glob(f"{result.qid}.*")):
        return cached

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

    extension = _extension_for(response.content,
                               response.headers.get("Content-Type", ""),
                               result.url)
    path = IMAGE_DIR / f"{result.qid}{extension}"
    try:
        path.write_bytes(response.content)
    except OSError as exc:
        logger.debug("Could not cache image for '%s': %s", lemma, exc)
        return None

    logger.debug("Cached %s (%d bytes) for '%s'.", path.name, len(response.content), lemma)
    return path


# ── Batched resolution ────────────────────────────────────────────────────────
#
# The per-lemma path above costs four Wikimedia requests, paced at one a
# second because Wikimedia asks callers to pace. Measured 6 September 2026,
# that is about 27 minutes for a 400-noun deck, which is a real reason not to
# turn images on by default.
#
# All three APIs accept up to 50 items per request, and one Wikidata call
# returns P31 and P18 together, so the same deck costs about 24 requests
# instead of 1600. Measured on 8 German words: one Wikipedia request in
# 0.70s and one Wikidata request in 1.71s, against roughly 32 seconds for
# the same work one lemma at a time.

_BATCH = 50


def _chunks(items: list[str], size: int = _BATCH):
    """Yield successive `size`-length slices."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _articles(lemmas: list[str], language: str) -> dict[str, dict]:
    """
    Wikipedia pages for many lemmas at once, keyed by the lemma asked for.

    Wikipedia normalises titles and follows redirects, so the page that comes
    back is often filed under a different name than the one requested. The
    response carries `normalized` and `redirects` tables for exactly this,
    and both are followed here so a caller still gets its own lemma back as
    the key.
    """
    data = _get(_WIKIPEDIA_API.format(lang=language.split("-")[0]), {
        "action": "query",
        "redirects": 1,
        "titles": "|".join(lemmas),
        "prop": "pageimages|pageprops",
        "piprop": "thumbnail",
        "pithumbsize": _THUMB_WIDTH,
        "ppprop": "wikibase_item",
        "format": "json",
    })
    if not data:
        return {}
    query = data.get("query", {})

    # Walk requested title -> normalised -> redirect target.
    alias: dict[str, str] = {}
    for entry in query.get("normalized", []):
        alias[entry["to"]] = entry["from"]
    for entry in query.get("redirects", []):
        alias[entry["to"]] = alias.get(entry["from"], entry["from"])

    out: dict[str, dict] = {}
    for page in query.get("pages", {}).values():
        if "missing" in page:
            continue
        title = page.get("title", "")
        out[alias.get(title, title)] = page
    return out


def _entities(qids: list[str], description_language: str = "") -> dict[str, dict]:
    """
    The classes, the image and the description for many items in one request.

    Returns {qid: {"P31": [...], "P279": [...], "P18": [...],
    "description": str}}. One call rather than four per item, which is where
    most of the saving comes from. `P279` and the description ride along free
    of charge: the request is the cost, not the fields on it.

    Args:
        qids: Wikidata item ids, at most _BATCH of them.
        description_language: Language to read the description in. Empty
                  asks for none, which is what a caller that only needs the
                  gate should pass.
    """
    params = {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "claims|descriptions" if description_language else "claims",
        "format": "json",
    }
    if description_language:
        params["languages"] = description_language.split("-")[0]
    data = _get(_WIKIDATA_API, params)
    if not data:
        return {}

    out: dict[str, dict] = {}
    for qid, entity in data.get("entities", {}).items():
        claims = entity.get("claims", {})
        parsed: dict = {}
        for prop in ("P31", "P279", "P18"):
            values = []
            for claim in claims.get(prop, []):
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                if isinstance(value, dict) and "id" in value:
                    values.append(value["id"])
                elif isinstance(value, str):
                    values.append(value)
            parsed[prop] = values
        code = description_language.split("-")[0] if description_language else ""
        parsed["description"] = (
            entity.get("descriptions", {}).get(code, {}).get("value", "") if code else ""
        )
        out[qid] = parsed
    return out


def _attributions(filenames: list[str]) -> dict[str, str]:
    """Credit lines for many Commons files at once, keyed by filename."""
    data = _get(_COMMONS_API, {
        "action": "query",
        "titles": "|".join(f"File:{name}" for name in filenames),
        "prop": "imageinfo",
        "iiprop": "extmetadata",
        "format": "json",
    })
    if not data:
        return {}

    out: dict[str, str] = {}
    for page in data.get("query", {}).get("pages", {}).values():
        title = page.get("title", "")
        name = title[5:] if title.startswith("File:") else title
        # Commons reports titles with spaces; the filenames these are looked
        # up by come out of a URL path and carry underscores. Keying on the
        # raw title silently returns no credit for every file, which is a
        # licence breach rather than a cosmetic miss, so normalise both ends.
        name = name.replace(" ", "_")
        info = page.get("imageinfo")
        if not info:
            continue
        out[name] = _credit_line(info[0].get("extmetadata", {}))
    return out


def find_images(lemmas: list[str], language: str,
                description_language: Optional[str] = None) -> dict[str, ImageResult]:
    """
    Resolve many lemmas at once, applying the same gate as find_image.

    Args:
        lemmas:   Words in their own language.
        language: BCP-47 code of that language.
        description_language: Which language to read each concept's
                  description in. Defaults to `language`. cards.py passes
                  the definition's language, because the description is
                  there to be compared against the definition on the card.

    Returns:
        {lemma: ImageResult} for the lemmas that earned an image. A lemma
        absent from the result is the normal case: the gate refuses most
        vocabulary on purpose.

    Same judgements as find_image, in about a fiftieth of the requests.
    Never raises; a failed batch yields nothing for that batch.
    """
    results: dict[str, ImageResult] = {}

    for chunk in _chunks(sorted(set(lemmas))):
        pages = _articles(chunk, language)
        if not pages:
            continue

        by_qid: dict[str, str] = {}
        for lemma, page in pages.items():
            qid = page.get("pageprops", {}).get("wikibase_item")
            # No Wikidata item means no way to judge the concept, and an
            # ungated image is what this module exists to avoid.
            if qid:
                by_qid.setdefault(qid, lemma)

        if not by_qid:
            continue
        entities = _entities(list(by_qid), description_language or language)

        # filename -> the finished result, less its credit line
        pending: dict[str, ImageResult] = {}
        for qid, lemma in by_qid.items():
            claims = entities.get(qid)
            if not claims:
                continue
            if not _admits(claims["P31"], claims["P279"]):
                continue

            url = _commons_url(claims["P18"]) \
                or pages[lemma].get("thumbnail", {}).get("source")
            if not url:
                continue
            name = _commons_filename(url)
            pending[name] = ImageResult(
                url=url, qid=qid,
                source="wikidata" if claims["P18"] else "wikipedia",
                filename=name,
                description=claims["description"],
            )

        if not pending:
            continue
        credits = _attributions(list(pending))
        for name, result in pending.items():
            result.attribution = credits.get(name, "")
            results[by_qid[result.qid]] = result

    return results


def mislabelled_cached_files() -> list[tuple[Path, str]]:
    """
    Cached images whose extension disagrees with their content.

    Returns (path, correct extension) pairs, empty when the cache is sound.

    Files downloaded before the naming fix keep their wrong name, because
    the bytes are fine and only the label is wrong. Anki picks a renderer
    from the extension, so such a file is a broken-image icon on every card
    that uses it until it is renamed.

    Reported rather than repaired silently: a cache is the user's data, and
    `tango doctor` is where this project tells someone what is wrong and
    what fixes it.
    """
    if not IMAGE_DIR.exists():
        return []

    wrong: list[tuple[Path, str]] = []
    for path in sorted(IMAGE_DIR.glob("*")):
        if not path.is_file():
            continue
        try:
            head = path.read_bytes()[:16]
        except OSError:
            continue
        actual = next((ext for sig, ext in _MAGIC if head.startswith(sig)), "")
        if not actual:
            continue
        # .jpeg and .jpg are the same renderer, so not a fault.
        current = path.suffix.lower()
        if current == actual or {current, actual} == {".jpg", ".jpeg"}:
            continue
        wrong.append((path, actual))
    return wrong


def repair_cached_names() -> int:
    """
    Rename cached images to match their content, returning how many moved.

    Never raises: a cache that cannot be repaired is still a working cache
    with some broken pictures in it, which is what it was already.
    """
    moved = 0
    for path, extension in mislabelled_cached_files():
        target = path.with_suffix(extension)
        try:
            path.replace(target)
        except OSError as exc:
            logger.debug("Could not rename %s: %s", path.name, exc)
            continue
        moved += 1
        logger.debug("Renamed %s -> %s", path.name, target.name)
    return moved
