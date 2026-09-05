"""
test_images.py

The concept-resolution and gating logic for card images.

Network is mocked here, per CLAUDE.md 3.5: no test in the default run may
require it. The real Wikipedia and Wikidata behaviour is pinned by the
integration tests at the bottom, which are deselected by default.
"""

from unittest.mock import patch

import pytest

import pipeline.images as images


def _page(qid=None, lead=None, missing=False):
    """A Wikipedia API page dict, as the real API returns it."""
    page = {}
    if missing:
        page["missing"] = ""
        return {"query": {"pages": {"-1": page}}}
    if qid:
        page["pageprops"] = {"wikibase_item": qid}
    if lead:
        page["original"] = {"source": lead}
    return {"query": {"pages": {"1": page}}}


class TestPhotographable:
    """
    The gate. Wikidata's `instance of` claims separate a thing from an idea,
    and the judgement attaches to the concept rather than the word, which is
    what makes it work for German: `Hund` and `chien` both resolve to Q144,
    so one answer serves every language.
    """

    def test_a_thing_passes(self):
        with patch.object(images, "_claims", return_value=["Q55983715"]):
            assert images.is_photographable("Q144") is True

    def test_an_ideal_is_refused(self):
        # Q2979, which both Freiheit and liberté resolve to. Wikipedia will
        # hand back the Statue of Liberty for it, a photograph of a statue
        # rather than of freedom.
        with patch.object(images, "_claims", return_value=["Q840396", "Q1207505"]):
            assert images.is_photographable("Q2979") is False

    def test_one_abstract_claim_is_enough_to_refuse(self):
        # Strict in the same way as definition.is_concrete_noun: an empty
        # field costs a learner nothing, a wrong image costs the association.
        with patch.object(images, "_claims", return_value=["Q55983715", "Q840396"]):
            assert images.is_photographable("Q1") is False

    def test_no_claims_means_no(self):
        # An item nothing has classified cannot be judged, and an ungated
        # image is what this module exists to prevent.
        with patch.object(images, "_claims", return_value=[]):
            assert images.is_photographable("Q1") is False


class TestFindImage:

    def test_a_missing_article_yields_nothing(self):
        # `schwierig` has no German article. An adjective with no picture is
        # the correct outcome, not a failure.
        with patch.object(images, "_get", return_value=_page(missing=True)):
            assert images.find_image("schwierig", "de") is None

    def test_an_article_with_no_wikidata_item_is_refused(self):
        # Without an item there is nothing to gate on.
        with patch.object(images, "_get", return_value=_page(lead="http://x/y.jpg")):
            assert images.find_image("whatever", "de") is None

    def test_an_abstract_concept_is_refused_even_with_a_lead_image(self):
        # The Freiheit case exactly: the article has a picture, and the
        # concept is an ideal, so the picture must not be used.
        with patch.object(images, "_get", return_value=_page(qid="Q2979", lead="http://x/statue.jpg")), \
             patch.object(images, "is_photographable", return_value=False):
            assert images.find_image("Freiheit", "de") is None

    def test_wikidata_image_is_preferred_over_the_lead_image(self):
        # P18 is curated for the concept; the lead image is whatever the
        # article opens with.
        with patch.object(images, "_get", return_value=_page(qid="Q144", lead="http://x/lead.jpg")), \
             patch.object(images, "is_photographable", return_value=True), \
             patch.object(images, "_claims", return_value=["Dog.jpg"]):
            result = images.find_image("Hund", "de")
        assert result.source == "wikidata"
        assert "Dog.jpg" in result.url

    def test_the_lead_image_is_used_when_wikidata_has_none(self):
        with patch.object(images, "_get", return_value=_page(qid="Q144", lead="http://x/lead.jpg")), \
             patch.object(images, "is_photographable", return_value=True), \
             patch.object(images, "_claims", return_value=[]):
            result = images.find_image("Hund", "de")
        assert result.source == "wikipedia"
        assert result.url == "http://x/lead.jpg"

    def test_a_network_failure_is_no_image_not_an_error(self):
        # An image is an enhancement. Nothing about it is worth failing a run
        # that has already paid for a transcript and a thousand definitions.
        import requests
        with patch.object(images.requests, "get",
                          side_effect=requests.RequestException("down")):
            assert images.find_image("Hund", "de") is None

    def test_wikimedia_gets_an_identifying_user_agent(self):
        # Wikimedia answers the default python-requests agent with 403.
        from pipeline.config import WIKTIONARY_USER_AGENT
        with patch.object(images.requests, "get") as get:
            get.return_value.json.return_value = _page(missing=True)
            get.return_value.raise_for_status.return_value = None
            images.find_image("Hund", "de")
        assert get.call_args.kwargs["headers"]["User-Agent"] == WIKTIONARY_USER_AGENT


@pytest.mark.integration
class TestAgainstTheRealSources:
    """
    The behaviour the design rests on, against live Wikipedia and Wikidata.
    Deselected by default; run with `make test-all`.
    """

    @pytest.mark.parametrize("lang,word", [("de", "Hund"), ("de", "Haus"), ("fr", "chien")])
    def test_concrete_nouns_get_an_image(self, lang, word):
        assert images.find_image(word, lang) is not None

    @pytest.mark.parametrize("lang,word", [("de", "Freiheit"), ("fr", "liberté"),
                                           ("de", "laufen"), ("de", "schwierig")])
    def test_abstract_words_and_non_nouns_get_nothing(self, lang, word):
        assert images.find_image(word, lang) is None

    def test_german_works_where_wordnet_cannot_judge_it(self):
        # The gap this design exists to close: German has no WordNet in OMW,
        # so definition.is_concrete_noun admits 0% of German nouns.
        from pipeline.definition import images_supported
        assert images_supported("de") is False
        assert images.find_image("Hund", "de") is not None

    def test_the_same_concept_serves_both_languages(self):
        de = images.find_image("Hund", "de")
        fr = images.find_image("chien", "fr")
        assert de.qid == fr.qid == "Q144"
