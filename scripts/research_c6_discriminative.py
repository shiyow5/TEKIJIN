"""research_c6_discriminative.py — 識別的 topic_fit で R@3 天井を破れるか（ADR-0006 再挑戦）。

監査結論: R@3~0.80 の律速は C6 の `edge_weight = saturate(sum(base_score))`。証拠が
2〜3個で topic_fit≈0.95〜0.99 に飽和し、主役 0.45*topic_fit が強候補全員でほぼ定数化。
順位は弱い脇役信号が決める。総和の単調変換では薄い証拠の gold を厚い証拠の非gold の上に
出せない（ADR-0006）。**唯一未検証のレバー = 総和でなく「証拠タイプの希少度(IDF)で重み付け
した識別的 topic_fit」**。プール内で common な証拠(全員が持つ案件参加)を下げ、rare な証拠
(特定topicの回答/資格)を上げると、composition が効いて再順位化しうる。

topic_fit **単体**で順位付けして R@3 を測り、変種が baseline(saturate(sum)) を破るかを見る
（破れば C6 本体へ統合する価値がある）。retrieval は非依存なので 1クエリ1回検索。

使い方（DGX・throwaway prepare 済み）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_c6_discriminative.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out c6_disc.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
_RANK_DEPTH = 10


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
    return (
        session,
        HybridRetriever(embedder, session, top_k=_RANK_DEPTH),
        Repository(session),
    )


def _recall_at_3(ranked, gold):
    if not gold:
        return None
    return len(set(ranked[:3]) & set(gold)) / min(3, len(gold))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="c6_disc.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.eval.dataset import load_eval_queries
    from tekijin.scorer.evidence import collect_topic_evidence

    session, retriever, repo = build(url)
    queries = [q for q in load_eval_queries() if q.gold_experts and q.gold_topics]
    print(f"person-gold rows: {len(queries)}")

    # 1クエリ1回検索して候補を固定し、候補ごとの証拠リストを作る。
    per_query = []  # (q, {cand: [Evidence,...]})
    pool_recall = []
    for q in queries:
        cands = list(retriever.search(q.query)["candidate_people"])
        pool_recall.append(1.0 if set(q.gold_experts) & set(cands) else 0.0)
        topics = list(q.gold_topics)
        certs = repo.certifications_for_many(cands)
        skills = repo.skills_for_many(cands)
        mems = repo.project_memberships_for_many(cands)
        ans_by: dict[int, list] = {}
        for a in repo.answers_by_topics(topics):
            ans_by.setdefault(a.responder_id, []).append(a)
        ev_by = {
            c: collect_topic_evidence(
                topics,
                certs.get(c, []),
                skills.get(c, []),
                mems.get(c, []),
                ans_by.get(c, []),
            )
            for c in cands
        }
        per_query.append((q, cands, ev_by))
    print(f"候補プール recall: {statistics.mean(pool_recall):.3f}")

    def saturate(total):
        return 0.0 if total <= 0 else 1.0 - math.exp(-total)

    # プール内の証拠タイプ希少度(IDF)。common(全員が持つ)を下げ rare を上げる。
    def idf_weights(ev_by):
        n = len(ev_by) or 1
        df: dict[str, int] = {}
        for evs in ev_by.values():
            for t in {e.source_type for e in evs}:
                df[t] = df.get(t, 0) + 1
        return {t: math.log((1 + n) / (1 + d)) for t, d in df.items()}

    def score(evs, idf, *, use_idf, sat):
        if use_idf:
            total = sum(e.base_score * (1.0 + idf.get(e.source_type, 0.0)) for e in evs)
        else:
            total = sum(e.base_score for e in evs)
        return saturate(total) if sat else total

    VARIANTS = {
        "baseline saturate(sum)": {"use_idf": False, "sat": True},
        "raw sum(no sat)": {"use_idf": False, "sat": False},
        "idf-weighted saturate": {"use_idf": True, "sat": True},
        "idf-weighted raw(no sat)": {"use_idf": True, "sat": False},
    }

    report = []
    for label, cfg in VARIANTS.items():
        prim, alt = [], []
        for q, cands, ev_by in per_query:
            idf = idf_weights(ev_by)
            scored = sorted(cands, key=lambda c: (-score(ev_by[c], idf, **cfg), c))
            rp = _recall_at_3(scored, q.gold_experts)
            if rp is not None:
                prim.append(rp)
            ra = _recall_at_3(scored, q.gold_experts_alt)
            if ra is not None:
                alt.append(ra)
        row = {
            "variant": label,
            "R@3_primary": round(statistics.mean(prim), 4),
            "R@3_alt": round(statistics.mean(alt), 4) if alt else None,
            "n": len(prim),
        }
        report.append(row)
        print(
            f"{label:28s} R@3(primary)={row['R@3_primary']:.4f} R@3(alt)={row['R@3_alt']:.4f}"
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {"pool_recall": round(statistics.mean(pool_recall), 4), "rows": report},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
