#!/usr/bin/env python3
"""
eval_label_agreement.py — 自動導出した gold と、人手ラベル（PR #46）の一致度を測る。

評価セット v2 の gold は projects + daily_reports から自動導出している（build_eval_v2.py）。
自動である以上「合成データの中の別ルール」でしかなく、それ自体では妥当性を主張できない。

PR #46（reona 作）は、同じ案件・日報・チャットを**人が読んで**トピックごとの専門家を挙げたもの。
導出手順が完全に独立しているので、**両者の一致度が自動ラベルの外部検証**になる。

  Jaccard      … |auto ∩ human| / |auto ∪ human|。集合としての一致
  被覆(coverage) … |auto ∩ human| / |auto|。自動ラベルが人の判断に含まれている割合

実行: python3 scripts/eval_label_agreement.py
"""

import importlib.util
import os
from collections import defaultdict

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYN = os.path.join(REPO_ROOT, "fixtures", "synthetic")

_spec = importlib.util.spec_from_file_location(
    "build_eval_v2", os.path.join(os.path.dirname(__file__), "build_eval_v2.py")
)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)


def main():
    human = _m.load_human_labels()
    ev, _ = _m.build_gold_evidence()
    auto = {t: set(_m.rank_experts(ev, t)) for t in _m.TOPICS}
    employees = _m.load("people/employees.json")
    id2emp = {e["id"]: e for e in employees}

    # 人手トピック(67) を自前トピック(22) へ写像する
    mapped = defaultdict(list)
    for t in human:
        for mt, kws in _m.TOPICS.items():
            if any(k in t for k in kws) or mt.split("・")[0] in t:
                mapped[mt].append(t)
                break
    unmapped = [t for t in human if not any(t in v for v in mapped.values())]

    print("=== 自動ラベル vs 人手ラベル（PR #46）の一致 ===")
    print(f"{'自前トピック':24s} {'自動':>18s} {'人手(和)':>22s}  Jacc  被覆")
    print("-" * 82)
    jaccs, covs = [], []
    for mt in sorted(mapped):
        h = set().union(*[set(human[t]) for t in mapped[mt]])
        a = auto[mt]
        j = len(h & a) / len(h | a) if (h | a) else 0.0
        c = len(h & a) / len(a) if a else 0.0
        jaccs.append(j)
        covs.append(c)
        flag = "  ←不一致" if j < 0.5 else ""
        print(
            f"{mt:24s} {sorted(a)!s:>18s} {sorted(h)!s:>22s}  {j:.2f}  {c:.2f}{flag}"
        )

    print("-" * 82)
    print(f"平均 Jaccard: {sum(jaccs) / len(jaccs):.2f}")
    print(f"自動 gold が人手 gold に含まれる率（被覆）: {sum(covs) / len(covs):.2f}")
    perfect = sum(1 for j in jaccs if j >= 0.999)
    print(f"完全一致したトピック: {perfect}/{len(jaccs)}")

    print(
        f"\n=== 写像できなかった人手トピック（自前22トピック体系の穴）: {len(unmapped)} 件 ==="
    )
    for t in unmapped:
        ids = human[t]
        depts = sorted({id2emp[i]["department"] for i in ids})
        print(f"  {t:24s} {ids}  {depts}")
    print(
        "\n  → 営業事務・CS・経理庶務・マーケの日常業務。TEKIJIN の実用途に最も近い層で、"
    )
    print(
        "    自前のトピック体系（案件商材寄りの22分類）には無い。うち4件を L2 に取り込み済み。"
    )

    print("\n--- 読み方 ---")
    print(
        "  被覆が高い＝自動 gold は人の判断とおおむね一致している（外部検証として使える）。"
    )
    print("  Jaccard が低いトピックは、自動側が部署4名を一括で拾い、人手側が案件実績で")
    print(
        "  個人を絞り込んでいるケースが多い。人手側のほうが鋭いので、そこは L2 に取り込んである。"
    )


if __name__ == "__main__":
    main()
