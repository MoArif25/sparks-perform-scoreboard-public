"""
Dual-backend data layer and scoring logic for the SPARK PERFORM Week scoreboard.

Backend selection (automatic):
  * Postgres  -- when st.secrets["DATABASE_URL"] is set (Supabase / any hosted PG)
  * SQLite    -- fallback for local development (sparks.db next to this file)

All SQL is compatible with both backends. Placeholders are translated
from ? (SQLite) to %s (Postgres) automatically in _execute / _fetchone.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import pandas as pd

from scenarios import DEFAULT_MAX_POINTS, SCENARIOS

DB_PATH = Path(__file__).with_name("sparks.db")

DEFAULT_TIME_BONUS_TABLE = [5, 3, 2, 1]
STATUS_OPTIONS = ["Not Started", "In Progress", "Submitted", "Reviewed"]

_PG_URL: str | None = None


def _normalize_pg_url(url: str) -> str:
    """Convert a Supabase *direct* connection URL into the IPv4-compatible
    *session pooler* URL. Streamlit Cloud only supports IPv4, while Supabase
    direct connections (db.<ref>.supabase.co) are IPv6-only, so we rewrite
    them automatically to avoid 'Cannot assign requested address' errors.

    Direct: postgresql://postgres:PWD@db.<ref>.supabase.co:5432/postgres
    Pooler: postgresql://postgres.<ref>:PWD@<region>.pooler.supabase.com:5432/postgres
    """
    import re

    m = re.match(
        r"^postgres(?:ql)?://postgres:([^@]+)@db\.([a-z0-9]+)\.supabase\.co:(\d+)/([^?]+)",
        url,
    )
    if not m:
        return url  # already a pooler URL or some other host -> leave as-is

    password, ref, port, dbname = m.groups()

    region = "aws-1-us-east-1"
    try:
        import streamlit as st
        region = str(st.secrets.get("SUPABASE_POOLER_REGION", region))
    except Exception:
        pass

    return (
        f"postgresql://postgres.{ref}:{password}"
        f"@{region}.pooler.supabase.com:{port}/{dbname}"
    )


def _get_pg_url() -> str | None:
    global _PG_URL
    if _PG_URL is not None:
        return _PG_URL
    try:
        import streamlit as st
        url = st.secrets.get("DATABASE_URL")
        if url:
            _PG_URL = _normalize_pg_url(str(url))
            return _PG_URL
    except Exception:
        pass
    return None


def _is_pg() -> bool:
    return _get_pg_url() is not None


@contextmanager
def _connect():
    pg_url = _get_pg_url()
    if pg_url:
        import psycopg2
        conn = psycopg2.connect(pg_url)
        conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _execute(conn, sql: str, params=()) -> None:
    """Execute a write statement, translating ? placeholders for Postgres."""
    if _is_pg():
        conn.cursor().execute(sql.replace("?", "%s"), params)
    else:
        conn.execute(sql, params)


def _fetchone(conn, sql: str, params=()):
    if _is_pg():
        cur = conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    else:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def _read_sql(sql: str, conn) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn)


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create tables if they do not already exist."""
    with _connect() as conn:
        if _is_pg():
            stmts = [
                """CREATE TABLE IF NOT EXISTS teams (
                    id      SERIAL PRIMARY KEY,
                    name    TEXT NOT NULL UNIQUE
                )""",
                """CREATE TABLE IF NOT EXISTS scenarios (
                    num         INTEGER PRIMARY KEY,
                    title       TEXT NOT NULL,
                    max_points  INTEGER NOT NULL,
                    est_minutes REAL,
                    scoring     TEXT,
                    day         TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS scores (
                    team_id      INTEGER NOT NULL,
                    scenario_num INTEGER NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'Not Started',
                    points       REAL NOT NULL DEFAULT 0,
                    minutes      REAL,
                    passed       INTEGER NOT NULL DEFAULT 0,
                    notes        TEXT,
                    updated_at   TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (team_id, scenario_num),
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
                )""",
                """CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS catalog_extra (
                    num       INTEGER NOT NULL,
                    col_name  TEXT NOT NULL,
                    col_value TEXT,
                    PRIMARY KEY (num, col_name)
                )""",
                """CREATE TABLE IF NOT EXISTS score_log (
                    id           SERIAL PRIMARY KEY,
                    team_id      INTEGER NOT NULL,
                    team_name    TEXT NOT NULL,
                    scenario_num INTEGER NOT NULL,
                    status       TEXT,
                    points       REAL,
                    minutes      REAL,
                    passed       INTEGER,
                    notes        TEXT,
                    trainer_name TEXT NOT NULL,
                    logged_at    TIMESTAMPTZ DEFAULT NOW(),
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
                )""",
            ]
            for stmt in stmts:
                _execute(conn, stmt)
        else:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS teams (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS scenarios (
                    num         INTEGER PRIMARY KEY,
                    title       TEXT NOT NULL,
                    max_points  INTEGER NOT NULL,
                    est_minutes REAL,
                    scoring     TEXT,
                    day         TEXT
                );
                CREATE TABLE IF NOT EXISTS scores (
                    team_id      INTEGER NOT NULL,
                    scenario_num INTEGER NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'Not Started',
                    points       REAL NOT NULL DEFAULT 0,
                    minutes      REAL,
                    passed       INTEGER NOT NULL DEFAULT 0,
                    notes        TEXT,
                    updated_at   TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (team_id, scenario_num),
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS catalog_extra (
                    num       INTEGER NOT NULL,
                    col_name  TEXT NOT NULL,
                    col_value TEXT,
                    PRIMARY KEY (num, col_name)
                );
                CREATE TABLE IF NOT EXISTS score_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id      INTEGER NOT NULL,
                    team_name    TEXT NOT NULL,
                    scenario_num INTEGER NOT NULL,
                    status       TEXT,
                    points       REAL,
                    minutes      REAL,
                    passed       INTEGER,
                    notes        TEXT,
                    trainer_name TEXT NOT NULL,
                    logged_at    TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
                );
                """
            )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = _fetchone(conn, "SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        if _is_pg():
            _execute(conn,
                "INSERT INTO settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value))
        else:
            _execute(conn,
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))


def get_time_bonus_table() -> list[int]:
    raw = get_setting("time_bonus_table")
    if not raw:
        return list(DEFAULT_TIME_BONUS_TABLE)
    try:
        return [int(x) for x in raw.split(",") if x.strip() != ""]
    except ValueError:
        return list(DEFAULT_TIME_BONUS_TABLE)


def set_time_bonus_table(values: Iterable[int]) -> None:
    set_setting("time_bonus_table", ",".join(str(int(v)) for v in values))


def get_dashboard_title() -> str:
    return get_setting("dashboard_title") or "SPARK PERFORM Week - Live Leaderboard"


def set_dashboard_title(title: str) -> None:
    set_setting("dashboard_title", title.strip())


def get_sidebar_title() -> str:
    return get_setting("sidebar_title") or "SPARK Scoreboard"


def set_sidebar_title(title: str) -> None:
    set_setting("sidebar_title", title.strip())


def get_sidebar_subtitle() -> str:
    return get_setting("sidebar_subtitle") or "PERFORM Week - Module 3"


def set_sidebar_subtitle(subtitle: str) -> None:
    set_setting("sidebar_subtitle", subtitle.strip())


def speed_bonus_explanation(bonus_table: list[int] | None = None) -> str:
    if bonus_table is None:
        bonus_table = get_time_bonus_table()

    def ordinal(n: int) -> str:
        if n == 1:
            return "Fastest"
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")
        return f"{n}{suffix} fastest"

    lines = [
        "**How the speed bonus is allotted**",
        "",
        "For *each scenario*, the app looks only at teams whose solution was marked "
        "**Passed** *and* that have a recorded **time taken**. Those teams are ranked "
        "from fastest to slowest, and the quickest ones earn extra points:",
        "",
    ]
    for i, v in enumerate(bonus_table, start=1):
        lines.append(f"- {ordinal(i)} passing team -> **+{v} pts**")
    lines += [
        "- Everyone slower (or who did not pass / was not timed) -> **+0 pts**",
        "",
        "These bonuses are summed across all scenarios and added to the reviewer "
        "points to form each team's **Total**. If two teams have the same total, the "
        "team with the lower **overall time** ranks higher (tiebreaker).",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
def add_team(name: str) -> None:
    name = name.strip()
    if not name:
        return
    with _connect() as conn:
        if _is_pg():
            _execute(conn,
                "INSERT INTO teams (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                (name,))
        else:
            _execute(conn, "INSERT OR IGNORE INTO teams (name) VALUES (?)", (name,))


def rename_team(team_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        return
    with _connect() as conn:
        _execute(conn, "UPDATE teams SET name=? WHERE id=?", (new_name, team_id))


def delete_team(team_id: int) -> None:
    with _connect() as conn:
        _execute(conn, "DELETE FROM teams WHERE id=?", (team_id,))


def get_teams() -> pd.DataFrame:
    with _connect() as conn:
        return _read_sql("SELECT id, name FROM teams ORDER BY name", conn)


def ensure_default_teams(count: int = 10) -> None:
    if len(get_teams()) == 0:
        for i in range(1, count + 1):
            add_team(f"Team {i:02d}")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def get_scenarios() -> pd.DataFrame:
    with _connect() as conn:
        return _read_sql("SELECT * FROM scenarios ORDER BY num", conn)


def sync_scenarios(core: pd.DataFrame) -> None:
    """Sync the scenarios table from the catalog core columns."""
    keep_nums = [int(n) for n in core["num"].tolist()]
    with _connect() as conn:
        for _, r in core.iterrows():
            est = None if pd.isna(r.get("est_minutes")) else float(r["est_minutes"])
            if _is_pg():
                _execute(conn,
                    """INSERT INTO scenarios
                           (num, title, max_points, est_minutes, scoring, day)
                       VALUES (%s, %s, %s, %s, %s,
                               COALESCE((SELECT day FROM scenarios WHERE num=%s), ''))
                       ON CONFLICT (num) DO UPDATE SET
                           title=EXCLUDED.title,
                           max_points=EXCLUDED.max_points,
                           est_minutes=EXCLUDED.est_minutes,
                           scoring=EXCLUDED.scoring""",
                    (int(r["num"]), str(r["title"]), int(r["max_points"]),
                     est, str(r.get("scoring", "")), int(r["num"])))
            else:
                _execute(conn,
                    """INSERT INTO scenarios
                           (num, title, max_points, est_minutes, scoring, day)
                       VALUES (?, ?, ?, ?, ?,
                               COALESCE((SELECT day FROM scenarios WHERE num=?), ''))
                       ON CONFLICT(num) DO UPDATE SET
                           title=excluded.title,
                           max_points=excluded.max_points,
                           est_minutes=excluded.est_minutes,
                           scoring=excluded.scoring""",
                    (int(r["num"]), str(r["title"]), int(r["max_points"]),
                     est, str(r.get("scoring", "")), int(r["num"])))
        if keep_nums:
            ph = ",".join(["%s" if _is_pg() else "?"] * len(keep_nums))
            _execute(conn,
                f"DELETE FROM scenarios WHERE num NOT IN ({ph})",
                keep_nums)


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------
def upsert_score(
    team_id: int,
    scenario_num: int,
    status: str,
    points: float,
    minutes: float | None,
    passed: bool,
    notes: str | None,
) -> None:
    with _connect() as conn:
        if _is_pg():
            _execute(conn,
                """INSERT INTO scores
                       (team_id, scenario_num, status, points, minutes,
                        passed, notes, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (team_id, scenario_num) DO UPDATE SET
                       status=EXCLUDED.status,
                       points=EXCLUDED.points,
                       minutes=EXCLUDED.minutes,
                       passed=EXCLUDED.passed,
                       notes=EXCLUDED.notes,
                       updated_at=NOW()""",
                (team_id, scenario_num, status, float(points),
                 None if minutes in (None, "") else float(minutes),
                 1 if passed else 0, notes))
        else:
            _execute(conn,
                """INSERT INTO scores
                       (team_id, scenario_num, status, points, minutes,
                        passed, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(team_id, scenario_num) DO UPDATE SET
                       status=excluded.status,
                       points=excluded.points,
                       minutes=excluded.minutes,
                       passed=excluded.passed,
                       notes=excluded.notes,
                       updated_at=datetime('now')""",
                (team_id, scenario_num, status, float(points),
                 None if minutes in (None, "") else float(minutes),
                 1 if passed else 0, notes))


def get_score(team_id: int, scenario_num: int) -> dict | None:
    with _connect() as conn:
        return _fetchone(conn,
            "SELECT * FROM scores WHERE team_id=? AND scenario_num=?",
            (team_id, scenario_num))


def get_all_scores() -> pd.DataFrame:
    with _connect() as conn:
        return _read_sql(
            """SELECT s.team_id, t.name AS team, s.scenario_num,
                      COALESCE(sc.title,
                               'Scenario #' || CAST(s.scenario_num AS TEXT)) AS scenario,
                      s.status, s.points, s.minutes, s.passed, s.notes,
                      CAST(s.updated_at AS TEXT) AS updated_at,
                      COALESCE(sc.max_points, 0) AS max_points
               FROM scores s
               JOIN teams t           ON t.id = s.team_id
               LEFT JOIN scenarios sc ON sc.num = s.scenario_num
               ORDER BY s.scenario_num, t.name""",
            conn)


def reset_all_scores() -> None:
    with _connect() as conn:
        _execute(conn, "DELETE FROM scores")


def log_score_entry(
    team_id: int,
    team_name: str,
    scenario_num: int,
    status: str,
    points: float,
    minutes: float | None,
    passed: bool,
    notes: str | None,
    trainer_name: str,
) -> None:
    """Log a score entry to the audit trail."""
    with _connect() as conn:
        if _is_pg():
            _execute(conn,
                """INSERT INTO score_log
                       (team_id, team_name, scenario_num, status, points, minutes,
                        passed, notes, trainer_name, logged_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                (team_id, team_name, scenario_num, status, float(points),
                 None if minutes in (None, "") else float(minutes),
                 1 if passed else 0, notes, trainer_name))
        else:
            _execute(conn,
                """INSERT INTO score_log
                       (team_id, team_name, scenario_num, status, points, minutes,
                        passed, notes, trainer_name, logged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (team_id, team_name, scenario_num, status, float(points),
                 None if minutes in (None, "") else float(minutes),
                 1 if passed else 0, notes, trainer_name))


def get_score_log() -> pd.DataFrame:
    """Retrieve the complete score entry audit log."""
    with _connect() as conn:
        return _read_sql(
            """SELECT id, team_id, team_name, scenario_num, status, points, minutes,
                      passed, notes, trainer_name,
                      CAST(logged_at AS TEXT) AS logged_at
               FROM score_log
               ORDER BY logged_at DESC""",
            conn)


# ---------------------------------------------------------------------------
# Scoring computation
# ---------------------------------------------------------------------------
def compute_time_bonus(scores: pd.DataFrame, bonus_table: list[int]) -> pd.DataFrame:
    scores = scores.copy()
    scores["time_bonus"] = 0.0
    for scen_num in scores["scenario_num"].unique():
        mask = (
            (scores["scenario_num"] == scen_num)
            & (scores["passed"] == 1)
            & (scores["minutes"].notna())
            & (scores["minutes"] > 0)
        )
        eligible = scores.loc[mask].sort_values("minutes")
        for rank, idx in enumerate(eligible.index):
            if rank < len(bonus_table):
                scores.at[idx, "time_bonus"] = float(bonus_table[rank])
    return scores


def build_leaderboard(bonus_table: list[int] | None = None) -> pd.DataFrame:
    if bonus_table is None:
        bonus_table = get_time_bonus_table()

    teams = get_teams()
    scores = get_all_scores()
    scores = compute_time_bonus(scores, bonus_table)

    rows = []
    for _, team in teams.iterrows():
        t = scores[scores["team_id"] == team["id"]]
        completed = t[t["status"] == "Reviewed"]
        base = float(t["points"].sum())
        bonus = float(t["time_bonus"].sum())
        total_minutes = float(t["minutes"].fillna(0).sum())
        rows.append({
            "team": team["name"],
            "base_points": round(base, 1),
            "time_bonus": round(bonus, 1),
            "total_points": round(base + bonus, 1),
            "scenarios_completed": int(len(completed)),
            "total_minutes": round(total_minutes, 1),
        })

    lb = pd.DataFrame(rows)
    if lb.empty:
        return lb

    lb = lb.sort_values(
        by=["total_points", "total_minutes"],
        ascending=[False, True],
    ).reset_index(drop=True)
    lb.insert(0, "rank", lb.index + 1)
    return lb
