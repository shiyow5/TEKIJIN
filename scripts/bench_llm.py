#!/usr/bin/env python3
"""
bench_llm.py — 生成LLMの横並び比較（#61 / analysis/18 §5.1）。

**測定ハーネスであって製品コードではない。** #31（LangGraph エージェント）の実装とは独立に、
「C1/C2/C7 にどのモデルを当てるか」を決めるためだけに書いている。

比較の条件（analysis/18 §5.1）:
  固定するもの … プロンプト、temperature=0、max_tokens、評価セット、同時実行数=1
  変えるもの   … モデル（と thinking の ON/OFF）

測るもの:
  C1 意図理解  … JSONスキーマ妥当率 / トピック抽出 F1 / レイテンシ
  C2 充足判定  … 「情報が十分か」の正解率（eval_robustness の insufficient を不足側の正解に使う）
  C7 下書き    … レイテンシと tok/s。**品質は人手ルーブリックなので出力を保存するだけ**

実行:
    python scripts/bench_llm.py --base-url http://localhost:8080/v1 --model swallow-30b \
        --label "Qwen3-Swallow-30B-A3B-AWQ" --thinking off --out results.json
"""

import argparse
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYN = os.path.join(REPO_ROOT, "fixtures", "synthetic")

TOPICS_22 = [
    "ネットワーク・VPN",
    "セキュリティ",
    "社内IT・ヘルプデスク",
    "サーバー・インフラ運用",
    "クラウド移行",
    "基幹システム",
    "データ基盤・分析",
    "システム開発・API",
    "パフォーマンスチューニング",
    "モバイルアプリ開発",
    "ECサイト構築",
    "CRM・営業支援",
    "契約管理",
    "業務効率化コンサル",
    "Webマーケティング・広告",
    "SNS運用",
    "問い合わせ・ヘルプデスク運用",
    "経理・決算",
    "人事・採用",
    "総務・法務",
    "購買・仕入れ",
    "広報・PR",
]

C1_SYSTEM = (
    "あなたは社内の相談を仕分ける担当です。ユーザーの相談文を読み、"
    "次のJSONだけを出力してください。前置きも説明も付けないでください。\n"
    '{"topics": ["<下記の一覧から0〜2件>"], "situation": "<20字以内の要約>", '
    '"out_of_scope": <true|false>}\n'
    "topics に使ってよい値の一覧:\n" + "\n".join(f"- {t}" for t in TOPICS_22) + "\n"
    "社内の業務相談でない場合は out_of_scope を true にし、topics は空にしてください。"
)

C2_SYSTEM = (
    "あなたは社内の相談を受け付ける担当です。相談文だけで「誰に取り次ぐか」を判断できるか answer してください。"
    "次のJSONだけを出力してください。前置きも説明も付けないでください。\n"
    '{"enough_info": <true|false>, "followup": "<不足なら聞き返す質問を1文。十分なら空文字>"}\n'
    "困りごとの対象・領域が特定できないほど曖昧な場合は enough_info を false にしてください。"
)

C7_SYSTEM = (
    "あなたは社内の相談を取り次ぐ担当です。相談者に代わって、詳しい社員へ送る依頼文の下書きを書いてください。"
    "宛名・用件・背景・依頼事項・相手の負担への配慮を含め、200字程度の丁寧な日本語にしてください。"
    "JSONではなく本文だけを出力してください。"
)


def post(base_url, payload, timeout=180):
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer dummy"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# C1/C2 の構造化出力スキーマ。guided decoding（vLLM の structured outputs）に渡すと
# 生成がスキーマに拘束され、**内部推論そのものを出せなくなる**。
# JSON妥当率が上がるだけでなく、出力トークン数＝レイテンシが劇的に下がる。
C1_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"enum": TOPICS_22}, "maxItems": 2},
        "situation": {"type": "string"},
        "out_of_scope": {"type": "boolean"},
    },
    "required": ["topics", "situation", "out_of_scope"],
    "additionalProperties": False,
}
C2_SCHEMA = {
    "type": "object",
    "properties": {"enough_info": {"type": "boolean"}, "followup": {"type": "string"}},
    "required": ["enough_info", "followup"],
    "additionalProperties": False,
}


def call(base_url, model, system, user, max_tokens, thinking, schema=None):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "out", "schema": schema, "strict": True},
        }
    if thinking in ("on", "off"):
        payload["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    t0 = time.time()
    try:
        res = post(base_url, payload)
    except (urllib.error.URLError, TimeoutError) as e:
        return {"ok": False, "error": str(e), "elapsed": time.time() - t0}
    elapsed = time.time() - t0
    msg = res["choices"][0]["message"]
    text = msg.get("content") or ""
    usage = res.get("usage", {})
    out_tok = usage.get("completion_tokens", 0)
    return {
        "ok": True,
        "text": text,
        "reasoning": msg.get("reasoning_content") or "",
        "elapsed": elapsed,
        "out_tokens": out_tok,
        "tok_s": (out_tok / elapsed) if elapsed > 0 else 0.0,
    }


def extract_json(text):
    """モデルが前置きを付けた場合に備えて最初のJSONオブジェクトを拾う。"""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def load(rel):
    with open(os.path.join(SYN, rel), encoding="utf-8") as f:
        return json.load(f)


def f1(pred, gold):
    p, g = set(pred), set(gold)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    tp = len(p & g)
    prec = tp / len(p)
    rec = tp / len(g)
    return 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", default=None)
    ap.add_argument("--thinking", choices=["on", "off", "default"], default="off")
    ap.add_argument("--limit", type=int, default=0, help="各タスクの件数上限（0=全件）")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--guided",
        action="store_true",
        help="C1/C2 で構造化出力(guided decoding)を使う",
    )
    args = ap.parse_args()
    label = args.label or args.model
    c1_schema = C1_SCHEMA if args.guided else None
    c2_schema = C2_SCHEMA if args.guided else None

    person = load("eval/eval_person.json")
    robust = load("eval/eval_robustness.json")
    lim = lambda xs: xs[: args.limit] if args.limit else xs

    # ---------------- C1 意図理解 ----------------
    c1_items = lim([q for q in person if q["gold_topics"]])
    c1_oos = lim([r for r in robust if r["category"] == "out_of_scope"])
    valid, f1s, lat, toks, outs, oos_ok = 0, [], [], [], [], 0
    for q in c1_items:
        r = call(
            args.base_url,
            args.model,
            C1_SYSTEM,
            q["query"],
            512,
            args.thinking,
            c1_schema,
        )
        if not r["ok"]:
            continue
        lat.append(r["elapsed"])
        toks.append(r["tok_s"])
        outs.append(r["out_tokens"])
        d = extract_json(r["text"])
        if (
            isinstance(d, dict)
            and isinstance(d.get("topics"), list)
            and "out_of_scope" in d
        ):
            valid += 1
            f1s.append(f1([t for t in d["topics"] if t in TOPICS_22], q["gold_topics"]))
        else:
            f1s.append(0.0)
    for r0 in c1_oos:
        r = call(
            args.base_url,
            args.model,
            C1_SYSTEM,
            r0["query"],
            512,
            args.thinking,
            c1_schema,
        )
        if not r["ok"]:
            continue
        d = extract_json(r["text"])
        if isinstance(d, dict) and d.get("out_of_scope") is True:
            oos_ok += 1

    # ---------------- C2 充足判定 ----------------
    c2_short = lim([r for r in robust if r["category"] == "insufficient"])  # 正解: 不足
    c2_full = lim([q for q in person if q["difficulty"] in ("L1", "L2")])[
        : len(c2_short) * 3
    ]
    c2_hit, c2_n, c2_lat = 0, 0, []
    for item, gold_enough in [(x, False) for x in c2_short] + [
        (x, True) for x in c2_full
    ]:
        r = call(
            args.base_url,
            args.model,
            C2_SYSTEM,
            item["query"],
            512,
            args.thinking,
            c2_schema,
        )
        if not r["ok"]:
            continue
        c2_lat.append(r["elapsed"])
        d = extract_json(r["text"])
        c2_n += 1
        if isinstance(d, dict) and bool(d.get("enough_info")) is gold_enough:
            c2_hit += 1

    # ---------------- C7 下書き ----------------
    c7_items = lim([q for q in person if q["difficulty"] == "L2"])[:10]
    c7_lat, c7_tok, c7_out = [], [], []
    for q in c7_items:
        user = (
            f"相談内容: {q['query']}\n"
            "取り次ぎ先: 同じ社内の、この領域に詳しい先輩社員\n"
            "依頼文の下書きを書いてください。"
        )
        r = call(args.base_url, args.model, C7_SYSTEM, user, 700, args.thinking)
        if not r["ok"]:
            continue
        c7_lat.append(r["elapsed"])
        c7_tok.append(r["tok_s"])
        c7_out.append(
            {"query": q["query"], "draft": r["text"], "chars": len(r["text"])}
        )

    mean = lambda xs: statistics.mean(xs) if xs else float("nan")
    result = {
        "label": label,
        "model": args.model,
        "thinking": args.thinking,
        "guided": args.guided,
        "C1": {
            "n": len(c1_items),
            "json_valid_rate": valid / len(c1_items) if c1_items else float("nan"),
            "topic_f1": mean(f1s),
            "out_of_scope_acc": oos_ok / len(c1_oos) if c1_oos else float("nan"),
            "lat_p50": pct(lat, 0.5),
            "lat_p95": pct(lat, 0.95),
            "tok_s": mean(toks),
            "out_tokens": mean(outs),
        },
        "C2": {
            "n": c2_n,
            "acc": c2_hit / c2_n if c2_n else float("nan"),
            "lat_p50": pct(c2_lat, 0.5),
            "lat_p95": pct(c2_lat, 0.95),
        },
        "C7": {
            "n": len(c7_lat),
            "lat_p50": pct(c7_lat, 0.5),
            "lat_p95": pct(c7_lat, 0.95),
            "tok_s": mean(c7_tok),
            "mean_chars": mean([o["chars"] for o in c7_out]),
        },
        "drafts": c7_out,
    }

    c1, c2, c7 = result["C1"], result["C2"], result["C7"]
    print(f"\n===== {label}  (thinking={args.thinking}, guided={args.guided}) =====")
    print(
        f"C1 意図理解   JSON妥当率 {c1['json_valid_rate']:.3f} / トピックF1 {c1['topic_f1']:.3f} / "
        f"スコープ外検出 {c1['out_of_scope_acc']:.3f}"
    )
    print(
        f"              p50 {c1['lat_p50']:.2f}s  p95 {c1['lat_p95']:.2f}s  {c1['tok_s']:.1f} tok/s"
    )
    print(
        f"C2 充足判定   正解率 {c2['acc']:.3f} (n={c2['n']})  p50 {c2['lat_p50']:.2f}s  p95 {c2['lat_p95']:.2f}s"
    )
    print(
        f"C7 下書き     p50 {c7['lat_p50']:.2f}s  p95 {c7['lat_p95']:.2f}s  {c7['tok_s']:.1f} tok/s  "
        f"平均 {c7['mean_chars']:.0f}字"
    )
    print(f"C1+C2 合計 p95: {c1['lat_p95'] + c2['lat_p95']:.2f}s  (合格ライン 3秒)")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n結果: {args.out}")


if __name__ == "__main__":
    main()
