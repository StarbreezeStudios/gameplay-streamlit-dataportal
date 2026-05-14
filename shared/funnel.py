"""Shared funnel helpers for the Tutorial Path Explorer.

These started life inside `1_Event_Funnel.py` and got copied into the new
Combined Flow page — at which point keeping two copies in sync became
the bigger risk than the import indirection. Helpers stay here; the
pages stay thin and focused on layout + which data source they pull
from.

Each helper is pure: takes a DataFrame, returns a DataFrame or a Plotly
figure. No Streamlit calls. That keeps unit-testing trivial and lets a
page rearrange the call order without needing to dive into shared code.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


# Sentinel string for the fourth radio option. Kept here so the two pages
# that surface "Compare Returned vs Dropped" never drift on the label.
COMPARE_MODE = "Compare: Returned vs Dropped"


def anchor_to_launch(events: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Drop sessions whose step 1 isn't GAME_LAUNCHED.

    Returns (kept_events, n_kept_sessions, n_excluded_sessions). Excluded
    sessions are usually telemetry gaps where the launch event didn't
    land but heartbeats did. Operates on the gameplay-events fact's
    STEP_IDX, not the combined timeline — combined-view pages should
    anchor first, then merge in UI events.
    """
    all_users = events[["USER_ID", "SESSION_ID"]].drop_duplicates().shape[0]
    launched = events[
        (events["STEP_IDX"] == 1) & (events["EVENT_LABEL"] == "GAME_LAUNCHED")
    ][["USER_ID", "SESSION_ID"]].drop_duplicates()
    kept = events.merge(launched, on=["USER_ID", "SESSION_ID"], how="inner")
    return kept, launched.shape[0], all_users - launched.shape[0]


def build_links(events: pd.DataFrame) -> pd.DataFrame:
    """Convert an event timeline into a (FROM_IDX, FROM_LABEL, TO_LABEL, N_USERS) edge table.

    Expects columns USER_ID, SESSION_ID, STEP_IDX, EVENT_LABEL. Passes
    labels through as-is; each distinct heist / screen / etc. gets its
    own node and the caller's min_users filter handles density.
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
    """Coerce the retention flags to int, return (returned_set, dropped_set).

    Snowflake NUMBER columns can come back as decimal.Decimal in object
    dtype, where `== 1` works but is fragile across pandas versions —
    cast to int up front. USER_ID is also forced to str so the page
    can intersect with the (str) USER_ID column from the events fact
    via isin() without surprises.
    """
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
    """Top-N step-to-step transitions ranked by |% Returned − % Dropped|.

    Answers "where in the funnel do Returned vs Dropped players actually
    behave differently?" — the rigorous companion to the visual Sankey
    side-by-side.
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
        yaxis=dict(autorange="reversed"),
        xaxis=dict(title="% of players at upstream node"),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig


def _format_duration(seconds: float) -> str:
    """Compact human-readable duration for bar text and tooltips."""
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 90 * 60:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"


def compute_step_durations(events: pd.DataFrame) -> pd.DataFrame:
    """Per (user, session, step): seconds until the next event.

    Last event per session has no successor and is dropped. Non-positive
    durations are filtered defensively — events_sorted is deterministic
    via step_idx so they shouldn't occur, but a single negative value
    would skew a median fast.

    Caveat: EVENT_TS for SESSION_END is synthetic (heartbeat decay),
    so the duration at the previous real event before SESSION_END
    includes idle time, not active engagement. Page captions flag this.
    """
    e = events.sort_values(["USER_ID", "SESSION_ID", "STEP_IDX"]).copy()
    e["EVENT_TS"] = pd.to_datetime(e["EVENT_TS"])
    e["NEXT_TS"] = e.groupby(["USER_ID", "SESSION_ID"])["EVENT_TS"].shift(-1)
    e["DURATION_SEC"] = (e["NEXT_TS"] - e["EVENT_TS"]).dt.total_seconds()
    e = e.dropna(subset=["DURATION_SEC"])
    e = e[e["DURATION_SEC"] > 0]
    return e[["USER_ID", "SESSION_ID", "STEP_IDX", "EVENT_LABEL", "DURATION_SEC"]].rename(
        columns={"STEP_IDX": "FROM_IDX", "EVENT_LABEL": "FROM_LABEL"}
    )


def _aggregate_durations(d: pd.DataFrame, max_step: int) -> pd.DataFrame:
    """Per (step, label): median + p25/p75 + count, filtered to <= max_step."""
    d = d[d["FROM_IDX"] <= max_step]
    return (d.groupby(["FROM_IDX", "FROM_LABEL"])["DURATION_SEC"]
             .agg(median="median",
                  p25=lambda x: x.quantile(0.25),
                  p75=lambda x: x.quantile(0.75),
                  n="count")
             .reset_index())


def duration_chart(durations: pd.DataFrame, *, max_step: int, min_users: int,
                   top_n: int = 30) -> go.Figure | None:
    """Single-segment median-time-at-step chart with IQR error bars."""
    agg = _aggregate_durations(durations, max_step)
    agg = agg[agg["n"] >= min_users]
    if agg.empty:
        return None
    agg = (agg.sort_values(["FROM_IDX", "n"], ascending=[True, False])
              .head(top_n)
              .reset_index(drop=True))
    agg["label"] = ("step " + agg["FROM_IDX"].astype(int).astype(str)
                    + ": " + agg["FROM_LABEL"])

    fig = go.Figure(go.Bar(
        y=agg["label"], x=agg["median"], orientation="h",
        marker_color="#3498db",
        text=agg["median"].apply(_format_duration),
        textposition="outside",
        error_x=dict(type="data", symmetric=False,
                     array=(agg["p75"] - agg["median"]),
                     arrayminus=(agg["median"] - agg["p25"]),
                     color="rgba(52,73,94,0.45)"),
        customdata=list(zip(agg["n"], agg["p25"], agg["p75"])),
        hovertemplate=("<b>%{y}</b>"
                       "<br>Median: %{x:.1f}s"
                       "<br>IQR: %{customdata[1]:.1f}–%{customdata[2]:.1f}s"
                       "<br>n=%{customdata[0]:,}<extra></extra>"),
    ))
    fig.update_layout(
        title=("<b>Time spent at each step</b><br>"
               "<sub>Median seconds until the next event. Whiskers = IQR (p25–p75). "
               f"Top {top_n} nodes by player count (≥ {min_users}) shown, in funnel order. "
               "Duration before SESSION_END includes heartbeat-decay idle time, not active play.</sub>"),
        height=max(420, 26 * len(agg) + 160),
        margin=dict(l=10, r=10, t=110, b=10),
        font=dict(size=13, family="Inter, system-ui, sans-serif"),
        yaxis=dict(autorange="reversed"),
        xaxis=dict(title="seconds (log-ranged)", type="log"),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig


def compute_dwell_breakdown(durations: pd.DataFrame, *,
                            max_step: int,
                            threshold_sec: int,
                            quick_sec: int = 30) -> pd.DataFrame:
    """Per (step, label) split of dwell into quick / medium / AFK-suspect bands.

    `quick_sec` (default 30) — dwells shorter than this are "moved straight on"
    `threshold_sec` — dwells longer than this are flagged AFK-suspect

    Returns: FROM_IDX, FROM_LABEL, n, median, pct_quick, pct_med, pct_afk.
    Percentages sum to 100 within each row.
    """
    d = durations[durations["FROM_IDX"] <= max_step]
    if d.empty:
        return pd.DataFrame(columns=[
            "FROM_IDX", "FROM_LABEL", "n", "median",
            "pct_quick", "pct_med", "pct_afk"
        ])
    return (d.groupby(["FROM_IDX", "FROM_LABEL"])["DURATION_SEC"]
             .agg(
                 n="count",
                 median="median",
                 pct_quick=lambda s: (s < quick_sec).mean() * 100,
                 pct_med=lambda s: ((s >= quick_sec) & (s < threshold_sec)).mean() * 100,
                 pct_afk=lambda s: (s >= threshold_sec).mean() * 100,
             ).reset_index())


def dwell_tail_chart(durations: pd.DataFrame, *,
                     max_step: int, min_users: int,
                     threshold_sec: int, quick_sec: int = 30,
                     top_n: int = 30) -> go.Figure | None:
    """Stacked horizontal bars: dwell-time distribution per (step, label).

    Three colored bands per row — green `< quick_sec`, amber `quick_sec–threshold`,
    red `> threshold` (AFK-suspect). Sorted by % AFK descending so the worst
    offenders surface at the top. Answers "where do players linger long enough
    that they're probably AFK or stuck?"
    """
    agg = compute_dwell_breakdown(durations,
                                  max_step=max_step,
                                  threshold_sec=threshold_sec,
                                  quick_sec=quick_sec)
    agg = agg[agg["n"] >= min_users]
    if agg.empty:
        return None
    agg = (agg.sort_values("pct_afk", ascending=False)
              .head(top_n).reset_index(drop=True))
    agg["label"] = ("step " + agg["FROM_IDX"].astype(int).astype(str)
                    + ": " + agg["FROM_LABEL"])

    common_hover = (
        "<b>%{y}</b>"
        "<br>%{x:.1f}% in this band"
        "<br>n=%{customdata[0]:,} dwells · median %{customdata[1]:.1f}s"
        "<extra>%{fullData.name}</extra>"
    )
    cd = list(zip(agg["n"].astype(int), agg["median"].astype(float)))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name=f"< {quick_sec}s (moved on)",
        y=agg["label"], x=agg["pct_quick"], orientation="h",
        marker_color="#27ae60",
        customdata=cd, hovertemplate=common_hover,
    ))
    fig.add_trace(go.Bar(
        name=f"{quick_sec}s–{threshold_sec}s (lingered)",
        y=agg["label"], x=agg["pct_med"], orientation="h",
        marker_color="#f39c12",
        customdata=cd, hovertemplate=common_hover,
    ))
    fig.add_trace(go.Bar(
        name=f"> {threshold_sec}s (AFK-suspect)",
        y=agg["label"], x=agg["pct_afk"], orientation="h",
        marker_color="#c0392b",
        customdata=cd, hovertemplate=common_hover,
    ))
    fig.update_layout(
        title=("<b>Dwell distribution at each step</b><br>"
               "<sub>Each bar is a (step, label) node; segments split by how "
               f"long players stayed before the next event. Sorted by % "
               f"dwelling longer than {threshold_sec}s. "
               f"Top {top_n} nodes by player count (≥ {min_users}). "
               "Long red = candidate AFK / stuck pattern; long amber = "
               "lingering but not extreme.</sub>"),
        barmode="stack",
        height=max(420, 26 * len(agg) + 160),
        margin=dict(l=10, r=10, t=110, b=10),
        font=dict(size=13, family="Inter, system-ui, sans-serif"),
        legend=dict(orientation="h", y=-0.04, x=0),
        yaxis=dict(autorange="reversed"),
        xaxis=dict(title="% of dwells", range=[0, 100], ticksuffix="%"),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig


def dwell_delta_chart(dur_r: pd.DataFrame, dur_d: pd.DataFrame, *,
                      max_step: int, min_users: int,
                      threshold_sec: int,
                      top_n: int = 20) -> go.Figure | None:
    """Compare-mode dwell-tail divergence.

    For each (step, label), shows %_AFK_Returned vs %_AFK_Dropped side by side.
    Ranked by `|pct_afk_D − pct_afk_R|` descending so the nodes where Dropped
    players AFK significantly more (or less) than Returned float to the top.
    """
    agg_r = compute_dwell_breakdown(dur_r, max_step=max_step, threshold_sec=threshold_sec)
    agg_d = compute_dwell_breakdown(dur_d, max_step=max_step, threshold_sec=threshold_sec)
    merged = agg_r.merge(agg_d, on=["FROM_IDX", "FROM_LABEL"],
                         how="outer", suffixes=("_R", "_D")).fillna(0)
    merged = merged[(merged["n_R"] >= min_users) | (merged["n_D"] >= min_users)]
    if merged.empty:
        return None
    merged["delta"]     = merged["pct_afk_D"] - merged["pct_afk_R"]
    merged["abs_delta"] = merged["delta"].abs()
    top = (merged.nlargest(top_n, "abs_delta")
                 .sort_values("delta", ascending=True)
                 .reset_index(drop=True))
    top["label"] = ("step " + top["FROM_IDX"].astype(int).astype(str)
                    + ": " + top["FROM_LABEL"])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Returned D1+ (% AFK)",
        y=top["label"], x=top["pct_afk_R"], orientation="h",
        marker_color="#27ae60",
        customdata=top["n_R"].astype(int),
        hovertemplate=("<b>%{y}</b>"
                       "<br>%{x:.1f}% of Returned dwelled past threshold"
                       "<br>n=%{customdata:,}<extra></extra>"),
    ))
    fig.add_trace(go.Bar(
        name="Dropped after D0 (% AFK)",
        y=top["label"], x=top["pct_afk_D"], orientation="h",
        marker_color="#c0392b",
        customdata=top["n_D"].astype(int),
        hovertemplate=("<b>%{y}</b>"
                       "<br>%{x:.1f}% of Dropped dwelled past threshold"
                       "<br>n=%{customdata:,}<extra></extra>"),
    ))
    fig.update_layout(
        title=("<b>AFK-suspect dwell — Returned vs Dropped</b><br>"
               f"<sub>% of each segment that sat for more than {threshold_sec}s "
               "at this node before their next event. Sorted by the largest "
               "Returned-vs-Dropped gap. Same value on both sides = both "
               "segments linger equally here (likely real friction); large "
               "gap = behavior-specific to one cohort.</sub>"),
        barmode="group",
        height=max(420, 30 * len(top) + 160),
        margin=dict(l=10, r=10, t=110, b=10),
        font=dict(size=13, family="Inter, system-ui, sans-serif"),
        legend=dict(orientation="h", y=-0.06, x=0),
        yaxis=dict(autorange="reversed"),
        xaxis=dict(title="% of segment over threshold", range=[0, 100], ticksuffix="%"),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig


def duration_compare_chart(dur_r: pd.DataFrame, dur_d: pd.DataFrame, *,
                           max_step: int, min_users: int,
                           top_n: int = 30) -> go.Figure | None:
    """Grouped-bar median-time chart for Compare mode."""
    agg_r = _aggregate_durations(dur_r, max_step).rename(
        columns={"median": "median_R", "n": "n_R", "p25": "p25_R", "p75": "p75_R"})
    agg_d = _aggregate_durations(dur_d, max_step).rename(
        columns={"median": "median_D", "n": "n_D", "p25": "p25_D", "p75": "p75_D"})
    if agg_r.empty and agg_d.empty:
        return None
    merged = agg_r.merge(agg_d, on=["FROM_IDX", "FROM_LABEL"], how="outer").fillna(0)
    merged = merged[(merged["n_R"] >= min_users) | (merged["n_D"] >= min_users)]
    if merged.empty:
        return None
    merged["n_combined"] = merged["n_R"] + merged["n_D"]
    merged = (merged.sort_values(["FROM_IDX", "n_combined"], ascending=[True, False])
                    .head(top_n)
                    .reset_index(drop=True))
    merged["label"] = ("step " + merged["FROM_IDX"].astype(int).astype(str)
                       + ": " + merged["FROM_LABEL"])
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Returned D1+", y=merged["label"], x=merged["median_R"], orientation="h",
        marker_color="#27ae60",
        text=merged["median_R"].apply(lambda s: _format_duration(s) if s > 0 else ""),
        textposition="outside",
        customdata=merged["n_R"].astype(int),
        hovertemplate=("<b>%{y}</b><br>Returned median: %{x:.1f}s"
                       "<br>n=%{customdata:,}<extra></extra>"),
    ))
    fig.add_trace(go.Bar(
        name="Dropped after D0", y=merged["label"], x=merged["median_D"], orientation="h",
        marker_color="#c0392b",
        text=merged["median_D"].apply(lambda s: _format_duration(s) if s > 0 else ""),
        textposition="outside",
        customdata=merged["n_D"].astype(int),
        hovertemplate=("<b>%{y}</b><br>Dropped median: %{x:.1f}s"
                       "<br>n=%{customdata:,}<extra></extra>"),
    ))
    fig.update_layout(
        title=("<b>Time spent at each step — Returned vs Dropped</b><br>"
               "<sub>Median seconds until the next event. Bars grouped by funnel position; "
               f"top {top_n} (step, label) pairs by combined volume (≥ {min_users} on at least one side). "
               "Longer Dropped bars suggest confusion / lost players; shorter Dropped bars suggest "
               "rage-quit or impatience.</sub>"),
        barmode="group",
        height=max(520, 30 * len(merged) + 160),
        margin=dict(l=10, r=10, t=110, b=10),
        font=dict(size=13, family="Inter, system-ui, sans-serif"),
        legend=dict(orientation="h", y=-0.04, x=0),
        yaxis=dict(autorange="reversed"),
        xaxis=dict(title="seconds (log-ranged)", type="log"),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig
