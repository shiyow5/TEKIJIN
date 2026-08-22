"""Load the offline evaluation query set (``eval_queries.json``).

The file is a JSON list of ``{id, query, topics, correct_experts, route}`` — the
40-item set described in technical-spec §7. Kept read-only and schema-checked at
load so a malformed row fails loudly rather than silently skewing a metric.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tekijin.config import get_settings


@dataclass(frozen=True)
class EvalQuery:
    """One evaluation query with its gold labels."""

    id: int
    query: str
    topics: list[str]
    correct_experts: list[int]
    route: str


def default_eval_queries_path() -> Path:
    """The bundled eval query set under the configured fixtures directory."""

    return get_settings().fixtures_dir / "eval" / "eval_queries.json"


def load_eval_queries(path: Path | None = None) -> list[EvalQuery]:
    """Load and validate the eval query set (defaults to the bundled file)."""

    resolved = path or default_eval_queries_path()
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"eval query set must be a JSON list, got {type(raw).__name__}")
    return [_parse_query(row, resolved) for row in raw]


def _parse_query(row: object, source: Path) -> EvalQuery:
    if not isinstance(row, dict):
        raise ValueError(f"{source}: each query must be an object, got {type(row).__name__}")
    missing = {"id", "query", "topics", "correct_experts", "route"} - row.keys()
    if missing:
        raise ValueError(f"{source}: query is missing keys {sorted(missing)}")
    return EvalQuery(
        id=int(row["id"]),
        query=str(row["query"]),
        topics=[str(t) for t in _as_list(row["topics"])],
        correct_experts=[int(e) for e in _as_list(row["correct_experts"])],
        route=str(row["route"]),
    )


def _as_list(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"expected a list, got {type(value).__name__}")
    return value
