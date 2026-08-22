# 製品のまま動かした C1 → C2（Issue #113 / #116）

> ⚠️ **`llm_backend=vllm` にすると、層2 Recall@3 は 0.836 → 0.131 に落ちる。**
> C1 が出すトピックが後段の語彙と一致せず、C6 の証拠源が4つとも空振りするため。
> ランダム基準 0.107 とほぼ同じで、「回答数の多い順」基準 0.393 より悪い（→ #116）。

## 0. なぜ測り直したか

[c2.md](c2.md)（#111 / PR #112）の測定は、製品のリクエストを**手で写していた**。
そのため system プロンプトの字面は合っていても、

* human の書式が違う（C1 の `質問（依頼者: {'id': N}）: …` を落としていた）
* **`temperature=0.7` を送っていなかった**（`ChatOpenAI` の既定値。製品は明示していないので既定が乗る）
* JSON スキーマが手書きで、`SufficiencySchema` の Field description を持っていなかった
* C1 に**検索結果8断片を見せていた**（製品では C1/C2 は C3/C4 の前に走る）
* 異常系20件を全部 C2 の担当として数えていた（`out_of_scope` は C1 で止まり C2 に届かない）

という違いがあり、**「出荷時の挙動」を名乗れる状態ではなかった**（PR #112 の Codex レビュー指摘）。

今回は**リクエストを手で組み立てない**。`scripts/research_faithful.py` が製品の
`ChatOpenAI._get_request_payload` を直接呼び、送信直前の dict をそのまま取り出す。
`tools` / `tool_choice` / `parallel_tool_calls` / `temperature` / messages のすべてが製品と一致する
（`model` だけベンチのサーバ名に差し替える）。

実施 2026-08-23。実機 `internship-dgx1`、Qwen3.6-35B-A3B-NVFP4、`--max-model-len 8192`
（`scripts/serve_vllm.sh` の既定）。対象は正常系56件（層2の採点対象）+ 異常系20件。

## 1. いちばん重い問題 — C1 のトピックが後段の語彙に無い

製品の `_INTENT_SYSTEM` はトピック候補一覧を渡さない。C1 は自由記述で返す。

```
質問: 購買・仕入れの件でご相談です。取引先との値段交渉が担当者任せで…
C1:   ["購買", "仕入れ", "値段交渉", "条件整理", "取引先"]      gold は ["購買・仕入れ"]

質問: サーバー・インフラ運用について詳しい方を探しています。…
C1:   ["サーバー", "インフラ", "運用保守", "老朽化"]            gold は ["サーバー・インフラ運用"]
```

**複合語のトピック名が単語に割れる。** 一方 C6 側の照合は**完全一致**である。

| 証拠源 | 照合 | 場所 |
|---|---|---|
| 過去回答 | `Answer.topic.in_(topic_list)` | `data/repository.py` |
| スキル | `skill.topic in topic_set` | `scorer/evidence.py` |
| 資格 | `CERT_TOPIC_KEYWORDS[topic]` | `scorer/topics.py` |
| 案件 | `PRODUCT_TOPIC_MAP` の値と一致 | `scorer/topics.py` |

後ろ2つも**正規のトピック名がキー**なので、語彙外が来れば同じく引けない。**4つとも空振りする。**

実測: C1 が出したトピック **175個のうち、fixtures の語彙（22件）に載っていたのは3個**。
**1つでも載った相談は 66件中3件**（正常系では 50件中3件: `p3` `p4` `p54`）。

### 効き方（製品コード・実 DB で測った層2 Recall@3）

| 変種 | R@3 | Top-1 | MRR | L1 | L2 | L3 |
|---|---|---|---|---|---|---|
| 現状そのまま（#103 の経路バグ込み） | 0.140 | 0.393 | 0.393 | 0.333 | 0.116 | 0.033 |
| 経路の pin を外す | 0.732 | 0.768 | 0.812 | 0.967 | 0.694 | 0.633 |
| 候補を全社員にする（gold トピック） | **0.836** | 0.804 | 0.866 | 0.967 | 0.819 | 0.767 |
| **C1 の実トピック + 全社員** | **0.131** | 0.107 | 0.193 | 0.233 | 0.111 | 0.100 |
| **C1 の実トピック + 現状のまま** | **0.134** | 0.375 | 0.375 | 0.333 | 0.106 | 0.033 |

ベースライン: ランダム 0.107 / 回答数順 0.393（`scripts/eval_baselines.py`）。

**上3行はすべて gold トピックを渡している＝「C1 が完璧なら」という仮定の値。**
製品で C6 が実際に受け取るのは4行目である。
**経路の不具合（#103）を直しても、こちらが残る限り推薦は成立しない。**

なお C1 が構造化出力を返せなかった6件（下記）は空の推薦として数えている。
製品ではそこで**例外**になるので、0.131 はむしろ甘い側の見積もりである。

## 2. thinking が入りっぱなしで、C1 が長さ切れになる

`_openai_model`（`llm/vllm.py`）は `chat_template_kwargs` を渡していない。
**`enable_thinking=false` が無いので thinking が ON のまま動く。**

* **76件中10件が `finish_reason=length` で、関数呼び出しを返さなかった**
  （`p11` `p24` `p25` `p31` `p32` `p45` `r5` `r9` `r10` `r11`）。
  製品では `PydanticToolsParser` が例外にするので、**この13%はリクエストごと失敗する**
* 出力トークンは1件あたり1000〜1400
* **C1 単体で p50 14.14秒 / p95 105.35秒 / 最大 105.57秒**

`README.md` の「C1 p50 0.52秒」は `enable_thinking=false` で測った値で、**製品の設定では再現しない。**
合格ラインは C1+C2 合計3秒。

## 3. temperature が仕様と食い違う

`docs/specs/model-definition.md` は「C1・C2 は低温（決定性重視）」と書いている。
`_openai_model` は temperature を渡していないので、**`ChatOpenAI` の既定値 `0.7` が送られる**
（送信直前のボディを取り出して確認済み）。判断系のノードを 0.7 で回している。

## 5. 再現

```bash
# 1) 製品のリクエストを組み立てる（GPU不要）
python scripts/research_faithful.py --task c1 --out payload_c1_faithful.json

# 2) 送る（要 vLLM。組み立て済みボディをそのまま POST する）
python scripts/research_llm.py --task raw --payload payload_c1_faithful.json --out c1_faithful.json

# 3) C1 の実出力から C2 のリクエストを作って送る
python scripts/research_faithful.py --task c2 --c1 c1_faithful.json --out payload_c2_faithful.json
python scripts/research_llm.py --task raw --payload payload_c2_faithful.json --out c2_faithful.json

# 4) 集計（GPU不要）
python scripts/research_c2_faithful.py --c1 c1_faithful.json --c2 c2_faithful.json \
    --payload payload_c2_faithful.json

# 5) 層2 R@3 への効き方（GPU不要。pgdata は seed 済みのものを使う）
python scripts/research_e2e.py --task variants --pgdir <pgdata> --c1 c1_faithful.json
```

生データは `docs/benchmarks/ablation/{payload_,}c1_faithful.json` /
`{payload_,}c2_faithful.json` / `e2e_variants_c1.json`。
