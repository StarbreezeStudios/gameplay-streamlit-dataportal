# `<project-name>`

> One-line description of the app.

- **Owner:** `<your name>`
- **Helsinki URL:** http://helsinki:<port>
- **Jira / spec:** `<link>`

## Local dev

```bash
# from the monorepo root
python3 -m venv .venv && source .venv/bin/activate
pip install -r projects/<project-name>/requirements.txt

# either set up streamlit secrets:
cp projects/<project-name>/.streamlit/secrets.example.toml \
   projects/<project-name>/.streamlit/secrets.toml
# (edit and fill in)

# or set env vars for key-pair auth (path to your .p8):
export SNOWFLAKE_USER=... SNOWFLAKE_ACCOUNT=RE15009-STARBREEZE
export SNOWFLAKE_PRIVATE_KEY_PATH=~/path/to/snowflake_key.p8

cd projects/<project-name>
streamlit run app.py
```

## Deployment

Push to `main`. Jenkins's Dataportal job picks up this project via its
`Jenkinsfile` and deploys to `/opt/<project-name>` on Helsinki.

See [`CONVENTIONS.md`](../../CONVENTIONS.md) for port allocation, secrets,
and what to update when promoting a new project.
