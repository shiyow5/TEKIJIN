#!/usr/bin/env python3
"""research_corpus.py — アブレーション実験（#65）が共有するコーパス構築。

**測定ハーネスであって製品コードではない。** `bench_embeddings.py` の `build_corpus` と
同じチャンク定義を保ったまま、実験で必要になる派生表現（人物集約文書・日報込み）を足す。

チャンク定義を `bench_embeddings.py` と一致させてあるのは、既存の実測値
（Nemotron 層2 R@3 = 0.615）を基準線としてそのまま比較したいため。
"""

from __future__ import annotations

import json
import os
from collections import defaultdict

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYN = os.path.join(REPO_ROOT, "fixtures", "synthetic")

# doc15 の base_score。チャンク種別 → 人への寄与の重み（bench_embeddings.py と同じ）
SOURCE_WEIGHT = {
    "ans_helpful": 1.0,
    "proj_lead": 0.8,
    "ans": 0.7,
    "profile": 0.5,
    "proj_member": 0.48,
}


def load(rel):
    with open(os.path.join(SYN, rel), encoding="utf-8") as f:
        return json.load(f)


def load_all():
    """fixtures を一括で読む。実験ごとに読み直さないための束ね。"""
    members = defaultdict(list)
    for m in load("projects/project_members.json"):
        members[m["project_id"]].append(m)
    return {
        "documents": load("documents/documents.json"),
        "projects": load("projects/projects.json"),
        "profiles": load("people/employee_profiles.json"),
        "employees": load("people/employees.json"),
        "answers": load("answers/answers.json"),
        "questions": load("questions/questions.json"),
        "dailies": load("daily_reports/daily_reports.json"),
        "skills": load("self_declared/skills.json"),
        "certs": load("certifications/certifications.json"),
        "members": members,
    }


def build_chunks(fx, include_daily=False):
    """文書中心の索引（Balog Model 2 相当）。返り値は (chunks, owners)。

    chunks: [(chunk_id, text)] / owners: chunk_id -> [(employee_id, source_key)]
    id は eval_retrieval.json の gold_chunks と同じ命名。
    """
    chunks, owners = [], {}

    for d in fx["documents"]:
        cid = f"doc:{d['id']}"
        chunks.append((cid, f"{d['title']}。{d['body']}"))
        owners[cid] = []  # 文書は人の証拠にならない（doc14 で格下げ）

    for p in fx["projects"]:
        cid = f"proj:{p['id']}"
        chunks.append(
            (
                cid,
                f"{p['subject']}。課題: {p['client_issue']}。商材: {p['product']}。{p.get('remarks', '')}",
            )
        )
        owners[cid] = [
            (m["employee_id"], "proj_lead" if m["role"] == "lead" else "proj_member")
            for m in fx["members"][p["id"]]
        ]

    for pr in fx["profiles"]:
        cid = f"profile:{pr['employee_id']}"
        chunks.append((cid, pr["description"]))
        owners[cid] = [(pr["employee_id"], "profile")]

    for a in fx["answers"]:
        cid = f"ans:{a['id']}"
        chunks.append((cid, a["body"]))
        owners[cid] = [
            (a["responder_id"], "ans_helpful" if a.get("was_helpful") else "ans")
        ]

    if include_daily:
        for d in fx["dailies"]:
            cid = f"daily:{d['id']}"
            chunks.append((cid, d["content"]))
            owners[cid] = [(d["employee_id"], "profile")]

    return chunks, owners


def build_person_docs(fx, include_daily=False, max_chars=4000):
    """人物中心の索引（Balog Model 1 相当）。1人=1文書に行動履歴を畳み込む。

    文書中心の索引は「根拠チャンクを引いてから人に集約する」ので、証拠が薄く広く
    散っている人を拾いにくい。人物単位で先に畳んでおくと、その人の活動全体と
    クエリの類似度を直接測れる。どちらが効くかは経験的な問題なので両方作る。
    """
    parts = defaultdict(list)

    for pr in fx["profiles"]:
        parts[pr["employee_id"]].append(pr["description"])

    for s in fx["skills"]:
        parts[s["employee_id"]].append(f"担当領域: {s['topic']}")

    for c in fx["certs"]:
        parts[c["employee_id"]].append(f"資格: {c['name']}")

    for p in fx["projects"]:
        line = f"案件: {p['subject']}。課題: {p['client_issue']}。商材: {p['product']}"
        for m in fx["members"][p["id"]]:
            parts[m["employee_id"]].append(line)

    for a in fx["answers"]:
        parts[a["responder_id"]].append(f"回答: {a['body']}")

    if include_daily:
        for d in fx["dailies"]:
            parts[d["employee_id"]].append(d["content"])

    docs = []
    for emp in fx["employees"]:
        eid = emp["id"]
        body = "。".join(parts.get(eid, []))[:max_chars]
        docs.append((f"person:{eid}", body or emp["name"]))
    return docs


def load_eval():
    person = load("eval/eval_person.json")
    retrieval = load("eval/eval_retrieval.json")
    return person, retrieval


def scored_person_items(person):
    """層2 で採点対象になる項目（L4 と gold 空を除く）。bench_embeddings.py と同じ条件。"""
    return [q for q in person if q["difficulty"] != "L4" and q["gold_experts"]]
