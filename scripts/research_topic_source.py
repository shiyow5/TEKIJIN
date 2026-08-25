"""research_topic_source.py — C1 のトピック予測 vs 検索投票トピック vs 併合の gold 被覆診断。

#380 のフルグラフ実測で **実 E2E Hit@3=0.742（≠ オラクル 0.9355）** と判明し、真の律速が
C1 のトピック予測（acc@1=0.750）であることが分かった。C6 の採点は `state["topics"]`（C1 由来）
だけを使う。ここで問うのは:

  「C1 の topics より、検索で当たった過去Q&Aの topics を投票した集合（あるいは両者の併合）
    の方が、gold トピックをよく被覆するのではないか?」

これが真なら、C6 の採点トピック源を拡張すれば Hit@3 を 0.742 から押し上げられる（クエリ側に
畳み込む #371 と違い、**採点側**なので C5 の経路判定を壊さない）。本スクリプトは**本番コード
を変えず**、各 eval 行で c1 / retrieval / union の gold 被覆(hit@1/@3)を測る純診断。

本番 vLLM(:18080) 必須（C1 の実出力を使う）・埋め込み索引済みの DB が前提。

使い方（DGX・throwaway prepare 済み・本番 vLLM 稼働）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_topic_source.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15442/calib --out topic_source.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")


def _hit_at_k(predicted: list[str], gold: set[str], k: int) -> float:
    return 1.0 if gold and (set(predicted[:k]) & gold) else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="topic_source.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.eval.pipeline import build_answer_topics, predict_topics_from_retrieval
    from tekijin.llm.vllm import VllmIntentModel
    from tekijin.retrieval.embedding import SentenceTransformerEmbedder
    from tekijin.retrieval.retriever import HybridRetriever

    s = get_settings()
    session = get_sessionmaker(get_engine(url))()
    embedder = SentenceTransformerEmbedder(
        use_e5_prefix=s.embedding_use_e5_prefix,
        query_prefix=s.embedding_query_prefix,
        passage_prefix=s.embedding_passage_prefix,
        trust_remote_code=s.embedding_trust_remote_code,
        revision=s.embedding_model_revision,
        app_env=s.app_env,
    )
    retriever = HybridRetriever(embedder, session)
    intent = VllmIntentModel(settings=s)
    answer_topics = build_answer_topics(Repository(session))

    queries = load_eval_queries()
    if args.limit:
        queries = queries[: args.limit]

    rows = []
    for q in queries:
        gold = set(q.gold_topics or ())
        if not gold:
            continue  # gold トピックが無い行は topic 被覆を測れない
        try:
            c1_topics = list(intent.analyze(q.query, None).topics)
        except Exception as exc:  # noqa: BLE001
            print(f"  !! id={q.id} C1失敗: {exc}", file=sys.stderr)
            c1_topics = []
        retrieval = retriever.search(q.query)
        retr_topics = predict_topics_from_retrieval(retrieval, answer_topics)
        # union: C1 を優先し、検索投票で補う（順序＝C1 → 検索の新規）。
        union = c1_topics + [t for t in retr_topics if t not in c1_topics]
        rows.append(
            {
                "id": q.id,
                "gold": sorted(gold),
                "c1": c1_topics,
                "retrieval": retr_topics,
                "c1_h1": _hit_at_k(c1_topics, gold, 1),
                "c1_h3": _hit_at_k(c1_topics, gold, 3),
                "retr_h1": _hit_at_k(retr_topics, gold, 1),
                "retr_h3": _hit_at_k(retr_topics, gold, 3),
                "union_h3": _hit_at_k(union, gold, 3),
            }
        )

    def _mean(key: str) -> float:
        xs = [r[key] for r in rows]
        return round(statistics.mean(xs), 4) if xs else 0.0

    summary = {
        "n_scored": len(rows),
        "c1_hit@1": _mean("c1_h1"),
        "c1_hit@3": _mean("c1_h3"),
        "retrieval_hit@1": _mean("retr_h1"),
        "retrieval_hit@3": _mean("retr_h3"),
        "union_hit@3": _mean("union_h3"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
