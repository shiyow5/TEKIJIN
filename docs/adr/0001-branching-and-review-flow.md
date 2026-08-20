# ADR-0001: ブランチ戦略とレビューフロー

- ステータス: 承認
- 日付: 2026-08-20
- 決定者: Aチーム

## 背景

複数人（＋コーディング支援エージェント）で開発する。品質と履歴を保ちつつ、
誰が作業しても同じ手順になるようにしたい。

## 決定

- **main は保護**（PR 経由、会話解決必須）。安定版。リリースを fast-forward で行うため main は直線履歴を保つ（`required_linear_history` 必須は任意で再有効化可）。
- **develop を既定・統合ブランチ**とする。feature ブランチから PR で入れる。
- ブランチ命名: `<type>/<issue番号>-<slug>`。
- マージ要件: **CI 緑 + AIレビュー必須**。人間レビューは立場で非対称:
  - **PL（@shiyow5）** は自分の PR を AIレビューのみでマージ可（`enforce_admins=false` による管理者バイパス）。
  - **PL 以外のメンバー** は PL の承認が必須（`require_code_owner_reviews=true` + CODEOWNERS=@shiyow5）。
  - 少人数（レビュー可能者が PL のみ）で運用を回すための構成。
- feature→develop は Squash merge、マージ後ブランチ自動削除。
- **リリース（develop→main）は fast-forward + アノテートタグ**。
  main を develop の当該コミットへ FF し（新規コミットを作らない）、`vX.Y.Z` タグで印を付ける。
  main は常に develop の当該コミットと同一 SHA になり、先行もマージノードも生じない。
  - Squash/Rebase は SHA を別物に作り直し履歴を枝分かれさせるため**使わない**。
  - マージコミット方式も、リリース毎に main へ merge ノードが増えて main が develop より
    1 コミット先行し続けるため**やめた**（下記「経緯」）。GitHub の「Merge」ボタンは
    マージコミットを作るので使わず、リリースは CLI の `--ff-only` で行う。
- 手順は `.claude/skills/dev-flow/SKILL.md` を唯一の正とし、`AGENTS.md` から全エージェントに周知。

## 経緯（2026-08-20 の履歴分岐）

初回リリースで develop→main を **rebase マージ**した結果、develop のコミットが main 上に
別 SHA で複製され、両ブランチの履歴が枝分かれした（tree は同一）。
対処として develop を main に force-reset して一本化し、main の直線履歴必須を無効化、
以後のリリースはマージコミットに統一した。

### 更新（2026-08-20 その2）: マージコミット → fast-forward

マージコミット方式に統一した後、リリース PR を GitHub の「Merge」ボタンで統合すると
`Merge pull request` ノードが main 側にだけ増え、main が develop より恒常的に 1 コミット
先行した（GitHub に「main had recent pushes / Compare & pull request」バナーが出続ける）。
中身の差分は無くコード上の乖離ではないが、リリース毎に back-merge が要る運用は煩雑なため、
リリースを **fast-forward + タグ**へ変更した。FF は SHA を書き換えないので、rebase/squash で
起きた履歴分岐は発生しない。切替時、develop を当該 merge ノードへ FF して両ブランチを同一 SHA に揃えた。

## 影響

- PL（@shiyow5）は自分の PR を AIレビューのみでマージ可。メンバーは PL の承認が必要。
- main への変更も PR 経由が原則（bypass は PL の裁量）。
- リリースは fast-forward のため main に余分な merge ノードが増えず、main は常に
  リリース対象の develop コミットと同一 SHA。リリースの印はアノテートタグ（`vX.Y.Z`）で残す。
- GitHub の「Merge」ボタンは develop→main では使わない（マージコミットを作るため）。
  リリースは CLI（`git merge --ff-only` + `git push`）で行い、PL の管理者バイパスで push する。
- CI は変更領域のみ実行（path filter）。
