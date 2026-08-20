---
name: dev-flow
description: >-
  TEKIJIN リポジトリの開発フロー（Issue 起票 → ブランチ作成 → 実装 → ローカル/CI 検証 →
  PR 作成 → AIレビュー →（メンバーは PL レビュー）→ マージ）を定義する。main への直 push は避け、
  develop を統合ブランチとする。コード変更・Issue 作成・ブランチ作成・PR 作成・マージ・
  レビュー対応を行うとき、または「開発の進め方」「ブランチ名」「PRの書き方」「どこにマージ」
  を尋ねられたときに必ず参照する。あらゆるコーディング支援エージェントが従う共通ルール。
---

# TEKIJIN 開発フロー

このリポジトリでコードやドキュメントを変更するすべての作業（人間・AIエージェント問わず）は、
以下のフローに従う。**このファイルが唯一の正**とし、逸脱しない。

## 0. 大原則（絶対）

1. **`main` / `develop` へ直接 push しない。** 変更は必ず PR を通す。
2. **`develop` が開発の統合ブランチ。** 日々の作業はここへ PR で入れる。
3. **作業は必ず Issue → ブランチ → PR の順。** いきなりブランチを切らない、いきなり実装しない。
4. **マージには CI 緑 + AIレビューが必須。人間レビューはメンバーの PR で必須。**
   - **PL（@shiyow5）**: 自分の PR は **AIレビューのみでマージ可**（管理者バイパス）。
   - **PL 以外のメンバー**: PR には **PL（コードオーナー @shiyow5）の承認が必須**。自分では承認・マージできない。
5. **秘密情報（`.env`・鍵・トークン・サービスアカウント JSON）をコミットしない。**

ブランチの関係:

```
feature ブランチ ──PR──▶ develop ──（リリース時）PR──▶ main
   (実装)              (統合・既定)                 (安定版)
```

## 1. Issue を建てる

作業の前に、必ず Issue を1つ立てる。1 Issue = 1つのまとまった作業。

- テンプレートを使う（`.github/ISSUE_TEMPLATE/`）。機能は `feature`、不具合は `bug`。
- タイトルは Conventional Commits 形式で始める: `feat: 〜` / `fix: 〜`。
- **推奨構成（機能）**:
  - **背景・目的**: なぜ必要か。どの仕様（`docs/specs/`）・課題に紐づくか。
  - **期待する体験 / ユーザーストーリー**: 誰が何をできるようになるか。
  - **完了条件（Definition of Done）**: 満たすべき条件をチェックボックスで。曖昧さを残さない。
  - **スコープ外**: 今回やらないこと。
  - **参考**: 仕様・関連 Issue・外部資料。
- **推奨構成（バグ）**: 概要 / 再現手順 / 期待する挙動 / 実際の挙動 / 環境 / 参考ログ。

CLI 例:
```bash
gh issue create --title "feat: 専門性スコアラーの骨組み" --label feature --body "..."
```

## 2. ブランチを作る

**必ず develop から**切る。Issue 番号を含める。

### 命名規則

```
<type>/<issue番号>-<短い英小文字スラッグ>
```

- `type` = `feat` | `fix` | `refactor` | `docs` | `test` | `chore` | `perf` | `ci`
- スラッグは英小文字・数字・ハイフン（kebab-case）。簡潔に。
- 例: `feat/12-expertise-scorer` / `fix/34-login-race` / `docs/7-repo-structure`

```bash
git switch develop && git pull
git switch -c feat/12-expertise-scorer
```

## 3. 実装する

- 小さく、レビューしやすい単位に保つ。1 PR が大きくなりすぎたら分割する。
- コミットメッセージは **Conventional Commits**: `<type>: <日本語で簡潔に>`。
  例: `feat: 資格ベースの初期スコアを実装`。
- 仕様に関わる変更をするときは、対応する `docs/specs/` を併せて更新する。
- 意思決定（技術選定など）をしたら `docs/adr/` に1件追加する。

## 4. ローカルで検証する（push 前）

**push する前に必ずローカルで緑にする。** CI で落とさない。

```bash
make check      # = fmt-check + lint + test（backend/frontend 両方）
```

個別:
```bash
make fmt        # 自動整形（コミット前に実行）
make lint
make test
```

## 5. push して PR を作る

```bash
git push -u origin feat/12-expertise-scorer
gh pr create --base develop --fill   # 向き先は必ず develop（リリース時のみ main）
```

- **PR の向き先（base）は develop。** main への PR はリリース時のみ。
- テンプレート（`.github/pull_request_template.md`）に沿って本文を書く。
- **PR 本文の推奨構成**:
  - **概要**: 何を・なぜ。
  - **関連Issue**: `Closes #12`（マージで Issue を閉じる）。
  - **変更内容**: 主な変更点の箇条書き。
  - **動作確認・テスト**: どう確認したか。`make check` が緑であること。
  - **レビュー観点**: レビュアーに特に見てほしい点。
  - **スクリーンショット / デモ**: UI 変更がある場合。
  - **セルフチェック**: 秘密情報なし・ドキュメント更新・AI/人間レビュー依頼済み。
- タイトルは Conventional Commits 形式。

## 6. CI を確認する

PR を出すと、変更領域に応じて **Format Check / Lint / Test** が走る。
加えて **PR Policy Check** が、ブランチ名・向き先・タイトル・Issue 紐付けの逸脱を点検する
（重大な逸脱は失敗、体裁は警告）。警告が出たら直す。

```bash
gh pr checks --watch
```

- **すべて緑になるまでマージしない。** 落ちたら修正して push し直す。
- ログを見て原因を特定する（`gh run view --log-failed`）。

## 7. AIレビューを受ける（必須）

人間レビューに出す前に、**AIによるレビューを1回は通す**。

- 手段は各自の環境に合わせてよい（例: Claude Code の `/code-review`、Codex、Copilot などの
  コードレビュー機能）。
- 指摘のうち **重大・高**は対応してから人間レビューへ回す。対応 or 見送りの判断を PR に残す。
- AIレビューの結果（要約や対応方針）を **PR にコメントとして残す**。「AIレビュー実施済み」を
  可視化する。

## 8. 人間レビューを受ける

レビュー要件は立場で異なる（ブランチ保護 + CODEOWNERS で強制）。

- **PL 以外のメンバーの PR**: **PL（@shiyow5）の approve が必須**。
  自分の PR を自分で approve・マージすることはできない。
- **PL（@shiyow5）の PR**: 人間の approve は不要。**AIレビュー（§7）を済ませればマージ可**
  （管理者バイパス）。ただし AIレビューの重大指摘は必ず対応すること。
- いずれも、レビュー指摘は PR 上で会話を解決（resolve）してからマージ。
- CODEOWNERS（`@shiyow5`）により、レビュアーは自動でアサインされる。

## 9. マージする

- **feature → develop**: **Squash and merge**。マージ後、ブランチは自動削除される。
- コミットが Issue を閉じるよう `Closes #N` を PR に含めておく。
- **develop → main（リリース）**: develop から main への PR を作り、レビューを経てマージ。
  main は直線履歴（linear history）必須のため Squash / Rebase でマージする。

## 10. やってはいけないこと

- `main` / `develop` への直 push（PR を通す）。
- メンバーが PL の承認なしにマージすること。CI・AIレビューをスキップしてのマージ。
- 秘密情報のコミット。誤ってコミットしたら履歴から除去し、トークンをローテーションする。
- 1 PR に無関係な変更を混ぜる。Issue 単位に分ける。
- 巨大 PR。レビュー不能なサイズにしない。

## クイックリファレンス

```bash
# 1. Issue
gh issue create --title "feat: ..." --label feature
# 2. ブランチ（develop から）
git switch develop && git pull && git switch -c feat/<issue>-<slug>
# 3-4. 実装 → ローカル検証
make fmt && make check
# 5. PR（base=develop）
git push -u origin HEAD && gh pr create --base develop --fill
# 6. CI
gh pr checks --watch
# 7. AIレビュー（例: /code-review）→ 指摘対応
# 8. レビュー（PL 以外は @shiyow5 の approve 必須 / PL は AIレビューのみで可）＋ 会話解決
# 9. Squash merge（feature→develop）
```
