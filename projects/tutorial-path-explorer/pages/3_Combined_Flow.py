"""Combined Flow page — gameplay + UI events interleaved by event_ts.

The Event Funnel page shows the *gameplay* event stream only (login,
tutorial, lobby, heist, etc.). When players seem to "end the session
in lobby" they're almost always actually fiddling with menus until
the heartbeat decays — pause/inventory/customization/blackmarket — but
that activity is invisible on the gameplay-only view.

This page interleaves both streams from the two existing fact tables,
sorts by `event_ts`, collapses consecutive same-label rows (so a
5-toggle pause-spam reads as one node, not five), and renders the
same Sankey + divergence + duration scaffolding the Event Funnel page
uses. The shared funnel/sankey helpers do the heavy lifting; this
file just owns the data fetch + interleave + label disambiguation.
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
    anchor_to_launch,
    build_links,
    compute_step_durations,
    divergence_chart,
    duration_chart,
    duration_compare_chart,
    retention_keepsets,
)
# Note: the AFK-suspect dwell-tail chart lives on the UI Screen Flow page
# (page 2), not here. Combined-Flow dwells mix gameplay events (near-zero
# duration by construction) with UI screens, which muddies the AFK signal.
# The UI fact's `time_spent_on_screen` is the cleanest per-screen measure.

st.set_page_config(page_title="Combined Flow · PD3 Tutorial Path", page_icon="🎮", layout="wide")
st.title("Combined Flow — gameplay + UI events")
st.caption(
    "Both streams interleaved by `event_ts` within each first session. "
    "Gameplay events keep their normal palette; UI screens render in a "
    "muted slate so the gameplay spine still reads as the primary funnel. "
    "Use this view to spot menu-thrash patterns (pause/inventory/customization "
    "loops) right before a session ends mid-funnel."
)

# Pull filter state set in app.py
cohort = st.session_state.get("cohort_month")
platforms  = st.session_state.get("platforms", [])
countries  = st.session_state.get("countries", [])

# Combined timelines are LONGER than gameplay-only (UI events outnumber
# gameplay events ~10:1 per session), so the default min_users floor is
# higher and max_step is wider. Both sliders are page-local with
# distinct keys so the Event Funnel page's settings don't leak in.
st.sidebar.divider()
st.sidebar.caption("**Combined Flow controls**")
min_users  = st.sidebar.slider(
    "Min players per link", 10, 2000, 150, step=10,
    help="Links carrying fewer players than this are hidden. "
         "Higher default than the Event Funnel page because the combined "
         "timeline has far more low-traffic transitions.",
    key="combined_min_users",
)
max_step   = st.sidebar.slider(
    "Steps to show", 5, 30, 15,
    help="Truncate after this step index in the combined timeline. "
         "A first session typically generates 10–30 gameplay events and "
         "40–100 UI events; after compression ~20-50 combined steps.",
    key="combined_max_step",
)
retention_segment = st.sidebar.radio(
    "Retention segment",
    options=["All players", "Returned D1+", "Dropped after D0", COMPARE_MODE],
    index=0,
    help=(
        "Same retention split as the Event Funnel page — see "
        "`INT_NEW_PLAYER_FIRST_SESSION.RETURNED_AFTER_D0`. "
        "Compare mode renders both side-by-side, scaled to % of segment "
        "so widths line up across cohorts of different sizes."
    ),
    key="combined_retention",
)

if cohort is None:
    st.warning("Pick a cohort month in the sidebar first.")
    st.stop()


@st.cache_data(ttl=900, show_spinner="Loading event timeline…")
def fetch_events(cohort_month, platforms: tuple[str, ...], countries: tuple[str, ...]) -> pd.DataFrame:
    """Gameplay events fact. Same query as the Event Funnel page — kept
    inline here rather than imported because Streamlit cache_data is
    keyed by function identity and we want the two pages to share the
    cached result when they're called with the same args."""
    where = ["COHORT_MONTH = %s"]
    params: list = [cohort_month]
    if platforms:
        where.append("PLATFORM IN (" + ",".join(["%s"] * len(platforms)) + ")")
        params += list(platforms)
    if countries:
        where.append("COUNTRY_CODE IN (" + ",".join(["%s"] * len(countries)) + ")")
        params += list(countries)
    sql = f"""
        SELECT USER_ID, SESSION_ID, STEP_IDX, EVENT_TYPE, EVENT_LABEL, EVENT_TS
        FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_FIRST_SESSION_EVENTS
        WHERE {' AND '.join(where)}
    """
    return run_query(sql, tuple(params))


@st.cache_data(ttl=900, show_spinner="Loading UI screen flow…")
def fetch_ui(cohort_month) -> pd.DataFrame:
    """UI screen-flow fact. Filtered to first session per the dbt model,
    clustered by cohort_month, so a single-cohort fetch is fast even
    though the raw `UI_STACK_UPDATED` is 2.83B rows.

    Note: this fact doesn't carry PLATFORM / COUNTRY_CODE columns — the
    Streamlit-side intersection with anchored sessions (which DO have
    those filters applied via the gameplay events fact) restricts the
    final user set correctly.
    """
    sql = """
        SELECT USER_ID, SESSION_ID, SCREEN_IDX, SCREEN_BUCKET, EVENT_TS
        FROM PAYDAY3_PROD.DBT_ANALYTICS.FCT_NEW_PLAYER_UI_SCREEN_FLOW
        WHERE COHORT_MONTH = %s
    """
    return run_query(sql, (cohort_month,))


@st.cache_data(ttl=900, show_spinner="Loading retention flags…")
def fetch_retention(cohort_month) -> pd.DataFrame:
    sql = """
        SELECT
            USER_ID,
            RETURNED_AFTER_D0,
            JUDGEABLE_D1 AS JUDGEABLE
        FROM PAYDAY3_PROD.DBT_ANALYTICS.INT_NEW_PLAYER_FIRST_SESSION
        WHERE COHORT_MONTH = %s
    """
    return run_query(sql, (cohort_month,))


def interleave_and_compress(events: pd.DataFrame, ui: pd.DataFrame) -> pd.DataFrame:
    """UNION events + UI by event_ts per (user, session), collapse repeats.

    Steps:
    1. Tag each source with `event_label` already set on the gameplay side
       and `ui:<screen_bucket>` on the UI side, so the `ui:` prefix
       disambiguates (and `_display_label` strips it for the chart).
    2. Concat both sides, sort by (user, session, event_ts).
    3. Drop rows where the label is identical to the previous row in the
       same session — 5 pause-toggles in a row become 1 pause node,
       which is what reads as "menu thrash" rather than "five hops".
    4. Re-index step_idx within (user, session) as `cumcount + 1`.
    """
    gp = events[["USER_ID", "SESSION_ID", "EVENT_TS", "EVENT_LABEL"]].copy()
    gp["LABEL"] = gp["EVENT_LABEL"]

    ui_norm = ui[["USER_ID", "SESSION_ID", "EVENT_TS", "SCREEN_BUCKET"]].copy()
    ui_norm["LABEL"] = "ui:" + ui_norm["SCREEN_BUCKET"].astype(str)

    cols = ["USER_ID", "SESSION_ID", "EVENT_TS", "LABEL"]
    combined = pd.concat([gp[cols], ui_norm[cols]], ignore_index=True)
    combined["USER_ID"]    = combined["USER_ID"].astype(str)
    combined["SESSION_ID"] = combined["SESSION_ID"].astype(str)
    combined["EVENT_TS"]   = pd.to_datetime(combined["EVENT_TS"])
    combined = combined.sort_values(["USER_ID", "SESSION_ID", "EVENT_TS"]).reset_index(drop=True)

    # Collapse consecutive same-label rows per (user, session). Compare
    # to the row above within the group — keep only rows where the
    # label changed.
    same_session = (combined["USER_ID"].eq(combined["USER_ID"].shift())
                    & combined["SESSION_ID"].eq(combined["SESSION_ID"].shift()))
    same_label   = combined["LABEL"].eq(combined["LABEL"].shift())
    combined = combined[~(same_session & same_label)].copy()

    combined["STEP_IDX"] = combined.groupby(["USER_ID", "SESSION_ID"]).cumcount() + 1
    # build_links expects EVENT_LABEL; the LABEL column already has
    # the ui-prefixed form. Rename so the shared helper Just Works.
    combined["EVENT_LABEL"] = combined["LABEL"]
    return combined.drop(columns=["LABEL"])


with st.status("Loading combined flow…", expanded=True) as status:
    status.update(label="Fetching gameplay events…")
    events_all = fetch_events(cohort, tuple(platforms), tuple(countries))
    if events_all.empty:
        status.update(label="No gameplay events match filters.",
                      state="complete", expanded=False)
        st.info("No events match the current filters.")
        st.stop()
    events_all["USER_ID"] = events_all["USER_ID"].astype(str)
    st.write(f"• Pulled {len(events_all):,} gameplay event rows "
             f"({events_all['USER_ID'].nunique():,} players).")

    status.update(label="Fetching UI screen flow…")
    ui_all = fetch_ui(cohort)
    ui_all["USER_ID"]    = ui_all["USER_ID"].astype(str)
    ui_all["SESSION_ID"] = ui_all["SESSION_ID"].astype(str)
    st.write(f"• Pulled {len(ui_all):,} UI screen rows.")

    status.update(label="Anchoring sessions to GAME_LAUNCHED…")
    events_anchored, n_kept, n_excluded = anchor_to_launch(events_all)
    # Inner-join UI rows to anchored sessions only.
    keep_sessions = events_anchored[["USER_ID", "SESSION_ID"]].drop_duplicates()
    ui_anchored = ui_all.merge(keep_sessions, on=["USER_ID", "SESSION_ID"], how="inner")
    st.write(f"• Kept {n_kept:,} sessions, dropped {n_excluded:,} without "
             f"GAME_LAUNCHED at step 1. UI rows after join: {len(ui_anchored):,}.")

    def _process(events_subset: pd.DataFrame) -> tuple[pd.DataFrame, int, pd.DataFrame]:
        """Interleave + compress + build links for a given session subset.

        Returns (links, kept_session_count, combined_timeline). The
        combined timeline is passed back so the duration chart can
        operate on the same per-step granularity as the Sankey.
        """
        keep_sess = events_subset[["USER_ID", "SESSION_ID"]].drop_duplicates()
        ui_subset = ui_anchored.merge(keep_sess, on=["USER_ID", "SESSION_ID"], how="inner")
        combined = interleave_and_compress(events_subset, ui_subset)
        return build_links(combined), keep_sess.shape[0], combined

    if retention_segment == COMPARE_MODE:
        status.update(label="Splitting cohort into Returned vs Dropped…")
        ret = fetch_retention(cohort)
        keep_r, keep_d = retention_keepsets(ret)
        events_r = events_anchored[events_anchored["USER_ID"].isin(keep_r)]
        events_d = events_anchored[events_anchored["USER_ID"].isin(keep_d)]

        status.update(label="Interleaving + compressing both segments…")
        links_r, kept_r_n, combined_r = _process(events_r)
        links_d, kept_d_n, combined_d = _process(events_d)
        st.write(f"• Returned: {kept_r_n:,} sessions → {len(links_r):,} transitions · "
                 f"Dropped: {kept_d_n:,} sessions → {len(links_d):,} transitions.")

        status.update(label="Building side-by-side Sankeys + divergence chart…")
        fig_r = build_sankey(
            links_r, min_users=min_users, max_step=max_step,
            title=(f"<b>Returned D1+ · n={kept_r_n:,}</b>"
                   f"<br><sub>Widths = % of segment. Combined steps 1–{max_step}, "
                   f"links < {min_users} hidden.</sub>"),
            height=820, segment_size=kept_r_n,
        )
        fig_d = build_sankey(
            links_d, min_users=min_users, max_step=max_step,
            title=(f"<b>Dropped after D0 · n={kept_d_n:,}</b>"
                   f"<br><sub>Widths = % of segment. Combined steps 1–{max_step}, "
                   f"links < {min_users} hidden.</sub>"),
            height=820, segment_size=kept_d_n,
        )
        diff_fig = divergence_chart(links_r, links_d,
                                    max_step=max_step, min_link_users=min_users)

        status.update(label="Computing step durations…")
        dur_r = compute_step_durations(combined_r)
        dur_d = compute_step_durations(combined_d)
        dur_fig = duration_compare_chart(dur_r, dur_d,
                                         max_step=max_step, min_users=min_users)

        status.update(label="Done.", state="complete", expanded=False)

        col_r, col_d = st.columns(2)
        with col_r:
            st.plotly_chart(fig_r, use_container_width=True)
        with col_d:
            st.plotly_chart(fig_d, use_container_width=True)

        st.divider()
        if diff_fig is None:
            st.info("Not enough volume to build a divergence chart at the "
                    "current `min_users` threshold — drop the slider.")
        else:
            st.plotly_chart(diff_fig, use_container_width=True)

        st.divider()
        if dur_fig is None:
            st.info("Not enough volume to build the duration chart at the "
                    "current `min_users` threshold — drop the slider.")
        else:
            st.plotly_chart(dur_fig, use_container_width=True)

    else:
        events = events_anchored
        if retention_segment != "All players":
            status.update(label=f"Applying retention segment: {retention_segment}…")
            ret = fetch_retention(cohort)
            keep_r, keep_d = retention_keepsets(ret)
            keep_set = keep_r if retention_segment == "Returned D1+" else keep_d
            before = events["USER_ID"].nunique()
            events = events[events["USER_ID"].isin(keep_set)]
            st.write(f"• In segment {retention_segment}: {len(keep_set):,} · "
                     f"intersected with events fact: {events['USER_ID'].nunique():,} "
                     f"(dropped {before - events['USER_ID'].nunique():,} of {before:,}).")
            if events.empty:
                status.update(label=f"No players in the {retention_segment} segment.",
                              state="complete", expanded=False)
                st.info(f"No players in the **{retention_segment}** segment.")
                st.stop()

        status.update(label="Interleaving + compressing combined timeline…")
        links, kept_session_n, combined = _process(events)
        st.write(f"• Built {len(links):,} unique transitions over {kept_session_n:,} sessions.")

        status.update(label="Building combined-flow Sankey…")
        n_players = events["USER_ID"].nunique()
        seg_note  = "" if retention_segment == "All players" else f" · segment: {retention_segment}"
        title = (f"<b>Combined Flow · {cohort.strftime('%Y-%m')} cohort · n={n_players:,}{seg_note}</b>"
                 f"<br><sub>Gameplay events + UI screen flow interleaved by event_ts. "
                 f"UI rows are muted slate; consecutive same-label rows collapsed. "
                 f"Steps 1–{max_step} shown; links < {min_users} hidden.</sub>")
        fig = build_sankey(links, min_users=min_users, max_step=max_step,
                           title=title, height=1000)

        status.update(label="Computing step durations…")
        durations = compute_step_durations(combined)
        dur_fig = duration_chart(durations, max_step=max_step, min_users=min_users)

        status.update(label="Done.", state="complete", expanded=False)
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        if dur_fig is None:
            st.info("Not enough volume to build the duration chart at the "
                    "current `min_users` threshold — drop the slider.")
        else:
            st.plotly_chart(dur_fig, use_container_width=True)

with st.expander("How this view is built"):
    st.markdown(
        """
- **Sources:** `FCT_NEW_PLAYER_FIRST_SESSION_EVENTS` (gameplay) and
  `FCT_NEW_PLAYER_UI_SCREEN_FLOW` (UI screens). Both already filter to
  the first heartbeat-based session per new player.
- **Interleave:** rows from both facts are concatenated and sorted by
  `(user_id, session_id, event_ts)`. UI rows get a `ui:` prefix on the
  label so they can't collide with gameplay event_labels.
- **Compression:** consecutive rows with the same label inside one
  session are collapsed to a single node. Five pause-button toggles
  in a row become one `pausemenu` node, which reads as "menu thrash"
  rather than as five hops.
- **Anchor:** sessions without a `GAME_LAUNCHED` event at gameplay
  step 1 are excluded — these are telemetry gaps where the launch
  event didn't land but heartbeats did, and they'd otherwise start
  the combined timeline mid-flow.
- **Open question this view is meant to answer:** when a session
  ends in `lobby_solo` or mid-funnel, is the player actively quitting
  or are they cycling through menus until the heartbeat decays? If
  the latter, the right next step is flagging idle-quit vs active-quit
  on the existing first-session data. If we see clean restart
  patterns (close → reopen → second session), extend the universe
  to first day instead.
        """
    )
