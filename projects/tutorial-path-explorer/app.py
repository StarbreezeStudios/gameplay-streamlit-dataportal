"""Tutorial Path Explorer — landing page.

STX-1125. Interactive view of the first-session journey of new PD3 players.
Two pages in the sidebar:
  1. Event Funnel       — high-level session events (launch → login → tutorial → heist)
  2. UI Screen Flow     — screen-by-screen navigation from UI_STACK_UPDATED

Filters set here propagate to the pages via st.session_state.
"""
from __future__ import annotations
import pathlib, sys
# Bootstrap: add monorepo root to sys.path so `from shared.X import ...` works
# both in local dev and in Docker (where shared/ is copied alongside the project).
_root = pathlib.Path(__file__).resolve()
while _root != _root.parent and not (_root / "shared" / "__init__.py").exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

import pandas as pd
import streamlit as st

from shared.sf import run_query

st.set_page_config(
    page_title="PD3 Tutorial Path Explorer",
    page_icon="🎮",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------- shared filter dimensions, cached ------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_cohorts() -> list[pd.Timestamp]:
    df = run_query(
        "SELECT DISTINCT COHORT_MONTH FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS "
        "ORDER BY COHORT_MONTH DESC"
    )
    return [c.date() if hasattr(c, "date") else c for c in df["COHORT_MONTH"].tolist()]


@st.cache_data(ttl=3600, show_spinner=False)
def get_platforms() -> list[str]:
    df = run_query(
        "SELECT DISTINCT PLATFORM FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS "
        "WHERE PLATFORM IS NOT NULL ORDER BY PLATFORM"
    )
    return df["PLATFORM"].tolist()


@st.cache_data(ttl=3600, show_spinner=False)
def get_countries() -> list[str]:
    df = run_query(
        "SELECT COUNTRY_CODE, COUNT(DISTINCT USER_ID) AS n "
        "FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS "
        "WHERE COUNTRY_CODE IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 40"
    )
    return df["COUNTRY_CODE"].tolist()


# ---------- sidebar filters -----------------------------------------------
st.sidebar.title("Filters")
try:
    cohorts = get_cohorts()
except Exception as e:
    msg = str(e)
    if "does not exist" in msg.lower() or "object" in msg.lower() and "not authorized" in msg.lower():
        st.sidebar.error(
            "Backing dbt tables not found. "
            "Run `dbt run --select +fct_new_player_first_session_events +fct_new_player_ui_screen_flow` "
            "in payday3-dbt, then refresh."
        )
    else:
        st.sidebar.error(f"Could not load cohort list: {e}")
    cohorts = []

default_cohort = cohorts[0] if cohorts else None
selected_cohort = st.sidebar.selectbox(
    "Cohort month (first-login)",
    options=cohorts,
    index=0 if cohorts else None,
    format_func=lambda d: d.strftime("%Y-%m") if d else "(none)",
    help="Players' first-login month. Defaults to most recent.",
)
st.session_state["cohort_month"] = selected_cohort

if cohorts:
    platforms = get_platforms()
    selected_platforms = st.sidebar.multiselect(
        "Platform", platforms, default=platforms,
        help="Empty = all platforms.",
    )
    st.session_state["platforms"] = selected_platforms or platforms

    countries = get_countries()
    selected_countries = st.sidebar.multiselect(
        "Country (top 40 by player count)", countries, default=[],
        help="Empty = all countries.",
    )
    st.session_state["countries"] = selected_countries

st.sidebar.divider()
st.sidebar.caption(
    "Sankey-shape controls (min link size, step depth) live on each page "
    "since the two views have different sensible defaults."
)

# ---------- landing copy --------------------------------------------------
st.title("PD3 Tutorial Path Explorer")
st.caption(
    "STX-1125 · Investigation of new player path around tutorial. "
    "Two views — high-level session events and granular UI screen flow — "
    "for every PD3 first-login cohort since October 2025."
)

if not cohorts:
    st.warning(
        "No data found in `PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS`. "
        "Run the materialization SQL in `snowflake/01_create_*.sql` first."
    )
    st.stop()

# ---------- top-line metrics for selected cohort --------------------------
@st.cache_data(ttl=900, show_spinner="Loading cohort metrics…")
def cohort_metrics(cohort_month, platforms: tuple[str, ...], countries: tuple[str, ...]):
    where = ["COHORT_MONTH = %s"]
    params: list = [cohort_month]
    if platforms:
        where.append("PLATFORM IN (" + ",".join(["%s"] * len(platforms)) + ")")
        params += list(platforms)
    if countries:
        where.append("COUNTRY_CODE IN (" + ",".join(["%s"] * len(countries)) + ")")
        params += list(countries)
    sql = f"""
        WITH base AS (
          SELECT * FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS
          WHERE {' AND '.join(where)}
        )
        SELECT
            COUNT(DISTINCT USER_ID) AS players,
            COUNT(DISTINCT CASE WHEN EVENT_TYPE='TUTORIAL' THEN USER_ID END) AS touched_tutorial,
            COUNT(DISTINCT CASE WHEN EVENT_LABEL LIKE 'TUTORIAL_combat_success' THEN USER_ID END) AS combat_success,
            COUNT(DISTINCT CASE WHEN EVENT_TYPE='HEIST_START' THEN USER_ID END) AS reached_first_heist,
            COUNT(DISTINCT CASE WHEN EVENT_LABEL='HEIST_END_success' THEN USER_ID END) AS finished_a_heist
        FROM base
    """
    return run_query(sql, tuple(params))

m = cohort_metrics(
    st.session_state["cohort_month"],
    tuple(st.session_state.get("platforms", [])),
    tuple(st.session_state.get("countries", [])),
)

if not m.empty:
    row = m.iloc[0]
    players = int(row["PLAYERS"]) if row["PLAYERS"] else 0
    def pct(n):
        return f"{(int(n)/players*100):.1f}%" if players else "—"
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Players in cohort", f"{players:,}")
    c2.metric("Touched a tutorial",    f"{int(row['TOUCHED_TUTORIAL']):,}",   pct(row["TOUCHED_TUTORIAL"]))
    c3.metric("Combat-tutorial success", f"{int(row['COMBAT_SUCCESS']):,}",   pct(row["COMBAT_SUCCESS"]))
    c4.metric("Started a heist",       f"{int(row['REACHED_FIRST_HEIST']):,}", pct(row["REACHED_FIRST_HEIST"]))
    c5.metric("Finished a heist",      f"{int(row['FINISHED_A_HEIST']):,}",   pct(row["FINISHED_A_HEIST"]))

st.divider()
st.markdown(
    "**Pick a view in the sidebar:**\n"
    "- **Event Funnel** — the canonical session journey: launch → login → tutorial → party → matchmaking → lobby → heist.\n"
    "- **UI Screen Flow** — every screen the player visits, from `UI_STACK_UPDATED`. Reveals the menu navigation that the event-level view doesn't.\n\n"
    "Sidebar filters apply to both views."
)

with st.expander("About the data"):
    st.markdown(
        """
        - **Source tables:** `PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS` and
          `PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW`. Both are precomputed
          materializations of the new-player-first-session universe — built from
          `FIRST_LOGINS`, `SESSIONS`, the relevant `DBT_STAGING` event tables, and
          `UI_STACK_UPDATED` (2.83B rows; pre-filtered down to first-session events here).
        - **Cohort window:** first-login month ≥ Oct 2025.
        - **First session per user:** earliest by `SESSIONS.FIRST_HEARTBEAT_TS`.
        - **Heist event match rate:** ~64% pre-Feb 2026, ~94% from Mar 2026 onwards
          (client telemetry fix). Cohorts before Feb 2026 will overstate heist dropouts.
        """
    )
