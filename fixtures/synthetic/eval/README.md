# eval/ — 評価セット v2

推薦精度・検索精度・異常系対応を計測するための正解ラベル付きクエリ集。
`docs/specs/technical-spec.md` §7「評価計画」に対応する。

**設計の背景と限界は [`fixtures/synthetic/README.md`](../README.md) の「評価セット（v2 / Issue #43）」節に集約してある。**
ここではファイルの構成と使い方だけを書く。

```bash
python3 scripts/build_eval_v2.py      # 評価セットを生成（random.seed(42) で再現可能）
python3 scripts/eval_baselines.py     # ベースライン4本を測る
python3 scripts/eval_label_agreement.py  # 自動 gold と人手ラベルの一致度を測る
```

---

## ファイル

| ファイル | 件数 | 中身 | 生成 |
|---|---|---|---|
| `eval_person.json` | 71 | **主指標**。質問 → 正しい専門家 | `scripts/build_eval_v2.py` |
| `eval_retrieval.json` | 56 | 層1。質問 → 正しい根拠チャンク。**埋め込みモデルの横並び比較用** | 同上 |
| `eval_robustness.json` | 20 | 異常系。**全件 abstain（答えない・聞き返す）が正解** | 同上 |
| `topic_experts_human.json` | 67トピック | **人手ラベル**。topic → 専門家(`employee_id`) | `scripts/import_human_labels.py` |
| `eval_queries.json` | 40 | **非推奨**（#26）。比較のため残置 | `scripts/build_eval.py` |
| `route_calibration.json` | 71 | **C5 の閾値較正**。実 DB・実埋め込みで記録したチャネル類似度 | `scripts/research_e2e.py --task route` |

---

## `eval_person.json` のスキーマ

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | int | 連番（1〜71） |
| `query` | string | 自然文の質問。**L2以上はトピック語を含まない**（症状で書く） |
| `difficulty` | string | `L1`（易・トピック語明示）/ `L2`（症状のみ）/ `L3`（複数トピック横断・商材名のみ）/ `L4`（専門家不在） |
| `gold_topics` | string[] | 正解トピック（22トピック語彙）。**クエリには書かれていない**。C1 の評価に使う。体系外の項目は空 |
| `gold_experts` | int[] | 正解の専門家 `employee_id`。順不同。**層2の主指標 Recall@3 はこれで測る** |
| `gold_experts_alt` | int[] | **第2の正解**。`answers` だけから導出（主 gold は `answers` を使わない＝経路が独立）。段Bの検証と循環チェック用。体系外トピックでは空 |
| `gold_route` | string | `person`（主線）/ `prior_answer`（補助）/ `document`（格下げ）/ `none`（答えない）。**コーパスの状態から導出**（#52） |
| `expect_abstain` | bool | `true` なら「わかりません＋人へエスカレーション」が正解 |
| `constraint` | object\|null | 拠点などの制約（例 `{"branch": "大阪"}`）。無視すると外れる。**書かれ方は #84 で散らしてある**（下記） |
| `source_topic` | string\|null | 人手ラベル由来の項目における PR #46 側のトピック名 |
| `label_source` | string | `auto:project_daily` / `authored` / `human:pr46` |
| `alt_label_source` | string | 第2の正解の導出経路。現状は `auto:answers` 固定 |
| `note` | string | その項目の意図 |

難易度の内訳: L1 10件 / **L2 36件**（自動15＋人手21）/ L3 20件 / **L4 15件**（全 81 件）。

### 拠点制約の書かれ方（#84）

制約つきは15件（#158 で 5 → 15 に増やした）。**以前はすべて `…できれば{拠点}の拠点で動ける方だと助かります。`
という同じ文型**で、拠点名の文字列一致だけで 5/5 取れてしまい、抽出の難しさを測れていなかった。
いまは書き方を散らしてある。

- **拠点名を書かない**（地域名から解決）: id 11「九州の…現地で動ける方」→ 福岡 /
  id 14「関西で対応できる方」→ 大阪
- **言い換え**: id 13「本部に席がある方」→ 本社
- **拠点名は書くが文型が違う**: id 12（文頭）/ id 15（理由つき・末尾定型ではない）

さらに **`constraint` が `null` なのに地名が出てくる**3件（id 16 / 18 / 21）を入れてある。
「大阪の事例」「名古屋のお客様（拠点は問わない）」「東京で実施した施策」で、
**地名を見つけただけで制約と決めつける実装を落とすため**のもの。

素朴な文字列一致での再現率は **2/5**、誤検出は **3件**
（`scripts/research_robustness.py` の §4 が毎回測る）。

> **この JSON は `scripts/build_eval_v2.py` の生成物である。**
> 文面を直したいときは**生成器の側を直すこと**。JSON を手で直しても、
> 次に生成器を回した時点で消える（#84 で実際にやってしまった）。
> 言い回しの定義は `_constraint_phrasings` / `_pick_phrasing` / `DECOY_SENTENCES` にある。
>
> `_pick_phrasing` は**拠点名を隠す型を優先しつつ、同じ言い回しを二度使わない**。
> 単純な剰余で配ると拠点名の出る型に偏り、文字列一致で 4/5 拾えてしまう（一度そうなった）。

### 第2の正解（`gold_experts_alt`）— #73 で追加

主 gold は `projects`（lead 1.0 / member 0.6）+ `daily_reports`（0.15）から作り、
**`answers` を意図的に使っていない**（原則②）。`gold_experts_alt` はその `answers` だけから作る。

- 主 gold との平均 Jaccard **0.68**（中央 0.75、完全一致 15/45）
- **トピックが分かった後の人の並び（段B）は、この2本の差でしか測れない。**
  `projects` を使うスコアラーが主 gold の作り方をなぞっているだけでないかの検査に使う
- 45件で非空（`answers` が無いトピックでは空になる）

`eval_retrieval.json` は `eval_person.json` の L1〜L3 と `id` で1対1に対応する
（`gold_chunks` は `doc:` / `proj:` / `profile:` のプレフィックス付き）。
人を外したとき、検索が悪いのかスコアリングが悪いのかを切り分けるために使う。

`eval_robustness.json` の `category` は
`out_of_scope`(5) / `pii`(4) / `insufficient`(5) / `no_expert`(3) / `adversarial`(3)。

---

## `gold_experts` の導出方法（**v2 で変わった。重要**）

**`projects`（lead=1.0 / member=0.6）と `daily_reports`（0.15）からのみ導出する。
`answers` は使わない。**

### なぜ answers を使わないか

旧 `eval_queries.json`（#26）は `answers` を `topic` で集計して `responder_id` を正解にしていた。
ところが専門性推定（`analysis/15_専門性推定とグラフ成長.md`）も**同じ answers を最重量の証拠**
（有用回答1.0 / 過去回答0.7）に使う。つまり**正解と入力が同じ源から出ていた**。

結果、旧セットでは「**answers を topic で数えるだけ**」の20行の実装が
`correct_experts` と **40/40（100%）一致**した。スコアラーの良し悪しを一切測れていない。

v2 では導出経路を分けたので、同じベースラインの Recall@3 は **0.393** まで落ちる。

### 人手ラベルによる外部検証

自動導出だけでは「合成データの中の別ルール」でしかない。
PR #46 で別メンバーが案件・日報・チャットを**人手で読んで**付けたラベルと突き合わせている。

```
平均 Jaccard: 0.74 / 自動 gold が人手 gold に含まれる率（被覆）: 0.83 / 完全一致 10/22
```

このラベルは `topic_experts_human.json` に収録し、うち10トピックは `eval_person.json` の L2 に採用している
（`label_source: "human:pr46"`）。

---

## `gold_route` の決め方（#52 で変わった）

**クエリの言い回しは一切見ない。コーパスの状態から決める。**

```python
if not experts:                                   -> "none"
if docs >= 3 and 有用回答の平均reuse < 2.0:        -> "document"
if docs == 0 and 有用回答の平均reuse >= 4.0:       -> "prior_answer"
else:                                             -> "person"
```

以前は `scripts/build_eval_v2.py` に `PROCEDURAL_TOPICS` / `RECALL_TOPICS` という**定数**を持っていた。
全22トピックが「回答6〜7件・文書1〜2件」と横並びで、コーパスから決めようとすると全件が同じラベルになったため。
#52 で fixtures 側にトピック差を作ったので、**定数を廃止できた**。

現在の分布（全81件）: `person` 49 / `none` 15 / `document` 10 / `prior_answer` 7。
このうち上の規則で機械的に決まるのは `auto:project_daily` の25件（`document` 10 / `person` 8 /
`prior_answer` 7）で、残りは人手ラベル21件（すべて `person`）と authored 35件。

---

## ベースライン（`python3 scripts/eval_baselines.py`）

**本システムの数字は、この表との差分でしか意味を持たない。モデル比較の前にまずここを測る。**

| baseline | L1 | L2 | L3 | 全体 | 意味 |
|---|---|---|---|---|---|
| random | 0.100 | 0.093 | 0.033 | 0.076 | 下限 |
| **answers_count** | 0.967 | **0.213** | 0.200 | **0.323** | **リークの残量**（旧セットでは 1.000、#73 前は 0.393） |
| lexical_profile | 0.600 | 0.056 | 0.083 | 0.146 | 語彙一致のみ。埋め込みはこれを超えないと採用理由が無い |
| lexical_answers | 0.333 | 0.130 | 0.167 | 0.172 | 強めの語彙ベースライン。実質的な打倒目標 |

*(Recall@3。L4 は abstain 判定なので Recall の対象外)*

**必ず層別に出す。** 総合 0.8 でも L2 が 0.5 なら実力は 0.5。

---

## 変更するときの注意

`backend/tests/test_eval_quality.py` が、このセットが「自分に甘いテスト」に戻っていないことを検証している。

- L2/L3 のクエリにトピック語が混入していない
- 表層キーワードによる route 的中率が、多数クラスのベースライン+10pt を超えない
- 「answers を数えるだけ」ベースラインの Recall@3 < 0.6
- 独立サンプル数 >= 30

**クエリを足すときは、トピック語をクエリに書かないこと。** そこを崩すと評価が飽和して差が出なくなる。

---

## `route_calibration.json`（#105）

C5 の閾値（`PRIOR_ANSWER_SIM` / `DOCUMENT_SIM` / `PERSON_WEAK_SIM`）が
**実際のコサイン分布と噛み合っているか**を CI で見張るための記録。
`backend/tests/test_route_calibration.py` が読む（**GPU も DB も要らない**）。

#103 は「実装は動き、単体テストも通り、数字だけが壊れている」形の不具合だった。
e5-large では `answer_confidence` の最小値が 0.816 で `PRIOR_ANSWER_SIM`(0.80) を常に超えるため、
当時の全71件が `prior_answer` に倒れて層2 Recall@3 が 0.592 落ちていた。

**コサインの絶対値はモデル依存**なので、`settings.embedding_model` を変えたら必ず測り直す
（#63 で Nemotron に差し替えると最大が 0.57 になり、今度は一度も発火しなくなる）。
モデル名が食い違うとテストが明示的に落ちる。

```bash
python scripts/research_e2e.py --task prepare   # 初回のみ（seed + embed、CPU で約5分）
python scripts/research_e2e.py --task route --out fixtures/synthetic/eval/route_calibration.json
```
