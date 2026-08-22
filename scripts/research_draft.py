#!/usr/bin/env python3
"""research_draft.py — C7 下書きの入力を作り、根拠に基づいているかを機械的に測る（#91）。

C7 の契約（`agent/protocols.py`）は `draft(question, responder, asker, missing) -> str` で、
**検索した文書は渡らない**。渡るのは質問文・選ばれた専門家のレコード・不足スロットだけ。
したがってハルシネーションの表面は「**渡されていない事実を書く**」ことになる。

    # 1) 入力を作る（トピック→C6 の証拠で専門家を1名選び、その根拠だけを渡す）
    python scripts/research_draft.py --task payload --emb emb/...npz --out payload_draft.json

    # 2) 生成後、根拠に基づいているかを照合する
    python scripts/research_draft.py --task ground --payload payload_draft.json \
        --drafts draft_qwen36.json --out ground_qwen36.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "src")
)

import research_ablation as A
import research_corpus as rc
from tekijin.scorer.evidence import collect_topic_evidence
from tekijin.scorer.weights import DEFAULT_WEIGHTS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from research_c6_weights import C6, latest_moment, load_topics

# C6 の `_build_reasons` が UI に出す文言に寄せる（Evidence.detail は種別ごとに粒度が違う）
_REASON_PREFIX = {
    "cert": "資格: ",
    "self": "自己申告スキル: ",
    "inferred": "推定スキル: ",
}


def reason_text(evidence):
    return _REASON_PREFIX.get(evidence.source_type, "") + evidence.detail


def build_payload(ctx, model, topics_path):
    """1件 = 質問 + 専門家1名（名前・部署・根拠）+ 不足スロット。"""
    fx = ctx["fx"]
    c6 = C6(fx, latest_moment(fx))
    topics = load_topics(topics_path)
    emp = {e["id"]: e for e in fx["employees"]}
    person, _retrieval = rc.load_eval()

    payload = []
    for q in person:
        if q["difficulty"] == "L4":
            continue  # 棄却すべき相談は下書きを作らない
        ts = topics.get(q["id"], [])
        if not ts:
            continue
        ranked = c6.rank(ts[0], DEFAULT_WEIGHTS)
        if not ranked:
            continue
        eid = ranked[0]
        evidence = collect_topic_evidence(
            ts[0],
            c6.certs.get(eid, []),
            c6.skills.get(eid, []),
            c6.memberships.get(eid, []),
            [a for a in c6.topic_answers(eid, ts[0]) if a.topic == ts[0]],
        )
        payload.append(
            {
                "id": q["id"],
                "question": q["query"],
                "responder": {
                    "employee_id": eid,
                    "name": emp[eid]["name"],
                    "dept": emp[eid].get("department"),
                    "reasons": [reason_text(e) for e in evidence][:8],
                },
                # 不足スロットは C2 の判定結果。ここでは空にしておき、
                # 「渡していないのに補足を作文しないか」を見る側に寄せる。
                "missing": [],
            }
        )
    return payload


# --------------------------------------------------------------------------- #
# 根拠との照合
# --------------------------------------------------------------------------- #
def vocabularies(fx):
    """社内に実在する固有名詞の集合。ここに無いものを書いたら作り話。"""
    names = {e["name"] for e in fx["employees"]}
    # 姓だけで書かれることが多いので姓も引けるようにする
    surnames = {n.split()[0] for n in names if " " in n}
    return {
        "names": names,
        "surnames": surnames,
        "name_of": {
            e["name"].split()[0]: e["name"] for e in fx["employees"] if " " in e["name"]
        },
        "products": {p["product"] for p in fx["projects"] if p.get("product")},
        "certs": {c["name"] for c in fx["certs"]},
        "branches": {e.get("branch") for e in fx["employees"] if e.get("branch")},
        "topics": {s["topic"] for s in fx["skills"]},
    }


def check_draft(text, case, vocab):
    """下書き1件を照合する。返り値は違反の一覧。"""
    issues = []
    responder = case["responder"]
    allowed_names = {responder["name"], responder["name"].split()[0]}
    reasons_text = " ".join(responder["reasons"])

    # 1) 取り次ぎ先以外の社員名が出ていないか
    for surname, full in vocab["name_of"].items():
        if surname in allowed_names:
            continue
        if re.search(rf"{re.escape(surname)}\s*(?:さん|様|氏)", text):
            issues.append({"kind": "other_person", "value": full})

    # 2) 渡していない商材名・資格名を書いていないか
    for kind, key in (("product", "products"), ("cert", "certs")):
        for term in vocab[key]:
            if term and term in text and term not in reasons_text:
                issues.append({"kind": f"unsupported_{kind}", "value": term})

    # 3) 渡していない拠点名（相談者の拠点は渡していない）
    for br in vocab["branches"]:
        if br and re.search(rf"{re.escape(br)}(?:拠点|支店|オフィス)", text):
            issues.append({"kind": "unsupported_branch", "value": br})

    # 4) 宛名が取り次ぎ先になっているか
    if not any(n in text[:60] for n in allowed_names):
        issues.append({"kind": "missing_addressee", "value": responder["name"]})

    # 5) 不足スロットを渡したのに触れていないか
    for slot in case.get("missing") or []:
        if slot not in text:
            issues.append({"kind": "missing_slot_dropped", "value": slot})
    return issues


def run_ground(payload_path, drafts_path):
    fx = rc.load_all()
    vocab = vocabularies(fx)
    with open(payload_path, encoding="utf-8") as f:
        cases = {c["id"]: c for c in json.load(f)}
    with open(drafts_path, encoding="utf-8") as f:
        drafts = json.load(f)

    per_case, counter = [], defaultdict(int)
    lengths = []
    for d in drafts:
        case = cases.get(d["id"])
        if case is None:
            continue
        text = (d.get("content") or "").strip()
        lengths.append(len(text))
        issues = check_draft(text, case, vocab)
        for i in issues:
            counter[i["kind"]] += 1
        per_case.append({"id": d["id"], "chars": len(text), "issues": issues})

    clean = sum(1 for c in per_case if not c["issues"])
    print(
        f"  件数 {len(per_case)} / 違反ゼロ {clean} ({clean / max(1, len(per_case)):.1%})"
    )
    print(f"  字数 中央 {sorted(lengths)[len(lengths) // 2]} / 最大 {max(lengths)}")
    for kind, n in sorted(counter.items(), key=lambda x: -x[1]):
        print(f"    {kind:24s} {n} 件")
    return {
        "n": len(per_case),
        "clean": clean,
        "counts": dict(counter),
        "cases": per_case,
    }


# --------------------------------------------------------------------------- #
# ペア比較（LLM-as-judge）
# --------------------------------------------------------------------------- #
def build_judge_payload(payload_path, a_path, b_path):
    with open(payload_path, encoding="utf-8") as f:
        cases = {c["id"]: c for c in json.load(f)}
    drafts = []
    for path in (a_path, b_path):
        with open(path, encoding="utf-8") as f:
            drafts.append(
                {d["id"]: (d.get("content") or "").strip() for d in json.load(f)}
            )
    a, b = drafts
    out = []
    for cid, case in cases.items():
        if cid not in a or cid not in b:
            continue
        out.append(
            {
                "id": cid,
                "question": case["question"],
                "reasons": "、".join(case["responder"]["reasons"]) or "記載なし",
                "a": a[cid],
                "b": b[cid],
            }
        )
    return out


def aggregate_judge(path, label_a, label_b):
    """位置を入れ替えた2回の判定が一致したものだけを勝敗として採る（MT-Bench の手当て）。"""
    with open(path, encoding="utf-8") as f:
        records = json.load(f)

    wins = {label_a: 0, label_b: 0}
    inconsistent = ties = 0
    first_position = 0
    for r in records:
        try:
            ab = json.loads(r["verdicts"]["ab"])["winner"]
            ba = json.loads(r["verdicts"]["ba"])["winner"]
        except (ValueError, KeyError):
            inconsistent += 1
            continue
        first_position += sum(1 for v in (ab, ba) if v == "A")
        if ab == "tie" or ba == "tie":
            ties += 1
        elif ab == "A" and ba == "B":
            wins[label_a] += 1
        elif ab == "B" and ba == "A":
            wins[label_b] += 1
        else:
            inconsistent += 1

    n = len(records)
    decided = wins[label_a] + wins[label_b]
    print(
        f"  {n} 件中 決着 {decided} / 引き分け {ties} / 順序で判定が割れた {inconsistent}"
    )
    print(f"    {label_a}: {wins[label_a]}   {label_b}: {wins[label_b]}")
    print(
        f"    先に出したほうを選んだ率: {first_position / max(1, 2 * n):.2f}（0.50 なら位置バイアス無し）"
    )
    return {
        "n": n,
        "wins": wins,
        "ties": ties,
        "inconsistent": inconsistent,
        "first_position_rate": first_position / max(1, 2 * n),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--task",
        required=True,
        choices=["payload", "ground", "judge_payload", "judge_agg"],
    )
    ap.add_argument("--emb")
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--topics", default="docs/benchmarks/ablation/llm_topic_ctx.json")
    ap.add_argument("--payload")
    ap.add_argument("--drafts")
    ap.add_argument("--a")
    ap.add_argument("--b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--verdicts")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.task == "payload":
        ctx = A.build_context([args.emb])
        result = build_payload(ctx, args.model, args.topics)
        print(f"{len(result)} 件")
    elif args.task == "judge_payload":
        result = build_judge_payload(args.payload, args.a, args.b)
        print(f"{len(result)} 件")
    elif args.task == "judge_agg":
        result = aggregate_judge(args.verdicts, args.label_a, args.label_b)
    else:
        result = run_ground(args.payload, args.drafts)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
