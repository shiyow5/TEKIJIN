"""research_route_recall.py — C5 データ経路 recall の測定（#327）。

#291 自己回答の律速は C5 の「データ由来経路(document/prior_answer)への振り分け」recall
（#327）。既存 `research_route.py`(#88) は cosine 3閾値のスイープ止まりで、**#119 の
corpus-count routing (`prior_answer_reuse_min`)** を測っていない — npz ベースで
reuse_count を持たないため。本ハーネスは **DB+実 retriever** で past_answers の
reuse_count を用い、`prior_answer_reuse_min` を掃いて:

- **prior_answer recall**（gold_route=prior_answer をどれだけ prior_answer に振り分けたか）
- **document recall** / **person recall**（person を壊さないかの非退行チェック＝Pareto 条件）
- 全体の経路正解率と混同行列

を出す。decide_route は LLM 非依存なので **vLLM 不要・CPU 埋め込みのみ**。retrieval は
経路パラメータに非依存なので **1クエリ1回だけ検索**して各 config で再判定する（高効率）。

使い方（DGX・throwaway pgvector を prepare 済み）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_route_recall.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out route_recall.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")

ROUTES = ("person", "prior_answer", "document")

# 実験: corpus-count routing を「people 信号が弱いときだけ」発火させる案（#327）。
# 素の corpus-count は person-gold も高 reuse の top answer を持つため person を奪う。
# document 降格と同じく people_conf < weak で守れるかを、decide_route を変えずに harness
# 内で再現して測る（採否が決まってから本体を触る）。
PERSON_WEAK_SIM = 0.40
PRIOR_ANSWER_RELEVANCE_FLOOR = 0.15


def gated_route(retrieval, reuse_min: int, weak_people: float = PERSON_WEAK_SIM) -> str:
    """decide_route と同じだが、corpus-count 分岐に people_conf<weak のゲートを足した版。"""
    from tekijin.agent.route import decide_route

    answer_conf = float(retrieval.get("answer_confidence", 0.0))
    people_conf = float(retrieval.get("people_confidence", 0.0))
    past_answers = retrieval.get("past_answers") or []
    if past_answers and people_conf < weak_people:
        top = max(past_answers, key=lambda p: p.get("score", 0.0))
        reuse = int(top.get("reuse_count", 0) or 0)
        if reuse >= reuse_min and answer_conf >= PRIOR_ANSWER_RELEVANCE_FLOOR:
            return "prior_answer"
    # ゲートを通らなければ通常の decide_route（corpus-count は OFF）。
    return decide_route(retrieval).route


def build(url: str):
    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
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
    return session, HybridRetriever(embedder, session, top_k=10)


def _recall_by_route(items, predicted):
    """gold_route ごとの recall（正しくその経路へ振り分けた率）と件数。"""
    per = {}
    for route in ROUTES:
        golds = [it for it in items if it["gold_route"] == route]
        if not golds:
            per[route] = {"recall": None, "n": 0}
            continue
        hit = sum(1 for it in golds if predicted[it["id"]] == route)
        per[route] = {"recall": hit / len(golds), "n": len(golds), "hit": hit}
    return per


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="route_recall.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.agent.route import decide_route
    from tekijin.eval.dataset import load_eval_queries

    session, retriever = build(url)

    # 経路つき（none は #80 の証拠十分性の別段なので除外）。
    queries = [q for q in load_eval_queries() if q.gold_route in ROUTES]
    dist = collections.Counter(q.gold_route for q in queries)
    print(f"経路つきクエリ {len(queries)} 件: {dict(dist)}")
    majority = max(dist.values()) / len(queries)
    print(f"多数決ベースライン（常に person）= {majority:.3f}\n")

    # 1クエリ1回だけ検索（retrieval は経路パラメータに非依存）。
    cache = {}
    items = []
    for q in queries:
        cache[q.id] = retriever.search(q.query)
        items.append({"id": q.id, "gold_route": q.gold_route})

    # config: baseline(現行 reuse_min=None) と corpus-count routing を掃く。
    CONFIGS = [
        ("baseline (reuse_min=None)", None),
        ("reuse_min=2", 2),
        ("reuse_min=3", 3),
        ("reuse_min=4", 4),
        ("reuse_min=5", 5),
    ]

    # 実験: people 信号が弱いときだけ corpus-count を発火させる gated 版。
    GATED = [
        ("gated reuse_min=3 (people<0.40)", 3),
        ("gated reuse_min=4 (people<0.40)", 4),
    ]

    report = []
    for label, reuse_min in CONFIGS:
        predicted = {
            it["id"]: decide_route(
                cache[it["id"]], prior_answer_reuse_min=reuse_min
            ).route
            for it in items
        }
        _emit(label, reuse_min, items, predicted, report)

    for label, reuse_min in GATED:
        predicted = {it["id"]: gated_route(cache[it["id"]], reuse_min) for it in items}
        _emit(label, reuse_min, items, predicted, report, gated=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "n": len(items),
                "distribution": dict(dist),
                "majority": majority,
                "rows": report,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


def _emit(label, reuse_min, items, predicted, report, *, gated=False):
    """1 config の正解率・per-route recall・混同行列を出力し、report に1行足す。"""
    acc = sum(1 for it in items if predicted[it["id"]] == it["gold_route"]) / len(items)
    per = _recall_by_route(items, predicted)
    table = collections.Counter()
    for it in items:
        table[(it["gold_route"], predicted[it["id"]])] += 1
    report.append(
        {
            "config": label,
            "prior_answer_reuse_min": reuse_min,
            "gated": gated,
            "route_accuracy": round(acc, 4),
            "recall": {r: per[r] for r in ROUTES},
        }
    )
    pa, doc, per_p = per["prior_answer"], per["document"], per["person"]
    print(f"== {label} ==")
    print(f"  経路正解率 = {acc:.4f}")
    print(
        f"  recall  person={per_p['recall']:.3f}(n{per_p['n']}) "
        f"prior_answer={pa['recall']:.3f}(n{pa['n']}) "
        f"document={doc['recall']:.3f}(n{doc['n']})"
    )
    print(f"    {'gold＼予測':14s}" + "".join(f"{p:>14s}" for p in ROUTES))
    for g in ROUTES:
        print(f"    {g:14s}" + "".join(f"{table[(g, p)]:14d}" for p in ROUTES))
    print()


if __name__ == "__main__":
    main()
