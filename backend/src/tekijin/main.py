"""ASGI entry point.

The ``tekijin`` package lives under ``backend/src``, so the server must be told
where to find it. Run it via the Makefile target::

    make run-backend

or directly from the ``backend/`` directory::

    uvicorn tekijin.main:app --reload --app-dir src
"""

from __future__ import annotations

from tekijin.app import create_app

app = create_app()
