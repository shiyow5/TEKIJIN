# 現行モデルの評価指標 実測値（スコアカード）

指標の**定義と読み方**は [eval-metrics.md](eval-metrics.md)、天井の背景は
[ADR-0008](../adr/0008-system2-hit3-ceiling-and-augmentation-negatives.md)。ここには**数値だけ**を置く。

- 実機: `internship-dgx1`（本番 vLLM `Qwen3.6-35B-A3B-NVFP4` + `Nemotron-3-Embed-1B`）。
- 評価セット: `eval_person.json` 全 **87 行**（person 49 / data 23 / abstain 15）。
- 測定日: **2026-08-26**（フルグラフ E2E 系）。ハーネス: `scripts/research_fullgraph_eval.py`。
- ⚠ **C1 は同一入力でも run 間で topic acc@1 が ±0.09 振れる**（vLLM バッチ非決定性）。
  下の baseline Hit@3 が 0.71–0.76 に散るのはこのため。**単発の ±0.02 差は誤差**。複数 run で見る。

## 1. フルグラフ E2E（実 C1・製品の実力値）

`build_agent` を全 87 行に流し、C1 が自分でトピックを予測した条件。**これが製品の実力値。**

### 1.1 baseline（全フラグ OFF 相当・self_answer は既定 ON）

| 指標 | 実測（複数 run のレンジ） | 目標 |
|---|---|---|
| **Hit@3**（top3 に有効専門家≥1・真指標） | **0.71 – 0.76** | 高いほど良い |
| Top-1 Accuracy | 0.52 – 0.59 | 0.70 |
| Recall@3（分数被覆・補助） | 0.47 – 0.61 | 0.90 |
| **Route Accuracy** | **0.833** | 0.80 |
| **person recall（取次ぎ）** | **1.000（49/49）** | — |
| C1 topic acc@1 / acc@3 | 0.72 – 0.81 / 0.85 – 0.91 | — |
| abstain recall（棄却） | 0.000 | 構造的（L4 空 topic） |

### 1.2 系統①（自己回答 #291・self_answer ON）

| 指標 | 実測 |
|---|---|
| decision recall: 自己回答（data 由来） | 0.478（11/23） |
| decision recall: 取次ぎ（person） | **1.000（49/49）** |
| **source recall**（data 23 行・取りこぼさない率） | **0.239** |
| **source precision**（ハルシネーション検知） | **0.739** |
| grounded 率（保守的発火） | 0.261 |

→ self_answer は **person routing を不変（1.000）に保ったまま**、data 行で出典付き回答を返す
（低ハルシネーション）。[ADR-0008](../adr/0008-system2-hit3-ceiling-and-augmentation-negatives.md) §決定3。

### 1.3 オラクル上限（参考・gold topics を採点器へ渡した条件）

| 指標 | 値 | 注 |
|---|---|---|
| Hit@3（オラクル） | **≈0.9355** | **上限であって製品値ではない** |

実 E2E 0.71–0.76 との差（≈0.19）は**すべて C1 のトピック予測精度**。オラクルを実力と読まない。

## 2. 天井を超えようとした算法レバー＝すべて負（有効化しない）

同じフルグラフ harness・同条件での OFF/ON 比較。詳細は
[ADR-0008](../adr/0008-system2-hit3-ceiling-and-augmentation-negatives.md)。

| レバー | Hit@3 | RouteAccuracy | person recall | 判定 |
|---|---|---|---|---|
| baseline | 0.727 | 0.833 | **1.000** | — |
| **+ クエリ拡張 #371** | 0.742 | **0.667** | **0.776** | ✗ 経路破壊 |
| **+ union 採点 #380** | 0.712（R@3 0.472） | 0.833 | 1.000 | ✗ ランキング悪化 |
| **+ C1 few-shot #384** | 見かけ 0.803 → 実質ノイズ | 0.833 | 1.000 | ✗ eval leakage（[下記](#c1-few-shot)） |

<a id="c1-few-shot"></a>
### C1 few-shot の顛末（#384 クローズ）

| 段階 | 測定 | 判定 |
|---|---|---|
| 初版（eval で測定） | topic acc@1 0.721→0.868・Hit@3 0.712→0.803 | **train-on-test leakage**（暗記） |
| 定義ベース化 + eval | Hit@3 0.758→0.773（+0.015・分散内） | ノイズ |
| clean held-out（n=20・few-shot 語彙重複ゼロ・3 run） | acc@1 0.8→0.8/0.85/0.8・acc@3 0.8→0.9/0.9/0.8 | robust な信号なし |

→ **汚染（leakage・held-out 語彙・run 分散）を除くと改善は消える。有効化しない。**

## 3. 既存のオラクル/パイプライン実測（参考・self_answer OFF 時代）

[e2e.md](e2e.md) §0（`scripts/research_e2e.py` / PipelineRanker・**gold topics を C6 へ渡すオラクル**・
self_answer OFF・旧 81 行基準）。フルグラフ E2E とは測定モードが違うので直接比較しない。

| 構成 | 層2 R@3（オラクル） | 経路精度 |
|---|---|---|
| 現状（pin あり・候補10名） | 0.692 | 0.803 |
| 候補を全社員に（#87） | 0.775 | — |

## 4. まとめ（1 行）

**系統②の実力は Hit@3 ≈0.72–0.78・person recall 1.000・RouteAccuracy 0.833 で、
算法ではこれ以上 robust に伸びない**（データ/gold 天井・[ADR-0008](../adr/0008-system2-hit3-ceiling-and-augmentation-negatives.md)）。
系統①（自己回答）は person 経路を壊さず source precision 0.739 で有効化済。
この先は [rag-improvement-directions.md](../specs/rag-improvement-directions.md)（暗黙的情報の付与・多源抽出・系統③蓄積）。
