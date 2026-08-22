# ADR-0002: 埋め込みモデルを Nemotron-3-Embed-1B に採用（ライセンス判断を含む）

- ステータス: 承認
- 日付: 2026-08-22
- 決定者: Aチーム

## 背景

検索層 C3/C4 の埋め込みモデルを選定する。#61 / PR #60 で、DGX Spark 実機・評価セット
v2 で 5 本を横並び実測した（`docs/benchmarks/README.md`）。主指標の層2 Recall@3 は
Nemotron-3-Embed-1B が 0.615 で 1 位、次点は Qwen3-Embedding-0.6B の 0.533、現行の
`intfloat/multilingual-e5-large` は 0.530 で 3 位だった。1 位に替えると層2 R@3 が
+16%、層1 R@20 が +41%、いちばん弱い L3（複数トピック横断）が 0.267→0.383 と伸びる。

## 選択肢

- **案A: Nemotron-3-Embed-1B（2048次元）** — 精度最優先。ライセンスは NVIDIA Open
  Model License。2048 次元のため、将来 HNSW/ivfflat の ANN 索引を張るなら `halfvec`
  への移行が要る（`vector` 索引は 2000 次元上限）。
- 案B: Qwen3-Embedding-0.6B（1024次元）— Apache-2.0・スキーマ据え置きだが、精度は
  ほぼ横ばい（+0.6%）。instruction 形式プレフィックスへの分岐が要る。
- 案C: 現状維持（e5-large）— 実測 3 位。

## 決定

**案A を採用**。ただし本コンポーネント（#63）では列型を `vector(2048)` のまま据え置き、
**`halfvec` 化と HNSW 索引の導入は #101 に分離して延期**する。

理由:

- +16% の効果は**モデル（2048次元）由来**であり、`halfvec` 自体は精度に寄与しない。
  `halfvec` が必須になるのは ANN 索引を張るときだけで、現状の C4 dense 検索は総当たり
  （`retrieval/dense.py`）で索引を持たない。`vector` は最大 16000 次元まで格納でき、
  2048 次元の格納＋総当たり cosine は pgvector 0.6.2 でも動く（実地確認済み）。
- ローカルのテスト PG（`pgserver` 0.1.4）は pgvector **0.6.2** で `halfvec` 型が無い。
  今 `halfvec` にすると `create_all` がローカルで失敗し backend テストを検証できない。
  CI / docker の `pgvector/pgvector:pg16` は 0.8 系で対応する。

### ライセンス（NVIDIA Open Model License）の確認

- Nemotron-3-Embed-1B の重みは **NVIDIA Open Model License** で配布される（Apache-2.0
  ではない）。同ライセンスは商用利用を許諾するが、`https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/`
  に定める条件（帰属表示・改変物の扱い・NVIDIA の信頼性/安全性に関する条項など）に従う。
- **本リポジトリはインターン期間のプロトタイプであり、モデル重みを再配布しない**
  （実行時にホスト上でダウンロード・ローカル推論するのみ）。この用途では同ライセンスの
  範囲内と判断する。
- **本番・商用展開に進める場合は、法務でライセンス条件（特に再配布・派生物・帰属）を
  改めて確認すること。** その時点で条件が合わなければ、Apache-2.0 の Qwen3-Embedding-0.6B
  （案B、精度はほぼ同等）へ切り替える退避先がある。

## 影響

- `Settings.embedding_model = "nvidia/Nemotron-3-Embed-1B-BF16"`、`embedding_dim = 2048`。
  プレフィックスは e5 と同じ `query: ` / `passage: ` のため `embedding_use_e5_prefix` は
  True のまま。
- 全 4 埋め込み列（`employee_profiles` / `questions` / `answers` / `documents`）が
  `vector(2048)` になる。`EMBEDDING_DIM` が設定から一元化されているのでコード変更は不要。
- 既存 DB（Docker 永続ボリューム）向けに、`seed.py` の冪等 DDL が `vector(1024)` →
  `vector(2048)`（`USING NULL` で再埋め込み前提）へ広げる。モデルが変わったので旧埋め込みは
  どのみち無効。**モデル差し替え後は `make embed` の再実行が必須。**
- `halfvec` 化・HNSW 索引・pgvector バージョン固定は #101 に残す。
