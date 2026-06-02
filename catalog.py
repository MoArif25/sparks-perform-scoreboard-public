"""
Flexible, editable micro-scenario catalog.

The catalog is stored as a plain CSV (``scenarios_catalog.csv``) so trainers can
freely add/remove rows, add/remove columns, and edit any text — either inside the
app (Scenarios tab) or directly in Excel. The CSV is the single source of truth
for the scenario list; the scoring database is kept in sync from it.

Only a few "core" columns are meaningful to the scoring engine:
    num         -> unique scenario id (integer)
    title       -> shown in dropdowns / leaderboard
    max_points  -> reviewer points cap (blank = default)
    est_minutes -> suggested duration in minutes (optional)
Every other column (description, pillars, day, build resources, ...) is free-form
and preserved as-is.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scenarios import DEFAULT_MAX_POINTS, SCENARIOS

CATALOG_PATH = Path(__file__).with_name("scenarios_catalog.csv")

# Columns the scoring engine understands. These should not be deleted.
CORE_COLUMNS = ["num", "title", "max_points", "est_minutes"]

# Suggested column order when seeding a fresh catalog.
DEFAULT_COLUMNS = [
    "num",
    "title",
    "description",
    "duration",
    "est_minutes",
    "max_points",
    "pillars",
    "scoring",
    "day",
    "notes",
]


def _seed_dataframe() -> pd.DataFrame:
    rows = []
    for s in SCENARIOS:
        rows.append(
            {
                "num": s["num"],
                "title": s["title"],
                "description": s["description"],
                "duration": s["duration_text"],
                "est_minutes": s["est_minutes"],
                "max_points": s["max_points"] if s["max_points"] is not None else "",
                "pillars": " ".join(s["pillars"]),
                "scoring": s["scoring"],
                "day": s["day"],
                "notes": "",
            }
        )
    return pd.DataFrame(rows, columns=DEFAULT_COLUMNS)


def load_catalog() -> pd.DataFrame:
    """Load the catalog, seeding it from scenarios.py on first run."""
    if CATALOG_PATH.exists():
        return pd.read_csv(CATALOG_PATH, dtype=str, keep_default_na=False)
    df = _seed_dataframe()
    save_catalog(df)
    return pd.read_csv(CATALOG_PATH, dtype=str, keep_default_na=False)


def save_catalog(df: pd.DataFrame) -> None:
    """Persist the catalog to CSV."""
    df.to_csv(CATALOG_PATH, index=False)


def core_scenarios() -> pd.DataFrame:
    """Return a cleaned view with parsed core columns for the scoring engine.

    Columns: num (int), title (str), max_points (int), est_minutes (float|NaN),
    scoring (str). Rows without a numeric ``num`` are dropped.
    """
    df = load_catalog()
    out = pd.DataFrame()
    out["num"] = pd.to_numeric(df.get("num"), errors="coerce")
    out["title"] = df.get("title", "").astype(str)
    out["max_points"] = (
        pd.to_numeric(df.get("max_points"), errors="coerce")
        .fillna(DEFAULT_MAX_POINTS)
        .astype(int)
    )
    out["est_minutes"] = pd.to_numeric(df.get("est_minutes"), errors="coerce")
    out["scoring"] = df.get("scoring", "").astype(str) if "scoring" in df else ""
    out = out.dropna(subset=["num"]).copy()
    out["num"] = out["num"].astype(int)
    return out.sort_values("num").reset_index(drop=True)
