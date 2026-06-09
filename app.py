"""
SPARK Module 3 - PERFORM Week live scoreboard.

Run with:
    streamlit run app.py

Access model
------------
* The **Leaderboard** is public — everyone (participants, trainers, core team)
  can watch it live, e.g. on a projector or their own phones.
* **Score Entry**, **Scenario editing** and **Setup** are locked behind a
  trainer password (sidebar). Without it, visitors only see the leaderboard and
  a read-only scenario catalog.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd
import streamlit as st

import catalog
import scoring

st.set_page_config(
    page_title="SPARK PERFORM Week Scoreboard",
    page_icon="⚡",
    layout="wide",
)

# --- One-time startup: init DB, seed teams, sync catalog -> scoring DB -------- #
scoring.init_db()
scoring.ensure_default_teams(10)
scoring.sync_scenarios(catalog.core_scenarios())

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
DEFAULT_TRAINER_PASSWORD = "spark2026"
LOGO_CANDIDATE_NAMES = [
    "spark_logo.png",
    "spark_logo.jpg",
    "spark_logo.jpeg",
    "spark-logo.png",
    "spark-logo.jpg",
    "spark-logo.jpeg",
    "logo.png",
    "logo.jpg",
    "logo.jpeg",
]


def get_logo_path() -> Path | None:
    base_dir = Path(__file__).resolve().parent
    for name in LOGO_CANDIDATE_NAMES:
        candidate = base_dir / name
        if candidate.exists():
            return candidate
    return None


def strip_brand_prefix(text: str) -> str:
    text = text.strip()
    for prefix in ("⚡ ", "⚡", "SPARK ", "SPARKS "):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def render_brand_text(text: str, level: int = 2, sidebar: bool = False) -> None:
    cleaned_text = strip_brand_prefix(text)
    logo_path = get_logo_path()
    text_size = {1: "2.25rem", 2: "1.75rem", 3: "1.35rem"}.get(level, "1.75rem")
    font_weight = {1: 700, 2: 600, 3: 600}.get(level, 600)
    margin_bottom = {1: "0.6rem", 2: "0.5rem", 3: "0.35rem"}.get(level, "0.5rem")
    logo_height = {1: "1.35em", 2: "1.3em", 3: "1.25em"}.get(level, "1.3em")

    if logo_path is not None:
        encoded_logo = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        html = (
            f'<div style="display:flex;align-items:center;gap:0.45rem;margin-bottom:{margin_bottom};">'
            f'<img src="data:image/png;base64,{encoded_logo}" '
            f'style="height:{logo_height};width:auto;vertical-align:middle;" alt="SPARK logo">'
            f'<span style="font-size:{text_size};font-weight:{font_weight};line-height:1.2;">{cleaned_text}</span>'
            '</div>'
        )
    else:
        html = (
            f'<div style="display:flex;align-items:center;gap:0.45rem;margin-bottom:{margin_bottom};">'
            f'<span style="font-size:{text_size};line-height:1;">⚡</span>'
            f'<span style="font-size:{text_size};font-weight:{font_weight};line-height:1.2;">{cleaned_text}</span>'
            '</div>'
        )

    if sidebar:
        st.sidebar.markdown(html, unsafe_allow_html=True)
    else:
        st.markdown(html, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #
def trainer_gate() -> bool:
    """Render the sidebar trainer login; return True if unlocked."""
    render_brand_text(scoring.get_sidebar_title(), level=3, sidebar=True)
    st.sidebar.caption(scoring.get_sidebar_subtitle())

    if st.session_state.get("is_trainer"):
        st.sidebar.success("Trainer / Core team mode")
        if st.sidebar.button("🔒 Log out"):
            st.session_state["is_trainer"] = False
            st.rerun()
        return True

    st.sidebar.info("👀 Viewing the public leaderboard.")
    with st.sidebar.expander("🔑 Trainer / Core team login"):
        pwd = st.text_input("Password", type="password", key="pwd_input")
        if st.button("Unlock"):
            real = scoring.get_setting("trainer_password", DEFAULT_TRAINER_PASSWORD)
            if pwd == real:
                st.session_state["is_trainer"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


# --------------------------------------------------------------------------- #
# Leaderboard
# --------------------------------------------------------------------------- #
def render_leaderboard_body() -> None:
    bonus_table = scoring.get_time_bonus_table()
    lb = scoring.build_leaderboard(bonus_table)

    if lb.empty:
        st.info("No teams yet.")
        return

    leader = lb.iloc[0]
    cols = st.columns(3)
    cols[0].metric("🏆 Leader", leader["team"], f'{leader["total_points"]:.0f} pts')
    cols[1].metric("Teams scoring", int((lb["total_points"] > 0).sum()))
    cols[2].metric("Leading team points", f'{leader["total_points"]:.0f}')

    st.subheader("Standings")
    display = lb.copy()
    display["Rank"] = display["rank"].map(lambda r: f'{MEDALS.get(r, "")} {r}'.strip())
    display = display.rename(
        columns={
            "team": "Team",
            "base_points": "Reviewer pts",
            "time_bonus": "Speed bonus",
            "total_points": "Total",
            "scenarios_completed": "Done",
            "total_minutes": "Time (min)",
        }
    )
    display = display[
        ["Rank", "Team", "Reviewer pts", "Speed bonus", "Total", "Done", "Time (min)"]
    ]
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Total": st.column_config.ProgressColumn(
                "Total", format="%d", min_value=0,
                max_value=float(max(lb["total_points"].max(), 1)),
                color="green",
            ),
        },
    )

    st.subheader("Points by team")
    chart_df = lb.set_index("team")[["base_points", "time_bonus"]].rename(
        columns={"base_points": "Reviewer pts", "time_bonus": "Speed bonus"}
    )
    st.bar_chart(chart_df, color=["#1f77b4", "#ff7f0e"])

    with st.expander("ℹ️ How scoring & the speed bonus work"):
        st.markdown(scoring.speed_bonus_explanation(bonus_table))


@st.fragment(run_every=5)
def live_leaderboard() -> None:
    st.caption("🔴 Live · refreshes every 5 seconds")
    render_leaderboard_body()


def tab_leaderboard() -> None:
    render_brand_text(scoring.get_dashboard_title(), level=2)
    live = st.toggle("Live auto-refresh", value=True, key="live_toggle")
    if live:
        live_leaderboard()
    else:
        if st.button("🔄 Refresh now"):
            st.rerun()
        render_leaderboard_body()


# --------------------------------------------------------------------------- #
# Score entry (trainers only)
# --------------------------------------------------------------------------- #
def tab_score_entry() -> None:
    st.header("📝 Score Entry (Trainers)")
    teams = scoring.get_teams()
    scen = catalog.core_scenarios()

    if teams.empty:
        st.warning("Add teams in the **Setup** tab first.")
        return
    if scen.empty:
        st.warning("Add scenarios in the **Scenarios** tab first.")
        return

    scen_lookup = scen.set_index("num")
    c1, c2 = st.columns(2)
    team_id = c1.selectbox(
        "Team",
        options=teams["id"].tolist(),
        format_func=lambda i: teams.set_index("id").loc[i, "name"],
    )
    scen_num = c2.selectbox(
        "Scenario",
        options=scen["num"].tolist(),
        format_func=lambda n: f'#{n} — {scen_lookup.loc[n, "title"]}',
    )

    max_points = int(scen_lookup.loc[scen_num, "max_points"])
    est_minutes = scen_lookup.loc[scen_num, "est_minutes"]
    scoring_text = scen_lookup.loc[scen_num, "scoring"]

    st.caption(
        f"Max reviewer points: **{max_points}** · Scoring: *{scoring_text}*"
        + (f" · Suggested: ~{int(est_minutes)} min" if pd.notna(est_minutes) else "")
    )

    existing = scoring.get_score(team_id, int(scen_num)) or {}
    with st.form("score_form", clear_on_submit=False):
        fc1, fc2 = st.columns(2)
        status = fc1.selectbox(
            "Status", scoring.STATUS_OPTIONS,
            index=scoring.STATUS_OPTIONS.index(existing.get("status", "Not Started")),
        )
        passed = fc2.checkbox(
            "Solution passed (eligible for speed bonus)",
            value=bool(existing.get("passed", 0)),
        )
        points = fc1.number_input(
            "Reviewer points", min_value=0, max_value=max_points,
            value=min(int(existing.get("points", 0) or 0), max_points), step=1,
        )
        minutes = fc2.number_input(
            "Time taken (minutes)", min_value=0.0,
            value=float(existing["minutes"]) if existing.get("minutes") is not None else 0.0,
            step=1.0,
            help="Used for the speed bonus and as the tiebreaker. Leave 0 if not timed.",
        )
        notes = st.text_area("Notes (optional)", value=existing.get("notes", "") or "")
        submitted = st.form_submit_button("💾 Save score", type="primary")

    if submitted:
        # Get trainer name from session
        trainer_name = st.session_state.get("trainer_name", "Unknown")
        team_name = teams.set_index("id").loc[team_id, "name"]
        
        scoring.upsert_score(
            team_id=team_id, scenario_num=int(scen_num), status=status,
            points=points, minutes=None if minutes == 0 else minutes,
            passed=passed, notes=notes,
        )
        # Log the score entry to audit trail
        scoring.log_score_entry(
            team_id=team_id, team_name=team_name, scenario_num=int(scen_num),
            status=status, points=points, minutes=None if minutes == 0 else minutes,
            passed=passed, notes=notes, trainer_name=trainer_name,
        )
        st.success("Saved. The leaderboard updates on its next refresh.")

    with st.expander("ℹ️ How the speed bonus is allotted"):
        st.markdown(scoring.speed_bonus_explanation())

    st.subheader("This scenario — all teams")
    all_scores = scoring.get_all_scores()
    this_scen = all_scores[all_scores["scenario_num"] == scen_num]
    if this_scen.empty:
        st.caption("No scores recorded for this scenario yet.")
    else:
        view = this_scen[["team", "status", "points", "minutes", "passed", "notes"]].rename(
            columns={"team": "Team", "status": "Status", "points": "Points",
                     "minutes": "Minutes", "passed": "Passed", "notes": "Notes"}
        )
        view["Passed"] = view["Passed"].map({1: "✅", 0: ""})
        st.dataframe(view, hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------- #
# Scenario catalog
# --------------------------------------------------------------------------- #
def tab_scenarios(can_edit: bool) -> None:
    st.header("📋 Micro-Scenario Catalog")

    if not can_edit:
        df = catalog.load_catalog()
        st.caption("Read-only view. Trainers can edit via the sidebar login.")
        st.dataframe(df, hide_index=True, use_container_width=True)
        return

    st.caption(
        "Fully editable. "
        "**Rows:** click any cell to edit it; use the ➕ button at the bottom of the table to add a row; "
        "tick the checkbox on the left of a row then press **Delete** (or the 🗑 icon) to remove it. "
        "**Columns:** use the controls below to add, rename, or remove columns. "
        f"The columns **{', '.join(catalog.CORE_COLUMNS)}** drive scoring and can't be removed or renamed."
    )

    # Keep a working copy in session so column add/remove survives reruns.
    if "catalog_df" not in st.session_state:
        st.session_state["catalog_df"] = catalog.load_catalog()
    df = st.session_state["catalog_df"]

    editor_key = "cat_editor_" + "|".join(map(str, df.columns))
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key=editor_key,
        column_config={
            "description": st.column_config.TextColumn("description", width="large"),
        },
    )
    # Persist in-table edits immediately so they aren't lost on rerun.
    st.session_state["catalog_df"] = edited

    st.markdown("**Import scenarios from CSV**")
    with st.expander("📥 Load scenarios from CSV file"):
        csv_file = st.file_uploader("Choose a CSV file", type=["csv"], key="csv_import")
        if csv_file is not None:
            try:
                imported_df = pd.read_csv(csv_file)
                # Validate that core columns exist
                missing = [c for c in catalog.CORE_COLUMNS if c not in imported_df.columns]
                if missing:
                    st.error(f"CSV is missing required columns: {', '.join(missing)}")
                else:
                    # Merge imported data with existing
                    merged = edited.copy()
                    for idx, row in imported_df.iterrows():
                        num_val = row.get('num')
                        if num_val is not None and not pd.isna(num_val):
                            # Check if scenario already exists
                            existing_mask = merged['num'] == num_val
                            if existing_mask.any():
                                row_idx = merged[existing_mask].index[0]
                                # Update cells with imported values
                                for col in imported_df.columns:
                                    if col in merged.columns and pd.notna(row[col]):
                                        merged.at[row_idx, col] = row[col]
                            else:
                                # Add new row
                                merged = pd.concat([merged, pd.DataFrame([row])], ignore_index=True)
                    st.session_state["catalog_df"] = merged
                    st.success(f"Imported {len(imported_df)} scenarios. Review and save below.")
                    st.rerun()
            except Exception as e:
                st.error(f"Error reading CSV: {e}")

    st.markdown("**Columns**")
    cc1, cc2, cc3 = st.columns(3)
    with cc1:
        new_col = st.text_input("Add a column", placeholder="e.g. build_status")
        if st.button("➕ Add column") and new_col.strip():
            name = new_col.strip()
            if name in edited.columns:
                st.warning(f"Column '{name}' already exists.")
            else:
                edited[name] = ""
                st.session_state["catalog_df"] = edited
                st.rerun()
    with cc2:
        renamable = [c for c in edited.columns if c not in catalog.CORE_COLUMNS]
        if renamable:
            col_to_rename = st.selectbox("Rename a column", renamable, key="col_rename_sel")
            new_col_name = st.text_input(
                "New name", placeholder="Enter new column name", key="col_rename_input"
            )
            if st.button("✏️ Rename column") and new_col_name.strip():
                name = new_col_name.strip()
                if name == col_to_rename:
                    st.warning("New name is the same as the current name.")
                elif name in edited.columns:
                    st.warning(f"Column '{name}' already exists.")
                else:
                    st.session_state["catalog_df"] = edited.rename(columns={col_to_rename: name})
                    st.rerun()
        else:
            st.caption("No renamable columns (all are core columns).")
    with cc3:
        removable = [c for c in edited.columns if c not in catalog.CORE_COLUMNS]
        if removable:
            col_to_remove = st.selectbox("Remove a column", removable)
            if st.button("🗑️ Remove column"):
                st.session_state["catalog_df"] = edited.drop(columns=[col_to_remove])
                st.rerun()
        else:
            st.caption("No removable columns.")

    st.divider()
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("💾 Save catalog", type="primary"):
            errors = _validate_catalog(edited)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                catalog.save_catalog(edited)
                scoring.sync_scenarios(catalog.core_scenarios())
                st.success("Catalog saved and scoring synced.")
    with b2:
        if st.button("↩️ Reload from file"):
            st.session_state["catalog_df"] = catalog.load_catalog()
            st.rerun()
    with b3:
        st.download_button(
            "⬇️ Export catalog (CSV)",
            data=edited.to_csv(index=False).encode("utf-8"),
            file_name="scenarios_catalog.csv",
            mime="text/csv",
        )


def _validate_catalog(df: pd.DataFrame) -> list[str]:
    errors = []
    for col in ("num", "title"):
        if col not in df.columns:
            errors.append(f"Required column '{col}' is missing.")
    if "num" in df.columns:
        nums = pd.to_numeric(df["num"], errors="coerce")
        if nums.isna().any():
            errors.append("Every row needs a numeric 'num'. Some rows are blank/non-numeric.")
        elif nums.duplicated().any():
            dupes = sorted(nums[nums.duplicated()].astype(int).unique().tolist())
            errors.append(f"Duplicate scenario numbers: {dupes}. Each 'num' must be unique.")
    return errors


# --------------------------------------------------------------------------- #
# Setup (trainers only)
# --------------------------------------------------------------------------- #
def tab_setup() -> None:
    st.header("⚙️ Setup")

    st.subheader("Teams")
    teams = scoring.get_teams()
    st.dataframe(teams.rename(columns={"id": "ID", "name": "Name"}),
                 hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        new_name = st.text_input("Add a team", placeholder="e.g. Team Red")
        if st.button("➕ Add team") and new_name.strip():
            scoring.add_team(new_name)
            st.rerun()
    with c2:
        if not teams.empty:
            del_id = st.selectbox(
                "Remove a team", options=teams["id"].tolist(),
                format_func=lambda i: teams.set_index("id").loc[i, "name"],
            )
            if st.button("🗑️ Delete team"):
                scoring.delete_team(del_id)
                st.rerun()

    st.divider()
    st.subheader("Dashboard title")
    current_title = scoring.get_dashboard_title()
    new_title = st.text_input(
        "Customize the leaderboard heading",
        value=current_title,
        placeholder="e.g. SPARK PERFORM Week — Live Leaderboard",
        help="This title appears at the top of the leaderboard for all viewers."
    )
    if st.button("Update title"):
        scoring.set_dashboard_title(new_title)
        st.success("Dashboard title updated. Refresh the leaderboard to see it.")
        st.rerun()

    st.divider()
    st.subheader("Sidebar branding")
    sb_title = scoring.get_sidebar_title()
    sb_subtitle = scoring.get_sidebar_subtitle()
    new_sb_title = st.text_input(
        "Sidebar main title",
        value=sb_title,
        placeholder="e.g. SPARK Scoreboard",
        help="Appears at the top of the sidebar for all viewers."
    )
    new_sb_subtitle = st.text_input(
        "Sidebar subtitle",
        value=sb_subtitle,
        placeholder="e.g. PERFORM Week · Module 3",
        help="Appears below the main title."
    )
    if st.button("Update sidebar branding"):
        scoring.set_sidebar_title(new_sb_title)
        scoring.set_sidebar_subtitle(new_sb_subtitle)
        st.success("Sidebar branding updated. Refresh to see changes.")
        st.rerun()

    st.divider()
    st.subheader("Speed bonus")
    st.markdown(scoring.speed_bonus_explanation())
    current = scoring.get_time_bonus_table()
    bonus_text = st.text_input(
        "Bonus points by finishing position (comma-separated)",
        value=", ".join(str(v) for v in current),
        help="Example: 5, 3, 2, 1 means +5 fastest, +3 second, +2 third, +1 fourth.",
    )
    if st.button("Save speed bonus"):
        try:
            values = [int(x.strip()) for x in bonus_text.split(",") if x.strip()]
            scoring.set_time_bonus_table(values)
            st.success(f"Speed bonus saved: {values}")
            st.rerun()
        except ValueError:
            st.error("Please enter whole numbers separated by commas.")

    st.divider()
    st.subheader("Trainer password")
    with st.form("pwd_form"):
        np1 = st.text_input("New password", type="password")
        np2 = st.text_input("Confirm password", type="password")
        if st.form_submit_button("Update password"):
            if not np1:
                st.error("Password can't be empty.")
            elif np1 != np2:
                st.error("Passwords don't match.")
            else:
                scoring.set_setting("trainer_password", np1)
                st.success("Trainer password updated.")

    st.divider()
    st.subheader("Data")
    all_scores = scoring.get_all_scores()
    lb = scoring.build_leaderboard()
    score_log = scoring.get_score_log()
    
    d1, d2, d3 = st.columns(3)
    d1.download_button(
        "⬇️ Download all scores (CSV)",
        data=all_scores.to_csv(index=False).encode("utf-8"),
        file_name="spark_scores.csv", mime="text/csv", disabled=all_scores.empty,
    )
    d2.download_button(
        "⬇️ Download leaderboard (CSV)",
        data=lb.to_csv(index=False).encode("utf-8"),
        file_name="spark_leaderboard.csv", mime="text/csv", disabled=lb.empty,
    )
    d3.download_button(
        "⬇️ Export score entry log (CSV)",
        data=score_log.to_csv(index=False).encode("utf-8"),
        file_name="spark_score_entry_log.csv", mime="text/csv", disabled=score_log.empty,
    )
    
    if not score_log.empty:
        with st.expander("📋 View score entry audit log"):
            st.dataframe(score_log, hide_index=True, use_container_width=True)

    with st.expander("⚠️ Danger zone — reset all scores"):
        st.write("This permanently clears every recorded score. Teams are kept.")
        confirm = st.text_input("Type RESET to confirm")
        if st.button("Reset all scores", type="primary", disabled=confirm != "RESET"):
            scoring.reset_all_scores()
            st.success("All scores cleared.")
            st.rerun()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
is_trainer = trainer_gate()

if is_trainer:
    tabs = st.tabs(["🏆 Leaderboard", "📝 Score Entry", "📋 Scenarios", "⚙️ Setup"])
    with tabs[0]:
        tab_leaderboard()
    with tabs[1]:
        tab_score_entry()
    with tabs[2]:
        tab_scenarios(can_edit=True)
    with tabs[3]:
        tab_setup()
else:
    tabs = st.tabs(["🏆 Leaderboard", "📋 Scenarios"])
    with tabs[0]:
        tab_leaderboard()
    with tabs[1]:
        tab_scenarios(can_edit=False)
