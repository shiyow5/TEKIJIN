#!/usr/bin/env python3
"""research_encode_extra.py — HyDE / Query2doc の生成文を同じ埋め込み空間に載せる（#65）。

`research_llm.py --task hyde|q2d` の出力から派生クエリを組み立て、GPU ホストでエンコードする。
`research_pipeline.py --extra-emb` が読む `.npz` を作るのが目的。

順序は `research_corpus.scored_person_items()` と同じ（`research_pipeline.py` の row 対応に依存）。
非対称モデルは query 側と passage 側で prefix が違うので、**両方**を作って比較できるようにする。

    python scripts/research_encode_extra.py --models-dir ~/models --model Nemotron-3-Embed-1B-BF16 \
        --hyde llm_hyde.json --q2d llm_q2d.json --out emb_extra.npz
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_corpus as rc
from research_embed_dump import PREFIX


def load_texts(path):
    with open(path, encoding="utf-8") as f:
        return {d["id"]: (d["content"] or "").strip() for d in json.load(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=os.path.expanduser("~/models"))
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--hyde", required=True)
    ap.add_argument("--q2d", required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    qp, dp = PREFIX[args.model]
    person, _retrieval = rc.load_eval()
    items = rc.scored_person_items(person)
    hyde, q2d = load_texts(args.hyde), load_texts(args.q2d)

    variants = {"hyde": [], "q_hyde": [], "q_q2d": [], "q2d": []}
    for it in items:
        q, h, k = it["query"], hyde.get(it["id"], ""), q2d.get(it["id"], "")
        variants["hyde"].append(h)
        variants["q_hyde"].append(q + "\n" + h)
        variants["q_q2d"].append(q + " " + k)
        variants["q2d"].append(k)

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        os.path.join(args.models_dir, args.model),
        device=args.device,
        trust_remote_code=True,
    )

    def enc(texts, prefix):
        return model.encode(
            [prefix + t for t in texts],
            batch_size=16,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).astype(np.float32)

    out = {}
    for name, texts in variants.items():
        out[f"{name}__q"] = enc(texts, qp)
        out[f"{name}__d"] = enc(texts, dp)
    np.savez_compressed(args.out, **out)
    print(f"wrote {args.out} ({len(items)} 件 × {len(out)} 系統)")


if __name__ == "__main__":
    main()
