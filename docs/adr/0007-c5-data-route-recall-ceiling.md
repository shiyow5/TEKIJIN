# ADR-0007: C5 の prior_answer 経路は現行シグナルでは person と分離できない（自己回答#291の律速は経路でなく構造化）

- ステータス: 承認
- 日付: 2026-08-25
- 決定者: A チーム（研究・実測に基づく）
- 関連: ADR-0004（C5 経路閾値）/ ADR-0006（C6 証拠カバレッジ天井）/ #327 / #291 / #119 / #297

## 背景

#291 自己回答は C5 が **データ由来経路（document / prior_answer）** に振り分けたときだけ発火する。
本番 vLLM での recall 実測（#291 part3・#297 の source recall）で、律速は自己回答そのものでなく
**C5 のデータ経路振り分け recall**（#327）と判明していた。gold_route を持つ 72 件（person49 /
document16 / prior_answer7）について、実 retriever + `decide_route` を DB 上で回し（`scripts/
research_route_recall.py`・throwaway pgvector・CPU 埋め込み・**LLM 非依存**）、prior_answer 経路を
開ける唯一残った手段 **#119 の corpus-count routing（`prior_answer_reuse_min`）** を掃いた。

## 選択肢

- 案A: corpus-count routing（reuse_count 閾値）を有効化して prior_answer recall を上げる。
- 案B: それを「people 信号が弱いときだけ」発火させる（document 降格と同じゲート）。
- 案C: 経路レベルでは Pareto 改善しないと結論し、自己回答の実現手段を**知識層の構造化（#357）**に振る。

## 決定

**案C**。実測（62→72件 gold_route・実 Nemotron・top_k=10）：

| config | 経路正解率 | person recall | prior_answer recall | document recall |
| --- | --- | --- | --- | --- |
| **baseline (corpus-count OFF・現行)** | **0.833** | **1.000** (49/49) | 0.000 (0/7) | 0.688 (11/16) |
| reuse_min=2 | 0.333 | 0.224 | 1.000 | 0.375 |
| reuse_min=3 | 0.708 | 0.694 | 0.857 | 0.688 |
| reuse_min=4 | 0.764 | 0.776 | 0.857 | 0.688 |
| reuse_min=5 | 0.764 | 0.816 | 0.571 | 0.688 |
| gated reuse_min=3 (people<0.40) | 0.681 | 0.694 | 0.571 | 0.688 |
| gated reuse_min=4 (people<0.40) | 0.736 | 0.776 | 0.571 | 0.688 |

- **どの config も baseline を Pareto 改善しない**。prior_answer recall を上げると必ず person を奪う
  （person-gold クエリも高 reuse かつ相応に近い top answer を持つため）。最良の reuse_min=4 でも
  +6 prior_answer / **−11 person**、全体正解率 0.764 < baseline 0.833。
- **people 弱ゲートは逆効果**。誤ルートする person-gold は元々 people 信号も弱い（ゲートを素通り）ため
  守れず、逆に people 信号を持つ真の prior_answer を弾いて recall を 6→4 に下げる。
- **document recall は全 config で 0.688 固定**（corpus-count は document を触らない）。残 5 件は
  ADR-0004/#191 が示した低 confidence 群（閾値では正解率を崩さず回収できない）。

したがって **prior_answer 経路は cosine（#119）でも reuse_count（#327）でも people-gate でも
person-gold と分離できない**。reuse_count は「よく再利用される回答」を拾う信号であって、
「その質問が過去QAで解決すべきか」を person と切り分ける識別子ではない。ADR-0006 と同型の
**グラウンドトゥルースの緊張**（prior_answer gold と person gold が同じ表層シグナルを共有する）。

## 影響

- **`prior_answer_reuse_min` は既定 None（OFF）を維持**。`self_answer_enabled` も OFF 維持。
  経路の閾値・reuse 閾値の探索を自己回答有効化の手段として**追わない**（#327 の経路側レバーは打ち止め）。
- **自己回答の実現手段は「経路を賢くする」ではなく「知識層で答える」**。#357 の知識フレームワークは
  document/prior_answer の生データを **構造化知識単位（問題→打ち手→結果）** に変換して
  `search_knowledge_units` で直接引く。これは「person か document か」の経路分離問題を**迂回**し、
  gold_source を持つ質問に構造化知識で答える道を開く（#357 スライス4＝知識検索→自己回答接続）。
  → **#291 有効化の前提は #357 の知識層接続**であり、C5 経路の再調整ではない。
- 恒久ハーネス `scripts/research_route_recall.py`（DB+実 retriever・corpus-count/gated 掃引・
  per-route recall・非退行の Pareto 判定）を残す。コーパス/埋め込みが変わったら再実行して天井を測り直す。
- prior_answer gold 7 件は現行コーパスでは経路で救えないことを明示（ADR-0004 の #119 スタブ注記を裏取り）。
