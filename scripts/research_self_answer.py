"""#291 part3: 自己回答(self-answer)の recall 中心 DGX 検証。

`self_answer_enabled` を ON にしてよいかを、#297 で実装した **source recall / decision
recall** で実測する。本番 vLLM(:18080) で実際に `VllmSelfAnswerModel.compose` を呼び、
gold_source を持つデータ由来経路(document/prior_answer)の各クエリについて:

- **本番同様、C5 が data 経路に振り分けたときだけ self_answer を発火**（person 誤ルートは
  cited=空＝取りこぼしとして source recall に 0 で計上）。
- retrieval → collect_cited_evidence → compose → grounded/cited_source_ids を記録。
- #297 の `evaluate_source_recall` で source recall(取りこぼさない率)・precision(ハルシ
  ネーション検知)・grounded 率を、`decision_class` で C5 振り分けを見る。

使い方（DGX・throwaway pgvector を prepare 済み・本番vLLM 稼働前提）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_self_answer.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15433/calib \
      --out self_answer_eval.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "backend")
SRC = os.path.join(BACKEND, "src")

# C5 がデータ由来（自己回答が発火しうる）と判定する経路。
DATA_ROUTES = frozenset({"document", "prior_answer"})


def build(url: str):
    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
    from tekijin.llm.vllm import VllmSelfAnswerModel
    from tekijin.retrieval.embedding import SentenceTransformerEmbedder
    from tekijin.retrieval.retriever import HybridRetriever

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
    repo = Repository(session)
    return (
        session,
        HybridRetriever(embedder, session, top_k=10),
        repo,  # FragmentSource for collect_cited_evidence
        VllmSelfAnswerModel(settings=s),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="self_answer_eval.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.agent.route import decide_route
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.eval.metrics import (
        QueryResult,
        evaluate_source_recall,
        source_precision,
        source_recall,
    )
    from tekijin.retrieval.fragments import collect_cited_evidence

    session, retriever, fragment_source, self_model = build(url)

    # gold_source を持つ行＝自己回答すべきデータ由来経路（document/prior_answer）。
    queries = [q for q in load_eval_queries() if q.gold_source]
    print(f"citation-obligated queries: {len(queries)}")

    results: list[QueryResult] = []
    rows = []
    for q in queries:
        retrieval = retriever.search(q.query)
        route = decide_route(retrieval).route
        invoked = route in DATA_ROUTES
        grounded = False
        cited: list[str] = []
        answer_preview = ""
        if invoked:
            evidence = collect_cited_evidence(fragment_source, retrieval)
            result = self_model.compose(q.query, evidence)  # 本番 vLLM 呼び出し
            grounded = result.grounded
            cited = list(result.cited_source_ids) if grounded else []
            answer_preview = (result.answer or "")[:120]
        # 本番同様、grounded なら self_answered 終端、そうでなければ元経路へフォールバック。
        predicted = "self_answered" if grounded else route
        qr = QueryResult(
            ranked_experts=[],
            gold_experts=list(q.gold_experts),
            predicted_route=predicted,
            gold_route=q.gold_route,
            difficulty=q.difficulty,
            gold_source=list(q.gold_source),
            cited_source_ids=cited,
        )
        results.append(qr)
        rows.append(
            {
                "id": q.id,
                "difficulty": q.difficulty,
                "gold_route": q.gold_route,
                "predicted_route": route,
                "invoked_self_answer": invoked,
                "grounded": grounded,
                "gold_source": list(q.gold_source),
                "cited_source_ids": cited,
                "source_recall": source_recall(qr),
                "source_precision": source_precision(qr),
                "answer_preview": answer_preview,
            }
        )
        print(
            f"  id={q.id:>3} {q.gold_route:12s} route={route:12s} "
            f"grounded={grounded!s:5s} recall={source_recall(qr)} cited={cited}"
        )

    sr = evaluate_source_recall(results)
    routed_to_data = sum(1 for r in rows if r["invoked_self_answer"])
    print("\n=== #291 part3 self-answer (recall 中心) ===")
    print(f"  obligated rows      : {sr.n}")
    print(f"  C5→data経路(発火可)  : {routed_to_data}/{sr.n}  (それ以外は person 等へ誤ルート)")
    print(f"  grounded 率          : {sr.grounded_rate:.3f}  (実際に自己回答した割合)")
    print(f"  source recall        : {sr.recall:.3f}  (取りこぼさない率・主指標)")
    print(f"  source precision     : {sr.precision:.3f}  (引用有 n={sr.n_cited}・ハルシ検知)")

    report = {
        "_meta": {
            "n_obligated": sr.n,
            "routed_to_data": routed_to_data,
            "vllm": os.environ.get("TEKIJIN_LLM_BASE_URL"),
            "model": os.environ.get("TEKIJIN_LLM_MODEL"),
        },
        "aggregate": sr.as_dict(),
        "rows": rows,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
