#!/usr/bin/env python3
"""research_c1_vocab.py — C1 の出力トピックを語彙・gold と突き合わせる（#113 / #158）。

`llm_faithful.md` §1・§4.6 の数字（構造化出力の成否 / 語彙に載ったトピック /
gold 的中 / 応答時間の分位点）は、これまで**手で数えて本文に書いていた**。
評価セットを増やすたびに全部ずれるので、ここで JSON に落として
`render_bench_docs.py` から表を生成する。

    python scripts/research_c1_vocab.py --out docs/benchmarks/ablation/c1_vocab.json

入力は `research_llm.py --task raw` が書いた `c1_*.json`。id は `p<eval_id>` /
`r<robustness_id>` の形なので、`p` のものだけを評価セットに突き合わせる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABL = os.path.join(REPO, "docs", "benchmarks", "ablation")
EVAL = os.path.join(REPO, "fixtures", "synthetic", "eval")

# 表に出す変種と、その出所ファイル。
VARIANTS = [
    ("製品のまま", "c1_faithful.json"),
    ("案1 プロンプト", "c1_prompt.json"),
    ("案2 enum", "c1_enum.json"),
    ("両方", "c1_both.json"),
]


def vocabulary():
    """C6 が照合に使う語彙。**製品と同じ作り方**（`research_faithful.topic_vocabulary`）を使う。"""
    import research_faithful as rf

    return set(rf.topic_vocabulary())


def gold_topics():
    with open(os.path.join(EVAL, "eval_person.json"), encoding="utf-8") as f:
        rows = json.load(f)
    scored = [r for r in rows if r["difficulty"] != "L4"]
    return {r["id"]: set(r["gold_topics"]) for r in scored}


def topics_of(row):
    """`arguments`（tool call の JSON 文字列）から topics を取り出す。読めなければ None。"""
    if row.get("error") or not row.get("arguments"):
        return None
    try:
        return list(json.loads(row["arguments"]).get("topics") or [])
    except (ValueError, AttributeError):
        return None


def analyse(path, vocab, gold):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    n = len(rows)
    structured = 0
    n_topics = 0
    n_in_vocab = 0
    hit_all = 0
    hit_top1 = 0
    scorable = 0
    lat = []
    length_cut = 0
    out_tokens = []
    for row in rows:
        lat.append(row.get("latency") or 0.0)
        if row.get("finish_reason") == "length":
            length_cut += 1
        if row.get("out_tokens"):
            out_tokens.append(row["out_tokens"])
        topics = topics_of(row)
        if topics is None:
            continue
        structured += 1
        n_topics += len(topics)
        n_in_vocab += sum(1 for t in topics if t in vocab)
        rid = row["id"]
        if not (isinstance(rid, str) and rid.startswith("p")):
            continue
        eid = int(rid[1:])
        if eid not in gold:
            continue
        scorable += 1
        g = gold[eid]
        if g & set(topics):
            hit_all += 1
        if topics and topics[0] in g:
            hit_top1 += 1
    return {
        "n": n,
        "structured": structured,
        "topics": n_topics,
        "topics_in_vocab": n_in_vocab,
        "scorable": scorable,
        "hit_all": hit_all,
        "hit_top1": hit_top1,
        "length_cut": length_cut,
        "out_tokens_median": float(np.median(out_tokens)) if out_tokens else None,
        "out_tokens_max": max(out_tokens) if out_tokens else None,
        "p50": float(np.percentile(lat, 50)),
        "p95": float(np.percentile(lat, 95)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=ABL)
    ap.add_argument("--out", default=os.path.join(ABL, "c1_vocab.json"))
    args = ap.parse_args()

    vocab = vocabulary()
    gold = gold_topics()
    out = {"vocabulary_size": len(vocab), "scored_queries": len(gold), "variants": []}

    for label, fn in VARIANTS:
        path = os.path.join(args.dir, fn)
        if not os.path.exists(path):
            raise SystemExit(f"{fn} が無い。`research_llm.py --task raw` を先に回すこと。")
        out["variants"].append({"name": label, "file": fn, **analyse(path, vocab, gold)})

    thinking = os.path.join(args.dir, "c1_thinking.json")
    if os.path.exists(thinking):
        out["thinking_on"] = {"name": "thinking を ON に戻す", "file": "c1_thinking.json",
                              **analyse(thinking, vocab, gold)}

    # gold トピックが空の相談は的中しようがない。上限をここに残す。
    out["hit_ceiling"] = sum(1 for g in gold.values() if g)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"語彙 {len(vocab)} 件 / 採点対象 {len(gold)} 件（gold ありは {out['hit_ceiling']} 件）")
    for v in out["variants"] + ([out["thinking_on"]] if "thinking_on" in out else []):
        print(f"  {v['name']:14s} 構造化 {v['structured']}/{v['n']}"
              f"  語彙内 {v['topics_in_vocab']}/{v['topics']}"
              f"  的中 {v['hit_all']}/{v['scorable']}（上位1件 {v['hit_top1']}）"
              f"  p50 {v['p50']:.2f}s p95 {v['p95']:.2f}s")
    print(f"書き出し: {args.out}")


if __name__ == "__main__":
    main()
