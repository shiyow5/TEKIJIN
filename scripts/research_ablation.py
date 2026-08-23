#!/usr/bin/env python3
"""research_ablation.py — 精度をさらに上げる手法の横並びアブレーション（#65）。

**測定ハーネスであって製品コードではない。** 埋め込みモデルは固定し（`research_embed_dump.py`
が吐いた `.npz` を読む）、**アーキテクチャ側だけ**を差し替えて層2 Recall@3 を比べる。

指標と採点条件は `bench_embeddings.py` と同一（L4 と gold 空を除いた56件、
hit = |pred∩gold| / min(3,|gold|)）。基準線 = Nemotron の 0.601（#73 の評価セット）。

56件しかないので、**差分は対応ありブートストラップで見る**。単発の +0.02 を採用しない。

    python scripts/research_ablation.py --emb emb/emb_Nemotron-3-Embed-1B-BF16.npz
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_corpus as rc
import research_rank as rr

K_PERSON = 3
N_BOOT = 4000
SEED = 42


# --------------------------------------------------------------------------- #
# 文脈の組み立て
# --------------------------------------------------------------------------- #
def build_context(emb_paths, include_daily_default=False, gold_key="gold_experts"):
    fx = rc.load_all()
    chunks_all, owners = rc.build_chunks(fx, include_daily=True)
    n_base = len(rc.build_chunks(fx, include_daily=False)[0])
    person, _retrieval = rc.load_eval()
    items = rc.scored_person_items(person, gold_key)

    models = {}
    for path in emb_paths:
        with open(path + ".meta.json", encoding="utf-8") as f:
            meta = json.load(f)
        z = np.load(path)
        models[meta["model"]] = {
            "chunks": z["chunks"],
            "persons": z["persons"],
            "persons_full": z["persons_full"],
            "queries": z["queries"],
            "query_ids": meta["query_ids"],
        }

    tok = rr.tokenize_factory()
    chunk_tokens = [tok(t) for _, t in chunks_all]
    bm25_base = rr.BM25(chunk_tokens[:n_base])
    bm25_all = rr.BM25(chunk_tokens)
    # Per-chunk token SETS for the production-aligned lexical-overlap filter
    # (see bm25_chunk_rank). Mirrors backend/.../retrieval/sparse.py, which only
    # surfaces chunks sharing >=1 token with the query.
    chunk_token_sets = [set(t) for t in chunk_tokens]

    employee_ids = [e["id"] for e in fx["employees"]]
    trans, pidx = rr.build_person_graph(fx, employee_ids)

    qid_pos = {qid: i for i, qid in enumerate(next(iter(models.values()))["query_ids"])}
    return {
        "fx": fx,
        "chunk_ids": [c for c, _ in chunks_all],
        "owners": owners,
        "n_base": n_base,
        "items": items,
        "models": models,
        "tok": tok,
        "bm25": {"base": bm25_base, "all": bm25_all},
        "chunk_token_sets": {"all": chunk_token_sets, "base": chunk_token_sets[:n_base]},
        "person_ids": employee_ids,  # build_person_docs と同じ並び（fx["employees"] 順）
        "trans": trans,
        "pidx": pidx,
        "qid_pos": qid_pos,
        "include_daily_default": include_daily_default,
        "gold_key": gold_key,
    }


# --------------------------------------------------------------------------- #
# ランキング（1クエリぶん）
# --------------------------------------------------------------------------- #
def dense_chunk_rank(ctx, model, qi, include_daily, depth=64):
    m = ctx["models"][model]
    n = len(ctx["chunk_ids"]) if include_daily else ctx["n_base"]
    sims = m["queries"][qi] @ m["chunks"][:n].T
    order = np.argsort(-sims)[:depth]
    ids = [ctx["chunk_ids"][j] for j in order]
    return ids, {ctx["chunk_ids"][j]: float(sims[j]) for j in order}


def bm25_chunk_rank(ctx, query_text, include_daily, depth=64):
    key = "all" if include_daily else "base"
    idx = ctx["bm25"][key]
    qtok = set(ctx["tok"](query_text))
    token_sets = ctx["chunk_token_sets"][key]
    s = idx.scores(ctx["tok"](query_text))
    # PRODUCTION-ALIGNED (#68): only surface chunks that share >=1 token with the
    # query (lexical overlap), exactly like retrieval/sparse.py — NOT the top-`depth`
    # by score. The old unfiltered version fused zero-overlap noise the product never
    # sees, overstating how much equal-weight RRF hurt (codex on #68).
    ids: list = []
    sims: dict = {}
    for j in np.argsort(-s):
        if not (qtok & token_sets[j]):
            continue
        cid = ctx["chunk_ids"][j]
        ids.append(cid)
        sims[cid] = float(s[j])
        if len(ids) >= depth:
            break
    return ids, sims


def person_dense_rank(ctx, model, qi, full=False):
    m = ctx["models"][model]
    mat = m["persons_full"] if full else m["persons"]
    sims = m["queries"][qi] @ mat.T
    return {ctx["person_ids"][i]: float(sims[i]) for i in range(len(sims))}


# --------------------------------------------------------------------------- #
# 指標
# --------------------------------------------------------------------------- #
def score_item(pred, gold):
    gold = set(gold)
    hit = len(set(pred[:K_PERSON]) & gold) / min(K_PERSON, len(gold))
    rr_ = 0.0
    for i, e in enumerate(pred):
        if e in gold:
            rr_ = 1.0 / (i + 1)
            break
    top1 = 1.0 if pred and pred[0] in gold else 0.0
    return hit, rr_, top1


def evaluate(ctx, system):
    """system(ctx, item, qi) -> ranked person ids。項目ごとのスコア配列を返す。"""
    hits, mrrs, top1s, by_diff = [], [], [], {}
    gold_key = ctx.get("gold_key", "gold_experts")
    for item in ctx["items"]:
        qi = ctx["qid_pos"][item["id"]]
        pred = system(ctx, item, qi)
        h, m, t = score_item(pred, item[gold_key])
        hits.append(h)
        mrrs.append(m)
        top1s.append(t)
        by_diff.setdefault(item["difficulty"], []).append(h)
    return {
        "hits": np.array(hits, dtype=np.float64),
        "R@3": float(np.mean(hits)),
        "MRR": float(np.mean(mrrs)),
        "Top1": float(np.mean(top1s)),
        **{d: float(np.mean(v)) for d, v in sorted(by_diff.items())},
    }


def paired_bootstrap(base_hits, new_hits, n=N_BOOT, seed=SEED):
    """対応ありブートストラップ。差の95%CIと、差>0 の割合を返す。"""
    rng = np.random.default_rng(seed)
    d = new_hits - base_hits
    idx = rng.integers(0, len(d), size=(n, len(d)))
    boots = d[idx].mean(axis=1)
    return (
        float(np.percentile(boots, 2.5)),
        float(np.percentile(boots, 97.5)),
        float((boots > 0).mean()),
    )


# --------------------------------------------------------------------------- #
# 構成 → システム
# --------------------------------------------------------------------------- #
DEFAULT_CFG = {
    "model": "Nemotron-3-Embed-1B-BF16",
    "include_daily": False,
    "retrieval": "dense",  # dense | bm25 | hybrid | ensemble
    "ensemble_with": "Qwen3-Embedding-0.6B",
    "bm25_weight": 1.0,
    "depth": 64,
    "top_n": 20,
    "pooling": "rank_sum",
    "count_norm": 0.0,
    "source_weight": rc.SOURCE_WEIGHT,
    "person_mix": 0.0,  # Model 1（人物中心）を混ぜる重み。z正規化スコアの係数
    "person_full": False,
    "graph_alpha": 1.0,  # 1.0 = 伝播なし
    "graph_steps": 2,
}


def make_system(**over):
    cfg = {**DEFAULT_CFG, **over}

    def system(ctx, item, qi):
        daily = cfg["include_daily"]
        if cfg["retrieval"] == "dense":
            ranked, sims = dense_chunk_rank(ctx, cfg["model"], qi, daily, cfg["depth"])
        elif cfg["retrieval"] == "bm25":
            ranked, sims = bm25_chunk_rank(ctx, item["query"], daily, cfg["depth"])
        elif cfg["retrieval"] == "hybrid":
            d, sims = dense_chunk_rank(ctx, cfg["model"], qi, daily, cfg["depth"])
            b, _ = bm25_chunk_rank(ctx, item["query"], daily, cfg["depth"])
            ranked = rr.rrf_fuse([d, b], weights=[1.0, cfg["bm25_weight"]])
        elif cfg["retrieval"] == "ensemble":
            d, sims = dense_chunk_rank(ctx, cfg["model"], qi, daily, cfg["depth"])
            e, _ = dense_chunk_rank(ctx, cfg["ensemble_with"], qi, daily, cfg["depth"])
            ranked = rr.rrf_fuse([d, e])
        else:
            raise ValueError(cfg["retrieval"])

        score = rr.aggregate_people(
            ranked,
            ctx["owners"],
            cfg["source_weight"],
            top_n=cfg["top_n"],
            pooling=cfg["pooling"],
            sims=sims,
            count_norm=cfg["count_norm"],
        )

        if cfg["person_mix"]:
            pscore = person_dense_rank(ctx, cfg["model"], qi, full=cfg["person_full"])
            fused = rr.zscore_fuse([score, pscore], weights=[1.0, cfg["person_mix"]])
            score = fused

        if cfg["graph_alpha"] < 1.0:
            score = rr.propagate(
                score, ctx["trans"], ctx["pidx"], cfg["graph_alpha"], cfg["graph_steps"]
            )
        return rr.to_ranking(score)

    return system


def person_only_system(model, full=False):
    def system(ctx, item, qi):
        return rr.to_ranking(person_dense_rank(ctx, model, qi, full=full))

    return system


# --------------------------------------------------------------------------- #
# 実験一覧
# --------------------------------------------------------------------------- #
def experiments(model):
    """(グループ, 名前, system) の列。先頭が基準線。"""
    exps = [
        ("基準", "base(dense+rank_sum,top20)", make_system(model=model)),
        # --- A. 表現・索引 ---
        (
            "A索引",
            "＋日報をコーパスに入れる",
            make_system(model=model, include_daily=True),
        ),
        ("A索引", "BM25のみ", make_system(model=model, retrieval="bm25")),
        (
            "A索引",
            "Dense+BM25 RRF(等重み=現行C4)",
            make_system(model=model, retrieval="hybrid"),
        ),
        (
            "A索引",
            "Dense+BM25 RRF(BM25重み0.5)",
            make_system(model=model, retrieval="hybrid", bm25_weight=0.5),
        ),
        (
            "A索引",
            "Dense+BM25 RRF(BM25重み0.2)",
            make_system(model=model, retrieval="hybrid", bm25_weight=0.2),
        ),
        (
            "A索引",
            "Dense+BM25 RRF(BM25重み0.1)",
            make_system(model=model, retrieval="hybrid", bm25_weight=0.1),
        ),
        (
            "A索引",
            "埋め込み2本のRRF(Nemotron+Qwen3)",
            make_system(model=model, retrieval="ensemble"),
        ),
        ("A索引", "人物中心のみ(Model 1)", person_only_system(model)),
        ("A索引", "人物中心のみ(日報込み)", person_only_system(model, full=True)),
        ("A索引", "Model2+Model1 混合 w=0.5", make_system(model=model, person_mix=0.5)),
        ("A索引", "Model2+Model1 混合 w=1.0", make_system(model=model, person_mix=1.0)),
        # --- B. 集約 ---
        ("B集約", "top_n=10", make_system(model=model, top_n=10)),
        ("B集約", "top_n=30", make_system(model=model, top_n=30)),
        ("B集約", "top_n=50", make_system(model=model, top_n=50)),
        ("B集約", "pooling=max", make_system(model=model, pooling="max")),
        ("B集約", "pooling=top3_sum", make_system(model=model, pooling="top3_sum")),
        (
            "B集約",
            "pooling=score_sum(cos)",
            make_system(model=model, pooling="score_sum"),
        ),
        ("B集約", "件数正規化 0.5", make_system(model=model, count_norm=0.5)),
        ("B集約", "件数正規化 1.0(平均)", make_system(model=model, count_norm=1.0)),
        # --- C. グラフ伝播 ---
        ("Cグラフ", "案件共起で伝播 α=0.9", make_system(model=model, graph_alpha=0.9)),
        ("Cグラフ", "案件共起で伝播 α=0.7", make_system(model=model, graph_alpha=0.7)),
        ("Cグラフ", "案件共起で伝播 α=0.5", make_system(model=model, graph_alpha=0.5)),
    ]
    return exps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", nargs="+", required=True)
    ap.add_argument("--model", default="Nemotron-3-Embed-1B-BF16")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--gold",
        default="gold_experts",
        choices=["gold_experts", "gold_experts_alt"],
        help="gold_experts_alt は answers だけから導出した第2の正解（#73）",
    )
    args = ap.parse_args()

    ctx = build_context(args.emb, gold_key=args.gold)
    print(
        f"採点対象 {len(ctx['items'])} 件 / コーパス {ctx['n_base']}(+日報 {len(ctx['chunk_ids']) - ctx['n_base']})"
    )
    print(f"埋め込み: {list(ctx['models'])}\n")

    exps = experiments(args.model)
    base = evaluate(ctx, exps[0][2])
    rows = []
    for group, name, system in exps:
        r = base if name == exps[0][1] else evaluate(ctx, system)
        lo, hi, pos = paired_bootstrap(base["hits"], r["hits"])
        rows.append((group, name, r, r["R@3"] - base["R@3"], lo, hi, pos))

    header = (
        f"{'群':6s} {'構成':34s} {'R@3':>6s} {'Δ':>7s} {'95%CI':>16s} "
        f"{'P(Δ>0)':>7s} {'MRR':>6s} {'Top1':>6s} {'L1':>5s} {'L2':>5s} {'L3':>5s}"
    )
    print(header)
    print("-" * len(header))
    for group, name, r, d, lo, hi, pos in rows:
        print(
            f"{group:6s} {name:34s} {r['R@3']:6.3f} {d:+7.3f} "
            f"[{lo:+.3f},{hi:+.3f}] {pos:7.2f} {r['MRR']:6.3f} {r['Top1']:6.3f} "
            f"{r.get('L1', float('nan')):5.2f} {r.get('L2', float('nan')):5.2f} {r.get('L3', float('nan')):5.2f}"
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "group": g,
                        "name": n,
                        "R@3": r["R@3"],
                        "delta": d,
                        "ci": [lo, hi],
                        "p_gt0": pos,
                        "MRR": r["MRR"],
                        "Top1": r["Top1"],
                        "L1": r.get("L1"),
                        "L2": r.get("L2"),
                        "L3": r.get("L3"),
                    }
                    for g, n, r, d, lo, hi, pos in rows
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n結果を書き出し: {args.out}")


if __name__ == "__main__":
    main()
