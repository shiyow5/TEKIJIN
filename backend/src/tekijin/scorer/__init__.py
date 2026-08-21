"""C6 expertise scorer (Issue #30): deterministic, evidence-based ranking.

Public surface for the agent (#31) and route selector:

* :class:`~tekijin.scorer.scorer.ExpertiseScorer` — the C6 entry point.
* :class:`~tekijin.scorer.weights.Weights` / ``DEFAULT_WEIGHTS`` — tunable weights.
* :func:`~tekijin.scorer.evidence.collect_topic_evidence` /
  :func:`~tekijin.scorer.evidence.edge_weight` — the evidence aggregation C8 reuses.
"""

from __future__ import annotations

from tekijin.scorer.evidence import Evidence, collect_topic_evidence, edge_weight
from tekijin.scorer.scorer import ExpertiseScorer
from tekijin.scorer.weights import DEFAULT_WEIGHTS, Weights

__all__ = [
    "DEFAULT_WEIGHTS",
    "Evidence",
    "ExpertiseScorer",
    "Weights",
    "collect_topic_evidence",
    "edge_weight",
]
