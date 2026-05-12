"""Shared utilities for gameplay-streamlit-dataportal projects.

Import paths (from any project's app.py / pages/*):

    from shared.sf import run_query, get_snowflake_connection
    from shared.sankey import build_sankey

Each project's Dockerfile must COPY the `shared/` directory into the image
so the import path is available at runtime. Local dev relies on `sys.path`
being set to the repo root (the convention.bootstrap_path() helper handles
both cases).
"""

from .convention import bootstrap_path

__all__ = ["bootstrap_path"]
