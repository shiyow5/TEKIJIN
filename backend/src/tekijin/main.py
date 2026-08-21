"""ASGI entry point.

Run with::

    uvicorn tekijin.main:app --reload
"""

from __future__ import annotations

from tekijin.app import create_app

app = create_app()
