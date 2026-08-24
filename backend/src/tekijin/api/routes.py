"""HTTP routes: /ask, /answer, /events (SSE), /dashboard.

``/ask`` starts a NEW question; ``/answer`` RESUMES a paused run (a clarification
reply or a responder outcome). Both enqueue the next input and return an ack; the
run streams over ``/events/{session_id}``. Resume-vs-new-question and the pending
interrupt kind are validated against the durable checkpointer state (409/422), so
a stray /ask cannot overwrite a paused run and an outcome cannot be mis-delivered
to a clarification.

Read endpoints (dashboard/employees/inbox/questions/documents) have NO auth in the
prototype (all data is synthetic) and uniformly mask unexpected errors as a generic
500 via ``_generic_500`` — see docs/adr/0005-read-endpoint-auth-and-error-wrapping.md
for the auth seam (``require_reader``) and error policy (#146).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sse_starlette import EventSourceResponse

from tekijin.api import schemas
from tekijin.api.service import (
    AgentService,
    AskerNotFound,
    HandoffNotFound,
    ServiceBusy,
    SessionConflict,
    SessionInvalid,
)
from tekijin.data.dashboard import dashboard_summary
from tekijin.data.db import session_scope
from tekijin.data.documents import get_document
from tekijin.data.history import recent_questions_for_asker
from tekijin.data.inbox import pending_handoffs_for_responder
from tekijin.data.messages import list_messages, question_participants, resolve_question_id
from tekijin.data.notifications import pending_decline_notifications_for_asker
from tekijin.data.repository import Repository
from tekijin.data.writes import (
    QuestionHasPendingHandoff,
    ack_decline_notifications,
    insert_message,
    soft_delete_question,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _service(request: Request) -> AgentService:
    return request.app.state.agent_service


@contextlib.contextmanager
def _generic_500(route: str) -> Iterator[None]:
    """Log-and-mask an unexpected error as a generic 500 (no internal detail leaked).

    A deliberate :class:`HTTPException` (404/422/…) raised inside passes through
    unchanged; any OTHER exception is logged with a stack trace and reduced to a
    generic 500. This unifies the read endpoints (dashboard/employees/inbox/
    questions/documents) with the inline handling already on /ask, /answer and
    /handoff, so no read endpoint can surface an internal message (#146).
    """

    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("%s failed", route)
        raise HTTPException(status_code=500, detail="内部エラーが発生しました") from exc


@router.post("/ask", response_model=schemas.AckResponse)
def ask(req: schemas.AskRequest, request: Request) -> schemas.AckResponse:
    """Start a new question for ``session_id``; stream flows over /events."""

    try:
        _service(request).start_question(req.session_id, req.asker_id, req.question)
    except ServiceBusy as exc:
        # Backpressure (#180): the LLM run pool is full — shed with a graceful 503 +
        # Retry-After so the client can back off instead of hammering the GPU.
        logger.info("shedding /ask for session %s (busy): %s", req.session_id, exc)
        raise HTTPException(
            status_code=503,
            detail="現在混雑しています。少し待ってから、もう一度お試しください。",
            headers={"Retry-After": "5"},
        ) from exc
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
        _service(request).submit_resume(
            req.session_id,
            outcome=req.outcome,
            reply=req.reply,
            recommendation_id=req.recommendation_id,
        )
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


@router.post("/handoff/draft", response_model=schemas.AckResponse)
def handoff_draft(req: schemas.HandoffDraftRequest, request: Request) -> schemas.AckResponse:
    """Persist the asker's edited hand-off draft (画面3) so the responder (画面4)
    reads the edited text (#174).

    Draft-only: never touches the recommendation/outcome state. 404 when no
    hand-off is pending (unknown / finished / already answered); 409 when the
    session is awaiting a clarification instead. A blank draft is rejected by the
    request schema (422) before it reaches the service.
    """

    try:
        _service(request).save_handoff_draft(req.session_id, req.draft)
    except HandoffNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # unexpected: log detail, return a generic 500
        logger.exception("POST /handoff/draft failed for session %s", req.session_id)
        raise HTTPException(status_code=500, detail="内部エラーが発生しました") from exc
    return schemas.AckResponse(session_id=req.session_id, status="draft_saved")


@router.post("/handoff/select", response_model=schemas.HandoffSelectResponse)
def handoff_select(
    req: schemas.HandoffSelectRequest, request: Request
) -> schemas.HandoffSelectResponse:
    """Asker picks a different (of the currently shown) candidate as the hand-off
    target; the draft is regenerated for them (#200/#A1/#204).

    404 when no hand-off is pending (unknown / finished / already answered); 409
    when the session is awaiting a clarification instead; 422 when ``person_id``
    is not among the currently shown recommendations.
    """

    try:
        return _service(request).select_handoff_candidate(req.session_id, req.person_id)
    except HandoffNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # unexpected: log detail, return a generic 500
        logger.exception("POST /handoff/select failed for session %s", req.session_id)
        raise HTTPException(status_code=500, detail="内部エラーが発生しました") from exc


@router.get("/dashboard", response_model=schemas.DashboardResponse)
def dashboard(request: Request) -> schemas.DashboardResponse:
    """Aggregate load / topic mix / recent activity for the dashboard."""

    with _generic_500("GET /dashboard"):
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

    with _generic_500("GET /employees"):
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


@router.get("/inbox", response_model=schemas.InboxResponse)
def inbox(request: Request, responder_id: str = Query(min_length=1)) -> schemas.InboxResponse:
    """Questions currently awaiting ``responder_id`` (the responder inbox, #123).

    ``responder_id`` accepts an int or the ``"E###"`` form (422 otherwise). Each
    item deep-links to ``/answer/{session_id}``; seeded history (no session) is
    excluded — there is no live handoff to open.
    """

    with _generic_500("GET /inbox"):
        try:
            rid = schemas.coerce_employee_id(responder_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="responder_id must be an int or 'E###'"
            ) from exc

        with _service(request).session_factory() as session:
            rows = pending_handoffs_for_responder(session, rid)
        return schemas.InboxResponse(
            items=[
                schemas.InboxItem(
                    session_id=row["session_id"],
                    question_id=row["question_id"],
                    question=row["question"],
                    topics=row["topics"],
                    asker=schemas.HandoffAsker(
                        id=schemas.format_employee_id(row["asker_id"]),
                        name=row["asker_name"],
                        dept=row["asker_dept"],
                    ),
                    created_at=row["created_at"],
                )
                for row in rows
            ]
        )


@router.get("/questions", response_model=schemas.RecentQuestionsResponse)
def questions(
    request: Request,
    asker_id: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=500),
) -> schemas.RecentQuestionsResponse:
    """The asker's own recent questions with resolution state (画面1 の一覧, #125).

    ``asker_id`` accepts an int or the ``"E###"`` form (422 otherwise). ``limit``
    defaults to 5 (the "最近のあなたの質問" panel); the full history view (#208/#F9)
    passes a larger value to see everything. Soft-deleted questions (#207/#F8)
    are excluded.
    """

    with _generic_500("GET /questions"):
        try:
            aid = schemas.coerce_employee_id(asker_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="asker_id must be an int or 'E###'"
            ) from exc

        with _service(request).session_factory() as session:
            rows = recent_questions_for_asker(session, aid, limit=limit)
        return schemas.RecentQuestionsResponse(
            items=[schemas.RecentQuestionItem(**row) for row in rows]
        )


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(
    question_id: str, request: Request, asker_id: str = Query(min_length=1)
) -> Response:
    """Soft-delete one of the asker's own past questions (#207/#F8).

    ``asker_id`` accepts an int or the ``"E###"`` form (422 otherwise). 404 when
    the question does not exist, is not owned by ``asker_id``, or was already
    deleted. 409 when a responder is currently being asked (a live pending
    hand-off) — reselect or wait for an outcome before deleting.
    """

    with _generic_500("DELETE /questions"):
        try:
            aid = schemas.coerce_employee_id(asker_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="asker_id must be an int or 'E###'"
            ) from exc

        try:
            with session_scope(_service(request).session_factory) as session:
                deleted = soft_delete_question(session, question_id, aid)
        except QuestionHasPendingHandoff as exc:
            raise HTTPException(
                status_code=409, detail="対応中の依頼があるため削除できません"
            ) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="question not found or already deleted")
    return Response(status_code=204)


@router.get("/notifications", response_model=schemas.NotificationsResponse)
def notifications(
    request: Request, asker_id: str = Query(min_length=1)
) -> schemas.NotificationsResponse:
    """Decline events the asker hasn't seen yet, newest first (#E7).

    Paired with the automatic reroute (#206/#D5): the system has already moved
    on to the next candidate by the time this fires, so it is an informational
    "here's what happened" surface, not a request for the asker to act.
    ``asker_id`` accepts an int or the ``"E###"`` form (422 otherwise).
    """

    with _generic_500("GET /notifications"):
        try:
            aid = schemas.coerce_employee_id(asker_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="asker_id must be an int or 'E###'"
            ) from exc

        with _service(request).session_factory() as session:
            rows = pending_decline_notifications_for_asker(session, aid)
        return schemas.NotificationsResponse(
            items=[schemas.DeclineNotification(**row) for row in rows]
        )


@router.post("/notifications/ack", response_model=schemas.NotificationAckResponse)
def ack_notifications(
    req: schemas.NotificationAckRequest, request: Request
) -> schemas.NotificationAckResponse:
    """Mark decline notifications as seen (#E7)."""

    with _generic_500("POST /notifications/ack"):
        with session_scope(_service(request).session_factory) as session:
            count = ack_decline_notifications(session, req.asker_id, req.ids)
        return schemas.NotificationAckResponse(acknowledged=count)


@router.get("/messages", response_model=schemas.MessagesResponse)
def messages(request: Request, session_id: str = Query(min_length=1)) -> schemas.MessagesResponse:
    """The chat thread for a session's question, oldest first (#E6).

    Empty (not an error) for an unknown session or a question with no messages
    yet, so a client can poll before the first message exists.
    """

    with _generic_500("GET /messages"):
        with _service(request).session_factory() as session:
            question_id = resolve_question_id(session, session_id)
            rows = list_messages(session, question_id) if question_id is not None else []
        return schemas.MessagesResponse(
            items=[
                schemas.MessageItem(
                    id=row["id"],
                    sender_id=schemas.format_employee_id(row["sender_employee_id"]),
                    body=row["body"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]
        )


@router.post("/messages", response_model=schemas.MessageItem)
def post_message(req: schemas.MessageCreateRequest, request: Request) -> schemas.MessageItem:
    """Post one chat message on a session's post-acceptance thread (#E6).

    404 when the session is unknown; 409 when the question has no accepted
    responder yet (the thread is not open); 403 when ``sender_id`` is neither
    the asker nor the accepted responder.
    """

    with _generic_500("POST /messages"):
        with session_scope(_service(request).session_factory) as session:
            question_id = resolve_question_id(session, req.session_id)
            if question_id is None:
                raise HTTPException(status_code=404, detail="unknown session")
            asker_id, responder_id = question_participants(session, question_id)
            if responder_id is None:
                raise HTTPException(status_code=409, detail="この質問はまだ受諾されていません")
            if req.sender_id not in (asker_id, responder_id):
                raise HTTPException(status_code=403, detail="この会話の参加者ではありません")
            row = insert_message(session, question_id, req.sender_id, req.body)
            message_id = row.id
            created_at = row.created_at
        return schemas.MessageItem(
            id=message_id,
            sender_id=schemas.format_employee_id(req.sender_id),
            body=req.body,
            created_at=created_at.isoformat() if created_at is not None else None,
        )


@router.get("/documents/{doc_id}", response_model=schemas.DocumentDetail)
def document_detail(doc_id: str, request: Request) -> schemas.DocumentDetail:
    """Full content of one internal document, for the document viewer (#143).

    Read-only: viewing the cited document (from the ``document`` route's terminal
    message) never advances a run. 404 when ``doc_id`` is unknown.
    """

    with _generic_500("GET /documents"):
        with _service(request).session_factory() as session:
            row = get_document(session, doc_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        return schemas.DocumentDetail(**row)
