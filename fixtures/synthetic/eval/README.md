# eval/ — 評価セット（eval_queries）

推薦精度と経路判定精度を計測するための正解ラベル付きクエリ集。
`docs/specs/technical-spec.md` §7「評価計画」に対応する。

生成: [`scripts/build_eval.py`](../../../scripts/build_eval.py)（`random.seed(42)` で再現可能）。
`fixtures/synthetic/` の合成データ（#23 で生成）を入力に再作成する。

```bash
python3 scripts/build_eval.py
```

---

## `eval_queries.json` のスキーマ（40件・JSON配列）

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | int | 連番（1〜40） |
| `query` | string | 自然文の質問（トピックに関する現実的な相談文）。実在の固有名詞は含まない |
| `topics` | string[] | 質問トピック（実在の22トピック語彙内、各1件） |
| `correct_experts` | int[] | そのトピックの専門家 `employee_id`（2〜4名） |
| `route` | string | 正解経路ラベル: `person`（主線）/ `prior_answer`（補助）/ `document`（格下げ） |

---

## `correct_experts` の導出方法

**`answers/answers.json` を `topic` でグルーピングし、そのトピックの `responder_id` 集合を専門家とする。**
過去に実際にそのトピックへ回答した人＝行動裏付けのある専門性、という考え方。

1. answers を topic ごとに集計し、responder_id を「回答実績の多い順 → id 昇順」で並べる。
2. 上位最大4名を `correct_experts` とする（各トピックの実 responder は2〜4名に収まる）。

> #23 の過去QA生成時、`answers.responder_id` は各トピックの上位専門家（projects/daily の
> 行動データから推定）から選ばれている。そのため answers→topic→responder の復元が、
> そのまま「行動データに裏打ちされた correct_experts」になる。

---

## route ラベルの設計（経路判定精度用）

| route | 意味 | 件数 | クエリの性質 |
|---|---|---|---|
| `person` | 主線: 人に取り次ぐのが適切 | 24 | 現場判断が要る相談。既定のフォールバック先 |
| `prior_answer` | 補助: 過去回答の提示で足りる | 10 | そのトピックに近い過去回答が明確に存在する相談 |
| `document` | 格下げ: 文書の場所を指す | 6 | 手順書・FAQ（`documents/documents.json`）に記載がある種類の質問 |

主線（person）を多めにしている（`technical-spec.md` §6「人への取次ぎが常にフォールバック」）。
`document` ラベルは `documents/documents.json` が実際に扱うトピックにのみ付与している。

---

## 想定指標（technical-spec §7）

| 指標 | 目標 | 対象 |
|---|---|---|
| Top-1 Accuracy | 70% | `correct_experts` の1位一致 |
| Recall@3 | 90% | 上位3名に `correct_experts` が含まれる割合 |
| MRR | 0.75 | 正解の順位の質 |
| 経路判定精度 | 80% | `route`（person/prior_answer/document）の一致率 |

---

## 注意

- **合成データのみ。** 実在の社員・顧客・案件・固有名詞は含まない。実データは受領しない前提。
- `correct_experts` は `employee_id`（`people/employees.json` の `id`）で表現し、実名ラベルは持たない。
- クエリ本文はトピック主題＋経路別の言い回しで生成したテンプレートベース。表現の多様性は限定的で、
  ロジック検証を主目的とする（自然言語の頑健性評価には別途より自然な文面が要る）。
