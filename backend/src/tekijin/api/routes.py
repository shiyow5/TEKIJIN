"""HTTP routes: /ask, /answer, /events (SSE), /dashboard.

``/ask`` starts a NEW question; ``/answer`` RESUMES a paused run (a clarification
reply or a responder outcome). Both enqueue the next input and return an ack; the
run streams over ``/events/{session_id}``. Resume-vs-new-question and the pending
interrupt kind are validated against the durable checkpointer state (409/422), so
a stray /ask cannot overwrite a paused run and an outcome cannot be mis-delivered
to a clarification.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from sse_starlette import EventSourceResponse

from tekijin.api import schemas
from tekijin.api.service import (
    AgentService,
    AskerNotFound,
    HandoffNotFound,
    SessionConflict,
    SessionInvalid,
)
from tekijin.data.dashboard import dashboard_summary
from tekijin.data.repository import Repository

logger = logging.getLogger(__name__)

router = APIRouter()


def _service(request: Request) -> AgentService:
    return request.app.state.agent_service


@router.post("/ask", response_model=schemas.AckResponse)
def ask(req: schemas.AskRequest, request: Request) -> schemas.AckResponse:
    """Start a new question for ``session_id``; stream flows over /events."""

    try:
        _service(request).start_question(req.session_id, req.asker_id, req.question)
    except SessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AskerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # unexpected: log detail, return a generic 500
        logger.exception("POST /ask failed for session %s", req.session_id)
        raise HTTPException(status_code=500, detail="内部エラーが発生しました") from exc
    return schemas.AckResponse(session_id=req.session_id, status="accepted")


@router.post("/answer", response_model=schemas.AckResponse)
def answer(req: schemas.ResumeRequest, request: Request) -> schemas.AckResponse:
    """Resume a paused run: a responder outcome or a clarification reply."""

    try:
        _service(request).submit_resume(req.session_id, outcome=req.outcome, reply=req.reply)
    except SessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # unexpected: log detail, return a generic 500
        logger.exception("POST /answer failed for session %s", req.session_id)
        raise HTTPException(status_code=500, detail="内部エラーが発生しました") from exc
    return schemas.AckResponse(session_id=req.session_id, status="resumed")


@router.get("/events/{session_id}")
def events(session_id: str, request: Request) -> EventSourceResponse:
    """SSE stream of the queued run's node updates (model-definition §4).

    Returns 404 only when there is neither a queued run nor a paused run for the
    session; a paused session reconnects (re-emits its pending interrupt).
    """

    service = _service(request)
    if not service.is_streamable(session_id):
        raise HTTPException(status_code=404, detail="no active run for this session")
    return EventSourceResponse(service.stream_events(session_id))


@router.get("/handoff/{session_id}", response_model=schemas.HandoffResponse)
def handoff(session_id: str, request: Request) -> schemas.HandoffResponse:
    """Responder-facing handoff payload for a session paused at ``send``.

    Read-only view of the durable state (product-spec 画面4): the question, the
    asker, the filled-in slots, why this responder was chosen, the draft, and the
    responder's past-answer reuse. 404 when no handoff is pending (unknown /
    finished); 409 when the session is awaiting a clarification instead.
    """

    try:
        return _service(request).get_handoff(session_id)
    except HandoffNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # unexpected: log detail, return a generic 500
        logger.exception("GET /handoff failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="内部エラーが発生しました") from exc


@router.get("/dashboard", response_model=schemas.DashboardResponse)
def dashboard(request: Request) -> schemas.DashboardResponse:
    """Aggregate load / topic mix / recent activity for the dashboard."""

    with _service(request).session_factory() as session:
        data = dashboard_summary(session)
    return schemas.DashboardResponse(**data)


@router.get("/employees", response_model=schemas.EmployeeListResponse)
def employees(request: Request) -> schemas.EmployeeListResponse:
    """List employees for the current-user switcher (id / name / dept).

    The prototype has no auth, so the frontend picks the acting user from this
    directory (used as ``asker_id`` and as the responder id for the inbox). ids
    are the external ``"E###"`` form to match the rest of the contract.
    """

    with _service(request).session_factory() as session:
        rows = Repository(session).list_employees()
    return schemas.EmployeeListResponse(
        employees=[
            schemas.EmployeeSummary(
                id=schemas.format_employee_id(row.id),
                name=row.name,
                dept=row.department,
            )
            for row in rows
        ]
    )
