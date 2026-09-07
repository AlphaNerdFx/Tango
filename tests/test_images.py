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
        # "thumbnail", not "original": the module asks for a 480px thumbnail
        # rather than the full-resolution file. Measured 5 September 2026, an
        # original was 9.2 MB against 46 KB for the thumbnail of the same
        # photograph, for a card that displays it at 240px.
        page["thumbnail"] = {"source": lead}
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


class TestTheGateRefusesWhatLooksConcrete:
    """
    Added 6 September 2026, after opening the files rather than reading the
    rates. The first measurement admitted 36.9% of nouns, which looked like
    success until the pictures were examined: `leben` got a photograph of a
    newborn, `cowardice` the Cowardly Lion, `government` a group portrait of
    Dutch ministers, `loi` the Palais-Bourbon, and `couple` a Bolero
    choreography. Each is the wrong-association failure ADR-009 exists to
    prevent, and a coverage number cannot see any of them.

    One test per class that let one through, so a future edit to the
    denylist cannot quietly reopen one of these doors.
    """

    @pytest.mark.parametrize("qid,word", [
        ("Q96253971", "type of property, reached by leben"),
        ("Q1322005", "natural phenomenon, reached by leben"),
        ("Q2996394", "biological process, reached by leben"),
        ("Q33742", "natural language, reached by englisch"),
        ("Q1288568", "modern language"),
        ("Q34770", "language"),
        ("Q2393196", "personality trait, reached by cowardice"),
        ("Q17197366", "type of organization, reached by government"),
        ("Q33104303", "concept in physics, reached by force"),
        ("Q15617994", "administrative territorial entity type, by country"),
        ("Q2135465", "legal term or concept, reached by loi"),
        ("Q10541491", "legal form, reached by corporation"),
    ])
    def test_an_abstract_class_is_refused(self, qid, word):
        with patch.object(images, "_claims", return_value=[qid]):
            assert images.is_photographable("Q1") is False, word

    def test_a_disambiguation_page_is_refused(self):
        # Not a concept at all: its image belongs to whichever sense
        # Wikipedia happened to list first.
        with patch.object(images, "_claims", return_value=["Q4167410"]):
            assert images.is_photographable("Q15643227") is False

    def test_a_disambiguation_page_is_refused_even_beside_a_concrete_class(self):
        # The pair to the test above. Q4167410 must veto, not merely fail to
        # admit, or a disambiguation page carrying any concrete class passes.
        with patch.object(images, "_claims", return_value=["Q811102", "Q4167410"]):
            assert images.is_photographable("Q1") is False

    @pytest.mark.parametrize("qid,word", [
        ("Q55983715", "organisms known by a common name, reached by Hund"),
        ("Q811102", "type of building, reached by Haus"),
        ("Q2424752", "product, reached by Flugzeug and huile"),
        ("Q317088", "commodity"),
    ])
    def test_the_tightening_did_not_close_the_concrete_classes(self, qid, word):
        # The other half of the matched pair: a denylist wide enough to
        # refuse `government` must still admit a dog and a house.
        with patch.object(images, "_claims", return_value=[qid]):
            assert images.is_photographable("Q1") is True, word


class TestSubclassIsHowACommonNounIsClassified:
    """
    Added 7 September 2026, after the funnel was measured stage by stage.

    Of 785 nouns in the definition cache, 127 resolved to an item with no
    `instance of` claim at all, and 101 of those had both a `subclass of`
    and a picture: bonbon, coussin, fleur, apfelsaft, kühlschrank,
    frühstück, einkaufswagen. That was the largest recoverable loss in the
    funnel, bigger than the denylist and bigger than missing files.

    The cause is structural rather than an oversight in Wikidata. A common
    noun *is* a class, and a class is described by what it is a subclass of.
    Asking only for `instance of` asks the wrong question of exactly the
    words a vocabulary deck is made of.
    """

    def test_a_concrete_subclass_is_admitted_when_there_is_no_instance_of(self):
        # kaffee -> Q8486, no P31, P279 beverage. Refused until today.
        with patch.object(images, "_claims", side_effect=[[], ["Q40050"]]):
            assert images.is_photographable("Q8486") is True

    def test_an_abstract_subclass_is_refused(self):
        # The denylist has to apply to whichever list is read, or the
        # fallback is a hole straight through the gate.
        with patch.object(images, "_claims", side_effect=[[], ["Q151885"]]):
            assert images.is_photographable("Q1") is False

    def test_instance_of_wins_when_the_item_has_both(self):
        # The pair to the two above. `subclass of` is a fallback, not a
        # second opinion: almost every concrete class has an abstract
        # ancestor somewhere up its chain, so reading both would refuse
        # concepts that pass today.
        with patch.object(images, "_claims", side_effect=[["Q811102"], ["Q151885"]]):
            assert images.is_photographable("Q3947") is True

    def test_an_abstract_ancestor_does_not_refuse_a_concrete_instance_of(self):
        # The rule itself, given both lists at once. The test above cannot
        # see this: the single-lemma path never asks for `subclass of` once
        # `instance of` answered, so a rule reading both would pass it while
        # refusing real concepts in the batched path. Mutation found that.
        assert images._admits(["Q811102"], ["Q151885"]) is True

    def test_the_second_request_is_not_made_when_it_is_not_needed(self):
        # Every lookup here is a paced network call, so the fallback costs a
        # request only for the items that need one.
        with patch.object(images, "_claims", side_effect=[["Q811102"]]) as claims:
            images.is_photographable("Q3947")
        assert claims.call_count == 1

    def test_a_disambiguation_page_is_still_refused_when_it_has_a_subclass(self):
        with patch.object(images, "_claims", side_effect=[["Q4167410"], ["Q811102"]]):
            assert images.is_photographable("Q1") is False


class TestTheBatchedGateAgreesWithTheSingleOne:
    """
    `find_images` carries its own copy of the gate, because it works from
    claims already in hand rather than fetching them per item. Two copies of
    a judgement can disagree, so both are tested against the same cases.
    """

    @staticmethod
    def _resolve(claims, language="de", description_language=None):
        page = {"pageprops": {"wikibase_item": "Q8486"}}
        with patch.object(images, "_articles", return_value={"kaffee": page}), \
             patch.object(images, "_entities", return_value={"Q8486": claims}), \
             patch.object(images, "_attributions", return_value={}):
            return images.find_images(["kaffee"], language,
                                      description_language=description_language)

    def test_a_subclass_only_item_is_admitted(self):
        found = self._resolve({"P31": [], "P279": ["Q40050"],
                               "P18": ["A_small_cup_of_coffee.JPG"],
                               "description": "Heißgetränk"})
        assert found["kaffee"].qid == "Q8486"

    def test_an_abstract_subclass_is_refused(self):
        found = self._resolve({"P31": [], "P279": ["Q151885"],
                               "P18": ["Anything.jpg"], "description": ""})
        assert found == {}

    def test_a_disambiguation_page_is_refused(self):
        found = self._resolve({"P31": ["Q4167410"], "P279": ["Q811102"],
                               "P18": ["Anything.jpg"], "description": ""})
        assert found == {}

    def test_an_abstract_subclass_beside_a_concrete_instance_of_still_passes(self):
        # This is the path that really holds both lists at once, so this is
        # where a rule that read both would do its damage: Haus is a type of
        # building and a subclass of something abstract, and it has been on
        # cards since the feature existed.
        found = self._resolve({"P31": ["Q811102"], "P279": ["Q151885"],
                               "P18": ["Haus.jpg"], "description": "Gebäude"})
        assert found["kaffee"].qid == "Q8486"

    def test_the_description_travels_with_the_result(self):
        # cards.py compares it against the definition it is about to print,
        # so it has to arrive on the same object as the filename. Two
        # parallel dicts is how the credit line went missing in the batch.
        found = self._resolve({"P31": [], "P279": ["Q40050"],
                               "P18": ["A_small_cup_of_coffee.JPG"],
                               "description": "Heißgetränk aus Kaffeebohnen"})
        assert found["kaffee"].description == "Heißgetränk aus Kaffeebohnen"

    def test_the_description_is_read_in_the_language_asked_for(self):
        # Under --def-lang the card's definition is not in the transcript's
        # language, and comparing a French definition against a German
        # description would find no agreement anywhere.
        page = {"pageprops": {"wikibase_item": "Q8486"}}
        with patch.object(images, "_articles", return_value={"kaffee": page}), \
             patch.object(images, "_entities", return_value={}) as entities, \
             patch.object(images, "_attributions", return_value={}):
            images.find_images(["kaffee"], "de", description_language="fr")
        assert entities.call_args[0][1] == "fr"

    def test_the_transcript_language_is_the_default(self):
        page = {"pageprops": {"wikibase_item": "Q8486"}}
        with patch.object(images, "_articles", return_value={"kaffee": page}), \
             patch.object(images, "_entities", return_value={}) as entities, \
             patch.object(images, "_attributions", return_value={}):
            images.find_images(["kaffee"], "de")
        assert entities.call_args[0][1] == "de"


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



class TestThumbnails:
    """
    Commons serves originals and they are enormous. The first real download
    during development was 9.2 MB for one photograph of a dog, for a card
    that displays it at 240px. Both routes to an image must ask for a
    thumbnail, and there are two of them.
    """

    def test_the_wikipedia_request_asks_for_a_thumbnail(self):
        with patch.object(images, "_get", return_value=None) as get:
            images._article("Hund", "de")
        params = get.call_args[0][1]
        assert params["piprop"] == "thumbnail"
        assert params["pithumbsize"] == images._THUMB_WIDTH

    def test_the_commons_url_asks_for_a_width(self):
        url = images._commons_url(["A dog.jpg"])
        assert url.endswith(f"?width={images._THUMB_WIDTH}")

    def test_spaces_become_underscores_in_a_commons_filename(self):
        # Commons 404s on a raw space in the path.
        assert "A_dog.jpg" in images._commons_url(["A dog.jpg"])

    def test_no_p18_is_no_url(self):
        assert images._commons_url([]) is None


class TestExtension:
    """
    Anki picks a renderer from the file extension, so the cached filename
    has to carry the right one. The URL it comes from may have a query
    string, because that is how the thumbnail width is requested.
    """

    def test_a_plain_url(self):
        assert images._extension("https://x/y/A_dog.png") == ".png"

    def test_a_width_query_does_not_become_part_of_the_extension(self):
        assert images._extension("https://x/A_dog.jpg?width=480") == ".jpg"

    def test_an_unknown_extension_falls_back_to_jpg(self):
        # Special:FilePath does not always end in a filename.
        assert images._extension("https://x/Special:FilePath/Dog") == ".jpg"


class TestNamingADownloadedFile:
    """
    A cached file is named after its content, not after its URL.

    The URL lies, and this is measured rather than cautious. Commons
    rasterises an SVG when a width is requested, so
    `Special:FilePath/Procent-teken.svg?width=480` answers
    `Content-Type: image/png` with PNG bytes. Naming that `.svg` is why the
    `pourcent` card showed a broken-image icon: Anki reads the extension,
    picks an SVG renderer, and is handed PNG.
    """

    def test_png_bytes_from_an_svg_url_are_named_png(self):
        # The exact `pourcent` failure.
        got = images._extension_for(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
            "image/png",
            "https://commons.wikimedia.org/wiki/Special:FilePath/Procent-teken.svg?width=480",
        )
        assert got == ".png"

    def test_the_bytes_beat_a_wrong_content_type(self):
        # Magic bytes cannot be wrong; a header can.
        got = images._extension_for(b"\x89PNG\r\n\x1a\n", "image/svg+xml", "x.svg")
        assert got == ".png"

    def test_content_type_is_used_when_the_bytes_are_unrecognised(self):
        got = images._extension_for(b"RIFF\x00\x00\x00\x00WEBP", "image/webp", "x.bin")
        assert got == ".webp"

    def test_the_url_is_the_last_resort(self):
        got = images._extension_for(b"\x00\x01\x02\x03", "", "https://x/y/thing.gif")
        assert got == ".gif"

    def test_real_svg_bytes_are_still_named_svg(self):
        assert images._extension_for(b"<svg xmlns=", "", "x") == ".svg"

    def test_jpeg_bytes_are_named_jpg(self):
        assert images._extension_for(b"\xff\xd8\xff\xe0", "", "x") == ".jpg"


class TestRepairingTheCache:
    """
    Files cached before the naming fix keep their wrong name. The bytes are
    fine, so renaming is the repair; re-downloading would spend requests to
    obtain what is already on disk.
    """

    def test_a_png_named_svg_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "IMAGE_DIR", tmp_path)
        (tmp_path / "Q1.svg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        assert [(p.name, e) for p, e in images.mislabelled_cached_files()] == [
            ("Q1.svg", ".png")]

    def test_a_correctly_named_file_is_not_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "IMAGE_DIR", tmp_path)
        (tmp_path / "Q1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        assert images.mislabelled_cached_files() == []

    def test_jpeg_and_jpg_are_not_a_fault(self, tmp_path, monkeypatch):
        # Same renderer, so renaming would be churn.
        monkeypatch.setattr(images, "IMAGE_DIR", tmp_path)
        (tmp_path / "Q1.jpeg").write_bytes(b"\xff\xd8\xff\xe0")
        assert images.mislabelled_cached_files() == []

    def test_a_file_of_unknown_type_is_left_alone(self, tmp_path, monkeypatch):
        # Refusing to guess is better than renaming someone's file wrongly.
        monkeypatch.setattr(images, "IMAGE_DIR", tmp_path)
        (tmp_path / "Q1.png").write_bytes(b"\x00\x01\x02\x03")
        assert images.mislabelled_cached_files() == []

    def test_repair_renames_and_keeps_the_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "IMAGE_DIR", tmp_path)
        payload = b"\x89PNG\r\n\x1a\n" + b"payload"
        (tmp_path / "Q1.svg").write_bytes(payload)
        assert images.repair_cached_names() == 1
        assert (tmp_path / "Q1.png").read_bytes() == payload
        assert not (tmp_path / "Q1.svg").exists()

    def test_repair_is_a_no_op_on_a_sound_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "IMAGE_DIR", tmp_path)
        (tmp_path / "Q1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        assert images.repair_cached_names() == 0

    def test_a_missing_cache_directory_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "IMAGE_DIR", tmp_path / "nope")
        assert images.mislabelled_cached_files() == []


class TestAttribution:
    """
    Not decoration. Commons reports AttributionRequired: true on the images
    this module actually returns, so shipping one in a deck without naming
    the photographer and the licence would breach it.
    """

    def _meta(self, **kw):
        return {"query": {"pages": {"1": {"imageinfo": [
            {"extmetadata": {k: {"value": v} for k, v in kw.items()}}
        ]}}}}

    def test_artist_and_licence_are_joined(self):
        with patch.object(images, "_get", return_value=self._meta(
                Artist="Markus Trienke", LicenseShortName="CC BY-SA 2.0")):
            assert images.attribution("d.jpg") == "Markus Trienke, CC BY-SA 2.0"

    def test_the_artist_html_is_stripped(self):
        # extmetadata returns Artist as a link to the uploader's user page.
        with patch.object(images, "_get", return_value=self._meta(
                Artist='<a href="//commons.wikimedia.org/wiki/User:X">Jane</a>',
                LicenseShortName="CC BY 4.0")):
            assert images.attribution("d.jpg") == "Jane, CC BY 4.0"

    def test_html_entities_are_unescaped(self):
        with patch.object(images, "_get", return_value=self._meta(
                Artist="Bob &amp; Alice", LicenseShortName="CC0")):
            assert images.attribution("d.jpg") == "Bob & Alice, CC0"

    def test_a_licence_with_no_artist_still_credits_the_licence(self):
        with patch.object(images, "_get", return_value=self._meta(
                LicenseShortName="Public domain")):
            assert images.attribution("d.jpg") == "Public domain"

    def test_a_file_with_no_imageinfo_gives_no_credit(self):
        with patch.object(images, "_get", return_value={"query": {"pages": {"1": {}}}}):
            assert images.attribution("d.jpg") == ""

    def test_a_network_failure_gives_no_credit_rather_than_raising(self):
        with patch.object(images, "_get", return_value=None):
            assert images.attribution("d.jpg") == ""

    def test_find_image_carries_the_credit_with_the_result(self):
        # The credit has to travel with the image, not be fetched again by
        # the caller: the two would be able to disagree about which file.
        with patch.object(images, "_article", return_value={
                    "pageprops": {"wikibase_item": "Q144"},
                    "thumbnail": {"source": "https://x/dog.jpg"}}), \
             patch.object(images, "is_photographable", return_value=True), \
             patch.object(images, "_claims", return_value=[]), \
             patch.object(images, "attribution", return_value="Jane, CC0"):
            assert images.find_image("Hund", "de").attribution == "Jane, CC0"


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
