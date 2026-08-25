"""#114×#296: 型番/製品名クエリで適応BM25が document 経路の recall を上げるか実測。

#296 で入れた型番クエリ6件（gold_route=document・gold_source=特定製品文書 doc_031〜036）は
dense 埋め込みが無情報（型番は希少サブトークン・既存コーパス出現0）で、BM25 の exact-match
だけが手掛かり。ここで適応BM25(#114)の boosted 重みを OFF/0.5/1.0 と変えて、

- **document チャネルの出典recall@K**（gold_source 製品文書が retrieval.documents 上位Kに入るか）
- **document 経路への振り分け率**（decide_route が document を返すか）
- gold 文書の順位

を、**型番行(6)** と **通常の document 行** で分けて測る。埋め込みは一度だけロードし、
retriever の適応属性を config ごとに mutate して再検索する（research_bm25_sweep と同型）。

使い方（DGX・throwaway pgvector を新コーパス36文書で prepare 済み前提）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_model_recall.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15435/calib --out model_recall.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")

CONFIGS = [
    ("OFF base=0.2", None),
    ("boost0.5", 0.5),
    ("boost1.0", 1.0),
]
KS = [1, 3, 5, 10]


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


def _doc_recall_at_k(retrieved_ids, gold, k):
    return len(set(retrieved_ids[:k]) & gold) / len(gold)


def _first_gold_rank(retrieved_ids, gold):
    for i, d in enumerate(retrieved_ids):
        if d in gold:
            return i + 1
    return None


def _summarise(rows):
    if not rows:
        return {}
    out = {f"doc_recall@{k}": round(statistics.mean(r[f"recall@{k}"] for r in rows), 3) for k in KS}
    out["route_to_document"] = round(statistics.mean(1.0 if r["route"] == "document" else 0.0 for r in rows), 3)
    ranks = [r["gold_rank"] for r in rows if r["gold_rank"] is not None]
    out["gold_in_top10"] = round(len(ranks) / len(rows), 3)
    out["median_gold_rank"] = statistics.median(ranks) if ranks else None
    out["n"] = len(rows)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="model_recall.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.agent.route import decide_route
    from tekijin.config import get_settings
    from tekijin.eval.dataset import load_eval_queries

    session, retriever = build(url)
    s = get_settings()
    default_lo, default_hi = s.bm25_adapt_lo, s.bm25_adapt_hi

    queries = load_eval_queries()
    doc_rows = [q for q in queries if q.gold_route == "document" and q.gold_source]
    # 型番行: gold_source が製品文書(doc_031〜)。それ以外は通常の document 行。
    model_rows = [q for q in doc_rows if q.gold_source[0].startswith("doc_03") and q.gold_source[0] >= "doc_031"]
    generic_rows = [q for q in doc_rows if q not in model_rows]
    print(f"document rows: {len(doc_rows)} (型番 {len(model_rows)} / 通常 {len(generic_rows)})")

    report = []
    for label, boosted in CONFIGS:
        retriever._bm25_boosted = boosted
        retriever._bm25_adapt_lo = default_lo
        retriever._bm25_adapt_hi = default_hi

        def measure(rows):
            out = []
            for q in rows:
                retrieval = retriever.search(q.query)
                rids = [d["doc_id"] for d in retrieval["documents"]]
                gold = set(q.gold_source)
                route = decide_route(retrieval).route
                rec = {"id": q.id, "route": route, "gold_rank": _first_gold_rank(rids, gold)}
                for k in KS:
                    rec[f"recall@{k}"] = _doc_recall_at_k(rids, gold, k)
                out.append(rec)
            return out

        model_res = measure(model_rows)
        generic_res = measure(generic_rows)
        m, g = _summarise(model_res), _summarise(generic_res)
        report.append({"config": label, "boosted": boosted, "model": m, "generic": g,
                       "model_rows": model_res})
        print(
            f"{label:14s} | 型番: R@1={m['doc_recall@1']} R@3={m['doc_recall@3']} R@5={m['doc_recall@5']} "
            f"route→doc={m['route_to_document']} top10={m['gold_in_top10']} medRank={m['median_gold_rank']} "
            f"|| 通常: R@3={g['doc_recall@3']} route→doc={g['route_to_document']}"
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"_meta": {"ks": KS}, "rows": report}, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
