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

Bronze, Silver, Gold, Sapphire, Ruby, Diamond, Legendary. Everyone starts in Bronze.

* **Joining a week.** A participating learner enters the week with their first XP of it,
  into a group of at most `LEAGUE_GROUP_SIZE` (30) in their league. Joining mid-week is
  normal; weekly XP counts the whole week. Someone with no activity takes no part that
  week: they keep their league and cannot be demoted for being away.
* **Weekly XP** is the sum of `XPTransaction` inside the week. Lifetime XP is never touched
  by leagues. Farming is bounded by the existing daily XP budgets per activity group
  (flashcards 200, practice 150, vocabulary 100, planning 50, measured on the server's day in
  `APP_TIMEZONE`) plus once-per-period goal, challenge and badge XP - replaying the same set
  all day cannot outscore a learner who studies normally for long.
* **Ranking**: more weekly XP first, then whoever reached their total earlier, then the
  lower account id - the same order everywhere.
* **End of week** (Monday 00:00, `APP_TIMEZONE`): in a group of at least
  `LEAGUE_PROMOTE + LEAGUE_DEMOTE` learners the top `LEAGUE_PROMOTE` (10) move up and the
  bottom `LEAGUE_DEMOTE` (10) move down. Smaller groups use a third each, so there is always
  a safe middle; with two or more learners at least one still promotes, and a learner alone
  stays. Only learners with XP can be promoted. Legendary has no promotion, Bronze no
  demotion. Each learner gets exactly one outcome.
* **Exactly once.** `process_due_league_weeks` claims a finished week with a conditional
  update (`open -> processing`) in the same transaction that writes the results, so a
  crash rolls the claim back and a parallel run finds nothing to claim. Results (rank,
  weekly XP, group size, outcome, league after) stay on `league_entry` as history. While a
  finished week is unprocessed, no new entries are created, so nobody joins the new week
  with last week's league. It runs when someone opens the leaderboard or their profile, in
  the background after an XP award, and with `flask leagues process`.
* **Opting out** (Settings): removes the learner from this week's ranking at once; no
  promotion or demotion while out (someone who opts out after a week ended but before it was
  processed is recorded as `withdrawn`, keeps their league and is left out of everyone's
  zones); XP, levels, avatars, streaks and challenges are
  unaffected; the public profile never shows league participation. **Opting back in**
  sets `rejoin_from` to next Monday - never the current week, so leaving and returning can
  neither dodge nor replay a result.

The leaderboard (`/leaderboard`, `GET /api/leaderboard`) shows rank, avatar, username,
Premium, weekly XP, the promotion and demotion zones, the time left, and the signed-in
learner highlighted with "Promotion zone", "You're currently safe" or "Demotion zone". It
lists only real accounts in the learner's group and exposes no email or account id.

## Security

Every change is to the signed-in account only (no id is read from a request). XP, ranks,
unlocks and Premium are all derived on the server; there is no endpoint that writes any of
them. Usernames are rendered escaped everywhere (Jinja autoescape, `escapeHtml` in the
community script). Avatar SVGs are served as static files and are checked by tests to
contain no scripts, foreign objects, images, text or external references.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `FEATURE_LEAGUES` | same as `FEATURE_GAMIFICATION` | leagues on/off for the whole site |
| `LEAGUE_PROMOTE` / `LEAGUE_DEMOTE` | 10 / 10 | places per group that move up / down |
| `LEAGUE_GROUP_SIZE` | 30 | most learners in one group |
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
