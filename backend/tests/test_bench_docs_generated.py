"""`docs/benchmarks/` の数表が測定結果 JSON と一致しているかを見る（#158）。

この文書群は測定結果の記録なので、**表を手で書き換えると必ずずれる**。
実際、表を直して隣の表や散文を直し忘れる指摘をレビューで繰り返し受け、
信頼区間を手で書いて実測と違う値を載せたこともある。

そこで表は `scripts/render_bench_docs.py` が JSON から生成し、
このテストが「生成し直しても差分が出ないこと」と
「本文の 95%CI がすべて実測 JSON に存在すること」を CI で担保する。

落ちたら **文書ではなくスクリプトを実行して直す**:

    python scripts/render_bench_docs.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "render_bench_docs.py"


def test_benchmark_tables_match_the_measured_json() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "docs/benchmarks の表が測定結果 JSON とずれている。\n"
        "`python scripts/render_bench_docs.py` を実行して生成し直すこと。\n\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
