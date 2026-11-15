# Public reviews (About page)

Students review Learnova in "Was unsere Lernenden über Learnova sagen" ("What students think
about Learnova") on `/about`. The rules live in `learnova/reviews.py`, the routes in `app.py`
(`submit_review`, `delete_review`, `report_review`, `reviews_api`, `admin_reviews`,
`moderate_review`), and the tests in `tests/test_reviews.py`.

**Nothing is invented.** There are no seed or example reviews and no fallback testimonials.
With no published review the page shows an empty state, never "0.0 stars".

## Who can write

The server checks every submission (`review_eligibility`). It needs:
- a signed-in account;
- not blocked by a moderator (`review_block`);
- **Level 3 or higher**, computed from the account's XP (`level_for_xp`).

The owner is always the session's user; an id in the request is ignored. Simulated league
rivals are `league_rival` rows, not accounts: they cannot sign in, so they can never write.
Below Level 3 the page says "Du kannst ab Level 3 eine Bewertung abgeben." with a link to
keep learning. The form appears by itself once the level is reached.

## One review per account

- `product_review.user_id` is unique, and the rating has a CHECK constraint of 1–5.
- A second submit edits the review: `edited_at` is set and `created_at` never changes.
  A double click cannot make two reviews (`INSERT … ON CONFLICT DO NOTHING`).
- The owner can delete their own review. There is no route that takes another person's
  review id for editing or deleting.

## Publishing and moderation

The workflow follows the community library: quality never blocks, and reports bring in a
person but never hide anything by themselves.

- **Published at once**, unless the text contains a link, an email address, a phone number,
  a character repeated 10 times, or shouts a long text in capitals. Those wait as `pending`.
  **A low rating or harsh criticism is never held.**
- **Reports:** a signed-in reader can report once per review, with a reason. The first report
  creates one administrator alert (an `AdminNotification`, emailed when mail is set up).
- **Moderators** (`COMMUNITY_MODERATORS` or `PREMIUM_ADMIN_IDENTIFIERS`) use `/admin/reviews`
  to publish, hide, reject or restore a review, or to hide it and block its author. Each
  decision is in `review_moderation_event`.
- An edit of a hidden or rejected review goes back to `pending`.

## Order, filters and summary

- **Recommended (default):** rating descending, then newest. Also: newest, oldest, highest, lowest.
- **Filters:** all, or exactly 5, 4, 3, 2 or 1 stars. "All reviews" always contains every
  published rating, and the page says that the default order is a ranking of the complete list.
- **URLs:** filters live in the URL (`/about?rating=4&sort=newest#reviews`), so links can be shared.
- **Pages:** 6 reviews per page on the server; "Show more" loads the next page in place
  (`/api/reviews`).
- **Summary:** average, total, count and percentage per star, from one `GROUP BY` over
  **published** reviews only. Pending, rejected and hidden reviews are never counted.

## Safety

- Text is cleaned (trimmed, control and direction-override characters removed) and limited
  to 1,000 characters.
- Text is shown only through Jinja's auto-escaping (`white-space: pre-line`) and, in the
  preview, `textContent`.
- Every POST needs CSRF and is rate-limited.
- Cards show only the public identity card: username and avatar, plus the level when the
  profile is public. Never an email or an account id.

## Settings

Settings → Profile → "Deine Bewertung von Learnova" shows the student's own review (stars,
text, date, status) with Edit and Delete. Otherwise it shows when they can write one.
