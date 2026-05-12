# gameplay-streamlit-dataportal

Monorepo for self-hosted Streamlit apps that sit on top of the Starbreeze data platform — primarily PD2/PD3 gameplay analytics, but anything that reads from the warehouse and needs an interactive UI belongs here.

## Layout

```
.
├── shared/                                    # python package: sf.py, sankey.py, ...
│   └── (imported as `from shared.sf import ...`)
├── projects/
│   ├── tutorial-path-explorer/                # STX-1125 — new-player first-session funnel
│   │   ├── app.py · pages/ · requirements.txt
│   │   ├── Dockerfile · docker-compose.yaml · Jenkinsfile
│   │   ├── README.md · CLAUDE.md
│   │   └── .streamlit/{config,secrets.example}.toml · .env.example
│   └── _template/                             # skeleton: copy this when starting a new project
└── CONVENTIONS.md                             # naming, ports, secrets, Jenkins discovery
```

## Projects

| Project | Status | Port | Owner | Jira |
|---|---|---|---|---|
| `tutorial-path-explorer` | local-dev verified | 8504 | Irene Hjorth | [STX-1125](https://starbreeze.atlassian.net/browse/STX-1125) |

## Quick start (any project)

```bash
git clone git@github.com:StarbreezeStudios/gameplay-streamlit-dataportal.git
cd gameplay-streamlit-dataportal
python3 -m venv .venv && source .venv/bin/activate

# pick a project
PROJECT=tutorial-path-explorer
pip install -r projects/$PROJECT/requirements.txt

# auth — choose one:
#   (a) SSO via browser (simplest)
cp projects/$PROJECT/.streamlit/secrets.example.toml \
   projects/$PROJECT/.streamlit/secrets.toml
# (edit user only)
#   (b) key-pair (recommended for repeated dev)
mkdir -p projects/$PROJECT/keys
cp ~/path/to/your_key.p8 projects/$PROJECT/keys/snowflake_key.p8
# (then uncomment `private_key_path` in secrets.toml)

cd projects/$PROJECT
streamlit run app.py
```

## Adding a new project

```bash
cp -r projects/_template projects/<project-name>
# edit Jenkinsfile / Dockerfile / docker-compose.yaml: replace placeholders
# allocate a port from CONVENTIONS.md
# fill in README.md and app.py
```

Push to `main`. The Dataportal Jenkins folder auto-detects the project via its `Jenkinsfile` and deploys to `/opt/<project-name>` on the Helsinki internal server.

## Deployment summary

- **Jenkins folder:** `Dataportal` (Jenkins-side workspace `dataportal-projects`)
- **Discovery rule:** Starbreeze org repos named `*-dataportal`, plus the temporary exception `analytic-artifact-platform`. This repo matches the `*-dataportal` pattern directly — no exception needed.
- **Server:** Helsinki (`/opt/<project-name>` per project), key-pair Snowflake auth, ports per CONVENTIONS.md.
- **Bot access:** StarbreezeBot must be invited as a Read collaborator on this repo for Jenkins to clone (GitHub settings → Collaborators).

See [`CONVENTIONS.md`](CONVENTIONS.md) for the full layout, port allocation, and what to update when promoting a new project.
