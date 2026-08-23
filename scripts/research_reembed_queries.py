#!/usr/bin/env python3
"""research_reembed_queries.py — 評価クエリの埋め込みだけを差し替える（#84）。

`research_embed_dump.py` はコーパス全体（チャンク3410件ほか）を計算し直すが、
評価クエリの**文面だけ**を直したときは71件を測り直せば足りる。共有GPU機で
他の作業と同時に動かすことがあるので、負荷と所要時間を最小にするためのもの。

    python scripts/research_reembed_queries.py \
        --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
        --models-dir ~/models --model Nemotron-3-Embed-1B-BF16

**安全弁**: 文面を変えていないクエリの埋め込みが元と一致するかを必ず確認する。
ここがずれるなら prefix か正規化の条件が `research_embed_dump.py` と違うということなので、
書き出さずに止まる（気づかずに別条件の埋め込みを混ぜるのがいちばん危ない）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_corpus as rc  # noqa: E402
from research_embed_dump import PREFIX, encode  # noqa: E402

# 「文面が変わった」と見なすコサインの下限。実測では、文面を変えていないクエリは
# cos >= 0.99994（中央値 1.000000）に収まり、変えたものは 0.72-0.93 だったので、
# この2つの間には桁で差がある。
#
# 成分ごとの最大差で見てはいけない。sentence-transformers は長さでまとめてバッチを組むので、
# 一部の文面を変えると**変えていない文のパディングまで変わり**、最大成分差は 2e-3 ほど動く。
# それを「変わった」と読むと誤検出する（実際 1 度やった）。コサインなら影響を受けない。
COS_SAME = 0.999


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", required=True, help="差し替える .npz")
    ap.add_argument("--models-dir", default=os.path.expanduser("~/models"))
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", default=None, help="既定は --emb を上書き")
    args = ap.parse_args()

    if args.model not in PREFIX:
        raise SystemExit(f"未知のモデル: {args.model}")
    qp, _dp = PREFIX[args.model]

    old = dict(np.load(args.emb))
    with open(args.emb + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)

    person_ids, _ = rc.load_eval()
    queries = [q["query"] for q in person_ids]
    ids = [q["id"] for q in person_ids]
    if ids != meta["query_ids"]:
        raise SystemExit(
            "評価クエリの id 構成が変わっている。件数や id が動いたなら "
            "research_embed_dump.py で全体を作り直すこと"
        )
    if old["queries"].shape[0] != len(queries):
        raise SystemExit(f"件数不一致: npz {old['queries'].shape[0]} vs 評価 {len(queries)}")

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        os.path.join(args.models_dir, args.model), device=args.device, trust_remote_code=True
    )
    new_q = encode(model, queries, qp, batch_size=32)

    # 安全弁: どの行が動いたかを、旧埋め込みとのコサインで見る。
    o = old["queries"]
    cos = (o * new_q).sum(axis=1) / (
        np.linalg.norm(o, axis=1) * np.linalg.norm(new_q, axis=1)
    )
    moved = [ids[i] for i in np.where(cos < COS_SAME)[0]]
    same_cos = cos[cos >= COS_SAME]
    print(f"変わった: {len(moved)} 件 {moved}（cos {cos[cos < COS_SAME].max():.4f} 以下）")
    print(f"一致した: {len(same_cos)} 件（cos 最小 {same_cos.min():.6f}）")
    if not moved:
        raise SystemExit("1件も変わっていない。--emb か fixtures を取り違えていないか確認すること")

    old["queries"] = new_q
    out = args.out or args.emb
    np.savez_compressed(out, **old)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f}MB)")


if __name__ == "__main__":
    main()
