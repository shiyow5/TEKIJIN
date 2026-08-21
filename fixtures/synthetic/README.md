# fixtures/synthetic/ — 合成データ（ER スキーマ準拠）

デモ・評価・テストで使う **合成データ（synthetic data）** をここに置く。
TEKIJIN は実データに接続しない方針なので、**この合成データがそのまま「真実源」**になる。

すべて **架空**。実在の社員・顧客・案件とは一切関係がない。
生成スクリプトは [`scripts/build_fixtures.py`](../../scripts/build_fixtures.py)
（`random.seed(42)` で再現可能）。reona 作のダミーデータ（社員 / 案件 / チャット / 日報）を
入力に、ER スキーマ（`docs/specs/model-definition.md` §4 データモデル、`docs/specs/technical-spec.md` §4）へ
写像しつつ、不足エンティティ（社員プロフィール / 資格 / 過去QA）を補完している。

再生成:

```bash
python3 scripts/build_fixtures.py
```

---

## ファイル形式の規約

- **1エンティティ = 1つの JSON ファイル（`.json`）。中身は JSON 配列**（1要素 = 1レコード）。
  ※ 旧規約は JSON Lines（`.jsonl`）だったが、**JSON 配列に統一**した。
- フィールド名は **snake_case**。日付は ISO 8601（`2026-04-01` / `2026-04-01T09:00:00`）。
- **`embedding` 列は fixtures に含めない。** 埋め込みは取込（シード投入）時に
  アプリ側（C3 埋め込みモデル）が本文から計算してDBへ格納する。
  対象本文: `employee_profiles.description` / `questions.body` / `answers.body` /
  `projects.subject`・`remarks` / `daily_reports.content` など。

---

## ディレクトリ構成と ER（`docs/specs`）との対応

| ディレクトリ / ファイル | 件数 | ER 対応（technical-spec §4 / model-definition §4） | 主なキー・FK |
|---|---|---|---|
| `people/employees.json` | 40 | `employees(id, name, dept, role, branch, …)` | `id`（PK） |
| `people/employee_profiles.json` | 40 | `employees.self_intro`（人の自己紹介＝埋め込み対象） | `employee_id`→employees.id |
| `certifications/certifications.json` | 98 | `certifications(id, employee_id, name, acquired_at)` | `employee_id`→employees.id |
| `projects/projects.json` | 120 | `projects(id, industry, products, period, …)` | `id`（PK） |
| `projects/project_members.json` | 237 | `project_members(project_id, employee_id, role)` | `project_id`→projects.id, `employee_id`→employees.id |
| `chat/employee_chat_history.json` | 2000 | 社内チャットログ（行動痕跡・近接性の証拠） | `sender_employee_id`→employees.id |
| `daily_reports/daily_reports.json` | 3070 | 日報（`content` が類似検索の主要テキスト） | `employee_id`→employees.id |
| `questions/questions.json` | 150 | `questions(id, asker_id, body, topics, status, created_at)` | `asker_id`→employees.id |
| `answers/answers.json` | 150 | `answers(id, question_id, responder_id, body, reuse_count, was_helpful)` | `question_id`→questions.id, `responder_id`→employees.id |
| `documents/documents.json` | 30 | `documents(id, title, body, source, updated_at)` | `id`（PK） |
| `self_declared/skills.json` | 58 | `skills(id, employee_id, topic, level, source)`（自己申告＝最も弱い証拠、base_score 0.3） | `employee_id`→employees.id |
| `eval/` | — | 評価セット（正解ラベル付き）。本タスクの対象外 | — |

### 各エンティティのフィールド

- **employees**: `id, name, email, department, section, position, branch, role, hire_date, department_history[]`
  - `branch`（拠点）= 本社 / 東京 / 大阪 / 名古屋 / 福岡 から重み付き割当（スコア式 `proximity` の源）。
  - `role`（職種）= `department` から導出（営業 / 技術 / スタッフ）。
  - `section` = `department` + 役職ベースの簡易課（部長・課長=第1課、他=第2課）。
- **employee_profiles**: `employee_id, description, updated_at`
  — `description` は当人の案件（product/issue）・日報（content）から抽出した得意トピックを織り込んだ自己紹介文。
- **certifications**: `id, employee_id, name, acquired_at` — 資格は職種（role）に整合するリストから割当。
- **projects**: `id, subject, client_company, industry, company_size, client_issue, product, negotiation_count, status, remarks, start_date, end_date`
- **project_members**: `project_id, employee_id, role`（`lead` = 案件主担当、`member` = 同部署から0〜2名）。
- **employee_chat_history**: `id, sender_employee_id, receiver_employee_id(null), channel, message, sent_at`
- **daily_reports**: `id, employee_id, report_date, content, issue, created_at`（`issue` は content から簡易導出、無ければ `null`）。
- **questions**: `id, asker_id, body, topics[], status(="answered"), created_at`
- **answers**: `id, question_id, responder_id, body, created_at, reuse_count(0〜8), was_helpful(≈7割 true), topic`
  - **全40名が最低1回は `responder_id` に登場**する（少数者の埋没を防ぐため、未カバー社員は
    当人が実際に活動しているトピックのQAへ差し替え。合計150は維持）。
- **documents**: `id, title, body, source, updated_at` — 22トピック語彙に沿った社内手順書・FAQ 風。
  C5 の「格下げ経路（文書で場所を指す）」の素材。`body` は取込時に埋め込み対象。
- **skills**（`self_declared/skills.json`）: `id, employee_id, topic, level, source(="self")`
  — 1人1〜2件。一部は本人の実活動トピックと一致、一部はあえて実活動と無関係にしてあり、
  「自己申告は弱い証拠（base_score 0.3）」であることを表現している。

---

## 「トピック → 得意な社員」と eval の復元

過去QA（questions/answers 150ペア）は、**行動データに裏打ちされた専門性**を種にしている。

1. 各社員の得意トピックを、その社員の **projects（product/issue）** と **daily_reports（content）** の
   頻出語から推定する（トピック語彙は大塚商会商材寄り: UTM / ネットワーク / セキュリティ / クラウド /
   基幹システム / CRM / Webマーケ / SNS など＋各部署業務）。証拠の重み: 案件 lead=1.0 / member=0.6、日報=0.15。
2. `questions.topics[]` と `answers.topic` にトピックを残し、`answers.responder_id` は
   **そのトピックの上位専門家**から選ぶ。

これにより後段の評価（eval）は、**answers を `topic` でグルーピングして `responder_id` を集めるだけで、
`correct_experts` を employee_id 単位で復元**できる（C6 スコアラーの Top-1 / Recall@3 / MRR 計測の燃料）。

---

## `data/` との違い（重要）

| | `fixtures/synthetic/`（ここ） | `data/`（gitignore 済み） |
|---|---|---|
| 中身 | 手で吟味した**種データ**・評価セット | 生成物・大容量・実データ・モデル重み |
| Git | **コミットする**（全員/毎デモで同一） | **コミットしない** |
| 再現性 | バージョン管理で固定 | ローカル限り・再生成可 |

---

## 既知の偏り（要チーム判断）

- **職種比率が仕様§8目標と乖離している。**
  現状 `employees.role`（department から導出）は **技術30% / 営業30% / スタッフ40%**（技術12・営業12・スタッフ16名）で、
  `docs/specs/technical-spec.md` §8 の目標比率 **技術38.7% / 営業32.0% / スタッフ27.7%** から外れている。
- **原因**は、入力元（reona 作 `employees.json`）が **10部署 × 各4名の均等構造**であることに起因する。
  役職の導出（顧客接点・技術・バックオフィス）だけでは目標比率に寄せられない。
- **是正には社員・部署・案件の再生成が必要**（部署ごとの人数配分そのものを変えるか、
  role の割当規則を実態に合わせて再設計する）。案件（projects）・日報・チャットも人員構成に連動するため、
  影響範囲が広い。**この再生成を行うかはチーム判断**とし、本タスクでは既存構造を尊重して据え置いた。

## 注意

- **合成データのみ。** 実在の社員・顧客・案件を混ぜない。実データは受領しない前提。
- 秘密情報（トークン・実URL・実顧客名）は絶対に含めない。
