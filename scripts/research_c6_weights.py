#!/usr/bin/env python3
"""research_c6_weights.py — C6 の線形重みと base_score を実測する（#85）。

C6（#30）の重みは doc15 の設計値のまま一度も測っていない。#65 / #80 の実験で使っていたのは
**base_score の素の和**（飽和も recency も proximity も無し）なので、
「C6 の式が素の和より良いのか」も未確認だった。

`tekijin.scorer` の純関数を**そのまま import** して忠実に再現する（DTO を fixtures から組む）。
`ExpertiseScorer` 本体は Repository（SQLAlchemy）を要求するので使わないが、
スコアの式・重み・base_score・飽和・減衰はすべて製品と同じものを呼んでいる。

    python scripts/research_c6_weights.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "src")
)

import research_ablation as A
import research_corpus as rc
import research_rank as rr
from tekijin.data.dto import AnswerDTO, CertificationDTO, ProjectMembershipDTO, SkillDTO
from tekijin.scorer.evidence import collect_topic_evidence, edge_weight
from tekijin.scorer.features import answer_quality, load, proximity, recency
from tekijin.scorer.weights import LOAD_WINDOW_DAYS, Weights

LOAD_WINDOW = dt.timedelta(days=LOAD_WINDOW_DAYS)


def parse_ts(value):
    if not value:
        return None
    text = str(value)
    if "T" in text:
        return dt.datetime.fromisoformat(text)
    return dt.date.fromisoformat(text)


def build_dtos(fx):
    """fixtures → C6 が受け取る DTO。製品の mapper とは別物だが、同じ形に詰める。"""
    certs, skills, memberships, answers = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    for c in fx["certs"]:
        certs[c["employee_id"]].append(
            CertificationDTO(
                c["id"], c["employee_id"], c["name"], parse_ts(c.get("acquired_at"))
            )
        )
    for s in fx["skills"]:
        skills[s["employee_id"]].append(
            SkillDTO(
                s["id"], s["employee_id"], s["topic"], s.get("level"), s.get("source")
            )
        )
    projects = {p["id"]: p for p in fx["projects"]}
    for pid, members in fx["members"].items():
        p = projects[pid]
        for m in members:
            memberships[m["employee_id"]].append(
                ProjectMembershipDTO(
                    pid,
                    m["employee_id"],
                    m["role"],
                    p.get("product"),
                    p.get("industry"),
                    p.get("subject"),
                    p.get("status"),
                    parse_ts(p.get("start_date")),
                    parse_ts(p.get("end_date")),
                )
            )
    for a in fx["answers"]:
        answers[a["responder_id"]].append(
            AnswerDTO(
                a["id"],
                a["question_id"],
                a["responder_id"],
                a["body"],
                a.get("topic"),
                a.get("reuse_count") or 0,
                a.get("was_helpful"),
                parse_ts(a.get("created_at")),
                False,
            )
        )
    return certs, skills, memberships, answers


def latest_moment(fx):
    """`now` は実時計ではなく fixtures の最大タイムスタンプ+1日に固定する（再現性）。"""
    stamps = [parse_ts(a.get("created_at")) for a in fx["answers"]]
    stamps += [parse_ts(d.get("created_at")) for d in fx["dailies"]]
    stamps = [s for s in stamps if isinstance(s, dt.datetime)]
    return max(stamps) + dt.timedelta(days=1)


class C6:
    """C6 の式を、候補集合と重みを差し替えられる形で回す。"""

    def __init__(self, fx, now):
        self.fx = fx
        self.now = now
        self.certs, self.skills, self.memberships, self.answers = build_dtos(fx)
        self.branch = {e["id"]: e.get("branch") for e in fx["employees"]}
        self.employee_ids = [e["id"] for e in fx["employees"]]
        since = now - LOAD_WINDOW
        self.load_count = {
            eid: sum(
                1
                for a in ans
                if isinstance(a.created_at, dt.datetime)
                and since <= a.created_at <= now
            )
            for eid, ans in self.answers.items()
        }

    def topic_answers(self, eid, topic):
        """scorer._topic_answers と同じ絞り込み（answer.topic が別のサブトピックなら数えない）。"""
        return [
            a for a in self.answers.get(eid, []) if a.topic == topic or a.topic is None
        ]

    def score(self, eid, topic, weights, asker_branch=None, raw_sum=False):
        evidence = collect_topic_evidence(
            topic,
            self.certs.get(eid, []),
            self.skills.get(eid, []),
            self.memberships.get(eid, []),
            [a for a in self.topic_answers(eid, topic) if a.topic == topic],
        )
        if raw_sum:  # #65/#80 で使っていた素の和（飽和なし）
            return sum(e.base_score for e in evidence)
        if not evidence:
            return 0.0
        answers = [a for a in self.topic_answers(eid, topic) if a.topic == topic]
        moments = [
            self.now
            if (e.source_type == "project" and e.timestamp is None)
            else e.timestamp
            for e in evidence
            if e.source_type in ("project", "answer")
        ]
        return (
            weights.topic_fit * edge_weight(evidence)
            + weights.recency * recency(self.now, [m for m in moments if m is not None])
            + weights.answer_quality
            * answer_quality(
                sum(1 for a in answers if a.was_helpful is True),
                sum(a.reuse_count or 0 for a in answers),
                len(answers),
            )
            + weights.proximity * proximity(asker_branch, self.branch.get(eid))
            - weights.load * load(self.load_count.get(eid, 0))
        )

    def rank(self, topic, weights, candidates=None, asker_branch=None, raw_sum=False):
        if not topic:
            return []
        ids = candidates if candidates is not None else self.employee_ids
        scored = {e: self.score(e, topic, weights, asker_branch, raw_sum) for e in ids}
        scored = {e: v for e, v in scored.items() if v > 0.0}
        return rr.to_ranking(scored)


def load_topics(path):
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


def c4_candidates(ctx, model, item, qi, top_k=20):
    """C4 相当の候補集合（Dense 上位チャンクを人に集約した上位 top_k 名）。"""
    ranked, _ = A.dense_chunk_rank(ctx, model, qi, False, 64)
    return rr.to_ranking(
        rr.aggregate_people(ranked, ctx["owners"], rc.SOURCE_WEIGHT, top_n=20)
    )[:top_k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", required=True)
    ap.add_argument("--llm-dir", default="docs/benchmarks/ablation")
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ctx = A.build_context([args.emb])
    fx = ctx["fx"]
    now = latest_moment(fx)
    c6 = C6(fx, now)
    topics = load_topics(os.path.join(args.llm_dir, "llm_topic_ctx.json"))
    base = A.evaluate(ctx, A.make_system(model=args.model))
    default = Weights()
    print(
        f"採点対象 {len(ctx['items'])} 件 / now={now:%Y-%m-%d} / 基準（Dense 集約）= {base['R@3']:.3f}"
    )
    print(f"C6 既定重み: {default}\n")

    def system(weights, candidates=None, raw_sum=False, use_constraint=False):
        def run(c, item, qi):
            ts = topics.get(item["id"], [])
            cand = candidates(c, item, qi) if candidates else None
            branch = (
                (item.get("constraint") or {}).get("branch") if use_constraint else None
            )
            return c6.rank(ts[0] if ts else None, weights, cand, branch, raw_sum)

        return run

    results = {"base": {"R@3": base["R@3"], "MRR": base["MRR"], "Top1": base["Top1"]}}

    def show(label, sys_fn, store=None):
        r = A.evaluate(ctx, sys_fn)
        lo, hi, _p = A.paired_bootstrap(base["hits"], r["hits"])
        print(
            f"  {label:34s} R@3={r['R@3']:.3f} Δ={r['R@3'] - base['R@3']:+.3f} "
            f"[{lo:+.3f},{hi:+.3f}] MRR={r['MRR']:.3f} Top1={r['Top1']:.3f}"
        )
        if store is not None:
            # delta / ci も保存する。これが無いと「区間はコンソールにしか無い」ので、
            # 文書に書くときに手で写すことになり、実際に2度書き間違えた（#158）。
            results.setdefault(store, []).append(
                {
                    "name": label,
                    "R@3": r["R@3"],
                    "delta": r["R@3"] - base["R@3"],
                    "ci": [lo, hi],
                    "MRR": r["MRR"],
                    "Top1": r["Top1"],
                }
            )
        return r

    print("== 1. 素の base_score 和 vs C6 の式（候補=全40名）==")
    show("素の和（#65/#80 で使っていた形）", system(default, raw_sum=True), "formula")
    show("C6 の完全な式（既定重み）", system(default), "formula")

    print("\n== 2. 各項を 0 にする（候補=全40名）==")
    for field in ("recency", "answer_quality", "proximity", "load"):
        w = dataclasses.replace(default, **{field: 0.0})
        show(f"{field} を 0 に", system(w), "ablation")
    show(
        "topic_fit のみ",
        Weights and system(Weights(1.0, 0.0, 0.0, 0.0, 0.0)),
        "ablation",
    )

    print("\n== 3. 候補集合を C4 相当に絞る ==")
    for k in (10, 20, 40):
        show(
            f"C4 上位{k}名を候補に",
            system(
                default,
                candidates=lambda c, i, q, k=k: c4_candidates(ctx, args.model, i, q, k),
            ),
            "candidates",
        )

    print("\n== 4. 拠点制約を asker_branch として渡す（制約つき5件のみ効く）==")
    idx = [i for i, it in enumerate(ctx["items"]) if it.get("constraint")]
    for label, w in [
        ("既定 proximity=0.10", default),
        ("proximity=0.40", Weights(0.45, 0.15, 0.20, 0.40, 0.20)),
    ]:
        r = show(f"{label}", system(w, use_constraint=True), "constraint")
        print(f"      └ 制約つき{len(idx)}件だけ: {r['hits'][idx].mean():.3f}")

    print("\n== 5. 重みのグリッド探索（5分割交差検証）==")
    grid_search(ctx, c6, topics, base, results)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n結果を書き出し: {args.out}")


def grid_search(ctx, c6, topics, base, results, folds=5, seed=42):
    """全件でチューニングした値を性能として報告しないための交差検証。"""
    grid = [
        Weights(tf, rc_, aq, px, ld)
        for tf in (0.45, 0.7, 1.0)
        for rc_ in (0.0, 0.15, 0.3)
        for aq in (0.0, 0.2, 0.4)
        for px in (0.1,)
        for ld in (0.0, 0.2)
    ]
    hits = []
    for w in grid:

        def run(c, item, qi, w=w):
            ts = topics.get(item["id"], [])
            return c6.rank(ts[0] if ts else None, w)

        hits.append(A.evaluate(ctx, run)["hits"])
    hits = np.stack(hits)

    rng = np.random.default_rng(seed)
    order = rng.permutation(len(ctx["items"]))
    parts = np.array_split(order, folds)
    picked, scores = [], []
    for i in range(folds):
        test = parts[i]
        train = np.concatenate([parts[j] for j in range(folds) if j != i])
        best = int(np.argmax(hits[:, train].mean(axis=1)))
        picked.append(grid[best])
        scores.append(hits[best, test].mean())
    default_score = hits[grid.index(Weights(0.45, 0.15, 0.2, 0.1, 0.2))].mean()
    print(f"  グリッド {len(grid)} 通り / 既定重みの全件スコア {default_score:.3f}")
    print(
        f"  交差検証でのスコア: {np.mean(scores):.3f}（既定との差 {np.mean(scores) - default_score:+.3f}）"
    )
    for i, w in enumerate(picked):
        print(f"    fold{i + 1} が選んだ重み: {w}")
    results["grid"] = {
        "default_full": float(default_score),
        "cv_mean": float(np.mean(scores)),
        "picked": [str(w) for w in picked],
    }


if __name__ == "__main__":
    main()
