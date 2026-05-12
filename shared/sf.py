"""Shared Snowflake connection helper.

Resolves credentials from env (Docker / Jenkins) or st.secrets (local dev).
Uses key-pair auth. Cached for 30 min to stay under JWT expiry;
auto-reconnects if expired.

Env vars expected in containerised deploy:
    SNOWFLAKE_USER, SNOWFLAKE_ACCOUNT, SNOWFLAKE_WAREHOUSE,
    SNOWFLAKE_DATABASE, [SNOWFLAKE_ROLE], SNOWFLAKE_PRIVATE_KEY_PATH

Local dev `~/.streamlit/secrets.toml` (or `.streamlit/secrets.toml`) format:
    [snowflake]
    user = "..."
    account = "..."
    warehouse = "..."
    database = "..."
    role = "SYSADMIN"
    # authenticator defaults to externalbrowser if no key path is set
"""
from __future__ import annotations

import logging
import os
import streamlit as st

_log = logging.getLogger(__name__)


def _get_snowflake_creds() -> dict:
    if os.environ.get("SNOWFLAKE_ACCOUNT"):
        creds = {
            "user": os.environ["SNOWFLAKE_USER"],
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "USER_QUERIES"),
            "database": os.environ.get("SNOWFLAKE_DATABASE", "PAYDAY3_PROD"),
        }
        if os.environ.get("SNOWFLAKE_ROLE"):
            creds["role"] = os.environ["SNOWFLAKE_ROLE"]
        key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "/app/keys/snowflake_key.p8")
        if key_path and os.path.exists(key_path):
            from cryptography.hazmat.primitives import serialization
            with open(key_path, "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)
            creds["private_key"] = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        else:
            _log.error(
                "Snowflake private key not found at %s (exists=%s).",
                key_path, os.path.exists(key_path) if key_path else False,
            )
            st.error("Snowflake connection unavailable: credentials not loaded.")
            return {}
        return creds
    try:
        sec = st.secrets["snowflake"]
        creds = {
            "user": sec["user"],
            "account": sec["account"],
            "warehouse": sec.get("warehouse", "USER_QUERIES"),
            "database": sec.get("database", "PAYDAY3_PROD"),
            "role": sec.get("role", "SYSADMIN"),
        }
        # Local dev: prefer key-pair if a path is configured, else SSO via browser.
        key_path = sec.get("private_key_path")
        if key_path and os.path.exists(os.path.expanduser(key_path)):
            from cryptography.hazmat.primitives import serialization
            with open(os.path.expanduser(key_path), "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)
            creds["private_key"] = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        else:
            creds["authenticator"] = "externalbrowser"
        return creds
    except Exception:
        return {}


@st.cache_resource(ttl=1800)
def _cached_snowflake_connection():
    """Connect to Snowflake; raises on failure so a failed result is NEVER cached.

    Why: `st.cache_resource` stores whatever the function returns. If we returned
    `None` on connect failure, the next 30 min of requests would hit the cached
    None and the UI would stay broken even after the underlying issue (missing
    role grant, expired key, etc.) is fixed. Raising means the cache never
    populates on failure and the next request re-attempts the connect.
    """
    import snowflake.connector
    creds = _get_snowflake_creds()
    if not creds:
        raise RuntimeError("Snowflake credentials unavailable")
    return snowflake.connector.connect(**creds)


def get_snowflake_connection():
    try:
        conn = _cached_snowflake_connection()
    except Exception as e:
        st.error(f"Snowflake connection failed: {e}")
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        return conn
    except Exception:
        _cached_snowflake_connection.clear()
        try:
            return _cached_snowflake_connection()
        except Exception as e:
            st.error(f"Snowflake reconnect failed: {e}")
            return None


@st.cache_data(ttl=900, show_spinner=False)
def run_query(sql: str, params: tuple | None = None):
    """Cache query results for 15 min. Use stable SQL strings + tuple params."""
    import pandas as pd
    conn = get_snowflake_connection()
    if conn is None:
        return pd.DataFrame()
    cur = conn.cursor()
    try:
        cur.execute(sql, params or ())
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        cur.close()
