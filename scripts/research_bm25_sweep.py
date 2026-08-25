"""#114 適応BM25の重み/窓スイープ（DGX・実埋め込み）。

`task_variants` の「現状（そのまま）」経路を、適応BM25の (boosted, lo, hi) を変えながら
測り直す。埋め込みモデルは一度だけロードし、retriever の適応パラメータ属性を config ごとに
差し替えて再検索する（`HybridRetriever._fuse` は呼び出し時に self._bm25_boosted 等を読むので、
属性の mutate だけで設定が効く。BM25 索引・dense 索引は不変なので再構築は不要）。

目的: **症状語を除いた現行 eval で層2 R@3 が非回帰**であることと、
#70 で誤棄却された #37/#49 の hit@3 が動くかを見る。型番/製品名クエリ集合が無いので
「改善」の本番シグナルは限定的（#296 の評価データ拡張待ち）— ここでは非回帰と副作用の有無を確定する。

使い方（DGX, throwaway pgvector を prepare 済み前提）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_bm25_sweep.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15433/calib \
      --out bm25_sweep.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "backend")
SRC = os.path.join(BACKEND, "src")

# 採点の基準時刻は task_variants と同じ（fixtures 最新回答の直後）。
NOW = dt.datetime(2026, 8, 22, 0, 0, 0)

# (label, boosted, lo, hi)。boosted=None は適応OFF（固定 base=0.2）＝ベースライン。
# 窓はコサイン分布（answer 中央0.237/最大0.542, doc0.153, people≈0.15）に合わせて振る。
CONFIGS = [
    ("OFF base=0.2 (baseline)", None, None, None),
    ("boost0.5 win0.15-0.35", 0.5, 0.15, 0.35),
    ("boost1.0 win0.15-0.35", 1.0, 0.15, 0.35),
    ("boost1.5 win0.15-0.35", 1.5, 0.15, 0.35),
    ("boost1.0 win0.10-0.25", 1.0, 0.10, 0.25),
    ("boost1.0 win0.20-0.45", 1.0, 0.20, 0.45),
]

# #70 で critic が誤棄却した person 経路の2件（検索の候補 recall が原因）。
WATCH_IDS = {37, 49}


def build(url: str):
    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
    from tekijin.retrieval.embedding import SentenceTransformerEmbedder
    from tekijin.retrieval.retriever import HybridRetriever
    from tekijin.scorer.scorer import ExpertiseScorer

    session = get_sessionmaker(get_engine(url))()
    s = get_settings()
    embedder = SentenceTransformerEmbedder(
        use_e5_prefix=s.embedding_use_e5_prefix,
        query_prefix=s.embedding_query_prefix,
        passage_prefix=s.embedding_passage_prefix,
        trust_remote_code=s.embedding_trust_remote_code,
        revision=s.embedding_model_revision,
        app_env=s.app_env,
    )
    return (
        session,
        HybridRetriever(embedder, session, top_k=10),
        ExpertiseScorer(Repository(session)),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="bm25_sweep.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.agent.route import decide_route
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.eval.metrics import QueryResult, evaluate, evaluate_by_difficulty
    from tekijin.eval.pipeline import _pinned_responder

    session, retriever, scorer = build(url)
    queries = load_eval_queries()

    def rank(topics, candidates):
        if not topics or not candidates:
            return []
        out = scorer.rank(topics, candidates, None, NOW, top_k=10)
        return [r["person_id"] for r in out["recommendations"]]

    report = []
    for label, boosted, lo, hi in CONFIGS:
        # 適応パラメータを差し替える（_fuse が呼び出し時に読む）。
        retriever._bm25_boosted = boosted
        if lo is not None:
            retriever._bm25_adapt_lo = lo
            retriever._bm25_adapt_hi = hi

        results = []
        watch = {}
        for q in queries:
            res = retriever.search(q.query)
            route = decide_route(res).route
            if route == "document":
                ranked = []
            else:
                pinned = _pinned_responder(res) if route == "prior_answer" else None
                cands = [pinned] if pinned is not None else list(res["candidate_people"])
                ranked = rank(q.gold_topics, cands)
            qr = QueryResult(
                ranked_experts=ranked,
                gold_experts=list(q.gold_experts),
                predicted_route=route,
                gold_route=q.gold_route,
                difficulty=q.difficulty,
                gold_experts_alt=list(q.gold_experts_alt),
                gold_topics=list(q.gold_topics),
            )
            results.append(qr)
            if q.id in WATCH_IDS:
                watch[q.id] = {
                    "route": route,
                    "gold_route": q.gold_route,
                    "n_candidates": len(res.get("candidate_people") or []),
                    "gold_in_candidates": bool(
                        set(q.gold_experts) & set(res.get("candidate_people") or [])
                    ),
                    "hit_at_3": bool(set(ranked[:3]) & set(q.gold_experts)),
                }

        m = evaluate(results)
        layers = evaluate_by_difficulty(results)
        with_topics = evaluate(
            [r for q, r in zip(queries, results) if q.gold_topics and q.gold_experts]
        )
        line = " ".join(f"{k}:{v.recall_at_3:.3f}" for k, v in layers.items() if k != "L4")
        print(
            f"{label:26s} R@3={m.recall_at_3:.3f} Top1={m.top1_accuracy:.3f} "
            f"MRR={m.mrr:.3f} route={m.route_accuracy:.3f} | withGold R@3={with_topics.recall_at_3:.3f} "
            f"| {line} | watch={ {i: w['hit_at_3'] for i, w in watch.items()} }"
        )
        report.append(
            {
                "config": {"label": label, "boosted": boosted, "lo": lo, "hi": hi},
                "recall_at_3": m.recall_at_3,
                "top1": m.top1_accuracy,
                "mrr": m.mrr,
                "route_accuracy": m.route_accuracy,
                "recall_at_3_with_gold_topics": with_topics.recall_at_3,
                "by_difficulty": {k: v.recall_at_3 for k, v in layers.items()},
                "watch": watch,
                "per_query_hit3": [
                    {"id": q.id, "difficulty": q.difficulty, "hit_at_3": bool(set(r.ranked_experts[:3]) & set(r.gold_experts))}
                    for q, r in zip(queries, results)
                    if q.gold_experts
                ],
            }
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"_meta": {"now": NOW.isoformat(), "n": len(queries)}, "rows": report}, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
