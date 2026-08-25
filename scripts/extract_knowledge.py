"""#357 slice 2: 営業日報 → ケース知識単位の抽出バッチ（オフライン）。

指定トピック（PoC 既定「CRM・営業支援」）の営業日報を読み、本番 vLLM(:18080) の
structured output で `問題→打ち手→結果` のケースに蒸留し、`knowledge_units` へ
出典付き・冪等 upsert する。graph からは呼ばれない純オフライン。

抽出品質を測れるように、格納後に per-トピックの件数と、サンプル数件（problem/action/
result/confidence）を出力する。人手レビュー（#354）は review_status=unreviewed のまま
残るので、承認するまで検索経路（スライス3・既定 approved のみ）には出ない。

使い方（DGX・throwaway pgvector を prepare 済み・本番 vLLM 稼働前提）:
    PYTHONPATH=backend/src CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
      TEKIJIN_EMBEDDING_MODEL=/home/team_a/models/Nemotron-3-Embed-1B-BF16 \
      TEKIJIN_APP_ENV=development \
      TEKIJIN_LLM_BACKEND=vllm TEKIJIN_LLM_BASE_URL=http://localhost:18080/v1 \
      TEKIJIN_LLM_MODEL=Qwen3.6-35B-A3B-NVFP4 \
      .venv/bin/python scripts/extract_knowledge.py \
      --db-url postgresql+psycopg://postgres:calibpw@localhost:15433/calib \
      --topic "CRM・営業支援" --limit 60 --out knowledge_extract.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "backend", "src")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--topic", default="CRM・営業支援")
    ap.add_argument("--limit", type=int, default=None, help="抽出対象の日報件数上限（PoC 用）")
    ap.add_argument("--out", default="knowledge_extract.json")
    args = ap.parse_args()

    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["TEKIJIN_DATABASE_URL"] = url
    sys.path.insert(0, SRC)

    from tekijin.config import get_settings
    from tekijin.data.db import get_engine, get_sessionmaker, session_scope
    from tekijin.data.knowledge import list_knowledge_units
    from tekijin.knowledge.extract import (
        CaseExtractor,
        daily_report_sources,
        extract_and_store,
    )

    settings = get_settings()
    factory = get_sessionmaker(get_engine(url))
    extractor = CaseExtractor(settings=settings)

    with session_scope(factory) as session:
        sources = daily_report_sources(session, args.topic, limit=args.limit)
        print(f"topic={args.topic!r} 抽出対象の日報: {len(sources)} 件")
        counts = extract_and_store(session, sources, extractor)
        print(f"抽出結果: {counts}")
        stored = list_knowledge_units(session)

    samples = [
        {
            "source_id": u.source_id,
            "topics": list(u.topics),
            "industry": u.industry,
            "problem": u.problem,
            "action": u.action,
            "result": u.result,
            "confidence": u.confidence,
            "review_status": u.review_status,
        }
        for u in stored[:8]
    ]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "topic": args.topic,
                "counts": counts,
                "total_units": len(stored),
                "samples": samples,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"wrote {args.out} (total_units={len(stored)})")


if __name__ == "__main__":
    main()
