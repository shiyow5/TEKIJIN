#!/usr/bin/env python3
"""research_faithful.py — 製品の C1/C2 リクエストを**そのまま**組み立てる（#113）。

#111 の測定は system プロンプトを手で写し、human の書式を変え、JSON スキーマも
手書きだった。**それでは「出荷時の挙動」を名乗れない**（PR #112 の Codex レビュー）。

ここでは製品側を **import して** リクエストを作る。

リクエスト本体は**手で組み立てない**。製品が使う LangChain の組み立て器
（`ChatOpenAI._get_request_payload`）を直接呼んで、送信直前の dict をそのまま取り出す。
手で写すと必ずどこかがずれる（初版は temperature と依頼者を落としていた）。

これで自動的に入るもの:

  * system / human … `VllmIntentModel.prompt` / `VllmSufficiencyModel.prompt` の出力そのまま
  * `tools` / `tool_choice` / `parallel_tool_calls` … `with_structured_output` の既定は
    **function_calling** なので、Field description ごと関数定義になる
  * **`temperature=0.7`** … `_openai_model` は temperature を指定していないが、
    `ChatOpenAI` の既定値 0.7 が入る。`docs/specs/model-definition.md` は
    「C1・C2 は低温（決定性重視）」と書いているので、**製品はここで仕様に反している**
  * `max_tokens` は無し、`chat_template_kwargs` も無し
    （＝**thinking は ON のまま**動く）

GPU ホストには `tekijin` が入っていないので、**組み立てはローカル、送信は向こう**という分担にする。
組み立て結果（`messages` / `tools` / `tool_choice`）を JSON に落とし、
`research_llm.py --task raw` がそれを POST するだけにしてある。

    # 1) C1（質問文だけを見る。製品では C3/C4 の retrieval より前に走る）
    python scripts/research_faithful.py --task c1 --out payload_c1_faithful.json

    # 2) C1 の実出力から C2 のリクエストを作る
    python scripts/research_faithful.py --task c2 --c1 c1_faithful.json --out payload_c2_faithful.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "src")
)

import research_corpus as rc
from tekijin.agent.protocols import IntentResult
from tekijin.config import get_settings
from tekijin.llm.schemas import IntentSchema, SufficiencySchema
from tekijin.llm.vllm import VllmIntentModel, VllmSufficiencyModel, _openai_model

# 製品では `api/service.py` が必ず `{"id": asker_id}` を入れて C1 を呼ぶ。
# 誰にしても human 文字列の形は同じなので、固定の1名で代表させる。
ASKER = {"id": 1}


def as_request(messages, schema):
    """製品が送るリクエスト本体を、製品自身の組み立て器から取り出す。

    手で写すと必ずどこかがずれるので、`ChatOpenAI._get_request_payload` を直接呼ぶ。
    `model` だけは落とす（ベンチのサーバは `--served-model-name` が別名なので、
    送信側の `research_llm.py` が持っている名前を使う）。
    """
    settings = get_settings()
    bound = (
        _openai_model(settings.llm_model, settings).with_structured_output(schema).first
    )
    body = bound.bound._get_request_payload(messages, stop=None, **bound.kwargs)
    body.pop("model", None)
    return body


def items():
    """C2 に届きうる相談。正常系は層2の採点対象56件、異常系は eval_robustness の20件。

    異常系の内訳は C2 の担当ではないものを含む（`out_of_scope` は `graph._after_c1` で
    C1 の時点で外れる）。**どの段の担当かは集計側で分ける**ので、ここでは全部作る。
    """
    person, _ = rc.load_eval()
    rows = [
        {
            "id": f"p{i + 1}",
            "eval_id": q["id"],
            "klass": "normal",
            "difficulty": q["difficulty"],
            "query": q["query"],
        }
        for i, q in enumerate(rc.scored_person_items(person))
    ]
    rows += [
        {
            "id": f"r{r['id']}",
            "eval_id": r["id"],
            "klass": r["category"],
            "difficulty": None,
            "query": r["query"],
        }
        for r in rc.load("eval/eval_robustness.json")
    ]
    return rows


def build_c1(rows):
    return [
        {
            **row,
            "request": as_request(
                VllmIntentModel.prompt(row["query"], ASKER), IntentSchema
            ),
        }
        for row in rows
    ]


def load_c1(path):
    """C1 の実出力を IntentResult に戻す。

    **既定値で埋めない。** 製品では壊れた関数呼び出しは `PydanticToolsParser` が
    例外にし、ノードごと落ちる（`vllm.py` の `model.invoke`）。既定値で埋めると
    「トピック空」という、聞き返しを誘発する入力を自分で作ってしまう。
    読めなかった id は**返さない**ので、呼び出し側が母集団から外して別に数える。
    """
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    out = {}
    for d in records:
        try:
            parsed = IntentSchema(**json.loads(d["arguments"]))
        except (ValueError, KeyError, TypeError):
            continue
        out[d["id"]] = IntentResult(
            topics=list(parsed.topics),
            products=list(parsed.products),
            situation=parsed.situation,
            question_type=parsed.question_type,
            out_of_scope=parsed.out_of_scope,
            confidence=parsed.confidence,
        )
    return out


def build_c2(rows, c1_path):
    intents = load_c1(c1_path)
    dropped = [r["id"] for r in rows if r["id"] not in intents]
    if dropped:
        print(
            f"C1 が構造化出力を返せず除外（製品では例外）: {len(dropped)} 件 {dropped}"
        )
    payload = []
    for row in rows:
        if row["id"] not in intents:
            continue
        intent = intents[row["id"]]
        payload.append(
            {
                **row,
                # C1 が out_of_scope と判定した相談は、製品では C2 に**届かない**。
                # 集計で落とせるように印だけ付けて、リクエストは作っておく。
                "c1_out_of_scope": intent.out_of_scope,
                "c1_topics": list(intent.topics),
                "c1_products": list(intent.products),
                "c1_question_type": intent.question_type,
                "c1_confidence": intent.confidence,
                "request": as_request(
                    VllmSufficiencyModel.prompt(row["query"], intent), SufficiencySchema
                ),
            }
        )
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=["c1", "c2"])
    ap.add_argument("--c1", default=None, help="c2 で使う C1 の実出力")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = items()
    if args.task == "c1":
        payload = build_c1(rows)
    else:
        if not args.c1:
            raise SystemExit("--c1 が要る")
        payload = build_c2(rows, args.c1)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"wrote {args.out} ({len(payload)} 件)")


if __name__ == "__main__":
    main()
