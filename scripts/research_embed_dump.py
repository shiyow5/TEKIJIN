#!/usr/bin/env python3
"""research_embed_dump.py — アブレーション用に埋め込みを1回だけ計算して保存する（#65）。

GPU ホストで実行し、出力 `.npz` をローカルへ持ち帰る想定。集約・融合・グラフ系の実験は
埋め込みを固定したまま何十通りも試すので、**エンコードを毎回やり直さない**ための分離。

    python scripts/research_embed_dump.py --models-dir ~/models --device cuda \
        --model Nemotron-3-Embed-1B-BF16 --out emb_nemotron.npz

`--extra-queries FILE`（1行1テキストの JSON 配列）で、HyDE などの派生クエリも同じ空間に載せられる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_corpus as rc

# prefix はモデルごとに作法が違う（索引時と検索時で一致させること）。bench_embeddings.py と同じ。
PREFIX = {
    "ruri-v3-310m": ("検索クエリ: ", "検索文書: "),
    "multilingual-e5-large": ("query: ", "passage: "),
    "bge-m3": ("", ""),
    "Qwen3-Embedding-0.6B": (
        "Instruct: 社内の相談内容に対して、詳しい社員や関連する社内資料を検索する\nQuery: ",
        "",
    ),
    "Nemotron-3-Embed-1B-BF16": ("query: ", "passage: "),
}


def encode(model, texts, prefix, batch_size=64):
    return model.encode(
        [prefix + t for t in texts],
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=os.path.expanduser("~/models"))
    ap.add_argument("--model", required=True, help=f"{list(PREFIX)}")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra-queries", default=None, help="JSON 配列のファイル")
    ap.add_argument("--skip-daily", action="store_true")
    args = ap.parse_args()

    if args.model not in PREFIX:
        raise SystemExit(f"未知のモデル: {args.model}")
    qp, dp = PREFIX[args.model]

    from sentence_transformers import SentenceTransformer

    fx = rc.load_all()
    chunks, _ = rc.build_chunks(fx, include_daily=not args.skip_daily)
    persons = rc.build_person_docs(fx)
    person_full = rc.build_person_docs(fx, include_daily=True)
    person_ids, _retrieval = rc.load_eval()
    queries = [q["query"] for q in person_ids]

    extra = []
    if args.extra_queries:
        with open(args.extra_queries, encoding="utf-8") as f:
            extra = json.load(f)

    path = os.path.join(args.models_dir, args.model)
    t0 = time.time()
    model = SentenceTransformer(path, device=args.device, trust_remote_code=True)
    print(f"load {time.time() - t0:.1f}s", flush=True)

    out = {}
    t0 = time.time()
    out["chunks"] = encode(model, [t for _, t in chunks], dp)
    print(f"chunks {len(chunks)} {time.time() - t0:.1f}s", flush=True)
    out["persons"] = encode(model, [t for _, t in persons], dp)
    out["persons_full"] = encode(model, [t for _, t in person_full], dp)
    out["queries"] = encode(model, queries, qp, batch_size=32)
    # C5（経路判定）は「回答した過去質問」への近さも見る（retriever._question_mapped_answer_ids）。
    # 経路の実測（#88）に要るので、質問文も同じ空間に載せておく。
    answered = {a["question_id"] for a in fx["answers"]}
    questions = [q for q in fx["questions"] if q["id"] in answered]
    out["questions"] = encode(model, [q["body"] for q in questions], dp)
    if extra:
        out["extra"] = encode(model, extra, qp, batch_size=32)

    meta = {
        "model": args.model,
        "chunk_ids": [c for c, _ in chunks],
        "person_ids": [c for c, _ in persons],
        "query_ids": [q["id"] for q in person_ids],
        "question_ids": [q["id"] for q in questions],
        "n_extra": len(extra),
    }
    np.savez_compressed(args.out, **out)
    with open(args.out + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    print(f"wrote {args.out} ({os.path.getsize(args.out) / 1e6:.1f}MB)")


if __name__ == "__main__":
    main()
