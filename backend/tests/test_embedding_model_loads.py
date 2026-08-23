"""既定の埋め込みモデルが、宣言した依存で本当に読めるかを見る（#137）。

`SentenceTransformerEmbedder` は torch / sentence-transformers を
`_get_model()` の中で遅延 import する設計なので、**実モデルを読む経路は
通常のテストでは一度も踏まれない**。その結果 `requirements-ml.txt` の pin が
既定モデル（Nemotron-3-Embed-1B-BF16）を読めない状態のまま気づかれなかった。

重いので既定では走らせない（`-m 'not model'`）。実機でこう回す:

    cd backend && python -m pytest -m model --no-cov tests/test_embedding_model_loads.py
"""

from __future__ import annotations

import pytest

from tekijin.config import get_settings
from tekijin.retrieval.embedding import QUERY, SentenceTransformerEmbedder


@pytest.mark.model
def test_configured_embedding_model_loads_and_matches_the_configured_width() -> None:
    settings = get_settings()
    embedder = SentenceTransformerEmbedder(
        use_e5_prefix=settings.embedding_use_e5_prefix,
        trust_remote_code=settings.embedding_trust_remote_code,
        revision=settings.embedding_model_revision,
    )
    vectors = embedder.encode(["テスト"], kind=QUERY)

    assert len(vectors) == 1
    assert len(vectors[0]) == settings.embedding_dim, (
        f"{settings.embedding_model} は {len(vectors[0])} 次元だが、"
        f"settings.embedding_dim は {settings.embedding_dim}"
    )
    # 索引側と検索側で正規化がずれると内積がコサインにならないので、そこも見る。
    norm = sum(x * x for x in vectors[0]) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-2), f"正規化されていない（ノルム {norm}）"
