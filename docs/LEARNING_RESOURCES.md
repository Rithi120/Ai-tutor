# Learning resources: pictures and videos in chat and Short Lessons

Spec Phase 7 (classroom readiness, 2026-11-13). Learnova can add an educational picture or a
YouTube video to a chat answer or a Short Lesson **when seeing it teaches more than words**,
and only as a supplement to the explanation. Nothing here is invented by the model: the model
proposes a topic; the server finds, checks and links the resource.

## What the student sees

| Where | Picture | Video |
| --- | --- | --- |
| Chat (`/`, `/assistant`) | Appears by itself under the reply, with its credit line ("Bild: Ann Example, CC BY-SA 4.0 (Wikimedia Commons)", linked to the file page and the licence). | One tap: **Video dazu ansehen** → up to two checked video cards. Only with a YouTube key. |
| Short Lesson (`/lesson`) | As before (lesson JSON `image_media`), now only with a checked licence and the credit line. | Without a key: the YouTube/Studyflix links stay, labelled honestly as searches ("Auf YouTube suchen: „…“"). With a key: one tap **Passende Videos zeigen** replaces them with checked cards. |

A video card shows thumbnail, title, channel, length, a **Zusätzlich** badge and "Öffnet auf
YouTube". It is a plain link (`target=_blank`, `rel="noopener noreferrer"`): no iframe, no
YouTube script on the page. The lesson media card's kicker reads "Zusätzlich – ersetzt die
Erklärung nicht".

## Who decides that a resource helps

**Short Lessons** keep the existing lesson-JSON decision (`video_usefulness`,
`image_usefulness` 0-10, with explicit "no media for vocabulary, definitions, simple facts"
rules in the `/api/analyze` prompt); the scores cap how many items appear.

**Chat:** the tutoring layer (`learnova/assistant/presets.py`, `assistant_chat:v6`) has one
extra output line, 22 estimated tokens (the layer is 393 of its 420 cap):

```
If a picture teaches more: MEDIA: diagram|map|video | <reply-language Wikipedia title>
```

The line is cut off like CHIPS/TOPIC (`formatting.reply_trailers`) and is only a proposal.
`learnova/assistant/resources.media_offer` is a deterministic veto. No offer when:

- there is no MEDIA line, or the term looks like a link;
- the reply is not about a school subject (no subject, "Other", `TOPIC: none`);
- the reply is shorter than 350 characters (a definition, a fact, a yes/no);
- the subject is a language (English, German, Latin, …) and the proposal is a picture; a
  language video needs a reply of at least 900 characters and a topic/term that is not about
  a word's meaning (vocabulary, Vokabel, Bedeutung, meaning, translation, definition, …). A
  vocabulary definition therefore never gets a resource, whatever the model wrote.

A MEDIA term is one title: a model that copies the template ("diagram|map|video | Ohm")
keeps its first kind, and the other kinds and any further `|` are dropped from the term.

Diagram/map offers fetch the picture automatically and also offer the video tap (when a key
is set); `video` offers only the tap. The offer (kind, term, canonical subject, content
language) is stored in `conversation_message.resources_json`; the page only learns *whether*
a picture or video can be fetched, never the term.

**Honesty about links.** `SHARED_RULES` tells the assistant never to write links, URLs, video
titles or channel names it has not been shown. Any YouTube link it writes anyway is removed
from the stored reply (`resources.strip_video_links`; the markdown label stays), and
`static/js/markdown.js` refuses to link youtube.com / youtu.be in any rendered reply,
including replies stored before this change. The only clickable videos are checked cards.

## Pictures: Wikimedia with a licence check

`learnova/media_enrichment.py` (the one image system). The existing relevance rules are
unchanged (exact title, no biographies, no disambiguation, no redirect to another title, no
icons/flags/logos, `upload.wikimedia.org` and `thumb.wikimedia.org` only -
`ALLOWED_IMAGE_HOSTS`; since 2026-11-14 Wikipedia's API gives scaled thumbnails on
`thumb.wikimedia.org`, and with the upload host alone no picture was ever shown, see
REAL_WORLD_ACCEPTANCE_AUDIT.md). New:

1. The page lookup asks for the file name (`piprop=thumbnail|name`) and pins
   `pilicense=free`.
2. A second, cached lookup (`prop=imageinfo&iiprop=url|extmetadata`) reads the file's
   `LicenseShortName`, `LicenseUrl`, `Artist`, `AttributionRequired`, `NonFree`.
3. `judge_licence` accepts only CC0, public domain (PD-*), CC BY and CC BY-SA. Everything
   else (GFDL-only, NC/ND, fair use, unknown) is refused with a named reason; an attribution
   licence without an author is refused (`author_unknown`). The author is stripped of HTML and
   capped at 120 characters. Links go only to the file page (commons.wikimedia.org /
   *.wikipedia.org) and the licence deed (creativecommons.org).
4. A failed licence lookup hides the picture (`licence_lookup_failed`) and, in chat, is tried
   again on the next view; a settled answer (picture or "none") is stored with the reply.
5. Two lesson pictures are looked up in parallel (2 workers), so the request waits for the
   slowest picture, not the sum.

Lessons stored before the licence check have pictures without credit data; the page no longer
shows those (their attribution cannot be met).

## Videos: YouTube Data API v3

`learnova/youtube_videos.py`, built like `learnova/vocabulary/translate.py` (HTTP seam, never
raises, pure judgement functions). Per uncached query:

1. `search.list` (100 units): `q` = topic + subject + class **in the content language**
   ("Ohmsches Gesetz Physik Klasse 8"), `relevanceLanguage` = content language,
   `regionCode` = `YOUTUBE_REGION_CODE`, `safeSearch=strict`, `type=video`, 8 results.
2. `videos.list` (1 unit) for those ids. A video missing from the answer (deleted, private) is
   dropped as `unavailable`; then `judge_video` drops: not public / not processed,
   age-restricted, blocked in the region, live/upcoming, shorter than 60 s or longer than
   30 min, a title announcing shorts/reaction/prank/lyrics/music video, an incomplete
   snippet, and **off-topic** videos (at least one topic word in the title and half of them in
   title, description, tags or channel; word endings tolerated, subject/class words ignored).
3. Ranking: the content language first (`defaultAudioLanguage`/`defaultLanguage`), then
   `YOUTUBE_TRUSTED_CHANNELS` (a boost, never a filter), then YouTube's order.
4. A card holds title and channel **exactly as videos.list returned them**; the watch link,
   channel link and thumbnail path are built from the validated ids
   (`^[A-Za-z0-9_-]{11}$`, `^UC[A-Za-z0-9_-]{22}$`), never from model text or a URL in the
   response.

The browser never sends search text: `GET /api/lessons/<session_id>/videos` takes the query
from the stored lesson (`video_links[0]`, the model's own topic), and
`GET /api/assistant/messages/<id>/resources?part=videos` from the reply's stored offer. Both
are owner-checked (another student's lesson/message is a 404) and rate-limited (20 and 60 per
hour).

### Cache, re-check and quota

`learning_resource_cache` (migration `046_learning_resources`) is shared by all students:
one row per search (key = sha256 of provider, language, region, folded query). Payload =
cards, reason, rejected ids. Empty results are stored too (a dead topic is not searched
again); failed searches are stored and retried after an hour.

- Rows are reused for `YOUTUBE_CACHE_DAYS` (default 7, clamped 1-30) and deleted after 30
  days, in line with the API policy's 30-day limit for stored API data. Video cards are kept
  only in this table - never in the lesson JSON or the chat message - so deleting a row
  deletes the stored API data.
- Cards older than 24 h are re-checked with one `videos.list` call before they are shown, so
  a deleted or privated video disappears (a failed re-check keeps the cards).
- Daily cap: a new search runs only while today's searches (rows created since midnight US
  Pacific, when the quota resets) are below `YOUTUBE_DAILY_SEARCH_LIMIT` (default 80).
- Per student: at most `YOUTUBE_SEARCHES_PER_STUDENT` (default 20) *new* searches a day (a shared Flask-Limiter scope over both video endpoints that only counts requests that really searched). A student over the share gets 429 on video taps for the rest of the day, cached topics included; pictures are not affected.
- When Google answers `quotaExceeded`/`dailyLimitExceeded`, a `youtube_quota` marker row stops
  all searches until the next Pacific midnight. Cached answers keep working.

**Quota maths.** The default project quota is 10,000 units/day. A search costs 101 units
(search.list 100 + videos.list 1); a re-check 1. 80 searches = 8,080 units, leaving ~1,900
units for re-checks. Because results are cached per topic+class+language for 7 days and
shared by every student, 80 *new* topics per day is the real limit, not 80 taps. More needs
a quota increase from Google (owner item).

### Thumbnails through the app (privacy, CSP)

Cards point at `/media/yt-thumb/<id>`. That route (login required, 300/hour) accepts only an
11-character id that appears in a cached search payload (it is not an open proxy), fetches
`https://i.ytimg.com/vi/<id>/mqdefault.jpg` server-side (no redirects, JPEG/WebP only,
≤ 300 KB), keeps up to 300 (at most 12 MB) in memory and serves them with `Cache-Control: private,
max-age=86400`. No student IP or cookie reaches Google, and the CSP `img-src` stays
`'self' data: blob: https://upload.wikimedia.org https://thumb.wikimedia.org` with no `frame-src`. Clicking a card opens
youtube.com in a new tab - that is the moment the student's browser contacts YouTube.

Wikimedia pictures are still loaded directly from Wikimedia (`upload.wikimedia.org` or
`thumb.wikimedia.org`; `static/js/resources.js` `IMAGE_URL` accepts the same two hosts; the
referrer is suppressed with `referrerpolicy=no-referrer`).

### The key

`YOUTUBE_API_KEY` is read in `learnova/config.py` and passed only to `youtube_videos`, where
it goes in the `X-Goog-Api-Key` request header. It is never put in a URL (requests/urllib
quote URLs in exceptions), no error string is built from an exception's text, and log lines
carry only ids and reasons. Pages and JSON get a boolean (`videos_available`,
`resources.video`), never the key. Tests assert the key is absent from responses, pages, the
cache and the captured log.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `FEATURE_LEARNING_RESOURCES` | `true` | Chat resources, the lesson video endpoint and the thumbnail proxy. Off: none of them (lesson search links follow `FEATURE_LESSON_MEDIA` as before). |
| `YOUTUBE_API_KEY` | empty | YouTube Data API v3 key. Empty: no video cards anywhere; lessons keep honest search links. Set in the Render dashboard (`render.yaml` lists it with `sync: false`). |
| `YOUTUBE_REGION_CODE` | `DE` | `regionCode` for the search and the region-block check. |
| `YOUTUBE_DAILY_SEARCH_LIMIT` | `80` | New searches per Pacific day, across all users. 0 disables searching (cache still served). |
| `YOUTUBE_SEARCHES_PER_STUDENT` | `20` | New (uncached) searches one student may cause per day (Flask-Limiter, counted only when a search ran; picture requests are exempt). Over it, video taps get 429 until the next day, so one account cannot spend the shared daily limit. |
| `YOUTUBE_CACHE_DAYS` | `7` | Reuse a search this long (1-30). |
| `YOUTUBE_TIMEOUT_SECONDS` | `4` | Per API/thumbnail request (1-15). |
| `YOUTUBE_TRUSTED_CHANNELS` | empty | Comma-separated channel ids (`UC…`) ranked first among relevant videos. |

Only the key was added to `render.yaml`; the others use the code defaults.

## Endpoints

| Method and path | What |
| --- | --- |
| `GET /api/assistant/messages/<id>/resources` | The reply's picture (resolved once, then stored). `{ok, part:"image", image|null, reason}` |
| `GET /api/assistant/messages/<id>/resources?part=videos` | Checked cards from the cache/search. `{ok, part:"videos", items, reason}`; `reason` is `videos_unavailable`, `daily_limit`, `quota`, `no_results`, … when there are none. |
| `GET /api/lessons/<session_id>/videos` | Checked cards for a Short Lesson. 404 `lesson_expired` (not yours), `videos_unavailable` (no key), `no_video_offer`. |
| `GET /media/yt-thumb/<id>` | The thumbnail proxy above. |

`/api/analyze` and the lesson bootstrap gained `videos_available` (boolean).

## Data

- `conversation_message.resources_json TEXT NOT NULL DEFAULT '{}'` - `{"offer": {kind, term,
  subject, language}, "image": {...}|null}`; existing rows keep `{}`.
- `learning_resource_cache` - see above.
- Migration `046_learning_resources` (additive, idempotent, recorded once).

## Limits and open items

- **Owner / legal (open):** sign-off on the YouTube API Services Terms of Service and
  Developer Policies (references to the YouTube ToS and Google Privacy Policy in Learnova's
  terms/privacy page, YouTube branding on cards, the 30-day data rule as implemented above),
  and on showing YouTube links to under-13 students. Until that sign-off, leave
  `YOUTUBE_API_KEY` unset: the app then behaves exactly as before for videos, with honest
  search labels.
- The owner provides the key (Google Cloud project) and decides on a quota increase.
- Relevance is lexical: a correct video titled with different words is missed (never a wrong
  one shown for that reason), and a clickbait title repeating the topic words can pass. The
  rejection reasons are logged (`youtube.search … dropped=id:reason`) to measure this.
- The search language follows the lesson's content language; grade matching is by the class
  in the query ("Klasse 8"), not by any YouTube metadata (there is none).
- Relevance of a picture still rests on the exact Wikipedia title the model gives; in chat
  that is the MEDIA term. A picture is the article's lead image, which can be a photo rather
  than a diagram.
- Continue Learning, project, practice and similar-mistake lessons (`start_saved_practice`)
  still get no media; only `/api/analyze` Short Lessons and the chat do.
- The chat tutorial has no step for the new card (the card appears only on some replies).
- Thumbnails are cached in process memory (lost on restart, per worker); the first view after
  a restart fetches again.

## Tests

`tests/test_youtube_videos.py` (pure, fake HTTP), `tests/test_media_enrichment.py` (licence
checks), `tests/test_learning_resources.py` (spec Test 9: chat and lesson flows, veto, key
never leaked, quota/cache/re-check, thumbnail proxy, ownership, migration),
`tests/test_learning_resources_browser.py` (resources.js and markdown.js in Chrome/Edge, and the
real chat and lesson pages in light and dark at phone width).
