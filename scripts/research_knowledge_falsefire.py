"""research_knowledge_falsefire.py — 知識回答(System1)を全トピックで ON にして安全か。

#357 の knowledge_answer は C5 の前で走り、approved 知識単位が類似フロアを超えると
self_answered で**短絡**する（人取次ぎC5/C6/C7 を飛ばす）。これは「既存知識で答える」系統
そのものだが、**人に取り次ぐべき質問(person route)で誤発火すると、専門家に繋がらず canned 回答**
になる。フロア0.20は CRM のみ(3/84)で調律された。全トピックの知識コーパスを入れたとき、
gold_route 別に:
- person 行での**誤発火率**(fire=BAD): 本来人に取り次ぐべきなのに知識層が奪う率。
- document/prior_answer 行での**発火率**(fire=GOOD): データ由来でむしろ答えてよい率。
- none(棄却) 行での発火率(BAD)。
- 発火した unit の topics が gold_topics と重なる率(topic_match)。

を floor 掃引で測る。これで「System1 を ON にして System2 を壊さないフロア」が分かる。
決定的スコアラー不要・LLM 不要(埋め込みのみ)。approved+embedded 知識単位が前提。

使い方（DGX・prepare + 抽出 + 埋込 + approve 済み）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_knowledge_falsefire.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out kb_falsefire.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
FLOORS = [0.15, 0.20, 0.25, 0.30, 0.35]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="kb_falsefire.json")
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
    approved = [u for u in list_knowledge_units(session, review_status="approved") if u.has_embedding]
    topics_covered = sorted({t for u in approved for t in (u.topics or ())})
    print(f"approved+embedded 知識単位: {len(approved)} 件 / 網羅topic {len(topics_covered)}")
    if not approved:
        print("!! approved+embedded 知識単位が無い。抽出/埋込/approve を先に。")
        return

    # gold_route 別に上位1 unit の類似度と topic 一致を記録。
    rows = []
    for q in load_eval_queries():
        qvec = embedder.encode([q.query], kind=QUERY)[0]
        hits = search_knowledge_units(session, qvec, top_k=1, review_status="approved")
        top = hits[0] if hits else None
        top_sim = float(top[1]) if top else 0.0
        top_unit = top[0] if top else None
        gold = set(q.gold_topics or ())
        rows.append(
            {
                "id": q.id,
                "route": q.gold_route,
                "top_sim": top_sim,
                "topic_match": bool(top_unit and set(top_unit.topics) & gold),
            }
        )

    def _rate(items, pred):
        xs = [1.0 if pred(r) else 0.0 for r in items]
        return round(statistics.mean(xs), 4) if xs else None

    person = [r for r in rows if r["route"] == "person"]
    data_derived = [r for r in rows if r["route"] in ("document", "prior_answer")]
    abstain = [r for r in rows if r["route"] == "none"]
    print(f"rows: person={len(person)} data_derived={len(data_derived)} abstain={len(abstain)}")

    print(
        f"\n{'floor':>6} | {'person誤発火(BAD)':>16} | {'data発火(GOOD)':>14} | "
        f"{'data topic一致':>14} | {'abstain誤発火(BAD)':>16}"
    )
    result = {"n_approved": len(approved), "topics_covered": topics_covered, "by_floor": {}}
    for floor in FLOORS:
        ff_person = _rate(person, lambda r: r["top_sim"] >= floor)  # noqa: B023
        fire_data = _rate(data_derived, lambda r: r["top_sim"] >= floor)  # noqa: B023
        tm_data = _rate(
            data_derived,
            lambda r: r["top_sim"] >= floor and r["topic_match"],  # noqa: B023
        )
        ff_abstain = _rate(abstain, lambda r: r["top_sim"] >= floor)  # noqa: B023
        result["by_floor"][str(floor)] = {
            "person_false_fire": ff_person,
            "data_fire": fire_data,
            "data_topic_match": tm_data,
            "abstain_false_fire": ff_abstain,
        }
        print(
            f"{floor:>6.2f} | {_fmt(ff_person):>16} | {_fmt(fire_data):>14} | "
            f"{_fmt(tm_data):>14} | {_fmt(ff_abstain):>16}"
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")
    session.close()
    session.get_bind().dispose()


def _fmt(x):
    return "n/a" if x is None else f"{x:.3f}"


if __name__ == "__main__":
    main()
