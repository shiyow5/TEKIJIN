# fixtures/synthetic/ — 合成データの置き場所

デモ・評価・テストで使う **合成データ（synthetic data）** をここに置く。
TEKIJIN は実データに接続しない方針なので、**この合成データがそのまま「真実源」**になる。

> 現状は**置き場所とスキーマ定義のみ**。実際のレコード生成・シード投入は
> 仕様実装フェーズで行う（このディレクトリにはまだデータ本体を置かない）。

---

## `data/` との違い（重要）

| | `fixtures/synthetic/`（ここ） | `data/`（gitignore 済み） |
|---|---|---|
| 中身 | 手で吟味した**小さな種データ**・評価セット | 生成物・大容量・実データ・モデル重み |
| Git | **コミットする**（全員/毎デモで同一） | **コミットしない** |
| 再現性 | バージョン管理で固定 | ローカル限り・再生成可 |

生成スクリプトの**出力先**は `data/`（無視される）。そこから吟味して残す最小セットだけを
`fixtures/synthetic/` にコミットする、という運用を想定する。

---

## ディレクトリと、専門性推定の証拠（evidence）との対応

各エンティティは doc15「専門性推定とグラフ成長」の `evidence.source_type` / `base_score` に対応する。

| ディレクトリ | 内容 | source_type | base_score |
|---|---|---|---|
| `people/` | 社員マスタ（合成の氏名・部署・年次） | — | — |
| `answers/` | 過去の回答ログ。有用評価・再利用数を含む | `answer`（有用評価で `1.0` / 通常 `0.7`） | 1.0 / 0.7 |
| `projects/` | 案件履歴。リード担当かメンバーかを持つ | `project`（リード `0.8` / メンバー `0.5`） | 0.8 / 0.5 |
| `certifications/` | 保有資格 | `cert` | 0.6 |
| `self_declared/` | 自己申告スキル（最も弱い証拠） | `self` | 0.3 |
| `questions/` | 質問ログ（デモの入力・経路判定の素材） | —（結果が evidence を生む） | — |
| `eval/` | 評価セット（正解ラベル付き）。精度計測に使う | — | — |

> `redirect`（「別の人を薦める」による転送の証拠）は questions の結果として生成されるため、
> 種データとしては持たない。

---

## ファイル形式の規約

- **1エンティティ = 1つ以上の JSON Lines（`.jsonl`）**。1行1レコード。差分レビューしやすい。
- フィールド名は **snake_case**。ID は文字列（例: `p_001`, `ans_0007`）。
- 日付は ISO 8601（`2026-03-12`）。
- 文章（回答本文・案件記述）は**日本語で、質にこだわる**。専門性推定の精度は
  この本文の埋め込みに乗るため、量より質（doc15 §6）。

### 最小スキーマ（実装時に確定。ここは目安）

```jsonc
// people/people.jsonl
{ "person_id": "p_001", "name": "高梨 健太", "dept": "技術部", "years": 9 }

// answers/answers.jsonl
{ "answer_id": "ans_0007", "person_id": "p_001", "question_id": "q_0003",
  "topic": "UTM", "body": "移行時はまず現行のポリシーを…",
  "was_helpful": true, "reuse_count": 4, "ts": "2026-03-12" }

// projects/projects.jsonl
{ "project_id": "prj_012", "person_id": "p_001", "role": "lead",   // lead | member
  "topic": "ネットワークセキュリティ", "summary": "UTM入替 3拠点", "ts": "2026-05-01" }

// certifications/certifications.jsonl
{ "person_id": "p_001", "name": "情報処理安全確保支援士", "ts": "2022-10-01" }

// self_declared/self_declared.jsonl
{ "person_id": "p_002", "topic": "UTM", "ts": "2026-01-10" }

// questions/questions.jsonl
{ "question_id": "q_0003", "asker_id": "p_050",
  "text": "お客様がUTMの入れ替えを検討中。他社製品からの移行で注意点は？",
  "ts": "2026-08-01" }

// eval/recommendation.jsonl  — 推薦精度（Top-1 / Recall@3）用
{ "question_id": "q_e001", "text": "…", "gold_person_ids": ["p_001", "p_007"],
  "route": "handoff" }   // route: handoff（人へ取次ぎ）| past_answer（過去回答提示）
```

---

## 量の目安（doc12 §9 / doc15 §6）

| データ | 目標 | 用途 |
|---|---|---|
| 行動履歴（answers + projects） | 約 **150 件** | グラフ成長のデモ（0→N件で精度が上がる） |
| 評価セット（`eval/`） | **40 件**（正解ラベル付き） | Top-1 70%↑ / Recall@3 90%↑ / 経路判定 80%↑ |
| 負荷分散の比較用質問 | 100 件 | 上位1名への集中率を素朴方式と比較 |

---

## 注意

- **合成データのみ。** 実在の社員・顧客・案件を混ぜない。実データは受領しない前提。
- 氏名等は架空。それでも個人を想起させる生々しい内容は避け、あくまで技術トピックの検証用に留める。
- 秘密情報（トークン・実URL・実顧客名）は絶対に含めない。
