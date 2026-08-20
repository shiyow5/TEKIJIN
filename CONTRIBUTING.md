# コントリビューションガイド

開発の進め方は **[.claude/skills/dev-flow/SKILL.md](.claude/skills/dev-flow/SKILL.md)** に
まとまっている。人間・AIエージェントとも、これに従う。

## 要点

1. **Issue を建てる**（テンプレートを使用）。
2. **develop から**ブランチを切る: `<type>/<issue>-<slug>`（例 `feat/12-expertise-scorer`）。
3. **実装** → コミットは Conventional Commits（`feat: 〜` など）。
4. **`make check`** をローカルで緑にする。
5. **PR を develop 向けに作成**（テンプレートに沿って記入）。
6. **CI 緑**を確認。
7. **AIレビュー**（例: `/code-review`）→ **人間レビュー1名以上の approve**。
8. **Squash merge**（feature→develop）。リリースは develop→main の PR。

## ブランチ保護（設定済み）

| ブランチ | 直push | PRレビュー | 備考 |
|---|---|---|---|
| `main` | 禁止（管理者含む） | 1名必須 | 直線履歴・会話解決必須・安定版 |
| `develop` | 禁止（管理者は例外） | 1名必須 | 既定ブランチ・統合先 |

詳細・CLI 例は skill を参照。
