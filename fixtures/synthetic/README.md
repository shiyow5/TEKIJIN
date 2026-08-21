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
| `eval/` | 40 / 35 / 20 | 評価セット v2（`eval_person` / `eval_retrieval` / `eval_robustness`）。生成は `scripts/build_eval_v2.py` | 下記「評価セット」参照 |

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

これにより **answers を `topic` でグルーピングして `responder_id` を集めるだけで、
専門家を employee_id 単位で復元**できる。

> **⚠ この復元性を評価の正解に使ってはいけない（Issue #43）。**
> 専門性推定（doc15）も同じ answers を最重量の証拠（有用回答1.0 / 過去回答0.7）に使うため、
> 「answers を数えるだけ」の実装が満点を取ってしまい、**スコアラーの良し悪しを一切測れない**。
> 評価セット v2 は gold を **projects + daily_reports からのみ**導出し、answers を使わない。
> 下記「評価セット（v2 / Issue #43）」を参照。
> なお、この復元性そのものは**データの整合確認**には引き続き有用。

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

## 評価セット（v2 / Issue #43）

`scripts/build_eval_v2.py` が生成する。**旧 `eval_queries.json`（`scripts/build_eval.py`）は非推奨。**

### なぜ置き換えたか

旧セットは FK 整合・トピック網羅・route 分布という**機械的整合性**は満たしていたが、
**測定として成立していなかった**（2026-08-21 実測）:

| 検査項目 | 旧 eval_queries.json |
|---|---|
| クエリのユニークな文型数 | **6**（40件が6文型の穴埋め） |
| クエリに正解トピックの語が漏れている | **24/40（60%）** |
| キーワード5語だけで route を当てられる率 | **40/40（100%）** |
| 「answers を topic で数えるだけ」で正解と完全一致 | **40/40（100%）** |
| 独立サンプル数（ユニーク正解集合） | **21**（40件あるが実質21件） |
| 専門家不在・想定外入力・個人情報要求 | **0件** |

とくに、`correct_experts` を answers の responder 集計から作っていたため、
専門性推定も同じ answers を最重量の証拠に使う以上、
**「answers を数えるだけ」の実装が満点を取れた**。スコアラーの良し悪しを測れない。

### v2 の3ファイル

| ファイル | 件数 | 中身 |
|---|---|---|
| `eval/eval_person.json` | 40 | **主指標**。質問 → 正しい専門家。難易度 L1(10) / L2(15) / L3(10) / L4(5) |
| `eval/eval_retrieval.json` | 35 | 層1。質問 → 正しい根拠チャンク。**埋め込みモデルの横並び比較用**。`eval_person` の L1〜L3 と id で1対1 |
| `eval/eval_robustness.json` | 20 | 異常系。スコープ外5 / 機微4 / 情報不足5 / 専門家不在3 / 敵対的3。**全件 abstain が正解** |

難易度の定義:

- **L1（10件）**: トピック語を明示。易しい床。回帰検出用
- **L2（15件）**: **症状のみ**。トピック語をクエリから除去。うち5件は拠点制約付き
- **L3（10件）**: 複数トピック横断 / 商材名だけ / 言い換え
- **L4（5件）**: 社内に証拠を持つ人が存在しない。**「わかりません＋エスカレーション」が正解**

### 設計上の3原則

1. **クエリにトピック名・キーワードを書かない**（L2以上）。症状で書く
2. **正解の導出経路をシステムの証拠と分ける**。gold は `projects`(lead1.0/member0.6) + `daily_reports`(0.15)
   からのみ導出し、**`answers` を使わない**
3. 難問・異常系はスクリプト内に定数として著述する（`label_source: "authored"`）。
   L4 の文面は **L2/L3 と同じ体裁**で書く（「〜な方はいますか」等の語尾で書くと表層だけで abstain を当てられる）

### ベースライン（`python3 scripts/eval_baselines.py`）

| baseline | L1 | L2 | L3 | 全体 | 意味 |
|---|---|---|---|---|---|
| random | 0.100 | 0.100 | 0.100 | 0.100 | 下限 |
| **answers_count** | 0.900 | **0.278** | 0.350 | **0.476** | **リークの残量**（旧セットでは 1.000） |
| lexical_profile | 0.567 | 0.044 | 0.067 | 0.200 | 語彙一致のみ。埋め込みはこれを超えないと採用理由が無い |
| lexical_answers | 0.400 | 0.178 | 0.100 | 0.219 | 強めの語彙ベースライン。実質的な打倒目標 |

*(Recall@3。L4 は abstain 判定なので Recall の対象外)*

**本システムの数字は、この表との差分でしか意味を持たない。** モデル比較の前にまずここを測る。

### 妥当性を守るテスト

`backend/tests/test_eval_quality.py` が、評価セットが「自分に甘いテスト」に戻っていないことを検証する:

- L2/L3 のクエリにトピック語が混入していない
- 表層キーワードによる route 的中率が、多数クラスのベースライン+10pt を超えない
- 「answers を数えるだけ」ベースラインの Recall@3 < 0.6
- 独立サンプル数 >= 20

### 既知の限界（正直に開示する）

- **合成データである以上、gold を証拠から完全に独立させることはできない。**
  旧経路（answers 上位3）と新経路の平均重なりは **0.76**。この数字はスライドでそのまま開示する。
  ベースライン `answers_count` が L2 で 0.278 まで落ちることが、実質的な担保になっている。
- **独立サンプル数の上限は fixtures の構造で決まる。** 現在は 10部署 × 4名で、案件が部署単位に割り当たるため、
  トピック → 専門家がほぼ部署に一意に決まる。拠点制約の導入で 13 → **26** まで増やしたが、
  ここを大きく増やすには **案件（projects）を部署横断にする** fixtures 側の変更が要る。
- **route はコーパスの状態から決められなかった。** 全22トピックが「回答7件前後・文書1〜2件」とほぼ均一なため、
  コーパス状態に route を決めさせると全件が同じラベルになる。やむを得ず問いの性質から著述している。
  route 判定精度を本気で測るなら、**文書のあるトピック／無いトピックの差を fixtures 側で作る**必要がある。

---

## 注意

- **合成データのみ。** 実在の社員・顧客・案件を混ぜない。実データは受領しない前提。
- 秘密情報（トークン・実URL・実顧客名）は絶対に含めない。
