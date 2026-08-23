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
here. (Prefixing is toggled by ``settings.embedding_use_e5_prefix``, or overridden
per kind via ``settings.embedding_{query,passage}_prefix`` to reproduce an
instruction-tuned model's benchmarked setup — #108.)
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from tekijin.config import get_settings

QUERY = "query"
PASSAGE = "passage"
_KINDS = (QUERY, PASSAGE)


class _Unset:
    """Sentinel: distinguishes "argument omitted" from an explicit ``None``.

    ``revision=None`` is a MEANINGFUL value (load the repo's default branch), so a
    plain ``None`` default cannot signal "fall back to settings" — a caller that
    explicitly wants the default branch must not be silently overridden with the
    global settings' pinned revision.
    """


_UNSET = _Unset()


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
        query_prefix / passage_prefix: Per-kind instruction prefixes that OVERRIDE
            ``use_e5_prefix`` for that kind. ``None`` (default) reads
            ``settings.embedding_{query,passage}_prefix``, which itself defaults to
            ``None`` = fall back to the e5 toggle. An empty string is a meaningful
            override (= no prefix), distinct from ``None``. Used to reproduce an
            instruction-tuned model's benchmarked retrieval setup (e.g. Qwen's
            ``Instruct: <task>\\nQuery: `` on queries, nothing on passages — #108).
        trust_remote_code: Passed to ``SentenceTransformer`` at load time. The
            default Nemotron-3-Embed-1B ships custom modeling code and needs this;
            ``None`` reads ``settings.embedding_trust_remote_code``. SECURITY: this
            executes code from the model repo — keep it on only for trusted models.
        revision: Immutable model revision (commit/tag) to load. OMITTED reads
            ``settings.embedding_model_revision``; an explicit value (INCLUDING
            ``None`` = the repo's default branch) is used verbatim. Pin it in
            production so ``trust_remote_code`` cannot execute code from a moved
            branch.
        app_env: Deployment env driving the fail-closed check. ``None`` reads
            ``settings.app_env`` (the global singleton). Callers that build from a
            custom ``Settings`` MUST forward ``settings.app_env`` from THAT instance,
            or the guard would consult the global env instead of the hardened one it
            is protecting (a bypass in exactly the custom-Settings case — #108).

    Callers that build from an explicit ``Settings`` (e.g. ``build_default_service``)
    should pass ``trust_remote_code``, ``revision`` AND ``app_env`` from THAT instance
    so a custom (e.g. security-hardened) config is honored rather than the cached
    global. ``revision`` uses a sentinel so passing ``None`` for a fallback model that
    wants the default branch is NOT overridden by the global settings' pinned revision.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        model: Any | None = None,
        use_e5_prefix: bool = True,
        query_prefix: str | None = None,
        passage_prefix: str | None = None,
        trust_remote_code: bool | None = None,
        revision: str | None | _Unset = _UNSET,
        app_env: str | None = None,
    ) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model
        self._model = model
        self._use_e5_prefix = use_e5_prefix
        # None => inherit the global setting (which itself defaults to None = fall
        # back to the e5 toggle). An explicit "" stays "" (a real no-prefix override).
        self._query_prefix = (
            settings.embedding_query_prefix if query_prefix is None else query_prefix
        )
        self._passage_prefix = (
            settings.embedding_passage_prefix if passage_prefix is None else passage_prefix
        )
        self._trust_remote_code = (
            settings.embedding_trust_remote_code if trust_remote_code is None else trust_remote_code
        )
        self._revision: str | None = (
            settings.embedding_model_revision if isinstance(revision, _Unset) else revision
        )
        # Fail-closed (#108): outside development, refuse to execute remote model
        # code from a MOVING branch. trust_remote_code=True + revision=None would
        # run whatever the model repo's default branch holds at each cold load, so
        # an upstream change or compromise lands as code execution. Pin a reviewed
        # revision, or turn trust_remote_code off, before deploying. app_env MUST
        # come from the SAME settings instance that drove trust/revision (forwarded
        # by callers), not the global singleton, or a hardened custom Settings would
        # be checked against the wrong env and silently bypass the guard.
        effective_app_env = settings.app_env if app_env is None else app_env
        unpinned_remote_code = self._trust_remote_code and self._revision is None
        if effective_app_env != "development" and unpinned_remote_code:
            raise ValueError(
                "Refusing to load an embedding model with trust_remote_code=True and "
                f"no pinned revision outside development (app_env={effective_app_env!r}). "
                "Set TEKIJIN_EMBEDDING_MODEL_REVISION to a reviewed commit SHA/tag, or set "
                "TEKIJIN_EMBEDDING_TRUST_REMOTE_CODE=false."
            )
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
                    self._model = SentenceTransformer(
                        self._model_name,
                        trust_remote_code=self._trust_remote_code,
                        revision=self._revision,
                    )
        return self._model

    def _prefix(self, kind: str) -> str:
        # A per-kind override (incl. "") wins; otherwise fall back to the e5 toggle.
        override = self._query_prefix if kind == QUERY else self._passage_prefix
        if override is not None:
            return override
        return f"{kind}: " if self._use_e5_prefix else ""

    def encode(self, texts: Sequence[str], *, kind: str = PASSAGE) -> list[list[float]]:
        if kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")
        prefix = self._prefix(kind)
        prepared = [prefix + t for t in texts]
        vectors = self._get_model().encode(prepared, normalize_embeddings=True)
        return [[float(x) for x in vector] for vector in vectors]
