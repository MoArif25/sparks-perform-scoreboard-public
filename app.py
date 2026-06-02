"""
SPARKS Module 3 - PERFORM Week live scoreboard.

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

import pandas as pd
import streamlit as st

import catalog
import scoring

st.set_page_config(
    page_title="SPARKS PERFORM Week Scoreboard",
    page_icon="⚡",
    layout="wide",
)

# --- One-time startup: init DB, seed teams, sync catalog -> scoring DB -------- #
scoring.init_db()
scoring.ensure_default_teams(10)
scoring.sync_scenarios(catalog.core_scenarios())

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
DEFAULT_TRAINER_PASSWORD = "sparks2026"


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #
def trainer_gate() -> bool:
    """Render the sidebar trainer login; return True if unlocked."""
    st.sidebar.title(scoring.get_sidebar_title())
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
    cols[2].metric("Total points awarded", f'{lb["total_points"].sum():.0f}')

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
    st.header(scoring.get_dashboard_title())
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
        scoring.upsert_score(
            team_id=team_id, scenario_num=int(scen_num), status=status,
            points=points, minutes=None if minutes == 0 else minutes,
            passed=passed, notes=notes,
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
        "Fully editable. Add or delete **rows** with the +/🗑 controls in the table, "
        "edit any cell, and add or remove **columns** below. "
        f"The columns **{', '.join(catalog.CORE_COLUMNS)}** drive scoring and can't be removed."
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

    st.markdown("**Columns**")
    cc1, cc2 = st.columns(2)
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
        placeholder="e.g. ⚡ SPARKS PERFORM Week — Live Leaderboard",
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
        placeholder="e.g. ⚡ SPARKS Scoreboard",
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
    d1, d2 = st.columns(2)
    d1.download_button(
        "⬇️ Download all scores (CSV)",
        data=all_scores.to_csv(index=False).encode("utf-8"),
        file_name="sparks_scores.csv", mime="text/csv", disabled=all_scores.empty,
    )
    d2.download_button(
        "⬇️ Download leaderboard (CSV)",
        data=lb.to_csv(index=False).encode("utf-8"),
        file_name="sparks_leaderboard.csv", mime="text/csv", disabled=lb.empty,
    )

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
