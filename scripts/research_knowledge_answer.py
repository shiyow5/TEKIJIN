"""research_knowledge_answer.py — 知識回答の A/B・フロア調律（#357 slice 4b）。

スライス4a の `answer_from_knowledge`（approved 知識単位からの決定的回答）を、実 Nemotron
埋め込みの下で評価する。知識単位は CRM・営業支援トピックの営業日報から抽出済み（slice2）・
埋め込み済み（slice3）・approved 前提。測ること:

- **CRM クエリで発火し関連ケースを返すか**（recall: gold_topics に CRM を含むクエリで
  top 類似 unit の topics が gold_topics と重なる率）。
- **非CRM クエリで誤発火しないか**（false-fire: CRM 以外のクエリで top 類似がフロアを超える率）。
- `knowledge_answer_min_similarity` のフロアを掃いて **CRM-recall と 非CRM-誤発火の
  トレードオフ**を出し、実用フロアを決める。

decide/compose は LLM 非依存（構造化単位の決定的組み立て）なので **vLLM 不要・CPU 埋め込み**。

使い方（DGX・throwaway を prepare + 抽出 + 知識埋め込み + 全 approve 済み）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_knowledge_answer.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out kb_answer.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")

CRM_TOPIC = "CRM・営業支援"
FLOORS = [0.0, 0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--topic", default=CRM_TOPIC)
    ap.add_argument("--out", default="kb_answer.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.knowledge import list_knowledge_units, search_knowledge_units
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.retrieval.embedding import QUERY, SentenceTransformerEmbedder

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

    approved = [
        u
        for u in list_knowledge_units(session, review_status="approved")
        if u.has_embedding
    ]
    print(f"approved+embedded 知識単位: {len(approved)} 件（topic={args.topic}）")
    if not approved:
        print(
            "!! approved かつ embedding 済みの知識単位が無い。抽出/埋め込み/approve を先に。"
        )
        return

    queries = list(load_eval_queries())
    rows = []
    for q in queries:
        qvec = embedder.encode([q.query], kind=QUERY)[0]
        hits = search_knowledge_units(session, qvec, top_k=5, review_status="approved")
        top_sim = hits[0][1] if hits else 0.0
        top_unit = hits[0][0] if hits else None
        gold = set(q.gold_topics or ())
        rows.append(
            {
                "id": q.id,
                "is_crm": args.topic in gold,
                "top_sim": round(float(top_sim), 4),
                "topic_match": bool(top_unit and set(top_unit.topics) & gold),
                "cited_source": top_unit.source_id if top_unit else None,
            }
        )

    crm = [r for r in rows if r["is_crm"]]
    noncrm = [r for r in rows if not r["is_crm"]]
    print(f"CRM クエリ {len(crm)} 件 / 非CRM {len(noncrm)} 件\n")

    def _dist(items):
        vals = sorted(r["top_sim"] for r in items)
        if not vals:
            return "n/a"
        return f"最小{vals[0]:.3f} 中央{statistics.median(vals):.3f} 最大{vals[-1]:.3f}"

    print(f"CRM top_sim 分布   : {_dist(crm)}")
    print(f"非CRM top_sim 分布 : {_dist(noncrm)}\n")

    sweep = []
    print(
        f"{'floor':>6} | {'CRM recall(topic一致&floor超)':>28} | {'非CRM 誤発火':>14}"
    )
    for floor in FLOORS:
        crm_hit = sum(1 for r in crm if r["top_sim"] >= floor and r["topic_match"])
        crm_recall = crm_hit / len(crm) if crm else None
        false_fire = sum(1 for r in noncrm if r["top_sim"] >= floor)
        ff_rate = false_fire / len(noncrm) if noncrm else None
        sweep.append(
            {
                "floor": floor,
                "crm_recall": crm_recall,
                "crm_hit": crm_hit,
                "false_fire": false_fire,
                "false_fire_rate": ff_rate,
            }
        )
        cr = (
            f"{crm_recall:.3f}({crm_hit}/{len(crm)})"
            if crm_recall is not None
            else "n/a"
        )
        fr = (
            f"{ff_rate:.3f}({false_fire}/{len(noncrm)})"
            if ff_rate is not None
            else "n/a"
        )
        print(f"{floor:>6.2f} | {cr:>28} | {fr:>14}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "topic": args.topic,
                "n_approved_units": len(approved),
                "n_crm": len(crm),
                "n_noncrm": len(noncrm),
                "sweep": sweep,
                "rows": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nwrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
