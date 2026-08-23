# 製品コードでの実測（Issue #100 / 再測定 #132）

> **この文書は2つの測定を含む。** §0 が**現行 develop の数値**、§1 以降は
> e5-large 時代に #103 を見つけたときの記録（当時の構成のまま残してある）。

## 0. 現行構成での実測（2026-08-23 再測定 / #132）

> **#84 で評価クエリの文面を変えたので測り直した（生成器修正後の再測定を含む）。**
> 変えたのは文面だけで id・gold ラベル・難易度の内訳は不変なので**分母は動いていない**。
> 予告どおり **候補を全社員にする2行（0.836 / 0.750）は1桁も動かず**、検索を通る3行だけが下がった。
> → [robustness.md](robustness.md) §4

**構成**: `nvidia/Nemotron-3-Embed-1B-BF16`（2048次元）/ 経路閾値 0.55・0.30・0.40（[ADR-0004](../adr/0004-c5-route-thresholds-nemotron.md)）/
#115 の RRF 重み。DGX 上の `pgvector/pgvector:pg16` に seed + 埋め込み370行を投入して測定。

### 0.1 層2 Recall@3

**gold トピックを C6 に渡した条件。** 56件基準（`gold_topics` が空の4件を含む）、括弧内は52件基準。
右の4列は56件基準。**最右の「旧」は e5-large + 旧閾値のときの値**（§2 の表）。

| 構成 | R@3 | Top-1 | MRR | L1 | L2 | L3 | 旧 R@3 |
|---|---|---|---|---|---|---|---|
| **現状（そのまま）** | **0.658** (0.708) | 0.643 | 0.688 | 0.500 | 0.708 | 0.633 | 0.140 |
| 経路の pin を外す（C4 の候補10名） | **0.777** (0.837) | 0.768 | 0.812 | 0.967 | 0.764 | 0.633 | 0.732 |
| **候補を全社員にする**（[scorer.md](scorer.md) §3 / #87） | **0.836** (0.901) | 0.804 | 0.866 | 0.967 | 0.819 | 0.767 | 0.836 |

- **#120 の閾値較正で 0.140 → 0.658。** #103（全件 `prior_answer` で候補が1名に固定される）は解消した
- **「候補を全社員にする」行は旧測定と完全に同じ 0.836。** この変種は `rank(q, all_ids)` で
  `res`（C4 の検索結果）も `route` も読まないため、**埋め込みにも経路にも依存しない**。
  一致は偶然ではなく、測定が正しく効いていることの検算になっている
- C4 で候補を10名に絞ることの損失は **0.836 − 0.777 = 0.059**（旧構成では 0.104）

### 0.2 「現状そのまま」の 0.658 は、分母に構造上0点の11件を含む

`research_e2e.py` の `as_is` は **`route == "document"` のとき空リストを返す**（人を並べない）。
56件の内訳:

| 区分 | 件数 | 内容 |
|---|---|---|
| 構造上0点: 経路 `document` | 7 | **7件とも `gold_route` も `document`。経路は正しい** |
| 構造上0点: `gold_topics` が空 | 4 | 評価 ID 32〜35。C6 に渡すトピックが無い |
| 採点可能 | 45 | |

到達可能な上限は **45/56 = 0.804**。実測 0.658 は、その 45件のうち **0.819 相当**を取っている。

**L1 の 0.500 は取りこぼしではない。** L1 10件のうち5件が上の「経路 `document`」で、
**5件とも `gold_route` が `document`（＝正しく振っている）**。残る5件は全問正解しており、
L1 は**到達可能分の 5/5 = 100% を取っている**。

### 0.3 経路

```
予測経路の分布: {'person': 64, 'document': 7}   ← 71件
候補者数の分布: {10: 71}
```

| チャネル | 最小 | 中央 | 最大 | 閾値 |
|---|---|---|---|---|
| `answer_confidence` | 0.106 | 0.219 | **0.543** | `PRIOR_ANSWER_SIM = 0.55` |
| `document_confidence` | 0.039 | 0.150 | 0.565 | `DOCUMENT_SIM = 0.30` |
| `people_confidence` | 0.052 | 0.239 | 0.453 | `PERSON_WEAK_SIM = 0.40` |

**経路精度 0.821（46/56、`gold_route` が `none` でない56件基準）。** #103 当時の 0.125 から回復し、
[ADR-0004](../adr/0004-c5-route-thresholds-nemotron.md) がグリッド探索で出した 0.821 を
実 DB でそのまま再現した。全71件を分母にすると 0.648 になる（`none` 15件を含むため）。**基準に注意。**

gold → 予測の内訳:

| gold | → 予測 | 件数 |
|---|---|---|
| `document` | `document` | 7 |
| `document` | `person` | 3 |
| `person` | `person` | 39 |
| `prior_answer` | `person` | **7** |
| `none` | `person` | 15 |

**`prior_answer` は0件。** `answer_confidence` の最大 0.543 が閾値 0.55 に届かない。
これは不具合ではなく **ADR-0004 が意図してこの経路を無効化した**もので（person gold の
`answer_confidence` 最大 0.543 が prior_answer gold の最大 0.410 を上回り、
どこに閾値を置いても分離できない）、本筋の直しは **#119**。

### 0.4 C1 の実トピックを使った切り分け

C1（`#130` の「プロンプト列挙 + enum、上位1件のみ」）が実際に出したトピックを C6 に渡した場合。
56件中56件で突き合わせできた。

| 構成 | R@3（56件） | 52件 | 旧構成 |
|---|---|---|---|
| C1 の実トピック + 全社員 | **0.750** | 0.788 | **0.750**（完全一致） |
| C1 の実トピック + 現状の経路 | **0.580** | 0.606 | 0.146 |

> ⚠️ **`c1_both.json` の C1 出力は旧文面に対するもの。** #84 で変えた8件について、
> C1 を測り直してはいない（vLLM を立て直す必要があるため）。
> [ablation.md](ablation.md) §8-3 と同じ限界。

**「C1 の実トピック + 全社員」も旧測定と完全一致。** 0.1 の全社員行と同じ理由で
埋め込みにも経路にも依存しない。gold トピック（0.836）との差 **0.086 が C1 の取りこぼし分**。

### 0.5 誤レコメンドと確信度は旧測定と**バイト単位で同一**

`--task misrec` の出力 JSON が旧測定と `diff` で完全一致した（正解 121 / gold と同じ部署 27 /
部署も拠点も違う 4 / gold と同じ拠点 4、確信度「高」126中94 = 74.6%、「中」30中27 = 90.0%）。

[misrecommendation.md](misrecommendation.md) / #110 で「確信度の数字は埋め込みにも経路にも
依存しない」と書いた**予測が実測で裏付けられた**。同一ファイルなので複製は置いていない
（`ablation/misrecommendation.json` が両方の結果を兼ねる）。

### 0.6 再現方法

`requirements-dev.txt` の `pgserver` は **aarch64 のホイールが無い**ため DGX では入らない。
外部 PostgreSQL を指す `--db-url` を追加してある。

```bash
docker run -d --name tekijin_pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=tekijin \
  -p 55432:5432 pgvector/pgvector:pg16
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
| C4 で候補を絞ると落ちる（#85 / #87） | **再現。差は 0.030 → 0.104 と実機のほうが大きい** |
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
   較正前＝旧閾値で全71件 person に倒れていたときの分布。）

§1 の個別値は `ablation/e2e_variants.json` / `ablation/e2e_route_confidences.json`、
§0 の個別値は §0.6 に列挙したファイル。
