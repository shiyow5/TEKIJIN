# モデル定義 — TEKIJIN

version 0.1 / 2026-08-20
対象プロダクト: TEKIJIN。社内の「これ誰に聞けばいいんだろう」を、
AIが正しい人へ正しい形で取り次ぐ。**答えの出所は必ず人。**

> **設計の芯**: ランキング・推薦は**決定的スコア**で行い、LLM は「理解・判定・生成」に限定する。
> 理由は再現性・説明可能性・速度・評価可能性（13_技術仕様書 §1）。

---

## 0. AIコンポーネント一覧

システムは「1つの大きなAIモデル」ではなく、**役割の異なる小さなモデル/関数の合成**。
これらを **LangGraph の `StateGraph` のノード**として繋ぐ（C1〜C8＝ノード、分岐＝条件付きエッジ）。
LLM への接続は **LangChain**（`init_chat_model` / `with_structured_output`）で統一する。

| ID | コンポーネント | 種別 | LangGraph上の実装 | 入力→出力の要点 |
| --- | --- | --- | --- | --- |
| C1 | 意図理解・トピック抽出 | LLM（構造化出力） | ノード（LLM + `with_structured_output`）→ 条件付きエッジ(out_of_scope) | 質問 → トピック・製品・状況 |
| C2 | 情報充足判定・逆質問生成 | LLM（構造化出力） | ノード + **`interrupt()`**（不足なら聞き返し） | 質問＋文脈 → 不足項目 or 逆質問 |
| C3 | 埋め込み生成 | 埋め込みモデル | C4ノード内で実行 | テキスト → ベクトル |
| C4 | ハイブリッド検索 | 決定的（Dense+BM25+RRF） | ノード（決定的） | クエリ → 近い過去QA/文書/人 |
| C5 | 解決経路の判定 | 決定的（確信度＋閾値） | **`add_conditional_edges`**（person/prior_answer/document） | 検索結果 → 経路選択 |
| C6 | 専門性スコアラー | 決定的（証拠積み上げ） | ノード（決定的） | トピック＋人 → 適合度・根拠・順位 |
| C7 | 依頼文の下書き生成 | LLM | ノード（LLM） | 質問＋相手＋必須項目 → 依頼文 |
| C8 | 専門性グラフ更新 | 決定的（オンライン更新） | ノード（決定的） | 結果イベント → エッジ重み更新 |

LLM を使うのは **C1・C2・C7 の3つだけ**。推薦の中核（C4・C5・C6・C8）は決定的ノード。
**LangGraph はノードの接続・分岐・ストリーミング・中断再開・永続化を担い、"並べ替え"はしない。**

---

## 1. 全体データフロー

```mermaid
flowchart TD
  Q["ユーザーの質問（自然文）"] --> C1["C1 意図理解・トピック抽出<br/>（LLM）"]
  C1 -->|out_of_scope| X["業務外として丁重に受け流す"]
  C1 --> C2{"C2 情報は十分か<br/>（LLM）"}
  C2 -->|不足| FQ["逆質問を1つ返す"] --> Q
  C2 -->|十分| C3["C3 埋め込み生成"] --> C4["C4 ハイブリッド検索<br/>Dense + BM25 + RRF"]
  C4 --> C5{"C5 経路判定<br/>確信度 × 閾値"}
  C5 -->|補助| PA["過去に誰が答えたか<br/>＝人の証拠として提示"]
  C5 -->|格下げ| DOC["文書の場所を指す<br/>（答えは作らない）"]
  C5 -->|主線| C6["C6 専門性スコアラー<br/>候補3名＋適合度＋根拠"]
  PA -->|本人に追加で聞く| C6
  C6 --> C7["C7 依頼文の下書き生成<br/>（LLM）"]
  C7 --> SEND["送信"]
  SEND --> DEC{"断られた？"}
  DEC -->|はい 今は難しい| NEXT["次候補へ再ルーティング"] --> SEND
  DEC -->|いいえ| ANS["回答が届く"]
  ANS --> C8["C8 専門性グラフ更新<br/>使うほど精度向上"]

  classDef llm fill:#e3f2fd,stroke:#1565c0;
  classDef det fill:#f1f8e9,stroke:#558b2f;
  class C1,C2,C7 llm;
  class C4,C5,C6,C8 det;
```

> 青 = 生成LLM（C1・C2・C7）、緑 = 決定的処理（C4〜C8）。**推薦の中核は決定的**。
> このフローチャート＝ **LangGraph の StateGraph** そのもの（角丸＝ノード、菱形＝条件付きエッジ）。
> 各ノード更新は `graph.stream(stream_mode="updates")` で取り出し SSE 配信（＝思考過程の可視化）。

### LangGraph としての実装対応

| フロー上の要素 | LangGraph の機能 |
| --- | --- |
| C1〜C8 の各処理 | `add_node`（ノード。LLMノードと決定的ノードが混在） |
| out_of_scope / 充足 / 経路 / 断り再ルーティング | `add_conditional_edges`（分岐を宣言的に） |
| C2 の逆質問（ユーザーへ聞き返す） | `interrupt()` → ユーザー回答で `Command(resume=...)` |
| 思考過程の可視化 | `graph.stream(stream_mode="updates")` → SSE |
| セッション・中断再開・メモリ | `compile(checkpointer=PostgresSaver)`、`thread_id`=セッション |
| 状態 | `State`（`TypedDict`）。各ノードは部分更新の dict を返す |

---

## 2. コンポーネント別 入出力定義

### C1. 意図理解・トピック抽出（LLM / 構造化出力）

- **目的**: 自由文の質問を、検索・スコアリングで使える構造に落とす。業務外・悪意入力もここで分類。
- **モデル**: ローカルLLM（小〜中型で可。JSON固定なので軽い）。開発時は Claude で品質基準を作る。
- **入力**:

```json
{ "question": "お客様がUTMの入れ替えを検討中。他社製品からの移行で注意点は?",
  "asker": { "dept": "第3営業部", "role": "営業", "years": 1 } }
```

- **出力（JSON Schema 準拠を強制）**:

```json
{ "topics": ["セキュリティ", "UTM", "移行"],
  "products": ["UTM"],
  "situation": "他社製品からの移行",
  "question_type": "技術相談",            // 製品QA/見積/技術相談/事務手続き/雑談/業務外
  "out_of_scope": false,
  "confidence": 0.86 }
```

- **失敗時**: `out_of_scope=true` または `confidence<閾値` → 回答を作らず、業務外は丁重に受け流す / 低確信は C2 の逆質問へ。

### C2. 情報充足判定・逆質問生成（LLM / 構造化出力）

- **目的**: 取り次ぐ/検索する前に、判断に必要な情報が揃っているか点検し、足りなければ1問だけ聞き返す。
  （Notionヒアリング: チャットボットは一問一答で仮説が出ない → **先に不足を埋める**のが差別化）
- **入力**: C1出力 + 既往の会話文脈。
- **出力**:

```json
{ "sufficient": false,
  "missing": ["現行製品", "対象拠点数"],
  "followup_question": "現行の製品と、対象の拠点数を教えてください" }
```

- **ルール**: 逆質問は**まとめて1回**（往復を増やさない）。`sufficient=true` になるまで先へ進めない。

### C3. 埋め込み生成（埋め込みモデル）

- **目的**: 質問・過去QA・案件・文書・人の自己紹介をベクトル化し、意味検索を可能にする。
- **採用モデル**: `nvidia/Nemotron-3-Embed-1B-BF16`（2048次元）。**実測で選定済み**（#61 /
  ADR-0002：層2 R@3 = 0.615 で5本中1位）。退避先は Apache-2.0 の `Qwen3-Embedding-0.6B`。ローカル実行。
- **入力**: テキスト（クエリ/パッセージ）。**出力**: 固定次元ベクトル。
- **注意**: モデルによって `query:` / `passage:` プレフィックス要否が異なる。索引時と検索時で一致させる。

### C4. ハイブリッド検索（決定的：Dense + BM25 + RRF）

- **目的**: クエリに近い「過去QA」「社内文書」「人」を高速に絞り込む。
- **構成**: Dense(pgvector) と Sparse(BM25 + SudachiPy) を **重み付き RRF(k=60)** で統合
  （`score(d)=Σ_r w_r/(k+rank_r(d))`）。型番・社内用語（例「たよれーる」「SPR」）は BM25 が拾う。
- **重み**: dense チャネル=1.0、**BM25=`bm25_weight`（既定 0.2、#68）**。等重み RRF は評価セット v2
  （症状語クエリ）で BM25 の弱い順位を dense と同格に扱い層2 R@3 が -0.170 だったため下げた。
  BM25 は消さない（型番・製品名で効く）。dense 信号強度に応じた適応重みは #114。
- **入力**: query vector + 生クエリ（BM25用トークン）。
- **出力**:

```json
{ "past_answers": [{ "qa_id": "...", "score": 0.62, "responder_id": "E017" }],
  "documents":   [{ "doc_id": "...", "score": 0.31 }],
  "candidate_people": ["E017", "E042", "E103"] }
```

### C5. 解決経路の判定（決定的：確信度 × 閾値）

- **目的**: 「過去回答で足りるか」「文書で場所を示せるか」「人に取り次ぐか」を選ぶ。
  **既定の落とし先は常に主線（人）**（14_新規性の位置づけ）。
- **入力**: C4の各スコア。**出力**:

```json
{ "route": "person",           // person(主線) / prior_answer(補助) / document(格下げ)
  "reason": "移行判断は現場状況に依存",
  "confidence": 0.78 }
```

- **ルール**: 補助・文書経路は確信度が閾値未満なら採用せず **person** にフォールバック。
  経路の閾値は評価セットで決める。

### C6. 専門性スコアラー（決定的：証拠積み上げ）

- **目的**: 「誰が詳しいか」を、行動痕跡（回答・案件・資格）から**推定して順位付け**し、根拠を出す。
  詳細な式・エッジ・成長は 15_専門性推定とグラフ成長。
- **入力**: 質問トピック + 候補者。**出力**:

```json
{ "recommendations": [
    { "person_id": "E017", "name": "高梨 健太", "dept": "技術部",
      "score": 0.82, "confidence": "高",
      "reasons": [
        { "type": "cert",    "detail": "情報処理安全確保支援士" },
        { "type": "answers", "detail": "類似の質問に過去5件回答" },
        { "type": "project", "detail": "直近3か月に同種案件を2件担当" },
        { "type": "load",    "detail": "今週の対応件数: 少なめ" } ] } ] }
```

- **式（要点）**: `score = w1·topic_fit + w2·recency + w3·answer_quality + w4·proximity − w5·load`。
  各項の寄与をそのまま UI の「選ばれた理由」に出す（説明可能性＝誤推薦対策）。

### C7. 依頼文の下書き生成（LLM）

- **目的**: 相手の職種・関係性に合わせ、**必須項目が埋まった**依頼文を作る。
  （Notionヒアリング: 承認は階層的、呼称ルールが難しい → 失礼のない文面の負担を消す。
  受け手の「3往復してやっと本題」を無くす＝聞かれる側のための機能）
- **入力**:

```json
{ "asker": {...}, "responder": { "name":"高梨", "dept":"技術部" },
  "question": "...", "required_fields": ["現行製品","拠点数","希望時期"],
  "known_values": { "現行製品":"A社UTM","拠点数":"3拠点","希望時期":"10月" } }
```

- **出力**: 依頼文テキスト（ユーザーが編集可能）。**制約**: 事実を創作しない・敬体・簡潔。

### C8. 専門性グラフ更新（決定的：オンライン更新）

- **目的**: 1回のやり取りの結果でエッジ重みを増分更新し、**使うほど精度を上げる**。
- **入力（イベント）**:

```json
{ "topic": "UTM", "responder_id": "E017",
  "outcome": "answered_helpful",   // answered / answered_helpful / declined / redirected / reused
  "redirect_to": null }
```

- **作用（要点。詳細は 15_専門性推定とグラフ成長）**:

```mermaid
flowchart LR
  EV["やり取りの結果イベント"] --> K{"outcome?"}
  K -->|answered / helpful / reused| POS["専門性エッジに正の証拠を加算<br/>（helpful は最強）"]
  K -->|declined| AV["余裕度のみ低下<br/>専門性は下げない（断り≠非専門）"]
  K -->|redirected| RD["転送先に弱い＋ ／ 本人に弱い −"]
  POS --> UP["エッジ weight・confidence を更新"]
  AV --> UP
  RD --> UP
  UP --> BETTER["次回の推薦が改善<br/>＝使うほど適切に"]
```

- **出力**: 更新後の `person_topic_edges`（weight, confidence, evidence_count）。

---

## 3. モデル一覧（アーキテクチャ）

### システム構成図

```mermaid
flowchart TB
  subgraph Client["クライアント"]
    FE["フロント（Next.js）<br/>質問入力 / 結果 / SSEで思考過程表示"]
  end

  subgraph Server["サーバ（FastAPI）"]
    API["API層<br/>POST /ask ・ GET /events"]
    AG["LangGraph StateGraph<br/>ノード C1..C8 / 条件付きエッジ<br/>checkpointer=Postgres"]
    subgraph Det["決定的ノード"]
      C4d["C4 検索<br/>Dense+BM25+RRF"]
      C5d["C5 経路判定"]
      C6d["C6 専門性スコアラー"]
      C8d["C8 グラフ更新"]
    end
  end

  subgraph AIrt["AI実行基盤（ローカル / GPU）"]
    LLM["生成LLM（LangChain 経由）<br/>C1 意図・C2 逆質問・C7 下書き"]
    EMB["C3 埋め込みモデル"]
    ASR["音声認識 faster-whisper（任意）"]
  end

  DB[("PostgreSQL + pgvector<br/>人 / 資格 / 案件 / 回答 / 文書 / 専門性グラフ")]
  CL["Claude API<br/>フォールバック・品質比較"]

  FE <-->|REST + SSE| API
  API --> AG
  AG --> C4d
  AG --> C5d
  AG --> C6d
  AG --> C8d
  AG <-->|推論| LLM
  AG -->|ベクトル化| EMB
  C4d <--> DB
  C6d <--> DB
  C8d --> DB
  LLM -.->|環境不調・品質不足時| CL
  ASR -.->|音声→テキスト| API
```

> **顧客・社員に関わる推論はローカルで完結**（顧客情報を社外AIに出さない）。Claude はフォールバック限定。

### モデル一覧

| 用途 | 種別 | 採用（候補） | 実行場所 | 代替 |
| --- | --- | --- | --- | --- |
| **オーケストレーション** | フレームワーク | **LangGraph（StateGraph）** | サーバ | — |
| **LLM 接続 / 構造化出力** | フレームワーク | **LangChain**（`init_chat_model` / `with_structured_output`） | サーバ | — |
| **状態永続化 / メモリ** | チェックポイント | **langgraph-checkpoint-postgres（PostgresSaver）** | サーバ（既存DB） | InMemorySaver（開発初期） |
| 意図理解 / 逆質問 / 下書き（C1,C2,C7） | 生成LLM | ローカルLLM 20〜30B級（DGX Sparkに載る最大） | ローカル（GPU） | Claude API（品質不足・環境不調時のフォールバック） |
| 埋め込み（C3） | 埋め込み | **Nemotron-3-Embed-1B**（2048次元, #61/ADR-0002。退避先 Qwen3-Embedding-0.6B） | ローカル（GPU/CPU） | — |
| 検索（C4） | 決定的 | pgvector(HNSW) + BM25(SudachiPy) + RRF | サーバ | pgroonga/OpenSearch（規模拡大時） |
| 経路判定（C5） | 決定的 | 確信度×閾値ルール | サーバ | — |
| スコアラー（C6） | 決定的 | 証拠積み上げ式 | サーバ | — |
| グラフ更新（C8） | 決定的 | オンライン増分更新 | サーバ | — |
| 音声入力（任意） | 音声認識 | faster-whisper | ローカル | — |

サービング: DAY3〜5 は **Ollama**（起動最短）、DAY6〜7 で余力があれば **vLLM** に載せ替え、
前後のレイテンシ比較を「推論高速化」の実測として示す（13_技術仕様書 §3.6）。

### ローカル / クラウド境界（プライバシー）

- 顧客・社員に関わる推論（C1〜C8 の中核）は**ローカルで完結**させる方針。
  「顧客情報を社外の生成AIに出さない」という実用性の物語（00_勝ち筋サマリ）。
- Claude API はフォールバック / 開発時 / 品質比較に限定。合成データで検証する。

---

## 4. 外部API（システム全体の入出力契約）

### 1件の質問が流れるシーケンス

```mermaid
sequenceDiagram
  actor U as 聞く側（藤田）
  participant FE as フロント
  participant API as API / Agent
  participant LLM as 生成LLM
  participant DB as 検索・スコア（DB）
  actor R as 聞かれる側（高梨）

  U->>FE: 質問を入力
  FE->>API: POST /ask
  API->>LLM: C1 意図理解
  LLM-->>API: topics / situation
  API-->>FE: SSE understood
  API->>LLM: C2 充足判定
  LLM-->>API: 不足 → 逆質問
  API-->>FE: SSE followup
  U->>FE: 現行製品・拠点数を追記
  API->>DB: C4 検索 → C5 経路 → C6 スコア
  DB-->>API: 候補3名＋根拠
  API-->>FE: SSE recommend
  API->>LLM: C7 依頼文の下書き
  LLM-->>API: 依頼文
  API-->>FE: SSE draft
  U->>R: 依頼文を送信
  R-->>API: 回答 ／「今は難しい」
  API->>DB: C8 グラフ更新
  API-->>FE: SSE done
```

### リクエスト `POST /ask`

```json
{ "asker_id": "E200", "question": "…", "session_id": "…" }
```

### SSE イベント `GET /events/{session_id}`（思考過程）

**LangGraph の `graph.stream(stream_mode="updates")` が返す「ノード名→更新」を、そのまま SSE に写像**する。
逆質問は `interrupt()`、ユーザーの回答で `Command(resume=...)` により再開する。

```
（LangGraphノード更新）              →（SSEイベント）
c1_understand: {topics, situation}   → event: understood
c2_sufficiency: interrupt(逆質問)     → event: followup   （回答で resume）
c5_route: {route, confidence}         → event: route
c6_score: {recommendations}           → event: recommend
c7_draft: {draft}                     → event: draft
c8_update: {status}                   → event: done
```

```
event: understood   data: {"topics":[...],"situation":"…"}
event: followup      data: {"question":"現行製品と拠点数を教えてください"}
event: route         data: {"route":"person","reason":"…","confidence":0.78}
event: recommend     data: {"recommendations":[ … C6出力 … ]}
event: draft         data: {"draft":"高梨さん …"}
event: done          data: {"status":"sent"}
```

### レスポンス（最終）

```json
{ "route": "person",
  "recommendations": [ … ],
  "draft": "…",
  "meta": { "latency_ms": {"p50_target":1500,"p95_target":3000} } }
```

想定外入力時: `{"route":"declined","message":"業務外のため回答できません"}`（誤回答対策）。

---

## 5. プロンプト設計方針（C1・C2・C7）

| 項目 | 方針 |
| --- | --- |
| 出力形式 | **LangChain `model.with_structured_output(PydanticSchema)` で強制**。自由文で返させない（C1・C2）。スキーマは `backend/agent/schemas.py` に集約 |
| ハルシネーション抑制 | 「わからない場合は out_of_scope / sufficient=false を返す」を明示。**創作させない** |
| few-shot | 大塚商会の実商材（複合機/UTM/たのめーる/たよれーる/SPR）を例に数件 |
| 言語 | 日本語。敬体。社内用語を保持（SudachiPy mode C と整合） |
| バージョン管理 | プロンプトは `backend/agent/prompts/` でファイル管理し、変更を追える（発表素材にも） |
| 温度 | C1・C2 は低温（決定性重視）、C7 は中温（自然さ） |

---

## 6. 評価との対応（16_完成度評価 と接続）

| コンポーネント | 測る指標 | 評価項目 |
| --- | --- | --- |
| C1 意図理解 | トピック抽出の一致率 | 精度・実用性 |
| C5 経路判定 | 経路の正答率（人/過去回答の振り分け） | 自律性・工夫 |
| C6 スコアラー | Top-1精度 / Recall@3 / MRR | 精度・実用性 |
| C8 グラフ更新 | 利用0→N件での Top-1 改善曲線 | 自律性・工夫（使うほど適切に） |
| 全体 | レイテンシ p50/p95、想定外入力の降参率 | 技術完成度 / インタラクション設計 |

評価セットは社員ヒアリングの実質問（09_ヒアリング設計_人材サーチ §4）を核に40件を目標。

---

## 7. まだ決めていない事項（仕様確定時に埋める）

- ローカルLLMの具体モデル（DGX Spark 実機スペック確認後）
- 埋め込みモデルの最終選定（DAY3 実測）
- スコア式の重み `w1..w5`（評価セットで調整）
- 経路判定・確信度の閾値（評価セットで決定）
- トピックのタクソノミ語彙（大塚商会の商材体系から種を作る。15_専門性推定とグラフ成長 §1）
- LangGraph / LangChain の**固定バージョン**（着手時に context7 等で当該版のAPIを確認して pin）
- checkpointer を最初から Postgres にするか、InMemorySaver で始めて後で差し替えるか（工数次第）

> 本書は「AIとしての設計図」。実装は仕様確定後、リポジトリ（TEKIJIN）の
> `docs/specs/` に確定版を置いてから着手する。
