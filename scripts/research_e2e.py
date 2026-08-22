#!/usr/bin/env python3
"""research_e2e.py — 製品の評価ランナーを実機で回し、ハーネスの結論が再現するか確かめる（#100）。

これまでの数値（#65 / #73 / #80 / #85 / #88 / #91）は **`scripts/research_*.py` の再現実装**で測ったもの。
式・重み・`decide_route` は製品の純関数を import しているが、C4（HybridRetriever）と DB 経路は別実装なので、
結論が製品コードで再現する保証がなかった。

**ローカルで完結する。** `requirements-dev.txt` の `pgserver` が rootless PostgreSQL + pgvector を同梱しており、
Docker も GPU も要らない（埋め込みは CPU で 370 行 4〜5分）。

    python scripts/research_e2e.py --task prepare   # seed + embed（初回のみ、約5分）
    python scripts/research_e2e.py --task route     # C4→C5 の経路とチャネル類似度
    python scripts/research_e2e.py --task variants  # 経路のバグを直したときの層2 R@3
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import statistics as st
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(REPO_ROOT, "backend")
SRC = os.path.join(BACKEND, "src")
DEFAULT_PGDIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "tekijin_e2e_pgdata")

# scorer の recency / 7日負荷窓を固定する（`tekijin.eval.__main__` の EVAL_NOW と同じ）
# scorer は naive な datetime を要求する（保存側が naive）。ruff の DTZ001 はここでは不適切。
NOW = dt.datetime(2026, 8, 22, 0, 0, 0)  # noqa: DTZ001


def start_db(pgdir):
    import pgserver

    os.makedirs(pgdir, exist_ok=True)
    server = pgserver.get_server(pgdir)
    url = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)
    return url


def prepare(url):
    env = {**os.environ, "PYTHONPATH": SRC, "TEKIJIN_DATABASE_URL": url}
    for label, args, cwd in (
        ("seed", [sys.executable, "-m", "tekijin.data.seed"], BACKEND),
        (
            "embed",
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "embed_fixtures.py")],
            REPO_ROOT,
        ),
    ):
        proc = subprocess.run(
            args, cwd=cwd, env=env, text=True, capture_output=True, check=False
        )
        print(f"[{label}] exit={proc.returncode}\n{proc.stdout[-600:]}")
        if proc.returncode:
            print(proc.stderr[-2000:], file=sys.stderr)
            return False
    return True


def build(url):
    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
    from tekijin.retrieval.embedding import SentenceTransformerEmbedder
    from tekijin.retrieval.retriever import HybridRetriever
    from tekijin.scorer.scorer import ExpertiseScorer

    session = get_sessionmaker(get_engine(url))()
    embedder = SentenceTransformerEmbedder(
        use_e5_prefix=get_settings().embedding_use_e5_prefix
    )
    return (
        session,
        HybridRetriever(embedder, session, top_k=10),
        ExpertiseScorer(Repository(session)),
    )


def task_route(url, out):
    from tekijin.agent.route import (
        DOCUMENT_SIM,
        PERSON_WEAK_SIM,
        PRIOR_ANSWER_SIM,
        decide_route,
    )
    from tekijin.eval.dataset import load_eval_queries

    session, retriever, _scorer = build(url)
    rows, routes = [], collections.Counter()
    conf = collections.defaultdict(list)
    for q in load_eval_queries():
        res = retriever.search(q.query)
        route = decide_route(res).route
        routes[route] += 1
        for key in ("answer_confidence", "document_confidence", "people_confidence"):
            conf[key].append(float(res.get(key, 0.0)))
        rows.append(
            {
                "id": q.id,
                "gold_route": q.gold_route,
                "route": route,
                "n_candidates": len(res.get("candidate_people") or []),
                **{k: round(float(res.get(k, 0.0)), 3) for k in conf},
            }
        )

    print(
        f"閾値: prior_answer={PRIOR_ANSWER_SIM} document={DOCUMENT_SIM} person_weak={PERSON_WEAK_SIM}"
    )
    print(f"予測経路の分布: {dict(routes)}")
    print(
        f"候補者数の分布: {dict(collections.Counter(r['n_candidates'] for r in rows))}"
    )
    for key, values in conf.items():
        values = sorted(values)
        print(
            f"  {key:20s} 最小{values[0]:.3f} 中央{st.median(values):.3f} 最大{values[-1]:.3f}"
        )
    if out:
        from tekijin.config import get_settings

        payload = {
            "_meta": {
                "embedding_model": get_settings().embedding_model,
                "thresholds": {
                    "prior_answer_sim": PRIOR_ANSWER_SIM,
                    "document_sim": DOCUMENT_SIM,
                    "person_weak_sim": PERSON_WEAK_SIM,
                },
                "n": len(rows),
                "source": "scripts/research_e2e.py --task route",
            },
            "rows": rows,
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"wrote {out}")
    session.close()
    session.get_bind().dispose()


def task_variants(url, out):
    """経路の pin と候補集合を差し替えて、製品の metrics で測り直す。"""
    from sqlalchemy import select
    from tekijin.agent.route import decide_route
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.eval.metrics import QueryResult, evaluate, evaluate_by_difficulty
    from tekijin.eval.pipeline import _pinned_responder
    from tekijin.models.tables import Employee

    session, retriever, scorer = build(url)
    # pgserver は関数を抜けると停止する。プール解放より先に落ちると
    # AdminShutdown のトレースバックが出るので、最後に明示的に閉じる。
    all_ids = [
        int(x) for x in session.scalars(select(Employee.id).order_by(Employee.id)).all()
    ]
    queries = load_eval_queries()
    cache = {}
    for q in queries:
        res = retriever.search(q.query)
        cache[q.id] = (res, decide_route(res).route)

    def rank(query, candidates):
        if not query.gold_topics or not candidates:
            return []
        out_ = scorer.rank(query.gold_topics, candidates, None, NOW, top_k=10)
        return [r["person_id"] for r in out_["recommendations"]]

    def as_is(query, res, route):
        if route == "document":
            return []
        pinned = _pinned_responder(res) if route == "prior_answer" else None
        return rank(
            query, [pinned] if pinned is not None else list(res["candidate_people"])
        )

    variants = {
        "現状（そのまま）": as_is,
        "経路の pin を外す（C4 の候補10名）": lambda q, res, route: rank(
            q, list(res["candidate_people"])
        ),
        "候補を全社員にする（#87）": lambda q, res, route: rank(q, all_ids),
    }

    report = []
    for label, fn in variants.items():
        results = [
            QueryResult(
                ranked_experts=fn(q, *cache[q.id]),
                gold_experts=list(q.gold_experts),
                predicted_route=cache[q.id][1],
                gold_route=q.gold_route,
                difficulty=q.difficulty,
                gold_experts_alt=list(getattr(q, "gold_experts_alt", []) or []),
            )
            for q in queries
        ]
        metrics = evaluate(results)
        layers = evaluate_by_difficulty(results)
        line = " ".join(
            f"{k}:{v.recall_at_3:.3f}" for k, v in layers.items() if k != "L4"
        )
        print(
            f"{label:34s} R@3={metrics.recall_at_3:.3f} Top1={metrics.top1_accuracy:.3f} "
            f"MRR={metrics.mrr:.3f} | {line}"
        )
        report.append(
            {
                "name": label,
                "recall_at_3": metrics.recall_at_3,
                "top1": metrics.top1_accuracy,
                "mrr": metrics.mrr,
                "by_difficulty": {k: v.recall_at_3 for k, v in layers.items()},
            }
        )
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"wrote {out}")
    session.close()
    session.get_bind().dispose()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=["prepare", "route", "variants"])
    ap.add_argument(
        "--pgdir",
        default=DEFAULT_PGDIR,
        help="pgserver のデータディレクトリ（使い回す）",
    )
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    url = start_db(args.pgdir)
    print(f"DB: {url}")
    if args.task == "prepare":
        prepare(url)
    elif args.task == "route":
        task_route(url, args.out)
    else:
        task_variants(url, args.out)


if __name__ == "__main__":
    main()
