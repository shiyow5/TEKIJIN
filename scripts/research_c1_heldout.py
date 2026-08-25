"""research_c1_heldout.py — C1 few-shot の汎化を held-out で検証する（リーク対策）。

#384 レビューの指摘: few-shot 例が eval_person.json の取りこぼし質問の語彙・gold を反響
しており、同じ eval で測った +0.147 は暗記の可能性がある。そこで **eval とも few-shot 例
とも異なる新規質問**（`fixtures/synthetic/eval/eval_c1_heldout.json`・全22トピックを自然文で・
confusable 11 + 明確 11）で C1 のトピック的中を few-shot OFF/ON で測り、汎化を独立に確認する。

C1 のトピック予測は vLLM だけで完結する（埋め込み索引も pgvector も不要）。本番 vLLM(:18080)
のみ必要。confusable 群で改善しつつ、明確群で回帰しない（過剰発火しない）ことを確認する。

使い方（DGX・本番 vLLM 稼働）:
    PYTHONPATH=backend/src HF_HUB_OFFLINE=1 TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_c1_heldout.py --out c1_heldout.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
FIXTURE = os.path.join(REPO_ROOT, "fixtures", "synthetic", "eval", "eval_c1_heldout.json")

# confusable 群（few-shot が狙う隣接カテゴリ）と、明確群（回帰チェック）。held-out の id で分ける。
_CONFUSABLE_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 21}


def _hit(topics: list[str], gold: set[str], k: int) -> float:
    return 1.0 if gold and (set(topics[:k]) & gold) else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="c1_heldout.json")
    ap.add_argument("--fixture", default=FIXTURE, help="held-out 質問セットの JSON パス")
    args = ap.parse_args()
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.llm.vllm import VllmIntentModel

    base = get_settings()
    s_off = base.model_copy(update={"c1_fewshot_enabled": False})
    s_on = base.model_copy(update={"c1_fewshot_enabled": True})
    model_off = VllmIntentModel(settings=s_off)
    model_on = VllmIntentModel(settings=s_on)

    with open(args.fixture, encoding="utf-8") as fh:
        rows = json.load(fh)
    out_rows = []
    for r in rows:
        gold = set(r["gold_topics"])
        try:
            off_t = list(model_off.analyze(r["query"], None).topics)
        except Exception as exc:  # noqa: BLE001
            print(f"  !! id={r['id']} OFF失敗: {exc}", file=sys.stderr)
            off_t = []
        try:
            on_t = list(model_on.analyze(r["query"], None).topics)
        except Exception as exc:  # noqa: BLE001
            print(f"  !! id={r['id']} ON失敗: {exc}", file=sys.stderr)
            on_t = []
        out_rows.append(
            {
                "id": r["id"],
                "gold": sorted(gold),
                "off": off_t,
                "on": on_t,
                "confusable": r.get("confusable", r["id"] in _CONFUSABLE_IDS),
                "off_h1": _hit(off_t, gold, 1),
                "on_h1": _hit(on_t, gold, 1),
                "off_h3": _hit(off_t, gold, 3),
                "on_h3": _hit(on_t, gold, 3),
            }
        )
        print(f"id{r['id']} gold={sorted(gold)} off={off_t} on={on_t}")

    def _mean(key: str, subset: list[dict]) -> float:
        xs = [x[key] for x in subset]
        return round(statistics.mean(xs), 4) if xs else 0.0

    conf = [x for x in out_rows if x["confusable"]]
    clear = [x for x in out_rows if not x["confusable"]]
    summary = {
        "n": len(out_rows),
        "all": {
            "off_acc@1": _mean("off_h1", out_rows),
            "on_acc@1": _mean("on_h1", out_rows),
            "off_acc@3": _mean("off_h3", out_rows),
            "on_acc@3": _mean("on_h3", out_rows),
        },
        "confusable": {
            "n": len(conf),
            "off_acc@1": _mean("off_h1", conf),
            "on_acc@1": _mean("on_h1", conf),
            "off_acc@3": _mean("off_h3", conf),
            "on_acc@3": _mean("on_h3", conf),
        },
        "clear_regression_check": {
            "n": len(clear),
            "off_acc@1": _mean("off_h1", clear),
            "on_acc@1": _mean("on_h1", clear),
            "off_acc@3": _mean("off_h3", clear),
            "on_acc@3": _mean("on_h3", clear),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": out_rows}, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
