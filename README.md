# ⚡ SPARK Module 3 — PERFORM Week Live Scoreboard

A simple, live, interactive scoring system + leaderboard for the PERFORM week
micro-scenarios. Trainers enter reviewer points and completion time; the app
auto-computes a **speed bonus** and a **live ranking** (points first, time as
tiebreaker). Built with [Streamlit](https://streamlit.io) + SQLite — no servers
or accounts required.

## Who sees what

- **Leaderboard:** public — participants, trainers and core team all watch it
  live (projector or their own phones). A read-only scenario catalog and the
  team **Submit Work** form are also public.
- **Submissions inbox, Score Entry, Scenario editing, Setup:** locked behind a
  **trainer password** (sidebar login). Default password is `spark2026` — change
  it in Setup. Trainers also enter their name at login, which is recorded
  against every score in the audit log.

## How it works

- **Submit Work (teams):** a team picks itself, then selects **one or several
  scenarios**. Each scenario gets its own block for answers (if questions have
  been authored), a short summary, optional evidence files, and the points the
  team believes it earned. **One submission per team per scenario** — already
  submitted scenarios are removed from the picker and listed with their status.
  The whole batch is validated before anything is written, so a partial batch
  never lands. After submitting, the team sees *"pending trainer review"*.
- **Submissions inbox (trainers):** a live counter shows how many submissions
  await review, and the count also appears on the tab label. **Bulk review**
  lists pending submissions as an editable grid — set an award per row, tick the
  ones to post, check the preview, and accept them together. A detail panel
  below handles one submission at a time when you need to read the summary or
  download evidence. **Reopen** lets a team resubmit; **Void** rejects.
- **Awarding points:** accepting writes to the same score table the manual form
  uses. Choose **Replace** (the scenario ends up worth exactly the award — the
  normal case) or **Add** (the award is added to the scenario's existing score,
  for scenarios marked in several parts). Both paths show the scenario's current
  score, the resulting score, and the change to the team's total before you
  commit. The resulting score is validated against the scenario maximum.
- **Score Entry (trainers):** unchanged manual path — pick a team + scenario,
  enter points, time taken, and whether the solution passed. Still available as
  a fallback alongside the submission flow.
- **Speed bonus:** in each scenario, the fastest *passing* teams get bonus
  points (default `5, 3, 2, 1` for the top four). Configurable in **Setup**.
  Submissions do not record a time, so accepting one preserves whatever time was
  already recorded rather than clearing it — clearing would drop the team out of
  that scenario's ranking and shift bonus points to every other team.
- **Leaderboard:** `Total = reviewer points + speed bonus`. Ties broken by total
  time (faster wins). Auto-refreshes every 15 seconds — great for a projector.
  The live block is rendered as a single HTML table rather than Streamlit
  widgets, which would remount on every refresh and visibly flicker.

### Between events

Run both resets in **Setup**, in this order:

1. **Reset all scores** — clears the leaderboard.
2. **Delete all submissions** — clears submissions, answers and evidence.
   Skipping this leaves the one-per-team-per-scenario rows in place, which
   silently blocks teams from submitting next time.

Export from the Submissions tab first; neither reset can be undone.

### Scenario questions (sub-scenarios)

Scenarios can optionally carry a list of questions in the `scenario_items`
table. When a scenario has none, the submission form falls back to a single
free-text summary. When questions are added later, the form renders them
automatically — no code change needed. Each item already carries dormant
`verify_type` / `verify_config` columns for automatic grading in a later step.

Evidence files are stored inline in Postgres (Streamlit Cloud's filesystem is
ephemeral), capped at 5 MB per file and 4 files per submission. The Submissions
tab shows total evidence size and exports submissions, answers and all files.

- **Submissions** of the actual work stay in SharePoint/Teams as usual; this app
  only tracks scoring + ranking.

All data lives in Postgres (Supabase) for the deployed app. Local development
without `DATABASE_URL` falls back to a `sparks.db` SQLite file next to the app.

## Run locally

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Opens at http://localhost:8501.

## Let everyone view from their laptop/phone (same network)

```powershell
streamlit run app.py --server.address 0.0.0.0
```

Then share `http://<your-machine-ip>:8501`. Find your IP with `ipconfig`.
For a public link (e.g. remote teams), deploy free to
[Streamlit Community Cloud](https://share.streamlit.io) — push this folder to a
GitHub repo and point it at `app.py`.

### Streamlit Cloud + Supabase secrets

If you use Supabase/Postgres on Streamlit Cloud, set these in app **Secrets**:

- `DATABASE_URL`: your **Supabase session pooler** connection string (recommended)
- `SUPABASE_POOLER_REGION`: only needed if `DATABASE_URL` is a direct host (`db.<ref>.supabase.co`), example `aws-0-eu-west-1`

Alternative (field-based) secret format is also supported:

```toml
PGHOST = "<host>"
PGPORT = "5432"
PGDATABASE = "postgres"
PGUSER = "<user>"
PGPASSWORD = "<password>"
```

Quick template for a full URL (recommended):

```toml
DATABASE_URL = "postgresql://<user>:<password>@<region>.pooler.supabase.com:5432/postgres?sslmode=require"
```

If your password contains special characters (`@`, `:`, `/`, `#`, etc.), prefer the field-based format above so encoding is handled safely.

The app is configured to use Postgres for persistence. If Postgres cannot be
reached, the app now shows a clear startup diagnostic in the UI instead of a
generic crash page.

This deployment is Postgres-only: if `DATABASE_URL` is missing, the app blocks
startup and tells you exactly what to fix.

> Tip: keep **Score Entry** restricted to trainers' devices and put the
> **Leaderboard** tab on the room's big screen.

## Customizing

- **Scenarios:** edit them live in the **Scenarios** tab (trainer mode) — add /
  remove rows, add / remove columns, change descriptions, points, durations,
  etc. Stored in `scenarios_catalog.csv` (also editable in Excel). The columns
  `num`, `title`, `max_points`, `est_minutes` drive scoring and can't be removed.
- **Speed bonus:** Setup tab, or `DEFAULT_TIME_BONUS_TABLE` in `scoring.py`.
- **Teams:** Setup tab (10 default teams are created on first run).
- **Trainer password:** Setup tab.
