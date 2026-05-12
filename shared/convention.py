"""Path bootstrap for projects that need to import `shared.*`.

Each project's app.py and pages/* should start with:

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from shared.sf import run_query  # noqa: E402

Or, equivalently, call ``shared.bootstrap_path(__file__)`` after a single
``import shared`` — see projects/_template/app.py for the canonical pattern.
"""
from __future__ import annotations
import pathlib
import sys


def bootstrap_path(caller_file: str | pathlib.Path) -> pathlib.Path:
    """Ensure the repo root is on sys.path and return it.

    `caller_file` is typically ``__file__`` from the caller. Walks up
    to find the directory that contains the ``shared/`` package.
    """
    p = pathlib.Path(caller_file).resolve()
    for parent in p.parents:
        if (parent / "shared" / "__init__.py").exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return parent
    raise RuntimeError(
        f"Could not locate repo root containing `shared/` from {caller_file}"
    )
