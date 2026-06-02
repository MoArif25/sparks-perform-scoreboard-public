"""
SQLite data layer and scoring logic for the SPARKS PERFORM Week scoreboard.

Design goals: zero external services, single self-contained file (sparks.db),
easy to back up (just copy the .db file) and reset.

Scoring model
-------------
Per scenario, a trainer records for each team:
    * status   : Not Started / In Progress / Submitted / Reviewed
    * points   : reviewer-awarded points (0..max_points)
    * minutes  : time taken to complete (used for time bonus + tiebreaker)
    * passed   : whether the solution met the bar (eligible for time bonus)

Team total = sum(points across scenarios) + sum(time_bonus across scenarios)

Time bonus (per scenario): among teams that PASSED a scenario, rank by minutes
ascending; the fastest teams receive bonus points from TIME_BONUS_TABLE.

Tiebreaker: equal total points -> lower total minutes ranks higher.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import pandas as pd

from scenarios import DEFAULT_MAX_POINTS, SCENARIOS

DB_PATH = Path(__file__).with_name("sparks.db")

# Bonus points awarded to the fastest passing teams, position 1..N.
# Edit / extend this list to change how aggressive the speed reward is.
DEFAULT_TIME_BONUS_TABLE = [5, 3, 2, 1]

STATUS_OPTIONS = ["Not Started", "In Progress", "Submitted", "Reviewed"]


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables (if needed) and seed scenarios."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS teams (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                name    TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS scenarios (
                num        INTEGER PRIMARY KEY,
                title      TEXT NOT NULL,
                max_points INTEGER NOT NULL,
                est_minutes INTEGER,
                scoring    TEXT,
                day        TEXT
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
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                FOREIGN KEY (scenario_num) REFERENCES scenarios(num)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        # Seed / refresh scenario catalog.
        for s in SCENARIOS:
            max_pts = s["max_points"] if s["max_points"] is not None else DEFAULT_MAX_POINTS
            conn.execute(
                """
                INSERT INTO scenarios (num, title, max_points, est_minutes, scoring, day)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(num) DO UPDATE SET
                    title=excluded.title,
                    max_points=excluded.max_points,
                    est_minutes=excluded.est_minutes,
                    scoring=excluded.scoring,
                    day=excluded.day
                """,
                (s["num"], s["title"], max_pts, s["est_minutes"], s["scoring"], s["day"]),
            )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


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
    """Get the custom dashboard title, or the default if not set."""
    return get_setting("dashboard_title") or "⚡ SPARKS PERFORM Week — Live Leaderboard"


def set_dashboard_title(title: str) -> None:
    """Set the custom dashboard title."""
    set_setting("dashboard_title", title.strip())


def get_sidebar_title() -> str:
    """Get the custom sidebar title, or the default if not set."""
    return get_setting("sidebar_title") or "⚡ SPARKS Scoreboard"


def set_sidebar_title(title: str) -> None:
    """Set the custom sidebar title."""
    set_setting("sidebar_title", title.strip())


def get_sidebar_subtitle() -> str:
    """Get the custom sidebar subtitle, or the default if not set."""
    return get_setting("sidebar_subtitle") or "PERFORM Week · Module 3"


def set_sidebar_subtitle(subtitle: str) -> None:
    """Set the custom sidebar subtitle."""
    set_setting("sidebar_subtitle", subtitle.strip())


def speed_bonus_explanation(bonus_table: list[int] | None = None) -> str:
    """Return a human-readable markdown explanation of how the speed bonus works."""
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
        lines.append(f"- {ordinal(i)} passing team → **+{v} pts**")
    lines += [
        "- Everyone slower (or who didn't pass / wasn't timed) → **+0 pts**",
        "",
        "These bonuses are summed across all scenarios and added to the reviewer "
        "points to form each team's **Total**. If two teams have the same total, the "
        "team with the lower **overall time** ranks higher (tiebreaker).",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Teams
# --------------------------------------------------------------------------- #
def add_team(name: str) -> None:
    name = name.strip()
    if not name:
        return
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)", (name,))


def rename_team(team_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        return
    with _connect() as conn:
        conn.execute("UPDATE teams SET name=? WHERE id=?", (new_name, team_id))


def delete_team(team_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM teams WHERE id=?", (team_id,))


def get_teams() -> pd.DataFrame:
    with _connect() as conn:
        return pd.read_sql_query("SELECT id, name FROM teams ORDER BY name", conn)


def ensure_default_teams(count: int = 10) -> None:
    """Create 'Team 01'..'Team NN' if no teams exist yet."""
    if len(get_teams()) == 0:
        for i in range(1, count + 1):
            add_team(f"Team {i:02d}")


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
def get_scenarios() -> pd.DataFrame:
    with _connect() as conn:
        return pd.read_sql_query("SELECT * FROM scenarios ORDER BY num", conn)


def sync_scenarios(core: pd.DataFrame) -> None:
    """Sync the DB scenarios table from the editable catalog's core columns.

    ``core`` must have columns: num, title, max_points, est_minutes, scoring.
    Scenarios present in the DB but missing from the catalog are removed (their
    historical scores are kept; the leaderboard still counts them).
    """
    keep_nums = [int(n) for n in core["num"].tolist()]
    with _connect() as conn:
        for _, r in core.iterrows():
            est = None if pd.isna(r.get("est_minutes")) else float(r["est_minutes"])
            conn.execute(
                """
                INSERT INTO scenarios (num, title, max_points, est_minutes, scoring, day)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT day FROM scenarios WHERE num=?), ''))
                ON CONFLICT(num) DO UPDATE SET
                    title=excluded.title,
                    max_points=excluded.max_points,
                    est_minutes=excluded.est_minutes,
                    scoring=excluded.scoring
                """,
                (int(r["num"]), str(r["title"]), int(r["max_points"]),
                 est, str(r.get("scoring", "")), int(r["num"])),
            )
        if keep_nums:
            placeholders = ",".join("?" for _ in keep_nums)
            conn.execute(
                f"DELETE FROM scenarios WHERE num NOT IN ({placeholders})", keep_nums
            )


# --------------------------------------------------------------------------- #
# Scores
# --------------------------------------------------------------------------- #
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
        conn.execute(
            """
            INSERT INTO scores (team_id, scenario_num, status, points, minutes, passed, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(team_id, scenario_num) DO UPDATE SET
                status=excluded.status,
                points=excluded.points,
                minutes=excluded.minutes,
                passed=excluded.passed,
                notes=excluded.notes,
                updated_at=datetime('now')
            """,
            (team_id, scenario_num, status, float(points),
             None if minutes in (None, "") else float(minutes),
             1 if passed else 0, notes),
        )


def get_score(team_id: int, scenario_num: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM scores WHERE team_id=? AND scenario_num=?",
            (team_id, scenario_num),
        ).fetchone()
    return dict(row) if row else None


def get_all_scores() -> pd.DataFrame:
    with _connect() as conn:
        return pd.read_sql_query(
            """
            SELECT s.team_id, t.name AS team, s.scenario_num,
                   COALESCE(sc.title, 'Scenario #' || s.scenario_num) AS scenario,
                   s.status, s.points, s.minutes, s.passed, s.notes, s.updated_at,
                   COALESCE(sc.max_points, 0) AS max_points
            FROM scores s
            JOIN teams t           ON t.id = s.team_id
            LEFT JOIN scenarios sc ON sc.num = s.scenario_num
            ORDER BY s.scenario_num, t.name
            """,
            conn,
        )


def reset_all_scores() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM scores")


# --------------------------------------------------------------------------- #
# Scoring computation
# --------------------------------------------------------------------------- #
def compute_time_bonus(scores: pd.DataFrame, bonus_table: list[int]) -> pd.DataFrame:
    """Return scores with a 'time_bonus' column added.

    Per scenario, passing teams with a recorded minutes value are ranked
    fastest-first; ranks 1..N receive bonus_table[0..N-1] points.
    """
    scores = scores.copy()
    scores["time_bonus"] = 0.0
    if scores.empty:
        return scores

    eligible = scores[(scores["passed"] == 1) & (scores["minutes"].notna())]
    for scen, grp in eligible.groupby("scenario_num"):
        ordered = grp.sort_values("minutes", ascending=True)
        for rank, (idx, _row) in enumerate(ordered.iterrows()):
            if rank < len(bonus_table):
                scores.loc[idx, "time_bonus"] = float(bonus_table[rank])
    return scores


def build_leaderboard(bonus_table: list[int] | None = None) -> pd.DataFrame:
    """Aggregate per-team totals and rank them.

    Returns a DataFrame with columns:
        rank, team, base_points, time_bonus, total_points,
        scenarios_completed, total_minutes
    """
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
        rows.append(
            {
                "team": team["name"],
                "base_points": round(base, 1),
                "time_bonus": round(bonus, 1),
                "total_points": round(base + bonus, 1),
                "scenarios_completed": int(len(completed)),
                "total_minutes": round(total_minutes, 1),
            }
        )

    lb = pd.DataFrame(rows)
    if lb.empty:
        return lb

    # Points first (desc); tiebreaker = total minutes (asc, faster wins).
    lb = lb.sort_values(
        by=["total_points", "total_minutes"],
        ascending=[False, True],
    ).reset_index(drop=True)
    lb.insert(0, "rank", lb.index + 1)
    return lb
