"""
Dual-backend data layer and scoring logic for the SPARK PERFORM Week scoreboard.

Backend selection (automatic):
  * Postgres  -- when st.secrets["DATABASE_URL"] is set (Supabase / any hosted PG)
  * SQLite    -- fallback for local development (sparks.db next to this file)

All SQL is compatible with both backends. Placeholders are translated
from ? (SQLite) to %s (Postgres) automatically in _execute / _fetchone.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import pandas as pd

from scenarios import DEFAULT_MAX_POINTS, SCENARIOS

DB_PATH = Path(__file__).with_name("sparks.db")

DEFAULT_TIME_BONUS_TABLE = [5, 3, 2, 1]
STATUS_OPTIONS = ["Not Started", "In Progress", "Submitted", "Reviewed"]

_PG_URL: str | None = None
_BACKEND: str | None = None  # "pg" or "sqlite", decided once per process


def _build_pg_url_from_parts(
    host: str,
    port: str | int | None,
    dbname: str,
    user: str,
    password: str,
) -> str:
    """Build a URL-safe Postgres DSN from discrete connection fields."""
    safe_user = quote(str(user), safe="")
    safe_password = quote(str(password), safe="")
    port_part = f":{port}" if str(port or "").strip() else ""
    safe_db = quote(str(dbname), safe="")
    return f"postgresql://{safe_user}:{safe_password}@{host}{port_part}/{safe_db}"


def _first_non_empty(values: Iterable[object]) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _assemble_pg_url_from_fields(secret_lookup, env_lookup) -> str | None:
    """Build DATABASE_URL from common field-based secret names.

    Supports both generic PG* and Supabase-style keys.
    """
    host = _first_non_empty(
        [
            secret_lookup("PGHOST"),
            secret_lookup("POSTGRES_HOST"),
            secret_lookup("SUPABASE_DB_HOST"),
            env_lookup("PGHOST"),
            env_lookup("POSTGRES_HOST"),
            env_lookup("SUPABASE_DB_HOST"),
        ]
    )
    port = _first_non_empty(
        [
            secret_lookup("PGPORT"),
            secret_lookup("POSTGRES_PORT"),
            secret_lookup("SUPABASE_DB_PORT"),
            env_lookup("PGPORT"),
            env_lookup("POSTGRES_PORT"),
            env_lookup("SUPABASE_DB_PORT"),
            "5432",
        ]
    )
    dbname = _first_non_empty(
        [
            secret_lookup("PGDATABASE"),
            secret_lookup("POSTGRES_DB"),
            secret_lookup("SUPABASE_DB_NAME"),
            env_lookup("PGDATABASE"),
            env_lookup("POSTGRES_DB"),
            env_lookup("SUPABASE_DB_NAME"),
            "postgres",
        ]
    )
    user = _first_non_empty(
        [
            secret_lookup("PGUSER"),
            secret_lookup("POSTGRES_USER"),
            secret_lookup("SUPABASE_DB_USER"),
            env_lookup("PGUSER"),
            env_lookup("POSTGRES_USER"),
            env_lookup("SUPABASE_DB_USER"),
        ]
    )
    password = _first_non_empty(
        [
            secret_lookup("PGPASSWORD"),
            secret_lookup("POSTGRES_PASSWORD"),
            secret_lookup("SUPABASE_DB_PASSWORD"),
            env_lookup("PGPASSWORD"),
            env_lookup("POSTGRES_PASSWORD"),
            env_lookup("SUPABASE_DB_PASSWORD"),
        ]
    )

    if host and user and password:
        return _build_pg_url_from_parts(host, port, dbname or "postgres", user, password)
    return None


def _normalize_pg_url(url: str) -> str:
    """Normalize Supabase URLs for Streamlit Cloud.

    Preferred: provide the Supabase session pooler URL directly in DATABASE_URL.
    Optional: if DATABASE_URL is a direct Supabase host (db.<ref>.supabase.co),
    set SUPABASE_POOLER_REGION so we can rewrite to the pooler host.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Already a session pooler URL (recommended on Streamlit Cloud).
    if host.endswith("pooler.supabase.com"):
        return _ensure_sslmode_require(url)

    # Direct host -> rewrite only when region is explicitly provided.
    if host.startswith("db.") and host.endswith(".supabase.co"):
        ref = host.split(".")[1] if len(host.split(".")) >= 3 else ""
        if ref:
            try:
                import streamlit as st
                region = str(st.secrets.get("SUPABASE_POOLER_REGION", "")).strip()
            except Exception:
                region = ""
            if region:
                user = parsed.username or "postgres"
                password = parsed.password or ""
                auth = user
                if password:
                    auth = f"{user}:{password}"
                netloc = f"{auth}@{region}.pooler.supabase.com:{parsed.port or 5432}"
                rewritten = urlunparse(
                    (
                        parsed.scheme or "postgresql",
                        netloc,
                        parsed.path or "/postgres",
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
                return _ensure_sslmode_require(rewritten)

    return _ensure_sslmode_require(url)


def _ensure_sslmode_require(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "sslmode" not in query:
        query["sslmode"] = "require"
    return urlunparse(parsed._replace(query=urlencode(query)))


def _postgres_connection_hint(exc: Exception) -> str:
    msg = str(exc)
    if "Cannot assign requested address" in msg or "Network is unreachable" in msg:
        return (
            "Supabase direct DB host is likely unreachable from Streamlit Cloud. "
            "Use a Supabase session pooler DATABASE_URL, or set SUPABASE_POOLER_REGION "
            "so the app can rewrite direct URLs."
        )
    if "password authentication failed" in msg:
        return "Postgres authentication failed. Recheck DATABASE_URL username/password."
    if "could not translate host name" in msg:
        return "Postgres host is invalid. Recheck DATABASE_URL host value."
    if "timeout expired" in msg:
        return "Postgres connection timed out. Verify network access and Supabase status."
    return "Verify DATABASE_URL and SUPABASE_POOLER_REGION secrets in Streamlit Cloud."


def _get_pg_url() -> str | None:
    global _PG_URL, _BACKEND
    if _PG_URL is not None:
        return _PG_URL

    # First, trust explicit environment variables if provided.
    env_url = _first_non_empty([
        os.getenv("DATABASE_URL"),
        os.getenv("POSTGRES_URL"),
        os.getenv("SUPABASE_DB_URL"),
        os.getenv("SUPABASE_DATABASE_URL"),
    ])
    if env_url:
        _PG_URL = _normalize_pg_url(env_url)
        _BACKEND = "pg"
        return _PG_URL

    def _env_lookup(key: str):
        return os.getenv(key)

    def _secret_lookup(_key: str):
        return None

    try:
        import streamlit as st
        def _secret_lookup(key: str):
            return st.secrets.get(key)

        url = _first_non_empty([
            st.secrets.get("DATABASE_URL"),
            st.secrets.get("POSTGRES_URL"),
            st.secrets.get("SUPABASE_DB_URL"),
            st.secrets.get("SUPABASE_DATABASE_URL"),
            os.getenv("DATABASE_URL"),
            os.getenv("POSTGRES_URL"),
            os.getenv("SUPABASE_DB_URL"),
            os.getenv("SUPABASE_DATABASE_URL"),
        ])
        if not url:
            url = _assemble_pg_url_from_fields(_secret_lookup, _env_lookup)

        if url:
            _PG_URL = _normalize_pg_url(str(url))
            _BACKEND = "pg"
            return _PG_URL
    except Exception:
        pass

    # Last attempt: assemble from environment variables only.
    url = _assemble_pg_url_from_fields(_secret_lookup, _env_lookup)
    if url:
        _PG_URL = _normalize_pg_url(url)
        _BACKEND = "pg"
        return _PG_URL

    return None


def secret_diagnostics() -> dict:
    """Return non-sensitive info about which DB config keys are visible.

    Only reports key NAMES (never values) so it is safe to render in the UI
    when startup fails, helping diagnose Streamlit Cloud secret problems.
    """
    known_keys = [
        "DATABASE_URL", "POSTGRES_URL", "SUPABASE_DB_URL", "SUPABASE_DATABASE_URL",
        "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
        "SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_NAME",
        "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD",
    ]
    secret_keys_present: list[str] = []
    all_secret_key_names: list[str] = []
    secrets_readable = False
    secrets_error = ""
    try:
        import streamlit as st
        try:
            all_secret_key_names = sorted(list(st.secrets.keys()))
            secrets_readable = True
        except Exception as exc:  # noqa: BLE001
            secrets_error = str(exc)
        for k in known_keys:
            try:
                if str(st.secrets.get(k) or "").strip():
                    secret_keys_present.append(k)
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        secrets_error = str(exc)

    env_keys_present = [k for k in known_keys if str(os.getenv(k) or "").strip()]

    return {
        "secrets_readable": secrets_readable,
        "secrets_error": secrets_error,
        "all_secret_top_level_keys": all_secret_key_names,
        "recognized_secret_keys": secret_keys_present,
        "recognized_env_keys": env_keys_present,
    }


def _resolve_backend() -> str:
    """Decide ONCE whether this process talks to Postgres or SQLite, and keep
    that decision sticky for the whole process.

    This prevents a dangerous failure mode on Streamlit Cloud: if reading
    st.secrets transiently returns nothing inside a fragment/thread context,
    the app could silently fall back to a *different* (empty, ephemeral) SQLite
    database mid-session, making committed Supabase rows appear to come and go.
    Once we have ever seen a DATABASE_URL, we stay on Postgres permanently.
    """
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    if _get_pg_url():          # sets _BACKEND = "pg" on success
        return _BACKEND
    _BACKEND = "sqlite"        # no secret configured -> local dev mode
    return _BACKEND


def _is_pg() -> bool:
    return _resolve_backend() == "pg"


def backend_name() -> str:
    return _resolve_backend()


@contextmanager
def _connect():
    if _is_pg():
        pg_url = _get_pg_url()
        if not pg_url:
            # We are in Postgres mode but the URL could not be read. Do NOT
            # silently fall back to SQLite (that would expose a different,
            # empty database). Fail loudly so the problem is visible.
            raise RuntimeError(
                "DATABASE_URL is configured (Postgres mode) but could not be "
                "read from st.secrets. Refusing to fall back to local SQLite."
            )
        import psycopg2
        try:
            conn = psycopg2.connect(pg_url, connect_timeout=12)
        except Exception as exc:
            hint = _postgres_connection_hint(exc)
            raise RuntimeError(f"Postgres connection failed: {exc}. {hint}") from exc
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
        else:
            # Catalog was intentionally emptied -> remove all scenarios.
            # (Previously this branch was skipped, so 'delete all' never
            #  persisted and rows reappeared on the next load.)
            _execute(conn, "DELETE FROM scenarios")


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
               ORDER BY id DESC""",
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
    # Read everything we need over a SINGLE connection. Previously this opened
    # three separate connections (settings, teams, scores); on Supabase each
    # new pooled connection adds a network round-trip, and doing that on every
    # 15s live refresh caused latency spikes and visible flicker.
    with _connect() as conn:
        if bonus_table is None:
            row = _fetchone(conn,
                "SELECT value FROM settings WHERE key=?", ("time_bonus_table",))
            raw = row["value"] if row else None
            if raw:
                try:
                    bonus_table = [int(x) for x in raw.split(",") if x.strip() != ""]
                except ValueError:
                    bonus_table = list(DEFAULT_TIME_BONUS_TABLE)
            else:
                bonus_table = list(DEFAULT_TIME_BONUS_TABLE)

        teams = _read_sql("SELECT id, name FROM teams ORDER BY name", conn)
        scores = _read_sql(
            """SELECT s.team_id, t.name AS team, s.scenario_num,
                      s.status, s.points, s.minutes, s.passed
               FROM scores s
               JOIN teams t ON t.id = s.team_id
               ORDER BY s.scenario_num, t.name""",
            conn)

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
