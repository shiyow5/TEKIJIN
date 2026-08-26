# 製品コードでの実測（Issue #100 / 再測定 #132）

> **この文書は2つの測定を含む。** §0 が**現行 develop の数値**、§1 以降は
> e5-large 時代に #103 を見つけたときの記録（当時の構成のまま残してある）。

> 📏 **§0 は pipeline / oracle-topic 測定（gold トピックを C6 に直接渡す `PipelineRanker` 系）で、かつ
> `self_answer` OFF の条件**での数値。**これは「現行の完全な end-to-end」ではない。**
> 実 C1 を通して `build_agent` を 87 行に走らせる **full-graph E2E の真の数値（`self_answer` ON）は
> `scripts/research_fullgraph_eval.py`** が出す。2つの測定の別と指標定義は **[eval-metrics.md](eval-metrics.md)** を参照。

## 0. 現行構成での pipeline / oracle-topic 実測（2026-08-23 再測定 / #132・`self_answer` OFF）

> **#84 / #158 で評価セットを変えたので測り直した。**
> **#158 では制約つきを5件→15件に増やし（該当10件の gold がその拠点の人に絞られた）、
> さらに L3 を10件→20件に増やした。** 件数は 71 → **81**（採点対象 56 → **66** /
> gold トピックあり 52 → **62**）。**分母が動いているので、旧測定と直接は比べられない。**
> → [robustness.md](robustness.md) §4

**構成**: `nvidia/Nemotron-3-Embed-1B-BF16`（2048次元）/ 経路閾値 0.55・**0.30**・0.40（[ADR-0004](../adr/0004-c5-route-thresholds-nemotron.md)）/
#115 の RRF 重み。DGX 上の `pgvector/pgvector:pg16`（測定当時の浮動タグ。#101 で以降は固定）に seed + 埋め込み370行を投入して測定。

> ⚠️ **§0.3 だけが新しい。** #253 で §0.3（経路）を**現行コーパス87件・現行閾値
> `DOCUMENT_SIM`=0.28** で測り直した。§0.1 / §0.2 / §0.4 は `ablation/e2e_variants.json` 由来で
> **81件・`DOCUMENT_SIM`=0.30 のまま**（再生成していない）。上の「構成」行は §0.1/0.2/0.4 の条件。
> §0.2 の「経路 `document` で構造上0点 = 4件」も 0.30・81件時点の数で、§0.3 の 11件とは別基準。

### 0.1 層2 Recall@3

**gold トピックを C6 に渡した条件。** 66件基準（`gold_topics` が空の4件を含む）、括弧内は62件基準。
右の4列は66件基準。**最右の「旧」は e5-large + 旧閾値のときの値**（§2 の表）。

<!-- gen:e2e_variants -->
| 構成 | R@3 | Top-1 | MRR | L1 | L2 | L3 | 旧 R@3 |
|---|---|---|---|---|---|---|---|
| **現状（そのまま）** | **0.692** (0.737) | 0.652 | 0.736 | 0.667 | 0.722 | 0.650 | 0.140 |
| **経路の pin を外す（C4 の候補10名）** | **0.753** (0.801) | 0.712 | 0.797 | 0.967 | 0.750 | 0.650 | 0.732 |
| **候補を全社員にする（#87）** | **0.775** (0.825) | 0.742 | 0.830 | 0.967 | 0.745 | 0.733 | 0.836 |
<!-- /gen:e2e_variants -->

- **#120 の閾値較正で 0.140 → 0.692。** #103（全件 `prior_answer` で候補が1名に固定される）は解消した
- **「候補を全社員にする」は 0.836 → 0.775 に動いた。** この変種は `rank(q, all_ids)` で
  `res` も `route` も読まないので**埋め込みにも経路にも依存しない**（#132 / #84 の再測定では
  4回とも小数点以下3桁まで一致していた）。動いたのは **#158 で gold と件数そのものを変えた**ため。
  「検索・経路に依存しない」という性質は変わっていない
- C4 で候補を10名に絞ることの損失は **0.775 − 0.753 = 0.023**（旧構成では 0.104）
  — **ただしこれはオラクル測定（gold_topics を採点器に直接渡す変種比較）で、実グラフでは再現しない。**
  実 vLLM のフルグラフ E2E・3反復のペア比較では、全社員にすると Hit@3 は **0.7778 → 0.7626 と
  むしろ下がる**（3反復とも同符号）。#87 は
  [ADR-0009](../adr/0009-c6-candidate-pool-roster-vs-c4.md) で棄却済み。
  **この表の「候補を全社員にする」を推奨として読まないこと**

### 0.2 「現状そのまま」の 0.692 は、分母に構造上0点の8件を含む

`research_e2e.py` の `as_is` は **`route == "document"` のとき空リストを返す**（人を並べない）。
66件の内訳:

| 区分 | 件数 | 内容 |
|---|---|---|
| 構造上0点: 経路 `document` | 4 | **4件とも `gold_route` も `document`。経路は正しい** |
| 構造上0点: `gold_topics` が空 | 4 | 評価 ID 32〜35。C6 に渡すトピックが無い |
| 採点可能 | 58 | |

到達可能な上限は **58/66 = 0.879**。実測 0.692 は、その 58件のうち **0.787 相当**を取っている。

**L1 の 0.667 は取りこぼしではない。** L1 10件のうち3件（id 5, 6, 8）が上の「経路 `document`」で、
**3件とも `gold_route` が `document`（＝正しく振っている）**。残る7件のうち
**6.67件相当が正解**で、L1 は到達可能分の **0.667 / 0.700 = 95%** を取っている。

### 0.3 経路

> 📏 この経路表は **`self_answer` 有効化より前**に測ったもの（`self_answer` OFF）。自己回答が既定ON に
> なった後（#380）は、データ由来経路が `self_answered` 終端へ進むため終端分布が変わる。真の end-to-end は
> `scripts/research_fullgraph_eval.py` / [eval-metrics.md](eval-metrics.md) を参照。**下の数値は変更していない。**

```
予測経路の分布: {'person': 76, 'document': 11}   ← 87件
候補者数の分布: {10: 87}
```

<!-- gen:route_channels -->
| チャネル | 最小 | 中央 | 最大 | 閾値 |
|---|---|---|---|---|
| `answer_confidence` | 0.105 | 0.220 | **0.542** | `PRIOR_ANSWER_SIM = 0.55` |
| `document_confidence` | 0.025 | 0.158 | 0.528 | `DOCUMENT_SIM = 0.28` |
| `people_confidence` | 0.053 | 0.215 | 0.473 | `PERSON_WEAK_SIM = 0.40` |
<!-- /gen:route_channels -->

**経路精度 0.833（60/72、`gold_route` が `none` でない72件基準）。**
#103 当時の 0.125 からは大きく回復している。**#158 の前は 0.821（46/56）**、
#158 第1段（制約15件）では 0.768（43/56）、**66件基準・`DOCUMENT_SIM`=0.30 の時点では 0.803（53/66）**
だった。**分母が 56 → 66 → 72 と動いているので、これらを直接は比べられない。**
document recall は **11/16 = 0.688**（ADR-0007 の baseline 行と一致）。
全87件を分母にすると 0.690。**基準に注意。**

> **内訳（同じ JSON 上で閾値だけ振って測った）。** 「`document` が 4件 → 11件」を
> `DOCUMENT_SIM` 再較正の効果と読まないこと — **閾値が買っているのは1件だけ**で、
> 残りは評価セット側で gold `document` が 10 → 16 に増えた分:
>
> | 行数 | `DOCUMENT_SIM` | 経路精度 | document recall |
> |---|---|---|---|
> | 81（旧） | 0.30 | 53/66 = 0.803 | 4/10 |
> | 81（旧） | 0.28 | 54/66 = 0.818 | **5**/10 |
> | 87（現行） | 0.30 | 59/72 = 0.819 | 10/16 |
> | 87（現行） | 0.28 | **60/72 = 0.833** | **11**/16 |
>
> 81件・0.28 の行は [ADR-0004](../adr/0004-c5-route-thresholds-nemotron.md) 追補の記録
> （0.818 / 5-of-10）と一致する。**「0.26 に下げれば同じだけ稼げる」とは読めない。**

> この表は #253 で **現行コーパス（87件）・現行閾値（`DOCUMENT_SIM` 0.28）** で再生成した。
> それ以前の生成物は **81件・0.30** のままで、#191 の再較正も評価セットの拡張も反映されていなかった。

gold → 予測の内訳:

<!-- gen:route_matrix -->
| gold | → 予測 | 件数 |
|---|---|---|
| `document` | `document` | 11 |
| `document` | `person` | 5 |
| `none` | `person` | 15 |
| `person` | `person` | 49 |
| `prior_answer` | `person` | 7 |
<!-- /gen:route_matrix -->

**`prior_answer` は0件。** `answer_confidence` の最大 0.542 が閾値 0.55 に届かない。
これは不具合ではなく **ADR-0004 が意図してこの経路を無効化した**もので（person gold の
`answer_confidence` 最大 0.542 が prior_answer gold の最大 0.410 を上回り、
どこに閾値を置いても分離できない）。**この経路の復活は打ち止め**:
コーパス集計ルーティング（#119/#327）はどの config も baseline を Pareto 改善せず、
[ADR-0007](../adr/0007-c5-data-route-recall-ceiling.md) で棄却された（#119 は close 済み）。

### 0.4 C1 の実トピックを使った切り分け

C1（`#130` の「プロンプト列挙 + enum、上位1件のみ」）が実際に出したトピックを C6 に渡した場合。
66件中66件で突き合わせできた（#158 で id が動いたため C1 を測り直している）。

<!-- gen:e2e_c1 -->
| 構成 | R@3（66件） | 62件 |
|---|---|---|
| C1 の実トピック＋全社員 | **0.639** | 0.680 |
| C1 の実トピック＋現状の経路 | **0.556** | 0.591 |
<!-- /gen:e2e_c1 -->

> ✅ **#158 で C1 を測り直した。** 評価セットの id が動いて予測が別のクエリに紐づく事故を起こしたので、
> `c1_*.json` は81件基準で全部取り直してある。#84 で変えた8件も反映済み。

gold トピック（0.775）との差 **0.136 が C1 の取りこぼし分**。

### 0.5 誤レコメンドと確信度

内訳は [misrecommendation.md](misrecommendation.md) にある（同じ `misrecommendation.json` から生成）。
**「高」が「中」より当たらないという逆転（#110）は #158 後も変わっていない**（65.8% < 77.4%）。

> **この数字は #158 で初めて動いた。** #132 と #84 の再測定では、埋め込みを替えても経路を替えても
> クエリ文面を変えても**4回連続でバイト単位一致**しており、
> [misrecommendation.md](misrecommendation.md) / #110 の「確信度の数字は埋め込みにも経路にも
> 依存しない」を裏づけていた。**動いたのは #158 で gold を絞り、件数そのものを増やしたため**で、
> その性質が崩れたわけではない。gold が1〜2名に絞られた10件では、
> 3枠のうち当てられる枠が減るので正解率が下がる。

### 0.6 再現方法

`requirements-dev.txt` の `pgserver` は **aarch64 のホイールが無い**ため DGX では入らない。
外部 PostgreSQL を指す `--db-url` を追加してある。

```bash
docker run -d --name tekijin_pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=tekijin \
  -p 55432:5432 pgvector/pgvector:0.8.6-pg16
export DB=postgresql+psycopg://postgres:pw@host.docker.internal:55432/tekijin
python scripts/research_e2e.py --task prepare  --db-url "$DB"
python scripts/research_e2e.py --task route    --db-url "$DB"
python scripts/research_e2e.py --task variants --db-url "$DB" --c1 <c1_both.json> --c1-topk 1
python scripts/research_e2e.py --task misrec   --db-url "$DB"
```

個別値は `ablation/route_nemotron.json` / `ablation/e2e_variants_nemotron.json` /
`ablation/e2e_variants_nemotron_c1both_top1.json` / `ablation/misrecommendation.json`。

---

## 1. 以下は e5-large 時代の記録（#103 を見つけたときのもの）

> **測定時の構成**: e5-large(1024次元) / 経路閾値 0.80・0.70・0.50 / RRF 等重み。
> **§0 で再測定済み。ここの数値は現行構成では再現しない。**

> ⚠️ **ここの層2 R@3 はすべて gold トピックを渡した条件で、`gold_topics` が空の4件を含む56件基準。**
> `gold_topics` がある52件で測ると 0.836 は **0.901** になる。

これまでの数値（[ablation.md](ablation.md) / [robustness.md](robustness.md) / [scorer.md](scorer.md) /
[route.md](route.md) / [draft.md](draft.md)）は**すべて `scripts/research_*.py` の再現実装**で測ったもの。
式・重み・base_score・`decide_route` は製品の純関数を import しているが、
**C4（HybridRetriever）と DB 経路は別実装**なので、結論が製品コードで再現する保証がなかった。

#33（PR #95）で評価ランナーが入ったので、`HybridRetriever → decide_route → ExpertiseScorer` を
**実 DB・実埋め込み**で通した。実施 2026-08-23。

**ローカルで完結する。** `requirements-dev.txt` の `pgserver` が rootless PostgreSQL + pgvector を同梱しており、
Docker も GPU も要らない（埋め込みは CPU で370行・約4分）。
（**`pgserver` は aarch64 のホイールが無い**ため DGX 上では使えない。§0.6 の `--db-url` を使う。）

```bash
python scripts/research_e2e.py --task prepare    # seed + embed（初回のみ）
python scripts/research_e2e.py --task route      # C4→C5 の経路とチャネル類似度
python scripts/research_e2e.py --task variants   # 経路のバグを直したときの層2 R@3
```

### 1.1 見つかったこと: 経路が全件 `prior_answer` に倒れ、候補が常に1名になる

`python -m tekijin.eval` の出力（そのまま）:

```
queries        : 71 (ranked 56, routed 56)
Top-1 Accuracy : 0.393 (目標 0.70)
Recall@3       : 0.140 (目標 0.90)
MRR            : 0.393 (目標 0.75)
Route Accuracy : 0.125 (目標 0.80)
  L1: R@3=0.333 Top-1=1.000 (n=10)
```

**L1 の「Top-1 = 1.000 なのに R@3 = 0.333」**が手がかりだった。
gold 4名のうち1名しか出せていない＝**3枠のうち1枠しか埋まっていない**。
また経路精度 **0.125 はちょうど 7/56** で、`prior_answer` の gold 件数と一致する。

C4→C5 を直接叩いて確定した。

```
予測経路の分布: {'prior_answer': 71}    ← 全71件
候補者数の分布: {10: 71}                ← C4 は毎回10名返している
```

`pipeline.py` は `route == "prior_answer"` のとき候補を1名に固定する（`candidates = [pinned]`）。
**C4 が返した10名のうち9名が捨てられている。**

#### 原因: 閾値が e5-large のコサイン分布とまったく合っていない

| チャネル | 最小 | 中央 | 最大 | 閾値 |
|---|---|---|---|---|
| `answer_confidence` | **0.816** | 0.854 | 0.928 | `PRIOR_ANSWER_SIM = 0.80` |
| `document_confidence` | 0.777 | 0.808 | 0.860 | `DOCUMENT_SIM = 0.70` |
| `people_confidence` | 0.790 | 0.821 | 0.860 | `PERSON_WEAK_SIM = 0.50` |

**最小値ですら `PRIOR_ANSWER_SIM` を超えている。** `decide_route` は prior_answer を最初に判定するので、
他の分岐に到達しない。

[route.md](route.md)（#88）で報告した較正ずれと同じ問題だが、**向きが逆**。
あちらは Nemotron（コサイン最大 0.57）で測って「一度も発火しない」だった。
**同じ閾値が、埋め込みモデル次第で「全く発火しない」と「常に発火する」の両極に振れる。**

### 1.2 影響の大きさ

製品の `tekijin.eval.metrics` をそのまま使って、候補集合だけ差し替えて測った。

| 構成 | R@3 | Top-1 | MRR | L1 | L2 | L3 |
|---|---|---|---|---|---|---|
| **現状** | **0.140** | 0.393 | 0.393 | 0.333 | 0.116 | 0.033 |
| 経路の pin を外す（C4 の候補10名） | **0.732** | 0.768 | 0.812 | 0.967 | 0.694 | 0.633 |
| **候補を全社員にする**（[scorer.md](scorer.md) §3 / #87） | **0.836** | 0.804 | 0.866 | 0.967 | 0.819 | 0.767 |

- **この不具合だけで R@3 を 0.592 落としている**（#103）
- **候補を C4 で絞らず全社員を C6 に渡すと、さらに +0.104**。
  ハーネスで測った +0.030（#85）と同じ向きで、実機のほうが差が大きい

### 1.3 ハーネスの結論は再現したか

| ハーネスでの主張 | 製品コードでの結果 |
|---|---|
| C5 の閾値はモデル依存で較正が要る（#88 / #90） | **再現。しかも予測より深刻**（Nemotron で「発火しない」、e5 で「常に発火する」） |
| C4 で候補を絞ると落ちる（#85 / #87） | ~~再現。差は 0.030 → 0.104 と実機のほうが大きい~~ **オラクル測定でのみ再現。実グラフ E2E では逆に -0.015（ADR-0009 で棄却）** |
| トピックが分かれば人の並べ替えは強い | **再現**。gold トピックを渡した全社員スコアリングで **R@3 0.836 / L3 0.767** |

**ハーネスの向きはすべて正しく、大きさは控えめだった。**
唯一外したのは「経路が全件 `person` になる」という予測で、
これは私が Nemotron のコサイン分布で測っていたため。**製品の既定は e5-large で、逆に倒れる。**

### 1.4 限界

1. **ランナーは gold トピックを scorer に渡す**（C1 を経由しない）。
   したがってここで測っているのは**層1-2と経路**で、[ablation.md](ablation.md) §4 の
   トピック媒介（LLM 分類）の効果はここには現れない。
   → **§0.4 で C1 の実トピックを渡した切り分けを追加した**（#130 後で 0.750）
2. **`asker` を渡していない**ので `proximity` は company-wide 固定（ランナーの設計どおり）
3. 埋め込みは #63 で `nvidia/Nemotron-3-Embed-1B-BF16` に差し替え済み。この §1 の表は
   差し替え**前**の e5-large 時の測定。
   **閾値較正は #120（[ADR-0004](../adr/0004-c5-route-thresholds-nemotron.md)）で完了し、
   実 DB での再測定は §0 に載せた。**（`fixtures/synthetic/eval/route_calibration.json` は
   #434 で現行コーパス87件・現行閾値で取り直した。以前ここに書いていた「較正前＝旧閾値で
   全71件 person に倒れていたときの分布」は、その時点の同ファイルの説明であって現行の中身
   ではない。現行は person 76 / document 11。）

§1 の個別値は `ablation/e2e_variants.json` / `ablation/e2e_route_confidences.json`、
§0 の個別値は §0.6 に列挙したファイル。
