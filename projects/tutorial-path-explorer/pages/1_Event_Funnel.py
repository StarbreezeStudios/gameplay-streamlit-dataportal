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
retention_segment = st.sidebar.radio(
    "Retention segment",
    options=["All players", "Returned D1+", "Dropped after D0"],
    index=0,
    help=(
        "Split the cohort by whether players came back after their first-login day.\n\n"
        "• **Returned D1+** — logged in at least once on a calendar day after first-login.\n"
        "• **Dropped after D0** — no return; only counts users whose first-login was ≥1 day ago "
        "(otherwise retention can't be judged yet).\n"
        "• Source: `INT_NEW_PLAYER_FIRST_SESSION.RETURNED_AFTER_D0` (pre-aggregated from `FACT_DAILY_USER_LOGINS`)."
    ),
    key="event_funnel_retention",
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


@st.cache_data(ttl=900, show_spinner="Loading retention flags…")
def fetch_retention(cohort_month) -> pd.DataFrame:
    """Per-user retention flags from the enriched int model.

    `returned_after_d0` and `judgeable_d1` are pre-computed in
    int_new_player_first_session (one row per user) — cheaper than
    re-aggregating FACT_DAILY_USER_LOGINS on every page load.
    """
    sql = """
        SELECT
            USER_ID,
            RETURNED_AFTER_D0,
            JUDGEABLE_D1 AS JUDGEABLE
        FROM PAYDAY3_PROD.DBT_ANALYTICS.INT_NEW_PLAYER_FIRST_SESSION
        WHERE COHORT_MONTH = %s
    """
    return run_query(sql, (cohort_month,))


events = fetch_events(cohort, tuple(platforms), tuple(countries))
if events.empty:
    st.info("No events match the current filters.")
    st.stop()

# Apply retention segment BEFORE the GAME_LAUNCHED anchor so the
# "n_players" total reflects the chosen segment.
retention_dropped = 0
if retention_segment != "All players":
    ret = fetch_retention(cohort)
    # Snowflake NUMBER columns can come back as decimal.Decimal in object dtype.
    # Coerce both flag columns to int so `== 1` / `== 0` is bulletproof,
    # and coerce USER_ID to str on both sides so the isin() join can't fail
    # on a Decimal-vs-str mismatch.
    ret["RETURNED_AFTER_D0"] = pd.to_numeric(ret["RETURNED_AFTER_D0"], errors="coerce").fillna(0).astype(int)
    ret["JUDGEABLE"]         = pd.to_numeric(ret["JUDGEABLE"],         errors="coerce").fillna(0).astype(int)
    ret["USER_ID"]           = ret["USER_ID"].astype(str)
    events["USER_ID"]        = events["USER_ID"].astype(str)
    if retention_segment == "Returned D1+":
        keep_users = ret.loc[ret["RETURNED_AFTER_D0"] == 1, "USER_ID"]
    else:  # Dropped after D0
        keep_users = ret.loc[
            (ret["RETURNED_AFTER_D0"] == 0) & (ret["JUDGEABLE"] == 1), "USER_ID"
        ]
    before = events["USER_ID"].nunique()
    keep_set = set(keep_users)
    events = events[events["USER_ID"].isin(keep_set)]
    retention_dropped = before - events["USER_ID"].nunique()
    st.caption(
        f"Retention diagnostics — cohort total in int model: {len(ret):,} · "
        f"in segment **{retention_segment}**: {len(keep_set):,} · "
        f"intersected with events fact: {events['USER_ID'].nunique():,} "
        f"(dropped {retention_dropped:,} of {before:,} pre-filter)."
    )
    if events.empty:
        st.info(f"No players in the **{retention_segment}** segment for this cohort.")
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

# Pass labels through as-is. Each distinct heist gets its own node so we
# can see exactly which heist players move into after the tutorial. The
# `min_users` slider hides links carrying fewer than N players, which is
# how visual density is kept reasonable rather than by relabeling.
events["LABEL"] = events["EVENT_LABEL"]

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
seg_note = "" if retention_segment == "All players" else f" · segment: {retention_segment}"
title = (f"<b>Event Funnel · {cohort.strftime('%Y-%m')} cohort · n={n_players:,}{seg_note}{drop_note}</b>"
         f"<br><sub>Every journey starts at GAME_LAUNCHED. "
         f"Each column = N-th event in the player's first session. "
         f"Steps 1–{max_step} shown; links < {min_users} players hidden.</sub>")

fig = build_sankey(links, min_users=min_users, max_step=max_step, title=title, height=900)
st.plotly_chart(fig, use_container_width=True)

with st.expander("ℹ️ How to read the tutorial labels"):
    st.markdown(
        """
The four tutorial heists differ in whether they have a real fail condition:

| Tutorial | Success rate (Apr 2026) | Read |
|---|---|---|
| Combat | ~96% | Effectively no fail condition — `Success` fires on completion of the scripted walkthrough. Shown here as **`TUTORIAL_combat_completed`**. |
| CrowdControl | ~48% | Real pass/fail. Split on success/fail. |
| Detection | ~39% | Real pass/fail (hardest — players average 2.6 attempts). |
| Social | ~50% | Real pass/fail. |

So `TUTORIAL_combat_completed → SESSION_END` reads as "finished the combat walkthrough then quit" — the friction is the quit, not the success/fail. For the other three, the success/fail split is genuinely meaningful.
        """
    )

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
