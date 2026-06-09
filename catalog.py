"""
Flexible, editable micro-scenario catalog.

Storage strategy:
  • Postgres mode  – catalog lives entirely in the DB (catalog_extra table stores
                     free-form columns; scenarios table stores core columns).
                     All trainer edits (add/remove rows or columns, change values)
                     are retained permanently across restarts.
  • SQLite mode    – catalog lives in scenarios_catalog.csv as before (local dev).

Public API is unchanged so app.py needs no edits.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scenarios import DEFAULT_MAX_POINTS, SCENARIOS
import scoring as _scoring

CATALOG_PATH = Path(__file__).with_name("scenarios_catalog.csv")

CORE_COLUMNS = ["num", "title", "max_points", "est_minutes"]

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


# --------------------------------------------------------------------------- #
# Postgres catalog helpers
# --------------------------------------------------------------------------- #
def _pg_load() -> pd.DataFrame:
    """Rebuild the full catalog DataFrame from the DB (core + extra columns)."""
    with _scoring._connect() as conn:
        core_df = _scoring._read_sql(
            "SELECT num, title, max_points, est_minutes, scoring, day FROM scenarios ORDER BY num",
            conn,
        )
        if core_df.empty:
            return core_df
        extra_df = _scoring._read_sql(
            "SELECT num, col_name, col_value FROM catalog_extra ORDER BY num",
            conn,
        )

    if extra_df.empty:
        return core_df.astype(str).replace("None", "").replace("nan", "")

    pivot = extra_df.pivot(index="num", columns="col_name", values="col_value").reset_index()
    merged = core_df.merge(pivot, on="num", how="left").fillna("")
    merged = merged.astype(str).replace("None", "").replace("nan", "")
    return merged


def _pg_save(df: pd.DataFrame) -> None:
    """Persist the full catalog DataFrame back to the DB."""
    extra_cols = [c for c in df.columns if c not in CORE_COLUMNS]
    with _scoring._connect() as conn:
        # Wipe and rewrite catalog_extra for all rows in the edited df.
        nums = [int(r["num"]) for _, r in df.iterrows()
                if str(r.get("num", "")).strip().lstrip("-").isdigit()]
        if nums:
            ph = ",".join(["%s" if _scoring._is_pg() else "?"] * len(nums))
            _scoring._execute(conn, f"DELETE FROM catalog_extra WHERE num IN ({ph})", nums)

        for _, row in df.iterrows():
            try:
                num = int(row["num"])
            except (ValueError, KeyError):
                continue
            for col in extra_cols:
                val = str(row.get(col, ""))
                if _scoring._is_pg():
                    _scoring._execute(conn,
                        "INSERT INTO catalog_extra (num, col_name, col_value) VALUES (%s, %s, %s) "
                        "ON CONFLICT (num, col_name) DO UPDATE SET col_value=EXCLUDED.col_value",
                        (num, col, val))
                else:
                    _scoring._execute(conn,
                        "INSERT INTO catalog_extra (num, col_name, col_value) VALUES (?, ?, ?) "
                        "ON CONFLICT(num, col_name) DO UPDATE SET col_value=excluded.col_value",
                        (num, col, val))


def load_catalog() -> pd.DataFrame:
    """Load the catalog from DB (Postgres) or CSV (SQLite)."""
    if _scoring._is_pg():
        df = _pg_load()
        if df.empty:
            # First run: seed from scenarios.py into DB
            seed = _seed_dataframe()
            save_catalog(seed)
            df = _pg_load()
        return df
    # SQLite path (local dev / fallback)
    if CATALOG_PATH.exists():
        return pd.read_csv(CATALOG_PATH, dtype=str, keep_default_na=False)
    df = _seed_dataframe()
    save_catalog(df)
    return pd.read_csv(CATALOG_PATH, dtype=str, keep_default_na=False)


def save_catalog(df: pd.DataFrame) -> None:
    """Persist the catalog to DB (Postgres) or CSV (SQLite)."""
    if _scoring._is_pg():
        _pg_save(df)
        # Also push core columns into scenarios table
        _scoring.sync_scenarios(core_scenarios_from_df(df))
    else:
        df.to_csv(CATALOG_PATH, index=False)


def core_scenarios_from_df(df: pd.DataFrame) -> pd.DataFrame:
    """Parse core scoring columns from an arbitrary catalog DataFrame."""
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


def core_scenarios() -> pd.DataFrame:
    return core_scenarios_from_df(load_catalog())
