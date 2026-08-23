#!/usr/bin/env python3
"""research_robustness.py — 採用の前提を崩しかねない3点を測る（#80）。

#65 / #73 で「トピック媒介にすると層2 Recall@3 が +0.124（分割検証）」と分かったが、
その改善は**実質 `answers`（過去回答）の証拠に乗っている**（回答を外すと基準を下回る）。
TEKIJIN は回答ログが空から始まるので、そこを測らずに採用は決められない。

  1. コールドスタート耐性 … 回答を間引いて degradation curve を引く
  2. L3 の統合方法       … 複数トピックを「足す」以外の混ぜ方
  3. 拠点制約の扱い       … `constraint` を無視している現状のコスト

    python scripts/research_robustness.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
        --llm-dir docs/benchmarks/ablation
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import research_ablation as A
import research_corpus as rc
import research_rank as rr
import research_topic as rt

FRACTIONS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)
SEED = 42


def load_topics(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    out = {}
    for d in records:
        try:
            out[d["id"]] = [
                t for t in json.loads(d["content"])["topics"] if t != "該当なし"
            ]
        except (ValueError, KeyError):
            continue
    return out



def keep_answers(fx, fraction, seed=SEED):
    """回答を決定的に間引く。fraction=1.0 で全件、0.0 で空。"""
    answers = fx["answers"]
    if fraction >= 1.0:
        return answers
    rng = random.Random(seed)
    keep = rng.sample(range(len(answers)), round(len(answers) * fraction))
    return [answers[i] for i in sorted(keep)]


# --------------------------------------------------------------------------- #
# 1. コールドスタート
# --------------------------------------------------------------------------- #
def cold_start(ctx, model, ctopics, llm_plain, llm_ctx):
    fx = ctx["fx"]
    rows = []
    print(
        "== 1. コールドスタート耐性（回答ログを間引く。gold は projects+daily 由来なので動かない）=="
    )
    header = (
        f"{'回答ログ':>8s} {'現行 Dense集約':>14s} {'検索由来topic→構造化':>20s} "
        f"{'LLM(文脈なし)→構造化':>20s} {'LLM(文脈つき)→構造化':>20s} {'段A 検索由来':>12s}"
    )
    print(header)
    print("-" * len(header))
    for f in FRACTIONS:
        kept = keep_answers(fx, f)
        kept_ids = {f"ans:{a['id']}" for a in kept}
        # 間引いた回答チャンクは検索結果から落とす（索引に無いのと同じ）
        drop = {
            c for c in ctx["chunk_ids"] if c.startswith("ans:") and c not in kept_ids
        }

        def dense_ranked(item, qi, drop=drop):
            ranked, _ = A.dense_chunk_rank(ctx, model, qi, False, 200)
            return [c for c in ranked if c not in drop][:64]

        def dense_system(c, item, qi):
            return rr.to_ranking(
                rr.aggregate_people(
                    dense_ranked(item, qi), c["owners"], rc.SOURCE_WEIGHT, top_n=20
                )
            )

        def topic_system(topics_of, kept=kept):
            def system(c, item, qi):
                return rt.rank_experts_for_topics(
                    fx, topics_of(item, qi)[:1], answers=kept
                )

            return system

        def retrieval_topic(item, qi):
            return rt.predict_topic_from_ranking(dense_ranked(item, qi), ctopics, 20)

        res = {
            "dense": A.evaluate(ctx, dense_system),
            "retr": A.evaluate(ctx, topic_system(retrieval_topic)),
            "llm_plain": A.evaluate(
                ctx, topic_system(lambda it, qi: llm_plain.get(it["id"], []))
            ),
            "llm_ctx": A.evaluate(
                ctx, topic_system(lambda it, qi: llm_ctx.get(it["id"], []))
            ),
        }
        acc = np.mean(
            [
                1.0
                if (p := retrieval_topic(it, ctx["qid_pos"][it["id"]]))[:1]
                and p[0] in set(it["gold_topics"] or [])
                else 0.0
                for it in ctx["items"]
            ]
        )
        print(
            f"{f * 100:7.0f}% {res['dense']['R@3']:14.3f} {res['retr']['R@3']:20.3f} "
            f"{res['llm_plain']['R@3']:20.3f} {res['llm_ctx']['R@3']:20.3f} {acc:12.3f}"
        )
        rows.append(
            {
                "fraction": f,
                "dense": res["dense"]["R@3"],
                "retrieval_topic": res["retr"]["R@3"],
                "llm_plain_topic": res["llm_plain"]["R@3"],
                "llm_ctx_topic": res["llm_ctx"]["R@3"],
                "stageA_retrieval_acc1": float(acc),
            }
        )
    print(
        "\n  ※ LLM(文脈つき) の分類だけは全件コーパスで作った固定の出力を使っている（上限寄りの値）。\n"
        "     LLM(文脈なし) はクエリだけで分類しているので、どの間引き率でもそのまま成立する。"
    )
    return rows


# --------------------------------------------------------------------------- #
# 2. L3 の統合方法
# --------------------------------------------------------------------------- #
def l3_fusion(ctx, base, llm_ctx):
    fx = ctx["fx"]
    n_l3 = sum(1 for it in ctx["items"] if it.get("difficulty") == "L3")
    print(f"\n== 2. 複数トピックの統合方法（L3 = 2分野にまたがる相談 {n_l3}件）==")
    rows = []
    for k, mode in [
        (1, "weighted_sum"),
        (2, "weighted_sum"),
        (2, "znorm_max"),
        (2, "round_robin"),
        (2, "union_top2"),
        (3, "round_robin"),
    ]:

        def system(c, item, qi, k=k, mode=mode):
            return rt.rank_experts_for_topics(
                fx, llm_ctx.get(item["id"], [])[:k], mode=mode
            )

        r = A.evaluate(ctx, system)
        lo, hi, _p = A.paired_bootstrap(base["hits"], r["hits"])
        label = f"上位{k} / {mode}"
        print(
            f"  {label:26s} 全体={r['R@3']:.3f} Δ={r['R@3'] - base['R@3']:+.3f} "
            f"[{lo:+.3f},{hi:+.3f}] L1={r.get('L1', 0):.2f} L2={r.get('L2', 0):.2f} L3={r.get('L3', 0):.2f}"
        )
        rows.append(
            {
                "name": label,
                "R@3": r["R@3"],
                "L1": r.get("L1"),
                "L2": r.get("L2"),
                "L3": r.get("L3"),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 3. 拠点制約
# --------------------------------------------------------------------------- #
def constraints(ctx, base, llm_ctx):
    fx = ctx["fx"]
    branch_of = {e["id"]: e.get("branch") for e in fx["employees"]}
    idx = [i for i, it in enumerate(ctx["items"]) if it.get("constraint")]
    print(
        f"\n== 3. 拠点制約の扱い（制約つき {len(idx)} 件 / 全体 {len(ctx['items'])} 件）=="
    )
    if not idx:
        return []

    def topic_rank(item):
        return rt.rank_experts_for_topics(fx, llm_ctx.get(item["id"], [])[:1])

    def apply(mode):
        def system(c, item, qi):
            ranked = topic_rank(item)
            want = (item.get("constraint") or {}).get("branch")
            if not want or mode == "ignore":
                return ranked
            if mode == "filter":
                inside = [e for e in ranked if branch_of.get(e) == want]
                return inside + [e for e in ranked if branch_of.get(e) != want]
            # boost: 拠点一致を順位融合で持ち上げる（C6 の proximity 項に相当する扱い）
            same = [e for e in ranked if branch_of.get(e) == want]
            return rr.rrf_fuse([ranked, same], weights=[1.0, 0.5])

        return system

    rows = []
    for mode, label in [
        ("ignore", "制約を無視（現状）"),
        ("boost", "拠点一致を加点"),
        ("filter", "拠点で絞ってから並べる"),
    ]:
        r = A.evaluate(ctx, apply(mode))
        sub = r["hits"][idx].mean()
        lo, hi, _p = A.paired_bootstrap(base["hits"], r["hits"])
        print(
            f"  {label:22s} 制約つき{len(idx)}件={sub:.3f}  全体={r['R@3']:.3f} "
            f"Δ={r['R@3'] - base['R@3']:+.3f} [{lo:+.3f},{hi:+.3f}]"
        )
        # delta / ci も保存する（#158: 区間がコンソールにしか無いと手写しになる）。
        rows.append(
            {
                "name": label,
                "constrained": float(sub),
                "overall": r["R@3"],
                "delta": r["R@3"] - base["R@3"],
                "ci": [lo, hi],
            }
        )
    return rows


# 拠点名と、それを一意に指す地域名・言い換え。#84 で評価セットの制約文を散らしたので、
# 「素朴な文字列一致では取れない」ことを毎回この場で測れるようにしておく。
BRANCHES = ("本社", "東京", "名古屋", "大阪", "福岡")


def naive_branch(query):
    """拠点名がそのまま書かれている場合だけ拾う、いちばん素朴な抽出。"""
    for b in BRANCHES:
        if b in query:
            return b
    return None


def constraint_extraction(ctx):
    """制約の「抽出しやすさ」を測る（#84）。

    以前の評価セットは制約つき5件が同じ文型で終わっていたため、この素朴な抽出が
    5/5・誤検出0 で通ってしまい、抽出の難しさを一切測れていなかった。
    """
    items = ctx["items"]
    tp = fn = fp = 0
    missed, false_hits = [], []
    for it in items:
        want = (it.get("constraint") or {}).get("branch")
        got = naive_branch(it["query"])
        if want:
            if got == want:
                tp += 1
            else:
                fn += 1
                missed.append({"id": it["id"], "want": want, "got": got})
        elif got:
            fp += 1
            false_hits.append({"id": it["id"], "got": got})
    n_con = tp + fn
    print(f"\n== 4. 制約の抽出しやすさ（素朴な文字列一致）==")
    print(f"  再現率 {tp}/{n_con}  誤検出 {fp} 件（制約なし {len(items) - n_con} 件中）")
    for m in missed:
        print(f"    取り逃し id {m['id']}: 正解={m['want']} 抽出={m['got']}")
    for m in false_hits:
        print(f"    誤検出   id {m['id']}: 抽出={m['got']}（制約なし）")
    return {
        "recall": tp / n_con if n_con else 0.0,
        "n_constrained": n_con,
        "false_positives": fp,
        "missed": missed,
        "false_hits": false_hits,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", required=True)
    ap.add_argument("--llm-dir", default="docs/benchmarks/ablation")
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ctx = A.build_context([args.emb])
    ctopics = rt.chunk_topics(ctx["fx"])
    # **読むファイルは全部検査する。** 1本だけ守っても、他が同じ事故を起こす。
    for fn in ("llm_topic.json", "llm_topic_ctx.json"):
        rc.assert_llm_ids_match(os.path.join(args.llm_dir, fn))
    llm_plain = load_topics(os.path.join(args.llm_dir, "llm_topic.json"))
    llm_ctx = load_topics(os.path.join(args.llm_dir, "llm_topic_ctx.json"))
    base = A.evaluate(ctx, A.make_system(model=args.model))
    print(f"採点対象 {len(ctx['items'])} 件 / 基準（Dense 集約）= {base['R@3']:.3f}\n")

    result = {
        "base": {"R@3": base["R@3"]},
        "cold_start": cold_start(ctx, args.model, ctopics, llm_plain, llm_ctx),
        "l3_fusion": l3_fusion(ctx, base, llm_ctx),
        "constraints": constraints(ctx, base, llm_ctx),
        "constraint_extraction": constraint_extraction(ctx),
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n結果を書き出し: {args.out}")


if __name__ == "__main__":
    main()
