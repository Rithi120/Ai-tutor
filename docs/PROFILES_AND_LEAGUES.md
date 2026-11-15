# Profiles, avatars, challenges and Learnova Leagues

One progression system. Lifetime XP (the existing `XPTransaction` ledger and
`UserGamificationProfile.total_xp`) sets the level; the level unlocks avatars;
challenges pay XP into the same ledger; leagues rank the XP each learner earns inside a
competition week. Nothing has its own XP counter.

## Where it lives

| Part | Module | Owns |
| --- | --- | --- |
| Avatar catalogue | `learnova/gamification/avatars.py` | 20 characters, unlock level, rarity, accent |
| Artwork | `static/avatars/<id>.svg` | original, self-contained SVGs (no scripts, no external references) |
| Username rules | `learnova/gamification/usernames.py` | format, reserved and offensive names, cooldown and hold |
| Challenges | `learnova/gamification/challenges.py` | catalogue, selection, period bounds, what an event counts for |
| Leagues | `learnova/gamification/leagues.py` | tiers, ranking and tie-breaks, zone sizes, outcomes |
| Persistence and routes | `app.py` | models, award hooks, pages, APIs, CLI |

The four modules are Flask-free and unit-tested without a request.

## Profiles and usernames

* `/profile` - the owner's profile: large avatar, level and XP progress, streak, highlights,
  daily and weekly challenges, current league, the avatar collection, achievements and
  statistics. "Edit profile" goes to Settings -> Profile.
* `/u/<username>` - the public profile. Avatar, username and Premium always; level, streak,
  achievements and statistics only when the learner left **Public profile** on. Never the
  league, the email or anything from the account.
* Settings -> Profile: change username (asks for the current password, because the name
  is also a login), change avatar, public profile on/off, leagues on/off.

Username rules: 3-30 characters, lower-case letters, digits, `.`, `-`, `_`, starting with a
letter or digit, no trailing `.`/`-`, no `..`/`__`. Names that read as official
(`admin`, `learnova_team`, `moderator`, `support`, ...) and clearly offensive terms
(English and German, after folding look-alike characters; short terms only as a whole
word, so "classic" and "Kassel" pass) are refused. A name on `COMMUNITY_MODERATORS`,
`PREMIUM_ADMIN_IDENTIFIERS` or `AI_DIAGNOSTICS_ADMINS` is refused to everyone, and a
released name is held for `USERNAME_HOLD_DAYS` (90) - both because those allowlists match
on username, so a rename must never hand someone else an administrator's access.
Changes are limited to one per `USERNAME_CHANGE_COOLDOWN_DAYS` (30) and every change is
recorded in `username_change`. Community sets, leagues and profiles all look the name up
by account id, so a rename shows everywhere at once.

## Avatars

Twenty characters, one per level 1-20; rarity rises from common to legendary. The first is
available immediately. `sync_avatar_unlocks` records each unlock in `user_avatar_unlock`
(on level-up inside `award_xp`, and on the profile pages); unlocks are permanent because
lifetime XP never decreases and the rows are never deleted. `POST /api/profile/avatar`
checks the unlock on the server; a locked or unknown id is refused. Choosing an avatar
changes only `user.avatar_id`. Anywhere an avatar is shown, a choice that is not unlocked
(say, set directly in the database) falls back to the first avatar.

## Community identity

`serialize_public_set` adds `author_identity` when a set is published under the
username: avatar, username, level and a profile link (both only if the profile is
public), and `premium`. Nickname and anonymous sets carry no identity at all. Premium
comes from `premium_user_ids()`, which reads `premium_entitlement` by date on every
request - an expired or revoked subscription loses the badge immediately, and no client
field can set it. Lists build identities in one batch.

## Challenges

The old two fixed missions became a catalogue in the same `user_mission` table (new
columns `metric`, `difficulty`, `params_json`, `detail_json`, and a unique key
`(user_id, mission_key, starts_at)`). Each day and each week a learner gets three: one
easy, one medium, one hard, chosen with a seed of learner and period (stable across
requests, different for every learner and every period), preferring features the learner
used in the last 30 days. Lesson challenges are open to everyone.

| Metric | Counts |
| --- | --- |
| correct_answers | graded answers marked correct (lessons, flashcards, vocabulary) |
| lessons | lessons completed through the knowledge gate (`quiz_completed`) |
| lessons_without_hints | lessons with no hinted answer (explicit `hints_used: false`) |
| hard_correct | correct answers to difficulty-3 questions or hard flashcards |
| tests | completed flashcard tests and vocabulary sessions |
| improved_results / improved_subjects | a result that beat the previous one at the same set (distinct subjects for the weekly one) |
| subject_answers | answers in the learner's weakest subject (lowest mastery under 60) |
| flashcard_reviews, vocabulary_reviews | reviewed items |
| active_days | distinct study days |

Progress moves inside `record_learning_event`, which only runs for an event it has not
seen, so a replayed request never counts twice. Counting challenges advance with one atomic
`UPDATE ... SET progress = min(progress + n, target)`, so two answers arriving together (two
tabs) both count. Completion is claimed with a conditional update (`completed_at IS NULL`),
so XP is paid once even if two events finish a challenge at the same moment; the reward goes
through `award_xp` with the key `mission:<id>`. There is no claim endpoint and no route that
accepts XP, progress or a rank from the client.

What counts as evidence:

* **Correctness** only where the server graded it: lesson answers (scored by the evaluator),
  vocabulary answers (checked against the entry) and typed or multiple-choice flashcards.
  A flashcard the learner graded themselves (`self_graded`) still counts as a *review* but
  never as a correct answer.
* **Improvement** only on server-scored tests (flashcard tests, vocabulary sessions); a
  self-graded review session's accuracy is the learner's own opinion.
* **Hints** are server-side: questions reach the browser without their hint
  (`student_question`), the hint is fetched from `POST /api/hint`, which records that it was
  shown, and the answer endpoint treats a recorded hint as used whatever the page reports.
  Asking the lesson tutor for help during a question also counts against "without hints".
  A page can still *add* a hint flag, never remove one.
Rewards are 15-40 XP a day and 60-100 a week, deliberately below what an ordinary study day
earns. Days start at midnight and weeks on Monday in `APP_TIMEZONE` on the server's clock;
the profile timezone and the device clock play no part.

## Learnova Leagues

Bronze, Silver, Gold, Sapphire, Ruby, Emerald, Diamond (tiers 1-7). Everyone starts in
Bronze. Tiers are stored as integers (`league_membership.tier`, `league_entry.tier`), so the
names live only in `learnova/gamification/leagues.py`, the CSS and the translations. Until
2026-11 tier 6 was called "Diamond" and tier 7 "Legendary"; the owner renamed them to
match the spec, with no migration. A learner who was in the old "Diamond" league (tier 6) now
sees "Emerald". The top league's crest glows (`.league-diamond`).

* **Joining a week.** A participating learner enters the week with their first XP of it,
  into a group of at most `LEAGUE_GROUP_SIZE` (30) in their league. Joining mid-week is
  normal; weekly XP counts the whole week.
* **Simulated rivals, paced live (owner request 2026-11-15).** A group of fewer than 27
  real learners is filled up to 30 with rivals labelled "Rival" (`learnova/gamification/rivals.py`).
  A rival starts at 0 when its seat is filled and earns only in the 15-minute slots that
  have passed since, in its profile's waking hours (`advance_group_rivals`, run whenever the
  group is shown, a rival profile is opened or the week closes; each slot is paced once,
  claimed through `league_rival.advanced_until`). Its weekly target follows what the
  group's fastest real learner is on course for, measured up to the start of today: a fast
  learner meets faster rivals, a slow one slower ones. The 3 strongest rivals aim 8-22 %
  above that pace and the next 2 level with it, so a steady learner finishes just behind
  the top 3 and a strong day (the rivals answer only the next day) or a faster week gets
  past them. One simulated session is 2-45 XP; a rival earns at most
  `LEAGUE_RIVAL_MAX_DAILY_XP` a day and `LEAGUE_RIVAL_MAX_WEEKLY_XP` a week. Rivals created
  before pacing (`advanced_until` NULL) keep the week planned for them.
* **Groups by last week (owner request 2026-11-15).** Inside a league, learners who moved
  up into it last week, learners who moved down into it, and learners who stayed (a second
  or later week in the league, a first week ever, or a return after a break) are grouped
  separately (`league_cohort`, `league_entry.cohort`, `open_league_group`). Each group is
  filled with rivals on its own, and the page labels a promoted or demoted group.
* **Real learners before rivals (owner request 2026-11-15).** When anyone of a league
  enters the week, or opens the league page, every participating member of that league
  with XP in the last `LEAGUE_MEMBER_ACTIVE_DAYS` (28) days gets a seat too
  (`enroll_league_members`); rivals fill only the seats that are left. A member without XP
  this week is listed under "Not started this week" and is not ranked: at the end of the
  week their entry is closed as `inactive`, they keep their league and see no result card.
  Someone with no activity therefore still cannot be demoted for being away, and leaving
  the leagues before starting the week keeps the league too. On PostgreSQL the
  "count the group, then join it" step holds a transaction-scoped advisory lock per league
  and week (`lock_league_group_assignment`), so two learners joining at the same moment
  cannot make a group of 31. SQLite allows one writer at a time, so it needs no lock.
* **Weekly XP** is the sum of `XPTransaction` inside the week. A new week therefore starts
  at 0 by construction, and lifetime XP (`user_gamification_profile.total_xp`) is never
  touched by leagues. Lifetime XP is increased with one SQL `UPDATE ... SET total_xp =
  total_xp + n`, so two awards at the same moment both count.
* **Farming** is bounded by the daily XP budgets per activity group (flashcards 200,
  practice 150, vocabulary 100, planning 50, measured on the server's day in
  `APP_TIMEZONE`) plus once-per-period goal, challenge and badge XP.
* **Flashcard and vocabulary XP (owner rule 2026-11-15).** Nothing is paid per answer.
  When a session is complete it pays half its correct answers, rounded up
  (`service.study_session_xp`): 30 of 30 correct show "+15 XP", 7 correct pay 4. That is
  the session's whole XP - there is no separate completion, perfect-test or card-mastered
  bonus. Every answer, mastered card and completed session is still recorded, so streaks,
  daily goals, challenges and badges work as before. A card reviewed outside a session
  pays no XP. Project final and
  practice exams are still recorded as `test_completed` learning events (challenges,
  badges and statistics are unchanged), but their XP is booked as
  `project_exam_completed` and drawn from the **practice** budget, not the flashcard one
  (`record_learning_event(..., xp_event=...)`).
* **Ranking**: more weekly XP first, then whoever reached their total earlier, then the
  lower account id - the same order everywhere.
* **End of week** (Monday 00:00, `APP_TIMEZONE`): in a group of at least
  `LEAGUE_PROMOTE + LEAGUE_DEMOTE` learners the top `LEAGUE_PROMOTE` (10) move up and the
  bottom `LEAGUE_DEMOTE` (10) move down. Smaller groups use a third each, so there is always
  a safe middle; with two or more learners at least one still promotes, and a learner alone
  stays. Only learners with XP can be promoted. Diamond has no promotion, Bronze no
  demotion. Each learner gets exactly one outcome.
* **Exactly once.** `process_due_league_weeks` claims a finished week with a conditional
  update (`open -> processing`) in the same transaction that writes the results, so a
  crash rolls the claim back and a parallel run finds nothing to claim. A week is closed
  only `LEAGUE_CLOSE_GRACE_SECONDS` (120) after it ended, so an award stamped 23:59:59 whose
  request commits just after midnight is still counted. Results (rank, weekly XP, group
  size, outcome, league after) stay on `league_entry` as history. While a finished week is
  unprocessed (including the grace period), no new entries are created, so nobody joins the
  new week with last week's league. As soon as a week closes, learners who already earned XP
  in the new week are entered, earliest first (`backfill_league_entries`).
* **When it runs.** When someone opens the leaderboard or their profile, in the background
  after an XP award, and with `flask leagues process`. On the request path a failure is
  logged (`League week … could not be processed` with a traceback, then
  `league.lazy_transition_failed`) and the page still renders; the week stays open and is
  retried on the next visit. The CLI re-raises: it exits 1 and `leagues status --check`
  flags the week once it is overdue.
* **Opting out** (Settings) mid-week records this week's entry as `withdrawn` (owner
  decision, 2026-11-13). The row is kept, it is hidden from every standing at once and the
  transition never processes it again. Its rank, weekly XP and group size at that moment are
  stored. If the learner was in the **demotion zone** when they left, they are demoted
  (`tier_after` = one league down, membership updated); from anywhere else they keep their
  league and are never promoted. Someone who opts out after a week ended but before it was
  processed is treated the same way, at the final standings. A withdrawn learner **still
  counts toward the group size** the zones are computed from (`zone_sizes(...,
  withdrawn=, withdrawn_demoted=)`), so nobody else's zones shrink. A learner demoted on
  leaving keeps the demotion place they took, so the rest's demotion zone is one smaller.
  Promotion places are never taken by a leaver; they pass to the next learner. In the
  reproduced case (12 in Silver, the last one leaves) the outcome for the other 11 is exactly
  what it would have been had the leaver stayed: 4 promoted, 3 more demoted. XP, levels,
  avatars, streaks and challenges are unaffected, and the public profile never shows league
  participation. **Opting back in** sets `rejoin_from` to next Monday - never the current
  week - so leaving and returning can neither dodge nor replay a result.

### The leaderboard

`/leaderboard` and `GET /api/leaderboard` (same data) show:

* the current league (crest, name, the ladder of all seven), and the time left, rendered on
  the server (`time_left`, also readable without JavaScript) and kept ticking by
  `static/js/profile.js`. When the week is over, the timer says "The week has ended. Results
  in a moment." and reloads the page once, about 150 s after the end (after the close
  grace). The reload is guarded per week end in `sessionStorage`; without storage it does
  not reload at all, so it can never loop;
* `results_pending` - a note while the learner's last week has ended but is not closed yet;
* rank, avatar, username, Premium and weekly XP for everyone in the learner's group, the
  promotion and demotion zones (with dividers), and the learner highlighted with
  "Promotion zone", "You're currently safe" or "Demotion zone";
* the gaps, computed on the server by `league_rules.xp_gaps`: `me.xp_to_next_rank` (XP to
  pass the learner one place above; ties go to whoever got there first, so it is their XP -
  yours + 1), `me.xp_to_promotion` and `me.xp_to_leave_demotion` (0 when already there or
  not applicable);
* `history`: the learner's last `LEAGUE_HISTORY_WEEKS` (8) finished weeks, newest first -
  week, league, rank of group size, weekly XP, outcome (Promoted / Demoted / Stayed / Left
  the leagues) and the league after. A mid-week withdrawal is listed as soon as it happens.
  It is shown even when the learner is not taking part this week, because it is their own
  record.

It lists only real accounts in the learner's group and exposes no email or account id. The
one-time "last week's result" card is unchanged.

## Security

Every change is to the signed-in account only (no id is read from a request). XP, ranks,
unlocks and Premium are all derived on the server; there is no endpoint that writes any of
them. No route reads an XP amount, an XP idempotency key, a rank,
a tier or a league from the request: amounts come from `gamification.XP_VALUES` /
`bounded_answer_xp`, keys from server ids (`tests/test_league_integrity.py` sends forged
`xp`, `amount`, `idempotency_key`, `tier`, `weekly_xp` and `rank` fields and checks that
nothing changes). A flashcard answer's client `request_id` is stored for tracing only, scoped
to the item (`<item id>:<request id>`); a reused or foreign id used to fail the answer with
a 500 because the column is unique. Usernames are rendered escaped everywhere (Jinja autoescape, `escapeHtml` in the
community script). Avatar SVGs are served as static files and are checked by tests to
contain no scripts, foreign objects, images, text or external references.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `FEATURE_LEAGUES` | same as `FEATURE_GAMIFICATION` | leagues on/off for the whole site |
| `LEAGUE_PROMOTE` / `LEAGUE_DEMOTE` | 10 / 10 | places per group that move up / down |
| `LEAGUE_GROUP_SIZE` | 30 | most learners in one group |
| `LEAGUE_CLOSE_GRACE_SECONDS` | 120 | how long after a week ends it is closed (late commits still count) |
| `LEAGUE_MEMBER_ACTIVE_DAYS` | 28 | members with XP in this many days are seated before rivals (0: off) |
| `LEAGUE_RIVALS` | true | simulated rivals fill groups of fewer than 27 learners |
| `LEAGUE_RIVAL_MAX_DAILY_XP` / `LEAGUE_RIVAL_MAX_WEEKLY_XP` | 600 / 3500 | the most one rival earns |
| `USERNAME_CHANGE_COOLDOWN_DAYS` | 30 | between two changes |
| `USERNAME_HOLD_DAYS` | 90 | how long a released name stays unavailable to others |
| `APP_TIMEZONE` | Europe/Berlin | days, weeks and resets |

## Operations

```
flask --app app leagues process          # close every finished week; idempotent, exit 1 on failure
flask --app app leagues status           # recent weeks: state, end, processed time, outcome counts
flask --app app leagues status --check   # exit 2 if a finished week is still open after 6 h
```

## Scheduling the weekly transition

Weeks end on Monday 00:00 in `APP_TIMEZONE` (Europe/Berlin by default). Learnova also closes
a finished week on the next leaderboard or profile visit and after the next XP award, but a
schedule makes the hand-over deterministic. Nothing below is installed or enabled by the
repository; set it up on the server by hand.

**systemd (recommended).** Templates: `deploy/systemd/learnova-leagues.service` and
`deploy/systemd/learnova-leagues.timer`. The timer runs at Monday 00:05 in Europe/Berlin
and, as a safety net, every hour at :35 (the command is idempotent and cheap when nothing is
due), with `Persistent=true` to catch up after downtime.

1. Copy both files to `/etc/systemd/system/` and replace `<LEARNOVA_DIR>` (the checkout,
   containing `.venv`), `<LEARNOVA_USER>` and `<LEARNOVA_ENV_FILE>` (the environment file the
   web service uses - it must contain `DATABASE_URL` and the same `APP_TIMEZONE`). If
   `APP_TIMEZONE` is not Europe/Berlin, change the zone in `OnCalendar` to match.
2. `sudo systemctl daemon-reload`
3. Dry run once: `sudo systemctl start learnova-leagues.service` then
   `journalctl -u learnova-leagues.service -n 20` - expect `Nothing due.` or
   `Processed week ...`.
4. Enable: `sudo systemctl enable --now learnova-leagues.timer`
5. Verify: `systemctl list-timers learnova-leagues.timer` shows the next Monday 00:05 run.

Failure visibility: a failed run exits 1, so the unit shows as failed in
`systemctl --failed` and the journal has the traceback; the week stays open and the next
hourly run retries it. For an alert, add `OnFailure=` to the service or have your
monitoring run `flask --app app leagues status --check` (exit 2 means overdue).

**cron alternative.** `deploy/cron/learnova-leagues.cron` (uses `CRON_TZ=Europe/Berlin`,
so daylight-saving changes are handled): Monday 00:05, hourly at :35, and a Monday 07:15
`status --check` whose output cron mails when a week is overdue. Install with `crontab -e`
as the Learnova user after replacing the placeholders; output goes to
`instance/leagues.log`.

**Render.** If Learnova runs on Render instead, create a Cron Job service with the
command `flask --app app leagues process` and the schedule `35 * * * *` (Render cron uses
UTC; the hourly run makes the exact zone irrelevant), sharing the web service's
environment group.

## Avatar artwork

The 20 SVGs in `static/avatars/` are generated from drawing code in `scripts/avatars/`:
`lib.py` holds the shared face construction (head, eyes, brows, nose, mouth) that makes
the set read as one family; `chars1.py`-`chars4.py` draw each character's hair, headwear,
outfit, accessories and background by hand. Every random choice has a fixed seed, so the
output is reproducible byte for byte (a test asserts it).

```
python scripts/avatars/build_avatars.py              # check against static/avatars; writes nothing
python scripts/avatars/build_avatars.py --out DIR    # render the set somewhere else to look at it
python scripts/avatars/build_avatars.py --write founder   # replace one file after editing its code
```

The default is a check, so running the script never replaces the committed artwork by
accident; `--write` lists what differs and changes only the 20 known files. Each build
validates the XML, the per-file id prefixes and the absence of scripts, images and text.

## Known limitations

1. Leagues need traffic: a finished week is processed when anyone opens the leaderboard or
   a profile, after the next XP award, or by the CLI. Schedule `flask leagues process`
   shortly after Monday 00:00 for a deterministic hand-over.
2. The offensive-name list is deterministic. It reads look-alike digits both ways ("4" as
   "a" and "u"), removes separators and stretched letters, and accepts only ASCII after NFKC,
   so Unicode look-alikes cannot be used at all; new slang still needs adding by hand.
   Names within a digit or a separator of a moderator's or administrator's name are refused.
3. "Without hints" is tracked on the server, but help from outside Learnova (a friend, a
   search engine) is of course invisible to it.
4. Groups are filled in join order within a league, not by skill.
5. The week transition runs synchronously on the first leaderboard or profile visit after
   the close grace when no scheduler is installed, inside that request. With many learners
   that first Monday visit is slow; install the systemd timer or cron job (above).
   `render.yaml` defines no cron service.
6. A week that cannot be processed keeps blocking new league entries until it is fixed
   (pages still render). `leagues status --check` is the alarm.
7. The group-size guarantee under concurrency relies on the PostgreSQL advisory lock. The
   lock is held until the joining request commits; XP is awarded at the end of a request,
   so this is milliseconds, and only learners joining the same league in the same week ever
   wait.
8. Farming is bounded, not prevented: self-graded flashcard answers still count toward a
   session's XP (within the flashcard budget). A session pays only half its correct
   answers, so replaying a one-card set earns 1 XP a session.
9. A withdrawn learner who left from the promotion zone does not move up, and their
   promotion place passes to the next learner, so leaving can lift someone else. Zones
   among the remaining learners never overlap; if many learners leave from the top of a
   small group, promotion places are filled first.
