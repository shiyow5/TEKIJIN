# ADR-0001: ブランチ戦略とレビューフロー

- ステータス: 承認
- 日付: 2026-08-20
- 決定者: Aチーム

## 背景

複数人（＋コーディング支援エージェント）で開発する。品質と履歴を保ちつつ、
誰が作業しても同じ手順になるようにしたい。

## 決定

- **main は保護**（PR 経由、会話解決必須）。安定版。直線履歴は無効（マージコミットを許すため。下記参照）。
- **develop を既定・統合ブランチ**とする。feature ブランチから PR で入れる。
- ブランチ命名: `<type>/<issue番号>-<slug>`。
- マージ要件: **CI 緑 + AIレビュー必須**。人間レビューは立場で非対称:
  - **PL（@shiyow5）** は自分の PR を AIレビューのみでマージ可（`enforce_admins=false` による管理者バイパス）。
  - **PL 以外のメンバー** は PL の承認が必須（`require_code_owner_reviews=true` + CODEOWNERS=@shiyow5）。
  - 少人数（レビュー可能者が PL のみ）で運用を回すための構成。
- feature→develop は Squash merge、マージ後ブランチ自動削除。
- **リリース（develop→main）は Create a merge commit（マージコミット）**。
  Squash/Rebase は main と develop の履歴を枝分かれさせるため使わない（下記「経緯」）。
- 手順は `.claude/skills/dev-flow/SKILL.md` を唯一の正とし、`AGENTS.md` から全エージェントに周知。

## 経緯（2026-08-20 の履歴分岐）

初回リリースで develop→main を **rebase マージ**した結果、develop のコミットが main 上に
別 SHA で複製され、両ブランチの履歴が枝分かれした（tree は同一）。
対処として develop を main に force-reset して一本化し、main の直線履歴必須を無効化、
以後のリリースはマージコミットに統一した。

## 影響

- PL（@shiyow5）は自分の PR を AIレビューのみでマージ可。メンバーは PL の承認が必要。
- main への変更も PR 経由が原則（bypass は PL の裁量）。
- リリースはマージコミットのため、main には各リリースの merge commit が残る（develop の
  実コミットは main から辿れる。履歴は一本につながる）。
- CI は変更領域のみ実行（path filter）。
