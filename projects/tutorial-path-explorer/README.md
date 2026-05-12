# tutorial-path-explorer

> Interactive Sankey explorer of the PD3 new-player first-session journey — game launch → login → tutorial → heist — plus the parallel UI-screen navigation flow.

- **Owner:** Irene Hjorth
- **Helsinki URL:** http://helsinki:8504
- **Jira:** [STX-1125](https://starbreeze.atlassian.net/browse/STX-1125)
- **Data:** three incremental dbt models in `payday3-dbt` (`int_new_player_first_session`, `fct_new_player_first_session_events`, `fct_new_player_ui_screen_flow`) → `PAYDAY3_PROD.DBT_ANALYTICS`.

## Pages

| Path | Purpose |
|---|---|
| `app.py` | Landing + shared sidebar filters + top-line conversion metrics |
| `pages/1_Event_Funnel.py` | High-level session-event Sankey (launch → login → tutorial → heist) |
| `pages/2_UI_Screen_Flow.py` | Screen-by-screen UI navigation Sankey + screen-time leaderboard |

## Local dev

Run from the **monorepo root** (not from this folder):

```bash
cd dataplatform-streamlit
python3 -m venv .venv && source .venv/bin/activate
pip install -r projects/tutorial-path-explorer/requirements.txt

# auth (pick one):
#   (a) SSO via browser
cp projects/tutorial-path-explorer/.streamlit/secrets.example.toml \
   projects/tutorial-path-explorer/.streamlit/secrets.toml
#   then edit `user` in the file
#
#   (b) key-pair
mkdir -p projects/tutorial-path-explorer/keys
cp ~/path/to/your_key.p8 projects/tutorial-path-explorer/keys/snowflake_key.p8
#   then uncomment `private_key_path` in secrets.toml

cd projects/tutorial-path-explorer
streamlit run app.py
```

## Deployment

Push to `main` → Dataportal Jenkins picks up `Jenkinsfile` and deploys to `/opt/tutorial-path-explorer` on Helsinki, port 8504.

## Caveats

- **Heist match rate** is ~64% before Feb 2026 and ~94% from Mar 2026 onwards (client telemetry fix). Pre-Feb-2026 cohorts overstate heist dropouts.
- **First-session boundary** uses `sessions.first_heartbeat_ts`. `GAME_LAUNCHED` typically precedes the first heartbeat by 30-120 s (load + auth); both retained.
- **~32% of new-player first sessions have zero `ui_stack_updated` events** — primarily console / pre-fix telemetry. Visible as the difference between user counts on the two pages.
