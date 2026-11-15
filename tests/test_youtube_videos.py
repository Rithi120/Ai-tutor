"""learnova/youtube_videos.py over recorded API answers: no network, no key ever leaked.

Every card the page shows must be built from what search.list and videos.list returned
(title, channel exactly as given), with links built from validated ids; a video that is
gone, private, age-restricted, blocked in Germany, too short, too long or off-topic is
dropped with a named reason.
"""

import json
import unittest

from learnova import youtube_videos as yt

KEY = "AIzaSyTEST-not-a-real-key-0123456789"
CHANNEL = "UC" + "a" * 22
OTHER_CHANNEL = "UC" + "b" * 22


def video(video_id, *, title="Das Ohmsche Gesetz einfach erklärt", channel="Physik Kanal", channel_id=CHANNEL,
          duration="PT6M30S", privacy="public", upload="processed", rating=None, region=None,
          description="Spannung, Stromstärke und Widerstand: das ohmsche Gesetz U = R · I.",
          audio="de", live="none"):
    details = {"duration": duration}
    if rating:
        details["contentRating"] = {"ytRating": rating}
    if region:
        details["regionRestriction"] = region
    return {
        "id": video_id,
        "snippet": {"title": title, "channelTitle": channel, "channelId": channel_id,
                    "description": description, "defaultAudioLanguage": audio, "liveBroadcastContent": live,
                    "thumbnails": {"medium": {"url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"}}},
        "status": {"privacyStatus": privacy, "uploadStatus": upload, "embeddable": True},
        "contentDetails": details,
    }


def search_answer(*ids):
    return json.dumps({"items": [{"id": {"kind": "youtube#video", "videoId": video_id}} for video_id in ids]})


class FakeHttp:
    """Answers search.list and videos.list from recorded payloads and records every call."""

    def __init__(self, search_ids=(), videos=(), *, search_status=200, search_body=None, error=None):
        self.search = search_body if search_body is not None else search_answer(*search_ids)
        self.search_status = search_status
        self.videos = {item["id"]: item for item in videos}
        self.error = error
        self.calls = []

    def __call__(self, method, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": dict(params or {}),
                           "headers": dict(headers or {}), "timeout": timeout})
        if self.error is not None:
            raise self.error
        if url == yt.SEARCH_URL:
            return self.search_status, self.search
        if url == yt.VIDEOS_URL:
            ids = str(params["id"]).split(",")
            return 200, json.dumps({"items": [self.videos[i] for i in ids if i in self.videos]})
        raise AssertionError(f"unexpected URL {url}")


def find(http, **options):
    defaults = dict(topic="Ohmsches Gesetz", key=KEY, language="German", region="DE", limit=2, http=http)
    defaults.update(options)
    return yt.find_videos("Ohmsches Gesetz Physik Klasse 8", **defaults)


class JudgeVideoTests(unittest.TestCase):
    tokens = yt.topic_tokens("Ohmsches Gesetz")

    def judge(self, item):
        return yt.judge_video(item, tokens=self.tokens, region="DE")

    def test_a_good_german_explanation_passes(self):
        self.assertEqual(self.judge(video("abcdefghijk")), (True, "ok"))

    def test_every_refusal_has_its_reason(self):
        cases = {
            "not_public": video("abcdefghijk", privacy="private"),
            "not_processed": video("abcdefghijk", upload="uploaded"),
            "age_restricted": video("abcdefghijk", rating="ytAgeRestricted"),
            "region_blocked": video("abcdefghijk", region={"blocked": ["DE"]}),
            "live": video("abcdefghijk", live="live"),
            "too_short": video("abcdefghijk", duration="PT45S"),
            "too_long": video("abcdefghijk", duration="PT1H5M"),
            "no_duration": video("abcdefghijk", duration=""),
            "not_educational": video("abcdefghijk", title="Ohmsches Gesetz #shorts"),
            "not_relevant": video("abcdefghijk", title="Photosynthese einfach erklärt",
                                  description="Pflanzen und Licht"),
            "incomplete": video("abcdefghijk", channel_id="not-a-channel"),
            "unavailable": {"id": "bad"},
        }
        for reason, item in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(self.judge(item), (False, reason))

    def test_an_allow_list_without_germany_blocks(self):
        self.assertEqual(self.judge(video("abcdefghijk", region={"allowed": ["US"]})), (False, "region_blocked"))
        self.assertTrue(self.judge(video("abcdefghijk", region={"allowed": ["DE", "AT"]}))[0])

    def test_relevance_tolerates_word_endings_but_needs_the_topic_in_the_title(self):
        self.assertTrue(yt.relevance(self.tokens, "Das ohmsche Gesetz", "")[0])
        # Topic only in the description: not enough.
        self.assertFalse(yt.relevance(self.tokens, "Physik Klasse 8 Folge 3", "ohmsches gesetz")[0])
        self.assertEqual(yt.topic_tokens("Das ohmsche Gesetz einfach erklärt Klasse 8"), ["ohmsche", "gesetz"])

    def test_durations_parse(self):
        self.assertEqual(yt.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(yt.parse_duration("PT59S"), 59)
        self.assertIsNone(yt.parse_duration("garbage"))
        self.assertIsNone(yt.parse_duration("P"))


class CardTests(unittest.TestCase):
    def test_a_card_is_exactly_what_the_api_returned_with_links_built_from_ids(self):
        card = yt.card(video("abc_def-123", title="Ohmsches  Gesetz – Grundlagen", channel="Lehrer Kanal"))
        self.assertEqual(card, {
            "video_id": "abc_def-123",
            "url": "https://www.youtube.com/watch?v=abc_def-123",
            "thumbnail": "/media/yt-thumb/abc_def-123",
            "title": "Ohmsches Gesetz – Grundlagen",
            "channel": "Lehrer Kanal",
            "channel_url": f"https://www.youtube.com/channel/{CHANNEL}",
            "duration_seconds": 390, "language": "de", "source": "YouTube", "supplementary": True,
        })

    def test_ids_that_fail_the_pattern_never_become_links(self):
        for bad in ("short", "abc/def?x=1", "abcdefghijk&x", "javascript:x"):
            self.assertIsNone(yt.card(video(bad)), bad)
        self.assertIsNone(yt.card(video("abcdefghijk", channel_id="UCshort")))
        self.assertEqual(yt.search_ids({"items": [{"id": {"videoId": "../../evil"}}, {"id": {"videoId": "abcdefghijk"}},
                                                  {"id": {"videoId": "abcdefghijk"}}]}), ["abcdefghijk"])

    def test_german_audio_ranks_first_then_trusted_channels(self):
        english = video("eeeeeeeeeee", audio="en")
        german = video("ggggggggggg", audio="de")
        trusted = video("ttttttttttt", audio="en", channel_id=OTHER_CHANNEL)
        ranked = yt.rank([english, trusted, german], language="German", trusted=frozenset({OTHER_CHANNEL}))
        self.assertEqual([item["id"] for item in ranked], ["ggggggggggg", "ttttttttttt", "eeeeeeeeeee"])


class FindVideosTests(unittest.TestCase):
    def test_search_then_confirm_and_drop_what_is_gone(self):
        http = FakeHttp(["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"],
                        [video("aaaaaaaaaaa", title="Ohmsches Gesetz Versuch"), video("ccccccccccc", privacy="private")])
        result = find(http)
        self.assertEqual([card["video_id"] for card in result.cards], ["aaaaaaaaaaa"])
        self.assertEqual(result.cards[0]["title"], "Ohmsches Gesetz Versuch")
        self.assertIn(("bbbbbbbbbbb", "unavailable"), result.rejected)       # deleted: missing from videos.list
        self.assertIn(("ccccccccccc", "not_public"), result.rejected)
        self.assertTrue(result.searched)
        self.assertEqual([call["url"] for call in http.calls], [yt.SEARCH_URL, yt.VIDEOS_URL])

    def test_the_search_is_strict_in_the_content_language_and_region(self):
        http = FakeHttp([], [])
        find(http)
        params = http.calls[0]["params"]
        self.assertEqual(params["q"], "Ohmsches Gesetz Physik Klasse 8")
        self.assertEqual((params["relevanceLanguage"], params["regionCode"], params["safeSearch"], params["type"]),
                         ("de", "DE", "strict", "video"))

    def test_the_key_travels_only_in_the_header(self):
        http = FakeHttp(["aaaaaaaaaaa"], [video("aaaaaaaaaaa")])
        result = find(http)
        for call in http.calls:
            self.assertEqual(call["headers"]["X-Goog-Api-Key"], KEY)
            self.assertNotIn(KEY, json.dumps(call["params"]))
            self.assertNotIn(KEY, call["url"])
            self.assertNotIn("key", call["params"])
        self.assertNotIn(KEY, json.dumps(result.cards) + result.reason + repr(result.rejected))

    def test_a_failure_never_quotes_the_exception_which_may_carry_the_url(self):
        error = ConnectionError(f"HTTPSConnectionPool: /youtube/v3/search?key={KEY} failed")
        result = find(FakeHttp(error=error))
        self.assertEqual((result.cards, result.reason), ([], "ConnectionError"))
        self.assertNotIn(KEY, repr(result))

    def test_quota_exhaustion_is_reported_as_such(self):
        body = json.dumps({"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}})
        result = find(FakeHttp(search_status=403, search_body=body))
        self.assertEqual((result.cards, result.reason, result.searched), ([], "quota", True))
        self.assertEqual(find(FakeHttp(search_status=500, search_body="{}")).reason, "http_500")

    def test_no_key_means_no_call(self):
        http = FakeHttp(["aaaaaaaaaaa"], [video("aaaaaaaaaaa")])
        self.assertEqual(find(http, key="").reason, "no_key")
        self.assertEqual(http.calls, [])

    def test_the_query_is_plain_words(self):
        self.assertEqual(yt.clean_query('Ohm https://evil.example/x "<b>Gesetz</b>'), "Ohm b Gesetz /b")
        self.assertLessEqual(len(yt.clean_query("x" * 500)), yt.MAX_QUERY_CHARS)

    def test_recheck_drops_deleted_and_private_videos(self):
        cards = [yt.card(video("aaaaaaaaaaa")), yt.card(video("bbbbbbbbbbb")), yt.card(video("ccccccccccc"))]
        http = FakeHttp([], [video("aaaaaaaaaaa"), video("ccccccccccc", privacy="private")])
        kept = yt.recheck(cards, key=KEY, region="DE", http=http)
        self.assertEqual([card["video_id"] for card in kept], ["aaaaaaaaaaa"])
        self.assertEqual(http.calls[0]["url"], yt.VIDEOS_URL)
        self.assertIsNone(yt.recheck(cards, key=KEY, http=FakeHttp(error=TimeoutError())))


class ThumbnailTests(unittest.TestCase):
    def test_only_a_valid_id_is_fetched_from_ytimg(self):
        seen = []

        def get(url, timeout=None):
            seen.append(url)
            return 200, b"\xff\xd8jpeg", "image/jpeg"

        self.assertIsNone(yt.fetch_thumbnail("../../x", get=get))
        self.assertEqual(yt.fetch_thumbnail("abcdefghijk", get=get), (b"\xff\xd8jpeg", "image/jpeg"))
        self.assertEqual(seen, ["https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg"])

    def test_anything_but_a_small_image_is_refused(self):
        self.assertIsNone(yt.fetch_thumbnail("abcdefghijk", get=lambda url, timeout=None: (200, b"<html>", "text/html")))
        self.assertIsNone(yt.fetch_thumbnail("abcdefghijk", get=lambda url, timeout=None: (404, b"", "image/jpeg")))
        big = b"x" * (yt.MAX_THUMBNAIL_BYTES + 1)
        self.assertIsNone(yt.fetch_thumbnail("abcdefghijk", get=lambda url, timeout=None: (200, big, "image/jpeg")))

        def boom(url, timeout=None):
            raise OSError("down")
        self.assertIsNone(yt.fetch_thumbnail("abcdefghijk", get=boom))


if __name__ == "__main__":
    unittest.main()
