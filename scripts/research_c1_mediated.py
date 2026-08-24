#!/usr/bin/env python3
"""research_c1_mediated.py — 実C1（本番vLLM）の #69 トピック媒介 前後比較を生成する。

#69 part1/part2 で C1 は「検索してから分類（retrieve-then-classify）」になった。この
スクリプトは評価セットの各質問について、製品と同じ経路で **本番 vLLM の C1** を2通り走らせる:

  * baseline … 質問文だけ（context 無し）＝ #69 前の挙動の再現
  * mediated … C4 の検索断片を context として渡す（#69 後）

出力は ``research_faithful.load_c1`` と同じ形式（``[{"id", "arguments": <IntentSchema JSON>}]``）
なので、そのまま::

    python scripts/research_e2e.py --task variants --db-url <url> --c1 c1_baseline.json
    python scripts/research_e2e.py --task variants --db-url <url> --c1 c1_mediated.json

に食わせて「[切り分け] C1 の実トピック＋…」の層2 R@3 を前後で比べられる。

**共有GPUホスト配慮**: C1 呼び出しは *逐次* に投げる（本番 vLLM を占有しない）。埋め込み(C3)と
検索(C4)は CPU / DB のみ。事前に ``research_e2e.py --task prepare`` で seed + embed 済みの
使い捨てDBを ``--db-url`` で渡すこと（本番 tekijin_app_pg には触れない）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# research_e2e の DB/retriever 構築を再利用する。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from research_e2e import SRC, _redact, build, start_db

ASKER = {"id": 1}  # research_faithful と同じ（依頼者は C1 の判定に効かない固定値）


def _arguments(intent) -> str:
    """IntentResult を load_c1 が読む ``arguments``（IntentSchema JSON 文字列）にする。"""

    return json.dumps(
        {
            "topics": list(intent.topics),
            "products": list(intent.products),
            "situation": intent.situation,
            "question_type": intent.question_type,
            "out_of_scope": intent.out_of_scope,
            "confidence": intent.confidence,
        },
        ensure_ascii=False,
    )


def generate(url, out_prefix, floors, limit=0):
    import research_faithful as rf
    from tekijin.data.repository import Repository
    from tekijin.llm.vllm import VllmIntentModel
    from tekijin.retrieval.fragments import collect_context_fragments

    session, retriever, _scorer = build(url)
    repo = Repository(session)
    intent_model = VllmIntentModel()  # 既定 settings → 本番 vLLM（llm_base_url）

    # research_faithful.items() の正常系（採点対象）を、そのIDで反復する。
    # research_e2e.load_c1_topics は C1 レコードを rf の item id（"p{i}"）で受け取り、
    # eval_id へ変換して突き合わせるので、その id 体系に必ず合わせること（さもないと
    # 突き合わせ 0 件になる）。
    rows = [r for r in rf.items() if r["klass"] == "normal"]
    if limit:
        rows = rows[:limit]
    print(f"C1 を実行する件数: {len(rows)}（逐次・本番vLLM・採点対象の正常系）")
    print(f"relevance floor スイープ: baseline(context無し) + floors={floors}")

    # 1問につき C1 を (1 + len(floors)) 回叩く（baseline + 各 floor の media）。
    baseline: list[dict] = []
    mediated: dict[float, list[dict]] = {f: [] for f in floors}
    ctx_counts: dict[float, list[int]] = {f: [] for f in floors}
    t0 = time.monotonic()
    for n, row in enumerate(rows, 1):
        query = row["query"]
        res = retriever.search(query)
        contexts = {
            f: collect_context_fragments(repo, res, min_confidence=f) for f in floors
        }
        try:
            base_intent = intent_model.analyze(query, ASKER, context=None)
            med_intents = {
                f: intent_model.analyze(query, ASKER, context=contexts[f] or None)
                for f in floors
            }
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない（load_c1 が落とす）
            print(
                f"  [skip] id={row['id']}: {type(exc).__name__}: {exc}", file=sys.stderr
            )
            continue
        baseline.append({"id": row["id"], "arguments": _arguments(base_intent)})
        for f in floors:
            mediated[f].append(
                {"id": row["id"], "arguments": _arguments(med_intents[f])}
            )
            ctx_counts[f].append(len(contexts[f]))
        if n % 10 == 0 or n == len(rows):
            print(f"  {n}/{len(rows)}  経過 {time.monotonic() - t0:.0f}s")

    _write(f"{out_prefix}_baseline.json", baseline)
    for f in floors:
        path = f"{out_prefix}_f{f:.2f}.json"
        _write(path, mediated[f])
        cc = ctx_counts[f]
        nonempty = sum(1 for c in cc if c)
        changed = _changed_count(baseline, mediated[f])
        print(
            f"  floor={f:.2f}: 断片を渡せた {nonempty}/{len(cc)}"
            f"（平均 {sum(cc) / max(len(cc), 1):.1f}）／ baseline から変化 {changed}/{len(mediated[f])}"
        )


def _changed_count(baseline, mediated):
    by_id = {d["id"]: d["arguments"] for d in baseline}
    changed = 0
    for d in mediated:
        base = by_id.get(d["id"])
        if base is None:
            continue
        bt = set(json.loads(base).get("topics") or [])
        mt = set(json.loads(d["arguments"]).get("topics") or [])
        if bt != mt:
            changed += 1
    return changed


def _write(path, records):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"  書き出し: {path}（{len(records)} 件）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db-url",
        default=None,
        help="seed+embed 済みの使い捨てDB（aarch64 では pgserver が無いので必須）。"
        "本番 tekijin_app_pg には向けないこと",
    )
    ap.add_argument(
        "--pgdir",
        default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "tekijin_e2e_pgdata"),
    )
    ap.add_argument("--out-prefix", default="c1sweep", help="出力ファイル接頭辞")
    ap.add_argument(
        "--floors",
        default="0.0",
        help="relevance floor をカンマ区切りで（例 0.0,0.45,0.55,0.65）。"
        "0.0 は #69 現行（フロア無し）の再現",
    )
    ap.add_argument("--limit", type=int, default=0, help="先頭 N 件だけ（疎通確認用）")
    args = ap.parse_args()

    floors = [float(x) for x in args.floors.split(",") if x.strip() != ""]
    if args.db_url:
        url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        os.environ["TEKIJIN_DATABASE_URL"] = url
        sys.path.insert(0, SRC)
    else:
        url = start_db(args.pgdir)
    print(f"DB: {_redact(url)}")
    generate(url, args.out_prefix, floors, args.limit)


if __name__ == "__main__":
    main()
