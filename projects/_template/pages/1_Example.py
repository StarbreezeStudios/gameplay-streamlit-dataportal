"""Example secondary page — delete or replace."""
from __future__ import annotations
import pathlib, sys
_root = pathlib.Path(__file__).resolve()
while _root != _root.parent and not (_root / "shared" / "__init__.py").exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

import streamlit as st
from shared.sf import run_query  # noqa: E402

st.title("Example page")
st.caption("Use shared filters from st.session_state — do not redefine them here.")
