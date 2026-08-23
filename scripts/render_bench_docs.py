#!/usr/bin/env python3
"""render_bench_docs.py — docs/benchmarks/*.md の数表を測定結果 JSON から生成する（#158）。

## なぜ要るか

この文書群は測定結果の記録で、数字と「どの基準で測ったか」が命である。
にもかかわらず、これまで表を**手で書き換えて**いた。その結果、

* 表を直したのに、隣の散文や別の表を直し忘れる（レビューで CRITICAL 3件・HIGH 5件）
* 信頼区間を「基準の差だけ平行移動」して書き換える（＝実測でない数字を書く）を2回

を繰り返した。**手で書ける限り同じことが起きる**ので、表は JSON から生成する。

## 使い方

    python scripts/render_bench_docs.py           # 生成して書き戻す
    python scripts/render_bench_docs.py --check   # 差分があれば終了コード 1

markdown 側は次のマーカーで囲む。囲まれた中身は**毎回まるごと差し替わる**。

    <!-- gen:NAME -->
    （ここは生成される。手で編集しても次の実行で消える）
    <!-- /gen:NAME -->

## 監査

表の生成に加えて、**本文に現れる 95%CI がすべて実測 JSON に存在するか**を検査する。
存在しない区間は「測っていない数字」なので、`STALE_CI` に理由つきで登録しない限り落とす。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(REPO, "docs", "benchmarks")
ABL = os.path.join(BENCH, "ablation")
FIXTURES = os.path.join(REPO, "fixtures", "synthetic")


def load(name, base=ABL):
    with open(os.path.join(base, name), encoding="utf-8") as f:
        return json.load(f)


def f3(x):
    return f"{x:.3f}"


def d3(x):
    return f"{x:+.3f}"


def ci(row):
    return f"[{row['ci'][0]:+.3f},{row['ci'][1]:+.3f}]"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 拠点制約（robustness.md §3・§4）— fixtures が出所
# --------------------------------------------------------------------------- #
BRANCHES = ("本社", "東京", "名古屋", "大阪", "福岡")

# 言い回しのラベル → 表に出す説明。build_eval_v2._constraint_phrasings と対応する。
PHRASING_LABEL = {
    "region_visit": "地域名から解決（現地で動ける方）",
    "region_direct": "地域名から解決（〜で対応できる方）",
    "alias": "「本部」と言い換え",
    "lead": "制約を文頭に置く",
    "reason": "理由つき・末尾定型ではない",
}


def _naive_branch(q):
    for b in BRANCHES:
        if b in q:
            return b
    return None


def _phrasing_of(query, branch):
    """生成された文面から、どの言い回しかを判定する（表示用）。"""
    if "現地で動ける方" in query:
        return "region_visit"
    if "で対応できる方" in query:
        return "region_direct"
    if "本部に席がある方" in query:
        return "alias"
    if query.startswith(f"{branch}側で一緒に"):
        return "lead"
    return "reason"


def _eval_rows():
    with open(
        os.path.join(FIXTURES, "eval", "eval_person.json"), encoding="utf-8"
    ) as f:
        return json.load(f)


def gen_constraint_items():
    rows = []
    for r in sorted(_eval_rows(), key=lambda x: x["id"]):
        c = r.get("constraint")
        if not c:
            continue
        br = c["branch"]
        hidden = _naive_branch(r["query"]) != br
        rows.append(
            [
                str(r["id"]),
                br,
                PHRASING_LABEL[_phrasing_of(r["query"], br)],
                "**出ない**" if hidden else "出る",
            ]
        )
    return table(["id", "制約", "言い回し", "拠点名"], rows)


def gen_decoy_items():
    rows = []
    for r in sorted(_eval_rows(), key=lambda x: x["id"]):
        if r.get("constraint"):
            continue
        b = _naive_branch(r["query"])
        if not b:
            continue
        tail = r["query"].split("。")[-2] + "。" if "。" in r["query"] else r["query"]
        rows.append([str(r["id"]), r["difficulty"], b, f"「{tail}」"])
    return table(["id", "難易度", "出てくる地名", "その一文"], rows)


def gen_constraint_extraction():
    rob = load("robustness_results.json")
    ce = rob["constraint_extraction"]
    n = ce["n_constrained"]
    tp = round(ce["recall"] * n)
    n_free = sum(1 for r in _eval_rows() if r["difficulty"] != "L4" and not r.get("constraint"))
    body = table(
        ["評価セット", "再現率", f"誤検出（制約なし{n_free}件中）"],
        [
            ["旧（定型・5件）", "5/5", "0"],
            ["#84（散らした・5件）", "2/5", "3"],
            [f"**#158（{n}件）**", f"**{tp}/{n}**", f"**{ce['false_positives']}**"],
        ],
    )
    missed = "・".join(str(m["id"]) for m in ce["missed"])
    fps = "・".join(str(m["id"]) for m in ce["false_hits"])
    return (
        body
        + f"\n\n取り逃すのは **id {missed}**、誤検出するのは **id {fps}**。"
        + "\n上の2つの表の「拠点名が出ない行」「デコイ」とちょうど一致する。"
    )


def gen_constraints_table():
    rob = load("robustness_results.json")
    n = rob["constraint_extraction"]["n_constrained"]
    rows = [
        [r["name"], f3(r["constrained"]), f3(r["overall"]), d3(r["delta"]), ci(r)]
        for r in rob["constraints"]
    ]
    return table(["構成", f"制約つき{n}件", "全体", "Δ（基準比）", "95%CI"], rows)


# --------------------------------------------------------------------------- #
# robustness.md §1・§2
# --------------------------------------------------------------------------- #
COLD_LABEL = {0.0: "0%（0件）", 0.1: "10%（15件）", 0.25: "25%（38件）",
              0.5: "50%（75件）", 0.75: "75%（113件）", 1.0: "100%（150件）"}


def gen_cold_start():
    rob = load("robustness_results.json")
    rows = []
    for r in rob["cold_start"]:
        cells = [COLD_LABEL[r["fraction"]]]
        best = max(r["dense"], r["llm_ctx_topic"])
        for k in ("dense", "retrieval_topic", "llm_plain_topic", "llm_ctx_topic",
                  "stageA_retrieval_acc1"):
            v = f3(r[k])
            if k in ("dense", "llm_ctx_topic") and abs(r[k] - best) < 1e-9:
                v = f"**{v}**"
            cells.append(v)
        rows.append(cells)
    return table(
        ["回答ログ", "現行 Dense 集約", "検索由来topic→構造化", "LLM(文脈なし)→構造化",
         "LLM(文脈つき)→構造化", "段A 検索由来 acc@1"],
        rows,
    )


def gen_l3_fusion():
    rob = load("robustness_results.json")
    rows = []
    for i, r in enumerate(rob["l3_fusion"]):
        name = f"**{r['name']}（現状）**" if i == 0 else r["name"]
        val = f"**{f3(r['R@3'])}**" if i == 0 else f3(r["R@3"])
        l3 = f"**{r['L3']:.2f}**" if i == 0 else f"{r['L3']:.2f}"
        rows.append([name, val, f"{r['L1']:.2f}", f"{r['L2']:.2f}", l3])
    return table(["構成", "全体", "L1", "L2", "**L3**"], rows)


# --------------------------------------------------------------------------- #
# scorer.md
# --------------------------------------------------------------------------- #
def _c6_rows(section, bold=()):
    c6 = load("c6_weights.json")
    rows = []
    for r in c6[section]:
        b = r["name"] in bold
        w = (lambda x: f"**{x}**") if b else (lambda x: x)
        rows.append([w(r["name"]), w(f3(r["R@3"])), w(d3(r["delta"]) + " " + ci(r)),
                     f3(r["MRR"]), f3(r["Top1"])])
    return rows


def gen_scorer_formula():
    return table(["構成", "R@3", "Δ（基準比）", "MRR", "Top1"],
                 _c6_rows("formula", bold={"C6 の完全な式（既定重み）"})
                 + _c6_rows("ablation"))


def gen_scorer_candidates():
    c6 = load("c6_weights.json")
    full = [r for r in c6["formula"] if r["name"].startswith("C6 の完全な式")][0]
    rows = [["**全40名を C6 に渡す**", f"**{f3(full['R@3'])}**", d3(full["delta"]), f3(full["MRR"])]]
    for r in c6["candidates"]:
        rows.append([r["name"].replace("を候補に", ""), f3(r["R@3"]), d3(r["delta"]), f3(r["MRR"])])
    return table(["候補集合", "R@3", "Δ（基準比）", "MRR"], rows)


def gen_scorer_constraint():
    c6 = load("c6_weights.json")
    rob = load("robustness_results.json")
    n = rob["constraint_extraction"]["n_constrained"]
    full = [r for r in c6["formula"] if r["name"].startswith("C6 の完全な式")][0]
    ignore = [r for r in rob["constraints"] if r["name"].startswith("制約を無視")][0]
    rows = [["渡さない（現状）", f3(ignore["constrained"]), f3(full["R@3"]),
             d3(full["delta"]) + " " + ci(full)]]
    for r in c6["constraint"]:
        rows.append([f"**渡す（{r['name']}）**", "—", f"**{f3(r['R@3'])}**",
                     d3(r["delta"]) + " " + ci(r)])
    return table(["構成", f"制約つき{n}件", "全体", "Δ（基準比）"], rows)


# --------------------------------------------------------------------------- #
# ablation.md
# --------------------------------------------------------------------------- #
BM25_ROWS = [
    ("Dense のみ", "base(dense+rank_sum,top20)"),
    ("Dense + BM25 RRF（等重み＝旧C4）", "Dense+BM25 RRF(等重み=現行C4)"),
    ("BM25 重み 0.5", "Dense+BM25 RRF(BM25重み0.5)"),
    ("BM25 重み 0.2", "Dense+BM25 RRF(BM25重み0.2)"),
    ("BM25 重み 0.1", "Dense+BM25 RRF(BM25重み0.1)"),
    ("BM25 のみ", "BM25のみ"),
]


def gen_bm25_table():
    abl = {r["name"]: r for r in load("ablation_results.json")}
    rows = []
    for label, key in BM25_ROWS:
        r = abl[key]
        if label == "Dense のみ":
            rows.append([label, f3(r["R@3"]), "—", ""])
        else:
            bold = "等重み" in label
            w = (lambda x: f"**{x}**") if bold else (lambda x: x)
            rows.append([label, f3(r["R@3"]), w(d3(r["delta"])), ci(r)])
    return table(["構成", "R@3", "Δ", "95%CI"], rows)


STAGE_A_SHOW = ["検索由来のみ", "LLM(文脈なし)", "LLM(検索文脈つき)",
                "LLM(該当なし選択肢つき)", "LLM(自己整合性5回)", "LLM(文脈つき)+検索由来(専門語)"]


def gen_stage_a():
    pipe = load("pipeline_results.json")
    by = {r["name"]: r for r in pipe["stageA"]}
    rows = []
    for n in STAGE_A_SHOW:
        r = by[n]
        b = n == "LLM(検索文脈つき)"
        w = (lambda x: f"**{x}**") if b else (lambda x: x)
        rows.append([w(n), w(f3(r["acc@1"])), f3(r["acc@3"])])
    return table(["手法", "acc@1", "acc@3"], rows)


def gen_stage_c():
    pipe = load("pipeline_results.json")
    base = load("ablation_results.json")[0]["R@3"]
    rows = [["基準（Dense 集約）", f3(base), "—", "", ""]]
    for r in pipe["stageC"]:
        b = r["name"].startswith("LLM(検索文脈つき)")
        w = (lambda x: f"**{x}**") if b else (lambda x: x)
        rows.append([w(r["name"]), w(f3(r["R@3"])), d3(r["delta"]), ci(r), f"{r['p_gt0']:.2f}"])
    return table(["構成", "R@3", "Δ", "95%CI", "P(Δ>0)"], rows)


# --------------------------------------------------------------------------- #
# e2e.md
# --------------------------------------------------------------------------- #
OLD_E5 = {"現状（そのまま）": "0.140", "経路の pin を外す（C4 の候補10名）": "0.732",
          "候補を全社員にする（#87）": "0.836"}


def e2e_basis():
    """採点対象と gold トピックありの件数を、測定結果そのものから取る。"""
    d = load("e2e_variants_nemotron.json")
    return len(d[0]["per_query"]), d[0]["n_with_gold_topics"]


def gen_e2e_variants():
    d = load("e2e_variants_nemotron.json")
    rows = []
    for v in d:
        bd = v["by_difficulty"]
        rows.append([
            f"**{v['name']}**",
            f"**{f3(v['recall_at_3'])}** ({f3(v['recall_at_3_with_gold_topics'])})",
            f3(v["top1"]), f3(v["mrr"]),
            f3(bd["L1"]), f3(bd["L2"]), f3(bd["L3"]),
            OLD_E5.get(v["name"], "—"),
        ])
    return table(["構成", "R@3", "Top-1", "MRR", "L1", "L2", "L3", "旧 R@3"], rows)


def gen_e2e_c1():
    c = load("e2e_variants_nemotron_c1both_top1.json")
    rows = []
    for v in c:
        if not v["name"].startswith("[切り分け]"):
            continue
        rows.append([v["name"].replace("[切り分け] ", ""),
                     f"**{f3(v['recall_at_3'])}**", f3(v["recall_at_3_with_gold_topics"])])
    n_all = len(c[0]["per_query"])
    n_gold = c[0]["n_with_gold_topics"]
    return table(["構成", f"R@3（{n_all}件）", f"{n_gold}件"], rows)


def gen_route_channels():
    r = load("route_nemotron.json")
    rows_ = r["rows"]
    th = r["_meta"]["thresholds"]
    names = [("answer_confidence", "prior_answer_sim", "PRIOR_ANSWER_SIM"),
             ("document_confidence", "document_sim", "DOCUMENT_SIM"),
             ("people_confidence", "person_weak_sim", "PERSON_WEAK_SIM")]
    import statistics as st
    out = []
    for ch, tk, const in names:
        v = [x[ch] for x in rows_]
        mx = f3(max(v))
        if max(v) < th[tk]:
            mx = f"**{mx}**"
        out.append([f"`{ch}`", f3(min(v)), f3(st.median(v)), mx,
                    f"`{const} = {th[tk]:.2f}`"])
    return table(["チャネル", "最小", "中央", "最大", "閾値"], out)


def gen_route_matrix():
    import collections
    r = load("route_nemotron.json")
    cm = collections.Counter((x["gold_route"], x["route"]) for x in r["rows"])
    rows = [[f"`{g}`", f"`{p}`", str(n)] for (g, p), n in sorted(cm.items())]
    return table(["gold", "→ 予測", "件数"], rows)


# --------------------------------------------------------------------------- #
# misrecommendation.md / confidence.md
# --------------------------------------------------------------------------- #
def gen_misrec_buckets():
    m = load("misrecommendation.json")
    n = m["n_slots"]
    order = ["正解", "誤り: gold と同じ部署（証拠あり）",
             "誤り: gold と同じ拠点（部署は違う）", "誤り: 部署も拠点も違う（証拠あり）"]
    label = {"正解": "正解",
             "誤り: gold と同じ部署（証拠あり）": "**誤り: gold と同じ部署（そのトピックの証拠あり）**",
             "誤り: gold と同じ拠点（部署は違う）": "誤り: gold と同じ拠点（部署は違う。証拠あり）",
             "誤り: 部署も拠点も違う（証拠あり）": "誤り: 部署も拠点も違う（証拠あり）"}
    rows = []
    for k in order:
        c = m["buckets"].get(k, 0)
        b = k.startswith("誤り: gold と同じ部署")
        w = (lambda x: f"**{x}**") if b else (lambda x: x)
        rows.append([label[k], w(str(c)), w(f"{c / n:.1%}")])
    rows.append(["**誤り: そのトピックの証拠ゼロ**", "**0**", "**0.0%**"])
    return table(["分類", "件数", "割合"], rows)


def gen_misrec_confidence():
    m = load("misrecommendation.json")
    c = m["confidence"]
    rows = []
    for lab in ("高", "中"):
        ok, ng = c.get(f"{lab}/正解", 0), c.get(f"{lab}/誤り", 0)
        rows.append([f"**{lab}**", str(ok + ng), f"**{ok / (ok + ng):.1%}**"])
    rows.append(["低", "0", "—"])
    return table(["ラベル", "スロット数", "そのうち正解"], rows)


def _slots():
    return load("misrecommendation.json")["slots"]


def gen_gold_count():
    import collections
    by = collections.defaultdict(lambda: [0, 0])
    for s in _slots():
        by[s["n_gold"]][1] += 1
        by[s["n_gold"]][0] += 1 if s["hit"] else 0
    ks = sorted(by)
    best = max(ks, key=lambda k: by[k][0] / by[k][1])
    head = ["gold の人数"] + [str(k) for k in ks]
    rate = ["正解率"] + [
        (f"**{by[k][0] / by[k][1]:.3f}**" if k == best else f3(by[k][0] / by[k][1]))
        for k in ks
    ]
    n = ["n"] + [str(by[k][1]) for k in ks]
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
                      "| " + " | ".join(rate) + " |", "| " + " | ".join(n) + " |"])


def gen_gold_x_evidence():
    import collections
    slots = _slots()
    ks = sorted({s["n_gold"] for s in slots})
    rows = []
    for g in ks:
        cells = [f"gold {g}名"]
        for lo, hi in ((0, 3), (4, 99)):
            xs = [s for s in slots if s["n_gold"] == g and lo <= s["evidence_count"] <= hi]
            if not xs:
                cells.append("—")
                continue
            a = sum(1 for s in xs if s["hit"]) / len(xs)
            cells.append(f"{f3(a)} (n={len(xs)})")
        rows.append(cells)
    return table(["", "証拠3件以下", "証拠4件以上"], rows)


def gen_evidence_buckets():
    import collections
    by = collections.defaultdict(lambda: [0, 0])
    for s in _slots():
        by[s["evidence_count"]][1] += 1
        by[s["evidence_count"]][0] += 1 if s["hit"] else 0
    ks = sorted(by)
    head = ["証拠数"] + [str(k) for k in ks]
    rate = ["正解率"] + [
        ("1.000" if by[k][0] == by[k][1] else f"{by[k][0] / by[k][1]:.3f}".lstrip("0"))
        for k in ks
    ]
    n = ["n"] + [str(by[k][1]) for k in ks]
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
                      "| " + " | ".join(rate) + " |", "| " + " | ".join(n) + " |"])


def gen_unfillable():
    slots = _slots()
    un = [s for s in slots if s["rank"] > s["n_gold"]]
    ot = [s for s in slots if s["rank"] <= s["n_gold"]]
    return table(["", "n", "正解率"], [
        ["`rank > gold人数`（埋めようがない）", str(len(un)),
         f"**{f3(sum(1 for s in un if s['hit']) / len(un))}**"],
        ["それ以外", str(len(ot)), f3(sum(1 for s in ot if s["hit"]) / len(ot))],
    ])


# --------------------------------------------------------------------------- #
# ablation.md §2 の要約表 — 本文の見出し数字がここに集まるので、必ず生成する
# --------------------------------------------------------------------------- #
def _abl_by_name():
    return {r["name"]: r for r in load("ablation_results.json")}


def _pipe_by_name(stage):
    return {r["name"]: r for r in load("pipeline_results.json")[stage]}


# (表に出す名前, 出所, 備考) の3つ組。効果の数字は**必ず JSON から引く**。
WIN_ROWS = [
    ("**トピック媒介の構造化スコアリング**", ("stageC", "LLM(検索文脈つき) → 構造化"), "下記 §4", True),
    ("Query2doc（クエリ+専門語）", ("stageDE", "クエリ+専門語(Query2doc)"),
     "**#158 で L3 を増やして有意になった**（n=56 では +0.036 [-0.065,+0.146]）", False),
    ("日報をコーパスに入れる", ("abl", "＋日報をコーパスに入れる"),
     "向きは同じだが、#84 の再測定で有意でなくなった", False),
]

LOSE_ROWS = [
    ("Dense + BM25 の RRF（**旧 C4 の構成**）", ("abl", "Dense+BM25 RRF(等重み=現行C4)"), "§3", True),
    ("BM25 単独", ("abl", "BM25のみ"), "症状語しか出ないクエリと語彙が合わない", False),
    ("クロスエンコーダのリランカー（bge-reranker-v2-m3）",
     ("stageDE", "リランカー bge-reranker-v2-m3（574ms/query）"), "§5。+0.63s/クエリ", False),
    ("クロスエンコーダのリランカー（Qwen3-Reranker-0.6B）",
     ("stageDE", "リランカー Qwen3-Reranker-0.6B（704ms/query）"), "同上。+0.69s/クエリ", False),
    ("HyDE（仮想文書をクエリに連結）", ("stageDE", "クエリ+HyDE"),
     "段Aには効くが、人への集約では落ちる", True),
    ("HyDE（仮想文書だけで引く）", ("stageDE", "HyDE(passageプレフィクス)"),
     "同上。置き換えるとさらに落ちる", False),
    ("埋め込み2本のアンサンブル", ("abl", "埋め込み2本のRRF(Nemotron+Qwen3)"), "", False),
    ("人物中心の索引（Balog Model 1）", ("abl", "人物中心のみ(Model 1)"),
     "Balog 2006 の Model 2 優位は再現しなかった", False),
]


def _headline_rows(spec):
    src = {"abl": _abl_by_name(), "stageC": _pipe_by_name("stageC"),
           "stageDE": _pipe_by_name("stageDE")}
    rows = []
    for label, (where, key), note, bold in spec:
        r = src[where][key]
        eff = d3(r["delta"]) + " " + ci(r)
        rows.append([label, f"**{eff}**" if bold else eff, note])
    return rows


def gen_headline_win():
    return table(["手法", "効果", "備考"], _headline_rows(WIN_ROWS))


def gen_headline_lose():
    return table(["手法", "効果", "なぜ効かないか"], _headline_rows(LOSE_ROWS))


def gen_circularity():
    """§4 循環確認。**再現できる2行だけ**を生成する。

    「案件を使わない / 回答を使わない」の4行はこれを出したスクリプトが残っておらず、
    再計算できない。生成表に混ぜると新旧の基準が同じ表に並ぶので、本文側で別表にしてある。
    """
    main = [r for r in load("pipeline_results.json")["stageC"]
            if r["name"].startswith("LLM(検索文脈つき)")][0]
    alt = [r for r in load("pipeline_results_alt.json")["stageC"]
           if r["name"].startswith("LLM(検索文脈つき)")][0]
    rows = [
        ["主 gold（projects+daily 由来）", "全証拠", f3(main["R@3"]), d3(main["delta"]), ci(main)],
        ["第2の正解（answers 由来）", "全証拠", f3(alt["R@3"]), d3(alt["delta"]), ci(alt)],
    ]
    return table(["正解", "スコアラーが使う証拠", "R@3", "Δ（基準比）", "95%CI"], rows)


def gen_conclusion():
    """§1 の結論表。現行 Dense 集約と、トピック媒介の到達域を JSON から出す。"""
    base = load("ablation_results.json")[0]
    sc = [r for r in load("pipeline_results.json")["stageC"]
          if r["name"].startswith("LLM(検索文脈つき)")][0]
    c6 = {r["name"]: r for r in load("c6_weights.json")["formula"]}
    lo, hi = sorted((sc["R@3"], c6["C6 の完全な式（既定重み）"]["R@3"]))
    rows = [
        ["現行（Dense 検索 → チャンクを人へ集約）", f3(base["R@3"]), f3(base["MRR"])],
        ["**トピック媒介（LLM分類 → 構造化スコアラー）**",
         f"**{f3(lo)} – {f3(hi)}**", f3(sc["MRR"])],
    ]
    return table(["", "層2 R@3", "MRR"], rows)


def gen_holdout():
    """分割検証。**丸めた要約を本文に手書きしない**ための1行表。"""
    h = load("pipeline_results.json")["holdout"]
    rows = [[d3(h["mean"]), d3(h["median"]),
             f"{d3(h['p5'])} – {d3(h['p95'])}", f"{h['win_rate']:.2f}"]]
    return table(["平均", "中央値", "5–95%tile", "改善した割合"], rows)


# --------------------------------------------------------------------------- #
# llm_faithful.md — C1 の語彙・的中・応答時間（research_c1_vocab.py が出所）
# --------------------------------------------------------------------------- #
def _c1_vocab():
    return load("c1_vocab.json")


def gen_c1_variants():
    """§4.6 の C1 出力表。**手で数えない**（#158 で全部ずれた）。"""
    d = _c1_vocab()
    rows = []
    for v in d["variants"]:
        bold = v["name"] in ("製品のまま", "両方")
        w = (lambda x: f"**{x}**") if bold else (lambda x: x)
        rows.append([
            w(v["name"]),
            f"{v['structured']}/{v['n']}",
            f"{v['topics_in_vocab']}/{v['topics']}",
            w(f"{v['hit_all']}/{v['scorable']}"),
            f"{v['hit_top1']}/{v['scorable']}",
            f"{v['p50']:.2f}s",
            f"{v['p95']:.2f}s",
        ])
    return table(["", "構造化出力", "語彙に載ったトピック", "**gold 的中（全部）**",
                  "**gold 的中（上位1件）**", "p50", "p95<sup>※</sup>"], rows)


def gen_c1_thinking():
    """§4.5。製品は #141 で thinking OFF が既定になったので、ON に戻した版と並べる。"""
    d = _c1_vocab()
    on = d["thinking_on"]
    off = [v for v in d["variants"] if v["name"] == "製品のまま"][0]

    def col(v):
        return [f"{v['structured']}/{v['n']}", str(v["length_cut"]),
                (f"{v['out_tokens_median']:.0f}（最大 {v['out_tokens_max']}）"
                 if v["out_tokens_median"] is not None else "—"),
                f"{v['p50']:.2f}s / {v['p95']:.2f}s"]

    labels = ["C1 が構造化出力を返した", "`finish_reason=length`",
              "出力トークン中央", "C1 p50 / p95"]
    a, b = col(on), col(off)
    rows = [[lab, x, f"**{y}**"] for lab, x, y in zip(labels, a, b)]
    return table(["", "thinking を ON に戻す", "**製品のまま（#141 以降）**"], rows)


# 変種 → (全トピック, 上位2件, 上位1件) のファイル名。無い組み合わせは "—"。
C1_R3_ROWS = [
    ("製品のまま", "e2e_variants_c1_faithful.json", None, None),
    ("案1 プロンプト", "e2e_variants_c1_prompt.json", None, "e2e_variants_c1_prompt_top1.json"),
    ("案2 enum", "e2e_variants_c1_enum.json", None, "e2e_variants_c1_enum_top1.json"),
    ("両方", "e2e_variants_c1_both.json", "e2e_variants_c1_both_top2.json",
     "e2e_variants_c1_both_top1.json"),
]


def _c1_split(fn):
    """`[切り分け] C1 の実トピック＋全社員` の行を取る（gold あり基準, 全体基準）。"""
    if fn is None:
        return None
    d = load(fn)
    row = [v for v in d if v["name"].endswith("C1 の実トピック＋全社員")][0]
    return row["recall_at_3_with_gold_topics"], row["recall_at_3"]


def gen_c1_recall():
    """§4.6 の層2 R@3。**gold トピックがある件数を基準**にし、括弧内は採点対象全体。"""
    def cell(v, bold=False):
        if v is None:
            return "—"
        txt = f"{f3(v[0])} ({f3(v[1])})"
        return f"**{txt}**" if bold else txt

    rows = []
    for label, all_t, top2, top1 in C1_R3_ROWS:
        b = label == "両方"
        rows.append([f"**{label}**" if b else label,
                     cell(_c1_split(all_t)), cell(_c1_split(top2)),
                     cell(_c1_split(top1), bold=b)])
    gold = load("e2e_variants_nemotron.json")
    g = [v for v in gold if v["name"].startswith("候補を全社員")][0]
    rows.append(["（参考）gold トピック",
                 f"**{f3(g['recall_at_3_with_gold_topics'])}** ({f3(g['recall_at_3'])})", "—", "—"])
    return table(["", "トピック全部", "上位2件", "**上位1件だけ**"], rows)


def gen_c1c2_latency():
    """§4 の応答時間。**手で写さない**（#141 で桁が変わった）。"""
    d = load("c2_faithful_results.json")
    lat = d["latency"]
    rows = [
        ["C1（全件）", str(d["n_items"]), f"{lat['c1_p50']:.2f}s", f"{lat['c1_p95']:.2f}s"],
        ["C2（C2 に届いた分）", str(d["reached_c2"]), f"{lat['c2_p50']:.2f}s", f"{lat['c2_p95']:.2f}s"],
        ["**C1+C2（同じ相談で合算）**", str(d["reached_c2"]),
         f"**{lat['paired_p50']:.2f}s**", f"{lat['paired_p95']:.2f}s"],
    ]
    return table(["", "n", "p50", "p95"], rows)


def gen_confidence_rules():
    """confidence.md §4 のラベル案比較。**手で写さない**（#158 で全部ずれた）。"""
    d = load("confidence_stats.json")
    b, gaps = d["buckets"], d["gaps"]
    names = ["現行", "順位だけ", "証拠の種類だけ", "順位＋証拠の種類"]
    best = "順位＋証拠の種類"

    def cell(name, text):
        return f"**{text}**" if name == best else text

    rows = []
    for lab in ("高", "中", "低"):
        row = [lab]
        for n in names:
            v = b[n][lab]
            row.append(cell(n, f"{f3(v['acc'])} ({v['n']})") if v else "**出ない**")
        rows.append(row)
    rows.append(["単調"] + [("はい" if b[n]["monotonic"] else "**いいえ**") for n in names])
    for a, c in (("高", "中"), ("高", "低")):
        row = [f"{a} − {c}"]
        for n in names:
            g = gaps.get(f"{n}/{a}-{c}")
            row.append(cell(n, f"{d3(g['point'])} {ci(g)}") if g else "測れない")
        rows.append(row)
    return table([""] + [f"**{n}**" if n == best else n for n in names], rows)


def gen_scorer_conclusion():
    """scorer.md §1 の結論表。**地の文に数字を置かない**（#158 でここだけ旧値が残った）。"""
    c = load("c6_weights.json")
    form = {r["name"]: r for r in c["formula"]}
    abl = {r["name"]: r for r in c["ablation"]}
    cand = {r["name"]: r for r in c["candidates"]}
    full = form["C6 の完全な式（既定重み）"]
    plain = form["素の和（#65/#80 で使っていた形）"]
    grid = c["grid"]
    load_gain = full["Top1"] - abl["load を 0 に"]["Top1"]
    best_c4 = max(r["R@3"] for r in cand.values())
    rows = [
        ["C6 の式は素の base_score 和より良いか",
         f"**良い**（{f3(full['R@3'])} vs {f3(plain['R@3'])}）"],
        ["手で決めた重みは妥当か",
         f"**妥当。交差検証で上回る設定が見つからない**（{d3(grid['cv_mean'] - grid['default_full'])}）"],
        ["`load` ペナルティは精度を犠牲にしているか",
         f"**していない。むしろ Top-1 を {d3(load_gain)} 上げている**"],
        ["C4 で候補を絞ってから C6 に渡すべきか",
         f"**絞らないほうがよい**（{d3(best_c4 - full['R@3'])}）"],
    ]
    return table(["", "結果"], rows)


def gen_scorer_grid():
    """§5 グリッド探索。既定重みと交差検証の差を JSON から出す。"""
    g = load("c6_weights.json")["grid"]
    rows = [["既定重みの全件スコア", f"**{f3(g['default_full'])}**"],
            ["交差検証でのスコア", f"**{f3(g['cv_mean'])}**"],
            ["差（交差検証 − 既定）", f"**{d3(g['cv_mean'] - g['default_full'])}**"]]
    return table(["", "R@3"], rows)


TABLES = {
    "cold_start": (["robustness.md"], gen_cold_start),
    "l3_fusion": (["robustness.md", "ablation.md"], gen_l3_fusion),
    "constraints": (["robustness.md"], gen_constraints_table),
    "constraint_items": (["robustness.md"], gen_constraint_items),
    "decoy_items": (["robustness.md"], gen_decoy_items),
    "constraint_extraction": (["robustness.md"], gen_constraint_extraction),
    "scorer_conclusion": (["scorer.md"], gen_scorer_conclusion),
    "scorer_grid": (["scorer.md"], gen_scorer_grid),
    "scorer_formula": (["scorer.md"], gen_scorer_formula),
    "scorer_candidates": (["scorer.md"], gen_scorer_candidates),
    "scorer_constraint": (["scorer.md"], gen_scorer_constraint),
    "c1_variants": (["llm_faithful.md"], gen_c1_variants),
    "c1_recall": (["llm_faithful.md"], gen_c1_recall),
    "c1c2_latency": (["llm_faithful.md"], gen_c1c2_latency),
    "c1_thinking": (["llm_faithful.md"], gen_c1_thinking),
    "conclusion": (["ablation.md"], gen_conclusion),
    "holdout": (["ablation.md"], gen_holdout),
    "headline_win": (["ablation.md"], gen_headline_win),
    "circularity": (["ablation.md"], gen_circularity),
    "headline_lose": (["ablation.md"], gen_headline_lose),
    "bm25": (["ablation.md"], gen_bm25_table),
    "stage_a": (["ablation.md"], gen_stage_a),
    "stage_c": (["ablation.md"], gen_stage_c),
    "e2e_variants": (["e2e.md"], gen_e2e_variants),
    "e2e_c1": (["e2e.md"], gen_e2e_c1),
    "route_channels": (["e2e.md"], gen_route_channels),
    "route_matrix": (["e2e.md"], gen_route_matrix),
    "misrec_buckets": (["misrecommendation.md"], gen_misrec_buckets),
    "misrec_confidence": (["misrecommendation.md"], gen_misrec_confidence),
    "confidence_rules": (["confidence.md"], gen_confidence_rules),
    "gold_count": (["confidence.md"], gen_gold_count),
    "gold_x_evidence": (["confidence.md"], gen_gold_x_evidence),
    "evidence_buckets": (["confidence.md"], gen_evidence_buckets),
    "unfillable": (["confidence.md"], gen_unfillable),
}

# 実測 JSON に無い区間。**理由が書けるものだけ**をここに置く。
STALE_CI = {
    ("+0.003", "+0.191"): "ablation.md §4 循環確認・主gold（基準 0.601 時代。再計算スクリプトが残っていない）",
    ("-0.223", "+0.033"): "同上",
    ("+0.130", "+0.348"): "ablation.md §4 第2の正解（n=45 の基準が保存されていない）",
    ("+0.152", "+0.359"): "同上",
    ("-0.270", "+0.030"): "同上",
    ("-0.290", "-0.033"): "confidence.md 冒頭で「#158 以前はこうだった」と明示引用している過去の実測値",
    ("-0.065", "+0.146"): "ablation.md §2 で「n=56 のときはこうだった」と明示引用している過去の実測値",
    ("+0.014", "+0.278"): "misrecommendation.md で「#158 前はこうだった」と明示引用している過去の実測値",
    ("-0.017", "+0.319"): "misrecommendation.md スロット単位（research_confidence.py の出力ではないため JSON に無い）",
    ("+0.033", "+0.290"): "同上（問題単位）",
}


def known_cis():
    out = set()
    for name in ("ablation_results.json", "pipeline_results.json",
                 "pipeline_results_alt.json", "robustness_results.json",
                 "c6_weights.json", "confidence_stats.json"):
        path = os.path.join(ABL, name)
        if not os.path.exists(path):
            continue
        blob = json.dumps(load(name))
        for a, b in re.findall(r'"ci":\s*\[\s*(-?[\d.e-]+),\s*(-?[\d.e-]+)\s*\]', blob):
            out.add((f"{float(a):+.3f}", f"{float(b):+.3f}"))
        for a, b in re.findall(r'"ci\w*":\s*\[\s*(-?[\d.e-]+),\s*(-?[\d.e-]+)\s*\]', blob):
            out.add((f"{float(a):+.3f}", f"{float(b):+.3f}"))
    return out


def audit_intervals():
    """本文の 95%CI が実測 JSON に存在するかを見る。"""
    known = known_cis()
    bad = []
    for fn in sorted(os.listdir(BENCH)):
        if not fn.endswith(".md"):
            continue
        text = open(os.path.join(BENCH, fn), encoding="utf-8").read().replace("−", "-")
        for m in re.findall(r"\[\s*([+-]\d\.\d{3})\s*,\s*([+-]\d\.\d{3})\s*\]", text):
            if m in known or m in STALE_CI:
                continue
            bad.append(f"{fn}: [{m[0]},{m[1]}] は実測 JSON に無い")
    return bad


MARKER = re.compile(
    r"(<!-- gen:(?P<name>[a-z0-9_]+) -->\n)(?P<body>.*?)(\n<!-- /gen:(?P=name) -->)",
    re.S,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="差分があれば終了コード 1")
    args = ap.parse_args()

    stale, seen = [], set()
    for fn in sorted(os.listdir(BENCH)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(BENCH, fn)
        src = open(path, encoding="utf-8").read()

        def sub(m):
            name = m.group("name")
            seen.add(name)
            if name not in TABLES:
                raise SystemExit(f"{fn}: 未知のマーカー gen:{name}")
            body = TABLES[name][1]()
            if m.group("body") != body:
                stale.append(f"{fn}: gen:{name}")
            return m.group(1) + body + m.group(4)

        out = MARKER.sub(sub, src)
        if out != src and not args.check:
            open(path, "w", encoding="utf-8").write(out)

    missing = sorted(set(TABLES) - seen)
    if missing:
        print("マーカーが置かれていない表:", ", ".join(missing), file=sys.stderr)

    bad = audit_intervals()
    for b in bad:
        print("CI 監査:", b, file=sys.stderr)

    if args.check and (stale or bad):
        for s in stale:
            print("要再生成:", s, file=sys.stderr)
        raise SystemExit(1)
    if not args.check:
        print(f"生成: {len(seen)} 表 / 更新 {len(stale)} 箇所")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
