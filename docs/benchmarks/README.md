# `docs/benchmarks/` — モデル選定の実測結果

測定日: **2026-08-21** / 実機: `internship-dgx1`（NVIDIA GB10 / 121GB ユニファイドメモリ / aarch64 / CUDA 13.0）

詳細な読み解きは `analysis/20_モデル実測結果.md`（非gitの検討資料）にある。ここには**数字と再現方法**を置く。

> ✅ **#132 で現行 develop（Nemotron + 較正済み閾値 + #115 の RRF 重み）を実 DB で再測定した。**
> 層2 Recall@3 は **0.140 → 0.673**（56件基準）、経路精度は **0.125 → 0.821**。
> #103（全件 `prior_answer` で候補が1名に固定）は #120 で解消済み。→ **[e2e.md](e2e.md) §0**
>
> ただし **0.673 の分母には構造上0点の11件が入っている**（経路 `document` 7件 ＝
> **`gold_route` も `document` で正しい**、`gold_topics` 空 4件）。到達可能な上限は 45/56 = 0.804。
> **L1 の 0.500 は取りこぼしではなく、到達可能分を全問取っている**（e2e.md §0.2）。
>
> ⛔ **C1 の自由記述トピックが C6 の完全一致照合と噛み合わず、証拠源が4つとも空振りする**（175個中3個一致）。
> **上位3名が全クエリで同一**（47件中44件が同じ3名）。聞き返し後の2周目の C1 は測っていないので、
> **これは製品の上限値ではない**。
> **直し方は実測済み**: C1 に語彙を守らせ上位1件だけ使うと R@3 **0.788**（gold 配置 0.901 の87%、52件基準）。
> [llm_faithful.md](llm_faithful.md) §4.6 / #116。
> **#130 でこの直し方を入れた後の実 DB 実測は 0.750**（全社員候補・56件基準。gold トピックの
> 0.836 との差 0.086 が C1 の取りこぼし分）→ [e2e.md](e2e.md) §0.4。
> あわせて thinking が ON のままで **C1+C2 の p50 が 83秒**（仕様は端から端まで p50 1.5秒）。
> → **[llm_faithful.md](llm_faithful.md)**（#113 / #116）。
> [c2.md](c2.md)（#111）の結論は取り下げ済み。
>
> **続き**: モデルを固定したまま**アーキテクチャ側**で精度を上げる実験は
> [ablation.md](ablation.md)（#65）にある。層2 Recall@3 は分割検証で **+0.114** 伸びた。
> 現行 C4 の Dense+BM25 等重み RRF が **-0.170**（production 整合ハーネス・#68）と測れている点も、そちらを参照。

> ⚠️ **下表の層2 Recall@3 は評価セット拡張（#73）より前の 45件で測ったもの。**
> 採点対象が 56件になったので、埋め込みの順位は測り直しが要る（`bench_emb.json` も同様）。

> ⚠️ **`e2e.md` §1 と `route.md` の製品レベル数値（0.140 / 0.732 / 0.134 など）は
> e5-large + 旧経路閾値 + 等重み RRF の条件で、現行構成では再現しない。**
> **#132 で再測定した現行構成の値は [e2e.md](e2e.md) §0 にある。**
> 検索を通らない数値（`llm_faithful.md` §4.6、`confidence.md`）は影響を受けない
> — 実際、`confidence.md` の元データは再測定で**バイト単位で同一**だった（e2e.md §0.5）。
> **候補を全社員にする変種（0.836 / C1 実トピックの 0.750）も両構成で完全一致**する
> （`res` も `route` も読まないため）。

## 結論

| 役割 | 採用 | 根拠 |
|---|---|---|
| C3 埋め込み | **Nemotron-3-Embed-1B**（次点 Qwen3-Embedding-0.6B） | 層2 Recall@3 = 0.615（次点 0.533） |
| C1 意図理解 | **Qwen3.6-35B-A3B-NVFP4** + **構造化出力** | トピックF1 0.780 / JSON妥当率 1.000 / p50 0.52s |
| C2 充足判定 | **Qwen3.6-35B-A3B-NVFP4** + 構造化出力 | 正解率 0.900（n=20 の旧ベンチ。**製品のまま測ると母集団が長さ切れで壊れる** → [llm_faithful.md](llm_faithful.md) / #113） |
| C7 下書き | **Qwen3.6-35B-A3B-NVFP4**（#91 で決着） | 根拠違反 0/56・字数中央186・p50 1.38s。Swallow は単価と品番を捏造し、84%で依頼事項を勝手に追加 |

## ファイル

| ファイル | 中身 |
|---|---|
| `bench_emb.json` | 埋め込み5本の層1/層2スコアとレイテンシ |
| `res_swallow_off.json` | Qwen3-Swallow-30B-A3B-AWQ（thinking off、構造化出力なし） |
| `res_swallow_guided.json` | 同上 + 構造化出力 |
| `res_qwen36_guided.json` | Qwen3.6-35B-A3B-NVFP4 + 構造化出力。**C7 の下書き出力も入っている**（人手評価用） |
| `ablation/c2_qwen36_{product,scoped}.json` | C2 充足判定の生出力（#111。**結論は取り下げ済み**） |
| `ablation/{payload_,}c1_faithful.json` / `{payload_,}c2_faithful.json` | **製品のリクエストをそのまま再現した C1/C2 の入出力**。[llm_faithful.md](llm_faithful.md) |
| `ablation/e2e_variants_c1.json` | C1 の実トピックで測った層2 R@3。[llm_faithful.md](llm_faithful.md) |
| `ablation/route_nemotron.json` | **現行構成**の経路とチャネル類似度（#132）。[e2e.md](e2e.md) §0.3 |
| `ablation/e2e_variants_nemotron{,_c1both_top1}.json` | **現行構成**の層2 R@3（#132）。同 §0.1 / §0.4 |
| `ablation/misrecommendation.json` | 誤推薦の分類と、**1スロットごとの確信度素性**。[confidence.md](confidence.md)（#110） |
| `ablation/confidence_stats.json` | 確信度ラベル案ごとの差と区間（問題単位・20000回）。同上 |
| `ablation/c1_nothink.json` / `e2e_variants_c1_nothink.json` | `enable_thinking=false` を足しただけの反実仮想。同上 §4.5 |
| `ablation/{payload_,}c1_{prompt,enum,both}.json` | C1 にトピック語彙を守らせる2案の比較。同上 §4.6（#116） |
| `ablation/e2e_variants_c1_{prompt,enum,both}{,_top1}.json` と `_both_top2.json` | 上記の層2 R@3。**1問ごとの当落 `per_query` 入り** |

## 埋め込み（層2 Recall@3 が主指標）

| モデル | 次元 | 層1 R@10 | 層1 R@20 | **層2 R@3** | MRR | L1 | L2 | L3 |
|---|---|---|---|---|---|---|---|---|
| **Nemotron-3-Embed-1B** | 2048 | **0.290** | **0.475** | **0.615** | **0.768** | 0.833 | **0.620** | **0.383** |
| Qwen3-Embedding-0.6B | 1024 | 0.271 | 0.396 | 0.533 | 0.729 | 0.733 | 0.527 | 0.350 |
| multilingual-e5-large | 1024 | 0.193 | 0.336 | 0.530 | 0.667 | 0.800 | 0.527 | 0.267 |
| bge-m3 | 1024 | 0.198 | 0.314 | 0.519 | 0.651 | 0.600 | 0.547 | 0.367 |
| ruri-v3-310m | 768 | 0.174 | 0.239 | 0.515 | 0.631 | 0.767 | 0.473 | 0.367 |

ベースライン（**#74 の評価セット拡張後・56件で再実行**）:
random 0.077 / answers_count 0.327 / lexical_profile 0.173 / lexical_answers 0.116
（`scripts/eval_baselines.py`）。**旧値 0.107 / 0.393 / 0.193 は拡張前のもの。**

**Nemotron-3-Embed-1B は 2048次元。pgvector の HNSW は `vector` が2000次元上限なので `halfvec` が必須。**
ライセンスは NVIDIA Open Model License なので商用条件の確認が要る。
Apache-2.0 と 1024次元の扱いやすさを優先するなら Qwen3-Embedding-0.6B。

## LLM

| モデル | 設定 | C1 JSON | C1 F1 | C1 p50 | C1 出力tok | C2 正解率 | C2 p50 | C7 p50 | tok/s |
|---|---|---|---|---|---|---|---|---|---|
| Qwen3-Swallow-30B-A3B-AWQ | thinking off | 0.829 | 0.573 | 5.13s | 約417 | 0.600 | 4.28s | 4.88s | 81 |
| Qwen3-Swallow-30B-A3B-AWQ | + 構造化出力 | 1.000 | 0.545 | 0.56s | 44 | 0.350 | 0.62s | 4.66s | 79 |
| **Qwen3.6-35B-A3B-NVFP4** | + 構造化出力 | **1.000** | **0.780** | **0.52s** | 33 | **0.900** | **0.25s** | 1.40s | 62〜73 |

**C1+C2 合計 p95: 1.31秒**（仕様の目標は `technical-spec.md` の**初回表示 p50 1.5秒 / p95 3秒**。
段別の線は仕様に無いので、これは内訳の目安）

> ⛔ **上の表はすべて `enable_thinking=false` で測った値で、製品の設定では再現しない。**
> `_openai_model`（`llm/vllm.py`）は `chat_template_kwargs` を渡していないので thinking が ON のまま動く。
> 製品のリクエストをそのまま再現すると **C1 p50 14.14秒 / C1+C2 p50 83.15秒**、
> C1 は 76件中10件、C2 は届いた60件中29件が長さ切れになる
> （製品では SSE に `error` イベントが1つ流れて、回答が出ないまま stream が終わる）。
> → [llm_faithful.md](llm_faithful.md)（#116）。

## 実装に直結する注意（実測で分かったこと）

1. **`--reasoning-parser` を必ず付ける。** 付けないと thinking の出力が `content` に丸ごと入り、
   **JSON妥当率が 0.083 まで落ちる**。Qwen 系は `--reasoning-parser qwen3`
2. **`enable_thinking: false` だけでは足りない。** リクエストで切っても内部推論は残り、
   C1 で1回あたり約417トークン・5秒かかる
3. **構造化出力（`response_format: json_schema`）を使う。** 出力が 417→44トークン、
   **p50 が 5.13秒 → 0.56秒**。JSON妥当率も 1.000 になる
4. **ただし C2（判断が要る処理）は構造化出力で精度が落ちるモデルがある**（Swallow は 0.600→0.350）。
   Qwen3.6 は 0.900 を維持したので、**モデルごとに確認すること**
5. **AWQ-INT4 は sm_121a で動く**（vLLM が `awq_marlin` に自動変換）
6. **GPT-OSS-Swallow-120B-MXFP4（61GiB）は OOM で起動できなかった。**
   `gpu-memory-utilization` を 0.80 / 0.68 のどちらにしても、全シャードのロード完了後に kill される。
   **ユニファイドメモリ機では「モデルサイズ < 総メモリ」でも載るとは限らない。**
   ロード中はホスト全体が固まり ssh も落ちるので、共有機では慎重に

## 再現

```bash
# GPUホスト（~/tekijin-bench に fixtures/ と scripts/ を置く）
uv venv .venv
uv pip install --python .venv/bin/python "sentence-transformers>=3" torch "huggingface_hub[cli]"

# 【重要】重い処理は1つずつ nohup で流す。前景の ssh セッションで走らせると、
# 接続が切れた時点でリモート側も SIGHUP で死ぬ（2026-08-22 に実際に消えた）。
#   nohup <cmd> > run.log 2>&1 &    … として、ログを見に行く
#
# 【ssh が繋がらないとき】ping は通るのに ssh だけタイムアウトするなら、
# Tailscale SSH の再認証切れを疑う。短い timeout で殺すと下のメッセージが見えない。
#   # Tailscale SSH requires an additional check. To authenticate, visit: https://login.tailscale.com/a/...
# 長め（600秒）で1回だけ叩いて stderr を最後まで読むこと。

# Triton の JIT が Python.h を要求する。システムに python3-dev が無いので uv 管理 CPython のヘッダを見せる
export CPATH=$HOME/.local/share/uv/python/cpython-3.12.14-linux-aarch64-gnu/include/python3.12

python scripts/bench_embeddings.py --models-dir ~/models --device cuda --out bench_emb.json

GMU=0.60 ./scripts/serve_vllm.sh Qwen3.6-35B-A3B-NVFP4 qwen36-35b \
  --reasoning-parser qwen3 --quantization modelopt
python scripts/bench_llm.py --model qwen36-35b --thinking off --guided --out res.json
```
