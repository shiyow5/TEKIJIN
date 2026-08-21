#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_eval.py — #23 の合成データ (fixtures/synthetic/) を入力に、評価セット
`fixtures/synthetic/eval/eval_queries.json`（40件）を再設計・生成する。

旧 eval_queries は廃止されたトピック体系に依存しており突合先を失っていた。本スクリプトは
**answers/answers.json を topic でグルーピングして responder_id 集合を専門家とみなす**ことで、
correct_experts を employee_id 単位で導出する（回答実績＝行動裏付けのある専門性）。

入力 (fixtures/synthetic/):
  answers/answers.json      … topic ごとの responder_id（correct_experts の源）
  questions/questions.json  … 参考（query の自然さの裏取り）
  documents/documents.json  … route="document" が成立するトピックの裏取り
  people/employees.json     … FK 検証用

出力:
  fixtures/synthetic/eval/eval_queries.json … 40件
    { id:int, query:str, topics:[str], correct_experts:[int], route:str }
    route ∈ {"person"(主線), "prior_answer"(補助), "document"(格下げ)}

route 分布 (technical-spec §7 経路判定精度用):
  person 24 / prior_answer 10 / document 6 （合計40、主線多め）

再現性: random.seed(42)。実行: python3 scripts/build_eval.py
"""

import json
import os
import random
from collections import Counter, defaultdict

random.seed(42)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYN = os.path.join(REPO_ROOT, "fixtures", "synthetic")
OUT_PATH = os.path.join(SYN, "eval", "eval_queries.json")

# 22トピックの相談主題（実データ: projects/issue・daily・documents に沿った短い課題句）
SUBJECT = {
    "ネットワーク・VPN": "拠点間VPNの接続不安定",
    "セキュリティ": "UTMの入れ替えとセキュリティ対策",
    "社内IT・ヘルプデスク": "社内PCのセットアップとアカウント管理",
    "サーバー・インフラ運用": "オンプレサーバーの老朽化と保守負荷",
    "クラウド移行": "オンプレミスからクラウドへの移行",
    "基幹システム": "基幹システムの老朽化と刷新",
    "データ基盤・分析": "部門間のデータ分断とデータ基盤整備",
    "システム開発・API": "他システムとのAPI連携設計",
    "パフォーマンスチューニング": "アクセス集中時の処理速度低下",
    "モバイルアプリ開発": "モバイルアプリの操作性と利用率低迷",
    "ECサイト構築": "ECサイトの決済手段不足と売上停滞",
    "CRM・営業支援": "顧客情報の一元管理とCRM導入",
    "契約管理": "契約更新漏れリスクと契約書管理",
    "業務効率化コンサル": "手作業によるコスト増と業務効率化",
    "Webマーケティング・広告": "Web広告の費用対効果低下",
    "SNS運用": "SNSでの認知度不足とエンゲージメント改善",
    "問い合わせ・ヘルプデスク運用": "問い合わせ対応の遅延と窓口一元化",
    "経理・決算": "月次決算と請求・経費精算の効率化",
    "人事・採用": "中途・新卒採用と人事評価制度の見直し",
    "総務・法務": "契約書のリーガルチェックと社内規程の整備",
    "購買・仕入れ": "仕入れ先との価格交渉とサプライヤー選定",
    "広報・PR": "プレスリリースと採用広報の強化",
}

# route ごとの言い回し。トピック主題 {subj} を差し込む
#  person       : 現場判断が要る相談 → 人に取り次ぐのが適切
#  prior_answer : 過去に同種の回答が存在し、その内容の再利用が効く
#  document     : 手順書・FAQ に記載があり、まず文書を参照すればよい
FRAMES = {
    "person": [
        "お客様が{subj}で悩んでいます。提案の進め方を、詳しい方に直接相談したいです。",
        "{subj}の件で商談が動いています。現場の勘所を分かっている方に取り次いでほしいです。",
    ],
    "prior_answer": [
        "{subj}について、社内で過去に似た相談と回答があったはずです。その時の回答内容を参考にしたいです。",
        "以前にも{subj}の質問が出ていたと思います。過去の回答をそのまま流用できますか。",
    ],
    "document": [
        "{subj}に関する社内の手順書やFAQの記載を確認したいのですが、どこを見ればよいですか。",
        "{subj}について、まず社内マニュアルの該当箇所を参照したいです。ドキュメントはありますか。",
    ],
}


def load(rel):
    with open(os.path.join(SYN, rel), encoding="utf-8") as f:
        return json.load(f)


def main():
    answers = load("answers/answers.json")
    documents = load("documents/documents.json")
    employees = load("people/employees.json")
    emp_ids = {e["id"] for e in employees}

    # topic -> correct_experts（回答実績の多い順→id順、2〜4名に丸める）
    by_topic = defaultdict(Counter)
    for a in answers:
        by_topic[a["topic"]][a["responder_id"]] += 1
    topic_experts = {}
    for t, c in by_topic.items():
        ranked = [eid for eid, _ in sorted(c.items(), key=lambda x: (-x[1], x[0]))]
        topic_experts[t] = ranked[:4]  # 上限4名（各トピックの実 responder は2〜4名）

    # documents が扱うトピック（route="document" 成立の裏取り）
    doc_topics = {d["title"].split("手順")[0].split("FAQ")[0].split("チェック")[0]
                  .split("運用")[0].split("提案")[0] for d in documents}

    topics = sorted(topic_experts.keys())  # 22トピック
    assert len(topics) == 22, f"topic 数が22でない: {len(topics)}"

    # --- 40スロットにトピックを分散（各トピック>=1、先頭18トピックは2件で計40）---
    slots = list(topics)  # 22
    slots += topics[:40 - len(topics)]  # +18 = 40
    assert len(slots) == 40
    random.shuffle(slots)

    # --- route ラベルを 40件へ割当（person24 / prior_answer10 / document6）---
    routes = ["person"] * 24 + ["prior_answer"] * 10 + ["document"] * 6
    random.shuffle(routes)

    # document は文書が扱うトピックにのみ付与。競合したら person と交換して整合を取る
    for i in range(40):
        if routes[i] == "document" and slots[i] not in doc_topics:
            for j in range(40):
                if routes[j] == "person" and slots[j] in doc_topics:
                    routes[i], routes[j] = routes[j], routes[i]
                    break

    out = []
    for i in range(40):
        topic = slots[i]
        route = routes[i]
        query = random.choice(FRAMES[route]).format(subj=SUBJECT[topic])
        out.append({
            "id": i + 1,
            "query": query,
            "topics": [topic],
            "correct_experts": topic_experts[topic],
            "route": route,
        })

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # -------- 検証 --------
    print(f"=== 出力: {os.path.relpath(OUT_PATH, REPO_ROOT)}  {len(out)} 件 ===")
    assert len(out) == 40, "40件でない"

    route_dist = Counter(q["route"] for q in out)
    print("route 分布:", dict(route_dist))

    covered = {q["topics"][0] for q in out}
    print(f"topics カバー: {len(covered)}/22")
    assert all(len(q["topics"]) == 1 and q["topics"][0] in topic_experts for q in out), \
        "実在22トピック外がある"

    sizes = [len(q["correct_experts"]) for q in out]
    print(f"correct_experts 平均人数: {sum(sizes) / len(sizes):.2f}（min {min(sizes)} / max {max(sizes)}）")

    # FK: correct_experts の全 employee_id が employees に存在
    bad = sorted({eid for q in out for eid in q["correct_experts"] if eid not in emp_ids})
    print("FK(employee_id) 整合:", "OK" if not bad else f"NG {bad}")
    assert not bad, "correct_experts に未知 employee_id"
    assert all(2 <= len(q["correct_experts"]) <= 4 for q in out), "correct_experts が2〜4名の範囲外"

    # document は文書が扱うトピックのみか
    bad_doc = [q["id"] for q in out if q["route"] == "document" and q["topics"][0] not in doc_topics]
    print("document route の文書裏取り:", "OK" if not bad_doc else f"NG {bad_doc}")


if __name__ == "__main__":
    main()
