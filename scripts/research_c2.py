#!/usr/bin/env python3
"""research_c2.py — C2「情報が足りているか」の判定精度を測る（#111）。

C2 は相談文だけで「取り次げるか / 聞き返すか」を決める段。ここが厳しすぎると
**答えられる相談まで聞き返しで止まる**し、緩すぎると空振りの推薦が出る。
LLM 出力（`research_llm.py --task c2`）を入力に、クラスごとの一致率を出す。

  正常系 56 件（層2 の採点対象）… sufficient=true が期待
  異常系 20 件（eval_robustness.json）… sufficient=false が期待

    python scripts/research_llm.py --task c2 --payload payload_c2.json --out c2_qwen36.json
    python scripts/research_c2.py --result docs/benchmarks/ablation/c2_qwen36.json \
        --payload docs/benchmarks/ablation/payload_c2.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import research_corpus as rc

# 異常系はこの順で並べる（件数ではなく意味の重い順）
ABNORMAL = ("insufficient", "out_of_scope", "pii", "no_expert", "adversarial")


def parse(record):
    """LLM の JSON を読む。壊れていたら None（= JSON 不正）を返す。"""
    try:
        d = json.loads(record["content"])
        return {
            "sufficient": bool(d["sufficient"]),
            "missing": list(d.get("missing") or []),
            "followup": (d.get("followup_question") or "").strip(),
        }
    except (ValueError, KeyError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--payload", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.result, encoding="utf-8") as f:
        result = json.load(f)
    with open(args.payload, encoding="utf-8") as f:
        payload = {p["id"]: p for p in json.load(f)}

    parsed = {r["id"]: parse(r) for r in result}
    bad_json = [i for i, p in parsed.items() if p is None]
    print(f"JSON 妥当 {len(parsed) - len(bad_json)}/{len(parsed)}")
    if bad_json:
        print(f"  不正: {bad_json}")

    print("\n== クラス別の一致率 ==")
    rows, by_class = [], collections.defaultdict(list)
    for r in result:
        p = parsed[r["id"]]
        if p is None:
            continue
        klass = r["klass"]
        expected = klass == "normal"
        by_class[klass].append(p["sufficient"] == expected)
    print(f"    {'クラス':14s}{'n':>4s}{'期待':>7s}{'一致':>16s}")
    for klass in ("normal", *ABNORMAL):
        hits = by_class[klass]
        exp = "true" if klass == "normal" else "false"
        cell = f"{sum(hits)}/{len(hits)} = {np.mean(hits):.3f}"
        print(f"    {klass:16s}{len(hits):4d}{exp:>7s}{cell:>16s}")
    allhits = [h for v in by_class.values() for h in v]
    print(f"\n  全体 {sum(allhits)}/{len(allhits)} = {np.mean(allhits):.3f}")

    print("\n== 誤検出（答えられる相談を聞き返しで止めたもの）==")
    misfires = [
        r
        for r in result
        if r["klass"] == "normal"
        and parsed[r["id"]]
        and not parsed[r["id"]]["sufficient"]
    ]
    by_diff = collections.Counter(payload[m["id"]]["difficulty"] for m in misfires)
    print(f"  {len(misfires)} 件 / 難易度内訳 {dict(by_diff)}")
    # 聞き返した相談について、C1 が渡したトピックが正解だったかを見る。
    # 一致しているなら「分からないから聞く」ではなく、**分かっているのに聞いている**。
    gold_of = {q["id"]: (q["gold_topics"] or []) for q in rc.load_eval()[0]}
    for m in misfires:
        p, q = parsed[m["id"]], payload[m["id"]]
        gold = gold_of[q["eval_id"]]
        rank = next((i + 1 for i, t in enumerate(q["topics"]) if t in gold), None)
        agree = (
            "gold なし"
            if not gold
            else (f"C1 の第{rank}候補が正解" if rank else "C1 も外している")
        )
        rows.append(
            {
                "id": m["id"],
                "eval_id": q["eval_id"],
                "difficulty": q["difficulty"],
                "missing": p["missing"],
                "followup": p["followup"],
                "c1_topics": q["topics"],
                "gold_topics": gold,
                "c1_gold_rank": rank,
            }
        )
        print(
            f"  {m['id']:5s} {q['difficulty']:3s} {agree} / 不足={'、'.join(p['missing'])}"
        )
        print(f"        {q['query']}")
        print(f"        聞き返し: {p['followup']}")
    solvable = [r for r in rows if r["c1_gold_rank"]]
    print(
        f"\n  → うち {len(solvable)}/{len(rows)} 件は C1 が正解トピックを出せていた"
        f"（第1候補で当てたのは {sum(1 for r in solvable if r['c1_gold_rank'] == 1)} 件）"
    )

    print("\n== 聞き返し文の整合 ==")
    # sufficient=false なのに聞き返しが空 / true なのに聞き返しがある = 契約違反
    viol = [
        r["id"]
        for r in result
        if parsed[r["id"]]
        and bool(parsed[r["id"]]["followup"]) != (not parsed[r["id"]]["sufficient"])
    ]
    print(f"  違反 {len(viol)} 件{' ' + str(viol) if viol else ''}")

    lat = np.array([r["latency"] for r in result])
    print(
        f"\n== 応答時間 ==\n  p50 {np.median(lat):.2f}s / p95 {np.percentile(lat, 95):.2f}s "
        f"/ 最大 {lat.max():.2f}s"
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "n": len(result),
                    "json_valid": len(parsed) - len(bad_json),
                    "by_class": {
                        k: {"n": len(v), "hits": int(sum(v)), "rate": float(np.mean(v))}
                        for k, v in by_class.items()
                    },
                    "overall": float(np.mean(allhits)),
                    "misfires": rows,
                    "followup_violations": viol,
                    "latency_p50": float(np.median(lat)),
                    "latency_p95": float(np.percentile(lat, 95)),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n結果を書き出し: {args.out}")


if __name__ == "__main__":
    main()
