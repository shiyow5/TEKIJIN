#!/usr/bin/env python3
"""research_rerank.py — クロスエンコーダのリランカーを段2に挟む（#65）。GPU ホストで実行。

二段検索（bi-encoder で粗く引き、cross-encoder で並べ替える）は monoBERT/monoT5 以来の定番。
ここでは日本語を含む多言語リランカーを2種類比べる:
  bge-reranker-v2-m3   … BAAI。sentence-transformers の CrossEncoder で直接使える
  Qwen3-Reranker-0.6B  … Qwen。causal LM の "yes"/"no" ロジットを関連度に使う指示型

出力は {query_id: [chunk_id, ...]}（並べ替え後）。集約と採点はローカル側で行う。

    python scripts/research_rerank.py --emb emb_Nemotron-3-Embed-1B-BF16.npz \
        --reranker ~/models/bge-reranker-v2-m3 --kind bge --depth 50 --out rr_bge.json
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

QWEN_INSTRUCT = (
    "Given a user's internal help request, judge whether the document is evidence "
    "about who is knowledgeable or what the answer is."
)


def score_bge(path, pairs, device, batch_size=32):
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(path, device=device, trust_remote_code=True, max_length=512)
    return np.asarray(
        model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
    )


def score_qwen(path, pairs, device, batch_size=16):
    """Qwen3-Reranker は "yes"/"no" の対数尤度差を関連度に使う（モデルカードの手順）。"""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(path, padding_side="left")
    model = (
        AutoModelForCausalLM.from_pretrained(path, dtype=torch.float16)
        .to(device)
        .eval()
    )
    yes_id, no_id = tok.convert_tokens_to_ids("yes"), tok.convert_tokens_to_ids("no")
    prefix = (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on the "
        "Query and the Instruct provided. Note that the answer can only be "
        '"yes" or "no".<|im_end|>\n<|im_start|>user\n'
    )
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    out = []
    for i in range(0, len(pairs), batch_size):
        batch = [
            f"{prefix}<Instruct>: {QWEN_INSTRUCT}\n<Query>: {q}\n<Document>: {d[:1500]}{suffix}"
            for q, d in pairs[i : i + batch_size]
        ]
        enc = tok(
            batch, return_tensors="pt", padding=True, truncation=True, max_length=2048
        ).to(device)
        with torch.no_grad():
            logits = model(**enc).logits[:, -1, :]
        out.extend((logits[:, yes_id] - logits[:, no_id]).float().cpu().numpy())
    return np.asarray(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", required=True)
    ap.add_argument("--reranker", required=True)
    ap.add_argument("--kind", choices=["bge", "qwen"], required=True)
    ap.add_argument(
        "--depth", type=int, default=50, help="bi-encoder の上位いくつを並べ替えるか"
    )
    ap.add_argument("--include-daily", action="store_true")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    fx = rc.load_all()
    chunks_all, _ = rc.build_chunks(fx, include_daily=True)
    n_base = len(rc.build_chunks(fx, include_daily=False)[0])
    text_of = dict(chunks_all)
    ids_all = [c for c, _ in chunks_all]
    n = len(ids_all) if args.include_daily else n_base

    with open(args.emb + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    z = np.load(args.emb)
    sims = z["queries"] @ z["chunks"][:n].T

    person, _retrieval = rc.load_eval()
    items = rc.scored_person_items(person)
    qid_pos = {qid: i for i, qid in enumerate(meta["query_ids"])}

    pairs, spans = [], []
    for item in items:
        qi = qid_pos[item["id"]]
        order = np.argsort(-sims[qi])[: args.depth]
        cand = [ids_all[j] for j in order]
        spans.append((item["id"], cand))
        pairs.extend((item["query"], text_of[c]) for c in cand)

    t0 = time.time()
    scores = (score_bge if args.kind == "bge" else score_qwen)(
        args.reranker, pairs, args.device
    )
    elapsed = time.time() - t0

    out, pos = {}, 0
    for qid, cand in spans:
        s = scores[pos : pos + len(cand)]
        pos += len(cand)
        out[str(qid)] = [cand[j] for j in np.argsort(-s)]

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "reranker": os.path.basename(args.reranker),
                "depth": args.depth,
                "include_daily": args.include_daily,
                "total_s": elapsed,
                "per_query_ms": elapsed / len(items) * 1000,
                "rankings": out,
            },
            f,
            ensure_ascii=False,
        )
    print(
        f"wrote {args.out}  {elapsed:.1f}s ({elapsed / len(items) * 1000:.0f}ms/query)"
    )


if __name__ == "__main__":
    main()
