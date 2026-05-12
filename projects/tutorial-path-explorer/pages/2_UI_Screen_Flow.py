"""UI Screen Flow page — screen-by-screen navigation Sankey."""
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

st.set_page_config(page_title="UI Screen Flow · PD3 Tutorial Path", page_icon="🎮", layout="wide")

st.title("UI Screen Flow — first-session menu navigation")
st.caption("Source: `PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW` "
           "(precomputed from `UI_STACK_UPDATED`). "
           "`in_game` is the synthetic label for `emptystackstring` = no UI overlay.")

cohort = st.session_state.get("cohort_month")
platforms = st.session_state.get("platforms", [])
countries = st.session_state.get("countries", [])
min_users = int(st.session_state.get("min_users", 80))
max_step  = int(st.session_state.get("max_step", 10))

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


@st.cache_data(ttl=900, show_spinner="Loading UI screen flow…")
def fetch_screens(cohort_month, platforms: tuple[str, ...], countries: tuple[str, ...],
                  label_col: str, max_screen_idx: int) -> pd.DataFrame:
    where = ["COHORT_MONTH = %s", "SCREEN_IDX <= %s"]
    params: list = [cohort_month, max_screen_idx]
    if platforms:
        where.append("PLATFORM IN (" + ",".join(["%s"] * len(platforms)) + ")")
        params += list(platforms)
    if countries:
        where.append("COUNTRY_CODE IN (" + ",".join(["%s"] * len(countries)) + ")")
        params += list(countries)
    sql = f"""
        SELECT USER_ID, SESSION_ID, SCREEN_IDX, {label_col} AS LABEL
        FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW
        WHERE {' AND '.join(where)}
    """
    return run_query(sql, tuple(params))


# Pull screens up to max_step+1 so we can compute the trailing transitions
screens = fetch_screens(cohort, tuple(platforms), tuple(countries), label_col, max_step + 1)
if screens.empty:
    st.info("No screens match the current filters.")
    st.stop()

screens_sorted = screens.sort_values(["USER_ID", "SESSION_ID", "SCREEN_IDX"]).copy()
screens_sorted["TO_LABEL"] = screens_sorted.groupby(["USER_ID", "SESSION_ID"])["LABEL"].shift(-1)
screens_sorted["TO_LABEL"] = screens_sorted["TO_LABEL"].fillna("<end>")
links = (
    screens_sorted.groupby(["SCREEN_IDX", "LABEL", "TO_LABEL"]).size()
    .reset_index(name="N_USERS")
    .rename(columns={"SCREEN_IDX": "FROM_IDX", "LABEL": "FROM_LABEL"})
)

n_players = screens["USER_ID"].nunique()
title = (f"<b>UI Screen Flow · {cohort.strftime('%Y-%m')} cohort · n={n_players:,}</b>"
         f"<br><sub>Each column = N-th screen visited in the first session. "
         f"Screens 1–{max_step} shown; links < {min_users} players hidden. "
         f"Labels: {label_col}.</sub>")

fig = build_sankey(links, min_users=min_users, max_step=max_step, title=title, height=950)
st.plotly_chart(fig, use_container_width=True)

# Time-spent leaderboard
st.subheader("Median seconds spent on each screen")
@st.cache_data(ttl=900, show_spinner=False)
def screen_time(cohort_month, platforms: tuple[str, ...], countries: tuple[str, ...], label_col: str):
    where = ["COHORT_MONTH = %s", "TIME_SPENT_ON_SCREEN IS NOT NULL"]
    params: list = [cohort_month]
    if platforms:
        where.append("PLATFORM IN (" + ",".join(["%s"] * len(platforms)) + ")")
        params += list(platforms)
    if countries:
        where.append("COUNTRY_CODE IN (" + ",".join(["%s"] * len(countries)) + ")")
        params += list(countries)
    sql = f"""
        SELECT {label_col} AS LABEL,
               COUNT(*) AS n_visits,
               COUNT(DISTINCT USER_ID) AS n_players,
               MEDIAN(TIME_SPENT_ON_SCREEN) AS median_seconds,
               APPROX_PERCENTILE(TIME_SPENT_ON_SCREEN, 0.9) AS p90_seconds
        FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW
        WHERE {' AND '.join(where)}
        GROUP BY 1
        HAVING COUNT(*) >= 100
        ORDER BY n_visits DESC
        LIMIT 30
    """
    return run_query(sql, tuple(params))

st.dataframe(
    screen_time(cohort, tuple(platforms), tuple(countries), label_col),
    use_container_width=True, hide_index=True,
)
