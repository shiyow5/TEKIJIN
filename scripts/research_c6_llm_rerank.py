"""research_c6_llm_rerank.py — LLM リランカーで R@3 天井を破れるか（0.90 への本命）。

決定的スコアラー(証拠の総和/IDF)は打ち止め(ADR-0006・research_c6_discriminative)。だが
**候補プール recall=0.952＝正解はほぼプールに居るのに上位3に入れられていない**。証拠の"数"
でなく**質問文と候補のプロフィール/実績の"中身"を LLM が意味理解で読んで並べ替え**れば、
証拠カウントで越えられない壁を越えられるかを実測する。

各クエリ1回の LLM 呼び出しでプール(~10人)を並べ替え、R@3 を決定的スコアラー baseline と
比較する。retrieval は非依存なので 1クエリ1回検索。本番 vLLM(:18080) 必須。

使い方（DGX・prepare 済み・本番 vLLM 稼働）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_c6_llm_rerank.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out c6_llm.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

from pydantic import BaseModel, Field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
NOW = dt.datetime(2026, 8, 22, 0, 0, 0)
_RANK_DEPTH = 10
_CLIP = 160


class RerankSchema(BaseModel):
    """LLM リランカーの構造化出力: 適合度の高い順に並べた person_id。"""

    ranked_person_ids: list[int] = Field(
        default_factory=list,
        description="質問に最も的確に答えられる順に並べた候補の person_id",
    )


def _clip(text, n=_CLIP):
    t = " ".join((text or "").split())
    return t[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="c6_llm.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.llm.vllm import build_structured_model
    from tekijin.retrieval.embedding import SentenceTransformerEmbedder
    from tekijin.retrieval.retriever import HybridRetriever
    from tekijin.scorer.scorer import ExpertiseScorer

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
    retriever = HybridRetriever(embedder, session, top_k=_RANK_DEPTH)
    repo = Repository(session)
    scorer = ExpertiseScorer(repo)
    model = build_structured_model(RerankSchema, s)

    queries = [q for q in load_eval_queries() if q.gold_experts and q.gold_topics]
    print(f"person-gold rows: {len(queries)}")

    def _recall_at_3(ranked, gold):
        return len(set(ranked[:3]) & set(gold)) / min(3, len(gold)) if gold else None

    def candidate_block(cands, topics):
        emps = repo.employees_by_ids(cands)
        mems = repo.project_memberships_for_many(cands)
        ans_by: dict[int, list] = {}
        for a in repo.answers_by_topics(topics):
            ans_by.setdefault(a.responder_id, []).append(a)
        lines = []
        for c in cands:
            e = emps.get(c)
            name = f"{e.name}（{e.department or ''}）" if e else str(c)
            prof = repo.get_profile(c)
            desc = _clip(prof.description) if prof else ""
            prods = sorted({m.product for m in mems.get(c, []) if m.product})
            answers = [_clip(a.body, 80) for a in ans_by.get(c, [])[:2]]
            parts = [f"person_id={c} {name}"]
            if desc:
                parts.append(f"自己紹介:{desc}")
            if prods:
                parts.append(f"案件商材:{'/'.join(prods[:4])}")
            if answers:
                parts.append("過去回答:" + " / ".join(answers))
            lines.append("  ".join(parts))
        return "\n".join(lines)

    SYS = (
        "あなたは社内の質問に最適な回答者を選ぶアシスタントです。質問に対し、候補者の"
        "プロフィール・案件商材・過去回答から、その質問に最も的確に答えられる順に "
        "person_id を並べてください。答えられそうにない人は下位に。候補の person_id 以外は"
        "出さないこと。"
    )

    prim_base, alt_base, prim_llm, alt_llm = [], [], [], []
    llm_fail = 0
    for q in queries:
        cands = list(retriever.search(q.query)["candidate_people"])
        if not cands:
            continue
        # baseline: 決定的スコアラー
        out = scorer.rank(q.gold_topics, cands, None, NOW, top_k=_RANK_DEPTH)
        base_ranked = [r["person_id"] for r in out["recommendations"]]
        # LLM rerank
        block = candidate_block(cands, list(q.gold_topics))
        prompt = [
            ("system", SYS),
            (
                "human",
                f"質問: {q.query}\n\n候補者:\n{block}\n\n最適な順に person_id を並べてください。",
            ),
        ]
        try:
            res = model.invoke(prompt)
            llm_ranked = [
                c for c in (res.ranked_person_ids if res else []) if c in set(cands)
            ]
        except Exception:
            llm_ranked = []
        if not llm_ranked:
            llm_fail += 1
            llm_ranked = base_ranked  # フォールバック（LLM 失敗はスコアラーに戻す）

        for acc_p, acc_a, ranked in (
            (prim_base, alt_base, base_ranked),
            (prim_llm, alt_llm, llm_ranked),
        ):
            rp = _recall_at_3(ranked, q.gold_experts)
            if rp is not None:
                acc_p.append(rp)
            ra = _recall_at_3(ranked, q.gold_experts_alt)
            if ra is not None:
                acc_a.append(ra)

    print(f"LLM 出力が空/無効でフォールバックした件数: {llm_fail}/{len(queries)}")
    print(
        f"baseline(決定的)      R@3(primary)={statistics.mean(prim_base):.4f} R@3(alt)={statistics.mean(alt_base):.4f}"
    )
    print(
        f"LLM rerank            R@3(primary)={statistics.mean(prim_llm):.4f} R@3(alt)={statistics.mean(alt_llm):.4f}"
    )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "n": len(prim_base),
                "llm_fallback": llm_fail,
                "baseline_primary": round(statistics.mean(prim_base), 4),
                "baseline_alt": round(statistics.mean(alt_base), 4),
                "llm_primary": round(statistics.mean(prim_llm), 4),
                "llm_alt": round(statistics.mean(alt_llm), 4),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
