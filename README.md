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

- **Submit Work (teams):** a team picks itself and a scenario, answers the
  scenario questions (if any have been authored), writes a short summary,
  optionally attaches evidence files, and self-reports the points it believes it
  earned. **One submission per team per scenario.** After submitting, the team
  sees *"Submitted — pending trainer review"*.
- **Submissions inbox (trainers):** review a submission, download its evidence,
  then **Accept** to post points to the leaderboard, **Reopen** to let the team
  resubmit, or **Void** it. Accepting writes to the same score table the manual
  form uses, so the leaderboard is unchanged.
- **Score Entry (trainers):** unchanged manual path — pick a team + scenario,
  enter points, time taken, and whether the solution passed. Still available as
  a fallback alongside the submission flow.
- **Speed bonus:** in each scenario, the fastest *passing* teams get bonus
  points (default `5, 3, 2, 1` for the top four). Configurable in **Setup**.
  Submissions do not record time, so accepted submissions earn no speed bonus.
- **Leaderboard:** `Total = reviewer points + speed bonus`. Ties broken by total
  time (faster wins). Auto-refreshes every 15 seconds — great for a projector.

### Scenario questions (sub-scenarios)

Scenarios can optionally carry a list of questions in the `scenario_items`
table. When a scenario has none, the submission form falls back to a single
free-text summary. When questions are added later, the form renders them
automatically — no code change needed. Each item already carries dormant
`verify_type` / `verify_config` columns for automatic grading in a later step.

Evidence files are stored inline in Postgres (Streamlit Cloud's filesystem is
ephemeral), capped at 5 MB per file and 4 files per submission.

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
