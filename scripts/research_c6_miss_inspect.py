"""research_c6_miss_inspect.py — R@3 の取りこぼしを1件ずつ目視する診断（なぜ gold が負けるか）。

集計値(0.79)でなく、**gold が満点(R@3=1)にならなかったクエリを1件ずつ**、質問文・gold の
順位/スコア/証拠・実際に top3 に入った人の証拠を並べて出力する。1回の実行で
- FULL MISS (R@3=0): gold が top3 に1人も居ない
- PARTIAL   (0<R@3<1): gold の一部だけ top3・残りは圏外（gold が複数のときのみ発生）
の両方をダンプし、取りこぼした各 gold を **プール外(検索失敗)/プール内で4位以下(順位)/未解決**
に自動分類する。これで「検索(retrieval)が律速か」「順位付けか」「gold ラベルが不自然か」を
人間が1件ずつ判断でき、末尾の集計で全体の律速内訳が出る。

決定的スコアラーのみ（LLM 不要・vLLM 不要）。

使い方（DGX・prepare 済み）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_c6_miss_inspect.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out c6_miss.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
NOW = dt.datetime(2026, 8, 22, 0, 0, 0)
_DEPTH = 10


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="c6_miss.txt")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
    from tekijin.eval.dataset import load_eval_queries
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
    retriever = HybridRetriever(embedder, session, top_k=_DEPTH)
    repo = Repository(session)
    scorer = ExpertiseScorer(repo)
    emps = {e.id: e for e in repo.list_employees()}

    def who(pid):
        e = emps.get(pid)
        return f"{pid}:{e.name}({e.department or ''})" if e else str(pid)

    def evline(rec):
        rs = "; ".join(f"{r['type']}={r['detail']}" for r in rec.get("reasons", []))
        return f"score={rec['score']:.3f} [{rs}]"

    queries = [q for q in load_eval_queries() if q.gold_experts and q.gold_topics]
    lines = [f"person-gold rows: {len(queries)}\n"]

    n_hit = n_partial = n_miss = 0
    # 取りこぼした gold の律速内訳（プール外=検索失敗 / プール内4位以下=順位 / 未解決）
    lost_out_of_pool = lost_ranked_low = lost_unresolved = 0

    for q in queries:
        cands = list(retriever.search(q.query)["candidate_people"])
        out = scorer.rank(q.gold_topics, cands, None, NOW, top_k=_DEPTH) if cands else {"recommendations": []}
        recs = out["recommendations"]
        by_id = {r["person_id"]: r for r in recs}
        ranked = [r["person_id"] for r in recs]
        gold = set(q.gold_experts)
        hit_top3 = gold & set(ranked[:3])
        # R@3 は本番と同じ分母 min(3, |gold|)
        r_at_3 = len(hit_top3) / min(3, len(gold))

        if r_at_3 >= 1.0:
            n_hit += 1
            continue
        if r_at_3 > 0.0:
            n_partial += 1
            tag = f"PARTIAL R@3={r_at_3:.2f}"
        else:
            n_miss += 1
            tag = "FULL MISS"

        in_pool = gold & set(cands)
        block = [
            f"\n### {tag}  #{q.id}  topics={list(q.gold_topics)}  |gold|={len(gold)}",
            f"Q: {q.query}",
            f"gold_experts={sorted(gold)}  top3入り={sorted(hit_top3)}  goldはプールに居る?={sorted(in_pool)}",
        ]
        # top3 に入れなかった gold だけを律速分類（入った gold は成功なので除外）
        for g in sorted(gold - hit_top3):
            if g in by_id:
                rank_pos = ranked.index(g) + 1
                lost_ranked_low += 1
                zero = " 証拠ゼロ!" if not by_id[g].get("reasons") else ""
                block.append(f"  ✗gold {who(g)} → 順位{rank_pos}位(プール内・順位負け)  {evline(by_id[g])}{zero}")
            elif g in set(cands):
                lost_unresolved += 1
                block.append(f"  ✗gold {who(g)} → プールに居るが scorer が未順位化(employee未解決?)")
            else:
                lost_out_of_pool += 1
                block.append(f"  ✗gold {who(g)} → プール外(検索で拾えず=retrieval失敗)")
        block.append("  --- 実際の top3 ---")
        for i, r in enumerate(recs[:3], 1):
            star = " ★gold" if r["person_id"] in gold else ""
            block.append(f"  {i}. {who(r['person_id'])}{star}  {evline(r)}")
        lines.append("\n".join(block))

    total = n_hit + n_partial + n_miss
    summary = [
        "\n\n=== 集計 ===",
        f"行数 {total}: 満点(R@3=1) {n_hit} / 部分(0<R@3<1) {n_partial} / 完全ミス(R@3=0) {n_miss}",
        (
            f"取りこぼした gold の律速内訳: プール外(検索失敗)={lost_out_of_pool}  "
            f"プール内で4位以下(順位)={lost_ranked_low}  未解決={lost_unresolved}"
        ),
    ]
    lines.extend(summary)
    text = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text[-5000:])
    print(f"\nwrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
