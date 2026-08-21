#!/usr/bin/env python3
"""research_pipeline.py — トピック媒介パイプラインの評価（#65 の段D〜I）。

`research_ablation.py` が「埋め込みだけを変える」実験なのに対し、こちらは
**LLM を使う段を足したパイプライン**を測る。LLM の出力は `research_llm.py` が
先に書き出した JSON（`docs/benchmarks/ablation/`）を読むだけなので、GPU なしで再現できる。

段の分解（なぜ分けるかは research_topic.py の docstring）:
  段A  query → topic の的中率
  段C  topic → 人の並び（層2 Recall@3）

    python scripts/research_pipeline.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
        --llm-dir docs/benchmarks/ablation [--extra-emb emb/emb_extra.npz]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_ablation as A
import research_corpus as rc
import research_rank as rr
import research_topic as rt

NONE_LABEL = "該当なし"


def load_topics(path):
    """research_llm.py の topic 系出力 → {query_id: [topic, ...]}"""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for d in records:
        try:
            out[d["id"]] = [
                t for t in json.loads(d["content"])["topics"] if t != NONE_LABEL
            ]
        except (ValueError, KeyError):
            continue
    return out


def load_votes(path):
    """自己整合性: 複数ドローを順位重み投票でまとめる。"""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for d in records:
        counter = collections.Counter()
        for draw in d.get("draws", []):
            try:
                topics = json.loads(draw)["topics"]
            except ValueError:
                continue
            for i, t in enumerate(topics):
                counter[t] += 1.0 / (i + 1)
        out[d["id"]] = [t for t, _ in counter.most_common() if t != NONE_LABEL]
    return out


def load_order(rank_path, payload_path):
    """listwise リランクの出力 → {query_id: (並べ替え後, 元の並び)}"""
    if not (os.path.exists(rank_path) and os.path.exists(payload_path)):
        return {}
    with open(payload_path, encoding="utf-8") as f:
        payload = {c["id"]: c for c in json.load(f)}
    with open(rank_path, encoding="utf-8") as f:
        records = json.load(f)
    out = {}
    for d in records:
        try:
            order = json.loads(d["content"])["order"]
        except (ValueError, KeyError):
            order = []
        cands = payload[d["id"]]["candidates"]
        by_no = {c["no"]: c["employee_id"] for c in cands}
        seen = []
        for no in order:
            eid = by_no.get(no)
            if eid and eid not in seen:
                seen.append(eid)
        for c in cands:
            if c["employee_id"] not in seen:
                seen.append(c["employee_id"])
        out[d["id"]] = (seen, [c["employee_id"] for c in cands])
    return out


def topic_accuracy(items, predict):
    a1 = np.mean(
        [
            1.0
            if (p := predict(it))[:1] and p[0] in set(it["gold_topics"] or [])
            else 0.0
            for it in items
        ]
    )
    a3 = np.mean(
        [
            1.0 if set(predict(it)[:3]) & set(it["gold_topics"] or []) else 0.0
            for it in items
        ]
    )
    return float(a1), float(a3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", required=True)
    ap.add_argument("--llm-dir", default="docs/benchmarks/ablation")
    ap.add_argument("--extra-emb", default=None, help="HyDE/専門語の埋め込み（任意）")
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--gold",
        default="gold_experts",
        choices=["gold_experts", "gold_experts_alt"],
        help="gold_experts_alt は answers だけから導出した第2の正解（#73）",
    )
    args = ap.parse_args()

    ctx = A.build_context([args.emb], gold_key=args.gold)
    fx, items = ctx["fx"], ctx["items"]
    ctopics = rt.chunk_topics(fx)
    model = args.model
    d = args.llm_dir

    llm_plain = load_topics(os.path.join(d, "llm_topic.json"))
    llm_ctx = load_topics(os.path.join(d, "llm_topic_ctx.json"))
    llm_ab = load_topics(os.path.join(d, "llm_topic_abstain.json"))
    llm_sc = load_votes(os.path.join(d, "llm_topic_sc.json"))
    rerank2 = load_order(
        os.path.join(d, "llm_rerank2.json"), os.path.join(d, "payload_rerank2.json")
    )

    extra = (
        np.load(args.extra_emb)
        if args.extra_emb and os.path.exists(args.extra_emb)
        else None
    )
    row = {it["id"]: i for i, it in enumerate(items)}
    chunk_emb = ctx["models"][model]["chunks"][: ctx["n_base"]]

    def retrieval_topic(item, key="query", top_n=20):
        if key == "query":
            ranked, _ = A.dense_chunk_rank(
                ctx, model, ctx["qid_pos"][item["id"]], False, 64
            )
        else:
            sims = extra[key][row[item["id"]]] @ chunk_emb.T
            ranked = [ctx["chunk_ids"][j] for j in np.argsort(-sims)[:64]]
        return rt.predict_topic_from_ranking(ranked, ctopics, top_n)

    predictors = {
        "検索由来のみ": lambda it: retrieval_topic(it),
        "LLM(文脈なし)": lambda it: llm_plain.get(it["id"], []),
        "LLM(検索文脈つき)": lambda it: llm_ctx.get(it["id"], []),
        "LLM(該当なし選択肢つき)": lambda it: llm_ab.get(it["id"], []),
        "LLM(自己整合性5回)": lambda it: llm_sc.get(it["id"], []),
        "LLM(文脈つき)+検索由来": lambda it: rr.rrf_fuse(
            [llm_ctx.get(it["id"], []), retrieval_topic(it)]
        ),
    }
    if extra is not None:
        predictors["LLM(文脈つき)+検索由来(専門語)"] = lambda it: rr.rrf_fuse(
            [llm_ctx.get(it["id"], []), retrieval_topic(it, "q_q2d__q")]
        )
        predictors["検索由来(クエリ+HyDE)"] = lambda it: retrieval_topic(
            it, "q_hyde__q"
        )

    print(f"採点対象 {len(items)} 件\n== 段A: query → topic ==")
    acc_rows = []
    for name, fn in predictors.items():
        a1, a3 = topic_accuracy(items, fn)
        acc_rows.append({"name": name, "acc@1": a1, "acc@3": a3})
        print(f"  {name:30s} acc@1={a1:.3f} acc@3={a3:.3f}")

    base = A.evaluate(ctx, A.make_system(model=model))
    print(f"\n== 段C: 層2 Recall@3（基準 dense集約 = {base['R@3']:.3f}）==")
    systems = {
        f"{name} → 構造化": (
            lambda c, it, qi, fn=fn: rt.rank_experts_for_topics(fx, fn(it)[:1])
        )
        for name, fn in predictors.items()
    }
    if rerank2:
        systems["LLM(文脈つき)+検索由来 → 構造化 → listwiseリランクRRF"] = (
            lambda c, it, qi: rr.rrf_fuse(list(rerank2[it["id"]]))
        )
    rows = []
    for name, system in systems.items():
        r = A.evaluate(ctx, system)
        lo, hi, p = A.paired_bootstrap(base["hits"], r["hits"])
        rows.append(
            {
                "name": name,
                "R@3": r["R@3"],
                "delta": r["R@3"] - base["R@3"],
                "ci": [lo, hi],
                "p_gt0": p,
                "MRR": r["MRR"],
                "L1": r.get("L1"),
                "L2": r.get("L2"),
                "L3": r.get("L3"),
            }
        )
        print(
            f"  {name:44s} R@3={r['R@3']:.3f} Δ={r['R@3'] - base['R@3']:+.3f} "
            f"[{lo:+.3f},{hi:+.3f}] P={p:.2f} MRR={r['MRR']:.3f}"
        )

    hold = holdout_check(ctx, base, systems)
    print(
        f"\n== 分割検証（半分で構成を選び、残りで測る／200回）==\n"
        f"  改善: 平均 {hold['mean']:+.3f} / 中央 {hold['median']:+.3f} / "
        f"5-95%tile [{hold['p5']:+.3f},{hold['p95']:+.3f}] / 改善した割合 {hold['win_rate']:.2f}"
    )

    print("\n== 段B': 棄却（L4 5件 vs 採点対象45件）==")
    abst = abstention_table(os.path.join(d, "llm_abstain_conf.json"), items)
    for th, tp, fp in abst:
        print(f"  confidence<{th:3d}: L4を棄却 {tp}/5   誤って棄却 {fp}/45")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "stageA": acc_rows,
                    "stageC": rows,
                    "holdout": hold,
                    "abstention": abst,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n結果を書き出し: {args.out}")


def holdout_check(ctx, base, systems, reps=200, seed=42):
    """全件で一番良い構成を選ぶと多重比較で楽観的になる。半分で選んで残りで測る。"""
    hits = np.stack([A.evaluate(ctx, s)["hits"] for s in systems.values()])
    rng = np.random.default_rng(seed)
    n = hits.shape[1]
    gains = []
    for _ in range(reps):
        perm = rng.permutation(n)
        tr, te = perm[: n // 2], perm[n // 2 :]
        best = int(np.argmax(hits[:, tr].mean(axis=1)))
        gains.append(hits[best, te].mean() - base["hits"][te].mean())
    gains = np.array(gains)
    return {
        "mean": float(gains.mean()),
        "median": float(np.median(gains)),
        "p5": float(np.percentile(gains, 5)),
        "p95": float(np.percentile(gains, 95)),
        "win_rate": float((gains > 0).mean()),
    }


def abstention_table(path, items):
    if not os.path.exists(path):
        return []
    person, _retrieval = rc.load_eval()
    l4 = {q["id"] for q in person if q["difficulty"] == "L4"}
    conf = {}
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for d in records:
        try:
            conf[d["id"]] = json.loads(d["content"])["confidence"]
        except (ValueError, KeyError):
            continue
    out = []
    for th in (20, 30, 50, 70, 80):
        tp = sum(1 for i in l4 if conf.get(i, 100) < th)
        fp = sum(1 for it in items if conf.get(it["id"], 100) < th)
        out.append((th, tp, fp))
    return out


if __name__ == "__main__":
    main()
