"""research_knowledge_source.py — 未活用のチャット/日報を self_answer の知識源に足すと
person経路の grounded率(System1発火)が上がるかを、スキーマ変更前に measure-first で測る。

背景(ユーザー指示「暗黙的情報の付与=未活用のチャット/日報を知識源に」): 現在 self_answer は
retrieval の past_answers + documents からしか合成しない。employee_chat_history(2000行・完全
未活用) と daily_reports(3070行・C6証拠のみでSystem1未使用) を証拠に足せば、雑談/日報に埋もれた
暗黙知を System1 が引用できる可能性。ただし合成 daily は活動ログ("〜をヒアリングした/提案した")
で知識ベアリングが弱い疑い(過去メモリ「chat=daily冗長・固有1.5%」)。本番の実データなら価値が
あるかもしれないが、合成では測れない限界がある — それを数値で確かめてから配線判断する。

測るもの: person-gold行で self_answer.compose を
  (a) baseline: answers+documents(現行)
  (b) +daily:   baseline + 質問に近い daily 上位(content+issue)
  (c) +chat:    baseline + 質問に近い chat 上位(message)
  (d) +both:    baseline + daily + chat
で回し、grounded率 と「chat/daily が実際に引用されたか」を比較。上がれば知識源化に価値=
スキーマ+索引+検索チャネル+collect_cited_evidence 拡張を配線。上がらねば(合成が弱いだけかも
しれないので)本番データ限定 or #357知識抽出経由が正道、と判断材料にする。

注意: chat/daily を**メモリ内でバッチ埋め込み**(スキーマ変更不要)。person recall には無関係
(self_answer は data経路 or additive person経路の発火量のみ・取次ぎは常に残る)。

使い方（DGX・throwaway prepare 済み・本番 vLLM）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_knowledge_source.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:154XX/calib --out ks.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
_TOPK = 3  # chat/daily の上位いくつを証拠に足すか


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
    return session, embedder, HybridRetriever(embedder, session, top_k=10), Repository(session)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="ks.json")
    ap.add_argument("--limit-corpus", type=int, default=0, help="chat/daily 埋め込み上限(debug)")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from sqlalchemy import select

    from tekijin.config import get_settings
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.llm.vllm import VllmSelfAnswerModel
    from tekijin.models.tables import DailyReport, EmployeeChatHistory
    from tekijin.retrieval.embedding import PASSAGE, QUERY
    from tekijin.retrieval.fragments import CitedEvidence, collect_cited_evidence

    session, embedder, retriever, repo = build(url)
    sa = VllmSelfAnswerModel(settings=get_settings())

    # --- chat/daily を in-memory でバッチ埋め込み（スキーマ変更不要） ---------- #
    def _clip(t: str) -> str:
        return (t or "").strip()[:600]

    daily_rows = list(session.scalars(select(DailyReport)))
    daily_texts = [
        _clip(f"{r.issue or ''} {r.content or ''}") for r in daily_rows if (r.content or r.issue)
    ]
    daily_ids = [r.id for r in daily_rows if (r.content or r.issue)]
    chat_rows = list(session.scalars(select(EmployeeChatHistory)))
    chat_texts = [_clip(r.message) for r in chat_rows if r.message and r.message.strip()]
    chat_ids = [r.id for r in chat_rows if r.message and r.message.strip()]
    if args.limit_corpus:
        daily_texts, daily_ids = daily_texts[: args.limit_corpus], daily_ids[: args.limit_corpus]
        chat_texts, chat_ids = chat_texts[: args.limit_corpus], chat_ids[: args.limit_corpus]
    print(f"embedding daily={len(daily_texts)} chat={len(chat_texts)} …")
    daily_vecs = embedder.encode(daily_texts, kind=PASSAGE) if daily_texts else []
    chat_vecs = embedder.encode(chat_texts, kind=PASSAGE) if chat_texts else []
    print("embedded.")

    def top_evidence(qv, ids, texts, vecs, kind):
        sims = sorted(
            ((i, _cos(qv, vecs[i])) for i in range(len(vecs))), key=lambda x: -x[1]
        )[:_TOPK]
        return [
            CitedEvidence(source_id=f"{kind}_{ids[i]}", kind=kind, text=texts[i]) for i, _s in sims
        ]

    queries = [q for q in load_eval_queries() if q.gold_route == "person"]
    print(f"person-gold rows: {len(queries)}")

    variants = {"baseline": set(), "+daily": {"d"}, "+chat": {"c"}, "+both": {"d", "c"}}
    counters = {k: {"grounded": 0, "cited_ks": 0} for k in variants}
    n = 0
    for q in queries:
        qv = embedder.encode([q.query], kind=QUERY)[0]
        retrieval = retriever.search(q.query, query_vector=qv)
        base_ev = collect_cited_evidence(repo, retrieval)
        d_ev = top_evidence(qv, daily_ids, daily_texts, daily_vecs, "daily")
        c_ev = top_evidence(qv, chat_ids, chat_texts, chat_vecs, "chat")
        n += 1
        for name, flags in variants.items():
            ev = list(base_ev)
            if "d" in flags:
                ev += d_ev
            if "c" in flags:
                ev += c_ev
            result = sa.compose(q.query, ev)
            if result.grounded:
                counters[name]["grounded"] += 1
                ks_ids = {e.source_id for e in (d_ev + c_ev)}
                if set(result.cited_source_ids) & ks_ids:
                    counters[name]["cited_ks"] += 1

    print(f"\n{'variant':10s} {'grounded率':>10s} {'chat/daily引用率':>16s}")
    print("-" * 40)
    report = {}
    for name in variants:
        g = counters[name]["grounded"] / n if n else 0.0
        c = counters[name]["cited_ks"] / n if n else 0.0
        report[name] = {"grounded": round(g, 4), "cited_ks": round(c, 4)}
        print(f"{name:10s} {g:>10.3f} {c:>16.3f}")

    base_g = report["baseline"]["grounded"]
    print(
        f"\n判定: +both の grounded率が baseline({base_g:.3f}) を明確に上回り chat/daily引用率が"
        "正なら、知識源化に価値=スキーマ+索引+検索+collect_cited_evidence 配線へ。上がらねば"
        "(合成 daily は活動ログで弱い)本番限定 or #357知識抽出経由が正道。person recall 不変。"
    )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "n": n,
                "topk": _TOPK,
                "daily": len(daily_texts),
                "chat": len(chat_texts),
                "report": report,
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
