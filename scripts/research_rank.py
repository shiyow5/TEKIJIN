#!/usr/bin/env python3
"""research_rank.py — アブレーション（#65）で使うランキング部品。

**測定ハーネスであって製品コードではない。** 製品の C4/C6 とは独立に、
「どの構成が効くか」だけを切り分けるための最小実装を置く。

参照した定式化:
  - 文書中心 / 人物中心の2モデル: Balog et al., "Formal Models for Expert Finding in
    Enterprise Corpora", SIGIR 2006
  - 順位融合 RRF: Cormack et al., "Reciprocal Rank Fusion outperforms Condorcet and
    individual Rank Learning Methods", SIGIR 2009
  - 伝播: Klicpera et al., "Predict then Propagate (APPNP)", ICLR 2019 の
    personalized PageRank 伝播を、学習なしのスコア平滑化として使う
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

RRF_K = 60


# --------------------------------------------------------------------------- #
# 疎検索（BM25）
# --------------------------------------------------------------------------- #
class BM25:
    """BM25 Okapi。k1/b は Robertson らの標準値。"""

    def __init__(self, docs_tokens, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.n = len(docs_tokens)
        self.len = np.array([len(d) for d in docs_tokens], dtype=np.float32)
        self.avgdl = float(self.len.mean()) if self.n else 0.0
        self.tf = []
        df = defaultdict(int)
        for toks in docs_tokens:
            counts = defaultdict(int)
            for t in toks:
                counts[t] += 1
            self.tf.append(counts)
            for t in counts:
                df[t] += 1
        self.idf = {
            t: math.log(1.0 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def scores(self, query_tokens):
        out = np.zeros(self.n, dtype=np.float32)
        for t in query_tokens:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, counts in enumerate(self.tf):
                f = counts.get(t, 0)
                if f:
                    denom = f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avgdl)
                    out[i] += idf * f * (self.k1 + 1) / denom
        return out


def tokenize_factory():
    """SudachiPy mode C。製品側（#29 の sparse.py）と同じ分割単位に合わせる。"""
    from sudachipy import dictionary, tokenizer

    tok = dictionary.Dictionary(dict="core").create()
    mode = tokenizer.Tokenizer.SplitMode.C
    stop = set("のにはをがでとてもだしただですますあるいるするされるこれそれこのその")

    def run(text):
        return [
            m.dictionary_form()
            for m in tok.tokenize(text, mode)
            if m.part_of_speech()[0] in ("名詞", "動詞", "形容詞")
            and m.dictionary_form() not in stop
        ]

    return run


# --------------------------------------------------------------------------- #
# 順位融合
# --------------------------------------------------------------------------- #
def rrf_fuse(rankings, k=RRF_K, weights=None):
    """複数の順位リストを Reciprocal Rank Fusion で1本にする。"""
    weights = weights or [1.0] * len(rankings)
    score = defaultdict(float)
    for w, ranking in zip(weights, rankings, strict=True):
        for rank, item in enumerate(ranking):
            score[item] += w / (k + rank + 1)
    return [i for i, _ in sorted(score.items(), key=lambda x: (-x[1], str(x[0])))]


def zscore_fuse(score_maps, weights=None):
    """スコアを z 正規化してから重み付き和で融合する（順位ではなく値を使う融合）。"""
    weights = weights or [1.0] * len(score_maps)
    total = defaultdict(float)
    for w, sm in zip(weights, score_maps, strict=True):
        if not sm:
            continue
        vals = np.array(list(sm.values()), dtype=np.float32)
        mu, sd = float(vals.mean()), float(vals.std()) or 1.0
        for key, v in sm.items():
            total[key] += w * (v - mu) / sd
    return total


# --------------------------------------------------------------------------- #
# チャンク → 人への集約（Balog Model 2 の実装バリエーション）
# --------------------------------------------------------------------------- #
def aggregate_people(
    ranked_ids,
    owners,
    source_weight,
    top_n=20,
    pooling="rank_sum",
    sims=None,
    count_norm=0.0,
):
    """上位チャンクを人に畳む。

    pooling:
      rank_sum … Σ 1/(RRF_K+rank+1) × 種別重み（既存の参照スコアラーと同じ）
      max      … その人の最良チャンク1件だけを見る（多作な人の水増しを止める）
      score_sum… コサイン類似度そのものを足す
      top3_sum … その人の上位3チャンクだけ足す（max と rank_sum の中間）
    count_norm: 証拠件数 c で割る度合い。score / c**count_norm（1.0 で平均、0.0 で総和）
    """
    per_person = defaultdict(list)
    for rank, cid in enumerate(ranked_ids[:top_n]):
        if pooling == "score_sum" and sims is not None:
            base = float(sims.get(cid, 0.0))
        else:
            base = 1.0 / (RRF_K + rank + 1)
        for eid, src in owners.get(cid, []):
            per_person[eid].append(base * source_weight.get(src, 0.3))

    score = {}
    for eid, vals in per_person.items():
        if pooling == "max":
            s = max(vals)
        elif pooling == "top3_sum":
            s = sum(sorted(vals, reverse=True)[:3])
        else:
            s = sum(vals)
        if count_norm:
            s /= len(vals) ** count_norm
        score[eid] = s
    return score


def to_ranking(score_map):
    return [e for e, _ in sorted(score_map.items(), key=lambda x: (-x[1], x[0]))]


# --------------------------------------------------------------------------- #
# 専門性の伝播（人-人グラフ上の平滑化）
# --------------------------------------------------------------------------- #
def build_person_graph(fx, employee_ids):
    """同じ案件に入った回数を辺の重みとする無向グラフ（行正規化した遷移行列を返す）。"""
    idx = {e: i for i, e in enumerate(employee_ids)}
    n = len(employee_ids)
    adj = np.zeros((n, n), dtype=np.float32)
    for pid, members in fx["members"].items():
        ids = [m["employee_id"] for m in members if m["employee_id"] in idx]
        for a in ids:
            for b in ids:
                if a != b:
                    adj[idx[a], idx[b]] += 1.0
    row = adj.sum(axis=1, keepdims=True)
    row[row == 0] = 1.0
    return adj / row, idx


def propagate(score_map, trans, idx, alpha=0.85, steps=2):
    """personalized PageRank 風の平滑化。alpha=1.0 で伝播なし。

    APPNP と同じ形（z ← α·z0 + (1-α)·A z）を、学習なしでスコアに適用する。
    共著（同案件）関係の近傍に少しだけ信用を配る。
    """
    z0 = np.zeros(len(idx), dtype=np.float32)
    for eid, v in score_map.items():
        if eid in idx:
            z0[idx[eid]] = v
    if z0.sum() == 0:
        return dict(score_map)
    z = z0.copy()
    for _ in range(steps):
        z = alpha * z0 + (1.0 - alpha) * (trans.T @ z)
    return {eid: float(z[i]) for eid, i in idx.items()}
