# ADR-0008: 系統②の実 Hit@3 天井（≈0.72–0.78）と、天井を超えようとした算法レバーの負の結果、および評価方法論

- ステータス: 承認
- 日付: 2026-08-26
- 決定者: A チーム（フルグラフ E2E 実測に基づく）
- 関連: ADR-0006（C6 証拠カバレッジ天井）/ ADR-0007（C5 データ経路 recall 天井・self_answer の status を本 ADR で更新）/ #380 / #381 / #382 / #371 / #291 / #327

## 背景

系統②（取次ぎ）の推薦品質を、これまでは `PipelineRanker`（検索＋採点のみ）や
retrieval-recall 近似で測っていた。いずれも **gold_topics を採点器へ直接与えるオラクル**で、
C1 が自分でトピックを予測し C5 が経路判定する**実グラフの挙動と乖離**していた。フラグ有効化の
是非を、実際の end-to-end 挙動でなくオラクル値で判断するリスクがあった。

そこで `build_agent` の実グラフを全 87 行（eval_person.json）へ E2E で流し、既存 `run_eval`/
`metrics`（Hit@3 / Recall@3 / source recall / decision recall / RouteAccuracy）にそのまま乗せる
ハーネス `scripts/research_fullgraph_eval.py`（#381）を作った。これで初めて、フラグ有効化の判断を
オラクルでなく実グラフで行えるようになった。

**指標の定義**: Hit@3 = top3 に有効専門家が 1 人以上いるか（プロダクト真指標＝取次ぎが成功したか）。
fractional Recall@3 は |gold|=4 の行（eval の 40%）を過小評価するため補助扱い。

## この ADR が確定させる実測（DGX・本番 Qwen3.6 + Nemotron・全 87 行）

### 実 E2E baseline（全フラグ OFF）

| 指標 | 値 |
| --- | --- |
| Hit@3 | **0.72–0.78**（run 間で振れる。下記の分散注記） |
| Top-1 | ~0.58 |
| RouteAccuracy | 0.833 |
| person recall | **1.000**（49/49） |
| C1 topic acc@1 | **run 間で ±0.09 振れる**（同一 prompt・同一質問でも 0.72–0.81） |

従来「Hit@3 0.9355 ＝ 系統②達成済」は **オラクル gold_topics を採点器へ与えた値**であり、
実システム（C1 がトピックを予測）の Hit@3 ではなかった。**実 E2E の天井は C1 のトピック予測精度**
（acc@1 ≈0.75）で決まり、オラクル上限 0.9355 との差はすべて C1 の精度差である。

### 天井を超えようとした算法レバー3本＝すべて負

| レバー | 仮説 | 実測 | 判定 |
| --- | --- | --- | --- |
| **クエリ拡張 #371**（C1 topics を C4 検索クエリに畳み込む） | 多ファセット質問の各部署を surface | retrieval-harness では R@3 +0.04 だが、**実グラフで person recall 1.000→0.776・RouteAccuracy 0.833→0.667**（noisy な実 C1 topics が C5 の retrieval confidence を崩す） | **有効化不可**。既定 OFF 維持 |
| **union 採点トピック #380**（C6 の採点に C1∪検索投票トピックを使う） | C1 と検索は別行で失敗するので併合が gold 被覆を上げる（診断: C1 hit@3 0.882→union 0.956） | **Hit@3 0.727→0.712・R@3 −0.114**。被覆↑でも検索投票（hit@1 0.53）のノイズが採点を汚し非 gold を押し上げる。**被覆≠ランキング品質** | 出荷せず revert |
| **C1 few-shot #380**（隣接カテゴリ判別を C1 プロンプトに付与） | C1 の取りこぼしは隣接カテゴリ取り違え | 下記の「評価方法論」参照。**汚染を除くと robust な信号は消滅** | 出荷せず（#384 クローズ） |

**共通の教訓**: C1 のトピックは検索由来のどの補強より高精度で、**低精度シグナルを足すと必ず悪化する**。
系統②の算法的改善は「補強」では実らない。真のレバーは C1 自体の精度だが、それも下記の通り
プロンプトでは robust に動かせなかった。

## 評価方法論（C1 few-shot の3ラウンド検証が残した教訓）

C1 few-shot は当初 eval 上 topic acc@1 0.721→0.868 / Hit@3 0.712→0.803 と大きく見えたが、
厳密化するたびにゲインが縮み、最終的に robust な改善は残らなかった。潰した汚染は3種:

1. **train-on-test leakage**: few-shot 例を eval の取りこぼし質問から設計し、同じ eval で測ると、
   例が質問の語彙・gold を反響して暗記を測る。→ few-shot を **定義ベース**（買う側/売る側・
   契約文書/営業・社外/社内・運用/開発の taxonomy 区別）へ書き直し、シナリオ反響を除去。
2. **held-out 側の語彙汚染**: 汎化を測る held-out 質問に few-shot の定義語（例「商談」「覚書」）が
   混じると、lift が数問のときその数問だけで説明でき、概念汎化と区別できない。→ few-shot 語彙と
   **重複ゼロ**の held-out（n=20・婉曲な新規シナリオ）で測り直し。
3. **LLM run 分散**: 同一 prompt・同一質問でも C1 topic acc@1 は run 間で ±0.09 振れる。
   単発の ±0.02 差は無意味。→ 3 run 反復で判定。

**結果**: 語彙重複ゼロの clean held-out（n=20・3 run）で acc@1 はフラット（0.8→0.8/0.85/0.8）、
acc@3 は弱く不安定（0.8→0.9/0.9/0.8）。**見かけの勝ちは leakage＋held-out 汚染＋LLM 分散の合成**
だった。**プロンプトで有効化提案する前に、オラクル採点・train-on-test・held-out 語彙汚染・run 分散を
反証してから判断する**。

## 決定

1. **系統②の算法的改善レバー（query_expansion / union 採点 / C1 few-shot）は打ち止め**。
   `query_expansion_enabled` は既定 OFF を維持（有効化＝経路破壊）。union 採点・C1 few-shot は
   コードを出荷しない。**実 Hit@3 ≈0.72–0.78 は、このコーパス/C1/gold の実力天井**であり、
   算法では robust に超えられない（オラクル 0.9355 は gold topics を与えた別物の上限）。
   この「80 点天井」は RAG の一般論とも一致する（社内資料 *RAG に関する知見*：システムが答えに使える
   情報は人間が使う情報のごく一部＝暗黙知が入らないため、PoC 実装は 80 点程度が限界）。
   → [rag-improvement-directions.md](../specs/rag-improvement-directions.md)。
2. **系統②の以後の改善は算法でなくデータ/gold 側へ**: CRM eval 拡充・多面 gold の見直し・
   多源知識抽出（チャット履歴等）。ADR-0006（証拠カバレッジ天井）・ADR-0007（経路 recall 天井）と
   同型の**グラウンドトゥルースの緊張**が最終律速。
3. **self_answer（#291）は有効化済（#382）** ＝ ADR-0007 の「`self_answer_enabled` OFF 維持」を更新する。
   full-graph E2E 検証で、self_answer は C5 の後・データ由来経路でのみ発火し **person routing recall
   1.000 を不変に保ちつつ**（構造的に person 質問を奪わない）、データ 23 行で出典付き回答
   （source recall 0.239・precision 0.739・grounded 0.261＝保守的・低ハルシネーション）を返す。
   ADR-0007 の「#291 有効化の前提は #357 知識層」は「reach（発火母数）を増やすには #357 が要る」の
   意味に限定される。発火母数は C5 のデータ経路 recall（#327・11/23）が上限で、それを増やすのは
   引き続き #357 の仕事。self_answer 自体は現状の reach の範囲で安全に有効化した。

## 影響

- `research_fullgraph_eval.py`（#381・実グラフ E2E 評価の基盤）を恒久ハーネスとして残す。
  以後のフラグ有効化判断は**オラクルでなくこれで行う**。コーパス/gold/C1 が変わったら測り直す。
- config フラグの現況を明文化: `query_expansion_enabled` = OFF（有効化不可）、`self_answer_enabled`
  = ON、`c1_fewshot_enabled` は**追加しない**（#384 クローズ）。
- 評価データの基準を更新: eval_person.json は **87 行**（person49 / data23 / abstain15・|gold|=4 が 27）。
  「分母なしの数字」「オラクル topics 採点値」「単発 run の ±0.02 差」は判断根拠にしない。
- C1 の topic 予測は同一入力でも run 間 ±0.09 振れる（`llm_temperature=0` でも vLLM のバッチ非決定性）。
  C1 品質を測るときは複数 run で。
- 指標の定義と読み方は [eval-metrics.md](../benchmarks/eval-metrics.md) に集約した。天井を踏まえた
  この先の伸ばし方（暗黙的情報の付与・多源抽出・系統③蓄積）は
  [rag-improvement-directions.md](../specs/rag-improvement-directions.md) にまとめた。
