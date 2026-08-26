"""research_knowledge_llm_gate.py — System1 の発火を LLM ゲートで救えるか。

research_knowledge_falsefire の決定的な負の結果: cosine フロアだけでは「知識で答えるべき質問」と
「人に取り次ぐべき質問」を分離できず、有用に発火するフロアでは person 質問の 10-45% を奪う。

そこで cosine の代わりに **LLM 判定**でゲートする: 上位知識単位(低フロア0.15で候補化)を取り、
本番 Qwen3.6 に「この知識単位(問題→打ち手→結果)は、ユーザーの質問に**直接答えられる**内容か
(単に同じトピックというだけでなく)」を yes/no で判定させ、yes のときだけ発火とみなす。

測る: gold_route 別に person誤発火 / data発火 / data topic一致。cosine-only(0.20) baseline と比較し、
LLM ゲートが person 誤発火をほぼ 0 にしつつ data 発火を保てるか。保てれば System1 は
「短絡してよい確信」を得る手段を得る＝有効化の道が開ける。保てなければ短絡設計自体を捨てる根拠。

本番 vLLM(:18080) 必須・approved+embedded 知識単位が前提。

使い方（DGX・抽出+埋込+approve 済み・vLLM 稼働）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/research_knowledge_llm_gate.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15441/calib --out kb_gate.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

from pydantic import BaseModel, Field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")
CANDIDATE_FLOOR = 0.15  # 候補化の下限（ここを超えた上位1件だけ LLM 判定にかける）


class GateSchema(BaseModel):
    """LLM ゲートの構造化出力。"""

    answerable: bool = Field(description="この知識単位でユーザーの質問に直接答えられるなら true")
    reason: str = Field(default="", description="判定理由（短く）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="kb_gate.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker
    from tekijin.data.knowledge import list_knowledge_units, search_knowledge_units
    from tekijin.eval.dataset import load_eval_queries
    from tekijin.llm.vllm import build_structured_model
    from tekijin.retrieval.embedding import QUERY, SentenceTransformerEmbedder

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
    model = build_structured_model(GateSchema, s)
    approved = [u for u in list_knowledge_units(session, review_status="approved") if u.has_embedding]
    print(f"approved+embedded 知識単位: {len(approved)} 件")
    if not approved:
        print("!! approved+embedded 知識単位が無い。")
        return

    SYS = (
        "あなたは社内の質問に、蓄積された知識ケースで直接答えられるかを判定する審査員です。"
        "知識ケース(問題→打ち手→結果)が、ユーザーの質問が求めている具体的な回答を実際に含んでいる"
        "ときだけ answerable=true にしてください。単に同じ分野・同じトピックというだけ、あるいは"
        "関連する人を紹介すべき相談は answerable=false です。確信が持てなければ false。"
    )

    def judge(question, unit):
        body = f"問題: {unit.problem or ''}\n打ち手: {unit.action or ''}\n結果: {unit.result or ''}"
        prompt = [
            ("system", SYS),
            ("human", f"ユーザーの質問:\n{question}\n\n知識ケース:\n{body}\n\n直接答えられますか。"),
        ]
        try:
            res = model.invoke(prompt)
            return bool(res and res.answerable)
        except Exception:
            return False

    rows = []
    for q in load_eval_queries():
        qvec = embedder.encode([q.query], kind=QUERY)[0]
        hits = search_knowledge_units(session, qvec, top_k=1, review_status="approved")
        top = hits[0] if hits else None
        sim = float(top[1]) if top else 0.0
        unit = top[0] if top else None
        gold = set(q.gold_topics or ())
        cosine_fire = sim >= 0.20  # baseline（誤発火の元凶）
        gate_fire = bool(unit and sim >= CANDIDATE_FLOOR and judge(q.query, unit))
        rows.append(
            {
                "route": q.gold_route,
                "cosine_fire": cosine_fire,
                "gate_fire": gate_fire,
                "topic_match": bool(unit and set(unit.topics) & gold),
            }
        )

    def _rate(items, key, extra=None):
        xs = [1.0 if (r[key] and (extra is None or r[extra])) else 0.0 for r in items]
        return round(statistics.mean(xs), 4) if xs else None

    person = [r for r in rows if r["route"] == "person"]
    data_d = [r for r in rows if r["route"] in ("document", "prior_answer")]
    abstain = [r for r in rows if r["route"] == "none"]
    out = {
        "n_approved": len(approved),
        "person": len(person),
        "data": len(data_d),
        "abstain": len(abstain),
        "cosine_0.20": {
            "person_false_fire": _rate(person, "cosine_fire"),
            "data_fire": _rate(data_d, "cosine_fire"),
            "abstain_false_fire": _rate(abstain, "cosine_fire"),
        },
        "llm_gate": {
            "person_false_fire": _rate(person, "gate_fire"),
            "data_fire": _rate(data_d, "gate_fire"),
            "data_topic_match_fire": _rate(data_d, "gate_fire", "topic_match"),
            "abstain_false_fire": _rate(abstain, "gate_fire"),
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")
    session.close()
    session.get_bind().dispose()


if __name__ == "__main__":
    main()
