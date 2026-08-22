# TEKIJIN（適時／適材適所）

社内の「これ誰に聞けばいいんだろう」を、AIが正しい人へ正しい形で取り次ぐプロダクト。
大塚商会サマーインターン Aチーム。

> プロトタイプ実装が進行中です。ローカルでの起動方法は
> [アプリの起動（開発）](#アプリの起動開発)を参照してください。

## リポジトリ構成

```
TEKIJIN/
├─ backend/          Python 3.12 バックエンド（FastAPI + LangGraph）
│  ├─ src/tekijin/   アプリ本体（API / エージェント / 検索 / データ）
│  ├─ tests/         pytest（単体・結合）
│  ├─ pyproject.toml ruff / pytest 設定
│  └─ requirements-dev.txt
├─ frontend/         TypeScript フロントエンド（Next.js 15 + React 19）
│  ├─ src/
│  ├─ tests/
│  ├─ package.json   biome / vitest
│  └─ biome.json
├─ docs/             仕様書・設計文書の置き場（下記参照）
│  ├─ specs/         仕様書（プロダクト/技術仕様など）
│  └─ adr/           意思決定記録（Architecture Decision Records）
├─ scripts/          補助スクリプト
├─ .github/
│  ├─ workflows/     CI（format / lint / test）
│  └─ actions/       セットアップ用 composite action
└─ Makefile          開発タスクの入口
```

詳細は [docs/REPO_STRUCTURE.md](docs/REPO_STRUCTURE.md) を参照。

## 開発の始め方

```bash
make setup        # backend/frontend の開発ツールを導入
make check        # フォーマット確認 + lint + テストを一括実行
make help         # 使えるタスク一覧
```

個別に:

```bash
make fmt          # 自動整形（backend: ruff / frontend: biome）
make lint         # lint
make test         # テスト
```

## アプリの起動（開発）

バックエンド（`:8000`）とフロントエンド（`:3000`）をまとめて 1 コマンドで立ち上げます。

```bash
make setup        # 初回のみ: backend/frontend の依存を導入
make serve        # backend(uvicorn --reload) + frontend(next dev) を同時起動
                  # 別名: make dev（make serve のエイリアス）
```

- 起動後: フロント <http://localhost:3000> ／ API <http://localhost:8000>（`/docs` に OpenAPI）。
- `Ctrl-C` で backend・frontend を**両方**停止します（プロセスの取り残しなし）。
- 既定ではスタブ LLM ＋ MemorySaver で動くため、**DB や ML 依存（`make setup-ml`）なしで起動**できます。
- フロントは `NEXT_PUBLIC_API_BASE_URL`（既定 `http://localhost:8000`）で API を参照します。

片方だけ起動したいとき:

```bash
make run-backend  # backend のみ（uvicorn --reload）
make run-frontend # frontend のみ（next dev）
```

本番相当（vLLM + PostgreSQL/PostgresSaver、backend のみ）で動かすとき:

```bash
make db-up        # PostgreSQL 16 + pgvector を起動
make seed         # 合成フィクスチャを投入
make setup-ml     # 実埋め込みモデル用の ML 依存を導入
make embed        # 密ベクトルを計算・格納
TEKIJIN_LLM_BACKEND=vllm TEKIJIN_CHECKPOINTER_BACKEND=postgres make serve-prod
```

## 開発フロー（重要）

Issue → ブランチ → 実装 → `make check` → PR（develop向け）→ CI → AIレビュー →（メンバーは PL レビュー）→ マージ。
**`main` への直 push は禁止。`develop` が統合ブランチ。**

- 手順の唯一の正: **[.claude/skills/dev-flow/SKILL.md](.claude/skills/dev-flow/SKILL.md)**
- 全エージェント共通指示: [AGENTS.md](AGENTS.md) / 人間向け要約: [CONTRIBUTING.md](CONTRIBUTING.md)

## ツールチェーン

| 対象 | 言語/FW | フォーマッタ | Linter | テスト |
|---|---|---|---|---|
| backend | Python 3.12 | ruff format | ruff | pytest |
| frontend | TypeScript | Biome | Biome | vitest |

## CI

`main` への push と Pull Request で、変更のあった領域だけ以下が走ります。

- **Format Check** … 整形崩れの検出
- **Lint** … 静的解析
- **Test** … 単体テスト

GitHub-hosted の `ubuntu-latest` で動作します（追加のランナー設定は不要）。

## 注意

- 秘密情報（`.env`、サービスアカウント JSON 等）はコミットしないこと。`.gitignore` で除外済み。
- 設定値のひな型は `.env.example` を参照。
