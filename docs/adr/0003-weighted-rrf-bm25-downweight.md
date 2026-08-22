# ADR-0003: C4 ハイブリッド検索を重み付き RRF にし BM25 を下げる

- ステータス: 承認
- 日付: 2026-08-22
- 決定者: Aチーム

## 背景

C4（Dense + BM25 + RRF）の融合は当初、順位のみで統合する**等重み RRF(k=60)** だった
（正規化不要が利点）。#65 / PR #67 の ablation（埋め込み Nemotron-3-Embed-1B、評価
セット eval_person.json v2、層2 Recall@3、`docs/benchmarks/ablation.md` §3）で、
**等重み RRF が Dense 単体より -0.200 悪い**ことが分かった。

| 構成 | R@3 | Δ |
| --- | --- | --- |
| Dense のみ | 0.607 | — |
| Dense + BM25 RRF（等重み＝旧 C4） | 0.407 | **-0.200** |
| BM25 重み 0.5 | 0.474 | -0.133 |
| BM25 重み 0.2 | 0.596 | -0.011 |
| BM25 重み 0.1 | 0.604 | -0.004 |
| BM25 のみ | 0.304 | -0.304 |

原因: 評価セット v2 は**クエリにトピック語を書かない**（症状で書く）設計のため、語彙一致に
依存する BM25 は原理的に不利。等重み RRF はその弱い順位を Dense と同格に扱い、上位を汚す。

## 選択肢

- 案A: **重み付き RRF**（`score(d)=Σ_r w_r/(k+rank_r(d))`）にし、BM25 を 0.1〜0.2 に下げる。
- 案B: BM25 を外し Dense のみにする。
- 案C: 等重みのまま（現状）。

## 決定

**案A を採用。** ランカー別重み付き RRF を実装し、**BM25 の既定重みを 0.2**（`bm25_weight`）とする。
Dense チャネルは 1.0。

- 0.2 は ablation で主指標をほぼ回復（-0.011、Dense 単体の 95%CI 内）しつつ、上端寄りで
  BM25 の寄与を残す選択。
- **BM25 は外さない（案B 却下）**: 実運用の相談には製品名・型番・エラーコード（例「RX-3000」）が
  入り、そこは語彙一致＝BM25 が効く。評価セット v2 だけで「消す」判断はできない。
- `bm25_weight` は設定化し、`Settings`→`build_default_service`→`AgentService`→`build_agent`→
  `HybridRetriever` に配線（注入 Settings を尊重）。`0.0` で BM25 を完全無効化できる。

## 影響

- `retrieval/fusion.py` の `rrf(..., weights=)`、`retrieval/retriever.py` の `_fuse` が
  dense/sparse を分離して重み付け。仕様書（technical-spec §3.4 / model-definition C4）も更新。
- **残るトレードオフ**: 固定の低重みは、Dense が無情報な型番クエリ（BM25 のみが正解を指す）で
  回収を弱める。評価セット(症状語)と型番クエリで最適重みが相反するため、**Dense 信号強度に応じた
  適応重み**で両立させるのは #114 に切り出す。
- 絶対値の再測定は DGX 実埋め込みでの `scripts/research_ablation.py`（#65）による。
