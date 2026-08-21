"""HTTP routes: /ask, /answer, /events (SSE), /dashboard.

``/ask`` and ``/answer`` enqueue the next input for a session and return an ack;
``/events/{session_id}`` streams the queued run as Server-Sent Events. Resume vs.
new question is explicit: ``/ask`` is a fresh question (new invoke), ``/answer``
is a ``Command(resume=…)`` for a paused run (followup reply or responder outcome).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sse_starlette import EventSourceResponse

from tekijin.api import schemas
from tekijin.api.service import AgentService
from tekijin.data.dashboard import dashboard_summary

router = APIRouter()


def _service(request: Request) -> AgentService:
    return request.app.state.agent_service


@router.post("/ask", response_model=schemas.AckResponse)
def ask(req: schemas.AskRequest, request: Request) -> schemas.AckResponse:
    """Start a new question for ``session_id``; stream flows over /events."""

    _service(request).enqueue_question(req.session_id, req.asker_id, req.question)
    return schemas.AckResponse(session_id=req.session_id, status="accepted")


@router.post("/answer", response_model=schemas.AckResponse)
def answer(req: schemas.ResumeRequest, request: Request) -> schemas.AckResponse:
    """Resume a paused run: a responder outcome or a clarification reply."""

    _service(request).enqueue_resume(req.session_id, req.resume_value)
    return schemas.AckResponse(session_id=req.session_id, status="resumed")


@router.get("/events/{session_id}")
def events(session_id: str, request: Request) -> EventSourceResponse:
    """SSE stream of the queued run's node updates (model-definition §4)."""

    service = _service(request)
    if not service.has_pending(session_id):
        raise HTTPException(status_code=404, detail="no pending run for this session")
    return EventSourceResponse(service.stream_events(session_id))


@router.get("/dashboard", response_model=schemas.DashboardResponse)
def dashboard(request: Request) -> schemas.DashboardResponse:
    """Aggregate load / topic mix / recent activity for the dashboard."""

    with _service(request).session_factory() as session:
        data = dashboard_summary(session)
    return schemas.DashboardResponse(**data)
