"""
test_wiktdata.py

Offline Wiktionary index: build, lookup, and graceful degradation.

Every test builds its own miniature index in tmp_path from an inline
archive, so the default run needs no download, no network, and none of the
real 323 MB index (CLAUDE.md 3.5).

Run unit tests:  pytest tests/test_wiktdata.py -m "not integration"
"""

from __future__ import annotations

import gzip
import json
import sqlite3

import pytest

import pipeline.wiktdata as wiktdata
from pipeline.wiktdata import (
    DictionaryBuildError,
    DictionaryDownloadError,
    build_index,
    is_available,
    index_path,
    lookup,
)


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_dict_dir(tmp_path, monkeypatch):
    """Point the module at a throwaway directory and clear its connection cache."""
    monkeypatch.setattr(wiktdata, "DICT_DIR", tmp_path / "dictionaries")
    # Connections are cached per thread; a stale one would point at a
    # previous test's index.
    if hasattr(wiktdata._local, "conns"):
        for conn in wiktdata._local.conns.values():
            if conn is not None:
                conn.close()
        del wiktdata._local.conns
    yield
    if hasattr(wiktdata._local, "conns"):
        for conn in wiktdata._local.conns.values():
            if conn is not None:
                conn.close()
        del wiktdata._local.conns


def _record(word, lang_code="fr", gloss="Une definition.", examples=(),
            synonyms=(), antonyms=(), pos="noun", sounds=None, senses=None):
    """
    Build one wiktextract-shaped record.

    `senses` overrides the single-sense default, for tests that need several
    -- an inflection pointer sitting in front of a real gloss, say. `sounds`
    takes the shape wiktextract emits: a list of dicts carrying ipa, mp3_url
    and ogg_url in any combination.
    """
    return {
        "word": word,
        "lang_code": lang_code,
        "pos": pos,
        "senses": senses if senses is not None else [{
            "glosses": [gloss],
            "examples": [{"text": t} for t in examples],
        }],
        "sounds": sounds or [],
        "synonyms": [{"word": w} for w in synonyms],
        "antonyms": [{"word": w} for w in antonyms],
    }


def _sense(gloss, form_of=None, tags=(), examples=()):
    """One sense. form_of names the word this is an inflected form of."""
    s = {"glosses": [gloss], "examples": [{"text": t} for t in examples]}
    if form_of:
        s["form_of"] = [{"word": form_of}]
        s["tags"] = ["form-of", *tags]
    elif tags:
        s["tags"] = list(tags)
    return s


def _archive(tmp_path, records) -> "object":
    path = tmp_path / "extract.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


# -- Graceful degradation -----------------------------------------------------

class TestNoIndex:

    def test_is_available_false_when_never_built(self):
        assert is_available("fr") is False

    def test_lookup_returns_none_without_an_index(self):
        # Must degrade, not raise: every other optional source in this
        # project behaves this way, and the pipeline has to keep working
        # for anyone who never runs --build-dictionary.
        assert lookup("maison", "fr") is None

    def test_lookup_returns_none_for_unbuilt_language(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        assert lookup("Haus", "de") is None


# -- Building -----------------------------------------------------------------

class TestBuildIndex:

    def test_builds_and_reports_count(self, tmp_path):
        count = build_index("fr", archive=_archive(tmp_path, [
            _record("maison"), _record("eau"),
        ]))
        assert count == 2
        assert is_available("fr") is True

    def test_filters_out_other_languages(self, tmp_path):
        # The French extract also documents German, Occitan and others *in*
        # French. Only real target-language words belong in the index --
        # this discards roughly 70% of the real file.
        count = build_index("fr", archive=_archive(tmp_path, [
            _record("maison", lang_code="fr"),
            _record("Haus", lang_code="de"),
            _record("casa", lang_code="oc"),
        ]))
        assert count == 1
        assert lookup("maison", "fr") is not None
        assert lookup("Haus", "fr") is None

    def test_records_without_a_gloss_are_skipped(self, tmp_path):
        bare = {"word": "vide", "lang_code": "fr", "senses": [{}]}
        count = build_index("fr", archive=_archive(tmp_path, [_record("maison"), bare]))
        assert count == 1

    def test_malformed_lines_do_not_abort_the_build(self, tmp_path):
        # A truncated line must not fail a multi-million-line build.
        path = tmp_path / "extract.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps(_record("maison")) + "\n")
            fh.write('{"word": "broken", "lang_c\n')
            fh.write(json.dumps(_record("eau")) + "\n")
        assert build_index("fr", archive=path) == 2

    def test_empty_result_raises_rather_than_leaving_a_dead_index(self, tmp_path):
        with pytest.raises(DictionaryBuildError, match="No 'fr' entries"):
            build_index("fr", archive=_archive(tmp_path, [_record("Haus", lang_code="de")]))
        # A failed build must not leave something is_available() calls usable.
        assert is_available("fr") is False

    def test_interrupted_build_leaves_no_partial_index(self, tmp_path):
        path = tmp_path / "corrupt.jsonl.gz"
        path.write_bytes(b"not actually gzip")
        with pytest.raises(DictionaryBuildError):
            build_index("fr", archive=path)
        assert not index_path("fr").exists()

    def test_download_failure_is_a_typed_error(self, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError("network down")
        monkeypatch.setattr(wiktdata.urllib.request, "urlretrieve", _boom)
        with pytest.raises(DictionaryDownloadError, match="Could not download"):
            build_index("fr")

    def test_progress_callback_receives_status_lines(self, tmp_path):
        seen: list = []
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]),
                    progress=seen.append)
        assert any("Indexed" in line for line in seen)


# -- Lookup -------------------------------------------------------------------

class TestLookup:

    def test_returns_all_fields(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [
            _record("maison", gloss="Batiment servant de logis.",
                    examples=["Une belle maison.", "La maison est grande."],
                    synonyms=["demeure", "logis"], antonyms=["dehors"], pos="noun"),
        ]))
        entry = lookup("maison", "fr")
        assert entry.definition == "Batiment servant de logis."
        assert entry.part_of_speech == "noun"
        assert entry.example1 == "Une belle maison."
        assert entry.example2 == "La maison est grande."
        assert entry.synonyms == ["demeure", "logis"]
        assert entry.antonyms == ["dehors"]

    def test_lookup_is_case_insensitive(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        assert lookup("MAISON", "fr") is not None

    def test_missing_word_returns_none(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        assert lookup("xyzqwerty", "fr") is None

    def test_empty_optional_fields_come_back_as_none_not_blank(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("eau")]))
        entry = lookup("eau", "fr")
        assert entry.example1 is None
        assert entry.example2 is None
        assert entry.synonyms == []
        assert entry.antonyms == []

    # -- Apostrophe normalisation ---------------------------------------------
    #
    # Matched pair. Wiktionary stores French elisions with a typographic
    # apostrophe, so an ASCII quote silently misses an entry that is
    # definitely present -- on one of the most common words in the language.
    # These fail in opposite directions if either spelling stops working.

    def test_ascii_apostrophe_finds_typographic_entry(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("aujourd’hui")]))
        assert lookup("aujourd'hui", "fr") is not None

    def test_typographic_apostrophe_finds_ascii_entry(self, tmp_path):
        build_index("fr", archive=_archive(tmp_path, [_record("aujourd'hui")]))
        assert lookup("aujourd’hui", "fr") is not None


# -- Schema versioning --------------------------------------------------------

class TestSchemaVersion:

    def test_stale_schema_is_rejected_rather_than_queried(self, tmp_path):
        # Querying an old layout with new column names would raise deep
        # inside a lookup. Better to report it unavailable and ask for a
        # rebuild.
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        conn = sqlite3.connect(index_path("fr"))
        conn.execute(f"PRAGMA user_version = {wiktdata._SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        assert is_available("fr") is False


# -- Download URL resolution --------------------------------------------------

class TestDownloadUrl:

    def test_most_languages_use_the_uniform_per_language_path(self):
        assert wiktdata.download_url("fr").endswith("/fr/fr-extract.jsonl.gz")
        assert wiktdata.download_url("de").endswith("/de/de-extract.jsonl.gz")

    def test_english_uses_its_own_path(self):
        # kaikki's primary extraction is English and lives elsewhere; the
        # uniform path 404s for "en". Confirmed by probing both.
        url = wiktdata.download_url("en")
        assert "en-extract" not in url
        assert url.endswith("kaikki.org-dictionary-English.jsonl.gz")


# -- Discouraged languages ----------------------------------------------------

class TestDiscouraged:

    def test_english_is_discouraged_with_a_reason(self):
        # Measured, not assumed: a live English run consulted the index zero
        # times because Merriam-Webster already answered everything.
        reason = wiktdata.is_discouraged("en")
        assert reason
        assert "Merriam-Webster" in reason

    def test_languages_that_need_the_index_are_not_discouraged(self):
        # These are exactly the languages with no other definition source.
        for lang in ("fr", "de", "ru"):
            assert wiktdata.is_discouraged(lang) is None

    def test_discouraged_is_advisory_and_does_not_block_building(self, tmp_path):
        # It must stay a warning, not a refusal -- the data is real and
        # someone may have a reason to want it.
        count = build_index("en", archive=_archive(tmp_path, [
            _record("house", lang_code="en", gloss="A building for living in."),
        ]))
        assert count == 1
        assert lookup("house", "en") is not None

    def test_failed_download_leaves_no_partial_archive(self, monkeypatch, tmp_path):
        # A truncated archive can be hundreds of MB of useless disk. Found
        # for real by interrupting a French build mid-download.
        monkeypatch.setattr(wiktdata, "DICT_DIR", tmp_path / "d")

        def _partial(_url, dest):
            (tmp_path / "d").mkdir(parents=True, exist_ok=True)
            open(dest, "wb").write(b"truncated")
            raise OSError("connection reset")

        monkeypatch.setattr(wiktdata.urllib.request, "urlretrieve", _partial)
        with pytest.raises(DictionaryDownloadError):
            build_index("fr")
        assert not (tmp_path / "d" / "fr-extract.jsonl.gz").exists()

    def test_interrupt_during_download_leaves_no_partial_archive(
        self, monkeypatch, tmp_path
    ):
        # Ctrl-C on a multi-minute download is normal usage, and
        # KeyboardInterrupt is not an OSError so it needs its own handling.
        monkeypatch.setattr(wiktdata, "DICT_DIR", tmp_path / "d")

        def _interrupted(_url, dest):
            (tmp_path / "d").mkdir(parents=True, exist_ok=True)
            open(dest, "wb").write(b"partial")
            raise KeyboardInterrupt()

        monkeypatch.setattr(wiktdata.urllib.request, "urlretrieve", _interrupted)
        with pytest.raises(KeyboardInterrupt):
            build_index("fr")
        assert not (tmp_path / "d" / "fr-extract.jsonl.gz").exists()


# -- Sense selection by part of speech ----------------------------------------

class TestSenseSelection:
    """
    ARCHITECTURE.md 8.29. The index holds one row per (word, part of
    speech), so an ambiguous word has several and the first is frequently
    the wrong sense for the video it came from.

    Every test here builds a word with more than one row and asserts which
    one comes back, because a single-row fixture makes the whole bug class
    inexpressible -- the same trap SESSION.md 6.11 records for
    _make_token()'s identical text and lemma arguments.
    """

    @staticmethod
    def _multi(tmp_path):
        """'marche': noun first, then verb -- the real French row order."""
        return _archive(tmp_path, [
            _record("marche", gloss="Province frontiere.", pos="noun"),
            _record("marche", gloss="Se deplacer a pied.", pos="verb"),
            _record("marche", gloss="Ancien comte francais.", pos="name"),
        ])

    def test_verb_in_context_gets_the_verb_row(self, tmp_path):
        build_index("fr", archive=self._multi(tmp_path))
        entry = lookup("marche", "fr", pos="VERB")
        assert entry.definition == "Se deplacer a pied."
        assert entry.part_of_speech == "verb"

    def test_noun_in_context_gets_the_noun_row(self, tmp_path):
        # The pair to the test above. One alone can pass by accident -- the
        # noun row is also the first row, so a POS filter that silently did
        # nothing would satisfy this one and fail its partner.
        build_index("fr", archive=self._multi(tmp_path))
        entry = lookup("marche", "fr", pos="NOUN")
        assert entry.definition == "Province frontiere."
        assert entry.part_of_speech == "noun"

    def test_no_pos_keeps_the_first_row(self, tmp_path):
        # Review and backlog mode have no transcript to tag, so they pass
        # nothing and must get exactly the previous behaviour.
        build_index("fr", archive=self._multi(tmp_path))
        assert lookup("marche", "fr").definition == "Province frontiere."

    def test_unmatched_pos_keeps_the_first_row(self, tmp_path):
        # A word whose part of speech has no row must keep its card rather
        # than lose one. No ADV row exists here.
        build_index("fr", archive=self._multi(tmp_path))
        assert lookup("marche", "fr", pos="ADV").definition == "Province frontiere."

    def test_a_name_row_is_never_selected(self, tmp_path):
        # Russian "vid" led with a river in Germany and "blizkiy" with an
        # island in the Kara Sea. Proper nouns are filtered upstream by
        # nlp._is_valid_token, so a name row is never why a word is on a card.
        build_index("fr", archive=_archive(tmp_path, [
            _record("cote", gloss="Nom de famille.", pos="name"),
            _record("cote", gloss="Region des cotes.", pos="noun"),
        ]))
        assert lookup("cote", "fr").definition == "Region des cotes."

    def test_a_name_row_is_used_when_it_is_the_only_one(self, tmp_path):
        # The partner to the test above: skipping name rows must not turn a
        # word that has only one into a miss.
        build_index("fr", archive=_archive(tmp_path, [
            _record("cote", gloss="Nom de famille.", pos="name"),
        ]))
        assert lookup("cote", "fr").definition == "Nom de famille."

    def test_pos_selection_survives_the_apostrophe_variant(self, tmp_path):
        # _lookup_variants retries a typographic apostrophe. Sense selection
        # has to apply to whichever spelling actually matched, not only the
        # first one tried.
        build_index("fr", archive=_archive(tmp_path, [
            _record("aujourd’hui", gloss="Le jour ou l'on est.", pos="noun"),
            _record("aujourd’hui", gloss="En ce jour.", pos="adv"),
        ]))
        assert lookup("aujourd'hui", "fr", pos="ADV").definition == "En ce jour."

    def test_unknown_upos_does_not_narrow(self, tmp_path):
        # PROPN and friends never reach here, but an unmapped tag must fall
        # through to the previous behaviour rather than matching nothing.
        build_index("fr", archive=self._multi(tmp_path))
        assert lookup("marche", "fr", pos="PROPN").definition == "Province frontiere."


class TestPosForUpos:
    """
    pos_for_upos is public because the definition cache keys on it: a tag
    that cannot change which row is selected must not fragment the cache.
    """

    def test_maps_the_four_tags_nlp_keeps(self):
        assert wiktdata.pos_for_upos("NOUN") == "noun"
        assert wiktdata.pos_for_upos("VERB") == "verb"
        assert wiktdata.pos_for_upos("ADJ") == "adj"
        assert wiktdata.pos_for_upos("ADV") == "adv"

    def test_unmappable_tags_return_none(self):
        assert wiktdata.pos_for_upos("PROPN") is None
        assert wiktdata.pos_for_upos("") is None
        assert wiktdata.pos_for_upos(None) is None


# -- Schema v2: pronunciation and inflection pointers -------------------------

class TestPronunciationFields:
    """
    ADR-009 phase 1. IPA and a Commons audio URL sit in the same records the
    index is already built from, so capturing them is a schema change rather
    than a new source. Measured on 20000 German entries: 93% carry IPA, 91%
    an audio URL.
    """

    def test_ipa_and_audio_are_stored(self, tmp_path):
        build_index("de", archive=_archive(tmp_path, [
            _record("hallo", lang_code="de", sounds=[
                {"ipa": "[ha'lo:]"},
                {"mp3_url": "https://upload.wikimedia.org/De-Hallo.ogg.mp3",
                 "ogg_url": "https://commons.wikimedia.org/De-Hallo.ogg"},
            ]),
        ]))
        entry = lookup("hallo", "de")
        assert entry.ipa == "[ha'lo:]"
        assert entry.audio_url == "https://upload.wikimedia.org/De-Hallo.ogg.mp3"

    def test_mp3_is_preferred_over_ogg(self, tmp_path):
        # ogg_url is a Special:FilePath redirect; mp3_url is the direct file,
        # and Anki plays mp3 without depending on the user's codec setup.
        build_index("de", archive=_archive(tmp_path, [
            _record("haus", lang_code="de", sounds=[
                {"ogg_url": "https://commons.wikimedia.org/De-Haus.ogg",
                 "mp3_url": "https://upload.wikimedia.org/De-Haus.ogg.mp3"},
            ]),
        ]))
        assert lookup("haus", "de").audio_url.endswith(".mp3")

    def test_ogg_is_used_when_there_is_no_mp3(self, tmp_path):
        # The partner: preferring mp3 must not mean discarding a lone ogg.
        build_index("de", archive=_archive(tmp_path, [
            _record("baum", lang_code="de", sounds=[
                {"ogg_url": "https://commons.wikimedia.org/De-Baum.ogg"},
            ]),
        ]))
        assert lookup("baum", "de").audio_url.endswith(".ogg")

    def test_absent_sounds_leave_both_empty(self, tmp_path):
        # 7% of German entries have no sounds block at all, and every entry
        # in a v1-era fixture has none. Must be None, not "".
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        entry = lookup("maison", "fr")
        assert entry.ipa is None
        assert entry.audio_url is None


class TestFormOfSenses:
    """
    Wiktionary indexes every inflected form as its own entry, so a card can
    read "Dativ Plural des Substantivs Krieg" -- a pointer, not a definition.
    Measured at 18 of 342 German cards on a real video.
    """

    def test_a_real_gloss_wins_over_a_pointer_in_the_same_record(self, tmp_path):
        # The pointer is FIRST here. Taking senses[0] unconditionally, which
        # is what v1 did, stores the pointer and loses the real definition
        # sitting right behind it.
        build_index("de", archive=_archive(tmp_path, [
            _record("hab", lang_code="de", senses=[
                _sense("2. Person Singular Imperativ des Verbs haben", form_of="haben"),
                _sense("Hab und Gut; Besitz"),
            ]),
        ]))
        entry = lookup("hab", "de")
        assert entry.definition == "Hab und Gut; Besitz"
        assert entry.form_of is None

    def test_a_pointer_is_kept_when_every_sense_is_one(self, tmp_path):
        # The partner. Skipping pointers must not turn a word that has only
        # pointers into a miss -- it would lose the card entirely.
        build_index("de", archive=_archive(tmp_path, [
            _record("glaube", lang_code="de", senses=[
                _sense("1. Person Singular Indikativ des Verbs glauben",
                       form_of="glauben"),
            ]),
        ]))
        entry = lookup("glaube", "de")
        assert entry.definition.startswith("1. Person Singular")
        assert entry.form_of == "glauben"

    def test_the_pointer_target_is_recorded(self, tmp_path):
        # form_of naming the target is what lets a caller follow the pointer
        # to glauben's real definition instead of printing the pointer.
        build_index("de", archive=_archive(tmp_path, [
            _record("kriegen", lang_code="de", senses=[
                _sense("Dativ Plural des Substantivs Krieg", form_of="Krieg"),
            ]),
        ]))
        assert lookup("kriegen", "de").form_of == "Krieg"

    def test_a_tagged_sense_with_no_target_still_counts_as_a_pointer(self, tmp_path):
        # wiktextract tags some senses form-of without resolving a target.
        # Those must still lose to a real gloss.
        build_index("de", archive=_archive(tmp_path, [
            _record("test", lang_code="de", senses=[
                {"glosses": ["Genitiv Singular"], "tags": ["form-of"]},
                _sense("Eine Pruefung."),
            ]),
        ]))
        assert lookup("test", "de").definition == "Eine Pruefung."

    def test_examples_follow_the_selected_sense(self, tmp_path):
        # The gloss and its examples must come from the same sense. Reading
        # the gloss from one and examples from another is the field-mismatch
        # shape this project keeps hitting.
        build_index("de", archive=_archive(tmp_path, [
            _record("hab", lang_code="de", senses=[
                _sense("Imperativ des Verbs haben", form_of="haben",
                       examples=["Hab dich nicht so!"]),
                _sense("Besitz", examples=["Sein Hab und Gut."]),
            ]),
        ]))
        assert lookup("hab", "de").example1 == "Sein Hab und Gut."


class TestSchemaVersioning:

    def test_a_v1_index_is_reported_unusable(self, tmp_path, monkeypatch):
        # A stale index must be rebuilt rather than queried with the wrong
        # columns. Without this the v2 read would raise on a missing column
        # for anyone who does not rebuild.
        build_index("fr", archive=_archive(tmp_path, [_record("maison")]))
        path = index_path("fr")
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA user_version = 1")
        assert is_available("fr") is False


class TestFollowingFormOfPointers:
    """
    81.4% of the real German index is inflected forms, so a selected row's
    whole gloss is often a pointer. v2 stores the target, so it can be
    followed instead of printed. Verified on the real index: "glaube" went
    from "1. Person Singular Indikativ Präsens Aktiv des Verbs glauben" to
    "religiös sein, an einen oder mehrere Götter glauben".
    """

    @staticmethod
    def _pair(tmp_path):
        return _archive(tmp_path, [
            _record("glaube", lang_code="de", pos="verb",
                    sounds=[{"ipa": "[glaube-ipa]"}],
                    senses=[_sense("1. Person Singular des Verbs glauben",
                                   form_of="glauben")]),
            _record("glauben", lang_code="de", pos="verb",
                    sounds=[{"ipa": "[glauben-ipa]"}],
                    senses=[_sense("geistig taetig sein",
                                   examples=["Ich glaube dir."])]),
        ])

    def test_a_pointer_resolves_to_its_target_definition(self, tmp_path):
        build_index("de", archive=self._pair(tmp_path))
        entry = lookup("glaube", "de", pos="VERB")
        assert entry.definition == "geistig taetig sein"
        assert entry.form_of == "glauben"

    def test_examples_come_from_the_target_too(self, tmp_path):
        # The pointer entry has no examples of its own; the target does.
        build_index("de", archive=self._pair(tmp_path))
        assert lookup("glaube", "de", pos="VERB").example1 == "Ich glaube dir."

    def test_pronunciation_stays_with_the_word_that_was_looked_up(self, tmp_path):
        # The learner sees "glaube" on the card, so it must carry glaube's
        # IPA even though the meaning is borrowed. Taking the whole row from
        # the target would put the wrong pronunciation on the card -- the
        # field-mismatch shape CLAUDE.md 3.2 exists to prevent.
        build_index("de", archive=self._pair(tmp_path))
        assert lookup("glaube", "de", pos="VERB").ipa == "[glaube-ipa]"

    def test_a_real_definition_is_not_redirected(self, tmp_path):
        # The partner: only pointers are followed. A word with a real gloss
        # must be returned untouched.
        build_index("de", archive=_archive(tmp_path, [
            _record("haus", lang_code="de", senses=[_sense("Ein Gebaeude.")]),
        ]))
        entry = lookup("haus", "de")
        assert entry.definition == "Ein Gebaeude."
        assert entry.form_of is None

    def test_a_missing_target_keeps_the_pointer_rather_than_the_card(self, tmp_path):
        # Losing the card is worse than a poor definition -- the rule the
        # rest of this module follows.
        build_index("de", archive=_archive(tmp_path, [
            _record("glaube", lang_code="de",
                    senses=[_sense("1. Person Singular des Verbs glauben",
                                   form_of="glauben")]),
        ]))
        entry = lookup("glaube", "de")
        assert entry.definition.startswith("1. Person Singular")

    def test_a_pointer_to_a_pointer_is_not_chased(self, tmp_path):
        # One hop only. A chain must terminate rather than loop.
        build_index("de", archive=_archive(tmp_path, [
            _record("a", lang_code="de", senses=[_sense("pointer to b", form_of="b")]),
            _record("b", lang_code="de", senses=[_sense("pointer to a", form_of="a")]),
        ]))
        assert lookup("a", "de").definition == "pointer to b"

    def test_the_target_row_is_chosen_by_the_same_pos(self, tmp_path):
        # The target can itself be ambiguous. "leide" is a verb form, so the
        # verb sense of "leiden" is wanted, not the noun.
        build_index("de", archive=_archive(tmp_path, [
            _record("leide", lang_code="de", pos="verb",
                    senses=[_sense("1. Person des Verbs leiden", form_of="leiden")]),
            _record("leiden", lang_code="de", pos="noun",
                    senses=[_sense("Das Leiden, ein Zustand.")]),
            _record("leiden", lang_code="de", pos="verb",
                    senses=[_sense("Schmerzen verspueren.")]),
        ]))
        assert lookup("leide", "de", pos="VERB").definition == "Schmerzen verspueren."


class TestFormOfSpellings:
    """
    An inflection pointer's target is not always spelled the way the index
    stores the headword, and until 18 August 2026 that cost Russian one card
    in twenty-two. Measured on the real builds: 12 of 14 unresolved pointers
    in a 300-word Russian sample were one of these two shapes, while German
    and French carried neither across 2.3 million pointers.
    """

    def test_the_literal_target_is_tried_first(self):
        # Order is the safety property: anything after the literal can only
        # add a hit, never move one that already worked. Asserted on a target
        # that actually normalises, since one that does not makes every
        # ordering look identical.
        assert wiktdata._form_of_spellings("нача́ло") == ["нача́ло", "начало"]
        assert wiktdata._form_of_spellings("кома#I") == ["кома#i", "кома"]

    def test_a_homograph_disambiguator_is_dropped(self):
        # Russian Wiktionary disambiguates in the pointer itself, so the
        # target arrives as "толк#(существительное I)" and matches nothing.
        assert "толк" in wiktdata._form_of_spellings("толк#(существительное I)")

    def test_a_cyrillic_stress_mark_is_stripped(self):
        # The index stores headwords unstressed, so the marked spelling
        # cannot find them.
        assert "начало" in wiktdata._form_of_spellings("нача́ло")

    def test_a_french_accent_is_never_stripped(self):
        """
        The partner, and the reason the strip is scoped to Cyrillic.

        A combining acute on Cyrillic is stress notation and means nothing.
        The identical codepoint on Latin spells a different word: "a" and
        "à" are separate French entries, as are "eleve" and "élève".
        Stripping everywhere measurably resolved a French pointer to the
        wrong word, so this is a real regression guard, not a hypothetical.
        """
        for word in ("à", "élève", "déjà"):
            assert wiktdata._form_of_spellings(word) == [word]

    def test_no_spelling_is_offered_twice(self):
        # A target needing no normalisation must produce one candidate, not
        # three identical queries against the index.
        assert wiktdata._form_of_spellings("gehen") == ["gehen"]

    def test_a_pointer_with_a_disambiguator_resolves(self, tmp_path):
        build_index("ru", archive=_archive(tmp_path, [
            _record("кому", lang_code="ru", pos="noun",
                    senses=[_sense("дательный падеж", form_of="кома#I")]),
            _record("кома", lang_code="ru", pos="noun",
                    senses=[_sense("глубокое бессознательное состояние")]),
        ]))
        assert lookup("кому", "ru").definition == "глубокое бессознательное состояние"

    def test_a_pointer_with_a_stress_mark_resolves(self, tmp_path):
        build_index("ru", archive=_archive(tmp_path, [
            _record("начала", lang_code="ru", pos="noun",
                    senses=[_sense("родительный падеж", form_of="нача́ло")]),
            _record("начало", lang_code="ru", pos="noun",
                    senses=[_sense("первый момент чего-либо")]),
        ]))
        assert lookup("начала", "ru").definition == "первый момент чего-либо"
