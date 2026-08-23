# 精度改善のアブレーション（Issue #65 / #73 で再測定）

モデル選定（[README.md](README.md) / #61）で決めた構成を**固定したまま**、
アーキテクチャ側の改善手法を横並びで測った記録。実施 2026-08-22、DGX Spark（GB10）実機。
**数字はすべて #158 まで拡張した評価セット（全81件 / 採点対象66件 / L4 15件）で測り直したもの。**

- 埋め込み: Nemotron-3-Embed-1B-BF16（実測1位）
- LLM: Qwen3.6-35B-A3B-NVFP4（vLLM、`--reasoning-parser qwen3`、guided decoding、temperature=0）
- 評価: `fixtures/synthetic/eval/eval_person.json` の **66件**（L4 を除く）／棄却は L4 **15件**
- 主指標: 層2 **Recall@3** = `|pred[:3] ∩ gold| / min(3, |gold|)`（`bench_embeddings.py` と同一）

## 1. 結論

<!-- gen:conclusion -->
|  | 層2 R@3 | MRR |
|---|---|---|
| 現行（Dense 検索 → チャンクを人へ集約） | 0.553 | 0.647 |
| **トピック媒介（LLM分類 → 構造化スコアラー）** | **0.712 – 0.727** | 0.800 |
<!-- /gen:conclusion -->

**分割検証**（採点対象を半分に割り、片方で構成を選び、もう片方で測る／200回）:

<!-- gen:holdout -->
| 平均 | 中央値 | 5–95%tile | 改善した割合 |
|---|---|---|---|
| +0.127 | +0.136 | +0.000 – +0.203 | 0.95 |
<!-- /gen:holdout -->

全件で最良構成を選んだとき（§4 の「C6 の完全な式」）の Δ は多重比較で楽観的なので、
採用判断は**この分割検証の値**で行う。

**律速はトピック推定だった。** 検索結果からトピックを推定すると acc@1=0.530 だが、
LLM に検索文脈を渡して分類させると **0.848** まで上がる。伸びしろの大半はこの段にある。

## 2. 何が効いて、何が効かなかったか

### 効いた

<!-- gen:headline_win -->
| 手法 | 効果 | 備考 |
|---|---|---|
| **トピック媒介の構造化スコアリング** | **+0.159 [+0.073,+0.245]** | 下記 §4 |
| Query2doc（クエリ+専門語） | +0.101 [+0.023,+0.182] | **#158 で L3 を増やして有意になった**（n=56 では +0.036 [-0.065,+0.146]） |
| 日報をコーパスに入れる | +0.023 [-0.018,+0.071] | 向きは同じだが、#84 の再測定で有意でなくなった |
<!-- /gen:headline_win -->

* 検索文脈つきの LLM 分類（RAG型分類）… 段A acc@1 は下記 §4 の表を見ること（+0.06s）
* 棄却は「証拠の十分性」を別段で判定 … §6

### 効かなかった（測って捨てたもの。ここが本文の価値）

<!-- gen:headline_lose -->
| 手法 | 効果 | なぜ効かないか |
|---|---|---|
| Dense + BM25 の RRF（**旧 C4 の構成**） | **-0.134 [-0.215,-0.058]** | §3 |
| BM25 単独 | -0.288 [-0.386,-0.189] | 症状語しか出ないクエリと語彙が合わない |
| クロスエンコーダのリランカー（bge-reranker-v2-m3） | -0.010 [-0.091,+0.073] | §5。+0.63s/クエリ |
| クロスエンコーダのリランカー（Qwen3-Reranker-0.6B） | -0.045 [-0.129,+0.038] | 同上。+0.69s/クエリ |
| HyDE（仮想文書をクエリに連結） | **-0.035 [-0.129,+0.058]** | 段Aには効くが、人への集約では落ちる |
| HyDE（仮想文書だけで引く） | -0.139 [-0.247,-0.030] | 同上。置き換えるとさらに落ちる |
| 埋め込み2本のアンサンブル | -0.025 [-0.098,+0.048] |  |
| 人物中心の索引（Balog Model 1） | -0.005 [-0.088,+0.076] | Balog 2006 の Model 2 優位は再現しなかった |
<!-- /gen:headline_lose -->

* LLM listwise リランク（RankGPT型）… 弱い候補集合には効くが、良い候補集合では動かない（§4）
* 自己整合性（5サンプル多数決）… 段A は上がるが段Cは同値。5倍のコストに見合わない（§4）
* 集約関数の変更（max / top3和 / 件数正規化）… 現行の順位重み和が既に妥当
* 案件共起グラフでの伝播（APPNP型）… **45件のときの +0.011 は noise だった**（訂正）
* 「該当なし」を分類の選択肢に足す … LLM は範囲外でも必ず分野を選ぶ。§6

## 3. Dense+BM25 のハイブリッドは、この課題では悪化する

現行の C4（#29）は Dense + BM25 + RRF(k=60) を等重みで融合している。**測ると悪化する。**

> **更新（#68）**: 下表は harness の BM25 を **production（`retrieval/sparse.py`）と同じ
> 語彙オーバーラップ・フィルタ**に揃えて測り直したもの。以前は BM25 がスコア上位を無条件に
> 返し、語彙の重ならないノイズ文書まで融合していた（等重みの悪化を過大に見せうる）。
> **フィルタを揃えても結論は不変**（等重み -0.128／BM25 0.2 で -0.036）。日本語は機能語の
> オーバーラップが広く、production でも BM25 の弱い順位が等重み RRF を汚すため。

> **#84 で評価クエリ8件の文面を変えたため 2026-08-23 に測り直した。**
> 基準（Dense のみ）は **0.562**（#158 で制約つきを15件にした後の再測定）。
> **以前あった「§1・§4 とのスナップショット差」は解消し、全節で同じ基準に揃っている。**

<!-- gen:bm25 -->
| 構成 | R@3 | Δ | 95%CI |
|---|---|---|---|
| Dense のみ | 0.553 | — |  |
| Dense + BM25 RRF（等重み＝旧C4） | 0.419 | **-0.134** | [-0.215,-0.058] |
| BM25 重み 0.5 | 0.490 | -0.063 | [-0.144,+0.018] |
| BM25 重み 0.2 | 0.518 | -0.035 | [-0.096,+0.023] |
| BM25 重み 0.1 | 0.528 | -0.025 | [-0.076,+0.018] |
| BM25 のみ | 0.265 | -0.288 | [-0.386,-0.189] |
<!-- /gen:bm25 -->

評価セット v2 は**クエリにトピック語を書かない**（症状で書く）設計なので、
語彙一致に依存する BM25 は原理的に不利になる。等重み RRF はその弱い順位を
Dense と同格に扱うため、上位20チャンクが汚染される。

実運用で製品名や型番を含む相談は BM25 が効くはずなので「BM25 を捨てろ」ではなく、
**重みを 0.1〜0.2 に落とす**か、クエリに固有名詞が含まれるときだけ有効化するのが妥当。

## 4. トピック媒介パイプライン

```
query
 └→ Dense 検索（Nemotron, 上位8件）           …数ms
     └→ LLM 分類（検索断片を文脈に、guided JSON で3候補）  …p50 0.64s
         └→ 構造化スコアラー（資格・スキル・案件・回答の base_score 和）  …数ms
             └→ 上位3名
```

段A（トピック的中）:

<!-- gen:stage_a -->
| 手法 | acc@1 | acc@3 |
|---|---|---|
| 検索由来のみ | 0.530 | 0.652 |
| LLM(文脈なし) | 0.742 | 0.864 |
| **LLM(検索文脈つき)** | **0.848** | 0.909 |
| LLM(該当なし選択肢つき) | 0.803 | 0.909 |
| LLM(自己整合性5回) | 0.833 | 0.909 |
| LLM(文脈つき)+検索由来(専門語) | 0.803 | 0.909 |
<!-- /gen:stage_a -->

段C（層2 R@3）:

<!-- gen:stage_c -->
| 構成 | R@3 | Δ | 95%CI | P(Δ>0) |
|---|---|---|---|---|
| 基準（Dense 集約） | 0.553 | — |  |  |
| 検索由来のみ → 構造化 | 0.470 | -0.083 | [-0.167,-0.008] | 0.02 |
| LLM(文脈なし) → 構造化 | 0.657 | +0.104 | [-0.010,+0.217] | 0.96 |
| **LLM(検索文脈つき) → 構造化** | **0.712** | +0.159 | [+0.073,+0.245] | 1.00 |
| LLM(該当なし選択肢つき) → 構造化 | 0.692 | +0.139 | [+0.058,+0.222] | 1.00 |
| LLM(自己整合性5回) → 構造化 | 0.712 | +0.159 | [+0.073,+0.245] | 1.00 |
| LLM(文脈つき)+検索由来 → 構造化 | 0.614 | +0.061 | [-0.015,+0.139] | 0.94 |
| LLM(文脈つき)+検索由来(専門語) → 構造化 | 0.697 | +0.144 | [+0.056,+0.235] | 1.00 |
| 検索由来(クエリ+HyDE) → 構造化 | 0.576 | +0.023 | [-0.078,+0.129] | 0.67 |
| LLM(文脈つき)+検索由来 → 構造化 → listwiseリランクRRF | 0.720 | +0.167 | [+0.083,+0.253] | 1.00 |
<!-- /gen:stage_c -->

### L3（複合条件）はトピックを増やしても直らない

L3 20件は gold トピックが2つある「2分野にまたがる相談」で、難易度別 R@3 も最も低い。
**トピックを2つ以上使う形に変えても改善しない**（表は [robustness.md](robustness.md) §2 と同じもの）。

<!-- gen:l3_fusion -->
| 構成 | 全体 | L1 | L2 | **L3** |
|---|---|---|---|---|
| **上位1 / weighted_sum（現状）** | **0.712** | 0.93 | 0.73 | **0.57** |
| 上位2 / weighted_sum | 0.601 | 0.63 | 0.64 | 0.52 |
| 上位2 / znorm_max | 0.558 | 0.63 | 0.56 | 0.52 |
| 上位2 / round_robin | 0.593 | 0.73 | 0.60 | 0.52 |
| 上位2 / union_top2 | 0.538 | 0.57 | 0.54 | 0.52 |
| 上位3 / round_robin | 0.503 | 0.50 | 0.48 | 0.55 |
<!-- /gen:l3_fusion -->

LLM の上位2トピックは L3 の gold トピックを覆いきれていないが、**覆えても順位重み和で薄まって悪化する**。
クエリ分解は少なくともこの形（トピックを足して和を取る）では効かない。L3 は未解決の課題として残す。

### 循環していないかの確認

gold は `projects` + `daily_reports` のトピック証拠から機械的に作られているので、
`projects` を使う構造化スコアラーは gold の作り方と一部重なる。**正解の導出経路と、
スコアラーが使う証拠を食い違わせて**確かめた（#73 で足した `gold_experts_alt` を使う）。

<!-- gen:circularity -->
| 正解 | スコアラーが使う証拠 | R@3 | Δ（基準比） | 95%CI |
|---|---|---|---|---|
| 主 gold（projects+daily 由来） | 全証拠 | 0.712 | +0.159 | [+0.073,+0.245] |
| 第2の正解（answers 由来） | 全証拠 | 0.836 | +0.252 | [+0.155,+0.352] |
<!-- /gen:circularity -->

**正解の作り方を変えても、トピック媒介の改善は残る。** 第2の正解（`answers` だけから
導出したもの）で採点すると、むしろ差は大きくなる。

### 再現できない旧測定（証拠を落とした版）

証拠を1種類ずつ落として測った下の4行は、**出したスクリプトが残っておらず再計算できない**。
基準も採点対象も当時のまま（主 gold n=56 / 第2の正解 n=45）なので、
**上の表と同じ表に並べない**。傾向を読むだけに使うこと。

| 正解 | スコアラーが使う証拠 | R@3 | Δ（基準比） | 95%CI |
|---|---|---|---|---|
| 主 gold（n=56） | **案件を使わない**（経路が交わらない） | 0.699 | **+0.107** | [+0.003,+0.191] ※ |
| 主 gold（n=56） | 回答を使わない | 0.509 | -0.083 | [-0.223,+0.033] ※ |
| 第2の正解（n=45） | 案件を使わない | 0.867 | +0.252 | [+0.152,+0.359] |
| 第2の正解（n=45） | **回答を使わない**（経路が交わらない） | 0.493 | -0.122 | [-0.270,+0.030] |

> ⚠️ **※印の 95%CI は基準 0.601 時代のもの。** Δ 列は「絶対値 − 基準」で計算し直してあるが、
> **区間はペア・ブートストラップの出力**なので基準の差だけ平行移動しても求まらない。
> Δ と区間で基準が食い違っている点に注意。
>
> ⚠️ **「第2の正解（n=45）」2行の Δ の基準**は n=45 部分集合ごとの Dense 単体スコアで、
> その値を保存したファイルが見当たらない。**#84 で文面を変えた8件（id 11〜18）のうち5件
> （id 11, 14, 16, 17, 18）はこの n=45 に入る**ので、基準がわずかに動いている可能性がある。

読み方は2つある。

1. **循環ではなさそうだが、有意性は #84 後に確かめ直せていない。** 主 gold（projects+daily 由来）
   に対して、**案件をまったく見ない**スコアラーでも **+0.107** と基準を上回る。向きは変わっていない。
   ただし **95%CI（※印）は #84 前の基準 0.601 で測ったもので、測り直していない**（下記）。
   旧基準では [+0.003,+0.191] とかろうじて 0 を外していたが、**基準が動いた今も
   0 を外しているとは言えない。** 「有意に勝つ」と読まないこと
2. **ただし効いているのは実質 `answers` の証拠である。** 回答を外すと、どちらの正解に対しても
   基準を下回る（0.509 / 0.493）。トピック媒介が強いのは「トピックで回答履歴を引ける」からで、
   資格・スキル・案件だけでは足りない。**回答ログが薄い立ち上げ期には、この改善は出ない**と読むべき

### ラベルの出所で割っても向きは同じ

| 群 | n | 基準（Dense 集約） | トピック媒介 |
|---|---|---|---|
| 人手ラベル由来（PR #46。私の導出経路と独立） | 21 | 0.556 | **0.730** |
| 自動ラベル・著述 | 35 | 0.629 | 0.776 |

## 5. リランカーが効かない理由

bge-reranker-v2-m3 の並べ替え後、上位20チャンクの種別構成はほぼ変わらない
（人に紐づくチャンクの比率 0.953 → 0.926、`profile` が 0.414 → 0.346 に減って
`doc` が 0.047 → 0.074 に増える程度）。構成が壊れたのではなく、**順序そのものが
この課題では bi-encoder より悪い**。汎用リランカーは「この文書はクエリに答えているか」を
測るが、ここで必要なのは「この文書は**誰が詳しいか**の証拠か」で、目的関数がずれている。

## 6. 棄却（route=none）

「該当なし」を分類の選択肢に足しても **L4 15件のうち 0件**しか棄却できなかった
（誤棄却は 0/56 なので、単に一度も選ばれない）。L4 は「社内に痕跡が無い領域」であって
「分野が存在しない」ではないため、分類器では原理的に検出できない。

**トピックではなく証拠の当たり方を別段で判定する**と機能する
（CRAG の retrieval evaluator / Self-RAG の critique に相当）。
候補上位3名の実績サマリを見せて `confidence` 0–100 を出させた:

| しきい値 | L4を棄却 | 誤って棄却 |
|---|---|---|
| <20 | 9/15 | 1/56 |
| **<30〜70** | **13/15** | **2/56** |
| <80 | 14/15 | 7/56 |

閾値 30〜70 で**再現率 0.87・誤棄却 2/56**。L4 が5件だった頃は 4/5・3/45 で、
閾値 30〜70 が平らな理由が「事例が少ないから」なのか「本当に平らなのか」を区別できなかったが、
15件でも同じ形なので**この範囲は実際に平ら**だと分かった。運用点は 50 を既定にしてよい。

**boolean で聞くと誤棄却が跳ねる**（45件時代の実測で 18/45）。必ず数値で聞いて閾値を外に出すこと。

## 7. レイテンシ

| 段 | p50 | p95 |
|---|---|---|
| Dense 検索（66件平均） | 2ms | — |
| LLM トピック分類（文脈つき、guided） | 0.62s | 0.69s |
| 構造化スコアリング | 1ms未満 | — |
| 棄却判定（任意、並列可） | 0.44s | — |
| クロスエンコーダのリランカー（不採用） | 0.63s / 0.69s | — |
| LLM listwise リランク（不採用） | 0.71s | 0.73s |

トピック媒介にしても追加は **+0.62s**。C1+C2 の合計 p95 1.31s（#61 実測）と足すと 1.93s。

> ⚠️ **この 1.31s は `enable_thinking=false` で測った値で、製品の設定では再現しない。**
> 製品のリクエストをそのまま流すと C1 だけで p50 14.14秒になる（[llm_faithful.md](llm_faithful.md) / #116）。
> また仕様の 1.5s / 3s は `technical-spec.md` の**初回表示（端から端まで）**の目標であって、
> 「C1+C2 で3秒」という段別の線は仕様に無い。上の足し算は内訳の目安として読むこと。

> **続き**: この結論が成り立つ前提（回答ログの量・L3・拠点制約）を [robustness.md](robustness.md)（#80）で測った。
> **回答ログが25%未満のときはトピック媒介が現行に負ける。** 採用時はそちらも読むこと。

## 8. 限界

1. **66件しかない。** ±0.09 未満の差は分離できない。本書で「効いた」と書いたのは
   分割検証で +0.136（中央値）が残った**トピック媒介という構成の選択**であって、
   その中の変種（+0.061 〜 +0.167）の優劣ではない
2. **合成データである。** 特に BM25 の不利は、症状語で書くという評価セットの設計が
   増幅している可能性がある
3. **#158 で LLM 分類を測り直した。** #84 で文面を変えた8件について「旧文面に対する予測のまま」
   という限界がここに書かれていたが、**#158 で評価セットの id が動いたのを機に
   `ablation/llm_*.json` と `ablation/c1_*.json` をすべて81件基準で取り直した**ので解消した。
   取り直しを忘れると別クエリの予測を使ってしまうので、`research_corpus.assert_llm_ids_match`
   が **id 集合**（全ファイル）と **クエリ文**（`query` を保存しているファイル）を照合して止める。
   `llm_*.json` には #158 で `query` を入れたので、件数を変えずに文面だけ差し替えた場合も捕まる
4. **段Bの評価はまだ弱い。** gold が topic 由来なので「トピックが合っていれば人も合う」構造は残る。
   #73 で足した第2の正解（answers 由来）と人手ラベル21件で挟んではいるが、
   **どちらの正解も、スコアラーが使う証拠のどれかと重なる**（§4 の表）
5. **改善の実体は `answers` の証拠**。回答ログが薄い導入初期には出ない可能性が高い

## 9. 再現手順

```bash
# 1) 埋め込みを1回だけ計算（GPUホスト。重い処理は1つずつ nohup で）
export CPATH=$HOME/.local/share/uv/python/cpython-3.12.14-linux-aarch64-gnu/include/python3.12
python scripts/research_embed_dump.py --models-dir ~/models --device cuda \
    --model Nemotron-3-Embed-1B-BF16 --out emb_Nemotron-3-Embed-1B-BF16.npz

# 2) 索引・集約・グラフのアブレーション（**GPU不要**）
python scripts/research_ablation.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
    --out docs/benchmarks/ablation/ablation_results.json

# 3) LLM を使う段（vLLM 起動が必要。出力は ablation/ に同梱済みなので通常は不要）
#    payload は backend/src が要るのでローカルで作り、ホストへ送る
python scripts/research_payloads.py --task topic_ctx --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
    --out payload_topic_ctx.json
GMU=0.60 ./serve.sh Qwen3.6-35B-A3B-NVFP4 qwen36-35b --reasoning-parser qwen3 --quantization modelopt
python scripts/research_llm.py --task topic_ctx --payload payload_topic_ctx.json --out llm_topic_ctx.json
python scripts/research_payloads.py --task abstain --emb ... --topics llm_topic_ctx.json --out payload_abstain.json
python scripts/research_llm.py --task abstain_check --payload payload_abstain.json --out llm_abstain_conf.json
python scripts/research_encode_extra.py --model Nemotron-3-Embed-1B-BF16 \
    --hyde llm_hyde.json --q2d llm_q2d.json --out emb_extra.npz

# 4) パイプラインの評価（**GPU不要**。3) の出力を読むだけ）
python scripts/research_pipeline.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
    --llm-dir docs/benchmarks/ablation --extra-emb emb/emb_extra.npz \
    --out docs/benchmarks/ablation/pipeline_results.json

# 5) 第2の正解（answers 由来）で採点し直す（循環の検査）
python scripts/research_pipeline.py --emb ... --llm-dir docs/benchmarks/ablation \
    --gold gold_experts_alt
```

`ablation/` に LLM の実出力（トピック分類・HyDE・専門語・リランク順位・棄却判定）と
リランカーの並べ替え結果を同梱してある。**GPU なしで 2) と 4) と 5) は再現できる。**

個別値は `ablation/ablation_results.json` / `ablation/pipeline_results.json`。

## 10. 参考文献

本アブレーションの各手法の出典。

- K. Balog, L. Azzopardi, M. de Rijke. *Formal Models for Expert Finding in Enterprise Corpora.* SIGIR 2006.
  — 文書中心（Model 2）と人物中心（Model 1）の2定式化。本書の §2「人物中心の索引」はこれ
- K. Balog et al. *Expertise Retrieval.* Foundations and Trends in IR, 2012. — 分野のサーベイ
- G. V. Cormack, C. L. A. Clarke, S. Buettcher. *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods.* SIGIR 2009. — RRF(k=60)
- L. Gao, X. Ma, J. Lin, J. Callan. *Precise Zero-Shot Dense Retrieval without Relevance Labels (HyDE).* ACL 2023. [arXiv:2212.10496](https://arxiv.org/abs/2212.10496)
- L. Wang, N. Yang, F. Wei. *Query2doc: Query Expansion with Large Language Models.* EMNLP 2023. [arXiv:2303.07678](https://arxiv.org/abs/2303.07678)
- W. Sun et al. *Is ChatGPT Good at Search? Investigating Large Language Models as Re-Ranking Agents (RankGPT).* EMNLP 2023. [arXiv:2304.09542](https://arxiv.org/abs/2304.09542)
- S. Jeong et al. *Adaptive-RAG: Learning to Adapt Retrieval-Augmented LLMs through Question Complexity.* NAACL 2024. [arXiv:2403.14403](https://arxiv.org/abs/2403.14403) — クエリを先に分類して経路を決める
- A. Asai et al. *Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection.* ICLR 2024. [arXiv:2310.11511](https://arxiv.org/abs/2310.11511) — 棄却・自己批評
- S.-Q. Yan et al. *Corrective Retrieval Augmented Generation (CRAG).* 2024. [arXiv:2401.15884](https://arxiv.org/abs/2401.15884) — retrieval evaluator（§6 の証拠十分性判定）
- X. Wang et al. *Self-Consistency Improves Chain of Thought Reasoning in Language Models.* ICLR 2023. [arXiv:2203.11171](https://arxiv.org/abs/2203.11171)
- J. Klicpera, A. Bojchevski, S. Günnemann. *Predict then Propagate: Graph Neural Networks meet Personalized PageRank (APPNP).* ICLR 2019. [arXiv:1810.05997](https://arxiv.org/abs/1810.05997) — §2 のグラフ伝播
- R. Nogueira, K. Cho. *Passage Re-ranking with BERT.* 2019. [arXiv:1901.04085](https://arxiv.org/abs/1901.04085) — 二段検索とクロスエンコーダ
