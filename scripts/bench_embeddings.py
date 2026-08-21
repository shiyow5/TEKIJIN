#!/usr/bin/env python3
"""
bench_embeddings.py — 埋め込みモデルの横並び比較（#55 / analysis/18 §5.2）。

**測定ハーネスであって製品コードではない。** C3/C4（#29）や C6 スコアラー（#30）の実装とは独立に、
「どの埋め込みモデルを採用するか」だけを決めるために書いている。

比較の条件（analysis/18_モデル調査_GPU制約.md §5.2）:
  固定するもの … コーパス、チャンクの作り方、評価セット、k、スコアリング規則
  変えるもの   … 埋め込みモデルのみ

2層で測る（analysis/19_評価データ設計.md §3.4）:
  層1 検索   … 質問 → 正しい根拠チャンク（eval_retrieval.json）  Recall@5/10, nDCG@10
  層2 推薦   … 質問 → 正しい専門家（eval_person.json）           **Recall@3 が主指標**

層2 は参照スコアラー（doc15 の base_score 重みを RRF 風の順位重みで集約）を使う。
製品の C6 とは別物なので、**モデル間の相対比較にのみ使う**こと。

実行:
    python scripts/bench_embeddings.py --models-dir ~/models [--device cuda|cpu] [--only ruri-v3-310m]
"""

import argparse
import json
import math
import os
import time
from collections import defaultdict

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYN = os.path.join(REPO_ROOT, "fixtures", "synthetic")

# 候補モデル。prefix はモデルごとに作法が違う（索引時と検索時で一致させること）
MODELS = [
    {
        "name": "ruri-v3-310m",
        "dir": "ruri-v3-310m",
        "query_prefix": "検索クエリ: ",
        "doc_prefix": "検索文書: ",
        "dim": 768,
        "note": "cl-nagoya / Apache-2.0 / 日本語特化",
    },
    {
        "name": "multilingual-e5-large",
        "dir": "multilingual-e5-large",
        "query_prefix": "query: ",
        "doc_prefix": "passage: ",
        "dim": 1024,
        "note": "intfloat / MIT / 最大512トークン",
    },
    {
        "name": "bge-m3",
        "dir": "bge-m3",
        "query_prefix": "",
        "doc_prefix": "",
        "dim": 1024,
        "note": "BAAI / MIT / dense+sparse+ColBERT",
    },
    {
        "name": "Qwen3-Embedding-0.6B",
        "dir": "Qwen3-Embedding-0.6B",
        "query_prefix": (
            "Instruct: 社内の相談内容に対して、詳しい社員や関連する社内資料を検索する\nQuery: "
        ),
        "doc_prefix": "",
        "dim": 1024,
        "note": "Qwen / Apache-2.0 / instruction 対応",
    },
    {
        "name": "Nemotron-3-Embed-1B-BF16",
        "dir": "Nemotron-3-Embed-1B-BF16",
        "query_prefix": "query: ",
        "doc_prefix": "passage: ",
        "dim": 2048,
        "note": "NVIDIA / 2026-07 / 2048次元は pgvector で halfvec 必須",
    },
]

K_RETRIEVAL = (5, 10, 20)
K_PERSON = 3
RRF_K = 60

# doc15 の base_score。チャンク種別 → 人への寄与の重み
SOURCE_WEIGHT = {
    "ans_helpful": 1.0,
    "proj_lead": 0.8,
    "ans": 0.7,
    "profile": 0.5,
    "proj_member": 0.48,
}


def load(rel):
    with open(os.path.join(SYN, rel), encoding="utf-8") as f:
        return json.load(f)


def build_corpus(include_daily=False):
    """索引対象チャンク。id は eval_retrieval.json の gold_chunks と同じ命名にする。"""
    documents = load("documents/documents.json")
    projects = load("projects/projects.json")
    profiles = load("people/employee_profiles.json")
    answers = load("answers/answers.json")
    dailies = load("daily_reports/daily_reports.json")
    members = defaultdict(list)
    for m in load("projects/project_members.json"):
        members[m["project_id"]].append(m)

    chunks = []  # (chunk_id, text)
    owners = {}  # chunk_id -> [(employee_id, source_key)]

    for d in documents:
        chunks.append((f"doc:{d['id']}", f"{d['title']}。{d['body']}"))
        owners[f"doc:{d['id']}"] = []  # 文書は人の証拠にならない（doc14 で格下げ）

    for p in projects:
        cid = f"proj:{p['id']}"
        chunks.append(
            (
                cid,
                f"{p['subject']}。課題: {p['client_issue']}。商材: {p['product']}。{p.get('remarks', '')}",
            )
        )
        owners[cid] = [
            (m["employee_id"], "proj_lead" if m["role"] == "lead" else "proj_member")
            for m in members[p["id"]]
        ]

    for pr in profiles:
        cid = f"profile:{pr['employee_id']}"
        chunks.append((cid, pr["description"]))
        owners[cid] = [(pr["employee_id"], "profile")]

    for a in answers:
        cid = f"ans:{a['id']}"
        chunks.append((cid, a["body"]))
        owners[cid] = [
            (a["responder_id"], "ans_helpful" if a.get("was_helpful") else "ans")
        ]

    # 日報は 3,070 件あり、しかも定型文の繰り返し（「仕入れ先との価格交渉を行った。」等）が多い。
    # 入れるとコーパスの9割を占め、同点の近似重複が上位を埋め尽くして層1の指標が潰れる。
    # eval_retrieval.json の gold は doc/proj/profile のみなので既定では除外し、
    # --include-daily で入れて比較できるようにしてある。
    if include_daily:
        for d in dailies:
            cid = f"daily:{d['id']}"
            chunks.append((cid, d["content"]))
            owners[cid] = [(d["employee_id"], "profile")]  # 弱い証拠として profile 相当

    return chunks, owners


def recall_at_k(ranked_ids, gold, k):
    """Recall@k = |gold ∩ top-k| / |gold|（標準の定義。k について単調）。

    gold_chunks は平均9件・最大18件あるので R@10 の上限は事実上1.0未満。
    絶対値ではなく**モデル間の相対比較**に使うこと。R@20 も併記する。
    """
    if not gold:
        return None
    return len(set(ranked_ids[:k]) & set(gold)) / len(gold)


def ndcg_at_k(ranked_ids, gold, k):
    if not gold:
        return None
    gset = set(gold)
    dcg = sum(1.0 / math.log2(i + 2) for i, c in enumerate(ranked_ids[:k]) if c in gset)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(gset))))
    return dcg / ideal if ideal else 0.0


def score_people(ranked_ids, owners, top_n=20):
    """層2の参照スコアラー。順位重み(RRF風) × 種別重み で人に集約する。"""
    score = defaultdict(float)
    for rank, cid in enumerate(ranked_ids[:top_n]):
        rw = 1.0 / (RRF_K + rank + 1)
        for eid, src in owners.get(cid, []):
            score[eid] += rw * SOURCE_WEIGHT.get(src, 0.3)
    return [e for e, _ in sorted(score.items(), key=lambda x: (-x[1], x[0]))]


def run_model(spec, models_dir, device, chunks, owners, retrieval, person):
    from sentence_transformers import SentenceTransformer

    path = os.path.join(models_dir, spec["dir"])
    t0 = time.time()
    model = SentenceTransformer(path, device=device, trust_remote_code=True)
    load_s = time.time() - t0

    texts = [spec["doc_prefix"] + t for _, t in chunks]
    ids = [c for c, _ in chunks]
    t0 = time.time()
    emb = model.encode(
        texts,
        batch_size=64,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    index_s = time.time() - t0

    queries = [spec["query_prefix"] + q["query"] for q in person]
    t0 = time.time()
    qemb = model.encode(
        queries,
        batch_size=32,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    query_s = (time.time() - t0) / max(1, len(queries))

    sims = qemb @ emb.T  # 正規化済みなので内積 = cosine
    order = (-sims).argsort(axis=1)
    ranked = {
        person[i]["id"]: [ids[j] for j in order[i][:64]] for i in range(len(person))
    }

    # ---- 層1: 根拠チャンク ----
    layer1 = defaultdict(list)
    for r in retrieval:
        rk = ranked.get(r["id"])
        if rk is None:
            continue
        for k in K_RETRIEVAL:
            v = recall_at_k(rk, r["gold_chunks"], k)
            if v is not None:
                layer1[f"R@{k}"].append(v)
        v = ndcg_at_k(rk, r["gold_chunks"], 10)
        if v is not None:
            layer1["nDCG@10"].append(v)

    # ---- 層2: 人 ----
    layer2 = defaultdict(list)
    for q in person:
        if q["difficulty"] == "L4" or not q["gold_experts"]:
            continue
        pred = score_people(ranked[q["id"]], owners)
        gold = set(q["gold_experts"])
        hit = len(set(pred[:K_PERSON]) & gold) / min(K_PERSON, len(gold))
        layer2["Recall@3"].append(hit)
        layer2[f"L{q['difficulty'][1]}"].append(hit)
        rr = 0.0
        for i, e in enumerate(pred):
            if e in gold:
                rr = 1.0 / (i + 1)
                break
        layer2["MRR"].append(rr)

    mean = lambda xs: (sum(xs) / len(xs)) if xs else float("nan")
    return {
        "name": spec["name"],
        "dim": emb.shape[1],
        "load_s": load_s,
        "index_s": index_s,
        "query_ms": query_s * 1000,
        "layer1": {k: mean(v) for k, v in layer1.items()},
        "layer2": {k: mean(v) for k, v in layer2.items()},
        "note": spec["note"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=os.path.expanduser("~/models"))
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--only", default=None, help="モデル名を1つだけ指定")
    ap.add_argument("--out", default=None, help="結果を JSON で書き出す")
    ap.add_argument(
        "--include-daily",
        action="store_true",
        help="日報3,070件をコーパスに入れる（層1の指標が潰れるので既定は除外）",
    )
    args = ap.parse_args()

    chunks, owners = build_corpus(include_daily=args.include_daily)
    retrieval = load("eval/eval_retrieval.json")
    person = load("eval/eval_person.json")
    print(
        f"コーパス {len(chunks)} チャンク / 層1 {len(retrieval)} 件 / 層2 {len(person)} 件"
    )
    print(f"device={args.device}\n")

    results = []
    for spec in MODELS:
        if args.only and spec["name"] != args.only:
            continue
        if not os.path.exists(
            os.path.join(args.models_dir, spec["dir"], "config.json")
        ):
            print(f"[skip] {spec['name']}: 未ダウンロード")
            continue
        try:
            r = run_model(
                spec, args.models_dir, args.device, chunks, owners, retrieval, person
            )
            results.append(r)
            print(
                f"[done] {r['name']:26s} dim={r['dim']:5d} "
                f"層1 R@10={r['layer1'].get('R@10', float('nan')):.3f}  "
                f"層2 R@3={r['layer2'].get('Recall@3', float('nan')):.3f}  "
                f"索引 {r['index_s']:.0f}s / クエリ {r['query_ms']:.0f}ms"
            )
        except Exception as e:  # noqa: BLE001 - どのモデルが落ちたかを残して次へ進む
            print(f"[FAIL] {spec['name']}: {type(e).__name__}: {e}")

    if not results:
        raise SystemExit("結果なし")

    print("\n" + "=" * 104)
    print(
        f"{'model':26s} {'dim':>5s} {'R@5':>7s} {'R@10':>7s} {'nDCG10':>7s} | "
        f"{'R@3':>7s} {'MRR':>7s} {'L1':>6s} {'L2':>6s} {'L3':>6s} | {'索引s':>6s} {'q ms':>6s}"
    )
    print("-" * 104)
    for r in sorted(results, key=lambda x: -x["layer2"].get("Recall@3", 0)):
        g = lambda d, k: d.get(k, float("nan"))
        print(
            f"{r['name']:26s} {r['dim']:5d} "
            f"{g(r['layer1'], 'R@5'):7.3f} {g(r['layer1'], 'R@10'):7.3f} "
            f"{g(r['layer1'], 'R@20'):7.3f} {g(r['layer1'], 'nDCG@10'):7.3f} | "
            f"{g(r['layer2'], 'Recall@3'):7.3f} {g(r['layer2'], 'MRR'):7.3f} "
            f"{g(r['layer2'], 'L1'):6.3f} {g(r['layer2'], 'L2'):6.3f} {g(r['layer2'], 'L3'):6.3f} | "
            f"{r['index_s']:6.0f} {r['query_ms']:6.0f}"
        )
    print("=" * 104)
    print(
        "層1=根拠チャンクの検索 / 層2=人の推薦（**主指標は R@3**） / L1〜L3=難易度別の R@3"
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n結果を書き出し: {args.out}")


if __name__ == "__main__":
    main()
