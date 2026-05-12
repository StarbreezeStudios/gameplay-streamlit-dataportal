"""<project-name> — landing page.

Replace this docstring with a short description. Sidebar filters go here;
each page reads them from st.session_state.
"""
from __future__ import annotations
import pathlib, sys

# Bootstrap: locate monorepo root so `from shared.X import ...` works in both
# local dev and the deploy container. Walks up until it finds the `shared/`
# package; works regardless of how deeply nested the file is.
_root = pathlib.Path(__file__).resolve()
while _root != _root.parent and not (_root / "shared" / "__init__.py").exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

import streamlit as st
from shared.sf import run_query  # noqa: E402  (must come after path bootstrap)

st.set_page_config(
    page_title="<project-name>",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("<project-name>")
st.caption("One-line subtitle / context.")

# Example query — replace.
df = run_query("SELECT CURRENT_TIMESTAMP() AS now, CURRENT_USER() AS who")
if not df.empty:
    st.write(df)
