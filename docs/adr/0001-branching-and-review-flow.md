# ADR-0001: ブランチ戦略とレビューフロー

- ステータス: 承認
- 日付: 2026-08-20
- 決定者: Aチーム

## 背景

複数人（＋コーディング支援エージェント）で開発する。品質と履歴を保ちつつ、
誰が作業しても同じ手順になるようにしたい。

## 決定

- **main は保護**（PR 経由、直線履歴・会話解決必須）。安定版。
- **develop を既定・統合ブランチ**とする。feature ブランチから PR で入れる。
- ブランチ命名: `<type>/<issue番号>-<slug>`。
- マージ要件: **CI 緑 + AIレビュー必須**。人間レビューは立場で非対称:
  - **PL（@shiyow5）** は自分の PR を AIレビューのみでマージ可（`enforce_admins=false` による管理者バイパス）。
  - **PL 以外のメンバー** は PL の承認が必須（`require_code_owner_reviews=true` + CODEOWNERS=@shiyow5）。
  - 少人数（レビュー可能者が PL のみ）で運用を回すための構成。
- feature→develop は Squash merge、マージ後ブランチ自動削除。
- リリースは develop→main の PR。
- 手順は `.claude/skills/dev-flow/SKILL.md` を唯一の正とし、`AGENTS.md` から全エージェントに周知。

## 影響

- 1人での自己マージは不可（他者の approve が必要）。少人数では相互レビュー体制を敷く。
- main への緊急修正も PR 経由（管理者も直 push 不可）。
- CI は変更領域のみ実行（path filter）。
