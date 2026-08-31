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
import hashlib
import io
import zipfile
from html import escape
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
_startup_error = None
try:
    if scoring.backend_name() != "pg":
        raise RuntimeError(
            "Postgres backend is required. Set Streamlit secret DATABASE_URL "
            "to your Supabase session pooler connection string."
        )
    scoring.init_db()
    scoring.ensure_default_teams(10)
    scoring.sync_scenarios(catalog.core_scenarios())
except Exception as exc:
    _startup_error = str(exc)

if _startup_error:
    st.error("Database startup failed. The app is running, but cannot connect to Postgres.")
    st.code(_startup_error)
    st.markdown(
        "Set one of these Streamlit Cloud secret formats:\n"
        "- `DATABASE_URL` (Supabase session pooler URL recommended)\n"
        "- or field-based keys: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`\n"
        "- `SUPABASE_POOLER_REGION` is only needed when `DATABASE_URL` uses direct `db.<ref>.supabase.co`"
    )
    with st.expander("🔎 Secret detection diagnostics (safe — shows key names only)"):
        st.json(scoring.secret_diagnostics())
    st.stop()

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
        st.sidebar.success(f"Trainer / Core team mode — {st.session_state.get('trainer_name', 'Unknown')}")
        if st.sidebar.button("🔒 Log out"):
            st.session_state["is_trainer"] = False
            st.session_state.pop("trainer_name", None)
            st.rerun()
        return True

    st.sidebar.info("👀 Viewing the public leaderboard.")
    with st.sidebar.expander("🔑 Trainer / Core team login"):
        name = st.text_input("Your name", key="trainer_name_input",
                             placeholder="Used in the score audit log")
        pwd = st.text_input("Password", type="password", key="pwd_input")
        if st.button("Unlock"):
            real = scoring.get_setting("trainer_password", DEFAULT_TRAINER_PASSWORD)
            if not name.strip():
                st.error("Please enter your name.")
            elif pwd == real:
                st.session_state["is_trainer"] = True
                st.session_state["trainer_name"] = name.strip()
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


# --------------------------------------------------------------------------- #
# Leaderboard
# --------------------------------------------------------------------------- #
REFRESH_SECONDS = 15


@st.cache_data(ttl=REFRESH_SECONDS - 1, show_spinner=False)
def _cached_leaderboard():
    """Cache leaderboard reads so live refreshes don't hit the database on
    every redraw. TTL sits just under the refresh interval so each cycle makes
    at most one round trip. Cleared immediately when a score is saved."""
    return scoring.build_leaderboard()


def _standings_html(lb: pd.DataFrame) -> str:
    """Render the whole live block as one HTML string.

    Streamlit's dataframe/metric widgets are React components that remount on
    every fragment rerun, which is what causes the visible flicker on a
    projector. A single markdown block redraws without remounting.
    """
    leader = lb.iloc[0]
    scoring_teams = int((lb["total_points"] > 0).sum())
    top = float(max(lb["total_points"].max(), 1))

    cards = [
        ("🏆 Leader", escape(str(leader["team"])), f'{leader["total_points"]:.0f} pts'),
        ("Teams scoring", str(scoring_teams), ""),
        ("Leading team points", f'{leader["total_points"]:.0f}', ""),
    ]
    card_html = "".join(
        '<div style="flex:1;min-width:140px;padding:0.6rem 0.9rem;border:1px solid '
        'rgba(128,128,128,0.25);border-radius:0.5rem;">'
        f'<div style="font-size:0.8rem;opacity:0.7;">{label}</div>'
        f'<div style="font-size:1.5rem;font-weight:700;line-height:1.25;">{value}</div>'
        f'<div style="font-size:0.85rem;opacity:0.75;">{sub}</div>'
        "</div>"
        for label, value, sub in cards
    )

    rows = []
    for _, r in lb.iterrows():
        rank = int(r["rank"])
        medal = MEDALS.get(rank, "")
        pct = max(2.0, float(r["total_points"]) / top * 100.0)
        rows.append(
            "<tr>"
            f'<td style="padding:0.35rem 0.5rem;white-space:nowrap;">{medal} {rank}</td>'
            f'<td style="padding:0.35rem 0.5rem;font-weight:600;">{escape(str(r["team"]))}</td>'
            f'<td style="padding:0.35rem 0.5rem;text-align:right;font-weight:700;">{r["total_points"]:.0f}</td>'
            '<td style="padding:0.35rem 0.5rem;width:45%;">'
            '<div style="background:rgba(128,128,128,0.18);border-radius:0.35rem;height:0.65rem;">'
            f'<div style="width:{pct:.1f}%;background:#21c354;height:100%;border-radius:0.35rem;"></div>'
            "</div></td>"
            f'<td style="padding:0.35rem 0.5rem;text-align:right;">{int(r["scenarios_completed"])}</td>'
            "</tr>"
        )

    headers = ["Rank", "Team", "Points", "", "Done"]
    header_html = "".join(
        '<th style="padding:0.4rem 0.5rem;text-align:left;font-size:0.8rem;'
        f'opacity:0.7;border-bottom:1px solid rgba(128,128,128,0.3);">{h}</th>'
        for h in headers
    )

    return (
        f'<div style="display:flex;gap:0.75rem;flex-wrap:wrap;margin-bottom:1rem;">{card_html}</div>'
        '<div style="font-size:1.35rem;font-weight:600;margin:0.3rem 0 0.5rem;">Standings</div>'
        '<table style="width:100%;border-collapse:collapse;">'
        f"<thead><tr>{header_html}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def render_leaderboard_body() -> None:
    """Lightweight live part: metrics + standings, drawn as a single HTML
    block so the auto-refresh doesn't remount widgets and flicker."""
    lb = _cached_leaderboard()

    if lb.empty:
        st.info("No teams yet.")
        return

    st.markdown(_standings_html(lb), unsafe_allow_html=True)


def render_leaderboard_extras() -> None:
    """Static part: chart + explanation. Rendered once, not auto-refreshed,
    so it doesn't flicker every refresh cycle."""
    lb = _cached_leaderboard()
    if lb.empty:
        return

    st.subheader("Points by team")
    chart_df = lb.set_index("team")[["total_points"]].rename(
        columns={"total_points": "Points"}
    )
    st.bar_chart(chart_df, color="#1f77b4")

    with st.expander("ℹ️ How scoring works"):
        st.markdown(
            "- A team's **Points** are the reviewer points awarded across every "
            "scenario.\n"
            "- Teams work through scenarios in their own order and at their own "
            "pace, so nothing is timed and there is no speed bonus.\n"
            "- **Done** counts scenarios that have been reviewed.\n"
            "- Equal points are broken by scenarios completed, then team name."
        )


@st.fragment(run_every=REFRESH_SECONDS)
def live_leaderboard() -> None:
    st.caption(f"🔴 Live · refreshes every {REFRESH_SECONDS} seconds")
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

    # Static chart + explanation render once (outside the auto-refresh loop)
    # so they don't flicker on every refresh cycle.
    render_leaderboard_extras()


# --------------------------------------------------------------------------- #
# Score entry (trainers only)
# --------------------------------------------------------------------------- #
def tab_score_entry() -> None:
    st.header("📝 Score Entry (Trainers)")
    teams = scoring.get_teams()
    scen = _catalog_scenarios()

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
    scoring_text = scen_lookup.loc[scen_num, "scoring"]

    caption = f"Max reviewer points: **{max_points}**"
    if pd.notna(scoring_text) and str(scoring_text).strip() not in ("", "TBD"):
        caption += f" · Scoring: *{scoring_text}*"
    st.caption(caption)

    existing = scoring.get_score(team_id, int(scen_num)) or {}
    with st.form("score_form", clear_on_submit=False):
        fc1, fc2 = st.columns(2)
        status = fc1.selectbox(
            "Status", scoring.STATUS_OPTIONS,
            index=scoring.STATUS_OPTIONS.index(existing.get("status", "Not Started")),
        )
        passed = fc2.checkbox(
            "Solution passed",
            value=bool(existing.get("passed", 0)),
        )
        points = fc1.number_input(
            "Reviewer points", min_value=0, max_value=max_points,
            value=min(int(existing.get("points", 0) or 0), max_points), step=1,
        )
        notes = st.text_area("Notes (optional)", value=existing.get("notes", "") or "")
        submitted = st.form_submit_button("💾 Save score", type="primary")

    if submitted:
        trainer_name = st.session_state.get("trainer_name", "Unknown")
        team_name = teams.set_index("id").loc[team_id, "name"]

        # Preserve any historical time rather than clearing it on every save.
        minutes = existing.get("minutes")

        scoring.upsert_score(
            team_id=team_id, scenario_num=int(scen_num), status=status,
            points=points, minutes=minutes,
            passed=passed, notes=notes,
        )
        scoring.log_score_entry(
            team_id=team_id, team_name=team_name, scenario_num=int(scen_num),
            status=status, points=points, minutes=minutes,
            passed=passed, notes=notes, trainer_name=trainer_name,
        )
        _cached_leaderboard.clear()
        st.success("Saved. The leaderboard updates on its next refresh.")

    st.subheader("This scenario — all teams (score snapshot)")
    st.caption("This table is filtered by the currently selected scenario and is not the audit log.")
    all_scores = scoring.get_all_scores()
    this_scen = all_scores[all_scores["scenario_num"] == scen_num]
    if this_scen.empty:
        st.caption("No scores recorded for this scenario yet.")
    else:
        view = this_scen[["team", "status", "points", "passed", "notes"]].rename(
            columns={"team": "Team", "status": "Status", "points": "Points",
                     "passed": "Passed", "notes": "Notes"}
        )
        view["Passed"] = view["Passed"].map({1: "✅", 0: ""})
        st.dataframe(view, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Score entry audit log — all scenarios")
    score_log = scoring.get_score_log()
    if score_log.empty:
        st.caption("No score entry logs recorded yet.")
    else:
        st.caption(f"Showing {len(score_log)} log entries across all scenarios.")
        st.dataframe(score_log, hide_index=True, use_container_width=True)
        st.download_button(
            "⬇️ Export full audit log (CSV)",
            data=score_log.to_csv(index=False).encode("utf-8"),
            file_name="spark_score_entry_log.csv",
            mime="text/csv",
        )


# --------------------------------------------------------------------------- #
# Team submission form (public)
# --------------------------------------------------------------------------- #
def _parse_options(raw: str | None) -> list[str]:
    if not raw:
        return []
    separator = "|" if "|" in raw else "\n"
    return [o.strip() for o in str(raw).split(separator) if o.strip()]


@st.cache_data(ttl=60, show_spinner=False)
def _catalog_scenarios() -> pd.DataFrame:
    """Shared across sessions, so many teams submitting at once don't each
    re-read the catalog from Postgres on every rerun. Cleared on catalog save."""
    return catalog.core_scenarios()


@st.cache_data(ttl=60, show_spinner=False)
def _scenario_items(scenario_num: int) -> pd.DataFrame:
    return scoring.get_scenario_items(scenario_num)


def _render_item(item: pd.Series, key_prefix: str) -> dict:
    """Render one sub-scenario question and return its answer row."""
    label = str(item["label"])
    item_type = str(item["item_type"] or "text")
    key = f"{key_prefix}_item_{int(item['id'])}"
    required = bool(item["required"])
    display = f"{label} *" if required else label
    if pd.notna(item["max_points"]):
        display += f"  ·  {float(item['max_points']):g} pts"

    answer = {"item_id": int(item["id"]), "label": label, "required": required,
              "answer_text": None, "answer_number": None}

    if item_type == "long_text":
        answer["answer_text"] = st.text_area(display, key=key)
    elif item_type == "number":
        answer["answer_number"] = st.number_input(display, value=0.0, step=1.0, key=key)
    elif item_type == "choice":
        options = _parse_options(item["options"])
        if options:
            answer["answer_text"] = st.radio(display, options, key=key)
        else:
            answer["answer_text"] = st.text_input(display, key=key)
    elif item_type == "multi_choice":
        options = _parse_options(item["options"])
        picked = st.multiselect(display, options, key=key) if options else []
        answer["answer_text"] = ", ".join(picked)
    elif item_type == "checkbox":
        answer["answer_text"] = "Yes" if st.checkbox(display, key=key) else "No"
    else:
        answer["answer_text"] = st.text_input(display, key=key)

    return answer


def _render_scenario_block(scen_num: int, part: int, title: str,
                           max_points: int) -> dict:
    """Render one part of one scenario and collect its submission payload."""
    prefix = f"s{scen_num}_p{part}"

    part_label = st.text_input(
        "Which part / sub-scenario is this? (optional)",
        key=f"{prefix}_part",
        placeholder="e.g. PLC code, Simulation, Fault 3 — blank means the whole scenario",
    )

    items = _scenario_items(scen_num)
    answers: list[dict] = []

    if items.empty:
        summary = st.text_area(
            "What did your team do?", height=90, key=f"{prefix}_summary",
            placeholder="Describe your approach, what you configured, and the result",
        )
    else:
        for _, item in items.iterrows():
            answers.append(_render_item(item, prefix))
        summary = st.text_area(
            "Anything else the reviewer should know? (optional)",
            key=f"{prefix}_summary",
        )

    uploads = st.file_uploader(
        f"Evidence (optional) — max {scoring.MAX_FILES_PER_SUBMISSION} files, "
        f"{scoring.MAX_FILE_BYTES // (1024 * 1024)} MB each",
        accept_multiple_files=True, key=f"{prefix}_files",
    )

    c1, c2 = st.columns(2)
    self_completed = c1.checkbox("Completed in full", key=f"{prefix}_done")
    self_points = c2.number_input(
        "Points you believe you earned", min_value=0, max_value=max_points,
        value=0, step=1, key=f"{prefix}_points",
        help="A trainer confirms or adjusts this during review.",
    )

    return {
        "scenario_num": scen_num, "part": part, "title": title,
        "answers": answers, "summary": summary, "uploads": uploads or [],
        "part_label": part_label, "self_completed": self_completed,
        "self_points": float(self_points), "has_items": not items.empty,
    }


def _block_is_blank(block: dict) -> bool:
    """An untouched optional part, which should simply be ignored."""
    return (
        not str(block["summary"] or "").strip()
        and not block["uploads"]
        and not str(block["part_label"] or "").strip()
        and not block["self_completed"]
        and not block["self_points"]
        and not any(
            str(a["answer_text"] or "").strip() or a["answer_number"]
            for a in block["answers"]
        )
    )


def _validate_block(block: dict) -> str | None:
    """Return an error message for one scenario block, or None if valid."""
    label = f'#{block["scenario_num"]} {block["title"]}'
    if block["part"]:
        label += f' (extra part {block["part"]})'

    if len(block["uploads"]) > scoring.MAX_FILES_PER_SUBMISSION:
        return f'{label}: attach at most {scoring.MAX_FILES_PER_SUBMISSION} files.'

    for upload in block["uploads"]:
        if len(upload.getvalue()) > scoring.MAX_FILE_BYTES:
            return (
                f'{label}: "{upload.name}" is '
                f'{len(upload.getvalue()) / (1024 * 1024):.1f} MB — the limit is '
                f'{scoring.MAX_FILE_BYTES // (1024 * 1024)} MB per file.'
            )

    missing = [
        a["label"] for a in block["answers"]
        if a["required"] and not str(a["answer_text"] or "").strip()
        and a["answer_number"] is None
    ]
    if missing:
        return f'{label}: answer the required questions — {", ".join(missing)}.'

    if not block["has_items"] and not str(block["summary"] or "").strip():
        return f'{label}: describe what your team did before submitting.'

    return None


def tab_submit() -> None:
    st.header("📤 Submit Your Work")
    teams = scoring.get_teams()
    scen = _catalog_scenarios()

    if teams.empty:
        st.warning("No teams have been set up yet.")
        return
    if scen.empty:
        st.warning("No scenarios have been set up yet.")
        return

    scen_lookup = scen.set_index("num")
    team_id = int(st.selectbox(
        "Your team",
        options=teams["id"].tolist(),
        format_func=lambda i: teams.set_index("id").loc[i, "name"],
        key="submit_team",
    ))

    attempts = scoring.get_team_attempts(team_id)
    if not attempts.empty:
        with st.expander(f"📋 Your team's previous attempts ({len(attempts)})"):
            st.dataframe(
                attempts.rename(columns={
                    "scenario_num": "#", "scenario": "Scenario",
                    "attempt_no": "Attempt", "part_label": "Part",
                    "status": "Status", "final_points": "Awarded",
                    "submitted_at": "Submitted"}),
                hide_index=True, use_container_width=True,
            )

    picked = st.multiselect(
        "Scenarios to submit",
        options=[int(n) for n in scen["num"].tolist()],
        format_func=lambda n: f'#{n} — {scen_lookup.loc[n, "title"]}',
        key="submit_scenarios",
        help="Pick as many as you like. You can submit the same scenario again later.",
    )

    if not picked:
        st.info("Select one or more scenarios above to start.")
        return

    st.caption(
        "Each scenario opens with tabs for its parts, for when one scenario is "
        "split into sub-scenarios. Fill in only the parts you need — empty ones "
        "are ignored. A trainer can accept several parts and the points add up."
    )

    # Outside the form so raising it takes effect immediately; inside the form
    # it could not change the number of rendered tabs.
    part_slots = int(st.number_input(
        "Parts per scenario", min_value=1, max_value=25, value=1, step=1,
        key="submit_part_slots",
        help="Only raise this if a scenario is split into sub-scenarios. Each "
             "extra part makes the form longer.",
    ))

    with st.form("submit_form", clear_on_submit=False):
        submitted_by = st.text_input("Submitted by (optional)",
                                     placeholder="Your name")
        # Also at the top: with several scenarios open the form runs to a few
        # screens, and the button at the bottom is easy to miss. The labels must
        # differ - Streamlit derives a form submit button's id from its label,
        # and two identical ones collide.
        send_top = st.form_submit_button("📤 Submit", type="primary")
        st.divider()

        blocks = []
        for n in picked:
            title = str(scen_lookup.loc[n, "title"])
            max_points = int(scen_lookup.loc[n, "max_points"])
            scoring_text = scen_lookup.loc[n, "scoring"]

            st.markdown(f"##### #{n} — {title}")
            caption = f"Max points: **{max_points}** for all parts combined"
            if pd.notna(scoring_text) and str(scoring_text).strip() not in ("", "TBD"):
                caption += f" · Scoring: *{scoring_text}*"
            st.caption(caption)

            if part_slots == 1:
                blocks.append(_render_scenario_block(n, 0, title, max_points))
            else:
                # Tabs keep the page short however many parts are open, which
                # stacked expanders did not.
                tabs = st.tabs([f"Part {p + 1}" for p in range(part_slots)])
                for p, tab in enumerate(tabs):
                    with tab:
                        blocks.append(
                            _render_scenario_block(n, p, title, max_points))
            st.divider()

        send_bottom = st.form_submit_button("📤 Submit all entries",
                                            type="primary")

    send = send_top or send_bottom

    if not send:
        return

    filled = [b for b in blocks if not _block_is_blank(b)]
    if not filled:
        st.error("Nothing to submit — fill in at least one part.")
        return

    started = {b["scenario_num"] for b in filled}
    if missing := [n for n in picked if n not in started]:
        st.error(
            "Nothing filled in for " + ", ".join(f"#{n}" for n in missing)
            + ". Complete it, or deselect it above."
        )
        return

    # Validate everything before writing, so a partial batch never lands.
    if errors := [msg for msg in (_validate_block(b) for b in filled) if msg]:
        for msg in errors:
            st.error(msg)
        return

    saved, failed = [], []
    for block in filled:
        files = [
            {"filename": u.name, "mime_type": u.type, "content": u.getvalue()}
            for u in block["uploads"]
        ]
        try:
            scoring.save_submission(
                team_id=team_id,
                scenario_num=block["scenario_num"],
                summary=block["summary"],
                self_completed=block["self_completed"],
                self_points=block["self_points"],
                submitted_by=submitted_by.strip() or None,
                answers=block["answers"],
                files=files,
                part_label=(block["part_label"] or "").strip() or None,
            )
            saved.append(f'#{block["scenario_num"]}')
        except Exception as exc:
            failed.append(f'#{block["scenario_num"]}: {exc}')

    if saved:
        st.success(
            f"✅ Submitted {len(saved)} entr{'ies' if len(saved) > 1 else 'y'} "
            f"({', '.join(saved)}) — pending trainer review."
        )
        _pending_count.clear()
        st.balloons()
    for msg in failed:
        st.error(msg)


# --------------------------------------------------------------------------- #
# Submissions inbox (trainers only)
# --------------------------------------------------------------------------- #
def _evidence_zip() -> bytes:
    """Bundle every uploaded file into one archive, foldered by submission."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for f in scoring.iter_all_files():
            safe_team = str(f["team"]).replace("/", "-").replace("\\", "-")
            folder = f'sub{f["submission_id"]}_{safe_team}_scenario{f["scenario_num"]}'
            archive.writestr(f'{folder}/{f["filename"]}', f["content"])
    return buffer.getvalue()


def _render_submission_archive() -> None:
    usage = scoring.storage_usage()
    with st.expander("💾 Where this data lives, and exporting it"):
        st.markdown(
            "Everything teams submit is written to **Supabase Postgres** — the same "
            "database as the scores. Nothing is kept on the Streamlit server, whose "
            "disk is wiped on every restart.\n\n"
            "| Data | Table |\n|---|---|\n"
            "| Submission header, summary, self-reported points | `submissions` |\n"
            "| Answers to sub-scenario questions | `submission_answers` |\n"
            "| Uploaded evidence files (binary) | `submission_files` |\n\n"
            "**Retention: indefinite.** There is no expiry job — rows stay until "
            "someone deletes them here or in Supabase. Note that *Reset all scores* "
            "in Setup clears scores only and deliberately leaves submissions intact."
        )
        u1, u2, u3 = st.columns(3)
        u1.metric("Submissions", usage["submissions"])
        u2.metric("Files", usage["files"])
        u3.metric("Evidence size", f'{usage["bytes"] / (1024 * 1024):.1f} MB')

        if usage["bytes"] > 300 * 1024 * 1024:
            st.warning(
                "Evidence is approaching the Supabase free-tier database limit. "
                "Export and purge, or move files to Supabase Storage."
            )

        e1, e2, e3 = st.columns(3)
        e1.download_button(
            "⬇️ Submissions (CSV)",
            data=scoring.export_submissions().to_csv(index=False).encode("utf-8"),
            file_name="spark_submissions.csv", mime="text/csv",
            disabled=usage["submissions"] == 0,
        )
        e2.download_button(
            "⬇️ Answers (CSV)",
            data=scoring.export_submission_answers().to_csv(index=False).encode("utf-8"),
            file_name="spark_submission_answers.csv", mime="text/csv",
            disabled=usage["answers"] == 0,
        )
        with e3:
            if usage["files"] == 0:
                st.button("⬇️ Evidence (ZIP)", disabled=True)
            elif st.session_state.get("evidence_zip_ready"):
                st.download_button(
                    "⬇️ Evidence (ZIP)", data=st.session_state["evidence_zip"],
                    file_name="spark_evidence.zip", mime="application/zip",
                )
            elif st.button("📦 Build evidence ZIP"):
                st.session_state["evidence_zip"] = _evidence_zip()
                st.session_state["evidence_zip_ready"] = True
                st.rerun()

        st.caption(
            "Take these exports at the end of each event — they are your backup "
            "and the only copy if the database is later purged."
        )


@st.cache_data(ttl=10, show_spinner=False)
def _pending_count() -> int:
    return scoring.count_pending_submissions()


@st.fragment(run_every=20)
def _pending_watch() -> None:
    """Poll only the pending count.

    The submissions list itself is deliberately left static: it contains a
    data_editor, and rerunning that on a timer would discard awards a trainer
    is part-way through typing.
    """
    n = _pending_count()
    if n:
        st.warning(
            f"🔔 **{n}** submission(s) awaiting review. "
            "Press **Refresh list** to load the latest."
        )
    else:
        st.success("✅ Nothing awaiting review.")


def _render_bulk_review(view: pd.DataFrame, subs: pd.DataFrame) -> None:
    """Award and accept many attempts in one pass."""
    st.subheader("Bulk review")
    st.caption(
        "A scenario's score is the **sum of every accepted attempt**, so a team "
        "can submit one scenario several times — one per sub-scenario — and you "
        "can accept as many as make sense. **Scenario total** shows what the "
        "scenario will score once the ticked rows are posted."
    )

    accepted_now = scoring.get_accepted_totals()
    grid = view[["id", "team_id", "team", "scenario_num", "scenario",
                 "part_label", "self_points", "max_points",
                 "attempt_no", "final_points", "status"]].copy()
    grid["Accepted so far"] = [
        accepted_now.get((int(t), int(s)), 0.0)
        for t, s in zip(grid["team_id"], grid["scenario_num"])
    ]
    grid["Award"] = (
        grid["self_points"].fillna(0).astype(float)
        .combine(grid["max_points"].astype(float), min)
    )
    grid["Accept"] = False

    display = grid[["id", "team", "scenario_num", "scenario", "attempt_no",
                    "part_label", "max_points", "Accepted so far", "Award",
                    "Accept"]].rename(
        columns={"id": "ID", "team": "Team", "scenario_num": "#",
                 "scenario": "Scenario", "attempt_no": "Att",
                 "part_label": "Part", "max_points": "Max"})

    edited = st.data_editor(
        display,
        hide_index=True,
        use_container_width=True,
        key="bulk_review_editor",
        disabled=["ID", "Team", "#", "Scenario", "Att", "Part", "Max",
                  "Accepted so far"],
        column_config={
            "Award": st.column_config.NumberColumn("Award", min_value=0, step=1),
            "Accept": st.column_config.CheckboxColumn("Accept"),
        },
    )

    chosen = edited[edited["Accept"]].copy()
    if chosen.empty:
        st.caption("Tick **Accept** on the rows you want to post.")
        return

    # An already-accepted attempt is re-scored rather than added twice, so its
    # previous contribution must come out of the running total first.
    prior = grid.set_index("id")
    chosen["_replaces"] = [
        float(prior.loc[i, "final_points"] or 0)
        if prior.loc[i, "status"] == "accepted" else 0.0
        for i in chosen["ID"]
    ]

    totals = []
    for (team, num), rows in chosen.groupby(["Team", "#"]):
        base = float(rows["Accepted so far"].iloc[0]) - float(rows["_replaces"].sum())
        totals.append({
            "Team": team, "#": int(num),
            "Max": float(rows["Max"].iloc[0]),
            "Attempts posted": len(rows),
            "Scenario total": base + float(rows["Award"].sum()),
            "Was": float(rows["Accepted so far"].iloc[0]),
        })
    summary = pd.DataFrame(totals)
    summary["Δ Total"] = summary["Scenario total"] - summary["Was"]

    st.markdown("**Resulting scenario scores**")
    st.dataframe(
        summary[["Team", "#", "Attempts posted", "Was", "Scenario total",
                 "Δ Total", "Max"]],
        hide_index=True, use_container_width=True,
    )

    over = summary[summary["Scenario total"] > summary["Max"]]
    if not over.empty:
        for _, r in over.iterrows():
            st.error(
                f'{r["Team"]} scenario #{int(r["#"])}: the accepted attempts add '
                f'up to {r["Scenario total"]:.0f}, above the scenario max of '
                f'{r["Max"]:.0f}. Lower one of the awards by '
                f'{r["Scenario total"] - r["Max"]:.0f}.'
            )
        return

    st.info(
        f"**{len(chosen)}** attempt(s) across **{len(summary)}** scenario(s) · "
        f"net change to leaderboard points: **{summary['Δ Total'].sum():+.0f}**"
    )

    notes = st.text_input("Review note applied to all selected (optional)",
                          key="bulk_notes")
    if st.button(f"✅ Accept {len(chosen)} attempt(s)", type="primary"):
        awards = {int(r["ID"]): float(r["Award"]) for _, r in chosen.iterrows()}
        reviewer = st.session_state.get("trainer_name", "Unknown")
        count, errors = scoring.accept_submissions_bulk(
            awards, reviewer, notes.strip() or None)
        _cached_leaderboard.clear()
        _pending_count.clear()
        if count:
            st.success(f"Accepted {count} attempt(s).")
        for msg in errors:
            st.error(msg)
        st.rerun()


def tab_submissions() -> None:
    st.header("📥 Submissions Inbox")

    _pending_watch()
    if st.button("🔄 Refresh list"):
        _pending_count.clear()
        st.rerun()

    subs = scoring.get_submissions_overview()

    if subs.empty:
        st.info("No submissions yet. Teams submit from the **Submit Work** tab.")
        return

    pending = int((subs["status"] == "submitted").sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("Awaiting review", pending)
    m2.metric("Accepted", int((subs["status"] == "accepted").sum()))
    m3.metric("Total submissions", len(subs))

    _render_submission_archive()

    status_filter = st.multiselect(
        "Show", scoring.SUBMISSION_STATUSES, default=["submitted", "reopened"],
    )
    view = subs[subs["status"].isin(status_filter)] if status_filter else subs

    st.dataframe(
        view[["id", "team", "scenario_num", "scenario", "attempt_no", "part_label",
              "status", "self_completed", "self_points", "final_points", "files",
              "submitted_at"]].rename(
            columns={"id": "ID", "team": "Team", "scenario_num": "#",
                     "scenario": "Scenario", "attempt_no": "Att",
                     "part_label": "Part", "status": "Status",
                     "self_completed": "Self-complete", "self_points": "Self pts",
                     "final_points": "Awarded", "files": "Files",
                     "submitted_at": "Submitted"}),
        hide_index=True, use_container_width=True,
    )

    if view.empty:
        return

    st.divider()
    _render_bulk_review(view, subs)

    st.divider()
    st.subheader("Review one submission in detail")
    sub_id = int(st.selectbox(
        "Submission",
        options=view["id"].tolist(),
        format_func=lambda i: (
            f'#{i} · {view.set_index("id").loc[i, "team"]} · '
            f'{view.set_index("id").loc[i, "scenario"]} · '
            f'attempt {int(view.set_index("id").loc[i, "attempt_no"])}'
            + (f' · {view.set_index("id").loc[i, "part_label"]}'
               if view.set_index("id").loc[i, "part_label"] else "")
        ),
    ))

    sub = scoring.get_submission(sub_id)
    if sub is None:
        st.error("Submission not found.")
        return

    max_points = int(sub["max_points"] or 0)
    i1, i2, i3 = st.columns(3)
    i1.metric("Team", sub["team"])
    i2.metric("Self-reported", f'{float(sub["self_points"] or 0):.0f} / {max_points}')
    i3.metric("Attempt", int(sub["attempt_no"]))
    st.caption(
        f'Status: **{sub["status"]}** · Submitted: {sub["submitted_at"]}'
        + (f' · by {sub["submitted_by"]}' if sub["submitted_by"] else "")
        + (f' · Part: **{sub["part_label"]}**' if sub.get("part_label") else "")
        + (" · ✅ team marked complete" if sub["self_completed"] else "")
    )

    answers = scoring.get_submission_answers(sub_id)
    if not answers.empty:
        st.markdown("**Answers**")
        st.dataframe(
            answers[["label", "answer_text", "answer_number"]].rename(
                columns={"label": "Question", "answer_text": "Answer",
                         "answer_number": "Value"}),
            hide_index=True, use_container_width=True,
        )

    if sub["summary"]:
        st.markdown("**Team summary**")
        st.info(sub["summary"])

    files = scoring.list_submission_files(sub_id)
    if files:
        st.markdown("**Evidence**")
        for meta in files:
            fetched = scoring.get_file_content(int(meta["id"]))
            if fetched is None:
                continue
            filename, mime, content = fetched
            st.download_button(
                f'⬇️ {filename}  ({meta["byte_size"] / 1024:.0f} KB)',
                data=content, file_name=filename, mime=mime,
                key=f"dl_{meta['id']}",
            )

    st.divider()
    reviewer = st.session_state.get("trainer_name", "Unknown")
    accepted_now = scoring.get_accepted_totals().get(
        (int(sub["team_id"]), int(sub["scenario_num"])), 0.0)
    # Re-scoring an accepted attempt replaces its own contribution.
    already = float(sub["final_points"] or 0) if sub["status"] == "accepted" else 0.0
    others = accepted_now - already

    with st.form(f"review_form_{sub_id}"):
        award = st.number_input(
            "Points to award this attempt", min_value=0, max_value=max_points,
            value=min(int(float(sub["self_points"] or 0)), max_points), step=1,
        )
        notes = st.text_area("Review notes (optional)", value=sub["review_notes"] or "")
        accept = st.form_submit_button("✅ Accept & post to leaderboard", type="primary")

    after = others + float(award)
    st.caption(
        f'Other accepted attempts at this scenario total **{others:.0f}**. '
        f'Accepting this one makes the scenario score **{after:.0f}** of '
        f'{max_points} for {sub["team"]}, changing their total by '
        f'**{after - accepted_now:+.0f}**.'
    )

    if accept:
        if after > max_points:
            st.error(
                f"{others:.0f} from other accepted attempts + {award:.0f} here "
                f"= {after:.0f}, above this scenario's max of {max_points}. "
                f"Lower the award to at most {max(max_points - others, 0):.0f}."
            )
        else:
            total = scoring.accept_submission(sub_id, float(award), reviewer,
                                              notes or None)
            _cached_leaderboard.clear()
            _pending_count.clear()
            st.success(
                f"Accepted — {sub['team']} now scores {total:.0f} on this scenario."
            )
            st.rerun()

    a1, a2 = st.columns(2)
    if a1.button("🔄 Reopen for resubmission"):
        scoring.set_submission_status(sub_id, "reopened", reviewer)
        _cached_leaderboard.clear()
        _pending_count.clear()
        st.success("Reopened — the team can submit again.")
        st.rerun()
    if a2.button("🚫 Void this attempt"):
        scoring.set_submission_status(sub_id, "void", reviewer)
        _cached_leaderboard.clear()
        _pending_count.clear()
        st.warning("Attempt voided. Any points it contributed have been removed.")
        st.rerun()


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
        if "csv_import_nonce" not in st.session_state:
            st.session_state["csv_import_nonce"] = 0
        uploader_key = f"csv_import_{st.session_state['csv_import_nonce']}"
        csv_file = st.file_uploader("Choose a CSV file", type=["csv"], key=uploader_key)
        if csv_file is not None:
            file_bytes = csv_file.getvalue()
            file_sig = hashlib.sha256(file_bytes).hexdigest()
            st.caption("File selected. Click **Import and Save** to apply exactly once and persist it.")
            if st.button("📥 Import and Save", key=f"csv_import_btn_{st.session_state['csv_import_nonce']}"):
                if st.session_state.get("last_csv_import_sig") == file_sig:
                    st.info("This exact CSV was already imported. No changes applied.")
                else:
                    try:
                        imported_df = pd.read_csv(io.BytesIO(file_bytes))
                        # Validate that core columns exist
                        missing = [c for c in catalog.CORE_COLUMNS if c not in imported_df.columns]
                        if missing:
                            st.error(f"CSV is missing required columns: {', '.join(missing)}")
                        else:
                            # Merge imported data with existing
                            merged = edited.copy()
                            for _, row in imported_df.iterrows():
                                num_raw = row.get("num")
                                if num_raw is None or pd.isna(num_raw):
                                    continue
                                num_num = pd.to_numeric([num_raw], errors="coerce")[0]
                                if pd.isna(num_num):
                                    continue
                                num_val = int(num_num)
                                merged_nums = pd.to_numeric(merged.get("num"), errors="coerce")
                                existing_mask = merged_nums == float(num_val)
                                if existing_mask.any():
                                    row_idx = merged[existing_mask].index[0]
                                    for col in imported_df.columns:
                                        if col not in merged.columns:
                                            merged[col] = ""
                                        if pd.notna(row[col]):
                                            merged.at[row_idx, col] = row[col]
                                else:
                                    new_row = row.to_dict()
                                    new_row["num"] = num_val
                                    for col in imported_df.columns:
                                        if col not in merged.columns:
                                            merged[col] = ""
                                    merged = pd.concat([merged, pd.DataFrame([new_row])], ignore_index=True)

                            errors = _validate_catalog(merged)
                            if errors:
                                for e in errors:
                                    st.error(e)
                            else:
                                catalog.save_catalog(merged)
                                _cached_leaderboard.clear()
                                _catalog_scenarios.clear()
                                _scenario_items.clear()
                                st.session_state["catalog_df"] = catalog.load_catalog()
                                st.session_state["last_csv_import_sig"] = file_sig
                                st.session_state["csv_import_nonce"] += 1
                                st.success(f"Imported and saved {len(imported_df)} scenarios.")
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
                _cached_leaderboard.clear()
                _catalog_scenarios.clear()
                _scenario_items.clear()
                # Ensure other tabs (score entry / leaderboard) re-read the
                # just-saved catalog in the same user interaction.
                st.session_state.pop("catalog_df", None)
                st.success("Catalog saved and scoring synced.")
                st.rerun()
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

    with st.expander("⚠️ Danger zone — delete all submissions"):
        usage = scoring.storage_usage()
        st.write(
            f'This permanently deletes **{usage["submissions"]} submissions**, '
            f'their answers and **{usage["files"]} uploaded files** '
            f'({usage["bytes"] / (1024 * 1024):.1f} MB). Scores are kept.'
        )
        st.info(
            "Run this between events. Submissions are one-per-team-per-scenario, "
            "so leftover rows would block teams from submitting next time. "
            "**Export from the Submissions tab first — this cannot be undone.**"
        )
        confirm_subs = st.text_input("Type DELETE to confirm", key="purge_subs")
        if st.button("Delete all submissions", type="primary",
                     disabled=confirm_subs != "DELETE"):
            scoring.delete_all_submissions()
            _pending_count.clear()
            st.session_state.pop("evidence_zip", None)
            st.session_state.pop("evidence_zip_ready", None)
            st.success("All submissions deleted.")
            st.rerun()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
is_trainer = trainer_gate()

if is_trainer:
    _pending = _pending_count()
    tabs = st.tabs([
        "🏆 Leaderboard", "📤 Submit Work",
        f"📥 Submissions ({_pending})" if _pending else "📥 Submissions",
        "📝 Score Entry", "📋 Scenarios", "⚙️ Setup",
    ])
    with tabs[0]:
        tab_leaderboard()
    with tabs[1]:
        tab_submit()
    with tabs[2]:
        tab_submissions()
    with tabs[3]:
        tab_score_entry()
    with tabs[4]:
        tab_scenarios(can_edit=True)
    with tabs[5]:
        tab_setup()
else:
    tabs = st.tabs(["🏆 Leaderboard", "📤 Submit Work", "📋 Scenarios"])
    with tabs[0]:
        tab_leaderboard()
    with tabs[1]:
        tab_submit()
    with tabs[2]:
        tab_scenarios(can_edit=False)
