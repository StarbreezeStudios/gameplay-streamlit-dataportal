# Conventions

How to add, run, and deploy a Streamlit project in this monorepo.

## Project layout

Each project lives under `projects/<project-name>/` and is fully self-contained on the deploy side (its own Dockerfile, docker-compose, requirements). Deploy orchestration lives in the **root** `Jenkinsfile` (see Jenkins integration below). The only thing projects share is `shared/` at the repo root — Snowflake helpers, the Sankey builder, and any other reusable utilities.

```
projects/<project-name>/
├── README.md                  # what the app does + owner + link
├── CLAUDE.md                  # optional: project memory for Claude / Codex
├── app.py                     # landing page (Streamlit entry)
├── pages/                     # one file per page; Streamlit auto-discovers
├── requirements.txt           # pinned project deps
├── Dockerfile                 # multi-stage build, copies shared/ + project
├── docker-compose.yaml        # local stack
│   (no per-project Jenkinsfile — root Jenkinsfile dispatches; see below)
├── .streamlit/
│   ├── config.toml            # theme + server flags (committed)
│   └── secrets.example.toml   # template (committed); real `secrets.toml` gitignored
└── .env.example               # template (committed); real `.env` gitignored
```

## Naming

| Element | Rule |
|---|---|
| Project folder | `kebab-case` describing the app (e.g. `tutorial-path-explorer`). |
| Streamlit container | Same as the folder. |
| Helsinki stack dir | `/opt/<project-name>` (matches the folder). |
| Helsinki port | Allocate from the table below. |
| Docker image tag | `<project-name>:latest` plus `<project-name>:<git-short-sha>`. |

## Port allocation

Helsinki internal server. **Always check this table before deploying a new project** and update it in the same PR.

| Port | Project | Notes |
|---|---|---|
| 8501 | budget tracker | pre-existing |
| 8502 | revenue-target | pre-existing |
| 8503 | community-dashboard | pre-existing |
| 8504 | tutorial-path-explorer | STX-1125 |
| 8505+ | _available_ | |
| 3030 | Metabase | not a project here |

## Imports — `shared/`

The `shared/` package is at the repo root. All projects use the same bootstrap idiom at the top of `app.py` and every `pages/*.py`:

```python
from __future__ import annotations
import pathlib, sys
_root = pathlib.Path(__file__).resolve()
while _root != _root.parent and not (_root / "shared" / "__init__.py").exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

from shared.sf import run_query  # now resolvable
```

This works in three environments:
- Local dev (`streamlit run app.py` from `projects/<name>/`)
- Local docker (`docker compose up` from `projects/<name>/`, build context `../..`)
- Jenkins / Helsinki (`shared/` is COPYed into `/app/shared/` by the Dockerfile)

The `_template` project has the bootstrap pre-pasted — copy it.

## Secrets / auth

| Where | What | Auth |
|---|---|---|
| Local dev | `.streamlit/secrets.toml` (gitignored) | SSO (default) or key-pair (set `private_key_path`) |
| Container | env vars from `.env` | Key-pair only |
| Jenkins / Helsinki | Jenkins credential `snowflake_prod_credentials` → writes `.env` + key file at deploy time | Key-pair |

The two auth paths converge in `shared/sf.py` — anything that sets `SNOWFLAKE_ACCOUNT` env-var triggers key-pair; otherwise it falls back to `st.secrets` and SSO.

`.streamlit/secrets.toml`, `.env`, and `keys/` are all `.gitignored` globally.

## Jenkins integration

- Jenkins folder: **Dataportal** · workspace `dataportal-projects`.
- Discovery: repos in the Starbreeze GitHub org named `*-dataportal` (with a `Jenkinsfile`). Temporary exception: `analytic-artifact-platform` until renamed to match `*-dataportal`.
- Root Jenkinsfile (`/Jenkinsfile`): single entry point that the Dataportal multibranch scanner picks up. While there is only one project in the monorepo it deploys that one directly. When a second project is added, convert this into a dispatcher that detects which `projects/<name>/**` paths changed (via `when { changeset }` filters) and runs only the relevant deploy stages.
- Snowflake credentials are pulled from Jenkins credential `snowflake_prod_credentials` via the standard `withCredentials(sshUserPrivateKey ...)` block — see the `_template` Jenkinsfile.

## Adding a new project — checklist

1. `cp -r projects/_template projects/<new-name>` and rename references inside.
2. Allocate the next free port in the table above; update both this file and the project's Dockerfile/compose/Jenkinsfile/README.
3. Write `app.py` (sidebar filters + landing copy) and at least one page in `pages/`.
4. Local smoke test: `streamlit run app.py` against the live warehouse.
5. Docker smoke test: `docker compose up --build` from the project dir.
6. Open a PR. Once merged, Jenkins picks the project up automatically on the next scan.
7. Add a row to the **Projects** table in `README.md`.
