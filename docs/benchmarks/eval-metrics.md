# モデルの評価指標（eval-metrics）

TEKIJIN のエージェントを何で測るか、その数字をどう読むか、を1枚にまとめる。
実装は `backend/src/tekijin/eval/metrics.py`、評価セットは
`fixtures/synthetic/eval/eval_person.json`（**全 87 行**：person 49 / data 23 / abstain 15、
`|gold|=4` の多面 gold が 27 行）。

> **最重要の前提（RAG の一般論とも一致）**: システムが答えに使える情報は、人間が使う情報の
> ごく一部（明文化された文書＋α）に過ぎない。暗黙知・社内文脈・関係性はほとんど入らない。
> そのため **PoC レベルの検索応答は「80 点くらい」が実力天井**になりやすい。TEKIJIN でも
> 実 E2E の Hit@3 は **≈0.72–0.78** で頭打ちで、これは算法でなくデータ/gold 側の天井である
> （[ADR-0008](../adr/0008-system2-hit3-ceiling-and-augmentation-negatives.md)）。
> **指標を読むときは常にこの天井を念頭に置く。**

## 1. 指標の定義

3 系統（①知識で答える / ②人に取次ぐ / ③蓄積）のうち、②の推薦品質と①の回答品質を測る。

### 系統②（取次ぎ）＝ 推薦ランキング

| 指標 | 定義 | 実装 | 目標 |
|---|---|---|---|
| **Hit@3**（プロダクト真指標） | top3 に有効専門家が **1 人以上**いるか（＝取次ぎ先に辿り着けたか） | `hit_at_k(r, 3)` | 高いほど良い |
| Recall@3（補助） | `|top3 ∩ gold| / min(3, |gold|)`（分数被覆） | `recall_at_k(r, 3)` | 0.90 |
| Top-1 Accuracy | 1 位が gold か | `top1_hit` | 0.70 |
| MRR | 最初の正解の逆順位 | `reciprocal_rank` | 0.75 |
| Route Accuracy | C5 の経路（person/prior_answer/document）が gold_route と一致 | `route_hit` | 0.80 |

> **Hit@3 と Recall@3 の使い分け**: `|gold|=4` の行が eval の約 40% を占める。Recall@3 は分母が
> `min(3,|gold|)=3` なので多面 gold 行を構造的に過小評価する（4 人中 2 人拾っても 0.67）。
> 「取次ぎが成功したか（誰か有効な人に届いたか）」を測るなら **Hit@3 が製品真指標**。R@3 は
> ファセット被覆の補助として見る。

### 系統①（自己回答 #291）＝ 回答の接地と出典

| 指標 | 定義 | 実装 |
|---|---|---|
| decision recall | self_answer / route / abstain の各クラスを C5 が正しく振り分けた率（macro） | `evaluate_decisions` |
| source recall | 引用義務のある行（data 由来・gold_source あり）で gold_source を引用できた率（取りこぼさない率） | `source_recall` |
| source precision | 引用したうち gold だった率（ハルシネーション検知） | `source_precision` |
| grounded 率 | self_answer が接地して発火した率 | `evaluate_source_recall` |

### C1（トピック予測）＝ ②の律速

| 指標 | 定義 |
|---|---|
| topic acc@1 / acc@3 | C1 が予測したトピック上位 1 / 3 件に gold トピックが入る率（`topic_hit_at_k`） |

## 2. 測り方は2モードある（混同しない）

同じ指標でも**採点に渡すトピックが違う**と別物になる。ここを取り違えると有効化判断を誤る。

| モード | トピック源 | ハーネス | 位置づけ |
|---|---|---|---|
| **オラクル（層1-2 上限）** | **gold_topics を採点器へ直接渡す** | `PipelineRanker`（`python -m tekijin.eval`）/ `research_e2e.py --task variants` | 検索＋採点だけの**上限**。C1/C5/self_answer を通らない |
| **フルグラフ E2E（実力）** | **C1 が自分で予測** | `scripts/research_fullgraph_eval.py` | build_agent の実グラフを全 87 行に流す。**フラグ有効化はこれで判断する** |

- e2e.md §0.1 の「gold トピックを C6 に渡した条件」の R@3（例 0.692/0.775）は**オラクル**。
- **現行の実測値は [eval-scores.md](eval-scores.md) にスコアカードとしてまとめてある**（下表はその要約）。
- **実 E2E の baseline（実 C1・全 87 行・real Qwen3.6 + Nemotron）**:

  | 指標 | 値 |
  |---|---|
  | Hit@3 | **0.72–0.78**（run 間で振れる。§3） |
  | Top-1 | ~0.58 |
  | RouteAccuracy | 0.833 |
  | person recall | **1.000**（49/49） |
  | C1 topic acc@1 | ~0.75（run 間 ±0.09） |
  | source recall（self_answer ON） | 0.239（precision 0.739・grounded 0.261） |

  オラクルで topics を与えると Hit@3 ≈0.9355 まで上がる。**0.9355 と実 E2E 0.72–0.78 の差は
  すべて C1 のトピック予測精度**。オラクル値を「実力」と読んではいけない。

## 3. 数字を読むときの注意（測定方法論）

[ADR-0008](../adr/0008-system2-hit3-ceiling-and-augmentation-negatives.md) の教訓を運用ルールに落とす。

1. **オラクルを実力と読まない**。gold_topics を採点器へ渡した値・検索 recall 近似は上限であって
   製品値ではない。有効化判断は必ずフルグラフ E2E（`research_fullgraph_eval.py`）で。
2. **LLM の run 分散が大きい**。C1 は同一 prompt・同一質問でも topic acc@1 が **run 間で ±0.09**
   振れる（`llm_temperature=0` でも vLLM のバッチ非決定性）。**単発の ±0.02 差は無意味**。
   小さな効果は複数 run で判定する。
3. **train-on-test を疑う**。eval の取りこぼしを見てプロンプト/few-shot を設計し、同じ eval で測ると
   暗記を測る（#384 で実際に踏んだ）。プロンプト改善は **held-out**（eval と disjoint な新規質問）で
   汎化を確認する。
4. **held-out 側の語彙汚染も疑う**。few-shot の定義語が held-out 質問に混じると、少数問の lift を
   概念汎化と誤認する。held-out は改善側の語彙と**重複させない**。
5. **分母を明示する**。「Hit@3 0.8」だけでは意味を持たない。何行・どのモード・何 run かを併記する。
6. **補強はまず疑う**。C1 の高精度トピックに低精度シグナル（検索投票トピック等）を足すと、被覆が
   上がっても**ランキングは悪化**しうる（#380 union）。被覆 ≠ 品質。

## 4. 天井の内訳と、この先の伸ばし方

実 E2E Hit@3 ≈0.72–0.78 の内訳（[ADR-0006](../adr/0006-c6-scorer-at-evidence-coverage-ceiling.md) /
[ADR-0007](../adr/0007-c5-data-route-recall-ceiling.md) / ADR-0008）:

- **C1 トピック予測**（acc@1 ~0.75）が実 E2E の直接の律速。ただしプロンプト/few-shot では
  robust に動かせなかった（#384）。
- その上のオラクル天井 0.9355 は **gold の多面性（`|gold|=4`）とコーパスの証拠カバレッジ**で決まる。
- **算法（クエリ拡張・union 採点・C1 few-shot）はいずれも実 E2E を robust に超えられなかった**。

→ **この先の改善は算法でなくデータ/gold 側**（[rag-improvement-directions.md](../specs/rag-improvement-directions.md)）：
暗黙的情報（メタデータ・関係性・スレッド文脈・用語集）の付与、多源知識抽出、gold の見直し、
そして系統③（蓄積）で自己回答の母集団そのものを育てる。**評価項目を設計するときは、80 点天井を
前提に「どこまでを人に委ね、どこを自動化するか」を決める**（RAG の "G 不要論"＝検索/取次ぎだけを
提供するのも正当な選択）。
