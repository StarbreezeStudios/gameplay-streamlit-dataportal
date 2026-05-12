# CLAUDE.md

Project memory for the PD3 Tutorial Path Explorer.

## What this is

Streamlit app for STX-1125. Investigates the new-PD3-player first-session funnel — game launch → login → tutorial → heist — and the parallel UI-screen navigation flow. Two Sankey views, shared sidebar filters.

Lives in the `dataplatform-streamlit` monorepo at `projects/tutorial-path-explorer/`. Deployed to Helsinki port 8504 via the project's own Jenkinsfile.

## Data pipeline

Three incremental dbt models, source code in `payday3-dbt` at `models/analytics/tutorial_path/`:

```
int_new_player_first_session
   └─► fct_new_player_first_session_events    (event funnel)
   └─► fct_new_player_ui_screen_flow          (UI flow)
```

- Universe: new player + first heartbeat session only. Do not extend for later sessions; fork instead.
- Cohort scope: `cohort_month >= 2025-10-01`.
- Incremental window: each run re-processes the most recent 2 cohort months.
- Materialized in `PAYDAY3_PROD.DBT_ANALYTICS`, clustered by `cohort_month`.
- `event_label` in the events fact is the Sankey node identity. Don't rename without updating `shared/sankey.py::_default_color`.

## Filter convention

`app.py` populates `st.session_state` with `cohort_month`, `platforms`, `countries`, `min_users`, `max_step`. Each page reads from session_state — never re-renders its own copies of the filters. Keeps the two views in sync.

## Performance notes

- All Snowflake queries go through `shared.sf.run_query` which is `@st.cache_data(ttl=900)`.
- `_cached_snowflake_connection` has `ttl=1800` to stay under JWT expiry; `get_snowflake_connection()` re-checks with `SELECT 1` and reconnects on failure.
- The two fact tables are clustered by `cohort_month` so single-cohort queries prune aggressively.

## Snowflake gotchas (project-specific)

- Use 3-part fully qualified names everywhere in Streamlit queries.
- `ui_stack_updated` source is 2.83B rows. Never query it directly from the app — use `fct_new_player_ui_screen_flow`.
- Heist start/end match rate ~64% before Feb 2026, ~94% after. Old-cohort dropouts are inflated.
- `mission_result IS NULL` = mid-heist dropout (mapped to `HEIST_END_dropout` in `event_label`).
- `UNION ALL` at top level via MCP fails — wrap in subquery (project-level MCP gotcha, also applies if anyone runs the dbt SQL through Snowflake MCP).

## Adding a new page

1. Create `pages/<N>_<Name>.py`. Streamlit picks it up automatically.
2. Include the shared-path bootstrap idiom at the top — see existing pages.
3. Read filters from `st.session_state`, never re-define them.
4. Wrap any Snowflake query in `@st.cache_data(ttl=900, show_spinner="...")`.
5. Use `shared.sankey.build_sankey()` for any Sankey to keep colors/layout consistent.

## Open follow-ups

- Add an in-UI banner when a pre-Feb-2026 cohort is selected (telemetry-fix caveat).
- Multi-cohort view: month-over-month overlay of the same Sankey for tutorial regressions.
- Retention join: overlay `fact_daily_user_logins.is_day_n` on each node to show "of players who reached this node, % returning at D1/D7/D30".
