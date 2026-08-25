"""C6 スコアラーの天井診断＋重みスイープ（gold トピック上限・実埋め込み）。

gold トピックを与えても person Recall@3 が ~0.68 で頭打ちの原因を切り分ける:
- **候補 recall**: gold 専門家が retrieval の candidate_people に入っているか（入っていない
  なら retrieval/プールが天井＝#87 領域でスコアラー調整では上がらない）。
- **スコアラー天井**: プールに居るのに top3 に入らない率（＝重み調整の余地）。

retrieval はスコアラー重みに非依存なので **1クエリ1回だけ検索**し、重み config ごとに
`ExpertiseScorer(repo, weights=W)` を作って **同じ候補集合を再ランク**する（高効率）。
overfitting を避けるため **primary gold と alt gold(answers 由来・独立) の両方**で測る。

使い方（DGX・throwaway pgvector を prepare 済み）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES= HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_c6_sweep.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15437/calib --out c6_sweep.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")

NOW = dt.datetime(2026, 8, 22, 0, 0, 0)
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
    return session, HybridRetriever(embedder, session, top_k=_RANK_DEPTH), Repository(session)


def _recall_at_3(ranked, gold):
    if not gold:
        return None
    return len(set(ranked[:3]) & set(gold)) / min(3, len(gold))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="c6_sweep.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    import math

    import tekijin.scorer.scorer as scorer_mod
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.scorer.scorer import ExpertiseScorer
    from tekijin.scorer.weights import Weights

    session, retriever, repo = build(url)
    queries = [q for q in load_eval_queries() if q.gold_experts and q.gold_topics]
    print(f"person-gold rows (gold_experts かつ gold_topics): {len(queries)}")

    # 1クエリ1回だけ検索し、候補集合を固定する（retrieval は重みに非依存）。
    cache = {}
    pool_recall = []
    for q in queries:
        res = retriever.search(q.query)
        cands = list(res["candidate_people"])
        cache[q.id] = (q, cands)
        # 候補 recall: gold 専門家が候補プールに入っているか（top3 でなく プール全体）。
        pool_recall.append(1.0 if set(q.gold_experts) & set(cands) else 0.0)
    print(f"候補プール recall（gold がプールに居る率）: {statistics.mean(pool_recall):.3f}")
    sizes = [len(c) for _, c in cache.values()]
    from collections import Counter as _C

    print(f"候補プールサイズ 分布: {dict(sorted(_C(sizes).items()))} (中央値 {statistics.median(sizes)})")
    le3 = sum(1 for s in sizes if s <= 3)
    print(f"プール<=3人（top3=全員でランキング無関係）: {le3}/{len(sizes)} = {le3 / len(sizes):.3f}")

    # topic_fit の飽和スケールを掃く（本命の設計レバー）。scale=1.0 が現行=速く飽和し
    # 実績数件で全員 topic_fit≈0.95〜0.99 に張り付く。scale を上げると relevant 域が
    # 線形寄りになり「最も詳しい人」を識別できる（= edge_weight の脱飽和）。加えて重みも
    # 少し振るが、diag で重みは順位不変と分かっているので主眼は scale。
    D = Weights()
    CONFIGS = {
        "scale1.0 default(現行)": (1.0, D),
        "scale2.0": (2.0, D),
        "scale3.0": (3.0, D),
        "scale5.0": (5.0, D),
        "scale8.0": (8.0, D),
        "scale3.0+load0.10": (3.0, Weights(0.45, 0.15, 0.20, 0.10, 0.10)),
        "scale5.0+tf0.60": (5.0, Weights(0.60, 0.15, 0.20, 0.10, 0.20)),
        "scale5.0+tf0.60+load0.10": (5.0, Weights(0.60, 0.15, 0.20, 0.10, 0.10)),
    }

    def make_edge_weight(scale):
        def _ew(evidence):
            total = sum(e.base_score for e in evidence)
            return 0.0 if total <= 0.0 else 1.0 - math.exp(-total / scale)

        return _ew

    # base_score リバランス実験: 案件(project)の証拠を厚くすると、案件由来の primary gold が
    # 上がるか？ alt(回答由来) が落ちないか（Pareto 改善か primary↔alt トレードか）を見る。
    import tekijin.scorer.evidence as ev_mod

    BASE_DEFAULT = dict(lead=0.8, member=0.5, ans=0.7, helpful=1.0, cert=0.6, skill=0.3)

    def set_base(lead, member, ans, helpful, cert, skill):
        ev_mod.BASE_SCORE_PROJECT_LEAD = lead
        ev_mod.BASE_SCORE_PROJECT_MEMBER = member
        ev_mod.BASE_SCORE_ANSWER = ans
        ev_mod.BASE_SCORE_HELPFUL_ANSWER = helpful
        ev_mod.BASE_SCORE_CERTIFICATION = cert
        ev_mod.BASE_SCORE_SKILL = skill

    BASE_CONFIGS = {
        "base:default": (0.8, 0.5, 0.7, 1.0, 0.6, 0.3),
        "base:proj↑(lead1.0/mem0.8)": (1.0, 0.8, 0.7, 1.0, 0.6, 0.3),
        "base:proj↑↑(lead1.3/mem1.1)": (1.3, 1.1, 0.7, 1.0, 0.6, 0.3),
        "base:proj>ans(lead1.5/mem1.2/ans0.5)": (1.5, 1.2, 0.5, 0.7, 0.6, 0.3),
    }

    # 取りこぼし診断（default 設定）: gold がプールに居るのに top3 外のとき、その gold の
    # topic_fit 証拠がゼロか（＝行動痕跡が無く原理的にランク不可＝データ問題）を、
    # scorer と同一のバッチ取得＋collect_topic_evidence で正確に判定する。
    scorer_mod.edge_weight = make_edge_weight(1.0)
    diag_scorer = ExpertiseScorer(repo, weights=D)
    from tekijin.scorer.evidence import collect_topic_evidence

    def evidence_count(gid, topics):
        certs = repo.certifications_for_many([gid]).get(gid, [])
        skills = repo.skills_for_many([gid]).get(gid, [])
        mems = repo.project_memberships_for_many([gid]).get(gid, [])
        ans_by = {}
        for a in repo.answers_by_topics(list(topics)):
            ans_by.setdefault(a.responder_id, []).append(a)
        ans = ans_by.get(gid, [])
        return len(collect_topic_evidence(list(topics), certs, skills, mems, ans))

    miss_no_evidence = 0
    miss_has_evidence = 0
    miss_examples = []
    for q, cands in cache.values():
        if not cands:
            continue
        out = diag_scorer.rank(q.gold_topics, cands, None, NOW, top_k=_RANK_DEPTH)
        ranked = [r["person_id"] for r in out["recommendations"]]
        missed = (set(q.gold_experts) & set(cands)) - set(ranked[:3])
        for gid in missed:
            n_ev = evidence_count(gid, q.gold_topics)
            if n_ev == 0:
                miss_no_evidence += 1
            else:
                miss_has_evidence += 1
                if len(miss_examples) < 8:
                    miss_examples.append((q.id, gid, n_ev, q.gold_topics))
    print(f"取りこぼし内訳: 証拠ゼロ(データ問題)={miss_no_evidence} / 証拠ありだが負け(スコア問題)={miss_has_evidence}")
    if miss_examples:
        print(f"  証拠ありで負けた例(qid,gid,証拠数,topics): {miss_examples}")

    # base_score リバランスの測定（scale=1・default weights 固定）。
    scorer_mod.edge_weight = make_edge_weight(1.0)
    print("\n== base_score リバランス（案件証拠を厚くする）==")
    for label, bs in BASE_CONFIGS.items():
        set_base(*bs)
        scorer = ExpertiseScorer(repo, weights=D)
        prim, alt = [], []
        for q, cands in cache.values():
            out = scorer.rank(q.gold_topics, cands, None, NOW, top_k=_RANK_DEPTH) if cands else None
            ranked = [r["person_id"] for r in out["recommendations"]] if out else []
            rp = _recall_at_3(ranked, q.gold_experts)
            if rp is not None:
                prim.append(rp)
            ra = _recall_at_3(ranked, q.gold_experts_alt)
            if ra is not None:
                alt.append(ra)
        print(
            f"{label:36s} R@3(primary)={statistics.mean(prim):.4f} "
            f"R@3(alt)={statistics.mean(alt):.4f}"
        )
    set_base(*[BASE_DEFAULT[k] for k in ("lead", "member", "ans", "helpful", "cert", "skill")])

    # #355: daily-report evidence ON/OFF（default config・scale1.0）。gold は日報(0.15)を
    # 評価に数えるのにスコアラーは日報を見ていなかった非対称の効果を測る。ON で primary が
    # baseline を上回り alt を下げなければ Pareto 改善＝有効化候補。
    print("\n== #355 daily-evidence ON/OFF（default config・scale1.0）==")
    scorer_mod.edge_weight = make_edge_weight(1.0)
    daily_rows = []
    for label, de in [("daily OFF(現行)", False), ("daily ON(#355)", True)]:
        scorer = ExpertiseScorer(repo, weights=D, daily_evidence=de)
        prim, alt = [], []
        for q, cands in cache.values():
            out = scorer.rank(q.gold_topics, cands, None, NOW, top_k=_RANK_DEPTH) if cands else None
            ranked = [r["person_id"] for r in out["recommendations"]] if out else []
            rp = _recall_at_3(ranked, q.gold_experts)
            if rp is not None:
                prim.append(rp)
            ra = _recall_at_3(ranked, q.gold_experts_alt)
            if ra is not None:
                alt.append(ra)
        daily_rows.append(
            {"config": label, "daily_evidence": de,
             "R@3_primary": round(statistics.mean(prim), 4),
             "R@3_alt": round(statistics.mean(alt), 4) if alt else None}
        )
        print(
            f"{label:20s} R@3(primary)={statistics.mean(prim):.4f} "
            f"R@3(alt)={statistics.mean(alt):.4f}"
        )

    report = []
    for label, (scale, W) in CONFIGS.items():
        # edge_weight は scorer.py の名前空間に import 済みなので、そこを差し替える。
        scorer_mod.edge_weight = make_edge_weight(scale)
        scorer = ExpertiseScorer(repo, weights=W)
        prim, alt = [], []
        for q, cands in cache.values():
            if not cands:
                ranked = []
            else:
                out = scorer.rank(q.gold_topics, cands, None, NOW, top_k=_RANK_DEPTH)
                ranked = [r["person_id"] for r in out["recommendations"]]
            r_prim = _recall_at_3(ranked, q.gold_experts)
            if r_prim is not None:
                prim.append(r_prim)
            r_alt = _recall_at_3(ranked, q.gold_experts_alt)
            if r_alt is not None:
                alt.append(r_alt)
        row = {
            "config": label,
            "scale": scale,
            "weights": [W.topic_fit, W.recency, W.answer_quality, W.proximity, W.load],
            "R@3_primary": round(statistics.mean(prim), 4),
            "R@3_alt": round(statistics.mean(alt), 4) if alt else None,
            "n_primary": len(prim),
            "n_alt": len(alt),
        }
        report.append(row)
        print(
            f"{label:26s} R@3(primary)={row['R@3_primary']:.4f} "
            f"R@3(alt)={row['R@3_alt']:.4f} (n={row['n_primary']}/{row['n_alt']})"
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {"_meta": {"pool_recall": round(statistics.mean(pool_recall), 4), "n": len(queries)},
             "daily_evidence": daily_rows,
             "rows": report},
            f, ensure_ascii=False, indent=2,
        )
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
