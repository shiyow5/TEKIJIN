#!/usr/bin/env python3
"""research_c2_faithful.py — 製品そのままの C1→C2 を集計する（#113）。

#111 の集計は (a) 異常系20件を全部 C2 の担当として数え、(b) 検索結果を見せた C1 の
出力を使い、(c) `eval_person.json` の検索用ラベルを充足ラベルとして流用していた。
いずれも誤り（PR #112 の Codex レビュー）。ここでは段ごとに分けて数える。

**どの段の担当か**（`agent/graph.py` と `docs/specs/model-definition.md` §1 に従う）:

  out_of_scope / adversarial / pii … C1 が `out_of_scope=true` で弾く。**C2 に届かない**
  insufficient                     … **C2 の担当**。`sufficient=false` が正解
  no_expert                        … **C6 / `no_candidate` の担当**。C2 に名簿は見えないので、
                                      「該当者がいない」は C6 のスコアリングが空を返して
                                      初めて確定する（`graph._after_c6` → `no_candidate`）
  normal                           … 充足の正解ラベルが無い。**聞き返し率をそのまま出す**

**正解率とは呼ばない。** 代わりに、同じ `IntentResult` を `RuleSufficiencyModel`
（`agent/stubs.py`。`llm_backend=stub` の既定実装）へ渡し、その判断との一致を見る。
これが支えられるのは「**同じ入力で、vLLM 版は既定実装よりどれだけ多く聞き返すか**」までで、
「vLLM 版が間違っている」ではない。`_REQUIRED_SLOTS` は `見積`/`技術相談` の完全一致でしか
発火せず、仕様（§2 C2）も必須スロットを列挙していないからである。

    python scripts/research_c2_faithful.py --c1 docs/benchmarks/ablation/c1_faithful.json \
        --c2 docs/benchmarks/ablation/c2_faithful.json \
        --payload docs/benchmarks/ablation/payload_c2_faithful.json
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

import research_corpus as rc
import research_faithful as rf
from tekijin.agent.stubs import RuleSufficiencyModel
from tekijin.llm.schemas import SufficiencySchema

# 担当する段と、その段に期待する `sufficient`。None = 正解ラベルを置かない。
OWNER = {
    "normal": ("C2", None),
    "insufficient": ("C2", False),
    # C2 に名簿は見えない。「該当者がいない」は retrieval/scoring が no_candidate に
    # 落ちて初めて確定するので、C2 の担当にはしない。
    "no_expert": ("C6/no_candidate", None),
    "out_of_scope": ("C1", None),
    "adversarial": ("C1", None),
    "pii": ("C1", None),
}
CLASSES = ("normal", "insufficient", "no_expert", "pii", "out_of_scope", "adversarial")


def parse_c2(record):
    """C2 の関数呼び出しを製品と同じスキーマで検証する。

    戻り値は (SufficiencySchema | None, 理由)。`schema` 落ちは**製品では例外**になり
    リクエストごと失敗するので、黙って捨てずに数える。
    """
    if record.get("error"):
        return None, "http_error"
    if not record.get("arguments"):
        return None, "no_tool_call"
    try:
        return SufficiencySchema(**json.loads(record["arguments"])), None
    except (ValueError, TypeError):
        # `_followup_required_when_insufficient` に弾かれる形が代表例
        return None, "schema_violation"


def pct(arr, q):
    return float(np.percentile(arr, q)) if len(arr) else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--c1", required=True)
    ap.add_argument("--c2", required=True)
    ap.add_argument("--payload", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    intents = rf.load_c1(args.c1)
    with open(args.c1, encoding="utf-8") as f:
        c1_rows = {d["id"]: d for d in json.load(f)}
    with open(args.c2, encoding="utf-8") as f:
        c2_rows = {d["id"]: d for d in json.load(f)}
    with open(args.payload, encoding="utf-8") as f:
        payload = {d["id"]: d for d in json.load(f)}
    all_items = {r["id"]: r for r in rf.items()}

    print("== 0. 段ごとの歩留まり（母集団をここで確定する）==")
    n_norm = sum(1 for d in all_items.values() if d["klass"] == "normal")
    print(f"  相談 {len(all_items)} 件（正常系{n_norm} + 異常系{len(all_items) - n_norm}）")
    print(f"  C1 が応答            {len(c1_rows)}")
    c1_fail = [i for i in c1_rows if i not in intents]
    print(
        f"  C1 が構造化出力を返した {len(intents)}（**製品では残り {len(c1_fail)} 件が例外**）"
    )
    passed = [i for i in intents if not intents[i].out_of_scope]
    print(
        f"  C1 を通過して C2 へ    {len(passed)}（out_of_scope で停止 {len(intents) - len(passed)}）"
    )
    print(f"  C2 が応答            {len(c2_rows)}")
    trunc = [i for i, d in c1_rows.items() if d.get("finish_reason") == "length"]
    if trunc:
        print(f"  ⚠ C1 が長さ切れ: {len(trunc)} 件 {trunc[:10]}")

    print("\n== 1. C1 の out_of_scope（ここで弾けないと C2 に流れ込む）==")
    print(
        "  ※ C1 の system プロンプトは判定基準を『業務外・悪意ある入力』としか書いていない。"
    )
    print(
        "     有給の残日数・会議室予約のような『社内だが担当外』は基準に無いので、落とせなくても"
    )
    print("     C1 の失敗というより**プロンプトに書いていないだけ**と読むべき。")
    print(
        "  ※ 『通した』と『長さ切れで落ちた』は分けて数える。混ぜると C1 の甘さが隠れる。"
    )
    for klass in CLASSES:
        ids = [i for i, d in all_items.items() if d["klass"] == klass]
        caught = [i for i in ids if i in intents and intents[i].out_of_scope]
        passed_through = [
            i for i in ids if i in intents and not intents[i].out_of_scope
        ]
        lost = [i for i in ids if i not in intents]
        print(
            f"  {klass:14s} 弾いた {len(caught):3d} / 通した {len(passed_through):3d} / "
            f"長さ切れ {len(lost):3d}（担当={OWNER[klass][0]}）"
        )

    print("\n== 2. C2 に実際に到達した相談だけで見る ==")
    rule = RuleSufficiencyModel()
    rows, table = [], collections.defaultdict(lambda: collections.Counter())
    for i in passed:
        if i not in c2_rows:
            continue
        out, why = parse_c2(c2_rows[i])
        klass = all_items[i]["klass"]
        table[klass]["n"] += 1
        if out is None:
            table[klass][why or "unparsed"] += 1
            continue
        intent, d = intents[i], payload[i]
        stub = rule.check(d["query"], intent, 0)
        table[klass]["parsed"] += 1
        table[klass]["passed"] += int(out.sufficient)
        table[klass]["agrees"] += int(out.sufficient == stub.sufficient)
        table[klass]["stub_passed"] += int(stub.sufficient)
        rows.append(
            {
                "id": i,
                "eval_id": d["eval_id"],
                "klass": klass,
                "difficulty": d["difficulty"],
                "query": d["query"],
                "vllm_sufficient": out.sufficient,
                "vllm_missing": list(out.missing),
                "vllm_followup": out.followup_question,
                "stub_sufficient": stub.sufficient,
                "stub_missing": list(stub.missing),
                "c1_topics": list(intent.topics),
                "c1_question_type": intent.question_type,
                "c1_confidence": intent.confidence,
            }
        )
    head = ("クラス", "届いた", "読めた", "期待", "vLLMが通す", "stubが通す", "一致")
    print(
        f"    {head[0]:14s}{head[1]:>7s}{head[2]:>7s}{head[3]:>7s}"
        f"{head[4]:>12s}{head[5]:>12s}{head[6]:>8s}"
    )
    for klass in CLASSES:
        t = table[klass]
        if not t["n"]:
            continue
        exp = {True: "true", False: "false", None: "—"}[OWNER[klass][1]]
        vllm_cell = "{}/{}".format(t["passed"], t["parsed"])
        stub_cell = "{}/{}".format(t["stub_passed"], t["parsed"])
        agree_cell = "{}/{}".format(t["agrees"], t["parsed"])
        print(
            f"    {klass:16s}{t['n']:7d}{t['parsed']:7d}{exp:>7s}"
            f"{vllm_cell:>12s}{stub_cell:>12s}{agree_cell:>8s}"
        )
    violations = sum(t["schema_violation"] for t in table.values())
    if violations:
        print(f"  ⚠ スキーマ違反 {violations} 件（**製品ではここで例外**）")

    normal = [r for r in rows if r["klass"] == "normal"]
    if normal:
        asked = [r for r in normal if not r["vllm_sufficient"]]
        n_normal = sum(1 for d in all_items.values() if d["klass"] == "normal")
        n_reached = sum(1 for i in passed if all_items[i]["klass"] == "normal")
        print(
            f"\n  正常系: {n_normal} 件 → C1 を通過 {n_reached} 件 "
            f"→ C2 の出力が読めた {len(normal)} 件 → vLLM が聞き返す {len(asked)} 件"
        )
        print(
            f"  難易度内訳 {dict(collections.Counter(r['difficulty'] for r in asked))}"
        )
        print(
            f"  同じ入力で stub が聞き返す数 {sum(1 for r in normal if not r['stub_sufficient'])}"
        )
        print(
            "  ※ グラフは MAX_FOLLOWUPS=1 なので、聞き返しは1回で打ち切られ2周目は強制通過する。"
        )
        print(
            "     ここで測っているのは**1周目だけ**であり、『製品が拒否し続ける』ではない。"
        )
        slots = collections.Counter(m for r in asked for m in r["vllm_missing"])
        print(
            "  vLLM が挙げた不足の上位（stub の必須スロットは 現行製品 / 対象拠点数 の2つだけ）:"
        )
        for name, cnt in slots.most_common(12):
            print(f"    {cnt:3d}  {name[:60]}")

    print("\n== 3. C1 のトピックは後段の語彙に載っているか ==")
    print("  ※ 製品の C1 プロンプトはトピック候補一覧を渡さない（自由記述）。")
    print("     綴りが fixtures と合わなければ C6 のスコアラーは何も引けない。")
    fx = rc.load_all()
    vocab = {s["topic"] for s in fx["skills"]} | {
        a["topic"] for a in fx["answers"] if a.get("topic")
    }
    total = [t for i in intents for t in intents[i].topics]
    hits = [t for t in total if t in vocab]
    covered = [i for i in intents if any(t in vocab for t in intents[i].topics)]
    print(f"  C1 が出したトピック {len(total)} 個のうち語彙に一致 {len(hits)} 個")
    print(f"  少なくとも1つ一致した相談 {len(covered)}/{len(intents)} 件")
    print(f"  語彙（{len(vocab)}件）の例: {sorted(vocab)[:5]}")
    print(f"  C1 の出力例: {total[:8]}")

    print("\n== 4. 応答時間 ==")
    c1lat = np.array([d["latency"] for d in c1_rows.values()])  # C1 は全件に走る
    c2lat = np.array([c2_rows[i]["latency"] for i in passed if i in c2_rows])
    paired = np.array(
        [c1_rows[i]["latency"] + c2_rows[i]["latency"] for i in passed if i in c2_rows]
    )
    for name, arr in (
        ("C1（全件）", c1lat),
        ("C2（到達分）", c2lat),
        ("C1+C2（同一相談で合算）", paired),
    ):
        print(
            f"  {name:24s} p50 {pct(arr, 50):.2f}s / p95 {pct(arr, 95):.2f}s / 最大 {arr.max():.2f}s"
        )
    print(
        "  ※ 仕様の目標は初回表示 p50 1.5秒 / p95 3秒（端から端まで）。段別の線は無い。"
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "n_items": len(all_items),
                    "c1_structured": len(intents),
                    "reached_c2": len(passed),
                    "by_class": {k: dict(v) for k, v in table.items()},
                    "latency": {
                        "c1_p50": pct(c1lat, 50),
                        "c1_p95": pct(c1lat, 95),
                        "c2_p50": pct(c2lat, 50),
                        "c2_p95": pct(c2lat, 95),
                        "paired_p50": pct(paired, 50),
                        "paired_p95": pct(paired, 95),
                    },
                    "topic_vocab_hit_items": len(covered),
                    "rows": rows,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n結果を書き出し: {args.out}")


if __name__ == "__main__":
    main()
