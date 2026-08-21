#!/usr/bin/env python3
"""
eval_baselines.py — 評価セット v2 に対するベースライン3本を測る（Issue #43）。

本システムの数字は、ベースラインとの差分でしか意味を持たない。
モデル比較の前に、まずここを測る（analysis/19_評価データ設計.md §5-4）。

  1. random          … Recall@3 の下限
  2. answers_count   … 「過去回答を topic で数えるだけ」。**リークの残量**を示す
  3. lexical_profile … 社員プロフィールへの文字3-gram BM25。埋め込みが本当に効いているかの対照
  4. lexical_answers … 過去回答本文への文字3-gram BM25 → その回答者。強めの語彙ベースライン

実行: python3 scripts/eval_baselines.py
"""

import json
import math
import os
import random
from collections import Counter, defaultdict

random.seed(42)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYN = os.path.join(REPO_ROOT, "fixtures", "synthetic")

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "build_eval_v2", os.path.join(os.path.dirname(__file__), "build_eval_v2.py")
)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
TOPICS = _m.TOPICS

K = 3


def load(rel):
    with open(os.path.join(SYN, rel), encoding="utf-8") as f:
        return json.load(f)


def ngrams(text, n=3):
    text = "".join(text.split())
    return [text[i : i + n] for i in range(max(0, len(text) - n + 1))]


class BM25:
    """文字 n-gram の BM25。日本語の形態素解析なしで語彙ベースラインを作るための最小実装。"""

    def __init__(self, docs, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(ngrams(d)) for d in docs]
        self.len = [sum(c.values()) for c in self.docs]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter()
        for c in self.docs:
            df.update(c.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - v + 0.5) / (v + 0.5)) for t, v in df.items()}

    def scores(self, query):
        q = ngrams(query)
        out = []
        for i, c in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = c.get(t, 0)
                if not f:
                    continue
                s += (
                    self.idf.get(t, 0)
                    * f
                    * (self.k1 + 1)
                    / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
                )
            out.append(s)
        return out


def recall_at_k(pred, gold, k=K):
    if not gold:
        return None
    return len(set(pred[:k]) & set(gold)) / min(k, len(gold))


def main():
    person = load("eval/eval_person.json")
    employees = load("people/employees.json")
    profiles = load("people/employee_profiles.json")
    answers = load("answers/answers.json")
    emp_ids = [e["id"] for e in employees]

    # --- 2. answers_count（リーク baseline） ---
    by_topic = defaultdict(Counter)
    for a in answers:
        by_topic[a["topic"]][a["responder_id"]] += 1
    global_top = [
        e for e, _ in Counter(a["responder_id"] for a in answers).most_common()
    ]

    def answers_count(q):
        hits = [
            t
            for t, kws in TOPICS.items()
            if t in q["query"] or any(k in q["query"] for k in kws)
        ]
        if not hits:
            return global_top[:K]  # トピック語が拾えない＝この baseline はお手上げ
        c = Counter()
        for t in hits:
            c.update(by_topic[t])
        return [e for e, _ in c.most_common(K)]

    # --- 3. lexical_profile ---
    prof_ids = [p["employee_id"] for p in profiles]
    bm_prof = BM25([p["description"] for p in profiles])

    def lexical_profile(q):
        sc = bm_prof.scores(q["query"])
        order = sorted(range(len(sc)), key=lambda i: -sc[i])
        return [prof_ids[i] for i in order[:K]]

    # --- 4. lexical_answers ---
    ans_resp = [a["responder_id"] for a in answers]
    bm_ans = BM25([a["body"] for a in answers])

    def lexical_answers(q):
        sc = bm_ans.scores(q["query"])
        order = sorted(range(len(sc)), key=lambda i: -sc[i])
        seen, out = set(), []
        for i in order:
            r = ans_resp[i]
            if r not in seen:
                seen.add(r)
                out.append(r)
            if len(out) == K:
                break
        return out

    def rnd(q):
        return random.sample(emp_ids, K)

    baselines = [
        ("random", rnd),
        ("answers_count", answers_count),
        ("lexical_profile", lexical_profile),
        ("lexical_answers", lexical_answers),
    ]

    layers = ["L1", "L2", "L3"]
    print(
        f"評価セット: eval_person.json（{len(person)}件）。L4は abstain 判定なので Recall 対象外\n"
    )
    print(f"{'baseline':18s} " + "".join(f"{l:>9s}" for l in layers) + f"{'全体':>9s}")
    print("-" * 62)
    results = {}
    for name, fn in baselines:
        per = defaultdict(list)
        for q in person:
            if q["difficulty"] == "L4":
                continue
            r = recall_at_k(fn(q), q["gold_experts"])
            if r is not None:
                per[q["difficulty"]].append(r)
        row = [sum(per[l]) / len(per[l]) if per[l] else float("nan") for l in layers]
        allv = [x for l in layers for x in per[l]]
        overall = sum(allv) / len(allv)
        results[name] = overall
        print(f"{name:18s} " + "".join(f"{v:9.3f}" for v in row) + f"{overall:9.3f}")

    print("\n--- 読み方 ---")
    print(
        f"  random          {results['random']:.3f}  … 下限。これを超えない実装は無意味"
    )
    print(
        f"  answers_count   {results['answers_count']:.3f}  … **リークの残量**。低いほど評価セットとして健全"
    )
    print(
        f"  lexical_profile {results['lexical_profile']:.3f}  … 語彙一致のみ。埋め込みはこれを超えないと採用理由が無い"
    )
    print(
        f"  lexical_answers {results['lexical_answers']:.3f}  … 強めの語彙ベースライン。実質的な打倒目標"
    )
    print("\n  L1 と L2 の差が、トピック語のリークに依存していた分の大きさ。")
    print("  L2/L3 で落ちるベースラインほど、本システムが差をつけられる余地が大きい。")


if __name__ == "__main__":
    main()
