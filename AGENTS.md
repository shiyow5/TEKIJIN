# AGENTS.md

このリポジトリで作業するすべてのコーディング支援エージェント（Claude Code / Codex / Cursor /
Copilot など）への共通指示。人間の開発者も同じルールに従う。

## 最重要: 開発フローに必ず従う

開発の進め方（Issue 起票 → ブランチ作成 → 実装 → ローカル/CI 検証 → PR → AIレビュー +
人間レビュー → マージ）は、以下の **skill を唯一の正**として定義している:

- **[.claude/skills/dev-flow/SKILL.md](.claude/skills/dev-flow/SKILL.md)**

コード・ドキュメントを変更する前に必ずこの skill を読み、その手順に従うこと。
このファイルを読めないエージェントでも、上記 Markdown を直接参照すれば同じ手順に従える。

## 絶対に守ること（抜粋）

- `main` / `develop` へ直接 push しない。変更は PR を通す。`develop` が統合ブランチ。
- 作業は Issue → ブランチ（`<type>/<issue>-<slug>`）→ PR（base=develop）の順。
- push 前に `make check` をローカルで緑にする。
- マージには CI 緑 + AIレビューが必須。**PL 以外のメンバーの PR は PL（@shiyow5）の承認が必須**
  （PL 自身の PR は AIレビューのみでマージ可）。
- 秘密情報（.env・鍵・トークン）をコミットしない。

## タスクの入口

```bash
make help     # 使えるタスク一覧
make check    # fmt-check + lint + test
```

## リポジトリ構成

[docs/REPO_STRUCTURE.md](docs/REPO_STRUCTURE.md) を参照。仕様書は `docs/specs/` に置く。
