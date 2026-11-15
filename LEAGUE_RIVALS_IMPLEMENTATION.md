# League rivals, league page and Settings rows (2026-11-15)

Nothing was committed, pushed or deployed. No production data or migration was touched.

## 1. What was asked, and the decisions taken

**Three requests in order:**
1. A shorter league page whose text disappears after "OK", in a Duolingo-like style, with
   promotion and demotion zones; and Settings as rows ("Profil ›") that open on tap.
2. Rivals filling empty seats, realistic profiles, no AI, deleted after the week.
3. The detailed brief "Algorithmic League Rival System".

**Where the brief changed the earlier wishes, the brief was followed:**
- **Labelled, not hidden.** Rivals look like learners (name, avatar, level, achievements),
  but carry a "Rivale" chip in the table and a "Simulierter Rivale" marker on their profile
  (brief, Phase 2 and Phase 8). An earlier message asked for rivals that "look real"; the
  brief forbids presenting them as real people.
- **Scheduled, not reactive.** A first draft made rivals follow the group's XP a few hours
  behind. The brief forbids steering scores towards a specific learner's win or loss, so
  rivals now learn on their own pre-planned schedule.
- **No AI anywhere.** Names, profiles and schedules come from curated lists, fixed rules and
  a stored seed.

## 2. How it fits the existing league system (Phase 1)

There is no second league system: rivals are extra `Standing`s in the existing ranking
(`learnova/gamification/leagues.py`).

| Existing piece | How rivals use it |
|---|---|
| Joining a group: `ensure_league_entry` (first XP of the week, PostgreSQL advisory lock per league and week, `open_league_group` with at most 30 real learners) | After the entry is inserted, **in the same transaction and lock**, `sync_group_rivals` brings the group to `rival_count(real)` rivals. |
| Ranking: `league_rules.ranked` (more XP, then earlier, then lower id) | Unchanged. A rival's standing is `Standing(-rival.id, xp, time of its last event)`, so ids never collide with accounts and ties stay deterministic. |
| Zones: `zone_sizes`, `outcomes`, `xp_gaps` | Unchanged. A group with rivals is ranked over all of its places (30). |
| Weekly transition: `process_due_league_weeks` | Rivals are ranked at their final XP; then all of the week's rivals are deleted **in the same transaction** that closes the week. |
| Leaving mid-week: `leave_leagues` → `group_standings` | Includes rivals, so leaving from the demotion zone is judged against the same table the learner saw. |
| Profiles, avatars, achievements | The 20-avatar catalogue (an avatar unlocked at the rival's level), `BadgeDefinition` names and descriptions, and the public profile's design. |
| Migrations | Two new tables via `db.create_all()` plus the marker migration `050_league_rivals` (the pattern of 023). |

## 3. The data (Phase 2, Phase 6, Phase 7)

**`league_rival`**
- Columns: `week_start` (FK `league_week`), `tier`, `group_number`, `seat`, `username`,
  `display_name`, `avatar_id`, `level`, `behaviour`, `seed` (BIGINT), `baseline_xp`,
  `target_xp`, `achievements_json`, `created_at`.
- Constraints: `UNIQUE (week_start, tier, group_number, seat)` and
  `UNIQUE (week_start, username)`.
- Index: `ix_league_rival_group (week_start, tier, group_number)`.

**`league_rival_event`** (the separate score ledger)
- Columns: `rival_id` (FK, `ON DELETE CASCADE`), `occurs_at`, `kind`, `xp`.
- Index: `ix_league_rival_event_rival_time (rival_id, occurs_at)`.

**Never an account:**
- A rival is not a `User`, so it cannot log in.
- It writes no `XPTransaction`, `LearningEvent`, `Lesson`, `UserBadge` or gamification
  profile row.
- It never appears in `/u/<username>`, the community or any statistics.

**Rows per group:** 27 rivals × roughly 3–30 events each, for one week.

## 4. Population (Phase 5)

**How many rivals:** `rival_count(real) = 30 − real` when `real < 27`, else 0.
- 3 real learners get 27 rivals; 10 get 20; 20 get 10; 26 get 4; 27–30 get 0.
- `real` counts every learner who joined the group this week, including those who left
  mid-week (their place stays taken).
- With another `LEAGUE_GROUP_SIZE` the rule is the same: no rivals once at most 3 places
  are free.

**Idempotent filling:** rivals hold places 1…n.
- `sync_group_rivals` deletes rivals in places above n and creates only missing places.
- Running it twice changes nothing.
- When a real learner joins, the rival in the last place leaves (with its events).

**Never on ordinary page requests:**
- Groups are filled when a learner joins.
- The page only checks the count, and fills once a group that predates the feature, or
  whose filling was missed.

**Races:**
- Filling runs under the join's advisory lock (PostgreSQL) or the single SQLite writer.
- Each rival is inserted with `INSERT … ON CONFLICT DO NOTHING` (the codebase's
  `insert_ignoring_conflicts`; no savepoints, which pysqlite does not support reliably).
- The two unique constraints are the backstop. If a place is already filled, the loop moves
  on; if the name was taken meanwhile, the next name is tried. Events are only written for
  the rival this call created.
- A league never holds more than 30 places: real learners are capped at 30 by
  `open_league_group`, and rivals at `30 − real`.

**Names:**
- From curated first names, surnames and suffixes, in the brief's formats: `nathan7`,
  `emanuel.sc`, `mia.study`, `sophia_8`, `benjamin.de`, `leon.weber`. Display names match
  ("Emanuel S.", "Mia").
- Never a real learner's username, never twice in a week.
- A name collision only changes the name (a separate stream from the same seed), never
  the score.

## 5. The behaviour engine (Phase 3)

**Profiles:** the 30 places of a group are dealt 12 casual, 12 consistent, 4 competitive
and 2 strong (40/40/15/5 %), shuffled by the week, league and group.

| Profile | Share of the baseline | Active days |
|---|---|---|
| casual | 0.35–0.75 × | 2–4 |
| consistent | 0.8–1.15 × | 5–7 |
| competitive | 1.2–1.5 × | 5–7 |
| strong | 1.5–1.8 × | 6–7 |

**Calibration:**

```
baseline = median(final_xp of last week's real results in this league)   if ≥ LEAGUE_RIVAL_MIN_SAMPLES (5)
         = LEAGUE_RIVAL_BASELINE_XP (60) × league factor                otherwise
           (factors 1.0, 1.2, 1.4, 1.65, 1.9, 2.2, 2.5 for Bronze … Diamond)
baseline is bounded to [LEAGUE_RIVAL_MIN_BASELINE_XP 20, LEAGUE_RIVAL_MAX_BASELINE_XP 400]
target   = baseline × uniform(profile range), at most LEAGUE_RIVAL_MAX_WEEKLY_XP (600)
```

**Activity:**
- The target is split over the rival's active days, at most `LEAGUE_RIVAL_MAX_DAILY_XP`
  (150) per day.
- Each day is filled with simulated events, in XP amounts from the real XP rules:

  | Event | XP |
  |---|---|
  | lesson | 18–30 |
  | quiz | 14–24 |
  | flashcards | 12–28 |
  | vocabulary | 8–20 |
  | practice | 6–18 |

- Events fall at plausible times: 14:00–21:30 on school days, 9:00–20:00 at the weekend.
- A rival's weekly XP at any moment is the sum of its events up to then, so it never jumps
  and never goes down.

**Fairness:**
- The strongest rival earns at most 1.8 × the baseline in a week, so a learner who studies
  regularly can beat every rival.
- An idle learner is overtaken gradually, as the rivals' events happen.
- Rivals never react to any learner's XP.
- Ties with a rival go to whoever reached the score first.

**Profiles shown:**
- A level from the league's usual range: competitive and strong rivals sit 2 levels
  higher.
- Achievements a learner of that level plausibly has: only badges the catalogue awards, at
  most 6.
- Lifetime XP inside the level.
- The week's events as "activity this week".

## 6. Cleanup (Phase 6)

- `process_due_league_weeks` ranks the week with its rivals, then `delete_week_rivals`
  removes them (events first, then rivals) in the same transaction as the results.
- Each learner's `league_entry` keeps rank, XP and group size (now out of 30): the minimum
  snapshot the history needs.
- `purge_ended_rivals()` deletes rivals of any closed week.
  - It runs after every transition and in `flask leagues process`.
  - It is safe to repeat (a second run deletes 0).
  - It never touches a week still waiting for its results.
- An ended rival's profile answers 404.

## 7. The pages (Phase 8 and the earlier requests)

**League page (`templates/leaderboard.html`, `static/css/profile.css`)**
- The ladder of the seven crests is on top: your league large, passed leagues bright, the
  ones above dimmed and locked. Under it the league's name and a "⏱ time left" pill.
- **First visit:** two lines ("Sammle diese Woche XP …") with **OK**.
  - After OK it is gone for good on that device: a cookie, set by
    `POST /leaderboard/intro-seen`.
  - It works without JavaScript too.
- **Status:** one line: "#29", the message, and short chips ("13 XP diese Woche", "Noch 5 XP,
  um Platz 28 zu überholen", "Noch 41 XP bis zur Aufstiegszone").
- **Table:**
  - medals for places 1–3, avatar, name, level, a small "Rivale" chip and weekly XP;
  - green rows and the "▲ AUFSTIEGSZONE ▲" divider, red rows and "▼ ABSTIEGSZONE ▼";
  - your own row outlined.
- **"Deine letzten Wochen" and "So funktionieren die Ligen"** are folded into one line each.
  The rules mention the simulated rivals.

**Rival profile (`/leaderboard/rival/<id>`, `templates/rival_profile.html`)**
- The public profile's design: the "🤖 Simulierter Rivale" marker with one explaining
  sentence, display name and @username, level and XP bar, league, rank and weekly XP,
  achievements, and the activity list.
- Only for signed-in learners; `noindex`.

**Settings (`templates/settings.html`, `static/js/settings-groups.js`)**
- Four rows: 📚 Lerneinstellungen, 👤 Profil, ⭐ Abo, ℹ️ Hilfe und Support. Each has a
  one-line summary and "›", and opens on tap.
- Every setting, form, id and tutorial target is unchanged.
- A link to `#profile`, `#subscription`, `#help`, `#tips` … opens its row.
- After saving a form, its row opens again once.

**Tutorial fix (`static/js/tour.js`)**
- Current Chrome hides closed `<details>` content with `content-visibility`, which keeps a
  layout box. The tour measured such content as visible and lit it at the summary's place.
- It now counts anything inside a closed `<details>` (apart from its summary) as hidden, so
  "Open this part first" works again everywhere.
- The Settings chapter starts from the first row. "Your plan" starts when the Abo row is
  opened.

## 8. Tests (Phase 9)

**New: `tests/test_league_rivals.py`, 26 tests.** The brief's 24 points:

| Brief | Test |
|---|---|
| 1–8 counts 3/10/20/26/27/28/29/30 | `test_how_many_rivals_a_group_gets` (rule); `test_groups_are_filled_to_30_only_below_27_real_learners` (database, places 1…n) |
| 9 no duplicates | `test_filling_twice_creates_nothing_new`; `test_a_duplicate_seat_or_name_is_refused_by_the_database` |
| 10 unique names | `test_names_are_unique_and_never_a_real_learners_name`; `test_names_look_like_learners_names` (format, no 3-digit numbers, > 300 combinations) |
| 11, 12 profile and achievements | `test_a_rival_profile_shows_level_achievements_activity_and_says_simulated` |
| 13 no login | `test_rivals_cannot_log_in` |
| 14, 15 real XP and statistics | `test_rivals_never_touch_real_xp_or_statistics` (row counts of 8 real tables, `learning_stats`, lifetime XP) |
| 16, 17 overtaking | `test_rivals_overtake_an_inactive_learner_and_an_active_one_overtakes_them` |
| 18 limits | `test_the_week_target_and_events_stay_within_the_limits` (7 leagues × 4 baselines × 29 places) |
| 19 ties | `test_ties_are_broken_the_same_way_every_time` |
| 20, 21 cleanup | `test_the_transition_ranks_with_rivals_then_deletes_them`; `test_cleanup_keeps_the_rivals_of_a_week_that_is_still_running` |
| 22 promotion and demotion | `test_the_transition_ranks_with_rivals_then_deletes_them` (Silver, 3 + 27: 10 up and 10 down exactly as `outcomes()`, memberships moved, group size 30) |
| 23 never above 30 | `test_thirty_joins_never_exceed_30_participants`, plus the database constraints |
| 24 separate leagues | `test_two_groups_never_share_rivals` |

**Also covered:**
- the 40/40/15/5 deal and how each profile learns;
- calibration from last week's median, the default and its bounds;
- reproducibility, and that a rename never changes a score;
- joining through real learning (fill, then a new learner takes the last place);
- the page (29 "Rivale" chips, Bronze zones 10/0);
- the switch `LEAGUE_RIVALS=false`;
- no AI or network imports.

**Changed tests:**
- `tests/test_profiles_leagues.py` Base sets `LEAGUE_RIVALS=False`. The 93 existing league
  tests pin the rules for real-only groups (zones shrink in a small group), and those rules
  still apply when rivals are off. No assertion was weakened.
- `tests/test_tutorial_browser.py` AutoStart: the Settings chapter now starts with "open
  this part first", and "Your plan" follows when the Abo row is opened (intended).

**Run results:**
- league, i18n, countdown (Chrome) and tutorial suites: 181 passed;
- tutorial browser and JS suites: 82 passed, 1 skipped;
- AutoStart three times in a row: 82 passed each.

**Real Chrome screenshots:**
- league page at 390 px light and dark and at 1440 px: 30 rows, 29 rivals, divider, no
  sideways scroll, rows of at least 52 px;
- OK followed by a reload leaves no intro;
- the rival profile;
- Settings closed, and `#subscription` opening Abo.

**Full suite** (`pytest tests`, 1 h 18 min): 3777 passed, 11 skipped, 3 failed.
- The 3 failures were all in `tests/test_dashboard_show_more_browser.py`. The Settings
  code history and plan comparison now sit inside the closed "Abo" row, so the tests could
  not see them.
- The tests now tap that row first, as a student would, and also check that it starts
  closed and opens, without JavaScript too. No assertion was removed or loosened.
- After that change, the show-more, rival and league tests were run again: 78 passed.
  This run also covers the switch to `insert_ignoring_conflicts`, which happened after
  the full run had started.
- Total: 3780 passed, 11 skipped, 0 failed.

## 9. Remaining risks

- **PostgreSQL was not run here** (SQLite only).
  - The code uses the existing advisory lock, `INSERT … ON CONFLICT DO NOTHING`
    (`insert_ignoring_conflicts`), `BIGINT` and plain SQLAlchemy.
  - The constraints and indexes are created by `db.create_all()` on both databases.
  - The first PostgreSQL start creates the two tables; check them with
    `\d league_rival` and `\d league_rival_event`.
- **A learner who joins late in the week** meets rivals that already have days of
  activity: their schedule covers the whole week.
- **A rival disappears mid-week** when a real learner takes its place. That is visible in
  the table.
- **The first weeks run on the default baseline (60 XP in Bronze)** until a league has 5
  results in a week. Tune it with `LEAGUE_RIVAL_BASELINE_XP` if rivals feel too strong or
  too weak.
- **Daylight-saving change:** the schedule is built from local Monday plus days and minutes,
  so in the week of a change, times shift by one hour. Harmless.
- **A rival's name is checked against real usernames when it is created.** Someone
  registering that name later in the week does not clash: rival profiles live at
  `/leaderboard/rival/<id>`, not `/u/<name>`.
