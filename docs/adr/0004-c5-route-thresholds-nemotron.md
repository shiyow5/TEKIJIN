# ADR-0004: C5 の経路閾値を Nemotron 実測分布に較正する（応急処置）

- ステータス: 承認
- 日付: 2026-08-23
- 決定者: Aチーム

## 背景

C5 `decide_route`（`backend/src/tekijin/agent/route.py`）は、C4 が返す各チャネルの
**絶対コサイン類似度**（`answer_confidence` / `document_confidence` / `people_confidence`）を
固定閾値で判定し、`person` / `prior_answer` / `document` の経路を決める。

閾値はもともと **e5/BERT 系の想定**（near-duplicate ~0.85-0.95、strongly related ~0.75-0.85）で
`PRIOR_ANSWER_SIM=0.80` / `DOCUMENT_SIM=0.70` / `PERSON_WEAK_SIM=0.50` に置かれていた。

#63 で埋め込みを **Nemotron-3-Embed-1B** に替えたが、Nemotron のコサインは**帯が大きく圧縮**されて
おり（評価コーパス実測レンジ ~0.04-0.57）、旧閾値では **どの分岐も一度も発火しない**（`document` /
`prior_answer` が定数偽になり、全件 `person` に倒れる）。逆に e5 時代は最小値が閾値を超えて全件
`prior_answer` に倒れていた（#103）。いずれも「実装は正しいが閾値と分布が噛み合っていない」不具合。

## 実測（`fixtures/synthetic/eval/route_calibration.json`, Nemotron, **決定当時の n=71 / gold付き56件**）

チャネルごとの gold 別分布:

| gold | answer_conf (min/max/mean) | document_conf | people_conf |
| --- | --- | --- | --- |
| document (10) | 0.239 / 0.504 / 0.347 | 0.184 / 0.566 / **0.349** | 0.219 / 0.387 / 0.298 |
| prior_answer (7) | 0.212 / **0.410** / 0.304 | 0.081 / 0.281 / 0.165 | 0.130 / 0.454 / 0.293 |
| person (39) | 0.116 / **0.543** / 0.239 | 0.039 / 0.265 / 0.146 | 0.078 / 0.384 / 0.245 |
| none (15) | 0.105 / 0.222 / 0.167 | 0.093 / 0.240 / 0.140 | 0.053 / 0.225 / 0.144 |

グリッド探索（routed 56件の経路精度を最大化、1経路潰れ < 0.95 を制約）で最良帯を求めた。

## 決定

**応急処置として閾値を Nemotron 実測分布に較正する。**

| 定数 | 旧 (e5) | 新 (Nemotron) | 根拠 |
| --- | --- | --- | --- |
| `PRIOR_ANSWER_SIM` | 0.80 | **0.55** | 観測最大 0.543 の直上＝**意図的に発火させない**（下記） |
| `DOCUMENT_SIM` | 0.70 | **0.30** | document-gold mean 0.349 vs 他 ~0.14–0.17。document recall 7/10 |
| `PERSON_WEAK_SIM` | 0.50 | **0.40** | people レンジ 0.053–0.454 の内側。document-gold の people_conf 最大 0.387 を通す |

結果: **経路精度 0.821（多数決 person 0.696 を上回る）**、1経路への潰れ 0.90（< 0.95）。

> 注（#158）: その後 #84 / #158 で評価セットを変えたので、**上の 0.821 は当時の56件基準の値**である。
> #158 第1段（制約を5→15件）で 0.768（43/56）に下がり、L3 を10→20件に増やした現在は
> **0.803（53/66）**（`gold_route` が `none` でない件数が 56 → 66 に増えた）。
> **分母が違うので3つの値を直接は比べられない。** `document` に振られるのは 7件 → 4件 のまま
> 戻っていないので、決定そのものは変えないが **閾値は再較正の余地がある**。→
> [e2e.md](../benchmarks/e2e.md) §0.3

## 追補（#191, 2026-08-24）: 66件基準で DOCUMENT_SIM を 0.30 → 0.28 に再較正

#84 / #158 / #59 で評価セットが動いた（全81件 / 採点66件）ため、`route_calibration.json` を
**現行コーパスで作り直して**（`scripts/research_e2e.py --task prepare && --task route`、
Nemotron-3-Embed-1B、CPU）DOCUMENT_SIM を引き直した。`PRIOR_ANSWER_SIM`（無効化）と
`PERSON_WEAK_SIM=0.40` は据え置き。document-gold の `document_confidence` は
0.166 / 0.177 / 0.189 / 0.290 / 0.306 / 0.370 / 0.386 / 0.398 / 0.435 / 0.486。

`PA=0.55 / PW=0.40` 固定で DOCUMENT_SIM のみ掃引（66件の経路精度・document recall・
1経路潰れ率。多数決ベースライン 0.742）:

| DOCUMENT_SIM | 全体精度 | document recall | 1経路潰れ | 判定 |
| --- | --- | --- | --- | --- |
| 0.18 | 0.697 | 6/10 | 0.75 | 精度が多数決割れ |
| 0.20 | 0.727 | 5/10 | 0.84 | 精度が多数決割れ |
| 0.26 | 0.803 | 5/10 | 0.93 | 可 |
| **0.28** | **0.818** | **5/10** | **0.94** | **採用（最良）** |
| 0.30（旧） | 0.803 | 4/10 | **0.95** | **潰れ制約を割る** |
| 0.32 | 0.788 | 3/10 | 0.96 | 潰れ |

**決定: DOCUMENT_SIM = 0.28。** 旧 0.30 は現行コーパスで①潰れ率が 0.95 に達して
「< 0.95」制約を割り、②document recall が 4/10 に落ちていた。0.28 は全体精度を
0.803→0.818 に上げ、document recall を 5/10 に戻し、潰れ率を 0.94 に下げる。
下位3件（0.166 / 0.177 / 0.189）は精度を多数決以下に落とさずには回収できず、
**閾値では埋まらない**（本筋のコーパス集計ルーティング #119 / トピック語彙 #116 の担当）。
決定の骨子（閾値較正の応急処置・prior_answer 無効化）は変えないため新 ADR は起こさず追補とする。

## 追補（#434, 2026-08-26）: 較正データを 87件で取り直した（**閾値は変更なし**）

`fixtures/synthetic/eval/route_calibration.json` が **81件・`document_sim` 0.30 のまま**
残っていた。`docs/benchmarks/ablation/route_nemotron.json`（同じ `--task route` の出力）は
#253 で 87件・0.28 に更新したが、テストが読む較正データのほうは更新していなかった。
つまり **閾値ガードが、評価セットより6行・再較正1回分 古いコーパスで較正されていた**。

同じコマンド（`scripts/research_e2e.py --task route`、DGX・CPU 埋め込み・LLM 不要）で
取り直した。**`rows` は #253 の `route_nemotron.json` と完全一致**（同一コーパス・同一モデルの
決定的な計算なので当然だが、突き合わせて確認した）。

| 指標 | 81件（旧・#191 当時） | **87件（新）** | 制約 |
|---|---|---|---|
| 1経路への潰れ | 76/81 = 0.938 | **76/87 = 0.874** | < 0.95（余裕 0.012 → **0.076**） |
| 経路精度 | 0.818 | **0.833**（60/72） | > 多数決 |
| 多数決 | 0.742 | **0.681** | — |
| 経路別 | — | person 49/49・document 11/16・prior_answer 0/7 | — |

チャネル分布（87件）: `answer_confidence` 0.105 / 0.220 / 0.542、
`document_confidence` 0.025 / 0.158 / 0.528、`people_confidence` 0.053 / 0.215 / 0.473（最小/中央/最大）。
`PRIOR_ANSWER_SIM=0.55` が観測最大 0.542 の直上にあるという ADR-0007 の状況も変わっていない。

**この追補は決定を変えない。** 閾値（PA=0.55 / DOC=0.28 / PW=0.40）は据え置きで、
較正の土台が現行コーパスに揃っただけ。狙いは「将来 `DOCUMENT_SIM` を動かしたときに、
古い81行の上でだけ `_COLLAPSE_RATIO` を割って CI が落ちる」事故を無くすこと。

## prior_answer が発火しないことの明示

`answer_confidence` は **prior_answer を分離できない**:

- prior_answer gold の `answer_confidence` 最大 = **0.410**
- person gold の `answer_confidence` 最大 = **0.543**（prior_answer より高い）

どの閾値を置いても prior_answer より先に person を誤検出するため、`PRIOR_ANSWER_SIM=0.55` を
観測最大の直上に置き、**この経路を実質無効化**した。0.821 の精度は prior_answer 経路が死んだまま
達成している（prior_answer gold 7件はすべて person に落ちる）。これは埋め込みコサインの限界であり、
較正で埋められない。

## 選択肢

- 案A: **閾値較正（応急処置）＋ 本筋は別 Issue**。← 採用
- 案B: いま本筋（コーパス集計ルーティング）まで実装する。← #69 依存・評価循環の設計が必要で範囲過大
- 案C: prior_answer 経路を削除する。← 本筋で復活させる前提なので保留

## 本筋（#119）

prior_answer / document の判定を**コーパス集計**（`answers.reuse_count`・回答の実在、担当 `documents`
件数）に移す。#69（トピック媒介 C1→C6）が前提。`gold_route` がコーパス構造由来のため評価が循環する
点も #119 で扱う。本 ADR はそれまでの応急処置。

## 影響

- `backend/src/tekijin/agent/route.py`: 3定数と docstring を更新。
- `backend/tests/test_route_calibration.py`: `test_routes_do_not_collapse_to_a_single_branch` と
  `test_route_accuracy_beats_the_majority_baseline` の strict xfail を解除（較正で通るようになった）。
  `test_threshold_sits_inside_the_observed_distribution` は `PRIOR_ANSWER_SIM` パラメータのみ strict
  xfail を残す（意図的に分布外）。#119 で prior_answer が復活したら削除する。
