#!/usr/bin/env python3
"""research_topic.py — 「トピックを当てる」段と「トピックが分かった後に人を並べる」段を分けて測る（#65）。

なぜ分けるか: 評価セットの gold は `projects` + `daily_reports` のトピック証拠から機械的に
作られている（`build_eval_v2.build_gold_evidence`）。したがって**トピックさえ正しく当たれば
人の並べ替えはほぼ自明**という構造になっている。層2 Recall@3 を1本で見ていると、
「検索が強い」のか「トピック推定が強い」のかが分離できない。

そこで:
  段A: query → topic の的中率
  段B: topic → people の質。**gold と導出経路を共有しない構成（projects を使わない）** も併置して、
       循環（gold の作り方をなぞっているだけ）でないことを確かめる
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "src")
)

import research_rank as rr  # noqa: E402
from build_eval_v2 import TOPICS  # noqa: E402  トピック→キーワード（gold と同じ語彙）
from tekijin.scorer.topics import (  # noqa: E402
    PRODUCT_TOPIC_MAP,
    cert_matches_topic,
)

TOPIC_LIST = sorted(TOPICS)

# C6 の base_score（backend/src/tekijin/scorer/weights.py と同じ値）
BASE = {
    "ans_helpful": 1.0,
    "proj_lead": 0.8,
    "ans": 0.7,
    "cert": 0.6,
    "proj_member": 0.5,
    "skill": 0.3,
}


def chunk_topics(fx):
    """チャンク id → そのチャンクが証拠になるトピック集合。"""
    out = defaultdict(set)
    for d in fx["documents"]:
        for t in TOPIC_LIST:
            if t in d["title"] or any(k in d["title"] for k in TOPICS[t]):
                out[f"doc:{d['id']}"].add(t)
    for p in fx["projects"]:
        t = PRODUCT_TOPIC_MAP.get(p["product"])
        if t:
            out[f"proj:{p['id']}"].add(t)
    for a in fx["answers"]:
        if a.get("topic"):
            out[f"ans:{a['id']}"].add(a["topic"])
    skills_by_person = defaultdict(set)
    for s in fx["skills"]:
        skills_by_person[s["employee_id"]].add(s["topic"])
    for pr in fx["profiles"]:
        out[f"profile:{pr['employee_id']}"] |= skills_by_person[pr["employee_id"]]
    for d in fx["dailies"]:
        text = f"{d['content']} {d.get('issue', '')}"
        for t in TOPIC_LIST:
            if any(k in text for k in TOPICS[t]):
                out[f"daily:{d['id']}"].add(t)
    return out


def predict_topic_from_ranking(ranked_ids, ctopics, top_n=20):
    """検索結果の上位チャンクにトピック票を入れる（順位重み）。"""
    score = defaultdict(float)
    for rank, cid in enumerate(ranked_ids[:top_n]):
        w = 1.0 / (rr.RRF_K + rank + 1)
        for t in ctopics.get(cid, ()):
            score[t] += w
    return [t for t, _ in sorted(score.items(), key=lambda x: (-x[1], x[0]))]


def predict_topic_lexical(query, tokenizer=None):
    """クエリにトピックのキーワードが直接出るか（リーク検出と同じ規則）。ほぼ当たらないはず。"""
    hits = [(t, sum(1 for k in TOPICS[t] if k in query)) for t in TOPIC_LIST]
    hits = [(t, c) for t, c in hits if c]
    return [t for t, _ in sorted(hits, key=lambda x: (-x[1], x[0]))]


def expert_scores_for_topic(fx, topic, use_projects=True, use_answers=True):
    """トピックが与えられたときの人スコア（C6 の topic_fit 相当の飽和なし版）。

    use_projects=False にすると gold の導出経路（projects + daily）と重ならない証拠だけで並べる。
    循環かどうかの対照実験に使う。
    """
    score = defaultdict(float)
    for s in fx["skills"]:
        if s["topic"] == topic:
            score[s["employee_id"]] += BASE["skill"]
    for c in fx["certs"]:
        if cert_matches_topic(c["name"], topic):
            score[c["employee_id"]] += BASE["cert"]
    if use_projects:
        for p in fx["projects"]:
            if PRODUCT_TOPIC_MAP.get(p["product"]) == topic:
                for m in fx["members"][p["id"]]:
                    score[m["employee_id"]] += (
                        BASE["proj_lead"] if m["role"] == "lead" else BASE["proj_member"]
                    )
    if use_answers:
        for a in fx["answers"]:
            if a.get("topic") == topic:
                score[a["responder_id"]] += (
                    BASE["ans_helpful"] if a.get("was_helpful") else BASE["ans"]
                )
    return dict(score)


def rank_experts_for_topics(fx, topics, weights=None, **kw):
    """複数トピック（上位k）を重み付きで足して人を並べる。"""
    weights = weights or [1.0 / (i + 1) for i in range(len(topics))]
    total = defaultdict(float)
    for w, t in zip(weights, topics, strict=False):
        for eid, v in expert_scores_for_topic(fx, t, **kw).items():
            total[eid] += w * v
    return rr.to_ranking(total)
