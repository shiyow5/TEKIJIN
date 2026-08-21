#!/usr/bin/env python3
"""research_llm.py — LLM を使う改善手法の実測（#65）。GPU ホストの vLLM に投げる。

対象（いずれも論文ベース）:
  topic … query → トピック分類。Adaptive-RAG（Jeong et al., NAACL 2024）の
          「クエリを先に分類して経路を決める」に相当。段Aの精度を LLM で上げられるかを見る
  hyde  … Hypothetical Document Embeddings（Gao et al., ACL 2023）。症状語しかないクエリに
          対して「あり得る社内文書」を書かせ、その埋め込みで引く
  q2d   … Query2doc（Wang et al., EMNLP 2023）。生成文をクエリに連結する（疎検索にも効く形）
  rerank… RankGPT（Sun et al., EMNLP 2023）の listwise permutation generation。
          候補者を証拠つきで並べ替えさせる

    python scripts/research_llm.py --task topic --out llm_topic.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_corpus as rc  # noqa: E402

BASE_URL = os.environ.get("TEKIJIN_LLM_BASE_URL", "http://localhost:8080/v1")
MODEL = os.environ.get("TEKIJIN_LLM_MODEL", "qwen36-35b")

TOPIC_LIST = [
    "CRM・営業支援", "ECサイト構築", "SNS運用", "Webマーケティング・広告", "クラウド移行",
    "サーバー・インフラ運用", "システム開発・API", "セキュリティ", "データ基盤・分析",
    "ネットワーク・VPN", "パフォーマンスチューニング", "モバイルアプリ開発", "人事・採用",
    "問い合わせ・ヘルプデスク運用", "基幹システム", "契約管理", "広報・PR", "業務効率化コンサル",
    "社内IT・ヘルプデスク", "経理・決算", "総務・法務", "購買・仕入れ",
]

TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {"type": "string", "enum": TOPIC_LIST},
            "minItems": 3,
            "maxItems": 3,
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

RANK_SCHEMA = {
    "type": "object",
    "properties": {"order": {"type": "array", "items": {"type": "integer"}}},
    "required": ["order"],
    "additionalProperties": False,
}


def call(messages, schema=None, max_tokens=512, temperature=0.0):
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "out", "schema": schema, "strict": True},
        }
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    msg = data["choices"][0]["message"]
    return {
        "content": msg.get("content") or "",
        "reasoning": msg.get("reasoning_content") or "",
        "latency": time.time() - t0,
        "out_tokens": data.get("usage", {}).get("completion_tokens"),
    }


TOPIC_SYS = (
    "あなたは社内相談の振り分け担当です。相談文を読み、対応する分野を候補一覧から選びます。"
    "相談文には分野名がそのまま書かれていないことが多いので、症状から推測してください。"
    "確からしい順に3つ返します。"
)

HYDE_SYS = (
    "あなたは社内の情報システム部門のベテランです。以下の相談に対して、"
    "社内ナレッジベースに載っていそうな回答文を、事実確認せずに書いてください。"
    "専門用語・製品名・作業手順を具体的に含め、200字程度で。前置きは書かない。"
)

Q2D_SYS = (
    "以下の社内相談を、社内検索で使う専門用語のリストに言い換えてください。"
    "名詞句だけを読点区切りで10語程度。説明文は書かない。"
)


def run_query_task(items, task):
    out = []
    for i, item in enumerate(items):
        q = item["query"]
        if task == "topic":
            r = call(
                [
                    {"role": "system", "content": TOPIC_SYS},
                    {"role": "user", "content": f"相談文: {q}\n候補: {' / '.join(TOPIC_LIST)}"},
                ],
                schema=TOPIC_SCHEMA,
                max_tokens=128,
            )
        elif task == "hyde":
            r = call(
                [{"role": "system", "content": HYDE_SYS}, {"role": "user", "content": q}],
                max_tokens=400,
            )
        elif task == "q2d":
            r = call(
                [{"role": "system", "content": Q2D_SYS}, {"role": "user", "content": q}],
                max_tokens=200,
            )
        else:
            raise ValueError(task)
        out.append({"id": item["id"], "query": q, **r})
        print(f"[{i + 1}/{len(items)}] {r['latency']:.2f}s {r['content'][:60]!r}", flush=True)
    return out


RANK_SYS = (
    "社内の相談に最も詳しい社員を選ぶ担当者です。候補者ごとに社内データから作った実績サマリが"
    "与えられます。相談内容に対して詳しい順に候補者番号を並べ替えて返してください。"
    "サマリに根拠がない候補は後ろに置きます。"
)


def run_rerank(payload):
    out = []
    for i, case in enumerate(payload):
        lines = [f"[{c['no']}] {c['summary']}" for c in case["candidates"]]
        r = call(
            [
                {"role": "system", "content": RANK_SYS},
                {
                    "role": "user",
                    "content": "相談文: " + case["query"] + "\n\n候補者:\n" + "\n".join(lines),
                },
            ],
            schema=RANK_SCHEMA,
            max_tokens=256,
        )
        out.append({"id": case["id"], **r})
        print(f"[{i + 1}/{len(payload)}] {r['latency']:.2f}s {r['content'][:70]}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=["topic", "hyde", "q2d", "rerank"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--payload", default=None, help="rerank 用の候補者 JSON")
    args = ap.parse_args()

    if args.task == "rerank":
        with open(args.payload, encoding="utf-8") as f:
            out = run_rerank(json.load(f))
    else:
        person, _ = rc.load_eval()
        out = run_query_task(rc.scored_person_items(person), args.task)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
