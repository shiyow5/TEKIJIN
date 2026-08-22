"""Text embedding for dense retrieval (component C3).

Defines the :class:`Embedder` protocol the retrieval layer depends on, plus a
concrete :class:`SentenceTransformerEmbedder` backed by a local Japanese
embedding model. The heavy ``sentence-transformers`` / ``torch`` stack is
imported lazily (inside :meth:`SentenceTransformerEmbedder._get_model`) so that
merely importing this module — or running the test suite with an injected fake
embedder — never pulls those dependencies. See ``requirements-ml.txt``.

The default model (``settings.embedding_model``) is Nemotron-3-Embed-1B, which —
like the e5 family — expects ``query:`` / ``passage:`` prefixes; index-time and
query-time prefixes must agree, so both go through the single ``kind`` argument
here. (Prefixing is toggled by ``settings.embedding_use_e5_prefix``.)
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from tekijin.config import get_settings

QUERY = "query"
PASSAGE = "passage"
_KINDS = (QUERY, PASSAGE)


@runtime_checkable
class Embedder(Protocol):
    """Turns text into fixed-dimension dense vectors.

    Implementations must be deterministic for a given ``(text, kind)`` so that a
    query embedded as ``query`` matches passages embedded as ``passage``. Tests
    inject a fake implementation; production uses
    :class:`SentenceTransformerEmbedder`.
    """

    def encode(self, texts: Sequence[str], *, kind: str = PASSAGE) -> list[list[float]]:
        """Embed ``texts``; ``kind`` is ``"query"`` or ``"passage"``."""
        ...  # pragma: no cover - protocol stub


class SentenceTransformerEmbedder:
    """:class:`Embedder` backed by a ``sentence-transformers`` model.

    Args:
        model_name: Hugging Face model id. Defaults to
            ``settings.embedding_model``.
        model: Pre-loaded model to use instead of lazily loading ``model_name``.
            The model must expose ``encode(list[str], normalize_embeddings=bool)``.
            Primarily a dependency-injection seam for tests.
        use_e5_prefix: When true, prepend ``"<kind>: "`` to every text (required
            by e5-family models). Set false for models that take raw text.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        model: Any | None = None,
        use_e5_prefix: bool = True,
    ) -> None:
        self._model_name = model_name or get_settings().embedding_model
        self._model = model
        self._use_e5_prefix = use_e5_prefix
        # Guards the one-time lazy load so two concurrent sessions sharing this
        # embedder cannot each start a (heavy) model init (codex#6).
        self._model_lock = threading.Lock()

    def _get_model(self) -> Any:  # pragma: no cover - requires heavy model download
        # Double-checked locking: the fast path (already loaded, or an injected
        # model) skips the lock; only the first real load contends.
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:  # guide the operator to the ML extras
                        raise RuntimeError(
                            "sentence-transformers is not installed. The default "
                            "embedder needs the ML dependencies — run `make setup-ml` "
                            "(installs backend/requirements-ml.txt), or run the API "
                            "with an injected embedder for tests."
                        ) from exc
                    self._model = SentenceTransformer(self._model_name)
        return self._model

    @staticmethod
    def _prefix(kind: str) -> str:
        return f"{kind}: "

    def encode(self, texts: Sequence[str], *, kind: str = PASSAGE) -> list[list[float]]:
        if kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")
        prepared = [(self._prefix(kind) + t) if self._use_e5_prefix else t for t in texts]
        vectors = self._get_model().encode(prepared, normalize_embeddings=True)
        return [[float(x) for x in vector] for vector in vectors]
