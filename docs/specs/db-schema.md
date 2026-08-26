# DBスキーマ（ER図） — TEKIJIN

TEKIJIN のデータベース設計。**4層**で構成する。

- **A. 入力データ層** … 合成データ（PR #19）が埋める、社員・プロフィール・案件・チャット・日報。PR #18 の ER をベースとし、本ドキュメントを正とする。
- **B. アプリ実行時テーブル** … 質問・回答・推薦・計測など、アプリ稼働で溜まるデータ（技術仕様 §4 準拠）。
- **C. 専門性グラフ** … 行動痕跡から推定した「人×トピック」の重み付きエッジ（doc15＝新規性の中核）。
- **D. 形式知層** … 生データから抽出した構造化ケース知識 `knowledge_units`（#357/#448＝蓄積＝主軸）。

他テーブルはすべて `EMPLOYEES` の「誰」を FK で指す。永続化は PostgreSQL 16 + pgvector 1本（LangGraph の checkpoints は PostgresSaver が自動管理し、本図には含めない）。

> 整合状況（2026-08-21 監査）: A層は PR #18 の ER に忠実。**B層・C層は技術仕様 §4 / doc15 が要求するが ER 未登場**のため本ドキュメントで補完した。A層にも proximity 用の `branch`(拠点) 等が不足（末尾「A層の補足」参照）。実データ(PR #19)を A層スキーマへ寄せる方針は #18/#19 のレビューで追跡。
>
> 実装補足（#28）: PK 型は本 ER 図の `uuid` 表記ではなく**フィクスチャ実体に準拠**する。`employees`・`projects` は `int`、`cert_` / `q_` / `ans_` / `doc_` / `skill_` 接頭辞を持つエンティティ（certifications・questions・answers・documents・skills）は `string`。`project_members` の PK は `(project_id, employee_id)` とし、`role` は `CHECK(role IN ('lead','member'))` 付きの通常カラム（同一ペアで lead/member が二重登録されないため）。

## A. 入力データ層（合成データ層）— ER図

```mermaid
erDiagram
  EMPLOYEES ||--o| EMPLOYEE_PROFILES : "プロフィールを持つ"
  EMPLOYEES ||--o{ AI_CHAT_HISTORY : "AIとの会話に参加する"
  EMPLOYEES ||--o{ EMPLOYEE_CHAT_HISTORY : "送信した(sender)"
  EMPLOYEES ||--o{ EMPLOYEE_CHAT_HISTORY : "受信した(receiver)"
  EMPLOYEES ||--o{ DAILY_REPORTS : "日報を書く"
  PROJECTS }o..o{ EMPLOYEES : "employees配列で参照(FK制約なし)"

  EMPLOYEES {
    uuid id PK
    string name
    string email
    string department
    string section
    string position
    date hire_date
  }
  EMPLOYEE_PROFILES {
    uuid id PK
    uuid employee_id FK
    text description
    vector embedding
    timestamp updated_at
  }
  AI_CHAT_HISTORY {
    uuid id PK
    uuid employee_id FK
    string speaker
    text content
    timestamp created_at
  }
  EMPLOYEE_CHAT_HISTORY {
    uuid id PK
    uuid sender_employee_id FK
    uuid receiver_employee_id FK
    text channel
    text message
    timestamp sent_at
  }
  DAILY_REPORTS {
    uuid id PK
    uuid employee_id FK
    date report_date
    text content
    text issue
    timestamp created_at
    text_array topics
    vector embedding
  }
  PROJECTS {
    uuid id PK
    string subject
    string client_company
    string industry
    string company_size
    text client_issue
    string product
    int negotiation_count
    string status
    text remarks
    date start_date
    date end_date
    uuid employees "社員ID配列(FK制約なし)"
  }
```

## A層 各テーブルの説明

### EMPLOYEES（社員基本情報）
社員1人につき1行。部・課の両方を記録できる。他のテーブルはすべてこのテーブルの「誰」を指し示す形でつながっている。

| カラム | 説明 |
|---|---|
| `id` | 社員を一意に識別するID |
| `name` / `email` | 氏名・メールアドレス |
| `department` / `section` | 部・課 |
| `position` | 役職 |
| `hire_date` | 入社日 |

### EMPLOYEE_PROFILES（社員プロフィール）
社員1人につき1行。スキル・特徴を自由記述の文章としてまとめて記録する。`embedding` 列を持たせることで、他の検索対象（過去QA、日報など）と同じ仕組みで意味検索にかけられる。

| カラム | 説明 |
|---|---|
| `employee_id` | 誰のプロフィールか（1人1行、UNIQUE） |
| `description` | スキル・得意分野・人柄などをまとめた自由記述文 |
| `embedding` | `description` をベクトル化したもの（AI検索用） |
| `updated_at` | 最終更新日時 |

### PROJECTS（案件）
案件そのものの情報。1案件につき1行。関わっている社員は別テーブルに分けず、`employees` 列に社員IDの配列として直接持たせる。

| カラム | 説明 |
|---|---|
| `subject` | 件名（案件のタイトル） |
| `client_company` | 顧客企業名 |
| `industry` | 顧客企業の業界 |
| `company_size` | 顧客企業の規模（例: 従業員数、売上規模など） |
| `client_issue` | 顧客が抱えていた課題 |
| `product` | 提案・提供した商材 |
| `negotiation_count` | 商談を行った回数 |
| `status` | 案件ステータス（例: 商談中／受注／失注／完了） |
| `remarks` | 備考（自由記述） |
| `start_date` / `end_date` | 案件の開始日・終了日 |
| `employees` | この案件に関わっている社員のIDを配列でまとめて格納（例: `[社員Aのid, 社員Bのid]`） |

### AI_CHAT_HISTORY（AIチャット履歴）
1行=1メッセージの会話ログ形式。`speaker` 列で発言者が社員かAIかを区別する。`employee_id` は常に EMPLOYEES への FK で、「どの社員との会話セッションか」を表す（AIが発言したメッセージの行でも、`employee_id` にはその会話相手の社員IDが入る）。

| カラム | 説明 |
|---|---|
| `employee_id` | どの社員の会話セッションか（FK、常に社員を指す） |
| `speaker` | 発言者の区分:「employee」か「ai」（FKではなく単純な区分値） |
| `content` | 発言内容（質問文、またはAIの回答文） |
| `created_at` | 発言日時 |

### EMPLOYEE_CHAT_HISTORY（社員間チャット履歴）
社員同士のチャット（SlackやTeams等から取り込んだ会話ログを想定）。「誰が誰に、何を話したか」を記録する。

| カラム | 説明 |
|---|---|
| `sender_employee_id` | 送信した社員 |
| `receiver_employee_id` | 受信した社員（グループチャットの場合は空でもよい） |
| `channel` | チャンネル名やグループ名（1対1の場合は空でよい） |
| `message` | メッセージ本文 |
| `sent_at` | 送信日時 |

### DAILY_REPORTS（日報）
社員が日々提出する日報。業務内容（`content`）と課題（`issue`）を分けて記録する。

| カラム | 説明 |
|---|---|
| `employee_id` | 日報を書いた社員 |
| `report_date` | 対象の業務日 |
| `content` | その日行った業務内容 |
| `issue` | その日感じた課題・困りごと |
| `created_at` | 日報が登録された日時 |
| `topics` | **#355**: C6 スコアラーの証拠源に使う事前トピックタグ（`text[]`・seed 時付与） |
| `embedding` | **#433**: 自己回答（System1）の知識源。日報を dense 検索チャネルに載せ、出典 `kind="daily"` で引用（`daily_knowledge_enabled=true`・既定ON）。`migrate` が `ADD COLUMN IF NOT EXISTS`、`deploy.sh` の `embed_missing` が NULL 行を埋める |

---

## B. アプリ実行時テーブル（技術仕様 §4 準拠）

アプリ稼働で溜まるデータ。質問→推薦→回答→計測のループを支える。合成データではなく、デモ実行中に生成される（一部は評価用に合成でシードする）。

```mermaid
erDiagram
  EMPLOYEES ||--o{ CERTIFICATIONS : "資格を持つ"
  EMPLOYEES ||--o{ SKILLS : "スキル(自己申告/推定)"
  EMPLOYEES ||--o{ QUESTIONS : "質問する(asker)"
  EMPLOYEES ||--o{ ANSWERS : "回答する(responder)"
  QUESTIONS ||--o{ ANSWERS : "への回答"
  QUESTIONS ||--o{ RECOMMENDATIONS : "の推薦"
  EMPLOYEES ||--o{ RECOMMENDATIONS : "推薦された人"
  QUESTIONS ||--o{ EVENTS : "の計測"
  PROJECTS ||--o{ PROJECT_MEMBERS : "参加"
  EMPLOYEES ||--o{ PROJECT_MEMBERS : "担当"

  CERTIFICATIONS {
    uuid id PK
    uuid employee_id FK
    string name
    date acquired_at
  }
  SKILLS {
    uuid id PK
    uuid employee_id FK
    string topic
    string level
    string source
  }
  QUESTIONS {
    uuid id PK
    uuid asker_id FK
    text body
    string_array topics
    string status
    timestamp created_at
    vector embedding
  }
  ANSWERS {
    uuid id PK
    uuid question_id FK
    uuid responder_id FK
    text body
    timestamp created_at
    vector embedding
    int reuse_count
    bool was_helpful
  }
  RECOMMENDATIONS {
    uuid id PK
    uuid question_id FK
    uuid employee_id FK
    int rank
    float score
    jsonb reasons
    string outcome
    timestamp created_at
  }
  EVENTS {
    uuid id PK
    uuid question_id FK
    string stage
    timestamp started_at
    timestamp ended_at
    jsonb meta
  }
  PROJECT_MEMBERS {
    uuid project_id FK
    uuid employee_id FK
    string role
  }
  DOCUMENTS {
    uuid id PK
    string title
    text body
    string source
    timestamp updated_at
    vector embedding
  }
```

| テーブル | 役割 | 主なカラム |
|---|---|---|
| `CERTIFICATIONS` | 資格（**最も確実な証拠**、base_score 0.6） | `employee_id` FK, `name`, `acquired_at` |
| `SKILLS` | 自己申告/推定スキル（base_score 0.3） | `employee_id` FK, `topic`, `level`, `source` |
| `QUESTIONS` | 聞く側の質問 | `asker_id` FK, `body`, `topics[]`, `status`, `embedding` |
| `ANSWERS` | 回答（**F-10 ナレッジ化・学習の燃料**） | `question_id` FK, `responder_id` FK, `body`, `embedding`, `reuse_count`, `was_helpful` |
| `RECOMMENDATIONS` | 推薦結果と結末（**学習の要**） | `question_id` FK, `employee_id` FK, `rank`, `score`, `reasons`(jsonb), `outcome`(accepted/declined/timeout), `created_at`(推薦時刻・DB既定 now) |
| `EVENTS` | 各ステージの計測（**p50/p95 レイテンシKPI**） | `question_id` FK, `stage`, `started_at`, `ended_at`, `meta` |
| `PROJECT_MEMBERS` | 案件の担当（**lead/member を区別**。base_score が lead 0.8 / member 0.5） | `project_id` FK, `employee_id` FK, `role`(lead/member) |
| `DOCUMENTS` | 社内文書（格下げ経路用・優先度低） | `title`, `body`, `source`, `embedding` |

> `ANSWERS.reuse_count`/`was_helpful` は `answer_quality` スコアと C8 グラフ更新に、`RECOMMENDATIONS.outcome` は `load`（負荷）減点と「使うほど育つ」学習に、`EVENTS` はレイテンシ計測に直結する（技術仕様 §5・§7）。`RECOMMENDATIONS.created_at`（DB 既定 `now()`）は `load` を**直近7日**の推薦数で数えるための時刻窓に使う（技術仕様 §5）。実装では `ANSWERS.created_at` も同様に DB 既定 `now()` を持ち、実行時に生成される回答へ確実に時刻が入る。

---

## C. 専門性グラフ（doc15＝新規性の中核）

「誰が何に詳しいか」を、行動痕跡（回答・案件・資格）から**決定的に積み上げて**表す「人 × トピック」の重み付きエッジ。C8 ノードがオンライン更新する。**エッジは `EVIDENCE` から再計算可能**（監査可能＝根拠表示に使う）。

```mermaid
erDiagram
  EMPLOYEES ||--o{ PERSON_TOPIC_EDGES : "トピック専門性"
  EMPLOYEES ||--o{ EVIDENCE : "専門性の証拠"

  PERSON_TOPIC_EDGES {
    uuid person_id FK
    string topic_id
    float weight
    float confidence
    int evidence_count
    timestamp last_updated
  }
  EVIDENCE {
    uuid id PK
    uuid person_id FK
    string topic_id
    string source_type
    float base_score
    float weight_contrib
    timestamp ts
  }
```

| テーブル | 役割 | 主なカラム |
|---|---|---|
| `PERSON_TOPIC_EDGES` | 人×トピックの専門性エッジ | `person_id` FK, `topic_id`, `weight`, `confidence`, `evidence_count`, `last_updated` |
| `EVIDENCE` | エッジの根拠（積み上げ） | `person_id` FK, `topic_id`, `source_type`(cert/project/answer/self/redirect), `base_score`, `weight_contrib`, `ts` |

> `base_score`: 有用回答 1.0 > 案件リード 0.8 > 過去回答 0.7 > 資格 0.6 > 案件メンバー 0.5 > 自己申告 0.3（doc15）。断り(declined)は専門性を下げず余裕度のみ下げる。

---

## D. 形式知層（#357/#448＝蓄積＝主軸の実体）

生データ（日報・社員間チャット）から LLM で蒸留した**構造化ケース知識**。graph からは呼ばない
オフライン抽出バッチ（`knowledge/extract.py` 日報／`knowledge/chat.py` チャット）が upsert し、
埋め込み索引を張れば C4／自己回答（System1）が再利用する。承認（`review_status`）まで検索経路に出さない（#354）。

```mermaid
erDiagram
  KNOWLEDGE_UNITS {
    uuid id PK
    string kind
    text problem
    text action
    text result
    text_array topics
    string industry
    string source_type
    string source_id
    float confidence
    string review_status
    vector embedding
    timestamp created_at
  }
```

| カラム | 説明 |
|---|---|
| `kind` | `case`（問題→打ち手→結果）/ `procedure` / `decision`（CHECK 制約） |
| `problem` / `action` / `result` | ケースの中身（`result` は未確定なら NULL） |
| `topics` | 正規22語彙のトピック（日報はタグ継承、チャットは LLM 提案を `normalize_topics` でスナップ） |
| `industry` | 業種（明示があるときのみ） |
| `source_type` / `source_id` | 出典（`daily_report`/`chat` 等）。**`UNIQUE(source_type, source_id)`** で冪等 upsert |
| `confidence` | 抽出の確信度（0.0–1.0） |
| `review_status` | `unreviewed` / `approved` / `rejected`（CHECK）。既定は検索で `approved` のみ露出 |
| `embedding` | ケーステキスト（problem+action+result）の dense ベクトル。索引後に検索で再利用 |

> **索引**: `GIN(topics)`。**measure-first の結果（#448）**: 合成チャットは抽出0件（ケース不在＝データの限界・手法は正）、
> 日報は 25/30 抽出（陽性対照）。実データ（解決済スレッド）で価値が出る。

---

## A層の補足（仕様整合のための追加が必要な列）

PR #18 の ER（A層）に、仕様上あと少し不足がある。実データ(PR #19)を寄せる際に合わせて補う。

| 対象 | 追加/変更 | 理由 |
|---|---|---|
| `EMPLOYEES` | ~~`branch`(拠点) を追加~~ **実装済**（`models/tables.py`） | `proximity`（同支店>同エリア>全社）の計算に必要（技術仕様 §5 `w4·proximity`） |
| `EMPLOYEES` | ~~`role` を追加~~ **実装済**（`department_history` / `password_hash` も追加済） | 職種比率・スコアの説明に使用 |
| `PROJECTS` | `employees uuid[]` は **`PROJECT_MEMBERS`（役割つき）を正**とする | 配列では lead/member を区別できず base_score(0.8/0.5)を割り当てられない |
| （算出でよい） | `years`(在籍年数) は `hire_date` から算出 | 冗長カラムにしない |
