# 精度改善のアブレーション（Issue #65）

> ⚠️ **本書の数字は評価セット拡張（#73）より前の 45件で測ったもの。**
> #73 で採点対象が 45 → 56件、L4 が 5 → 15件になったので、**測り直しが要る**。
> 結論の向き（トピック媒介が効く／等重みハイブリッドが悪化する／リランカーは効かない）は
> 効果量が大きいので変わらないと見ているが、**個別の数値をそのまま引用しないこと。**

モデル選定（[README.md](README.md) / #61）で決めた構成を**固定したまま**、
アーキテクチャ側の改善手法を横並びで測った記録。実施 2026-08-22、DGX Spark（GB10）実機。

- 埋め込み: Nemotron-3-Embed-1B-BF16（実測1位）
- LLM: Qwen3.6-35B-A3B-NVFP4（vLLM、`--reasoning-parser qwen3`、guided decoding、temperature=0）
- 評価: `fixtures/synthetic/eval/eval_person.json` の **45件**（L4と gold 空を除く）
- 主指標: 層2 **Recall@3** = `|pred[:3] ∩ gold| / min(3, |gold|)`（`bench_embeddings.py` と同一）

## 1. 結論

| | 層2 R@3 | MRR |
|---|---|---|
| 現行（Dense 検索 → チャンクを人へ集約） | 0.607 | 0.768 |
| **トピック媒介（LLM分類 → 構造化スコアラー）** | **0.752 – 0.759** | 0.825 |

**分割検証（45件を半分に割り、片方で構成を選び、もう片方で測る／200回）で
+0.114（5–95%tile: +0.007 – +0.217、95% の分割で改善）。**
全件で最良構成を選んだときの +0.152 は多重比較で楽観的なので、採用判断はこの +0.114 で行う。

**律速はトピック推定だった。** 検索結果からトピックを推定すると acc@1=0.556 だが、
LLM に検索文脈を渡して分類させると **0.800** まで上がる。gold トピックを与えた場合の
到達点は R@3=0.781 なので、伸びしろの大半はこの段にある。

## 2. 何が効いて、何が効かなかったか

### 効いた

| 手法 | 効果 | 備考 |
|---|---|---|
| **トピック媒介の構造化スコアリング** | **+0.144** [+0.041,+0.248] | 下記 §4 |
| 検索文脈つきの LLM 分類（RAG型分類） | 段A acc@1 0.756 → **0.800** | +0.06s |
| 棄却は「証拠の十分性」を別段で判定 | L4 4/5 検出・誤棄却 3/45 | §6 |

### 効かなかった（測って捨てたもの。ここが本文の価値）

| 手法 | 効果 | なぜ効かないか |
|---|---|---|
| Dense + BM25 の RRF（**現行 C4 の構成**） | **-0.200** [-0.315,-0.093] | §3 |
| BM25 単独 | -0.304 | 症状語しか出ないクエリと語彙が合わない |
| クロスエンコーダのリランカー（bge-reranker-v2-m3） | -0.048 | §5 |
| クロスエンコーダのリランカー（Qwen3-Reranker-0.6B） | -0.063 | 同上。+0.8s/クエリ |
| HyDE（仮想文書で引く） | -0.044 | 段Aには効く（acc@1 0.556→0.644）が段Cには効かない |
| LLM listwise リランク（RankGPT型） | ±0.00 | 弱い候補集合には効くが、良い候補集合では動かない |
| 自己整合性（5サンプル多数決） | ±0.00 | temperature=0 の1回と 98% 一致。5倍のコストに見合わない |
| 埋め込み2本のアンサンブル | -0.015 | |
| 人物中心の索引（Balog Model 1） | -0.015 | Model 2 優位という Balog 2006 の報告と同じ向き |
| 集約関数の変更（max / top3和 / 件数正規化） | -0.06 〜 +0.004 | 現行の順位重み和が既に妥当 |
| 案件共起グラフでの伝播（APPNP型） | +0.011 | 効果が noise に埋もれる |
| 「該当なし」を分類の選択肢に足す | L4 **0/5** | LLM は範囲外でも必ず分野を選ぶ。§6 |

## 3. Dense+BM25 のハイブリッドは、この課題では悪化する

現行の C4（#29）は Dense + BM25 + RRF(k=60) を等重みで融合している。**測ると悪化する。**

| 構成 | R@3 | Δ |
|---|---|---|
| Dense のみ | 0.607 | — |
| Dense + BM25 RRF（等重み＝現行） | 0.407 | **-0.200** [-0.315,-0.093] |
| BM25 重み 0.5 | 0.474 | -0.133 |
| BM25 重み 0.2 | 0.596 | -0.011 |
| BM25 重み 0.1 | 0.604 | -0.004 |
| BM25 のみ | 0.304 | -0.304 |

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

| 手法 | acc@1 | acc@3 |
|---|---|---|
| 検索由来のみ | 0.556 | 0.733 |
| LLM（文脈なし） | 0.756 | 0.867 |
| **LLM（検索文脈つき）** | **0.800** | 0.889 |
| LLM（自己整合性5回） | 0.800 | 0.889 |
| LLM（文脈つき）+ 検索由来(専門語) の RRF | 0.778 | 0.867 |

段C（層2 R@3）:

| 構成 | R@3 | Δ | 95%CI | P(Δ>0) |
|---|---|---|---|---|
| 基準（Dense 集約） | 0.607 | — | | |
| 検索由来トピック → 構造化 | 0.567 | -0.041 | [-0.119,+0.033] | 0.16 |
| LLM（文脈なし）→ 構造化 | 0.707 | +0.100 | [-0.037,+0.230] | 0.93 |
| **LLM（文脈つき）→ 構造化** | **0.752** | +0.144 | [+0.041,+0.248] | 1.00 |
| LLM（文脈つき）+ 検索由来(専門語) → 構造化 | 0.759 | +0.152 | [+0.044,+0.263] | 1.00 |
| 参考: gold トピック → 構造化 | 0.781 | +0.174 | [+0.063,+0.289] | 1.00 |

### L3（複合条件）はトピックを増やしても直らない

L3 10件は gold トピックが2つある「2分野にまたがる相談」で、難易度別 R@3 も最も低い（top1 で 0.53）。
**トピック数を正解で与えても改善しない。**

| 構成 | 全体 | L1 | L2 | L3 |
|---|---|---|---|---|
| LLM top1 → 構造化 | 0.752 | 0.93 | 0.77 | **0.53** |
| LLM top2 → 構造化 | 0.630 | 0.57 | 0.75 | 0.40 |
| gold の個数だけ上位を使う（オラクル） | 0.722 | 0.93 | 0.77 | **0.40** |

LLM の上位2トピックは L3 の gold トピックを 0.60 しか覆っていない（上位3で 0.75）ので、
「2つ目を当てられていない」のも事実だが、**当てても順位重み和で薄まって悪化する**。
クエリ分解は少なくともこの形（トピックを足して和を取る）では効かない。L3 は未解決の課題として残す。

### 循環していないかの確認

gold は `projects` + `daily_reports` のトピック証拠から機械的に作られているので、
`projects` を使う構造化スコアラーは gold の作り方と一部重なる。2つの対照を置いた。

- **案件を使わない構成**（資格・スキル・回答のみ）でも自動ラベル群で 0.700（基準 0.629）
- **PR #46 の人手ラベル10件**（私の導出経路と独立）で 0.533 → **0.667**

どちらでも同じ向きに動くので、改善は導出経路の重なりだけでは説明できない。
ただし人手ラベルは n=10 で、単独では結論にできない。

## 5. リランカーが効かない理由

bge-reranker-v2-m3 の並べ替え後、上位20チャンクの種別構成はほぼ変わらない
（人に紐づくチャンクの比率 0.946 → 0.914）。構成が壊れたのではなく、**順序そのものが
この課題では bi-encoder より悪い**。汎用リランカーは「この文書はクエリに答えているか」を
測るが、ここで必要なのは「この文書は**誰が詳しいか**の証拠か」で、目的関数がずれている。

## 6. 棄却（route=none）

「該当なし」を分類の選択肢に足しても **L4 5件のうち 0件**しか棄却できなかった
（誤棄却は 0/45 なので、単に一度も選ばれない）。L4 は「社内に痕跡が無い領域」であって
「分野が存在しない」ではないため、分類器では原理的に検出できない。

**トピックではなく証拠の当たり方を別段で判定する**と機能する
（CRAG の retrieval evaluator / Self-RAG の critique に相当）。
候補上位3名の実績サマリを見せて `confidence` 0–100 を出させた:

| しきい値 | L4を棄却 | 誤って棄却 |
|---|---|---|
| <20 | 2/5 | 1/45 |
| **<30〜70** | **4/5** | **3/45** |
| <80 | 5/5 | 6/45 |

confidence の分布は L4 が [10, 10, 20, 20, 75]、L1–L3 は中央値 85（5%tile 31）。
1件だけ L4 に 75 が付いており、これは「海外登記」を総務・法務の実績で答えられると
判断したもの。**boolean で聞くと誤棄却 18/45 に跳ねるので、必ず数値で聞いて閾値を持つこと。**

## 7. レイテンシ

| 段 | p50 | p95 |
|---|---|---|
| Dense 検索（45件平均） | 3ms | — |
| LLM トピック分類（文脈つき、guided） | 0.64s | 0.69s |
| 構造化スコアリング | 1ms未満 | — |
| 棄却判定（任意、並列可） | 0.44s | — |
| LLM listwise リランク（不採用） | 0.71s | 0.73s |

トピック媒介にしても追加は **+0.64s**。C1+C2 の合計 p95 1.31s（#61 実測）と足しても
合格ライン3秒に収まる。

## 8. 限界

1. **45件しかない。** ±0.10 未満の差は分離できない。本書で「効いた」と書いたのは
   分割検証で +0.114 が残った**トピック媒介という構成の選択**であって、
   その中の変種（+0.144 と +0.152）の優劣ではない
2. **合成データである。** 特に BM25 の不利は、症状語で書くという評価セットの設計が
   増幅している可能性がある
3. **段Bの評価が弱い。** gold が topic 由来なので「トピックが合っていれば人も合う」構造で、
   人の並べ替えそのものの良し悪しを測れていない。人手ラベルは n=10
4. **棄却は L4 5件でしか測っていない**。閾値 30〜70 が広く平らなのは、単に事例が少ないため

## 9. 再現手順

```bash
# 1) 埋め込みを1回だけ計算（GPUホスト）
export CPATH=$HOME/.local/share/uv/python/cpython-3.12.14-linux-aarch64-gnu/include/python3.12
python scripts/research_embed_dump.py --models-dir ~/models --device cuda \
    --model Nemotron-3-Embed-1B-BF16 --out emb_Nemotron-3-Embed-1B-BF16.npz

# 2) 索引・集約・グラフのアブレーション（GPU不要）
python scripts/research_ablation.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
    --out docs/benchmarks/ablation/ablation_results.json

# 3) LLM を使う段（vLLM 起動が必要。出力は ablation/ に同梱済み）
GMU=0.60 ./serve.sh Qwen3.6-35B-A3B-NVFP4 qwen36-35b --reasoning-parser qwen3 --quantization modelopt
python scripts/research_llm.py --task topic_ctx --payload payload_all50.json --out llm_topic_ctx.json
python scripts/research_llm.py --task abstain_check --payload payload_abstain.json --out llm_abstain_conf.json

# 4) パイプラインの評価（GPU不要。3) の出力を読むだけ）
python scripts/research_pipeline.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz \
    --llm-dir docs/benchmarks/ablation --out docs/benchmarks/ablation/pipeline_results.json
```

`ablation/` に LLM の実出力（トピック分類・HyDE・専門語・リランク順位・棄却判定）と
リランカーの並べ替え結果を同梱してある。**GPU なしで 2) と 4) は再現できる。**

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
