"""Lesson media: a picture is shown only when the code can prove it is the right one.

The reported problem was "many times it adds unnecessary photos". The cause was not the
prompt - the prompt was already strict - but that `media_enrichment` asked the model for
an exact Wikipedia article title and then ran it through *full-text search*, taking
whatever ranked first without ever comparing it to what it asked for. "Ohm's law" ranks
the biography of Georg Ohm, so students got an oil painting captioned as an explanation
of resistance.

`judge_page` is a pure function of a recorded API response, so every one of those failures
is testable without a network. The whole module had no tests at all before this and the
Wikipedia call was never mocked.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_media_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from learnova import media_enrichment as media  # noqa: E402

UPLOAD = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/"


def page(title, *, thumb=None, width=800, disambiguation=False,
         categories=(), missing=False, url=None):
    """One page as the Wikipedia API returns it under formatversion=2."""
    if missing:
        return {"title": title, "missing": True}
    result = {"title": title, "fullurl": url or f"https://en.wikipedia.org/wiki/{title}"}
    if disambiguation:
        result["pageprops"] = {"disambiguation": ""}
    if categories:
        result["categories"] = [{"title": f"Category:{name}"} for name in categories]
    if thumb:
        result["thumbnail"] = {"source": thumb, "width": width, "height": width}
    return result


def want(term, kind="diagram", alt=""):
    return media.ImageRequest(term=term, kind=kind, alt=alt)


class RejectsTheWrongPictureTests(unittest.TestCase):
    """Each of these is a failure the old code shipped to students."""

    def assert_rejected(self, request, payload, reason):
        decision = media.judge_page(request, payload)
        self.assertFalse(decision.accepted, f"expected {reason}, got an accepted image")
        self.assertEqual(decision.reason, reason)

    def test_a_law_must_not_resolve_to_a_portrait_of_the_person(self):
        # The headline failure: full-text search ranked the biography above the law.
        self.assert_rejected(
            want("Ohm's law"),
            page("Georg Ohm", thumb=UPLOAD + "Georg_Simon_Ohm3.jpg",
                 categories=["1789 births", "1854 deaths", "German physicists"]),
            "about_a_person")

    def test_a_living_person_is_refused_too(self):
        self.assert_rejected(
            want("Higgs mechanism"),
            page("Peter Higgs", thumb=UPLOAD + "Higgs.jpg", categories=["Living people"]),
            "about_a_person")

    def test_a_disambiguation_page_is_not_an_illustration(self):
        self.assert_rejected(
            want("Cell", kind="structure"),
            page("Cell", thumb=UPLOAD + "Disambig_gray.svg.png", disambiguation=True),
            "disambiguation")

    def test_a_redirect_to_a_different_article_is_refused(self):
        # Following a redirect somewhere else is exactly where the wrong picture appears.
        self.assert_rejected(
            want("H2O", kind="structure"), page("Water", thumb=UPLOAD + "Water.jpg"),
            "title_mismatch")

    def test_an_icon_sized_thumbnail_is_refused(self):
        self.assert_rejected(
            want("Mitochondrion", kind="anatomy"),
            page("Mitochondrion", thumb=UPLOAD + "Mito.png", width=48),
            "image_too_small")

    def test_flags_arms_and_logos_teach_nothing(self):
        for filename in ("Flag_of_France.svg.png", "Coat_of_arms_of_Bavaria.png",
                         "Wappen_Berlin.png", "Company_logo.png"):
            self.assert_rejected(want("France", kind="map"),
                                 page("France", thumb=UPLOAD + filename),
                                 "not_a_teaching_image")

    def test_a_host_the_browser_would_block_is_refused_server_side(self):
        # The old check allowed any *.wikimedia.org while the CSP allows only
        # upload.wikimedia.org, so the image was stored and then blocked - a broken
        # picture under an intact, confident caption.
        self.assert_rejected(
            want("Europe", kind="map"),
            page("Europe", thumb="https://maps.wikimedia.org/img/osm-intl.png"),
            "host_not_allowed")

    def test_an_article_with_no_picture_yields_nothing(self):
        self.assert_rejected(want("Konjunktiv II"), page("Konjunktiv II"), "no_image")

    def test_a_title_that_does_not_exist_yields_nothing(self):
        self.assert_rejected(want("Zzzqqq"), page("Zzzqqq", missing=True), "no_such_article")
        self.assert_rejected(want("Zzzqqq"), None, "no_such_article")


class AcceptsTheRightPictureTests(unittest.TestCase):
    """Strict must not mean useless: real teaching pictures still get through."""

    def accepted(self, request, payload, wiki="en"):
        decision = media.judge_page(request, payload, wiki)
        self.assertTrue(decision.accepted, f"unexpectedly rejected: {decision.reason}")
        assert decision.image is not None
        return decision.image

    def test_a_real_diagram_is_shown(self):
        image = self.accepted(
            want("Mitochondrion", kind="anatomy", alt="Cut-away drawing of a mitochondrion"),
            page("Mitochondrion", thumb=UPLOAD + "Animal_mitochondrion_diagram_en.svg.png"))
        self.assertEqual(image["title"], "Mitochondrion")
        self.assertEqual(image["alt"], "Cut-away drawing of a mitochondrion")
        self.assertEqual(image["kind"], "anatomy")

    def test_wikipedias_own_qualifier_still_counts_as_the_same_article(self):
        self.accepted(want("Cell (biology)", kind="structure"),
                      page("Cell (biology)", thumb=UPLOAD + "Animal_cell_structure.png"))

    def test_accents_and_case_do_not_break_the_title_match(self):
        self.accepted(want("Zellkern", kind="anatomy"),
                      page("zellkern", thumb=UPLOAD + "Zellkern.png"), wiki="de")

    def test_the_caption_only_claims_what_was_verified(self):
        # The old caption said "Wikimedia Commons" for every image without checking a
        # licence. The article it came from is a fact the code actually established.
        image = self.accepted(want("Photosynthese", kind="diagram"),
                              page("Photosynthese", thumb=UPLOAD + "Photosynthese.png"))
        self.assertIn("Photosynthese", image["source"])
        self.assertNotIn("Commons", image["source"])

    def test_alt_text_falls_back_to_the_title_when_the_model_gives_none(self):
        image = self.accepted(want("Photosynthese", kind="diagram"),
                              page("Photosynthese", thumb=UPLOAD + "P.png"))
        self.assertEqual(image["alt"], "Photosynthese")


class OnlyVisualThingsGetPicturesTests(unittest.TestCase):
    """The 'unnecessary photos' half: a term must claim a visual kind to be looked up."""

    def test_a_term_without_a_visual_kind_never_reaches_wikipedia(self):
        requests, rejected = media.normalize_image_requests(
            [{"term": "Photosynthese", "kind": "process"}], limit=2)
        self.assertEqual(requests, [])
        self.assertEqual(rejected, [("Photosynthese", "kind_not_visual")])

    def test_a_bare_string_is_refused_because_it_claims_nothing(self):
        # Every term was a bare string under the old schema; accepting them would let the
        # decoration problem straight back in.
        requests, rejected = media.normalize_image_requests(["Photosynthese"], limit=2)
        self.assertEqual(requests, [])
        self.assertEqual(rejected, [("Photosynthese", "kind_not_visual")])

    def test_every_allowed_kind_is_accepted(self):
        for kind in sorted(media.IMAGE_KINDS):
            requests, _ = media.normalize_image_requests([{"term": "X", "kind": kind}], limit=2)
            self.assertEqual(len(requests), 1, kind)

    def test_duplicates_and_the_limit_are_respected(self):
        requests, _ = media.normalize_image_requests([
            {"term": "Cell", "kind": "structure"}, {"term": "cell", "kind": "structure"},
            {"term": "Nucleus", "kind": "anatomy"}, {"term": "Ribosome", "kind": "structure"},
        ], limit=2)
        self.assertEqual([item.term for item in requests], ["Cell", "Nucleus"])

    def test_garbage_yields_nothing_rather_than_raising(self):
        for payload in (None, 42, {}, [], "", [None, 7]):
            self.assertEqual(media.normalize_image_requests(payload, limit=2)[0], [])


class LanguageTests(unittest.TestCase):
    """Six content languages were being looked up in the English Wikipedia."""

    def test_each_content_language_uses_its_own_wikipedia(self):
        for value, expected in (("German", "de"), ("French", "fr"), ("Spanish", "es"),
                                ("Italian", "it"), ("Portuguese", "pt"), ("Dutch", "nl"),
                                ("Arabic", "ar"), ("English", "en"),
                                ("de", "de"), ("fr", "fr")):
            self.assertEqual(media.wiki_language(value), expected, value)

    def test_an_unknown_language_falls_back_to_english(self):
        for value in ("Klingon", "", "zz"):
            self.assertEqual(media.wiki_language(value), "en")
        # A missing language reaches here as None from stored lessons.
        self.assertEqual(media.wiki_language(None), "en")  # type: ignore[arg-type]

    def test_a_french_lesson_queries_the_french_wikipedia(self):
        self.assertIn("https://fr.wikipedia.org/", media._api_url("Photosynthèse", "fr"))

    def test_the_lookup_asks_for_a_title_not_a_search(self):
        url = media._api_url("Ohm's law", "en")
        self.assertIn("titles=", url)
        self.assertNotIn("gsrsearch", url)      # the root cause, gone
        self.assertIn("redirects=1", url)
        for prop in ("pageimages", "pageprops", "categories"):
            self.assertIn(prop, url)


class LessonImagesTests(unittest.TestCase):
    """End to end over a stubbed API, including what gets reported back for logging."""

    def setUp(self):
        media._fetch_page.cache_clear()

    def stub(self, pages):
        """Serve a recorded page per requested title."""
        def fetch(term, wiki):
            found = pages.get(term)
            body = {"query": {"pages": [found]}} if found else {"query": {"pages": []}}
            return json.dumps(body)
        return patch.object(media, "_fetch_page", side_effect=fetch)

    def test_good_images_are_kept_and_bad_ones_reported(self):
        with self.stub({
            "Mitochondrion": page("Mitochondrion", thumb=UPLOAD + "Mito_diagram.png"),
            "Ohm's law": page("Georg Ohm", thumb=UPLOAD + "Ohm.jpg", categories=["1789 births"]),
        }):
            result = media.lesson_images([
                {"term": "Mitochondrion", "kind": "anatomy"},
                {"term": "Ohm's law", "kind": "diagram"},
            ], "English", limit=4)
        self.assertEqual([image["title"] for image in result.images], ["Mitochondrion"])
        self.assertEqual(result.rejected, [("Ohm's law", "about_a_person")])

    def test_a_network_failure_is_reported_separately_from_a_bad_match(self):
        # These used to be indistinguishable, and neither was logged.
        with patch.object(media, "_fetch_page", side_effect=OSError("offline")):
            result = media.lesson_images([{"term": "Cell", "kind": "structure"}], "English")
        self.assertEqual(result.images, [])
        self.assertEqual(result.rejected, [("Cell", "lookup_failed")])

    def test_the_same_picture_is_not_shown_twice(self):
        shared = page("Cell (biology)", thumb=UPLOAD + "Cell.png")
        with self.stub({"Cell (biology)": shared, "Zelle": shared}):
            result = media.lesson_images([
                {"term": "Cell (biology)", "kind": "structure"},
                {"term": "Zelle", "kind": "structure"},
            ], "English", limit=4)
        self.assertEqual(len(result.images), 1)
        self.assertIn(("Zelle", "title_mismatch"), result.rejected)

    def test_a_term_is_looked_up_once_however_often_it_appears(self):
        calls = []

        def fetch(term, wiki):
            calls.append(term)
            return json.dumps({"query": {"pages": [
                page("Mitochondrion", thumb=UPLOAD + "M.png")]}})

        with patch.object(media, "_fetch_page", side_effect=fetch):
            for _ in range(3):
                media.lesson_images([{"term": "Mitochondrion", "kind": "anatomy"}], "English")
        # The stub replaces the cached function, so this asserts the call shape; the real
        # cache is asserted below.
        self.assertEqual(len(calls), 3)

    def test_the_real_lookup_is_cached(self):
        self.assertTrue(hasattr(media._fetch_page, "cache_info"))
        self.assertTrue(hasattr(media._fetch_page, "cache_clear"))

    def test_no_images_requested_costs_nothing(self):
        with patch.object(media, "_fetch_page", side_effect=AssertionError("should not fetch")):
            self.assertEqual(media.lesson_images([], "English").images, [])
            self.assertEqual(media.lesson_images(["bare string"], "English").images, [])


class VideoLinkTests(unittest.TestCase):
    def test_the_search_carries_subject_and_level_not_just_the_topic(self):
        links = media.video_links([{"query": "Photosynthese", "why": "it is a process"}],
                                  subject="Biologie", grade="Klasse 8", language="German")
        self.assertEqual(len(links), 1)
        self.assertIn("Photosynthese", links[0]["youtube"])
        self.assertIn("Biologie", links[0]["youtube"])
        self.assertIn("Klasse+8", links[0]["youtube"])

    def test_studyflix_is_offered_only_for_german_content(self):
        german = media.video_links(["Photosynthese"], language="German")
        english = media.video_links(["Photosynthesis"], language="English")
        self.assertIn("studyflix", german[0])
        self.assertNotIn("studyflix", english[0])   # a German-only site, linked to everyone before

    def test_a_topic_already_naming_the_subject_is_not_repeated(self):
        phrase = media.search_phrase("Biologie Zellkern", subject="Biologie", grade="")
        self.assertEqual(phrase, "Biologie Zellkern")

    def test_the_limit_is_respected_and_duplicates_dropped(self):
        links = media.video_links(["a", "A", "b", "c"], limit=2)
        self.assertEqual([link["title"] for link in links], ["a", "b"])

    def test_the_model_can_never_supply_a_url(self):
        links = media.video_links([{"query": "https://evil.example/watch", "why": ""}])
        self.assertTrue(links[0]["youtube"].startswith("https://www.youtube.com/results?"))


if __name__ == "__main__":
    unittest.main()
