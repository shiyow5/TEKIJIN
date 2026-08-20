# リポジトリ構成の方針

このドキュメントは、TEKIJIN リポジトリの構造と、その意図を記録する。

## 現状（環境構築フェーズ）

アプリ本体は未実装。存在するのは以下のみ:

- 開発ツール設定（ruff / pytest / biome / vitest）
- CI（format / lint / test）
- ディレクトリ雛形と scaffold テスト（CI を緑にするための最小限）

仕様（プロダクト仕様・技術仕様）が固まるまで、アプリのソースは追加しない。

## ディレクトリの役割

| パス | 役割 | 現状 |
|---|---|---|
| `backend/` | サーバー側。Python 3.12 | ツール設定＋パッケージ雛形のみ |
| `backend/src/tekijin/` | アプリのパッケージ | `__init__.py`（version のみ） |
| `backend/tests/` | テスト | scaffold のみ |
| `frontend/` | クライアント側。TypeScript | ツール設定のみ |
| `frontend/src/` | アプリのソース | 空 |
| `frontend/tests/` | テスト | scaffold のみ |
| `docs/specs/` | 仕様書の置き場 | 空（これから追加） |
| `docs/adr/` | 意思決定記録 | テンプレートのみ |
| `scripts/` | 補助スクリプト | 空 |
| `.github/workflows/` | CI 定義 | format / lint / test |
| `.github/actions/` | セットアップ用 composite action | setup-backend / setup-frontend |

## 仕様書の置き場所

- **`docs/specs/`** … プロダクト仕様書、技術仕様書、画面仕様など。
  ここに、話し合いで詰めた仕様を Markdown で置いていく。
- **`docs/adr/`** … 「なぜその技術を選んだか」等の意思決定を1件1ファイルで残す。
  発表の「技術アーキテクチャ」パートの素材になる。ADR のテンプレートは
  [`docs/adr/0000-template.md`](adr/0000-template.md)。

## 仕様確定後に想定される構成（参考・未作成）

仕様が固まったら、backend 側は概ね以下のような分割になる見込み。
**まだ作らない。** 仕様が変われば当然変わる。

```
backend/src/tekijin/
├─ api/          エントリポイント・ルーティング
├─ agent/        取次ぎのロジック
├─ retrieval/    検索
├─ scorer/       推薦スコアリング
├─ models/       データモデル
└─ ...
```

## 命名・ツールの決定（確定事項）

| 項目 | 決定 | 理由 |
|---|---|---|
| backend フォーマッタ/Linter | ruff | 整形と静的解析を1ツールで。高速 |
| backend テスト | pytest | 標準的 |
| frontend フォーマッタ/Linter | Biome | 整形と lint を1ツールで。設定が軽い |
| frontend テスト | vitest | 高速。設定が最小 |
| CI ランナー | `ubuntu-latest`（GitHub-hosted） | 追加インフラ不要で、push すれば即動く |
| タスク入口 | Makefile | ローカルと CI で同じコマンドを使う |

参考にしたリポジトリ: `sosu`（backend/frontend 分割・分割 CI・Makefile 構成）、
`soshosai-monorepo-2026`（Biome の設定方針）。
`sosu` は self-hosted ランナー前提だが、本リポジトリは新規のため GitHub-hosted に置き換えた。
