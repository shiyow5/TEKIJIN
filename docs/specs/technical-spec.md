# 技術仕様書 — TEKIJIN（仮称）

### MVP :　質問を入れると質問に答えられる社員をリストする
Web Tool
- バックエンド：Python（Fast API)
- フロントエンド：next.js + typescript
- 状態管理 : react
- CSS : windtail
- DBS : Postgresql
- AI :　ベクトル化　→ GPU借りる前提

---

version 0.1 / 2026-08-21 / Aチーム（3名）

---

## 1. 設計方針

| # | 方針 | 理由 | |
| --- | --- | --- | --- |
| 1 | **ランキングはLLMにやらせない** | 再現性・説明可能性・速度・評価可能性のすべてで決定的スコアが勝る。LLMは理解・生成に限定する | |
| 2 | **1本のE2Eを最優先** | 機能数より完走。DAY3中に入口から出口まで通す | |
| 3 | **ネットワーク非依存で動く構成** | ラウンドロビン45分の事故を防ぐ。ローカル推論なら自然に達成される | |
| 4 | **測れる形で作る** | 評価セット・レイテンシ計測を最初から仕込む。後付けは間に合わない | |
| 5 | **オーケストレーションは LangGraph、LLM I/O は LangChain** | 状態管理・条件分岐・ストリーミング(→SSE)・チェックポイント(→セッション/メモリ)・human-in-the-loop(→逆質問)を自前で作らず、実績あるフレームワークに載せる。ただし**推薦スコアはフレームワークに委ねず決定的関数のまま**（方針1と両立） | |

---

## 2. 全体アーキテクチャ

```
┌────────────────────────────────────────────────┐
│  ブラウザ（Next.js 15 / TypeScript / Tailwind / shadcn-ui）   │
│   質問画面 ・ 結果画面 ・ 回答画面 ・ ダッシュボード          │
└──────────────┬─────────────────────────────────┘
               │ REST（POST /ask, /answer） + SSE（GET /events/{id}）
┌──────────────▼─────────────────────────────────┐
│  API層  FastAPI + Pydantic                                   │
│   入出力の検証 / セッション管理 / SSE配信                    │
└──────────────┬─────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────┐
│  Router Agent = LangGraph StateGraph（ノード=C1..C8）        │
│                                                              │
│   C1 意図理解・トピック抽出   → LLM(with_structured_output)  │
│   C2 情報充足チェック → 不足なら interrupt() で逆質問        │
│   C3/C4 埋め込み＋検索         → Retrieval（決定的ノード）    │
│   C5 経路判定（条件付きエッジ add_conditional_edges）        │
│       ├ 補助: 過去に"誰が"答えたか（人の証拠として提示）     │
│       ├（格下げ）文書に明確な記載時のみ"場所"を指す         │
│       └ 主線: 人の推薦 → C6 Scorer（決定的ノード）          │
│   C7 依頼文の下書き生成       → LLM                          │
│      送信 → 断り検知 → 条件付きエッジで次候補へ再ルーティング │
│   C8 回答を専門性グラフへ取り込み（索引 + 推定の更新）       │
│                                                              │
│   graph.stream(stream_mode="updates") の各ノード更新を       │
│   SSE で逐次配信（＝思考過程の可視化）                       │
│   checkpointer(Postgres) で thread_id=セッションを永続化     │
└───┬────────────────────────┬─────────────────┘
    │                            │
┌───▼──────────────┐  ┌────▼──────────────────┐
│  Retrieval 層              │  │  Scorer（決定的・説明可能）    │
│   ・Dense: pgvector(HNSW)  │  │   適合度 × 余裕度              │
│   ・Sparse: BM25(SudachiPy)│  │   各項の寄与を返す             │
│   ・RRF で統合             │  │   → UIの「推薦理由」に直結     │
│  （LangGraph ノードとして実装。中身は決定的）                │
└───┬──────────────┘  └────┬──────────────────┘
    │                            │
┌───▼────────────────────────▼─────────────────┐
│  PostgreSQL 16 + pgvector                                    │
│   employees / certifications / skills / projects             │
│   questions / answers / recommendations / documents          │
│   events（レイテンシ・経路の記録）                           │
│   + LangGraph checkpoints（langgraph-checkpoint-postgres）    │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│  LLM 接続 = LangChain（統一インターフェース）                │
│   主: init_chat_model("openai:<model>", base_url=vLLM) … DGX  │
│        （vLLM が OpenAI互換 /v1。DGX Spark 上・Tailscale 経由）│
│   副: ChatAnthropic（Claude, フォールバック / 品質比較）      │
│   構造化: model.with_structured_output(PydanticSchema)（C1/C2）│
│  Embedding: langchain-huggingface（日本語埋め込み・ローカル） │
└──────────────────────────────────────────────┘
```

---

## 3. 技術選定と根拠

### 3.1 フロントエンド

| 項目 | 採用 | 根拠 | 代替 |
| --- | --- | --- | --- |
| フレームワーク | **Next.js（App Router）+ TypeScript** | ラウンドロビンで審査員が触る。反応速度と見栄えが評価に直結する。SSEの受信も標準APIで書ける | Streamlit（実装は速いが、触られるデモの質で劣る） |
| スタイル | **Tailwind CSS + shadcn/ui** | 短時間で設計の完成度が出る。コンポーネントをコピーして使う方式なので依存が増えない | 自前CSS |
| 状態管理 | **React の useState / useReducer のみ** | 画面が4つ。状態管理ライブラリを入れる規模ではない | Zustand |

### 3.2 バックエンド

| 項目 | 採用 | 根拠 |
| --- | --- | --- |
| 言語 | **Python 3.12** | 埋め込み・検索・評価・LangChain/LangGraph のライブラリが全てPython側にある |
| フレームワーク | **FastAPI** | 型定義から自動でスキーマ検証。SSEは `StreamingResponse` で標準実装できる |
| エージェント | **LangGraph** | StateGraph でノード/分岐/ストリーミング/永続化/human-in-the-loop（§3.7） |
| LLM 接続 | **LangChain**（`langchain`, `langchain-openai`, `langchain-anthropic`） | `init_chat_model("openai:…", base_url=vLLM)` で vLLM(OpenAI互換)に接続。接続先を1行で差し替え。`with_structured_output` |
| 検証 | **Pydantic v2** | 入力を境界で検証。LangChain の構造化出力スキーマも Pydantic で共通化 |
| 非同期 | **asyncio** | LLM呼び出しと検索を並行実行してレイテンシを削る |

**主な追加依存（requirements）**: `langgraph`, `langgraph-checkpoint-postgres`,
`langchain`, `langchain-openai`（vLLM/OpenAI互換）, `langchain-anthropic`, `langchain-huggingface`（埋め込み）。
**バージョンは固定**する（LangGraph/LangChain はAPI変化が速いため。§10 リスク）。

### 3.3 データベース

| 項目 | 採用 | 根拠 |
| --- | --- | --- |
| DB | **PostgreSQL 16 + pgvector** | **リレーショナル（社員・案件・回答の関係）とベクトル検索を1つのDBで扱える。** 別途ベクタDBを建てるとデータ同期の手間が発生する。pgvector は HNSW インデックスに対応しており、この規模なら十分速い |
| ORM | **SQLAlchemy 2.0** | 型付き。生SQLも書ける |
| 起動 | **Docker Compose** | 3人が同じ環境で動かせる。ローカル完結 |

> **なぜ Chroma / Qdrant ではないか**: 社員・案件・回答の**関係**を辿る必要がある
> （「この人が答えた質問のトピック」「この案件の担当者」）。
> 専用ベクタDBだとこの結合を毎回アプリ側で書くことになる。PostgreSQL 1本の方が総コストが低い。

### 3.4 検索

| 項目 | 採用 | 根拠 |
| --- | --- | --- |
| Dense | **pgvector + HNSW** | 意味の近い質問を拾う |
| Sparse | **BM25（SudachiPy + rank_bm25、インメモリ）** | **型番・製品名・社内用語はベクトル検索が苦手。**「RX-3000の見積」のような固有名詞は語彙一致で拾う必要がある |
| 統合 | **重み付き RRF（Reciprocal Rank Fusion, k=60）** | スコアのスケールが違う2系統を順位だけで統合。`score(d)=Σ_r w_r/(k+rank_r(d))`。dense=1.0・**BM25=0.2（#68）**：等重みは症状語クエリで BM25 の弱い順位が dense を汚し層2 R@3 -0.170 だったため（production 整合ハーネスで再測）。適応重みは #114 |
| 形態素解析 | **SudachiPy（mode C）** | 日本語のBM25には分かち書きが必須。複合語を保持する mode C が社内用語に向く |

> **なぜ BM25 をインメモリで持つか**: 本デモのデータ量は数百件。全件スコアリングでも数ミリ秒で終わる。
> Elasticsearch や pgroonga を立てるのは、この規模ではオーバースペック。
> **「将来はデータ量に応じて pgroonga / OpenSearch に置換可能」と設計上の逃げ道を示せば、技術選定の合理性として説明できる。**

### 3.5 埋め込みモデル

**実測で選定済み（#61 / ADR-0002）。** DGX 実機・評価セット v2 で 5 本を横並び比較し、
主指標の層2 Recall@3 が最良だった Nemotron-3-Embed-1B を採用した。

| 項目 | 採用 | 根拠 |
| --- | --- | --- |
| 採用 | **`nvidia/Nemotron-3-Embed-1B-BF16`**（2048次元） | 層2 R@3 = 0.615 で5本中1位（e5-large 0.530 比 +16%）。`query:` / `passage:` プレフィックスは e5 と同じ。ライセンスは NVIDIA Open Model License（ADR-0002 で判断） |
| 次点 / 退避先 | **`Qwen3-Embedding-0.6B`**（1024次元, Apache-2.0） | 精度はほぼ同等（0.533）。ライセンス制約でNemotronが使えない場合の退避先 |
| 実行 | sentence-transformers（ローカル） | 外部API不要。ネットワーク非依存の要件を満たす |

> 数字と再現方法は `docs/benchmarks/README.md`、判断は `docs/adr/0002-embedding-model-nemotron.md`。
> 「測って選んだ」記録そのものが技術完成度の評価材料になる。
> 2048次元のため、将来 HNSW/ivfflat の ANN 索引を張る際は `halfvec` への移行が要る（#101）。

### 3.6 LLM（接続は LangChain で統一）

すべてのチャットモデルは **LangChain の `init_chat_model` / `ChatAnthropic`** で扱い、
接続先（ローカル/クラウド）を1行で差し替えられるようにする。構造化出力は
**`model.with_structured_output(PydanticSchema)`** に統一（後段のプロンプト方針と一致）。

```python
from langchain.chat_models import init_chat_model
# vLLM は OpenAI 互換 /v1。base_url を DGX Spark の vLLM に向ける（Tailscale 経由）
llm = init_chat_model("openai:<model>", base_url="http://internship-dgx1:8080/v1",
                      api_key="dummy", temperature=0.1)
llm_c1 = llm.with_structured_output(IntentSchema)            # C1 は JSON 固定
# フォールバック
from langchain_anthropic import ChatAnthropic
cloud = ChatAnthropic(model="claude-...", temperature=0.1)
```

| 用途 | 採用 | 根拠 |
| --- | --- | --- |
| 意図理解・トピック抽出（C1） | ローカルLLM + `with_structured_output` | 出力がPydanticに固定できるので小さいモデルで足りる。レイテンシが効く |
| 逆質問の生成（C2） | ローカルLLM + 構造化出力 | 同上 |
| 依頼文の下書き（C7） | ローカルLLM、品質不足なら Claude | 文章の自然さが最も要求される箇所。LangChain なので切替は1行 |
| 開発支援 | **Claude Code**（提供あり） | 実装速度 |

**サービング**

| 段階 | 採用 | 根拠 |
| --- | --- | --- |
| 既定 | **vLLM（OpenAI 互換 /v1）** | DGX Spark の配布環境が既に vLLM でモデルを配信（`internship-dgx1`、Tailscale 経由）。OpenAI 互換なので LangChain の `init_chat_model("openai:…", base_url=…)` で接続でき、載せ替え工程が不要 |
| 代替 | Ollama / Claude API | vLLM が使えない場合のフォールバック。Ollama はローカル起動が最短、Claude は品質比較・環境不調時に使用 |

> MAC資料 p.11 に「NVIDIA GPU × 1 / Team」「モデルの動作や**推論高速化**まで理解する」と明記がある。
> vLLM（連続バッチング）でのスループット / p95 の実測が、この「推論高速化」要求への直接の回答になる。**やる価値が高い。**

**モデル選定**: DGX Spark の実機スペックを 確認し、載る範囲で最大のものを選ぶ。
20B〜30B級のオープンモデルが目安。**確認するまで確定させない。**

### 3.7 エージェント実装（LangGraph）

エージェントは **LangGraph の `StateGraph`** で組む。ノード = C1〜C8、分岐 = 条件付きエッジ。

| 項目 | 採用 | 根拠 |
| --- | --- | --- |
| オーケストレーション | **LangGraph `StateGraph`** | 状態管理・条件分岐・ストリーミング・永続化・human-in-the-loop が最初から揃う。自前実装の車輪の再発明を避ける |
| 状態定義 | `TypedDict`（`State`） | 各ノードは `dict` を返して部分更新。型で状態を保証 |
| 分岐 | `add_conditional_edges` | out_of_scope / 充足 / 経路(person/prior_answer/document) / 断り再ルーティング を宣言的に表現 |
| 逆質問（C2）・送信待ち | **`interrupt()` + `Command(resume=...)`** | human-in-the-loop を標準機能で。聞き返し／回答到着で再開 |
| 思考過程の可視化 | `graph.stream(stream_mode="updates")` | 各ノードの更新をそのまま SSE イベントに写像（17_AIモデル定義 §4） |
| 永続化 / セッション | **`langgraph-checkpoint-postgres`（PostgresSaver）** | `thread_id`=セッション。会話の中断再開・履歴を Postgres に集約（DBを増やさない） |
| LLM ノードの出力 | LangChain `with_structured_output(Pydantic)` | C1/C2 はスキーマ強制。関数呼び出しの実装差に依存しない |
| 推薦スコア（C6） | **決定的関数のまま（LangGraph ノード内）** | 方針1を堅持。LangGraph は"並べ替え"を担わない |

グラフ定義のスケッチ:

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver

g = StateGraph(State)
g.add_node("c1_understand", c1_understand)     # LLM(構造化)
g.add_node("c2_sufficiency", c2_sufficiency)   # LLM + interrupt()
g.add_node("c4_retrieve", c4_retrieve)         # 決定的（Dense+BM25+RRF）
g.add_node("c5_route", c5_route)               # 決定的
g.add_node("c6_score", c6_score)               # 決定的（説明可能）
g.add_node("c7_draft", c7_draft)               # LLM
g.add_node("c8_update", c8_update)             # 決定的（グラフ更新）
g.add_edge(START, "c1_understand")
g.add_conditional_edges("c1_understand", lambda s: "end" if s["out_of_scope"] else "c2")
g.add_conditional_edges("c2_sufficiency", lambda s: "c4" if s["sufficient"] else "c2")
g.add_conditional_edges("c5_route", route_selector)   # person / prior_answer / document
g.add_edge("c6_score", "c7_draft")
g.add_edge("c7_draft", "c8_update")
g.add_edge("c8_update", END)
graph = g.compile(checkpointer=PostgresSaver(...))
```

> 「なぜ LangGraph を使うのか」は審査で聞かれうる。
> **「状態・分岐・ストリーミング(→SSE)・中断再開(→逆質問)・永続化(→メモリ)を"実現できる体験"の
> 中核として使い、一方で推薦の並べ替えはあえてフレームワークに委ねず決定的にした」**
> と、採用と非採用の線引きを説明できれば加点になる。

### 3.8 その他

| 項目 | 採用 | 根拠 |
| --- | --- | --- |
| 音声入力 | **faster-whisper**（余力があれば） | 外出中の営業を想定。ローカル実行 |
| 計測 | 各ステージの経過時間をDBに記録 | p50/p95 を出すため。**最初から仕込む** |
| 評価 | 自作スクリプト（pytest） | Top-1 / Recall@3 / MRR / 経路判定精度 |
| 実行環境 | Docker Compose | 3人で同一環境。ローカル完結 |

---

## 4. データモデル

```sql
-- 人
employees(id, name, dept, role, branch, years, self_intro, embedding vector)
certifications(id, employee_id, name, acquired_at)        -- 資格。最も確実なデータ源
skills(id, employee_id, topic, level, source)             -- 自己申告 / 推定
projects(id, industry, products[], period)                -- 案件
project_members(project_id, employee_id, role)

-- 質問と回答
questions(id, asker_id, body, topics[], status, created_at, embedding vector)
answers(id, question_id, responder_id, body, created_at, embedding vector,
        reuse_count, was_helpful)
recommendations(id, question_id, employee_id, rank, score, reasons jsonb,
                outcome)                                   -- accepted / declined / timeout

-- 文書
documents(id, title, body, source, updated_at, embedding vector)

-- 計測
events(id, question_id, stage, started_at, ended_at, meta jsonb)

-- LangGraph の状態永続化（PostgresSaver が自動作成・管理）
-- checkpoints / checkpoint_writes / checkpoint_blobs
--   thread_id = セッション。会話の中断再開・履歴に使う（手書きしない）
```

**`recommendations.outcome` が学習の要。**
「推薦した → 受けてもらえた／断られた」が溜まることで、次の推薦が改善する。
これが評価項目「利用を重ねることで、より適切な支援ができる設計」の実体になる。

---

## 5. 推薦アルゴリズム

### スコア式

```
score(e, q) = w1·topic_fit(e,q)
            + w2·recency(e,q)
            + w3·answer_quality(e)
            + w4·proximity(e, asker)
            − w5·load(e)
```

| 項 | 内容 | データ源 |
| --- | --- | --- |
| `topic_fit` | 質問トピックとの適合。資格・自己申告・案件・過去回答を統合したRRFスコア | certifications / skills / projects / answers |
| `recency` | 直近の経験ほど重い。半減期6か月の時間減衰 | projects / answers |
| `answer_quality` | 過去回答の有用度と再利用数 | answers |
| `proximity` | 同支店 \> 同エリア \> 全社 | employees.branch |
| `load` | **直近7日の推薦・回答件数。多いほど減点** | recommendations / answers |

### 設計上の要点

1. **`load` の項が、リスク②（負荷集中）への回答そのもの。**
   「偏りを作らないことがアルゴリズムに書いてある」と言える。
2. **各項の寄与を返す。** UIの推薦理由はこの分解をそのまま表示する。
   → 評価項目「誤回答への対策」を、説明可能性で満たす。
3. **重みは評価セットで調整する。** 手で決めた値をそのまま出さない。

### コールドスタート

| 段階 | 主に効く項 |
| --- | --- |
| 初期（ログ0件） | `topic_fit`（資格・自己申告） |
| 利用が進む | `answer_quality`, `recency`（回答実績） |

**デモでは、ログ0件と30件の Top-1 精度を並べて見せる。** 学習性が主張ではなく実測になる。

---

## 6. 誤回答・想定外入力への対策

**評価項目に明文化されている箇所。実装で答える。**

| 対策 | 実装 |
| --- | --- |
| **出典の提示** | 補助経路は回答者と日付、主線はスコアの内訳を必ず表示（格下げの文書引用は文書名と該当箇所） |
| **確信度と閾値** | 補助経路は検索スコアが閾値未満なら採用せず主線（人）へ。閾値は評価セットで決める |
| **わからないと言う** | 補助経路の確信度が低ければ、答えを作らず人に回す。**人への取次ぎ（主線）が常にフォールバック先になる構造** |
| **情報不足の検知** | S2で必須項目の充足を判定。不足なら**逆質問**して先に進まない |
| **想定外入力の分類** | 業務外・空入力・悪意ある入力をS1で分類し、受け流す |
| **推薦を外した時の回復** | 「今は難しい」で次候補へ自動再ルーティング。行き止まりにしない |

> **デモで意図的に壊しにいく。** 「変な質問を打ってみてください」と審査員に促し、
> システムが正しく降参する様子を見せる。ここで差がつく。

---

## 7. 評価計画

### 評価セット

| 種類 | 件数 | 作り方 |
| --- | --- | --- |
| 質問サンプル | 40件 | **社員ヒアリングで実際の質問を収集**（09_ヒアリング設計_人材サーチ §4）+ 合成で補完 |
| 正解ラベル（担当者） | 40件 | 職種・部署の粒度。実名は集めない |
| 正解ラベル（分岐） | 40件 | A / B / C のどれで解決すべきか |

### 指標

| 指標 | 目標 | 意味 |
| --- | --- | --- |
| Top-1 Accuracy | 70% | 1位に正しい人が来る割合 |
| Recall@3 | 90% | 3名の中に正解がいる割合 |
| MRR | 0.75 | 正解の順位の質 |
| 分岐判定精度 | 80% | A/B/Cの振り分けの正解率 |
| 上位1名集中率 | 素朴方式の半分以下 | 100件流して比較 |
| レイテンシ p50 / p95 | 1.5秒 / 3秒（初回表示） | ステージ別に記録 |

### 計測の仕込み

`events` テーブルに全ステージの開始・終了時刻を記録する。**DAY3の骨組みの時点で入れる。**
後から足すと、計測のためにコードを触ることになり、DAY7の凍結後に不整合が出る。

---

## 8. 合成データ設計

実データは入手できない。**合成であることは正直に説明する。** リアリティは職種比率で担保する。

| データ | 件数 | 設計 |
| --- | --- | --- |
| 社員 | 40名 | MAC資料の職種比率に寄せる（技術38.7% / 営業32.0% / スタッフ27.7%）。拠点は3〜4箇所 |
| 資格 | 100件 | MAC資料の推奨資格リストから（情報処理系、中小企業診断士、日商簿記、G検定・E資格 など） |
| 案件 | 120件 | 大塚商会の実商材で構成（複合機、PC、ネットワーク、セキュリティ、たのめーる、たよれーる） |
| 過去QA | 150件 | 補助経路（誰が答えたか）と専門性推定の燃料。**質と多様性が体験と推薦精度を決めるので、ここに時間を使う** |
| 社内文書 | 30件 | 格下げ経路用。件数を減らす。実装が押したら未使用でも可 |
| 評価用質問 | 40件 | ヒアリング由来を優先 |

生成は Claude Code で行い、**人が目視で確認する。** 生成物をそのまま使うと不自然さがデモで露呈する。

---

## 9. 実装計画

| Day | 目標 | 完了条件 |
| --- | --- | --- |
| **DAY2 夕方**（3h） | 環境構築とスキーマ | Docker Compose が起動。テーブル作成完了。GitHub リポジトリ共有済み |
| **DAY3** | **E2E疎通** + データ生成 | 質問を打つと（中身が仮でも）候補3名と下書きが返る。合成データ完成 |
| **DAY4** | 検索層とスコアラー | ハイブリッド検索が動く。スコアの内訳がAPIから返る。評価セット着手 |
| **DAY5** | エージェントループとUI / **中間発表** | 主線（人への取次ぎ）と補助（誰が答えたか）が動く。思考過程がSSEで流れる。文書引用は未実装で可。中間発表でFB回収 |
| **DAY6** | 逆質問・断り導線・ナレッジ化・ダッシュボード | F-09〜F-12 完了。中間FBの反映 |
| **DAY7** | 計測と仕上げ / **17:00 機能凍結** | 評価指標の実測完了。vLLM載せ替え判断。デモ練習3周 |
| **DAY8** | 発表 5分 / ラウンドロビン 45分 | — |

### 期限の意味

- **DAY3 で E2E が通らなければ、機能を削る。** 中身は後から入れ替えられる。骨は後から通せない
- **DAY7 17:00 以降は一切の機能追加をしない。** 最終日の朝に足すと必ず壊れる

### 役割分担（案。スキルに応じて調整）

| 担当 | 範囲 |
| --- | --- |
| 佐藤 丞 | PM / コンセプト / LangGraph グラフ（C1〜C8）/ LLM 接続 / プレゼン |
| ティンザー アウー | データモデル / 検索層 / スコアラー / 合成データ / 評価 |
| 森田 怜央名 | フロント全般 / SSEの受信 / ダッシュボード / デモ導線 |

**全員がデモを回せるようにしておく。** 45分を1人で回すと声が持たない。

---

## 10. リスクと代替案

| リスク | 兆候 | 代替案 | 判断期限 |
| --- | --- | --- | --- |
| **DGX Spark の環境構築が進まない** | ドライバ・CUDA・vLLM がARM環境で嵌る | **Ollama のみに絞る。** それも駄目なら Claude API に切り替え、GPUは埋め込み計算だけに使う | **DAY3 午前** |
| Next.js の構築が重い | 半日経っても画面が出ない | Streamlit に切り替え | DAY3 午前 |
| ローカルLLMの日本語生成が弱い | 下書きが不自然 | 下書き生成のみ Claude API に振る（ハイブリッド構成） | DAY5 |
| 過去QAの合成データが不自然 | 補助経路と専門性推定が白ける | 件数を減らし、質の高い30件に集中 | DAY4 |
| 評価セットが集まらない | ヒアリングでサンプルが取れない | 合成で40件作る。**その旨を正直に明記する** | DAY4 |
| **LangGraph / LangChain の学習・バージョン差でハマる** | interrupt や PostgresSaver が期待通り動かない、API が資料と違う | まず最小グラフ（C1→C4→C6→C7 直列）を通す。checkpointer は InMemorySaver で始め、余裕があれば Postgres に。**バージョンを requirements で固定**し、context7 等で当該版のAPIを確認 | DAY3 |
| 推薦精度が上がらない | Top-1 が50%を切る | Recall@3 を主指標に切り替え、**3名提示の設計上の妥当性**として説明する | DAY6 |

> **DGX Spark は最大のリスク。** GB10（Grace Blackwell）はARM系のため、
> ライブラリによっては x86 前提の手順が通らないことがある。
> **DAY2 のうちに実機で「モデルが1つ動く」ところまで確認すること。**
> 動かなければ即座に代替案へ。ここで粘ると3日溶ける。

---

## 11. リポジトリ構成（案）

```
tazuneru/
├─ docker-compose.yml
├─ README.md
├─ backend/
│   ├─ app/
│   │   ├─ main.py              FastAPI エントリ
│   │   ├─ api/                 ルーティング（ask / answer / events / dashboard）
│   │   ├─ agent/               LangGraph
│   │   │   ├─ state.py         State（TypedDict）
│   │   │   ├─ graph.py         StateGraph 構築（add_node/add_conditional_edges/compile）
│   │   │   ├─ nodes/           C1..C8 を1ノード=1ファイルで
│   │   │   ├─ schemas.py       with_structured_output 用 Pydantic スキーマ
│   │   │   └─ prompts/         プロンプト（バージョン管理する）
│   │   ├─ retrieval/           LangGraph ノードから呼ぶ（中身は決定的）
│   │   │   ├─ dense.py         pgvector
│   │   │   ├─ sparse.py        BM25 + SudachiPy
│   │   │   └─ fusion.py        RRF
│   │   ├─ scorer/              推薦スコア（決定的）
│   │   ├─ llm/                 init_chat_model / ChatAnthropic のラッパ
│   │   └─ models/              SQLAlchemy
│   ├─ eval/
│   │   ├─ dataset/             評価セット
│   │   └─ run_eval.py          Top-1 / Recall@3 / MRR / 分岐精度
│   └─ seed/                    合成データ生成
├─ frontend/
│   └─ app/                     Next.js App Router
└─ docs/
    ├─ architecture.md
    └─ decisions.md             技術判断の記録（発表で使う）
```

> **`docs/decisions.md` を書きながら進めること。**
> 「なぜその技術を選んだか」を後から思い出すのは難しい。
> 5分プレゼンの「②技術アーキテクチャ」は、このファイルから作る。

---

## 12. 未確定事項

| # | 確認事項 | 相手 | 影響 |
| --- | --- | --- | --- |
| 1 | DGX Spark の実機スペックと環境（OS / CUDA / 使えるサービング基盤） | 運営・メンター | LLM選定とサービング方式 |
| 2 | Claude Pro で API 経由の利用が可能か。個人のAPIキーを使ってよいか | 運営 | フォールバック構成 |
| 3 | 「誰が何に詳しいか」を推測できるデータの実在（QD-01〜05） | メンター | スコア式の重み配分 |
| 4 | 既存 AIアシスタントに人検索機能があるか（QD-07） | メンター | **かぶり回避。最優先** |
| 5 | 社員が実際に社内でする質問のサンプル | ランチ・ヒアリング | 評価セットの質 |
