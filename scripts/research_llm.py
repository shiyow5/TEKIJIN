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
import research_corpus as rc

BASE_URL = os.environ.get("TEKIJIN_LLM_BASE_URL", "http://localhost:8080/v1")
MODEL = os.environ.get("TEKIJIN_LLM_MODEL", "qwen36-35b")

TOPIC_LIST = [
    "CRM・営業支援",
    "ECサイト構築",
    "SNS運用",
    "Webマーケティング・広告",
    "クラウド移行",
    "サーバー・インフラ運用",
    "システム開発・API",
    "セキュリティ",
    "データ基盤・分析",
    "ネットワーク・VPN",
    "パフォーマンスチューニング",
    "モバイルアプリ開発",
    "人事・採用",
    "問い合わせ・ヘルプデスク運用",
    "基幹システム",
    "契約管理",
    "広報・PR",
    "業務効率化コンサル",
    "社内IT・ヘルプデスク",
    "経理・決算",
    "総務・法務",
    "購買・仕入れ",
]

NONE_LABEL = "該当なし"

# 閉じた候補一覧から必ず1つ選ばせると、範囲外の相談でも必ず何かを選んでしまい**棄却できない**。
# Self-RAG（Asai et al., ICLR 2024）の反省トークンと同じ発想で、「該当なし」を選択肢に足した版。
TOPIC_SCHEMA_ABSTAIN = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {"type": "string", "enum": [*TOPIC_LIST, NONE_LABEL]},
            "minItems": 3,
            "maxItems": 3,
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

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


def call(messages, schema=None, max_tokens=512, temperature=0.0, thinking=False):
    # thinking を切らないと、guided decoding でも <think> に max_tokens を使い切って
    # content が空で返る（実測: max_tokens=128 が全部 reasoning に消えた）。
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": thinking},
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
                    {
                        "role": "user",
                        "content": f"相談文: {q}\n候補: {' / '.join(TOPIC_LIST)}",
                    },
                ],
                schema=TOPIC_SCHEMA,
                max_tokens=256,
            )
        elif task == "hyde":
            r = call(
                [
                    {"role": "system", "content": HYDE_SYS},
                    {"role": "user", "content": q},
                ],
                max_tokens=400,
            )
        elif task == "q2d":
            r = call(
                [
                    {"role": "system", "content": Q2D_SYS},
                    {"role": "user", "content": q},
                ],
                max_tokens=200,
            )
        else:
            raise ValueError(task)
        out.append({"id": item["id"], "query": q, **r})
        print(
            f"[{i + 1}/{len(items)}] {r['latency']:.2f}s {r['content'][:60]!r}",
            flush=True,
        )
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
                    "content": "相談文: "
                    + case["query"]
                    + "\n\n候補者:\n"
                    + "\n".join(lines),
                },
            ],
            schema=RANK_SCHEMA,
            max_tokens=256,
        )
        out.append({"id": case["id"], **r})
        print(
            f"[{i + 1}/{len(payload)}] {r['latency']:.2f}s {r['content'][:70]}",
            flush=True,
        )
    return out


TOPIC_CTX_SYS = (
    "あなたは社内相談の振り分け担当です。相談文と、社内データから検索した関連断片を読み、"
    "対応する分野を候補一覧から選びます。断片は関連しないものも混ざっているので鵜呑みにしないこと。"
    "確からしい順に3つ返します。"
)

TOPIC_ABSTAIN_SYS = (
    TOPIC_CTX_SYS
    + "どの分野にも当てはまらない相談（社外の話・雑談・自社が扱わない領域）なら"
    "「該当なし」を1位に選びます。無理に当てはめないこと。"
)


def run_topic_ctx(payload, abstain=False, samples=1, temperature=0.0):
    """検索結果を文脈として与えてから分類させる（RAG 型の分類）。"""
    out = []
    for i, case in enumerate(payload):
        ctx_text = "\n".join(f"- {c}" for c in case["context"])
        draws = []
        for _ in range(samples):
            draws.append(
                call(
                    [
                        {
                            "role": "system",
                            "content": TOPIC_ABSTAIN_SYS if abstain else TOPIC_CTX_SYS,
                        },
                        {
                            "role": "user",
                            "content": (
                                f"相談文: {case['query']}\n\n検索された関連断片:\n{ctx_text}\n\n"
                                f"候補: {' / '.join(TOPIC_LIST)}"
                            ),
                        },
                    ],
                    schema=TOPIC_SCHEMA_ABSTAIN if abstain else TOPIC_SCHEMA,
                    max_tokens=256,
                    temperature=temperature,
                )
            )
        r = {**draws[0], "draws": [d["content"] for d in draws]}
        _unused = (
            [
                {
                    "role": "system",
                    "content": TOPIC_ABSTAIN_SYS if abstain else TOPIC_CTX_SYS,
                },
                {
                    "role": "user",
                    "content": (
                        f"相談文: {case['query']}\n\n検索された関連断片:\n{ctx_text}\n\n"
                        f"候補: {' / '.join(TOPIC_LIST)}"
                    ),
                },
            ],
        )
        out.append({"id": case["id"], **r})
        print(
            f"[{i + 1}/{len(payload)}] {r['latency']:.2f}s {r['content'][:60]!r}",
            flush=True,
        )
    return out


ABSTAIN_SYS = (
    "社内の相談窓口の判断役です。相談文と、候補として挙がった社員の実績サマリを読み、"
    "**その相談に答えられる実績が社内にあるか**を判定します。"
    "候補者の実績が相談の主題をどれくらい覆っているかを confidence(0-100) で表します。"
    "同じ分野の実務経験があれば70以上、分野が近いだけなら30〜60、"
    "社内にまったく痕跡がない領域なら30未満。answerable は confidence>=50 と一致させます。"
    "理由を20字以内で添えます。"
)

ABSTAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
    },
    "required": ["answerable", "confidence", "reason"],
    "additionalProperties": False,
}


def run_abstain_check(payload):
    """CRAG / Self-RAG の retrieval evaluator に相当する「証拠が足りているか」判定。

    トピック分類に「該当なし」を足しても棄却できなかった（実測 0/5）ので、
    トピックではなく**証拠の当たり方**を見る別段として切り出す。
    """
    out = []
    for i, case in enumerate(payload):
        lines = [f"- {c['summary']}" for c in case["candidates"][:3]]
        r = call(
            [
                {"role": "system", "content": ABSTAIN_SYS},
                {
                    "role": "user",
                    "content": "相談文: "
                    + case["query"]
                    + "\n\n候補者の実績:\n"
                    + "\n".join(lines),
                },
            ],
            schema=ABSTAIN_SCHEMA,
            max_tokens=128,
        )
        out.append({"id": case["id"], **r})
        print(
            f"[{i + 1}/{len(payload)}] {r['latency']:.2f}s {r['content'][:70]}",
            flush=True,
        )
    return out


DRAFT_SYS = (
    "あなたは社内の相談を取り次ぐ担当です。相談者に代わって、詳しい社員へ送る依頼文の下書きを書いてください。"
    "宛名・用件・背景・依頼事項・相手の負担への配慮を含め、200字程度の丁寧な日本語にしてください。"
    "**与えられた情報だけを使い、書かれていない実績・商材・資格・拠点を書かないでください。**"
    "JSONではなく本文だけを出力してください。"
)

JUDGE_SYS = (
    "社内の依頼文の下書きを2つ比べる審査員です。次の観点で優劣を決めてください。"
    "(1) 与えられた根拠だけで書けているか（書かれていない実績を足していないか）"
    "(2) 相談内容が正しく伝わるか (3) そのまま送れる丁寧さか (4) 長すぎないか。"
    "どちらが良いかを A / B / tie で答え、理由を30字以内で添えます。"
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "reason": {"type": "string"},
    },
    "required": ["winner", "reason"],
    "additionalProperties": False,
}


def run_draft(payload):
    """C7 の契約どおり、質問・専門家のレコード・不足スロットだけを渡して下書きを書かせる。"""
    out = []
    for i, case in enumerate(payload):
        r = case["responder"]
        missing = "、".join(case.get("missing") or []) or "なし"
        user = (
            f"相談内容: {case['question']}\n\n"
            f"取り次ぎ先: {r['name']}（{r.get('dept') or '所属不明'}）\n"
            f"この方を選んだ根拠: {'、'.join(r['reasons']) or '記載なし'}\n"
            f"相談者に確認が必要な点: {missing}"
        )
        res = call(
            [
                {"role": "system", "content": DRAFT_SYS},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
        )
        out.append({"id": case["id"], **res})
        print(
            f"[{i + 1}/{len(payload)}] {res['latency']:.2f}s {len(res['content'])}字",
            flush=True,
        )
    return out


def run_judge(payload):
    """位置バイアス対策に順序を入れ替えて2回聞く。一致した判定だけを採る側で集計する。"""
    out = []
    for i, case in enumerate(payload):
        verdicts = {}
        for order in ("ab", "ba"):
            first, second = (
                (case["a"], case["b"]) if order == "ab" else (case["b"], case["a"])
            )
            res = call(
                [
                    {"role": "system", "content": JUDGE_SYS},
                    {
                        "role": "user",
                        "content": (
                            f"相談内容: {case['question']}\n\n"
                            f"取り次ぎ先の根拠: {case['reasons']}\n\n"
                            f"下書きA:\n{first}\n\n下書きB:\n{second}"
                        ),
                    },
                ],
                schema=JUDGE_SCHEMA,
                max_tokens=128,
            )
            verdicts[order] = res["content"]
        out.append({"id": case["id"], "verdicts": verdicts})
        print(f"[{i + 1}/{len(payload)}] {verdicts}", flush=True)
    return out


# 製品の `tekijin.llm.vllm._SUFFICIENCY_SYSTEM` を**そのまま**写したもの。これが出荷時の挙動。
C2_SYS_PRODUCT = (
    "あなたは情報充足の点検器です。取り次ぐ前に判断へ必要な情報が揃っているかを"
    "確認し、足りなければ『まとめて1つ』の逆質問を返します。"
)

# 「何が揃っていれば十分か」を書き足した版。製品版は基準を書いていないので、
# モデルが自前の基準（部署名・予算・技術スタック…）を持ち込む余地がある。
C2_SYS_SCOPED = (
    "あなたは社内の相談を受け付ける担当です。相談文だけで「誰に取り次ぐか」を判断できるかを決めます。"
    "困りごとの対象・領域が特定できないほど曖昧なら sufficient を false にし、"
    "聞き返す質問を1文だけ添えてください。判断できるなら true にし、聞き返しは空文字にします。"
    "不足している項目があれば missing に短い語で並べます。"
)

C2_PROMPTS = {"product": C2_SYS_PRODUCT, "scoped": C2_SYS_SCOPED}

C2_SCHEMA = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "missing": {"type": "array", "items": {"type": "string"}},
        "followup_question": {"type": "string"},
    },
    "required": ["sufficient", "missing", "followup_question"],
    "additionalProperties": False,
}


def run_c2(payload, variant="scoped"):
    """C2 充足判定（#111）。契約どおり C1 の推定トピックも渡す。

    **変えるのは system プロンプトだけ**にして、渡す情報は両版で同じにする
    （製品の human 側は `トピック=... 製品=... 種別=...` という書式だが、
    製品・種別は本ハーネスの入力に無いので字面を揃えず、情報量を揃える方を採った）。
    """
    out = []
    for i, case in enumerate(payload):
        topics = "、".join(case.get("topics") or []) or "推定できず"
        res = call(
            [
                {"role": "system", "content": C2_PROMPTS[variant]},
                {
                    "role": "user",
                    "content": f"相談文: {case['query']}\n\nC1 が推定した分野: {topics}",
                },
            ],
            schema=C2_SCHEMA,
            max_tokens=256,
        )
        out.append({"id": case["id"], "klass": case.get("klass"), **res})
        print(
            f"[{i + 1}/{len(payload)}] {res['latency']:.2f}s {res['content'][:70]}",
            flush=True,
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--task",
        required=True,
        choices=[
            "topic",
            "hyde",
            "q2d",
            "rerank",
            "topic_ctx",
            "topic_abstain",
            "abstain_check",
            "draft",
            "judge",
            "c2",
        ],
    )
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--c2-prompt",
        default="scoped",
        choices=sorted(C2_PROMPTS),
        help="product=製品の system プロンプトそのまま / scoped=判断基準を書き足した版",
    )
    ap.add_argument("--payload", default=None, help="rerank 用の候補者 JSON")
    ap.add_argument(
        "--samples",
        type=int,
        default=1,
        help="自己整合性（Wang et al., ICLR 2023）: temperature>0 で複数回引いて多数決",
    )
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args()

    if args.task in (
        "rerank",
        "topic_ctx",
        "topic_abstain",
        "abstain_check",
        "draft",
        "judge",
        "c2",
    ):
        with open(args.payload, encoding="utf-8") as f:
            payload = json.load(f)
        if args.task == "rerank":
            out = run_rerank(payload)
        elif args.task == "abstain_check":
            out = run_abstain_check(payload)
        elif args.task == "draft":
            out = run_draft(payload)
        elif args.task == "judge":
            out = run_judge(payload)
        elif args.task == "c2":
            out = run_c2(payload, args.c2_prompt)
        else:
            out = run_topic_ctx(
                payload,
                abstain=args.task == "topic_abstain",
                samples=args.samples,
                temperature=args.temperature,
            )
    else:
        person, _ = rc.load_eval()
        out = run_query_task(rc.scored_person_items(person), args.task)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
