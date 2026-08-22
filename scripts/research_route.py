#!/usr/bin/env python3
"""research_route.py — 経路判定 C5 の精度を測る（#88）。

`eval_person.json` には全件に `gold_route`（person / prior_answer / document / none）が入っているが、
**測られていなかった**。C5（`tekijin.agent.route`）の閾値もコード上 "tunable on eval" のまま。

C5 の `decide_route` を**そのまま import** して、C4 の3チャネルの絶対コサイン類似度を
埋め込みから再現して渡す。

  answer_confidence   … 過去回答 と「回答のある過去質問」の近さの最大（retriever と同じ定義）
  document_confidence … 社内文書の近さの最大
  people_confidence   … プロフィールの近さの最大

    python scripts/research_route.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "src")
)

import research_ablation as A
import research_corpus as rc
from tekijin.agent.route import (
    DOCUMENT_SIM,
    PERSON_WEAK_SIM,
    PRIOR_ANSWER_SIM,
    decide_route,
)

ROUTES = ("person", "prior_answer", "document", "none")


def channel_confidences(ctx, model, meta):
    """クエリごとに3チャネルの最大コサインを出す。C4 の *_confidence に対応する。"""
    z = ctx["models"][model]
    chunk_ids = ctx["chunk_ids"][: ctx["n_base"]]
    chunks = z["chunks"][: ctx["n_base"]]
    kinds = np.array([c.split(":")[0] for c in chunk_ids])
    sims = z["queries"] @ chunks.T
    qsims = z["queries"] @ z["questions"].T  # 回答のある過去質問

    out = {}
    for qi, qid in enumerate(z["query_ids"]):
        ans = float(sims[qi][kinds == "ans"].max())
        out[qid] = {
            # retriever は「過去回答 or 回答のある質問」の近い方を採る
            "answer_confidence": max(ans, float(qsims[qi].max())),
            "document_confidence": float(sims[qi][kinds == "doc"].max()),
            "people_confidence": float(sims[qi][kinds == "profile"].max()),
        }
    return out


def predict(conf, thresholds, has_people=True, has_answers=True):
    decision = decide_route(
        {
            **conf,
            "candidate_people": [1] if has_people else [],
            "past_answers": [1] if has_answers else [],
        },
        prior_answer_sim=thresholds[0],
        document_sim=thresholds[1],
        person_weak_sim=thresholds[2],
    )
    return decision.route


def accuracy(items, conf, thresholds):
    hits = np.array(
        [
            1.0 if predict(conf[it["id"]], thresholds) == it["gold_route"] else 0.0
            for it in items
        ]
    )
    return hits


def confusion(items, conf, thresholds):
    table = collections.Counter()
    for it in items:
        table[(it["gold_route"], predict(conf[it["id"]], thresholds))] += 1
    return table


def print_confusion(table, golds=("person", "prior_answer", "document")):
    preds = ("person", "prior_answer", "document")
    print(f"    {'gold＼予測':16s}" + "".join(f"{p:>14s}" for p in preds))
    for g in golds:
        row = "".join(f"{table[(g, p)]:14d}" for p in preds)
        print(f"    {g:16s}{row}")


def sweep(items, conf, folds=5, seed=42):
    """閾値のスイープ。学習 fold で選び、検証 fold で測る。"""
    grid = [
        (pa, dc, pw)
        for pa in np.arange(0.55, 0.96, 0.05)
        for dc in np.arange(0.40, 0.86, 0.05)
        for pw in np.arange(0.30, 0.81, 0.05)
    ]
    hits = np.stack([accuracy(items, conf, t) for t in grid])
    rng = np.random.default_rng(seed)
    parts = np.array_split(rng.permutation(len(items)), folds)
    picked, scores = [], []
    for i in range(folds):
        test = parts[i]
        train = np.concatenate([parts[j] for j in range(folds) if j != i])
        best = int(np.argmax(hits[:, train].mean(axis=1)))
        picked.append(grid[best])
        scores.append(hits[best, test].mean())
    return grid, hits, picked, float(np.mean(scores))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", required=True)
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--llm-dir", default="docs/benchmarks/ablation")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ctx = A.build_context([args.emb])
    with open(args.emb + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    ctx["models"][args.model]["query_ids"] = meta["query_ids"]
    ctx["models"][args.model]["questions"] = np.load(args.emb)["questions"]
    conf = channel_confidences(ctx, args.model, meta)

    person, _retrieval = rc.load_eval()
    # 棄却(none)は #80 で別段（証拠十分性）が効くと分かっているので、経路3値の分類とは分けて測る
    items = [q for q in person if q["gold_route"] != "none"]
    dist = collections.Counter(q["gold_route"] for q in items)
    print(
        f"経路つき {len(items)} 件（none 15件は #80 の別段で扱うため除外）: {dict(dist)}"
    )
    majority = max(dist.values()) / len(items)
    print(f"多数決ベースライン（常に person）= {majority:.3f}\n")

    default = (PRIOR_ANSWER_SIM, DOCUMENT_SIM, PERSON_WEAK_SIM)
    print(f"== 1. 現在の閾値 {default} ==")
    hits = accuracy(items, conf, default)
    print(f"  経路正解率 = {hits.mean():.3f}")
    print_confusion(confusion(items, conf, default))

    print("\n== 2. チャネル類似度の分布（閾値が現実的かを見る）==")
    for name in ("answer_confidence", "document_confidence", "people_confidence"):
        vals = np.array([conf[it["id"]][name] for it in items])
        print(
            f"  {name:20s} 最小{vals.min():.2f} 25%{np.percentile(vals, 25):.2f} "
            f"中央{np.median(vals):.2f} 75%{np.percentile(vals, 75):.2f} 最大{vals.max():.2f}"
        )

    print("\n== 3. 閾値のスイープ（5分割交差検証）==")
    grid, grid_hits, picked, cv = sweep(items, conf)
    best_full = int(np.argmax(grid_hits.mean(axis=1)))
    print(f"  グリッド {len(grid)} 通り")
    print(
        f"  全件で最良: {tuple(round(v, 2) for v in grid[best_full])} → {grid_hits[best_full].mean():.3f}（楽観的）"
    )
    print(f"  交差検証でのスコア: {cv:.3f}（現在の閾値との差 {cv - hits.mean():+.3f}）")
    for i, t in enumerate(picked):
        print(f"    fold{i + 1} が選んだ閾値: {tuple(round(v, 2) for v in t)}")

    print("\n== 4. 調整後の閾値での混同行列 ==")
    tuned = picked[0]
    print(
        f"  閾値 {tuple(round(float(v), 2) for v in tuned)} → "
        f"正解率 {accuracy(items, conf, tuned).mean():.3f}"
    )
    print_confusion(confusion(items, conf, tuned))

    print("\n== 5. 比較: トピックから経路を引き当てる ==")
    print(
        "  ※ 評価セットの route はトピックごとのコーパス統計から決めている（route_for）。"
    )
    print(
        "     これは**ラベルの作り方をなぞっている**上限であって、独立な手法ではない。"
    )
    print(
        "     人手ラベル由来の21件は route を person で固定しているので、自動ラベルだけで測る。"
    )
    auto = [q for q in items if q["label_source"] == "auto:project_daily"]
    dist_auto = collections.Counter(q["gold_route"] for q in auto)
    print(
        f"  自動ラベル {len(auto)} 件: {dict(dist_auto)} / "
        f"多数決 {max(dist_auto.values()) / len(auto):.3f}"
    )
    print(
        f"  現在の閾値                   経路正解率 = {accuracy(auto, conf, default).mean():.3f}"
    )
    print(
        f"  調整後の閾値                 経路正解率 = {accuracy(auto, conf, tuned).mean():.3f}"
    )
    topic_route = {}
    for q in person:
        if q["label_source"] != "auto:project_daily":
            continue
        for t in q["gold_topics"] or []:
            topic_route.setdefault(t, q["gold_route"])
    llm = {}
    path = os.path.join(args.llm_dir, "llm_topic_ctx.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for d in json.load(f):
                try:
                    llm[d["id"]] = [
                        t for t in json.loads(d["content"])["topics"] if t != "該当なし"
                    ]
                except (ValueError, KeyError):
                    continue
    for label, topic_of in [
        ("gold トピックから引く", lambda it: (it["gold_topics"] or [None])[0]),
        ("LLM 予測トピックから引く", lambda it: (llm.get(it["id"]) or [None])[0]),
    ]:
        acc = np.mean(
            [
                1.0
                if topic_route.get(topic_of(it), "person") == it["gold_route"]
                else 0.0
                for it in auto
            ]
        )
        print(f"  {label:26s} 経路正解率 = {acc:.3f}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "n": len(items),
                    "distribution": dict(dist),
                    "majority": majority,
                    "default_thresholds": list(default),
                    "default_accuracy": float(hits.mean()),
                    "cv_accuracy": cv,
                    "picked": [[round(float(v), 2) for v in t] for t in picked],
                    "tuned_accuracy": float(accuracy(items, conf, picked[0]).mean()),
                    "best_full": [round(float(v), 2) for v in grid[best_full]],
                    "best_full_accuracy": float(grid_hits[best_full].mean()),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n結果を書き出し: {args.out}")


if __name__ == "__main__":
    main()
