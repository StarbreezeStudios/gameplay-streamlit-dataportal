# Build from monorepo root so `shared/` is in the build context:
#   docker build -f projects/tutorial-path-explorer/Dockerfile -t tutorial-path-explorer .
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Shared deps + project deps. Shared deps are the superset; projects can pin tighter.
COPY projects/tutorial-path-explorer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the shared package alongside the project so `from shared.X import ...` works
COPY shared/ ./shared/
COPY projects/tutorial-path-explorer/app.py ./
COPY projects/tutorial-path-explorer/pages/ ./pages/
COPY projects/tutorial-path-explorer/.streamlit/ ./.streamlit/

# Streamlit listens on 8510 — see CONVENTIONS.md for the Helsinki port allocation.
EXPOSE 8510

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8510/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "app.py", \
    "--server.port=8510", \
    "--server.headless=true", \
    "--server.address=0.0.0.0"]
