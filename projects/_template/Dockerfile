# Build from monorepo root:
#   docker build -f projects/<project-name>/Dockerfile -t <project-name> .
# Replace <project-name> and <port> below.

FROM python:3.12-slim

ARG PROJECT=_template
ARG PORT=8500

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY projects/${PROJECT}/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY projects/${PROJECT}/app.py ./
COPY projects/${PROJECT}/pages/ ./pages/
COPY projects/${PROJECT}/.streamlit/ ./.streamlit/

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/_stcore/health || exit 1

# Note: ENTRYPOINT doesn't expand ARG at runtime; bake the port in via
# build-arg OR override CMD. For per-project deploys, copy this file and
# replace the port literal in ENTRYPOINT below.
ENTRYPOINT ["streamlit", "run", "app.py", \
    "--server.port=8500", \
    "--server.headless=true", \
    "--server.address=0.0.0.0"]
