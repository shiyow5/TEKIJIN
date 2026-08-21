#!/usr/bin/env python3
"""
import_human_labels.py — PR #46（reona 作）の人手ラベルを取り込む。

PR #46 は案件履歴・日報・チャットを人手で読み、67トピックについて
「そのトピックに詳しい社員」を氏名で列挙している。クエリ402件そのものは
38文型の穴埋めでトピック語が92%漏れているため評価セットとしては採らないが、
**topic -> 専門家の人手マッピングは資産**なので、employee_id に変換して取り込む。

用途:
  1. 自動導出した gold（build_eval_v2.py）の**人手ラベルとの一致度**を測る
     → scripts/eval_label_agreement.py
  2. 個人単位で鋭いトピック・日常業務トピックを、eval_person の L2 に追加する材料にする

入力: PR #46 の fixtures/synthetic/answers/eval_queries.json
      （既定パス。無ければ --src で指定する）
出力: fixtures/synthetic/eval/topic_experts_human.json

実行: python3 scripts/import_human_labels.py [--src PATH]
"""

import argparse
import json
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYN = os.path.join(REPO_ROOT, "fixtures", "synthetic")
DEFAULT_SRC = os.path.join(SYN, "answers", "eval_queries.json")
OUT = os.path.join(SYN, "eval", "topic_experts_human.json")

SOURCE_NOTE = (
    "PR #46 (chore/45-eval-queries-40, author: reona0620) の "
    "fixtures/synthetic/answers/eval_queries.json から topic -> correct_experts を抽出し、"
    "氏名を employee_id に解決したもの。クエリ本文は採用していない（38文型の穴埋めで"
    "トピック語が92%のクエリに漏れているため）。"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    args = ap.parse_args()

    if not os.path.exists(args.src):
        raise SystemExit(
            f"入力が見つかりません: {args.src}\n"
            "PR #46 のブランチから取得してください:\n"
            "  git show origin/chore/45-eval-queries-40:"
            "fixtures/synthetic/answers/eval_queries.json > /tmp/reona_eval.json\n"
            "  python3 scripts/import_human_labels.py --src /tmp/reona_eval.json"
        )

    with open(args.src, encoding="utf-8") as f:
        rows = json.load(f)
    with open(os.path.join(SYN, "people", "employees.json"), encoding="utf-8") as f:
        employees = json.load(f)
    name2id = {e["name"]: e["id"] for e in employees}

    by_topic = {}
    unresolved = set()
    for r in rows:
        ids = by_topic.setdefault(r["topic"], set())
        for n in r["correct_experts"]:
            if n in name2id:
                ids.add(name2id[n])
            else:
                unresolved.add(n)

    out = {
        "_meta": {
            "source": SOURCE_NOTE,
            "source_rows": len(rows),
            "topics": len(by_topic),
        },
        "topics": {t: sorted(v) for t, v in sorted(by_topic.items())},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"=== {os.path.relpath(OUT, REPO_ROOT)} ===")
    print(f"  入力 {len(rows)} 行 -> {len(by_topic)} トピック")
    print(f"  氏名の未解決: {sorted(unresolved) if unresolved else 'なし'}")
    covered = {i for v in by_topic.values() for i in v}
    print(f"  社員カバレッジ: {len(covered)}/{len(employees)}")
    assert not unresolved, f"employees.json に無い氏名: {sorted(unresolved)}"


if __name__ == "__main__":
    main()
