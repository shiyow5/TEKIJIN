"""research_c6_qsim.py — C6 に「質問↔人の証拠テキストの意味一致(qsim)」項を足して
飽和 topic_fit(ADR-0006) を破れるか。r90 メモリ line60「未検証の第3案=検索スコア基準の
順位付け」の本体。ユーザー指摘「タグ一致だけで飽和する・質問文からも探すべき」の直接検証。

仮説(過去のC6実験と非重複な理由):
  過去の C6 実験(識別的IDF・LLMリランカー・weight sweep・facet-pool)はいずれも
  「証拠の数/種類の再重み付け」または「gold topics を与えた条件」だった。ここで測るのは
  **証拠数を一切見ない、質問文↔候補の証拠テキストの cosine** をランキング項に足すこと。
  これは topic ラベルを迂回するので、**C1 が誤トピックを出した行**(質問文ベースの C4 検索は
  gold をプールに入れるが、C6 が誤 topic で topic_fit を計算し gold を落とす)を救える可能性が
  ある。oracle Hit@3 0.9355 と 実E2E 0.742 の差=C1誤り25% が、この項で埋まるかを見る。

  重要: C5 ルーティングは**スコアラーを読まない**ので、C6 に qsim を足しても routing は不変
  (query_expansion #371 の経路破壊リスクが原理的に無い=routing-safe by construction)。
  よってスコアラー単離 harness で Hit@3 が上がれば、E2E でも person recall を落とさず上がる。

測るもの:
  topic source ∈ {gold(オラクル天井), realC1(VllmIntentModelの実予測=C1誤り込み)} × 変種:
    - baseline           : 実 ExpertiseScorer.rank(topic_fit+recency+quality+proximity-load)
    - +λ·qsim_profile    : baseline_score + λ·cos(q, プロフィール記述)
    - +λ·qsim_answer     : baseline_score + λ·max cos(q, その人の回答本文)
    - +λ·qsim_evidence   : baseline_score + λ·max(profile, answer, 案件product)
    - qsim_evidence_only : 証拠数を無視し qsim_evidence 単独で順位付け
  指標: Hit@3(gold∈top3・二値=プロダクト真指標) / Top1 / R@3(分数・継続用)。
  決定打: realC1 で **C1 が topic を外した行だけ**の Hit@3 を baseline vs qsim で比較。

使い方（DGX・throwaway prepare 済み・本番 vLLM 稼働・埋め込みCPU）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_c6_qsim.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:154XX/calib --out c6_qsim.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
NOW = dt.datetime(2026, 8, 22, 0, 0, 0)
_RANK_DEPTH = 10
_LAMBDAS = (0.25, 0.5, 1.0, 2.0)


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


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
    retriever = HybridRetriever(embedder, session, top_k=_RANK_DEPTH)
    return session, embedder, retriever, Repository(session)


def _hit3(ranked, gold) -> float:
    return 1.0 if set(ranked[:3]) & set(gold) else 0.0


def _top1(ranked, gold) -> float:
    return 1.0 if ranked[:1] and ranked[0] in set(gold) else 0.0


def _recall3(ranked, gold) -> float:
    return len(set(ranked[:3]) & set(gold)) / min(3, len(gold))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="c6_qsim.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.llm.vllm import VllmIntentModel
    from tekijin.retrieval.embedding import PASSAGE, QUERY
    from tekijin.scorer.scorer import ExpertiseScorer

    session, embedder, retriever, repo = build(url)
    scorer = ExpertiseScorer(repo)
    intent = VllmIntentModel(settings=get_settings())

    queries = [q for q in load_eval_queries() if q.gold_experts and q.gold_topics]
    print(f"person-gold rows: {len(queries)}")

    # --- query-independent evidence embeddings (precompute ONCE, batched) - #
    # All evidence text is query-independent, so embed every corpus in ONE
    # batched encode() call each (per-item encode() was O(N) model round-trips
    # and left the run embedding for 10+ min). Product strings are deduped
    # globally so a product shared by many people is embedded once.
    profiles = repo.list_profiles()
    all_pids = [p.employee_id for p in profiles]
    prof_items = [(p.employee_id, p.description) for p in profiles if (p.description or "").strip()]
    prof_batch = embedder.encode([t for _, t in prof_items], kind=PASSAGE) if prof_items else []
    prof_vec = {pid: prof_batch[i] for i, (pid, _) in enumerate(prof_items)}

    ans_bodies: list[str] = []
    ans_owner: list[int] = []
    for a in repo.list_answers():
        if a.body and a.body.strip():
            ans_bodies.append(a.body)
            ans_owner.append(a.responder_id)
    ans_batch = embedder.encode(ans_bodies, kind=PASSAGE) if ans_bodies else []
    ans_vec: dict[int, list] = {}
    for owner, vec in zip(ans_owner, ans_batch, strict=True):
        ans_vec.setdefault(owner, []).append(vec)

    # project products per employee (membership lookup once for all 40), and a
    # deduped product->vector table.
    mems_all = repo.project_memberships_for_many(all_pids)
    products_by_pid = {
        pid: [m.product for m in mems_all.get(pid, []) if m.product] for pid in all_pids
    }
    distinct_products = sorted({p for ps in products_by_pid.values() for p in ps})
    prod_batch = embedder.encode(distinct_products, kind=PASSAGE) if distinct_products else []
    prod_vec = {p: prod_batch[i] for i, p in enumerate(distinct_products)}
    print(
        f"embedded profiles={len(prof_vec)} answers={len(ans_bodies)} "
        f"products={len(distinct_products)}"
    )

    def qsim_profile(pid, qv):
        return _cos(qv, prof_vec[pid]) if pid in prof_vec else 0.0

    def qsim_answer(pid, qv):
        return max((_cos(qv, v) for v in ans_vec.get(pid, [])), default=0.0)

    def qsim_project(pid, qv):
        return max(
            (_cos(qv, prod_vec[p]) for p in products_by_pid.get(pid, []) if p in prod_vec),
            default=0.0,
        )

    # --- per-query: retrieval pool, real C1 topics, baseline scores, qsim - #
    rows = []  # each: dict with cands, gold, gold_alt, base_score{}, qsim..{}, c1_hit
    pool_recall = []
    c1_acc = []
    for q in queries:
        qv = embedder.encode([q.query], kind=QUERY)[0]
        cands = list(retriever.search(q.query, query_vector=qv)["candidate_people"])
        pool_recall.append(1.0 if set(q.gold_experts) & set(cands) else 0.0)

        c1_topics = list(intent.analyze(q.query, None).topics)
        c1_hit = bool(set(q.gold_topics) & set(c1_topics))
        c1_acc.append(1.0 if c1_hit else 0.0)

        qprof = {c: qsim_profile(c, qv) for c in cands}
        qans = {c: qsim_answer(c, qv) for c in cands}
        qproj = {c: qsim_project(c, qv) for c in cands}
        qev = {c: max(qprof[c], qans[c], qproj[c]) for c in cands}

        base_scores = {}  # topic_source -> {pid: score}
        for src, topics in (("gold", list(q.gold_topics)), ("realC1", c1_topics)):
            if not topics:
                base_scores[src] = {c: 0.0 for c in cands}
                continue
            res = scorer.rank(topics, cands, None, NOW, top_k=len(cands))
            sc = {r["person_id"]: r["score"] for r in res["recommendations"]}
            base_scores[src] = {c: sc.get(c, 0.0) for c in cands}

        rows.append(
            {
                "id": q.id,
                "cands": cands,
                "gold": list(q.gold_experts),
                "gold_alt": list(q.gold_experts_alt),
                "c1_hit": c1_hit,
                "base": base_scores,
                "qprof": qprof,
                "qans": qans,
                "qev": qev,
            }
        )
    print(
        f"候補プール recall: {statistics.mean(pool_recall):.3f}  "
        f"C1 topic acc: {statistics.mean(c1_acc):.3f}"
    )

    # --- ranking variants ------------------------------------------------ #
    def rank(row, src, qkey, lam):
        base = row["base"][src]
        qs = row[qkey] if qkey else {c: 0.0 for c in row["cands"]}
        return sorted(row["cands"], key=lambda c: (-(base[c] + lam * qs[c]), c))

    def rank_qonly(row, qkey):
        qs = row[qkey]
        return sorted(row["cands"], key=lambda c: (-qs[c], c))

    variants = [("baseline", None, 0.0)]
    for qkey in ("qprof", "qans", "qev"):
        for lam in _LAMBDAS:
            variants.append((f"+{qkey}·{lam}", qkey, lam))

    report = []
    for src in ("gold", "realC1"):
        for label, qkey, lam in variants:
            h3 = t1 = r3 = 0.0
            h3_c1miss = []  # Hit@3 on rows where realC1 got topic WRONG
            for row in rows:
                ranked = rank(row, src, qkey, lam)
                h3 += _hit3(ranked, row["gold"])
                t1 += _top1(ranked, row["gold"])
                r3 += _recall3(ranked, row["gold"])
                if not row["c1_hit"]:
                    h3_c1miss.append(_hit3(ranked, row["gold"]))
            n = len(rows)
            report.append(
                {
                    "topic_source": src,
                    "variant": label,
                    "Hit@3": round(h3 / n, 4),
                    "Top1": round(t1 / n, 4),
                    "R@3": round(r3 / n, 4),
                    "Hit@3_on_C1miss": round(statistics.mean(h3_c1miss), 4) if h3_c1miss else None,
                    "n_C1miss": len(h3_c1miss),
                }
            )
        # qsim-only (topic-source independent, but list under each for readability)
        for qkey in ("qprof", "qans", "qev"):
            h3 = t1 = r3 = 0.0
            h3_c1miss = []
            for row in rows:
                ranked = rank_qonly(row, qkey)
                h3 += _hit3(ranked, row["gold"])
                t1 += _top1(ranked, row["gold"])
                r3 += _recall3(ranked, row["gold"])
                if not row["c1_hit"]:
                    h3_c1miss.append(_hit3(ranked, row["gold"]))
            n = len(rows)
            report.append(
                {
                    "topic_source": f"{src}(only)",
                    "variant": f"{qkey}_only",
                    "Hit@3": round(h3 / n, 4),
                    "Top1": round(t1 / n, 4),
                    "R@3": round(r3 / n, 4),
                    "Hit@3_on_C1miss": round(statistics.mean(h3_c1miss), 4) if h3_c1miss else None,
                    "n_C1miss": len(h3_c1miss),
                }
            )

    hdr = (
        f"{'topic_src':14s} {'variant':16s} {'Hit@3':>7s} "
        f"{'Top1':>7s} {'R@3':>7s} {'Hit@3|C1miss':>13s}"
    )
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in report:
        cm = (
            ""
            if r["Hit@3_on_C1miss"] is None
            else f"{r['Hit@3_on_C1miss']:.3f}(n={r['n_C1miss']})"
        )
        print(
            f"{r['topic_source']:14s} {r['variant']:16s} "
            f"{r['Hit@3']:7.4f} {r['Top1']:7.4f} {r['R@3']:7.4f} {cm:>13s}"
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "pool_recall": round(statistics.mean(pool_recall), 4),
                "c1_topic_acc": round(statistics.mean(c1_acc), 4),
                "rows": report,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nwrote {args.out}")
    print(
        "\n読み方: realC1+qsim変種の Hit@3 が realC1 baseline を超え、gold baseline(オラクル天井)"
        "に近づけば、qsim は C1誤りを救う=C6本体へ統合の価値。特に『Hit@3|C1miss』が baseline"
        "より上がるかが核心(C1が外した行を qsim が拾えるか)。上がらなければ IDF 同様に refuted。"
    )
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
