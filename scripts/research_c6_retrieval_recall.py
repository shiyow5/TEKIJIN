"""research_c6_retrieval_recall.py — R@3 の真の律速「検索recall(ファセット網羅)」を破れるか。

目視診断(research_c6_miss_inspect)の結論: 取りこぼした gold の 34/50 は "プール外"(検索失敗)で、
その大半は **質問が2トピック×2部署にまたがり、単一融合クエリが証拠の厚い側ファセットに潰れて
もう片方の部署の専門家を top_k から落とす** ため。|gold|=4 は分母 min(3,4)=3 なので、片部署2人
しか拾えないと構造的に R@3=0.67 で頭打ちになる。

そこで retrieval を次の複数構成で回し、pool-recall(gold がプールに入る率)と R@3 を比較する:
  base10/20/30 : 現行の単一クエリ・候補プール上限だけ広げる(truncation の切り分け)
  expand10/30  : クエリに gold_topics 文字列を連結して埋め込む(語彙ギャップ埋め)
  union        : gold_topics の**トピックごとに個別検索し候補プールを round-robin 合流**
                 (ファセット網羅・本命)。C1 は既に topics を抽出するので production 実装可能。

gold_topics をオラクルとして使う=「C1 が完璧に topic を出せたときの検索recall上限」。ここが伸びなければ
検索経由では 0.90 に届かない。伸びれば C1 topic のretrieval注入が正道と分かる。決定的スコアラーのみ。

使い方（DGX・prepare 済み）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      .venv/bin/python scripts/research_c6_retrieval_recall.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out c6_recall.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
from itertools import zip_longest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
NOW = dt.datetime(2026, 8, 22, 0, 0, 0)
_UNION_CAP = 15


def _round_robin(*lists):
    """Interleave lists, de-duplicating (first occurrence wins)."""
    out: list = []
    seen: set = set()
    for tup in zip_longest(*lists):
        for x in tup:
            if x is not None and x not in seen:
                seen.add(x)
                out.append(x)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="c6_recall.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.repository import Repository
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.llm.factory import make_llm_nodes
    from tekijin.retrieval.embedding import SentenceTransformerEmbedder
    from tekijin.retrieval.retriever import HybridRetriever
    from tekijin.scorer.scorer import ExpertiseScorer

    s = get_settings()
    session = get_sessionmaker(get_engine(url))()
    embedder = SentenceTransformerEmbedder(
        use_e5_prefix=s.embedding_use_e5_prefix,
        query_prefix=s.embedding_query_prefix,
        passage_prefix=s.embedding_passage_prefix,
        trust_remote_code=s.embedding_trust_remote_code,
        revision=s.embedding_model_revision,
        app_env=s.app_env,
    )
    ret10 = HybridRetriever(embedder, session, top_k=10)
    ret20 = HybridRetriever(embedder, session, top_k=20)
    ret30 = HybridRetriever(embedder, session, top_k=30)
    repo = Repository(session)
    scorer = ExpertiseScorer(repo)
    # 有効化ゲート用の本番 C1: settings が選ぶ intent model（DGX では vLLM=本番と同一）。
    # gold でなく C1 が質問から抽出するトピックでクエリ拡張したとき、実ゲインが残るか
    # （＝本番の C1 が口語質問から使えるトピックを出せるか）を測る。keyword stub は口語で
    # トピック空になるため、必ず vLLM 環境変数付きで実行すること。
    c1 = make_llm_nodes(s)[0]

    def c1_topics(q):
        try:
            return list(c1.analyze(q.query, None).topics)
        except Exception:
            return []

    queries = [q for q in load_eval_queries() if q.gold_experts and q.gold_topics]
    print(f"person-gold rows: {len(queries)}")

    def pool_for(cfg, q):
        topics = list(q.gold_topics)
        joined = " ".join(topics)
        if cfg == "base10":
            return ret10.search(q.query)["candidate_people"]
        if cfg == "base20":
            return ret20.search(q.query)["candidate_people"]
        if cfg == "base30":
            return ret30.search(q.query)["candidate_people"]
        if cfg == "expand10":
            return ret10.search(f"{q.query} {joined}")["candidate_people"]
        if cfg == "expand30":
            return ret30.search(f"{q.query} {joined}")["candidate_people"]
        if cfg == "expand10_c1":
            # 有効化ゲート: 本番 C1（settings 選択・DGXでは vLLM）が出すトピックで拡張。空なら raw。
            ct = c1_topics(q)
            query = f"{q.query} {' '.join(ct)}" if ct else q.query
            return ret10.search(query)["candidate_people"]
        if cfg == "union":
            # 元クエリ + トピックごとの検索を round-robin 合流（ファセット網羅）
            lists = [ret10.search(q.query)["candidate_people"]]
            lists += [ret10.search(t)["candidate_people"] for t in topics]
            return _round_robin(*lists)[:_UNION_CAP]
        raise ValueError(cfg)

    def facet_top3(pool, topics):
        """ファセット網羅の順位付け: トピックごとにプールを単独 topic で採点し、
        各トピックの最上位を round-robin で拾って top3 を作る（多部署質問で片ファセットが
        3枠を独占するのを防ぐ）。トピックが1つなら通常の集約採点と一致。"""
        if len(topics) <= 1:
            recs = scorer.rank(topics, pool, None, NOW, top_k=10)["recommendations"]
            return [r["person_id"] for r in recs[:3]]
        per_topic = []
        for t in topics:
            recs = scorer.rank([t], pool, None, NOW, top_k=len(pool))["recommendations"]
            per_topic.append([r["person_id"] for r in recs])
        picked: list = []
        seen: set = set()
        for tup in zip_longest(*per_topic):
            for pid in tup:
                if pid is not None and pid not in seen:
                    seen.add(pid)
                    picked.append(pid)
                    if len(picked) >= 3:
                        return picked
        return picked

    # facet-rerank は expand10 プールと union プールの両方で測る（プール構成の影響を分離）
    configs = [
        "base10",
        "base20",
        "base30",
        "expand10",
        "expand10_c1",
        "expand30",
        "union",
        "expand10_facet",
        "union_facet",
    ]
    # プロダクト真指標: R@3(分数・従来)/ Hit@3(top3 に有効専門家 ≥1)/ Top1(top1 が gold)/ pool-recall。
    r3 = {c: [] for c in configs}
    hit3 = {c: [] for c in configs}
    top1 = {c: [] for c in configs}
    poolrec = {c: [] for c in configs}

    pool_of = {
        "expand10_facet": "expand10",
        "union_facet": "union",
    }

    def ranked_ids(cfg, pool, q):
        """cfg ごとの順位付け済み person_id 列（top 先頭）。"""
        if cfg.endswith("_facet"):
            return facet_top3(pool, list(q.gold_topics))
        out = scorer.rank(q.gold_topics, pool, None, NOW, top_k=10)
        return [r["person_id"] for r in out["recommendations"]]

    for q in queries:
        gold = set(q.gold_experts)
        denom3 = min(3, len(gold))
        for cfg in configs:
            base_cfg = pool_of.get(cfg, cfg)
            pool = list(pool_for(base_cfg, q))
            poolrec[cfg].append(len(gold & set(pool)) / len(gold))
            if not pool:
                r3[cfg].append(0.0)
                hit3[cfg].append(0.0)
                top1[cfg].append(0.0)
                continue
            ranked = ranked_ids(cfg, pool, q)
            top3 = set(ranked[:3])
            r3[cfg].append(len(gold & top3) / denom3)
            hit3[cfg].append(1.0 if (gold & top3) else 0.0)
            top1[cfg].append(1.0 if (ranked and ranked[0] in gold) else 0.0)

    result = {"n": len(queries)}
    print(f"\n{'config':<14} {'R@3':>8} {'Hit@3':>8} {'Top1':>8}  {'pool-recall':>12}")
    for cfg in configs:
        r = round(statistics.mean(r3[cfg]), 4)
        h = round(statistics.mean(hit3[cfg]), 4)
        t = round(statistics.mean(top1[cfg]), 4)
        pr = round(statistics.mean(poolrec[cfg]), 4)
        result[cfg] = {"r_at_3": r, "hit_at_3": h, "top1": t, "pool_recall": pr}
        print(f"{cfg:<14} {r:>8.4f} {h:>8.4f} {t:>8.4f}  {pr:>12.4f}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
