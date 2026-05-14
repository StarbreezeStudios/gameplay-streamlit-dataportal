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
    options=["All players", "Returned D1+", "Dropped after D0", COMPARE_MODE],
    index=0,
    help=(
        "Split the cohort by whether players came back after their first-login day.\n\n"
        "• **Returned D1+** — logged in at least once on a calendar day after first-login.\n"
        "• **Dropped after D0** — no return; only counts users whose first-login was ≥1 day ago "
        "(otherwise retention can't be judged yet).\n"
        "• **Compare** — render both Sankeys side-by-side plus a per-transition divergence chart.\n"
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
        SELECT USER_ID, SESSION_ID, STEP_IDX, EVENT_TYPE, EVENT_LABEL, EVENT_TS
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


# All the heavy work below sits inside one st.status block so the user
# sees what's happening instead of staring at a blank page. Each .update()
# bumps the visible label; on a cold container the whole chain takes
# ~15s for a typical cohort. show_spinner on the cached fetches gives
# additional in-place feedback while Snowflake is the bottleneck.
with st.status("Loading event funnel…", expanded=True) as status:
    status.update(label="Fetching event timeline from Snowflake…")
    events_all = fetch_events(cohort, tuple(platforms), tuple(countries))
    if events_all.empty:
        status.update(label="No events match the current filters.",
                      state="complete", expanded=False)
        st.info("No events match the current filters.")
        st.stop()
    events_all["USER_ID"] = events_all["USER_ID"].astype(str)
    st.write(f"• Pulled {len(events_all):,} event rows "
             f"({events_all['USER_ID'].nunique():,} unique players).")

    # ---------- branch by mode ---------------------------------------------
    if retention_segment == COMPARE_MODE:
        status.update(label="Splitting cohort into Returned vs Dropped…")
        ret = fetch_retention(cohort)
        keep_r, keep_d = retention_keepsets(ret)
        events_r = events_all[events_all["USER_ID"].isin(keep_r)].copy()
        events_d = events_all[events_all["USER_ID"].isin(keep_d)].copy()
        st.write(f"• Returned D1+: {events_r['USER_ID'].nunique():,} players · "
                 f"Dropped after D0: {events_d['USER_ID'].nunique():,} players.")

        status.update(label="Anchoring journeys at GAME_LAUNCHED…")
        events_r, kept_r, excl_r = anchor_to_launch(events_r)
        events_d, kept_d, excl_d = anchor_to_launch(events_d)
        st.write(f"• Returned: kept {kept_r:,} sessions ({excl_r:,} excluded) · "
                 f"Dropped: kept {kept_d:,} sessions ({excl_d:,} excluded).")

        status.update(label="Aggregating step-to-step transitions for both segments…")
        links_r = build_links(events_r)
        links_d = build_links(events_d)
        st.write(f"• Built {len(links_r):,} returned-side and "
                 f"{len(links_d):,} dropped-side transitions.")

        status.update(label="Building side-by-side Sankeys + divergence chart…")
        # `segment_size` rescales link widths to % of segment so the two
        # Sankeys are directly comparable (both root flows render at the
        # same visual width). Filter threshold and hover counts stay raw.
        fig_r = build_sankey(
            links_r, min_users=min_users, max_step=max_step,
            title=(f"<b>Returned D1+ · n={kept_r:,}</b>"
                   f"<br><sub>Widths = % of segment so this side is "
                   f"row-comparable with Dropped. Steps 1–{max_step}, "
                   f"links < {min_users} hidden.</sub>"),
            height=720, segment_size=kept_r,
        )
        fig_d = build_sankey(
            links_d, min_users=min_users, max_step=max_step,
            title=(f"<b>Dropped after D0 · n={kept_d:,}</b>"
                   f"<br><sub>Widths = % of segment so this side is "
                   f"row-comparable with Returned. Steps 1–{max_step}, "
                   f"links < {min_users} hidden.</sub>"),
            height=720, segment_size=kept_d,
        )
        diff_fig = divergence_chart(links_r, links_d,
                                    max_step=max_step, min_link_users=min_users)

        status.update(label="Computing step durations…")
        dur_r = compute_step_durations(events_r)
        dur_d = compute_step_durations(events_d)
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
            st.info("Not enough volume on either side to build a divergence chart "
                    "at the current `min_users` threshold — drop the slider.")
        else:
            st.plotly_chart(diff_fig, use_container_width=True)

        st.divider()
        if dur_fig is None:
            st.info("Not enough volume to build the duration chart at the current "
                    "`min_users` threshold — drop the slider.")
        else:
            st.plotly_chart(dur_fig, use_container_width=True)

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
                """
            )

        with st.expander("Per-transition divergence table"):
            # Same dataset that drives the divergence chart, but full-length and
            # sortable. Useful for spotting medium-rank divergences the top-20
            # chart truncates away.
            lr = links_r[links_r["FROM_IDX"] <= max_step].copy()
            ld = links_d[links_d["FROM_IDX"] <= max_step].copy()
            lr["pct"] = lr["N_USERS"] / lr.groupby(["FROM_IDX", "FROM_LABEL"])["N_USERS"].transform("sum") * 100
            ld["pct"] = ld["N_USERS"] / ld.groupby(["FROM_IDX", "FROM_LABEL"])["N_USERS"].transform("sum") * 100
            tbl = (lr.merge(ld, on=["FROM_IDX", "FROM_LABEL", "TO_LABEL"],
                            how="outer", suffixes=("_R", "_D"))
                     .fillna(0))
            tbl = tbl[(tbl["N_USERS_R"] >= min_users) | (tbl["N_USERS_D"] >= min_users)]
            tbl["pp_delta"] = (tbl["pct_R"] - tbl["pct_D"]).round(1)
            tbl["pct_R"] = tbl["pct_R"].round(1)
            tbl["pct_D"] = tbl["pct_D"].round(1)
            tbl = tbl.sort_values("pp_delta", key=lambda s: s.abs(), ascending=False)
            st.dataframe(
                tbl[["FROM_IDX", "FROM_LABEL", "TO_LABEL",
                     "N_USERS_R", "pct_R", "N_USERS_D", "pct_D", "pp_delta"]]
                   .rename(columns={
                       "N_USERS_R": "n_Returned", "pct_R": "%_Returned",
                       "N_USERS_D": "n_Dropped",  "pct_D": "%_Dropped",
                   }),
                use_container_width=True, hide_index=True,
            )

    else:
        # ---------- single-segment path (existing behaviour) --------------
        events = events_all
        retention_dropped = 0
        if retention_segment != "All players":
            status.update(label=f"Applying retention segment: {retention_segment}…")
            ret = fetch_retention(cohort)
            keep_r, keep_d = retention_keepsets(ret)
            keep_set = keep_r if retention_segment == "Returned D1+" else keep_d
            before = events["USER_ID"].nunique()
            events = events[events["USER_ID"].isin(keep_set)]
            retention_dropped = before - events["USER_ID"].nunique()
            st.write(
                f"• In segment **{retention_segment}**: {len(keep_set):,} · "
                f"intersected with events fact: {events['USER_ID'].nunique():,} "
                f"(dropped {retention_dropped:,} of {before:,} pre-filter)."
            )
            if events.empty:
                status.update(label=f"No players in the {retention_segment} segment.",
                              state="complete", expanded=False)
                st.info(f"No players in the **{retention_segment}** segment for this cohort.")
                st.stop()

        status.update(label="Anchoring journeys at GAME_LAUNCHED…")
        events, kept_users, dropped = anchor_to_launch(events)
        st.write(f"• Kept {kept_users:,} sessions ({dropped:,} excluded, no GAME_LAUNCHED at step 1).")

        status.update(label="Aggregating step-to-step transitions…")
        links = build_links(events)
        st.write(f"• Built {len(links):,} unique transitions.")

        status.update(label="Building Sankey figure…")
        n_players = events["USER_ID"].nunique()
        drop_note = (f" · {dropped:,} sessions excluded (no GAME_LAUNCHED at step 1)"
                     if dropped else "")
        seg_note = "" if retention_segment == "All players" else f" · segment: {retention_segment}"
        title = (f"<b>Event Funnel · {cohort.strftime('%Y-%m')} cohort · n={n_players:,}{seg_note}{drop_note}</b>"
                 f"<br><sub>Every journey starts at GAME_LAUNCHED. "
                 f"Each column = N-th event in the player's first session. "
                 f"Steps 1–{max_step} shown; links < {min_users} players hidden.</sub>")
        fig = build_sankey(links, min_users=min_users, max_step=max_step, title=title, height=900)

        status.update(label="Computing step durations…")
        durations = compute_step_durations(events)
        dur_fig = duration_chart(durations, max_step=max_step, min_users=min_users)

        status.update(label="Done.", state="complete", expanded=False)
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        if dur_fig is None:
            st.info("Not enough volume to build the duration chart at the current "
                    "`min_users` threshold — drop the slider.")
        else:
            st.plotly_chart(dur_fig, use_container_width=True)

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
