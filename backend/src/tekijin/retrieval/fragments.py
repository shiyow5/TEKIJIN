"""Fragment-text retrieval for C1 topic mediation (#69).

C4's :class:`~tekijin.agent.state.RetrievalResult` carries only ids + scores, not
text. To let C1 classify a question's topic *with the retrieved evidence in front
of it* — the retrieve-then-classify order that lifted topic acc +0.114 in #65/#67
— we re-hydrate a few top-scoring fragments' text from the store.

This is a pure read path over a tiny repository protocol (id-keyed batch
lookups), so it is unit-tested with an in-memory fake and holds no DB dependency
of its own. The graph wiring (C4 → C1) lands in a later, focused change; here we
only build and test the data path and its interface.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import zip_longest
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids import cycles)
    from tekijin.agent.state import RetrievalResult
    from tekijin.data.dto import AnswerDTO, DocumentDTO, QuestionDTO

# How many fragments to surface, and how long each may be. Kept small on purpose:
# C1 needs a hint of the retrieved evidence's vocabulary, not the full corpus —
# a long context dilutes the signal and slows the LLM call.
DEFAULT_MAX_FRAGMENTS = 6
DEFAULT_MAX_CHARS = 200


class FragmentSource(Protocol):
    """The id-keyed batch lookups :func:`collect_context_fragments` needs.

    :class:`~tekijin.data.repository.Repository` satisfies this; tests pass a
    lightweight fake so the function stays database-free.
    """

    def answers_by_ids(self, ids: Sequence[str]) -> dict[str, AnswerDTO]: ...

    def questions_by_ids(self, ids: Sequence[str]) -> dict[str, QuestionDTO]: ...

    def documents_by_ids(self, ids: Sequence[str]) -> dict[str, DocumentDTO]: ...


def _clip(text: str, max_chars: int) -> str:
    """Collapse whitespace and truncate to ``max_chars`` (ellipsis if cut)."""

    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


def collect_context_fragments(
    source: FragmentSource,
    retrieval: RetrievalResult,
    *,
    max_fragments: int = DEFAULT_MAX_FRAGMENTS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[str]:
    """Re-hydrate the text of C4's top hits as short labelled snippets.

    Past-answer and document hits are **round-robin interleaved** (answer, doc,
    answer, doc, …) preserving each channel's own ranking, then capped at
    ``max_fragments``. The interleave (rather than concatenation) keeps a long
    answer list from crowding out the documents — and because RRF scores are not
    comparable across channels, it never sorts a merged list by score. Missing
    ids and empty-bodied rows are skipped. Empty retrieval → ``[]``.
    """

    qa_ids = [hit["qa_id"] for hit in retrieval["past_answers"]]
    doc_ids = [hit["doc_id"] for hit in retrieval["documents"]]
    if not qa_ids and not doc_ids:
        return []

    answers = source.answers_by_ids(qa_ids) if qa_ids else {}
    question_ids = [a.question_id for a in answers.values() if a.question_id]
    questions = source.questions_by_ids(question_ids) if question_ids else {}
    documents = source.documents_by_ids(doc_ids) if doc_ids else {}

    # Build each channel's fragments in ranking order (skipping unresolved ids).
    answer_fragments: list[str] = []
    for qa_id in qa_ids:
        answer = answers.get(qa_id)
        if answer is None:
            continue
        fragment = _answer_fragment(answer, questions, max_chars)
        if fragment:
            answer_fragments.append(fragment)

    document_fragments: list[str] = []
    for doc_id in doc_ids:
        document = documents.get(doc_id)
        if document is None:
            continue
        fragment = _document_fragment(document, max_chars)
        if fragment:
            document_fragments.append(fragment)

    interleaved: list[str] = []
    for answer_fragment, document_fragment in zip_longest(answer_fragments, document_fragments):
        for fragment in (answer_fragment, document_fragment):
            if fragment is not None:
                interleaved.append(fragment)
    return interleaved[:max_fragments]


def _answer_fragment(answer: AnswerDTO, questions: dict[str, QuestionDTO], max_chars: int) -> str:
    """A past-Q&A snippet: the linked question body plus the answer body.

    The *question* is where topic-bearing vocabulary usually lives (the answer is
    often a generic procedure), so both are included when available.
    """

    question = questions.get(answer.question_id)
    question_body = (question.body or "").strip() if question else ""
    answer_body = (answer.body or "").strip()
    text = (
        f"{question_body} / {answer_body}"
        if question_body and answer_body
        else (question_body or answer_body)
    )
    if not text:
        return ""
    # Clip the WHOLE labelled fragment so ``max_chars`` bounds what C1 actually sees.
    return _clip(f"過去のQ&A: {text}", max_chars)


def _document_fragment(document: DocumentDTO, max_chars: int) -> str:
    """An internal-document snippet: title plus body."""

    title = (document.title or "").strip()
    body = (document.body or "").strip()
    text = f"{title}: {body}" if title and body else title or body
    if not text:
        return ""
    return _clip(f"社内文書: {text}", max_chars)
