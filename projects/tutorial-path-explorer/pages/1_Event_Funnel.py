"""Event Funnel page — high-level session events Sankey."""
from __future__ import annotations
import pathlib, sys
_root = pathlib.Path(__file__).resolve()
while _root != _root.parent and not (_root / "shared" / "__init__.py").exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from shared.sf import run_query
from shared.sankey import build_sankey

st.set_page_config(page_title="Event Funnel · PD3 Tutorial Path", page_icon="🎮", layout="wide")

st.title("Event Funnel — first-session journey")

# Pull filter state set in app.py
cohort = st.session_state.get("cohort_month")
platforms  = st.session_state.get("platforms", [])
countries  = st.session_state.get("countries", [])

# Compare-mode sentinel — kept as a module constant so the branches that
# select it can't fall out of sync with the radio option label.
COMPARE_MODE = "Compare: Returned vs Dropped"

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


# ---------- helpers used by both single-segment and compare paths ---------
def anchor_to_launch(events: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Drop sessions whose step 1 isn't GAME_LAUNCHED.

    Returns (kept_events, n_kept_sessions, n_excluded_sessions). Excluded
    sessions are usually telemetry gaps where the launch event didn't
    land but heartbeats did.
    """
    all_users = events[["USER_ID", "SESSION_ID"]].drop_duplicates().shape[0]
    launched = events[
        (events["STEP_IDX"] == 1) & (events["EVENT_LABEL"] == "GAME_LAUNCHED")
    ][["USER_ID", "SESSION_ID"]].drop_duplicates()
    kept = events.merge(launched, on=["USER_ID", "SESSION_ID"], how="inner")
    return kept, launched.shape[0], all_users - launched.shape[0]


def build_links(events: pd.DataFrame) -> pd.DataFrame:
    """Convert an event timeline into a (FROM_IDX, FROM_LABEL, TO_LABEL, N_USERS) edge table.

    Passes labels through as-is. Each distinct heist gets its own node
    so we can see exactly which heist players move into after the
    tutorial — the `min_users` slider in build_sankey handles density.
    """
    events_sorted = events.sort_values(["USER_ID", "SESSION_ID", "STEP_IDX"]).copy()
    events_sorted["LABEL"] = events_sorted["EVENT_LABEL"]
    events_sorted["TO_LABEL"] = events_sorted.groupby(["USER_ID", "SESSION_ID"])["LABEL"].shift(-1)
    events_sorted["TO_LABEL"] = events_sorted["TO_LABEL"].fillna("<end>")
    return (
        events_sorted.groupby(["STEP_IDX", "LABEL", "TO_LABEL"]).size()
        .reset_index(name="N_USERS")
        .rename(columns={"STEP_IDX": "FROM_IDX", "LABEL": "FROM_LABEL"})
    )


def retention_keepsets(ret: pd.DataFrame) -> tuple[set[str], set[str]]:
    """Coerce the retention flags to int, return (returned_set, dropped_set)."""
    ret = ret.copy()
    ret["RETURNED_AFTER_D0"] = pd.to_numeric(ret["RETURNED_AFTER_D0"], errors="coerce").fillna(0).astype(int)
    ret["JUDGEABLE"]         = pd.to_numeric(ret["JUDGEABLE"],         errors="coerce").fillna(0).astype(int)
    ret["USER_ID"]           = ret["USER_ID"].astype(str)
    returned = set(ret.loc[ret["RETURNED_AFTER_D0"] == 1, "USER_ID"])
    dropped  = set(ret.loc[(ret["RETURNED_AFTER_D0"] == 0) & (ret["JUDGEABLE"] == 1), "USER_ID"])
    return returned, dropped


def divergence_chart(links_r: pd.DataFrame, links_d: pd.DataFrame,
                     *, max_step: int, top_n: int = 20,
                     min_link_users: int = 80) -> go.Figure | None:
    """Show the top-N step-to-step transitions where the two segments diverge most.

    For each (FROM_IDX, FROM_LABEL, TO_LABEL), compute the % of users at that
    upstream node who flowed to TO_LABEL in each segment, then rank by
    absolute percentage-point delta. The chart answers "where in the
    funnel do Returned vs Dropped players actually behave differently?"
    """
    lr = links_r[links_r["FROM_IDX"] <= max_step].copy()
    ld = links_d[links_d["FROM_IDX"] <= max_step].copy()
    if lr.empty or ld.empty:
        return None
    lr["pct"] = lr["N_USERS"] / lr.groupby(["FROM_IDX", "FROM_LABEL"])["N_USERS"].transform("sum") * 100
    ld["pct"] = ld["N_USERS"] / ld.groupby(["FROM_IDX", "FROM_LABEL"])["N_USERS"].transform("sum") * 100

    merged = lr.merge(
        ld, on=["FROM_IDX", "FROM_LABEL", "TO_LABEL"], how="outer", suffixes=("_R", "_D")
    ).fillna(0)
    merged["pp_delta"]  = merged["pct_R"] - merged["pct_D"]
    merged["abs_delta"] = merged["pp_delta"].abs()
    # Require some absolute volume in at least one segment, so we don't
    # rank-promote a 0.5%-vs-0% split that's just noise.
    merged = merged[(merged["N_USERS_R"] >= min_link_users) | (merged["N_USERS_D"] >= min_link_users)]
    if merged.empty:
        return None
    top = merged.nlargest(top_n, "abs_delta").sort_values("pp_delta")
    top["label"] = ("step " + top["FROM_IDX"].astype(int).astype(str)
                    + ": " + top["FROM_LABEL"] + " → " + top["TO_LABEL"])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=top["label"], x=top["pct_R"], name="Returned D1+", orientation="h",
        marker_color="#27ae60",
        hovertemplate=("%{y}<br>%{x:.1f}% of Returned reached this branch"
                       "<br>(%{customdata:,} players)<extra></extra>"),
        customdata=top["N_USERS_R"].astype(int),
    ))
    fig.add_trace(go.Bar(
        y=top["label"], x=top["pct_D"], name="Dropped after D0", orientation="h",
        marker_color="#c0392b",
        hovertemplate=("%{y}<br>%{x:.1f}% of Dropped reached this branch"
                       "<br>(%{customdata:,} players)<extra></extra>"),
        customdata=top["N_USERS_D"].astype(int),
    ))
    fig.update_layout(
        title=("<b>Where the two segments diverge</b><br>"
               "<sub>Top %d step-to-step transitions ranked by |%% Returned − %% Dropped|. "
               "Bar = share of players at the upstream node who took that branch. "
               "Requires ≥ %d players on at least one side.</sub>" % (top_n, min_link_users)),
        barmode="group",
        height=max(420, 30 * len(top) + 160),
        margin=dict(l=10, r=10, t=90, b=10),
        font=dict(size=13, family="Inter, system-ui, sans-serif"),
        legend=dict(orientation="h", y=-0.06, x=0),
        yaxis=dict(autorange="reversed"),  # largest delta at top
        xaxis=dict(title="% of players at upstream node"),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig


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
        fig_r = build_sankey(
            links_r, min_users=min_users, max_step=max_step,
            title=(f"<b>Returned D1+ · n={kept_r:,}</b>"
                   f"<br><sub>Steps 1–{max_step} shown; links < {min_users} hidden.</sub>"),
            height=720,
        )
        fig_d = build_sankey(
            links_d, min_users=min_users, max_step=max_step,
            title=(f"<b>Dropped after D0 · n={kept_d:,}</b>"
                   f"<br><sub>Steps 1–{max_step} shown; links < {min_users} hidden.</sub>"),
            height=720,
        )
        diff_fig = divergence_chart(links_r, links_d,
                                    max_step=max_step, min_link_users=min_users)
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

        status.update(label="Done.", state="complete", expanded=False)
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
