# TEKIJIN（適時／適材適所）

社内の「これ誰に聞けばいいんだろう」を、AIが正しい人へ正しい形で取り次ぐプロダクト。
大塚商会サマーインターン Aチーム。

> このリポジトリは現在 **環境構築フェーズ** です。
> アプリ本体（仕様に関わる実装）はまだ含まれていません。仕様確定後に追加します。

## リポジトリ構成

```
TEKIJIN/
├─ backend/          Python 3.12 バックエンド（ツール設定のみ、アプリ未実装）
│  ├─ src/tekijin/   パッケージ雛形
│  ├─ tests/         テスト（現状は scaffold のみ）
│  ├─ pyproject.toml ruff / pytest 設定
│  └─ requirements-dev.txt
├─ frontend/         TypeScript フロントエンド（ツール設定のみ、アプリ未実装）
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
