#!/usr/bin/env python3
"""research_confidence.py — 確信度ラベルの素性を評価する（#110）。

`research_e2e.py --task misrec` が残した1スロットごとの素性から、
**表を手で選べない形で**統計を出す。#110 の初版はここを手作業でやり、
証拠数の表から高精度の行を落としてしまった。

出す統計はすべてこのスクリプトが吐く。文書に書く数字はここからしか取らない。

    python scripts/research_confidence.py --slots docs/benchmarks/ablation/misrecommendation.json
"""

from __future__ import annotations

import argparse
import collections
import json

import numpy as np

BOOTSTRAP = 20000
SEED = 42
SPLIT_REPS = 200
SPLIT_SEED = 0  # 分割検証は別の乱数列。文書に書く 147/200 はこの seed の値


def auc(scores, labels):
    """順位付けの AUC。同値は 0.5 で数える。母集団を明示するために自前で書く。"""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=bool)
    pos, neg = scores[labels], scores[~labels]
    if not len(pos) or not len(neg):
        return float("nan")
    diff = pos[:, None] - neg[None, :]
    return float((np.sum(diff > 0) + 0.5 * np.sum(diff == 0)) / diff.size)


def within_query_auc(slots, key, sign=1):
    """**同じ問題の中だけ**で比べた AUC。

    問題をまたぐと「その問題が簡単か」が混ざる。素性が人を見分けられているかは
    同じ問題の中で比べないと分からない（#110 の初版はここを混ぜていた）。
    """
    num = den = 0.0
    by_q = collections.defaultdict(list)
    for s in slots:
        by_q[s["query_id"]].append(s)
    for group in by_q.values():
        pos = [sign * s[key] for s in group if s["hit"]]
        neg = [sign * s[key] for s in group if not s["hit"]]
        for a in pos:
            for b in neg:
                num += 1.0 if a > b else (0.5 if a == b else 0.0)
                den += 1
    return (num / den) if den else float("nan"), int(den)


def bootstrap_gap(slots, label_of, hi, lo, reps=BOOTSTRAP, seed=SEED):
    """`hi` と `lo` の正解率の差を、**問題単位**で再標本化して区間を出す。

    スロット単位で振ると、同じ問題の3枠が独立だと見なされて区間が狭くなる。
    """
    rng = np.random.default_rng(seed)
    by_q = collections.defaultdict(list)
    for s in slots:
        by_q[s["query_id"]].append(s)
    qs = sorted(by_q)

    def gap(data):
        a = [s["hit"] for s in data if label_of(s) == hi]
        b = [s["hit"] for s in data if label_of(s) == lo]
        return (np.mean(a) - np.mean(b)) if a and b else np.nan

    point = gap(slots)
    draws = []
    for _ in range(reps):
        pick = rng.choice(qs, size=len(qs), replace=True)
        data = [s for q in pick for s in by_q[q]]
        g = gap(data)
        if not np.isnan(g):
            draws.append(g)
    draws = np.array(draws)
    if not len(draws):
        # 片方のラベルが一度も出ないルール（現行の「低」など）は差を測れない
        return point, float("nan"), float("nan"), float("nan")
    return (
        point,
        float(np.percentile(draws, 2.5)),
        float(np.percentile(draws, 97.5)),
        float(np.mean(draws > 0)),
    )


def table(slots, keyfn, title, order=None):
    """群ごとの n と正解率を**全群**出す。件数の少ない群も落とさない。"""
    by = collections.defaultdict(list)
    for s in slots:
        by[keyfn(s)].append(s["hit"])
    keys = order or sorted(by)
    print(f"\n== {title} ==")
    for k in keys:
        if k not in by:
            continue
        print(f"  {k!s:28s} n={len(by[k]):4d}  正解率 {np.mean(by[k]):.3f}")


def has(s, src):
    return src in s["evidence_sources"]


def within_query_auc_ci(slots, key, sign=1, reps=5000, seed=SEED):
    """同一問題内 AUC の区間。**問題単位**で再標本化する。

    点推定だけ載せると、52対しか無いことが読み手に伝わらない。
    """
    rng = np.random.default_rng(seed)
    by_q = collections.defaultdict(list)
    for s in slots:
        by_q[s["query_id"]].append(s)
    qs = sorted(by_q)
    draws = []
    for _ in range(reps):
        pick = rng.choice(qs, size=len(qs), replace=True)
        data = [s for q in pick for s in by_q[q]]
        v, _ = within_query_auc(data, key, sign)
        if not np.isnan(v):
            draws.append(v)
    draws = np.array(draws)
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


# 実行時には使えない。**評価セットの gold 人数をどれだけ代理しているか**を測るためだけの
# 参照ルール。これと提案ルールの差が、提案ルールに含まれる「問題の難しさ」の分になる。
def _oracle_n_gold(s):
    if s["n_gold"] >= 4 and s["rank"] <= 2:
        return "高"
    if s["n_gold"] <= 2 and s["rank"] == 3:
        return "低"
    return "中"


RULES = {
    "現行": lambda s: s["confidence"],
    "順位だけ": lambda s: (
        "高" if s["rank"] == 1 else ("中" if s["rank"] == 2 else "低")
    ),
    "証拠の種類だけ": lambda s: (
        "低" if not has(s, "answer") else ("高" if not has(s, "project") else "中")
    ),
    "順位＋証拠の種類": lambda s: (
        "低"
        if not has(s, "answer")
        else ("高" if (s["rank"] <= 2 and not has(s, "project")) else "中")
    ),
    "【参照】gold人数を直接使う（実行時には使えない）": _oracle_n_gold,
}

# 分割検証で選ばせる候補。**現行は「低」が出ないため差を測れず、実質3案**である。
SPLIT_CANDIDATES = ("順位だけ", "証拠の種類だけ", "順位＋証拠の種類")


def split_half(slots, reps=SPLIT_REPS, seed=SPLIT_SEED):
    """問題単位で半分に割り、学習半分でルールを選び、残り半分で測る。

    **検証できるのは「候補から選ぶ」ところだけ**で、候補そのものを
    全データを見て設計した部分は検証できない（文書にもそう書くこと）。
    """
    rng = np.random.default_rng(seed)
    by_q = collections.defaultdict(list)
    for s in slots:
        by_q[s["query_id"]].append(s)
    qs = sorted(by_q)

    def spread(rule, data):
        a = [s["hit"] for s in data if rule(s) == "高"]
        b = [s["hit"] for s in data if rule(s) == "低"]
        return (np.mean(a) - np.mean(b)) if a and b else np.nan

    held, picked = [], collections.Counter()
    for _ in range(reps):
        perm = rng.permutation(qs)
        tr = [s for q in perm[: len(qs) // 2] for s in by_q[q]]
        te = [s for q in perm[len(qs) // 2 :] for s in by_q[q]]
        scored = [(spread(RULES[k], tr), k) for k in SPLIT_CANDIDATES]
        scored = [(v, k) for v, k in scored if not np.isnan(v)]
        if not scored:
            continue
        best = max(scored)[1]
        picked[best] += 1
        v = spread(RULES[best], te)
        if not np.isnan(v):
            held.append(v)
    return np.array(held), picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.slots, encoding="utf-8") as f:
        slots = json.load(f)["slots"]
    print(f"スロット {len(slots)} / 問題 {len({s['query_id'] for s in slots})}")

    print("\n== 1. 素性が当たり外れを見分けられるか ==")
    print(
        "  ※ 全体 = 問題をまたいで比べた値。同一問題内 = 同じ問題の3枠だけで比べた値。"
    )
    print(
        "  ※ 同一問題内は問題単位の再標本化5000回で区間も出す（対が52しかないため）。"
    )
    print(
        f"  {'素性':22s}{'全体':>8s}{'同一問題内':>12s}{'95%区間':>18s}{'対の数':>6s}"
    )
    for key, sign, name in [
        ("topic_fit", 1, "topic_fit"),
        ("evidence_count", 1, "証拠数"),
        ("score", 1, "合成スコア"),
        ("rank", -1, "順位（上位ほど良い）"),
        ("margin_to_next", 1, "次点とのスコア差"),
    ]:
        usable = [s for s in slots if s[key] is not None]
        vals = [sign * s[key] for s in usable]
        labs = [s["hit"] for s in usable]
        w, n = within_query_auc(usable, key, sign)
        lo, hi = within_query_auc_ci(usable, key, sign)
        print(
            f"  {name:22s}{auc(vals, labs):8.3f}{w:12.3f}  [{lo:.3f}, {hi:.3f}]{n:6d}"
        )

    # 「その問題が簡単か」が効いているので、層別に見ないと素性の効果を取り違える
    table(slots, lambda s: s["n_gold"], "2. gold の人数ごと（問題の難しさの代理）")
    print("\n== 3. gold の人数 × 証拠数（層別に見ると向きが変わるか）==")
    for ng in sorted({s["n_gold"] for s in slots}):
        g = [s for s in slots if s["n_gold"] == ng]
        lo = [s["hit"] for s in g if s["evidence_count"] <= 3]
        hi = [s["hit"] for s in g if s["evidence_count"] > 3]
        f = lambda v: f"{np.mean(v):.3f} (n={len(v)})" if v else "—"
        print(f"  gold {ng}名: 証拠3件以下 {f(lo):16s} 証拠4件以上 {f(hi)}")

    # 枠数 > gold 人数 のスロットは、どう並べても当たらない
    unfill = [s for s in slots if s["rank"] > s["n_gold"]]
    fill = [s for s in slots if s["rank"] <= s["n_gold"]]
    print(
        f"\n== 4. 埋めようのない枠 ==\n  rank > gold人数: n={len(unfill)} 正解率 {np.mean([s['hit'] for s in unfill]):.3f}"
        f" / それ以外: n={len(fill)} 正解率 {np.mean([s['hit'] for s in fill]):.3f}"
    )

    table(slots, lambda s: s["evidence_count"], "5. 証拠数ごと（全群）")
    table(slots, lambda s: s["rank"], "6. 順位ごと")
    print("\n== 7. 証拠の種類ごと ==")
    srcs = sorted({k for s in slots for k in s["evidence_sources"]})
    for src in srcs:
        a = [s["hit"] for s in slots if has(s, src)]
        b = [s["hit"] for s in slots if not has(s, src)]
        # 同一問題内で比べられるのは、その問題の中で種類が割れているときだけ
        split = [
            q
            for q in {s["query_id"] for s in slots}
            if len({has(s, src) for s in slots if s["query_id"] == q}) > 1
        ]
        print(
            f"  {src:10s} あり n={len(a):3d} {np.mean(a):.3f} / なし n={len(b):3d} {np.mean(b):.3f}"
            f"   （種類が問題内で割れている問題: {len(split)}/52）"
        )

    print("\n== 8. ラベル案ごとの正解率 ==")
    results = {}
    for name, f in RULES.items():
        print(f"\n  -- {name} --")
        prev, mono = None, True
        for lab in ("高", "中", "低"):
            g = [s["hit"] for s in slots if f(s) == lab]
            if not g:
                print(f"    {lab}: 0件")
                continue
            p = float(np.mean(g))
            print(f"    {lab}: n={len(g):3d} 正解率 {p:.3f}")
            if prev is not None and p > prev + 1e-9:
                mono = False
            prev = p
        print(f"    単調（高≧中≧低）: {'はい' if mono else '**いいえ**'}")
        for a, b in (("高", "中"), ("高", "低")):
            pt, lo_, hi_, pos = bootstrap_gap(slots, f, a, b)
            if np.isnan(pt):
                print(f"    {a} − {b} = 測れない（どちらかのラベルが出ない）")
            elif np.isnan(lo_):
                print(f"    {a} − {b} = {pt:+.3f}（区間は測れない）")
            else:
                print(
                    f"    {a} − {b} = {pt:+.3f}  95%CI [{lo_:+.3f}, {hi_:+.3f}]  P(>0)={pos:.3f}"
                    f"  （問題単位・{BOOTSTRAP}回）"
                )
                results[f"{name}/{a}-{b}"] = {
                    "point": pt,
                    "ci": [lo_, hi_],
                    "p_gt0": pos,
                }

    print("\n== 9. 分割検証（候補から選ぶ部分だけを検証する）==")
    held, picked = split_half(slots)
    print(f"  問題単位で半分に分割、{SPLIT_REPS}回、seed {SPLIT_SEED}")
    print(f"  学習半分で選ばれたルール: {dict(picked)}")
    print(
        f"  検証半分での 高−低: 中央 {np.median(held):+.3f}"
        f"  95%区間 [{np.percentile(held, 2.5):+.3f}, {np.percentile(held, 97.5):+.3f}]"
        f"  正の割合 {np.mean(held > 0):.3f}"
    )
    print(
        "  ※ 検証しているのは「候補から選ぶ」部分だけ。候補そのものは全データを見て設計している。"
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "bootstrap_reps": BOOTSTRAP,
                    "seed": SEED,
                    "unit": "query",
                    "gaps": results,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n結果を書き出し: {args.out}")


if __name__ == "__main__":
    main()
