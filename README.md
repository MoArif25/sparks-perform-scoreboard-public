# ⚡ SPARK Module 3 — PERFORM Week Live Scoreboard

A simple, live, interactive scoring system + leaderboard for the PERFORM week
micro-scenarios. Trainers enter reviewer points and completion time; the app
auto-computes a **speed bonus** and a **live ranking** (points first, time as
tiebreaker). Built with [Streamlit](https://streamlit.io) + SQLite — no servers
or accounts required.

## Who sees what

- **Leaderboard:** public — participants, trainers and core team all watch it
  live (projector or their own phones). A read-only scenario catalog is also
  public.
- **Score Entry, Scenario editing, Setup:** locked behind a **trainer password**
  (sidebar login). Default password is `sparks2026` — change it in Setup.

## How it works

- **Score Entry (trainers):** pick a team + scenario, enter points, time taken,
  and whether the solution passed. Save.
- **Speed bonus:** in each scenario, the fastest *passing* teams get bonus
  points (default `5, 3, 2, 1` for the top four). Configurable in **Setup**.
- **Leaderboard:** `Total = reviewer points + speed bonus`. Ties broken by total
  time (faster wins). Auto-refreshes every 5 seconds — great for a projector.
- **Submissions** of the actual work stay in SharePoint/Teams as usual; this app
  only tracks scoring + ranking.

All data lives in a single `sparks.db` file next to the app — back it up by
copying that file, reset it by deleting it (or use the Setup → reset button).

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
