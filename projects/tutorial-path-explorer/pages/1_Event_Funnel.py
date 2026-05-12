"""Event Funnel page — high-level session events Sankey."""
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

st.set_page_config(page_title="Event Funnel · PD3 Tutorial Path", page_icon="🎮", layout="wide")

st.title("Event Funnel — first-session journey")

# Pull filter state set in app.py
cohort = st.session_state.get("cohort_month")
platforms  = st.session_state.get("platforms", [])
countries  = st.session_state.get("countries", [])

# Page-local Sankey-shape controls. Defaults tuned for the event funnel —
# ~30 events per session, so 10 steps captures the loop nicely.
st.sidebar.divider()
st.sidebar.caption("**Event Funnel controls**")
min_users  = st.sidebar.slider(
    "Min players per link", 10, 1000, 80, step=10,
    help="Links carrying fewer players than this are hidden.",
    key="event_funnel_min_users",
)
max_step   = st.sidebar.slider(
    "Steps to show", 3, 15, 10,
    help="Truncate after this step index. Each step = N-th event in the session.",
    key="event_funnel_max_step",
)

if cohort is None:
    st.warning("Pick a cohort month in the sidebar first.")
    st.stop()


@st.cache_data(ttl=900, show_spinner="Loading event timeline…")
def fetch_events(cohort_month, platforms: tuple[str, ...], countries: tuple[str, ...]) -> pd.DataFrame:
    where = ["COHORT_MONTH = %s"]
    params: list = [cohort_month]
    if platforms:
        where.append("PLATFORM IN (" + ",".join(["%s"] * len(platforms)) + ")")
        params += list(platforms)
    if countries:
        where.append("COUNTRY_CODE IN (" + ",".join(["%s"] * len(countries)) + ")")
        params += list(countries)
    sql = f"""
        SELECT USER_ID, SESSION_ID, STEP_IDX, EVENT_TYPE, EVENT_LABEL
        FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS
        WHERE {' AND '.join(where)}
    """
    return run_query(sql, tuple(params))


events = fetch_events(cohort, tuple(platforms), tuple(countries))
if events.empty:
    st.info("No events match the current filters.")
    st.stop()

# Anchor every journey at GAME_LAUNCHED so the Sankey reads as a single funnel.
# Drops sessions whose step 1 is something else (LOGIN_OK alone, SESSION_END, etc.) —
# usually telemetry gaps where the launch event didn't land but heartbeats did.
all_users = events[["USER_ID", "SESSION_ID"]].drop_duplicates().shape[0]
launched = events[
    (events["STEP_IDX"] == 1) & (events["EVENT_LABEL"] == "GAME_LAUNCHED")
][["USER_ID", "SESSION_ID"]].drop_duplicates()
events = events.merge(launched, on=["USER_ID", "SESSION_ID"], how="inner")
kept_users = launched.shape[0]
dropped = all_users - kept_users

# Bucket HEIST_START_* tail into HEIST_START_other to keep the Sankey readable
TOP_HEISTS = {"SnGBB", "BranchBank", "SnGFO", "JewelryStore", "ONE", "FirstPlayable", "Bebe"}

def collapse(lbl: str) -> str:
    if not lbl.startswith("HEIST_START_"):
        return lbl
    name = lbl[len("HEIST_START_"):]
    if name in TOP_HEISTS or name.startswith("BranchBank"):
        return f"HEIST_START_{name}"
    return "HEIST_START_other"

events["LABEL"] = events["EVENT_LABEL"].map(collapse)

# Build (FROM_IDX, FROM_LABEL, TO_LABEL, N_USERS) link table
events_sorted = events.sort_values(["USER_ID", "SESSION_ID", "STEP_IDX"]).copy()
events_sorted["TO_LABEL"] = events_sorted.groupby(["USER_ID", "SESSION_ID"])["LABEL"].shift(-1)
events_sorted["TO_LABEL"] = events_sorted["TO_LABEL"].fillna("<end>")
links = (
    events_sorted.groupby(["STEP_IDX", "LABEL", "TO_LABEL"]).size()
    .reset_index(name="N_USERS")
    .rename(columns={"STEP_IDX": "FROM_IDX", "LABEL": "FROM_LABEL"})
)

n_players = events["USER_ID"].nunique()
drop_note = (f" · {dropped:,} sessions excluded (no GAME_LAUNCHED at step 1)"
             if dropped else "")
title = (f"<b>Event Funnel · {cohort.strftime('%Y-%m')} cohort · n={n_players:,}{drop_note}</b>"
         f"<br><sub>Every journey starts at GAME_LAUNCHED. "
         f"Each column = N-th event in the player's first session. "
         f"Steps 1–{max_step} shown; links < {min_users} players hidden.</sub>")

fig = build_sankey(links, min_users=min_users, max_step=max_step, title=title, height=900)
st.plotly_chart(fig, use_container_width=True)

with st.expander("Top transitions per step"):
    top = (links.sort_values(["FROM_IDX", "N_USERS"], ascending=[True, False])
                .groupby("FROM_IDX").head(6)
                .reset_index(drop=True))
    top["pct_of_step"] = (top["N_USERS"] /
                          top.groupby("FROM_IDX")["N_USERS"].transform("sum") * 100).round(1)
    st.dataframe(top, use_container_width=True, hide_index=True)

with st.expander("Raw link table (filtered)"):
    st.dataframe(
        links[(links["N_USERS"] >= min_users) & (links["FROM_IDX"] <= max_step)]
        .sort_values(["FROM_IDX", "N_USERS"], ascending=[True, False]),
        use_container_width=True, hide_index=True,
    )
