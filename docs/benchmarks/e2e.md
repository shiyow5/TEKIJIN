# 製品コードでの実測（Issue #100）

> **測定時の構成**: e5-large(1024次元) / 経路閾値 0.80・0.70・0.50 / RRF 等重み。
> develop はその後 #102（埋め込み）・#115（C4 の RRF 重み）・#120（C5 の閾値較正）で変わっている。
> **ここの数値は現行構成では再現しない** → #132。

> ⚠️ **ここの層2 R@3 はすべて gold トピックを渡した条件で、`gold_topics` が空の4件を含む56件基準。**
> `gold_topics` がある52件で測ると 0.836 は **0.901** になる。
> また **C1 が実際に出すトピックではこの数字は出ない**（[llm_faithful.md](llm_faithful.md) §1・§4.6 / #116）。

これまでの数値（[ablation.md](ablation.md) / [robustness.md](robustness.md) / [scorer.md](scorer.md) /
[route.md](route.md) / [draft.md](draft.md)）は**すべて `scripts/research_*.py` の再現実装**で測ったもの。
式・重み・base_score・`decide_route` は製品の純関数を import しているが、
**C4（HybridRetriever）と DB 経路は別実装**なので、結論が製品コードで再現する保証がなかった。

#33（PR #95）で評価ランナーが入ったので、`HybridRetriever → decide_route → ExpertiseScorer` を
**実 DB・実埋め込み**で通した。実施 2026-08-23。

**ローカルで完結する。** `requirements-dev.txt` の `pgserver` が rootless PostgreSQL + pgvector を同梱しており、
Docker も GPU も要らない（埋め込みは CPU で370行・約4分）。

```bash
python scripts/research_e2e.py --task prepare    # seed + embed（初回のみ）
python scripts/research_e2e.py --task route      # C4→C5 の経路とチャネル類似度
python scripts/research_e2e.py --task variants   # 経路のバグを直したときの層2 R@3
```

## 1. 見つかったこと: 経路が全件 `prior_answer` に倒れ、候補が常に1名になる

`python -m tekijin.eval` の出力（そのまま）:

```
queries        : 71 (ranked 56, routed 56)
Top-1 Accuracy : 0.393 (目標 0.70)
Recall@3       : 0.140 (目標 0.90)
MRR            : 0.393 (目標 0.75)
Route Accuracy : 0.125 (目標 0.80)
  L1: R@3=0.333 Top-1=1.000 (n=10)
```

**L1 の「Top-1 = 1.000 なのに R@3 = 0.333」**が手がかりだった。
gold 4名のうち1名しか出せていない＝**3枠のうち1枠しか埋まっていない**。
また経路精度 **0.125 はちょうど 7/56** で、`prior_answer` の gold 件数と一致する。

C4→C5 を直接叩いて確定した。

```
予測経路の分布: {'prior_answer': 71}    ← 全71件
候補者数の分布: {10: 71}                ← C4 は毎回10名返している
```

`pipeline.py` は `route == "prior_answer"` のとき候補を1名に固定する（`candidates = [pinned]`）。
**C4 が返した10名のうち9名が捨てられている。**

### 原因: 閾値が e5-large のコサイン分布とまったく合っていない

| チャネル | 最小 | 中央 | 最大 | 閾値 |
|---|---|---|---|---|
| `answer_confidence` | **0.816** | 0.854 | 0.928 | `PRIOR_ANSWER_SIM = 0.80` |
| `document_confidence` | 0.777 | 0.808 | 0.860 | `DOCUMENT_SIM = 0.70` |
| `people_confidence` | 0.790 | 0.821 | 0.860 | `PERSON_WEAK_SIM = 0.50` |

**最小値ですら `PRIOR_ANSWER_SIM` を超えている。** `decide_route` は prior_answer を最初に判定するので、
他の分岐に到達しない。

[route.md](route.md)（#88）で報告した較正ずれと同じ問題だが、**向きが逆**。
あちらは Nemotron（コサイン最大 0.57）で測って「一度も発火しない」だった。
**同じ閾値が、埋め込みモデル次第で「全く発火しない」と「常に発火する」の両極に振れる。**

## 2. 影響の大きさ

製品の `tekijin.eval.metrics` をそのまま使って、候補集合だけ差し替えて測った。

| 構成 | R@3 | Top-1 | MRR | L1 | L2 | L3 |
|---|---|---|---|---|---|---|
| **現状** | **0.140** | 0.393 | 0.393 | 0.333 | 0.116 | 0.033 |
| 経路の pin を外す（C4 の候補10名） | **0.732** | 0.768 | 0.812 | 0.967 | 0.694 | 0.633 |
| **候補を全社員にする**（[scorer.md](scorer.md) §3 / #87） | **0.836** | 0.804 | 0.866 | 0.967 | 0.819 | 0.767 |

- **この不具合だけで R@3 を 0.592 落としている**（#103）
- **候補を C4 で絞らず全社員を C6 に渡すと、さらに +0.104**。
  ハーネスで測った +0.030（#85）と同じ向きで、実機のほうが差が大きい

## 3. ハーネスの結論は再現したか

| ハーネスでの主張 | 製品コードでの結果 |
|---|---|
| C5 の閾値はモデル依存で較正が要る（#88 / #90） | **再現。しかも予測より深刻**（Nemotron で「発火しない」、e5 で「常に発火する」） |
| C4 で候補を絞ると落ちる（#85 / #87） | **再現。差は 0.030 → 0.104 と実機のほうが大きい** |
| トピックが分かれば人の並べ替えは強い | **再現**。gold トピックを渡した全社員スコアリングで **R@3 0.836 / L3 0.767** |

**ハーネスの向きはすべて正しく、大きさは控えめだった。**
唯一外したのは「経路が全件 `person` になる」という予測で、
これは私が Nemotron のコサイン分布で測っていたため。**製品の既定は e5-large で、逆に倒れる。**

## 4. 限界

1. **ランナーは gold トピックを scorer に渡す**（C1 を経由しない）。
   したがってここで測っているのは**層1-2と経路**で、[ablation.md](ablation.md) §4 の
   トピック媒介（LLM 分類）の効果はここには現れない
2. **`asker` を渡していない**ので `proximity` は company-wide 固定（ランナーの設計どおり）
3. 埋め込みは #63 で `nvidia/Nemotron-3-Embed-1B-BF16` に差し替え済み。この文書の上表は
   差し替え前の e5-large 時の測定。**Nemotron 実データで再測定した結果は
   `fixtures/synthetic/eval/route_calibration.json`（全 71 件 person・最大コサイン
   answer 0.543 / document 0.566 / people 0.454）にあり、閾値較正は #90 で行う**（§1）

個別値は `ablation/e2e_variants.json` / `ablation/e2e_route_confidences.json`。
