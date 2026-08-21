#!/usr/bin/env python3
"""research_payloads.py — `research_llm.py` に渡す入力を作る（#65 / #73）。

LLM を使う段は「検索結果を文脈に渡す」「候補者の実績サマリを見せる」ので、
入力そのものが検索結果に依存する。**その組み立てをここに集約して再現可能にする。**

    # 1) トピック分類（検索文脈つき）用: 全 71 件
    python scripts/research_payloads.py --task topic_ctx --emb emb/emb_Nemotron...npz --out payload_topic_ctx.json

    # 2) 棄却判定用: 分類結果から候補上位3名の実績サマリを作る
    python scripts/research_payloads.py --task abstain --emb ... --topics llm_topic_ctx.json --out payload_abstain.json

    # 3) listwise リランク用: 候補上位10名
    python scripts/research_payloads.py --task rerank --emb ... --topics llm_topic_ctx.json --out payload_rerank.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_ablation as A
import research_corpus as rc
import research_rank as rr
import research_topic as rt

NONE_LABEL = "該当なし"
CONTEXT_CHUNKS = 8
CONTEXT_CHARS = 180


def person_summaries(fx):
    """候補者1名 = 1行の実績サマリ。LLM に見せるのはこれだけ（本文は渡さない）。"""
    skills, certs, projects, answers = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    for s in fx["skills"]:
        skills[s["employee_id"]].append(s["topic"])
    for c in fx["certs"]:
        certs[c["employee_id"]].append(c["name"])
    for p in fx["projects"]:
        for m in fx["members"][p["id"]]:
            role = "リード" if m["role"] == "lead" else "メンバー"
            projects[m["employee_id"]].append(f"{p['product']}({role})")
    for a in fx["answers"]:
        if a.get("topic"):
            answers[a["responder_id"]].append(a["topic"])

    out = {}
    for e in fx["employees"]:
        eid = e["id"]
        out[eid] = (
            f"{e['name']}／{e.get('department', '')}／{e.get('branch', '')}。"
            f"担当領域: {'、'.join(skills[eid]) or 'なし'}。"
            f"資格: {'、'.join(certs[eid][:3]) or 'なし'}。"
            f"参加案件: {'、'.join(projects[eid][:6]) or 'なし'}。"
            f"回答実績: {'、'.join(answers[eid][:6]) or 'なし'}。"
        )
    return out


def load_topics(path):
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    out = {}
    for d in records:
        try:
            out[d["id"]] = [
                t for t in json.loads(d["content"])["topics"] if t != NONE_LABEL
            ]
        except (ValueError, KeyError):
            continue
    return out


def candidates_for(ctx, fx, item, topics, top_k):
    """トピックから構造化スコアラーで並べ、足りなければ dense 集約で埋める。"""
    picked = rt.rank_experts_for_topics(fx, topics[:1])[:top_k] if topics else []
    if len(picked) < top_k:
        ranked, _ = A.dense_chunk_rank(
            ctx, ctx["model_name"], ctx["qid_pos"][item["id"]], False, 64
        )
        fallback = rr.to_ranking(
            rr.aggregate_people(ranked, ctx["owners"], rc.SOURCE_WEIGHT, top_n=20)
        )
        for eid in fallback:
            if eid not in picked:
                picked.append(eid)
            if len(picked) >= top_k:
                break
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=["topic_ctx", "abstain", "rerank"])
    ap.add_argument("--emb", required=True)
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument(
        "--topics", default=None, help="abstain / rerank で使う分類結果 JSON"
    )
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    ctx = A.build_context([args.emb])
    ctx["model_name"] = args.model
    fx = ctx["fx"]
    person, _retrieval = rc.load_eval()

    if args.task == "topic_ctx":
        chunks_all, _ = rc.build_chunks(fx, include_daily=True)
        text_of = dict(chunks_all)
        payload = []
        for q in person:  # L4 も含めて全件（棄却の測定に要る）
            ranked, _ = A.dense_chunk_rank(
                ctx, args.model, ctx["qid_pos"][q["id"]], False, 64
            )
            payload.append(
                {
                    "id": q["id"],
                    "query": q["query"],
                    "context": [
                        text_of[c][:CONTEXT_CHARS] for c in ranked[:CONTEXT_CHUNKS]
                    ],
                }
            )
    else:
        if not args.topics:
            raise SystemExit("--topics が要る")
        topics = load_topics(args.topics)
        summaries = person_summaries(fx)
        top_k = 3 if args.task == "abstain" else 10
        payload = []
        for q in person:
            picked = candidates_for(ctx, fx, q, topics.get(q["id"], []), top_k)
            payload.append(
                {
                    "id": q["id"],
                    "query": q["query"],
                    "candidates": [
                        {"no": i + 1, "employee_id": e, "summary": summaries[e]}
                        for i, e in enumerate(picked)
                    ],
                }
            )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"wrote {args.out} ({len(payload)} 件)")


if __name__ == "__main__":
    main()
