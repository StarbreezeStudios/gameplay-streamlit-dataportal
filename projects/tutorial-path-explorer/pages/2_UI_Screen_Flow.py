"""UI Screen Flow page — screen-by-screen navigation + dwell / AFK analysis.

This is where the AFK-suspect question lives: gameplay events are
near-instantaneous (login fires, heist starts), so the "is the player
actively engaging or walked away" signal can only come from
UI_STACK_UPDATED dwell times. The dbt UI fact already carries
`time_spent_on_screen` per row, so we read it directly rather than
deriving from event_ts deltas.
"""
from __future__ import annotations
import pathlib, sys
_root = pathlib.Path(__file__).resolve()
while _root != _root.parent and not (_root / "shared" / "__init__.py").exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

import pandas as pd
import streamlit as st

from shared.sf import run_query
from shared.sankey import build_sankey
from shared.funnel import (
    COMPARE_MODE,
    dwell_delta_chart,
    dwell_tail_chart,
    retention_keepsets,
)

st.set_page_config(page_title="UI Screen Flow · PD3 Tutorial Path", page_icon="🎮", layout="wide")

st.title("UI Screen Flow — first-session menu navigation")
st.caption("Source: `PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW` "
           "(precomputed from `UI_STACK_UPDATED`). "
           "`in_game` is the synthetic label for `emptystackstring` = no UI overlay.")

cohort = st.session_state.get("cohort_month")
platforms = st.session_state.get("platforms", [])
countries = st.session_state.get("countries", [])

# ---------- page-local controls -------------------------------------------
st.sidebar.divider()
st.sidebar.caption("**UI Screen Flow controls**")
min_users = st.sidebar.slider(
    "Min players per link", 10, 2000, 80, step=10,
    help="Links carrying fewer players than this are hidden.",
    key="ui_flow_min_users",
)
max_step  = st.sidebar.slider(
    "Screens to show", 3, 12, 7,
    help="Truncate after this screen index. Each step = N-th UI screen in the session.",
    key="ui_flow_max_step",
)
retention_segment = st.sidebar.radio(
    "Retention segment",
    options=["All players", "Returned D1+", "Dropped after D0", COMPARE_MODE],
    index=0,
    help=(
        "Split the cohort by whether players came back after first-login.\n\n"
        "• **Compare** renders the AFK dwell-tail chart as Returned vs Dropped — "
        "the chart that directly answers \"are the Dropped players going AFK at "
        "menu screens before quitting?\"\n"
        "• Source: `INT_NEW_PLAYER_FIRST_SESSION.RETURNED_AFTER_D0`."
    ),
    key="ui_flow_retention",
)
afk_threshold = st.sidebar.slider(
    "AFK-suspect threshold (seconds)",
    min_value=30, max_value=600, value=180, step=30,
    help=("A screen visit longer than this flags the player as AFK-suspect or "
          "stuck at that screen. Drives the bottom Dwell distribution chart. "
          "180s (3min) is the default — long enough that engaged customization "
          "doesn't trip it, short enough that menu-fiddling-then-walking-away does."),
    key="ui_flow_afk_threshold",
)

if cohort is None:
    st.warning("Pick a cohort month in the sidebar first.")
    st.stop()

bucket_or_raw = st.radio(
    "Screen labels", ["Bucketed (recommended)", "Raw NEW_SCREEN"],
    horizontal=True,
    help=("Bucketed groups settings_*, customizations, wbp_*, etc. into a small label set "
          "for readability. Raw shows every distinct screen name (much higher cardinality)."),
)
label_col = "SCREEN_BUCKET" if bucket_or_raw.startswith("Bucketed") else "SCREEN_RAW"


# ---------- fetches -------------------------------------------------------
@st.cache_data(ttl=900, show_spinner="Loading UI screen flow…")
def fetch_screens(cohort_month, label_col: str, max_screen_idx: int) -> pd.DataFrame:
    """Per-screen rows for the Sankey + dwell analysis.

    The UI fact doesn't carry PLATFORM/COUNTRY_CODE — those are filtered
    Streamlit-side via the events-fact session set on the Combined Flow
    page, but on this page we just show the full UI cohort because the
    UI fact already restricts to first session.

    `time_spent_on_screen` is the canonical dwell (computed in dbt as
    next.event_ts - this.event_ts within (user, session)). Last screen
    of a session has heartbeat-decay idle time baked in — that's GOOD
    for AFK detection, not a bug.
    """
    sql = f"""
        SELECT USER_ID,
               SESSION_ID,
               SCREEN_IDX,
               {label_col} AS LABEL,
               TIME_SPENT_ON_SCREEN
        FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW
        WHERE COHORT_MONTH = %s AND SCREEN_IDX <= %s
    """
    return run_query(sql, (cohort_month, max_screen_idx))


@st.cache_data(ttl=900, show_spinner="Loading retention flags…")
def fetch_retention(cohort_month) -> pd.DataFrame:
    sql = """
        SELECT USER_ID, RETURNED_AFTER_D0, JUDGEABLE_D1 AS JUDGEABLE
        FROM PAYDAY3_PROD.DBT_ANALYTICS.INT_NEW_PLAYER_FIRST_SESSION
        WHERE COHORT_MONTH = %s
    """
    return run_query(sql, (cohort_month,))


def _build_links(screens: pd.DataFrame) -> pd.DataFrame:
    s = screens.sort_values(["USER_ID", "SESSION_ID", "SCREEN_IDX"]).copy()
    s["TO_LABEL"] = s.groupby(["USER_ID", "SESSION_ID"])["LABEL"].shift(-1)
    s["TO_LABEL"] = s["TO_LABEL"].fillna("<end>")
    return (s.groupby(["SCREEN_IDX", "LABEL", "TO_LABEL"]).size()
             .reset_index(name="N_USERS")
             .rename(columns={"SCREEN_IDX": "FROM_IDX", "LABEL": "FROM_LABEL"}))


def _build_dwell_frame(screens: pd.DataFrame) -> pd.DataFrame:
    """Adapt UI rows into the (FROM_IDX, FROM_LABEL, DURATION_SEC) shape the
    shared dwell helpers expect. NULL/zero dwells are dropped — a defensive
    guard, since the dbt model already computes time_spent_on_screen via
    a lead() within session and would NULL the last row out for us."""
    d = screens[screens["TIME_SPENT_ON_SCREEN"].notna()].copy()
    d = d[d["TIME_SPENT_ON_SCREEN"] > 0]
    return d.rename(columns={
        "SCREEN_IDX":            "FROM_IDX",
        "LABEL":                 "FROM_LABEL",
        "TIME_SPENT_ON_SCREEN":  "DURATION_SEC",
    })[["USER_ID", "SESSION_ID", "FROM_IDX", "FROM_LABEL", "DURATION_SEC"]]


# ---------- main pipeline -------------------------------------------------
with st.status("Loading UI screen flow…", expanded=True) as status:
    status.update(label="Fetching UI screen rows from Snowflake…")
    # Pull screens up to max_step+1 so the trailing transitions resolve.
    screens_all = fetch_screens(cohort, label_col, max_step + 1)
    if screens_all.empty:
        status.update(label="No screens match the current filters.",
                      state="complete", expanded=False)
        st.info("No screens match the current filters.")
        st.stop()
    screens_all["USER_ID"] = screens_all["USER_ID"].astype(str)
    st.write(f"• Pulled {len(screens_all):,} screen rows "
             f"({screens_all['USER_ID'].nunique():,} unique players).")

    keep_r: set[str] = set()
    keep_d: set[str] = set()
    if retention_segment != "All players":
        status.update(label="Loading retention flags…")
        ret = fetch_retention(cohort)
        keep_r, keep_d = retention_keepsets(ret)

    if retention_segment == COMPARE_MODE:
        screens_r = screens_all[screens_all["USER_ID"].isin(keep_r)]
        screens_d = screens_all[screens_all["USER_ID"].isin(keep_d)]
        st.write(f"• Returned D1+: {screens_r['USER_ID'].nunique():,} players · "
                 f"Dropped after D0: {screens_d['USER_ID'].nunique():,} players.")

        status.update(label="Building side-by-side Sankeys…")
        links_r = _build_links(screens_r)
        links_d = _build_links(screens_d)
        n_r = screens_r["USER_ID"].nunique()
        n_d = screens_d["USER_ID"].nunique()
        fig_r = build_sankey(
            links_r, min_users=min_users, max_step=max_step,
            title=(f"<b>Returned D1+ · n={n_r:,}</b>"
                   f"<br><sub>Widths = % of segment. Screens 1–{max_step}, "
                   f"links < {min_users} hidden. Labels: {label_col}.</sub>"),
            height=720, segment_size=n_r,
        )
        fig_d = build_sankey(
            links_d, min_users=min_users, max_step=max_step,
            title=(f"<b>Dropped after D0 · n={n_d:,}</b>"
                   f"<br><sub>Widths = % of segment. Screens 1–{max_step}, "
                   f"links < {min_users} hidden. Labels: {label_col}.</sub>"),
            height=720, segment_size=n_d,
        )

        status.update(label="Computing dwell-tail per segment…")
        dwell_r = _build_dwell_frame(screens_r)
        dwell_d = _build_dwell_frame(screens_d)
        dwell_fig = dwell_delta_chart(dwell_r, dwell_d,
                                      max_step=max_step, min_users=min_users,
                                      threshold_sec=afk_threshold)

        status.update(label="Done.", state="complete", expanded=False)
        col_r, col_d = st.columns(2)
        with col_r: st.plotly_chart(fig_r, use_container_width=True)
        with col_d: st.plotly_chart(fig_d, use_container_width=True)

        st.divider()
        if dwell_fig is None:
            st.info("Not enough volume to build the AFK-dwell chart at the "
                    "current `min_users` threshold — drop the slider.")
        else:
            st.plotly_chart(dwell_fig, use_container_width=True)

    else:
        screens = screens_all
        if retention_segment != "All players":
            keep_set = keep_r if retention_segment == "Returned D1+" else keep_d
            before = screens["USER_ID"].nunique()
            screens = screens[screens["USER_ID"].isin(keep_set)]
            st.write(f"• In segment {retention_segment}: {len(keep_set):,} · "
                     f"intersected with UI fact: {screens['USER_ID'].nunique():,} "
                     f"(dropped {before - screens['USER_ID'].nunique():,} of {before:,}).")
            if screens.empty:
                status.update(label=f"No players in the {retention_segment} segment.",
                              state="complete", expanded=False)
                st.info(f"No players in the **{retention_segment}** segment.")
                st.stop()

        status.update(label="Building screen-flow Sankey…")
        links = _build_links(screens)
        n_players = screens["USER_ID"].nunique()
        seg_note  = "" if retention_segment == "All players" else f" · segment: {retention_segment}"
        title = (f"<b>UI Screen Flow · {cohort.strftime('%Y-%m')} cohort · n={n_players:,}{seg_note}</b>"
                 f"<br><sub>Each column = N-th screen visited in the first session. "
                 f"Screens 1–{max_step} shown; links < {min_users} players hidden. "
                 f"Labels: {label_col}.</sub>")
        fig = build_sankey(links, min_users=min_users, max_step=max_step, title=title, height=950)

        status.update(label="Computing dwell-tail (AFK-suspect)…")
        dwell = _build_dwell_frame(screens)
        dwell_fig = dwell_tail_chart(dwell,
                                     max_step=max_step, min_users=min_users,
                                     threshold_sec=afk_threshold)

        status.update(label="Done.", state="complete", expanded=False)
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        if dwell_fig is None:
            st.info("Not enough volume to build the AFK-dwell chart.")
        else:
            st.plotly_chart(dwell_fig, use_container_width=True)


# ---------- supporting tables --------------------------------------------
st.subheader("Median seconds spent on each screen")
@st.cache_data(ttl=900, show_spinner=False)
def screen_time(cohort_month, label_col: str):
    sql = f"""
        SELECT {label_col} AS LABEL,
               COUNT(*) AS n_visits,
               COUNT(DISTINCT USER_ID) AS n_players,
               MEDIAN(TIME_SPENT_ON_SCREEN) AS median_seconds,
               APPROX_PERCENTILE(TIME_SPENT_ON_SCREEN, 0.9) AS p90_seconds
        FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW
        WHERE COHORT_MONTH = %s AND TIME_SPENT_ON_SCREEN IS NOT NULL
        GROUP BY 1
        HAVING COUNT(*) >= 100
        ORDER BY n_visits DESC
        LIMIT 30
    """
    return run_query(sql, (cohort_month,))

st.dataframe(
    screen_time(cohort, label_col),
    use_container_width=True, hide_index=True,
)
