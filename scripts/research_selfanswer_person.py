"""research_selfanswer_person.py — additive self-answer(引用付き回答を人取次ぎと併記)の
価値を配線前に測る。ユーザー症状「過去回答からの引用が全然発火しない」の根因=self_answer が
data由来経路(document/prior_answer)でしか発火せず、専門家が居る知識質問は PERSON 経路に流れて
self_answer をスキップする(graph.py _after_c5)。

そこで **PERSON経路の行(=現状 self_answer を通らない行)で self_answer.compose を走らせたら
どれだけ grounded な引用回答が出るか** を測る。grounded率が高ければ additive 配線の価値大
(人取次ぎは残したまま引用回答を併記=System1が発火)。低ければ compose が保守的すぎで別対応。

person recall は additive では構造的に不変(取次ぎを消さない)ので、ここでは「発火量と品質」だけ
測る。gold_source を持つ行では source recall/precision も出す。

使い方（DGX・throwaway prepare 済み・本番 vLLM）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_selfanswer_person.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:154XX/calib --out sa_person.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")


def build(url: str):
    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
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
    return session, embedder, HybridRetriever(embedder, session, top_k=10), Repository(session)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="sa_person.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.agent.route import PERSON, decide_route
    from tekijin.config import get_settings
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.llm.vllm import VllmSelfAnswerModel
    from tekijin.retrieval.embedding import QUERY
    from tekijin.retrieval.fragments import collect_cited_evidence

    session, embedder, retriever, repo = build(url)
    sa = VllmSelfAnswerModel(settings=get_settings())

    queries = load_eval_queries()
    records = []
    for q in queries:
        qv = embedder.encode([q.query], kind=QUERY)[0]
        retrieval = retriever.search(q.query, query_vector=qv)
        route = decide_route(retrieval).route
        evidence = collect_cited_evidence(repo, retrieval)
        result = sa.compose(q.query, evidence)
        cited = list(result.cited_source_ids) if result.grounded else []
        gold_src = set(q.gold_source or [])
        rec = {
            "id": q.id,
            "gold_route": q.gold_route,
            "decided_route": route,
            "n_evidence": len(evidence),
            "answer_conf": round(float(retrieval.get("answer_confidence") or 0.0), 3),
            "document_conf": round(float(retrieval.get("document_confidence") or 0.0), 3),
            "grounded": bool(result.grounded),
            "n_cited": len(cited),
            "cited": cited,
            "gold_source": list(gold_src),
            "src_hit": bool(gold_src & set(cited)) if gold_src else None,
        }
        records.append(rec)

    def rate(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(1 for v in vals if v) / len(vals) if vals else 0.0

    person = [r for r in records if r["decided_route"] == PERSON]
    data_rows = [r for r in records if r["decided_route"] != PERSON]
    print(f"総行 {len(records)}  PERSON経路 {len(person)}  data経路 {len(data_rows)}")
    print("\n=== PERSON経路(現状 self_answer を通らない=引用が出ない行) ===")
    print(f"  self_answer.compose grounded率 = {rate(person, 'grounded'):.3f}")
    grounded_cites = [r["n_cited"] for r in person if r["grounded"]]
    avg_cites = statistics.mean(grounded_cites) if grounded_cites else 0
    print(f"  grounded時の平均引用数 = {avg_cites:.2f}")
    print("  ※ additive配線ならこの grounded率ぶんの PERSON質問に引用が併記される(取次ぎ維持)")
    print("\n=== data経路(現状 self_answer が発火する行・比較) ===")
    print(f"  grounded率 = {rate(data_rows, 'grounded'):.3f}")

    # gold_route=person の行だけで見た grounded(=本当に人に投げるべき行での発火=併記の是非)
    gr_person = [r for r in records if r["gold_route"] == "person"]
    print(f"\ngold_route=person {len(gr_person)}行: grounded率 = {rate(gr_person, 'grounded'):.3f}")
    print("  (高すぎると人質問に無関係引用が併記されるリスク→grounded gate/relevance floor 要検討)")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"rows": records}, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
