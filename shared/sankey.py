"""Shared Sankey builder used by both pages.

Convention: input is a DataFrame with columns (FROM_IDX, FROM_LABEL, TO_LABEL, N_USERS).
Each (step_idx, label) becomes a distinct node — gives the column-by-column layout.
"""

from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go


def _display_label(label: str) -> str:
    """Compact, human-readable form of a STX-1125 event label.

    The Sankey columns already encode the step ordinal, so we drop the `[N]`
    prefix entirely. We also strip the verbose family prefixes (`TUTORIAL_`,
    `HEIST_START_`, …) and replace common outcomes with glyphs so labels are
    short enough to render without overlap. Hover still surfaces the full
    original label for clarity.
    """
    fixed = {
        "GAME_LAUNCHED": "launch",
        "LOGIN_OK":      "login ✓",
        "LOGIN_FAIL":    "login ✗",
        "LOBBY_JOINED":  "lobby",            # legacy label (pre-LOBBY-split cohorts)
        "LOBBY_solo":    "lobby · solo",
        "LOBBY_2":       "lobby · 2",
        "LOBBY_3":       "lobby · 3",
        "LOBBY_4":       "lobby · 4 ⨯",       # full crew
        "LOBBY_other":   "lobby · ?",
        "SESSION_END":   "quit",
        "<end>":         "—",
    }
    if label in fixed:
        return fixed[label]
    if label.startswith("TUTORIAL_"):
        rest = label[len("TUTORIAL_"):]
        if "_" in rest:
            name, outcome = rest.rsplit("_", 1)
            mark = {"success": "✓", "fail": "✗", "unknown": "?",
                    "disconnect": "↯", "completed": "•"}.get(outcome, outcome)
            return f"{name} {mark}"
        return rest
    if label.startswith("PARTY_"):
        return "party·" + label[len("PARTY_"):]
    if label.startswith("MATCHMAKING_"):
        return "match·" + label[len("MATCHMAKING_"):]
    if label.startswith("HEIST_START_"):
        return "▶ " + label[len("HEIST_START_"):]
    if label.startswith("HEIST_END_"):
        outcome = label[len("HEIST_END_"):]
        mark = {"success": "✓", "fail": "✗", "dropout": "…",
                "disconnect": "↯"}.get(outcome, outcome)
        return f"end {mark}"
    return label


def _default_color(label: str) -> str:
    """Sensible default coloring based on label prefix."""
    L = label.lower()
    if label == "<end>":                       return "#ecf0f1"
    if label == "SESSION_END":                 return "#7f8c8d"
    if label == "GAME_LAUNCHED":               return "#7f8c8d"
    if label == "LOGIN_OK":                    return "#27ae60"
    if label == "LOGIN_FAIL":                  return "#c0392b"
    if label.startswith("TUTORIAL_"):
        if label.endswith("_completed"):       return "#3498db"  # neutral: combat walkthrough
        if label.endswith("_success"):         return "#3498db"
        if label.endswith("_fail"):            return "#e67e22"
        return "#95a5a6"
    if label.startswith("PARTY_"):             return "#8e44ad"
    if label.startswith("MATCHMAKING_"):       return "#16a085"
    if label == "LOBBY_JOINED":                return "#f39c12"  # legacy single-node
    if label == "LOBBY_solo":                  return "#fde3a7"  # palest — alone
    if label == "LOBBY_2":                     return "#f9c270"
    if label == "LOBBY_3":                     return "#f39c12"
    if label == "LOBBY_4":                     return "#d35400"  # darkest — full crew
    if label == "LOBBY_other":                 return "#bdc3c7"
    if label.startswith("HEIST_START"):        return "#d35400"
    if label == "HEIST_END_success":           return "#2ecc71"
    if label == "HEIST_END_fail":              return "#e74c3c"
    if label == "HEIST_END_disconnect":        return "#95a5a6"
    if label == "HEIST_END_dropout":           return "#34495e"
    # UI-screen palette
    if label in {"joboverview"}:               return "#e67e22"
    if label in {"crime.net"}:                 return "#d35400"
    if label in {"crimenettutorial"}:          return "#9b59b6"
    if label in {"playscreen"}:                return "#3498db"
    if label in {"in_game"}:                   return "#2ecc71"
    if label in {"lobby_ui"}:                  return "#f39c12"
    if label in {"quickplay"}:                 return "#16a085"
    if label in {"serverbrowser"}:             return "#1abc9c"
    if label in {"inventory"}:                 return "#7f8c8d"
    if label in {"customization"}:             return "#95a5a6"
    if label in {"blackmarket"}:               return "#34495e"
    if label in {"skills", "quests"}:          return "#9b59b6"
    if label in {"pausemenu"}:                 return "#bdc3c7"
    if label in {"settings"}:                  return "#7f8c8d"
    if label in {"socialscreen", "chat"}:      return "#2980b9"
    if label in {"loginstate"}:                return "#c0392b"
    return "#cccccc"


def build_sankey(
    links: pd.DataFrame,
    *,
    min_users: int = 80,
    max_step: int = 10,
    title: str = "",
    height: int = 900,
    color_fn=None,
    segment_size: int | None = None,
) -> go.Figure:
    """Build a column-stacked Sankey figure.

    Parameters
    ----------
    links : DataFrame with columns FROM_IDX, FROM_LABEL, TO_LABEL, N_USERS
    min_users : drop links below this count (raw, in players)
    max_step  : truncate at this step_idx
    title     : figure title (HTML allowed for <br>/<sub>)
    color_fn  : optional override for node colors (label -> hex string)
    segment_size : if provided, link widths are rendered as % of this number
        instead of raw player counts (link width = N_USERS / segment_size * 100).
        Use to make two side-by-side Sankeys with different cohort totals
        visually comparable — both root flows render at the same width. The
        `min_users` filter and the player-count in the hover tooltip still
        operate on raw counts; only the visual width changes.
    """
    color_fn = color_fn or _default_color

    df = links[(links["N_USERS"] >= min_users) & (links["FROM_IDX"] <= max_step)].copy()
    if df.empty:
        fig = go.Figure()
        fig.update_layout(
            title=title or "No data",
            annotations=[dict(text="No links match current filters.",
                              showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")],
            height=height,
        )
        return fig

    node_keys: list[tuple[int, str]] = []
    idx_map: dict[tuple[int, str], int] = {}

    def node(i: int, lbl: str) -> int:
        k = (i, lbl)
        if k not in idx_map:
            idx_map[k] = len(node_keys)
            node_keys.append(k)
        return idx_map[k]

    # `val` drives visual width — scaled to % of segment when normalising
    # so two side-by-side Sankeys are directly width-comparable. `raw_val`
    # always carries the player count and is what min_users filters on
    # (above) and what link hover displays.
    src, tgt, val, raw_val = [], [], [], []
    for r in df.itertuples():
        s = node(int(r.FROM_IDX), r.FROM_LABEL)
        t = node(int(r.FROM_IDX) + 1, r.TO_LABEL)
        n = int(r.N_USERS)
        src.append(s); tgt.append(t)
        raw_val.append(n)
        val.append(n / segment_size * 100 if segment_size else n)

    # Iteratively prune orphan nodes: any node at step > root that has no
    # visible inbound flow. They appear when the min_users filter hides the
    # link that would have fed them, leaving a "source-only" node disconnected
    # from the funnel above it. (Root-level nodes are exempt — they are the
    # starting points and have no upstream by definition. `<end>` terminal
    # nodes are also kept because they only ever appear as targets.)
    #
    # Termination safety: an orphan that has no OUTBOUND links either —
    # e.g. a dangling node where every connecting link was already pruned —
    # would re-appear in `orphan_idxs` each pass without the `keep` filter
    # removing any links, so the loop would spin forever. We watch the
    # orphan set across iterations and break the moment it stops shrinking;
    # the reindex pass below then drops these stuck-orphan nodes for good.
    if node_keys:
        min_step = min(i for (i, _) in node_keys)
        prev_orphans: frozenset[int] | None = None
        while True:
            has_inbound = set(tgt)
            orphan_idxs = frozenset(
                i for i, (step, _) in enumerate(node_keys)
                if step > min_step and i not in has_inbound
            )
            if not orphan_idxs or orphan_idxs == prev_orphans:
                break
            prev_orphans = orphan_idxs
            keep = [k for k, s in enumerate(src) if s not in orphan_idxs]
            src = [src[k] for k in keep]
            tgt = [tgt[k] for k in keep]
            val = [val[k] for k in keep]
            raw_val = [raw_val[k] for k in keep]

        # Drop now-unreferenced nodes and reindex.
        referenced = set(src) | set(tgt)
        if len(referenced) < len(node_keys):
            old_to_new: dict[int, int] = {}
            new_node_keys: list[tuple[int, str]] = []
            for old_idx, key in enumerate(node_keys):
                if old_idx in referenced:
                    old_to_new[old_idx] = len(new_node_keys)
                    new_node_keys.append(key)
            node_keys = new_node_keys
            src = [old_to_new[i] for i in src]
            tgt = [old_to_new[i] for i in tgt]

    if not node_keys:
        fig = go.Figure()
        fig.update_layout(
            title=title or "No data",
            annotations=[dict(text="No links match current filters after orphan pruning.",
                              showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")],
            height=height,
        )
        return fig

    # Node hover always shows raw player counts, so accumulate from raw_val
    # rather than the (potentially scaled-to-%) val list.
    inbound  = {i: 0 for i in range(len(node_keys))}
    outbound = {i: 0 for i in range(len(node_keys))}
    for s, t, v in zip(src, tgt, raw_val):
        outbound[s] += v; inbound[t] += v

    n_cols = max(i for (i, _) in node_keys) + 1
    x_per_col = [(i + 0.5) / (n_cols + 1) for i in range(n_cols + 1)]
    node_x       = [x_per_col[i] for (i, _) in node_keys]
    node_label   = [_display_label(l) for (_i, l) in node_keys]
    node_full    = [l for (_i, l) in node_keys]              # original (for hover)
    node_step    = [i for (i, _) in node_keys]
    node_color   = [color_fn(l) for (_i, l) in node_keys]

    # Link hover shows raw player count from customdata; when scaled-to-%,
    # also surfaces the % of segment so the user sees both at once.
    if segment_size:
        link_hovertemplate = (
            "%{source.label} → %{target.label}"
            "<br>%{customdata:,} players (%{value:.2f}% of segment)<extra></extra>"
        )
    else:
        link_hovertemplate = (
            "%{source.label} → %{target.label}<br>%{customdata:,} players<extra></extra>"
        )

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            label=node_label,
            color=node_color,
            pad=14,
            thickness=18,
            x=node_x,
            customdata=[[node_step[i], node_full[i], inbound[i], outbound[i]]
                        for i in range(len(node_keys))],
            hovertemplate=("<b>%{customdata[1]}</b>"
                           "<br>step %{customdata[0]}"
                           "<br>in: %{customdata[2]:,}"
                           "<br>out: %{customdata[3]:,}<extra></extra>"),
        ),
        link=dict(
            source=src, target=tgt, value=val,
            customdata=raw_val,
            hovertemplate=link_hovertemplate,
            color="rgba(120,120,120,0.22)",
        ),
    ))
    fig.update_layout(
        title=title,
        font=dict(size=14, family="Inter, system-ui, sans-serif"),
        height=height,
        margin=dict(l=10, r=10, t=80, b=10),
        paper_bgcolor="white",
    )
    return fig
