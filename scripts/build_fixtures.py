#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_fixtures.py — reona作の合成データ(社員/案件/チャット/日報)を ER スキーマ準拠に整形し、
不足エンティティ(社員プロフィール/資格/過去QA)を補完して fixtures/synthetic/ 配下に出力する。

入力 (reona作、scratchpad に抽出済み。--input-dir で差し替え可):
  <input>/employees.json            40件 : id,name,department,email,position,hire_date,department_history
  <input>/case_history_dummy.json  120件 : id,title,status,department,name,customer_company,
                                            industry,company_size,product,issue,start_date,end_date
  <input>/chat_history_dummy.json 2000件 : id,speaker,channel,timestamp,message
  <input>/daily_report_dummy.json 3070件 : id,name,department,content,reported_at,registered_at

出力 (fixtures/synthetic/ 配下、いずれも JSON 配列。embedding 列は含めない=取込時にアプリが計算):
  people/employees.json              … reona employees + branch(拠点) + role(職種) + section
  people/employee_profiles.json      … 40名 {employee_id, description, updated_at}
  certifications/certifications.json … ~100件 {id, employee_id, name, acquired_at}
  projects/projects.json             … 120件 case_history → ER PROJECTS 写像
  projects/project_members.json      … lead(主担当)+0〜2 member
  chat/employee_chat_history.json    … chat_history 写像 {id, sender_employee_id, ...}
  daily_reports/daily_reports.json   … daily_report 写像 {id, employee_id, report_date, ...}
  questions/questions.json           … 150件 過去質問 {id, asker_id, body, topics[], status, created_at}
  answers/answers.json               … 150件 過去回答 {id, question_id, responder_id, body, ...}

生成ロジックの芯:
  - 「トピック → 得意な社員」を、各社員の projects(product/issue) と daily(content) の
    キーワード一致から evidence スコアとして推定する(projects lead=1.0/member=0.6, daily=0.15)。
  - 過去QA の question.topics[] と answer.topic にそのトピックを残し、answer.responder_id は
    そのトピックの上位専門家から選ぶ。これにより後段の eval が「topic でグルーピングして
    responder_id を集める」だけで correct_experts を employee_id 単位で復元できる。

再現性: 先頭で random.seed(42)。実行: python3 scripts/build_fixtures.py
"""

import argparse
import json
import os
import random
from collections import defaultdict
from datetime import date, datetime, timedelta

random.seed(42)

# --------------------------------------------------------------------------
# パス
# --------------------------------------------------------------------------
REPO_ROOT_ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# 一次データはリポジトリ内に置く（fixtures/source/README.md 参照）。
# 以前は一時ディレクトリを指しており、それが消えると再生成不能になる状態だった。
DEFAULT_INPUT = os.path.join(REPO_ROOT_, "fixtures", "source")
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_ROOT = os.path.join(REPO_ROOT, "fixtures", "synthetic")

SNAPSHOT = date(2026, 8, 19)  # データのスナップショット日

# --------------------------------------------------------------------------
# 部署 → 職種(role) / 拠点(branch) の割当設計
# --------------------------------------------------------------------------
# role: 顧客接点・技術・バックオフィスで 営業/技術/スタッフ に振り分ける
#   (technical-spec §8 の職種比率「技術/営業/スタッフ」に寄せる)
DEPT_ROLE = {
    "営業部": "営業",
    "マーケティング部": "営業",
    "広報部": "営業",
    "情報システム部": "技術",
    "開発部": "技術",
    "カスタマーサポート部": "技術",
    "人事部": "スタッフ",
    "経理部": "スタッフ",
    "総務部": "スタッフ",
    "購買部": "スタッフ",
}

BRANCHES = ["本社", "東京", "大阪", "名古屋", "福岡"]
# 拠点は 3〜4箇所に集中しがち、を再現しつつ全拠点を使う重み
BRANCH_WEIGHTS = [0.34, 0.24, 0.20, 0.12, 0.10]


# --------------------------------------------------------------------------
# トピック体系 (大塚商会商材寄り + 各部署業務)。keyword は projects/daily 文面に対する部分一致
# --------------------------------------------------------------------------
TOPICS = {
    "ネットワーク・VPN": ["VPN", "ネットワーク", "接続トラブル"],
    "セキュリティ": ["セキュリティ", "セキュリティパッチ", "UTM", "脆弱性"],
    "社内IT・ヘルプデスク": ["社内PC", "セットアップ", "アカウント管理", "IT問い合わせ", "ヘルプデスク"],
    "サーバー・インフラ運用": ["サーバー", "定期メンテナンス", "オンプレミス", "保守運用", "保守体制", "災害対策", "BCP"],
    "クラウド移行": ["クラウド移行", "クラウド"],
    "基幹システム": ["基幹システム", "システム間連携", "既存システムの老朽化"],
    "データ基盤・分析": ["データ基盤", "データ分析", "データベース", "応答遅延", "部門ごとのデータ分断"],
    "システム開発・API": ["API", "機能改修", "コードレビュー", "本番環境の障害"],
    "パフォーマンスチューニング": ["パフォーマンス", "処理速度", "アクセス集中"],
    "モバイルアプリ開発": ["モバイルアプリ", "アプリ利用率", "操作性"],
    "ECサイト構築": ["ECサイト", "決済手段"],
    "CRM・営業支援": ["CRM", "顧客情報の一元管理", "営業活動の可視化", "リード管理", "顧客接点のデジタル化"],
    "契約管理": ["契約管理", "契約書管理", "契約更新漏れ"],
    "業務効率化コンサル": ["業務効率化", "手作業によるコスト", "業務フローの非効率", "業務プロセスの属人化"],
    "Webマーケティング・広告": ["Webマーケティング", "Web広告", "広告運用", "広告費用対効果", "ターゲティング", "リード獲得"],
    "SNS運用": ["SNS", "投稿", "エンゲージメント", "認知度"],
    "問い合わせ・ヘルプデスク運用": ["問い合わせ対応", "問い合わせ窓口", "FAQ", "クレーム対応", "サポート対応", "対応品質"],
    "経理・決算": ["決算", "請求書", "経費精算", "予算管理", "資金繰り"],
    "人事・採用": ["採用", "給与計算", "人事評価", "社内研修", "面接"],
    "総務・法務": ["社内規程", "リーガルチェック", "契約書のリーガル", "株主総会", "オフィス備品", "来客対応"],
    "購買・仕入れ": ["仕入れ", "発注", "サプライヤー", "在庫", "納期調整"],
    "広報・PR": ["プレスリリース", "社内報", "採用広報", "メディア対応", "会社SNS"],
}

# --------------------------------------------------------------------------
# トピックごとの「答えの在り処」の差（#52）
# --------------------------------------------------------------------------
# 以前は全22トピックが「回答6〜7件・文書1〜2件」と横並びで、
# 「文書があるトピックは document」と定義すると全件が該当してしまい、
# **route の正解をコーパスの状態から決められなかった**（評価セット側で著述するしかなかった）。
# ここでトピックを3つの性格に分ける。
#
#   DOCUMENTED_TOPICS  … 標準手順・規程が文書化されている。過去回答は薄い    -> document
#   RECALL_RICH_TOPICS … 文書は無いが、再利用される過去回答が厚い            -> prior_answer
#   それ以外            … 文書も過去回答も薄い。現場判断が要る                -> person（主線）

# 手順・規程として書き下せる領域（社内手続き・IT運用の定型）
DOCUMENTED_TOPICS = [
    "社内IT・ヘルプデスク",
    "総務・法務",
    "経理・決算",
    "人事・採用",
    "購買・仕入れ",
    "ネットワーク・VPN",
    "セキュリティ",
    "サーバー・インフラ運用",
]

# 同じ問いが繰り返され、過去の回答がそのまま使い回される領域
RECALL_RICH_TOPICS = [
    "契約管理",
    "問い合わせ・ヘルプデスク運用",
    "SNS運用",
    "Webマーケティング・広告",
    "ECサイト構築",
    "業務効率化コンサル",
]

# 過去QAを1件も持たせないトピック（現場判断のみ。route=person の純粋なケース）
NO_ANSWER_TOPICS = ["モバイルアプリ開発", "パフォーマンスチューニング", "基幹システム"]


def reuse_for_topic(topic, base):
    """0〜8 の一様乱数を、トピックの性格に応じたレンジへ写像する。乱数は消費しない。"""
    if topic in RECALL_RICH_TOPICS:
        return 4 + base // 2  # 4〜8（よく使い回される）
    if topic in DOCUMENTED_TOPICS:
        return base // 4  # 0〜2（文書を見るので回答は使い回されない）
    return base // 2  # 0〜4


def helpful_rate_for_topic(topic):
    if topic in RECALL_RICH_TOPICS:
        return 0.85
    if topic in DOCUMENTED_TOPICS:
        return 0.55
    return 0.70


SPECIALIZE_RNG_SEED = 4251
CROSS_DEPT_RATE = 0.75  # 選ばれたメンバーを他部署の担当者へ差し替える確率

# --------------------------------------------------------------------------
# 営業部日報を SPR 訪問日報フォーマットに寄せる（#326）
# --------------------------------------------------------------------------
# ヒアリングで、営業部の日報は SPR（全社が閲覧できる顧客情報システム）に決まったフォーマットで
# 入力されていると分かった。**営業部の従業員の日報だけ**、訪問日報の構造（訪問日時 / 要件 /
# 相手担当 / 自社担当 / 所要時間 / 詳細）に沿った短文へ差し替える（他部署は不変）。粒度は 1〜2 文
# （実態）。実データ・実顧客名は使わず、大塚商会の一般的な商材レンジで合成する。
#
# 乱数は日報IDで決まる専用インスタンス（グローバル random を消費しない＝ダウンストリームの
# 件数を一切ずらさない。pick_members と同じ方針）。
SALES_DEPT = "営業部"
SALES_REPORT_RNG_SEED = 7326

SALES_PURPOSES = ["初期訪問", "課題ヒアリング", "提案", "デモ・PoC", "クロージング", "導入フォロー"]
# 訪問先（守秘のため業種＋規模の一般表現のみ。実顧客名は使わない）。
SALES_INDUSTRIES = ["製造業", "卸売業", "小売業", "建設業", "運輸・物流業", "医療・介護", "自治体", "サービス業"]
SALES_SIZES = ["中小企業", "中堅企業", "大手企業"]
SALES_DURATIONS = [30, 45, 60, 90]
SALES_COUNTERPARTS = ["情報システム部ご担当", "経営企画ご担当", "総務ご担当", "購買ご担当", "現場ご担当"]

# トピックごとの (顧客課題句, 提案商材句)。いずれも TOPICS のキーワードを含み、match_topics が
# 拾えるようにする（＝日報が案件と同じトピック証拠を張る）。商材名は大塚商会の公開商材レンジの一般名。
SALES_TOPIC_SCRIPT = {
    "CRM・営業支援": ("顧客情報の一元管理と営業活動の可視化", "CRM・営業支援（SFA/CRM）"),
    "業務効率化コンサル": ("手作業によるコストと業務フローの非効率", "業務効率化コンサル"),
    "契約管理": ("契約更新漏れが生じている契約管理", "契約書管理システム"),
    "基幹システム": ("既存システムの老朽化と基幹システムのシステム間連携", "基幹システム刷新"),
    "ネットワーク・VPN": ("拠点間の接続トラブルが続くネットワーク", "VPN・ネットワーク構築"),
    "セキュリティ": ("脆弱性対応とセキュリティ運用の負荷", "UTMによるセキュリティ対策"),
    "クラウド移行": ("オンプレミス資産のクラウド移行", "クラウド移行支援"),
    "データ基盤・分析": ("部門ごとのデータ分断とデータ分析基盤", "データ基盤構築"),
}
# 要件→詳細文テンプレ（{issue}=顧客課題句, {sol}=提案商材句）。粒度 1〜2 文。
SALES_DETAIL_TEMPLATE = {
    "初期訪問": "{issue}について現状をヒアリングした。",
    "課題ヒアリング": "{issue}の課題を整理し、要望を確認した。",
    "提案": "{issue}に対し、{sol}を提案した。",
    "デモ・PoC": "{issue}の解決に向け、{sol}のデモを実施した。",
    "クロージング": "{sol}について契約条件を最終調整し、受注に向けてクロージングした。",
    "導入フォロー": "{sol}の導入後の稼働状況を確認し、追加要望をヒアリングした。",
}
# 要件→課題タグ（daily_reports.issue）。
SALES_ISSUE_BY_PURPOSE = {
    "初期訪問": "新規顧客の課題把握",
    "課題ヒアリング": "顧客課題の深掘り",
    "提案": "提案内容の精度",
    "デモ・PoC": "導入効果の実証",
    "クロージング": "受注確度の見極め",
    "導入フォロー": "導入後定着の支援",
}


def sales_spr_report(report, emp_name, rep_topics):
    """営業部の日報1件を SPR 訪問日報フォーマットの短文へ変換する。

    返り値は ``(content, issue)``。乱数は日報IDで決まる専用インスタンス（グローバル random を
    消費しない）。担当トピックはその社員の**案件由来**の得意領域（``rep_topics``）から選ぶ＝
    日報が案件と同じトピック証拠を補強し、案件に無いトピックの偽の専門性を作らない
    （gold の乖離を避ける）。上位トピックほど選ばれやすく重み付ける。
    """
    rng = random.Random(SALES_REPORT_RNG_SEED + int(report["id"]))
    topics = rep_topics or list(SALES_TOPIC_SCRIPT)
    weights = [len(topics) - i for i in range(len(topics))]
    topic = rng.choices(topics, weights=weights, k=1)[0]
    issue_phrase, sol_phrase = SALES_TOPIC_SCRIPT[topic]

    purpose = rng.choice(SALES_PURPOSES)
    industry = rng.choice(SALES_INDUSTRIES)
    size = rng.choice(SALES_SIZES)
    counterpart = rng.choice(SALES_COUNTERPARTS)
    duration = rng.choice(SALES_DURATIONS)
    hh = rng.randint(9, 17)
    mm = rng.choice([0, 15, 30, 45])
    visit_date = report["reported_at"][:10]

    detail = SALES_DETAIL_TEMPLATE[purpose].format(issue=issue_phrase, sol=sol_phrase)
    content = (
        f"【訪問】{visit_date} {hh:02d}:{mm:02d}／{industry}の{size}／要件: {purpose}／"
        f"先方: {counterpart}／当社: {emp_name}／所要{duration}分。{detail}"
    )
    return content, SALES_ISSUE_BY_PURPOSE[purpose]

# 案件に後方から関わる部署。一次データは「顧客接点のある4部署」にしか案件を割り当てていないため、
# バックオフィスの社員は案件の証拠を一切持てず、トピック→専門家が部署に一意に決まってしまう。
# 実務では契約書のリーガルチェックに総務、請求まわりに経理、といった形で他部署が入る。
# これをメンバーとして表現する（件数は変えない。同部署メンバーとの差し替え）。
SUPPORT_DEPT_BY_TOPIC = {
    "契約管理": ["総務部", "営業部"],
    "経理・決算": ["経理部"],
    "人事・採用": ["人事部"],
    "購買・仕入れ": ["購買部"],
    "広報・PR": ["広報部"],
    "Webマーケティング・広告": ["マーケティング部"],
    "SNS運用": ["マーケティング部", "広報部"],
    "問い合わせ・ヘルプデスク運用": ["カスタマーサポート部"],
    "セキュリティ": ["情報システム部"],
    "社内IT・ヘルプデスク": ["情報システム部"],
    "ネットワーク・VPN": ["情報システム部", "カスタマーサポート部"],
    "サーバー・インフラ運用": ["カスタマーサポート部", "情報システム部"],
    "基幹システム": ["情報システム部", "営業部"],
    "業務効率化コンサル": ["営業部", "総務部"],
    "CRM・営業支援": ["営業部", "マーケティング部"],
    "データ基盤・分析": ["開発部", "マーケティング部"],
    "クラウド移行": ["開発部", "情報システム部"],
    "システム開発・API": ["開発部"],
    "パフォーマンスチューニング": ["開発部"],
    "モバイルアプリ開発": ["開発部"],
    "ECサイト構築": ["開発部", "マーケティング部"],
    "総務・法務": ["総務部"],
}


def pick_members(chosen, case, case_topics, dept_members, id2dept, lead_id):
    """メンバーの選び方（#51）。件数は変えない。

    選ばれた同部署メンバーの一定割合を、**支援部署**（SUPPORT_DEPT_BY_TOPIC）の社員へ差し替える。
    「営業がリードし、契約まわりで総務が入る」「開発がリードし、要件で情シスが入る」といった構成になり、
    トピック→専門家が単一部署に閉じなくなる。

    一次データにはバックオフィス部署の案件が1件も無いため、この差し替えが無いと
    経理・人事・総務・購買・広報の社員は案件の証拠を一切持てない。

    乱数は案件IDで決まる専用インスタンス。グローバルの random を消費しない
    （消費するとダウンストリームの件数がすべてずれ、他PRのテストを壊す）。
    """
    rng = random.Random(SPECIALIZE_RNG_SEED + case["id"])
    lead_dept = id2dept.get(lead_id)
    topics = case_topics[case["id"]]

    support = []
    for t in topics:
        for d in SUPPORT_DEPT_BY_TOPIC.get(t, []):
            if d != lead_dept:
                support += sorted(dept_members.get(d, []))
    support = sorted(set(support))

    out, taken = [], {lead_id}
    for mid in chosen:
        pick = mid
        cands = [m for m in support if m not in taken]
        if cands and rng.random() < CROSS_DEPT_RATE:
            pick = rng.choice(cands)
        if pick in taken:
            pick = mid
        if pick in taken:
            continue
        taken.add(pick)
        out.append(pick)
    return out


# 質問文テンプレ (トピックごと。営業/技術/事務いずれの聞き手からも自然な相談文)
QUESTION_TEMPLATES = {
    "ネットワーク・VPN": [
        "お客様先で拠点間VPNの接続が不安定になっています。切り分けの観点を教えてください。",
        "在宅勤務者のVPN接続トラブルが増えています。よくある原因はどのあたりでしょうか。",
    ],
    "セキュリティ": [
        "お客様がUTMの入れ替えを検討中です。他社製品からの移行で注意点はありますか。",
        "セキュリティパッチ適用の社内手順で、止められないサーバーの扱いを相談したいです。",
    ],
    "社内IT・ヘルプデスク": [
        "新入社員のPCセットアップとアカウント発行の標準手順を知りたいです。",
        "社内システムのアカウント権限の棚卸しはどう進めるのが効率的でしょうか。",
    ],
    "サーバー・インフラ運用": [
        "お客様のオンプレミスサーバーが老朽化しています。保守運用の負荷を下げる提案の勘所は。",
        "BCP・災害対策の観点でサーバー冗長化を提案したいのですが、優先順位の付け方を教えてください。",
    ],
    "クラウド移行": [
        "オンプレからクラウド移行を検討中のお客様に、段階移行の進め方を相談したいです。",
        "クラウド移行時のコスト試算で見落としがちな項目はありますか。",
    ],
    "基幹システム": [
        "既存の基幹システムが老朽化しており、刷新提案の進め方を相談したいです。",
        "基幹とサブシステムの連携不備が課題のお客様に、どう切り込むとよいでしょうか。",
    ],
    "データ基盤・分析": [
        "部門ごとにデータが分断しているお客様に、データ基盤構築をどう提案すべきでしょうか。",
        "データベースの応答遅延を訴えるお客様への初動の調査観点を教えてください。",
    ],
    "システム開発・API": [
        "他システムとのAPI連携の設計方針で相談したいことがあります。",
        "本番環境で断続的な障害が出ています。切り分けの進め方を教えてください。",
    ],
    "パフォーマンスチューニング": [
        "アクセス集中時にシステムの処理速度が落ちます。チューニングの当たりの付け方は。",
        "パフォーマンス改善調査で、まず見るべき指標はどこでしょうか。",
    ],
    "モバイルアプリ開発": [
        "既存モバイルアプリの操作性が悪く利用率が低迷しています。改善提案の観点を教えてください。",
        "モバイルアプリ開発の見積りで、工数がぶれやすい箇所はどこですか。",
    ],
    "ECサイト構築": [
        "ECサイトの決済手段が不足していて売上が伸び悩むお客様への提案を相談したいです。",
        "ECサイト構築案件で、要件定義の抜け漏れを防ぐコツはありますか。",
    ],
    "CRM・営業支援": [
        "顧客情報が一元管理できていないお客様に、CRM導入をどう提案するとよいでしょうか。",
        "営業活動の可視化不足が課題のお客様に刺さる、CRMの見せ方を教えてください。",
    ],
    "契約管理": [
        "契約更新漏れのリスクを抱えるお客様に、契約管理システムをどう提案すべきでしょうか。",
        "契約書管理が煩雑なお客様への提案で、費用対効果をどう説明しますか。",
    ],
    "業務効率化コンサル": [
        "手作業によるコスト増大に悩むお客様への、業務効率化コンサルの入り口を相談したいです。",
        "業務プロセスが属人化しているお客様に、どこから改善提案を切り出すべきでしょうか。",
    ],
    "Webマーケティング・広告": [
        "Web広告の費用対効果が落ちているお客様に、改善提案の観点を教えてください。",
        "リード獲得が伸び悩むお客様へのWebマーケ提案で、まず何を測るべきですか。",
    ],
    "SNS運用": [
        "SNSでの認知度が上がらないお客様に、運用代行をどう提案するとよいでしょうか。",
        "投稿頻度が低くエンゲージメントが伸びない、という相談への打ち手を教えてください。",
    ],
    "問い合わせ・ヘルプデスク運用": [
        "問い合わせ対応の遅延と属人化に悩むお客様へ、窓口一元化をどう提案しますか。",
        "FAQ整備が遅れているお客様に、対応品質のばらつきをどう改善提案しますか。",
    ],
    "経理・決算": [
        "月次決算の資料作成を効率化したいのですが、まず手を付けるべきところを教えてください。",
        "請求書発行と経費精算の承認フローを見直したいです。よくある改善点は。",
    ],
    "人事・採用": [
        "中途採用の書類選考の効率化について相談したいです。",
        "人事評価制度の見直しで、現場の納得を得るための進め方を教えてください。",
    ],
    "総務・法務": [
        "取引先との契約書のリーガルチェックで、特に注意すべき条項を教えてください。",
        "社内規程の見直しを進めています。優先順位の付け方を相談したいです。",
    ],
    "購買・仕入れ": [
        "仕入れ先との価格交渉で、条件を有利に進めるための準備を教えてください。",
        "新規サプライヤー選定の評価軸をどう組むとよいでしょうか。",
    ],
    "広報・PR": [
        "プレスリリースの反応を高めるための構成の勘所を教えてください。",
        "採用広報コンテンツの制作で、どんな切り口が効果的でしょうか。",
    ],
}

# 回答本文テンプレ (トピックごと。responder が経験に基づき答える体)
ANSWER_TEMPLATES = {
    "ネットワーク・VPN": "まず経路のどこで切れているかを切り分けます。現地の回線・機器・設定の順に見て、VPNのログでフェーズ1/2のどちらで失敗しているかを確認するのが早いです。",
    "セキュリティ": "移行時はまず現行のポリシーとルール棚卸しから入ります。UTMは機種で用語が違うので、要件を機能単位に落として突き合わせると事故が減ります。",
    "社内IT・ヘルプデスク": "PCセットアップは標準イメージ＋キッティング手順書を用意し、アカウントは権限グループで払い出すと属人化を防げます。棚卸しは四半期ごとが目安です。",
    "サーバー・インフラ運用": "老朽化案件はまず現行の稼働率と障害履歴を可視化します。止められない業務を洗い出してから冗長化・保守の優先順位をつけると提案が通りやすいです。",
    "クラウド移行": "いきなり全面移行せず、影響の小さい周辺システムから段階移行するのが定石です。コストは通信費と運用工数の見落としに注意します。",
    "基幹システム": "刷新は現行業務のヒアリングと連携インターフェースの棚卸しが肝です。段階移行と並行稼働の期間をどう取るかで難易度が変わります。",
    "データ基盤・分析": "まずデータの発生源と更新頻度を整理します。分断の解消は共通IDの設計から入り、応答遅延はインデックスとクエリを先に見ます。",
    "システム開発・API": "連携は認証方式とエラー時のリトライ設計を最初に決めます。障害切り分けは再現条件を固定し、ログの相関から入るのが早いです。",
    "パフォーマンスチューニング": "まず遅い箇所を計測で特定します。推測で触らず、DBのスロークエリとアクセス集中時のリソースから当たりをつけます。",
    "モバイルアプリ開発": "利用率低迷は導線と操作性の課題が多いです。既存の離脱ポイントを計測してから改修範囲を絞ると見積りがぶれません。",
    "ECサイト構築": "決済は主要手段を早めに確定します。要件定義は会員・在庫・決済・配送の四点セットで抜け漏れをチェックすると安全です。",
    "CRM・営業支援": "CRMは入力負荷を下げる設計が導入成否を分けます。営業の可視化は日報や商談ステージと連動させると現場が使い続けてくれます。",
    "契約管理": "更新漏れ対策はアラート運用が要です。費用対効果は「失注・違約リスクの回避額」で説明すると経営層に刺さります。",
    "業務効率化コンサル": "まず手作業の棚卸しと工数の実測から入ります。属人化は手順の標準化と権限分離で崩し、効果は削減時間で定量提示します。",
    "Webマーケティング・広告": "費用対効果の改善はまず計測設計の見直しからです。CPAとLTVを揃えて見て、効いていないチャネルを削るのが早い一手です。",
    "SNS運用": "認知拡大は投稿頻度より一本ごとの狙いの明確化が効きます。エンゲージメントの高い型を見つけて横展開する運用が現実的です。",
    "問い合わせ・ヘルプデスク運用": "窓口一元化はまず問い合わせの分類と件数の可視化から入ります。FAQ整備と合わせると対応品質のばらつきが下がります。",
    "経理・決算": "月次決算は締め作業の前倒しと入力の分散が効きます。承認フローはワークフロー化して差し戻しの手戻りを減らすのが定石です。",
    "人事・採用": "書類選考は評価基準の言語化とスクリーニング条件の統一で効率が上がります。評価制度の見直しは現場合意を段階的に取るのが安全です。",
    "総務・法務": "契約書は責任範囲・解約・損害賠償の条項を優先して確認します。規程見直しはリスクの大きいものから着手すると効果が出ます。",
    "購買・仕入れ": "価格交渉は相見積りと数量・納期条件の材料を揃えてから臨みます。サプライヤー選定は品質・納期・価格・BCPの四軸で点数化すると通ります。",
    "広報・PR": "リリースは結論を先頭に、数字と独自性を一文で示すと拾われやすいです。採用広報は社員の具体的な一日など等身大の切り口が効きます。",
}

CERT_BY_ROLE = {
    "技術": [
        "基本情報技術者", "応用情報技術者", "情報処理安全確保支援士",
        "ネットワークスペシャリスト", "データベーススペシャリスト",
        "G検定", "E資格", "AWS認定ソリューションアーキテクト", "LPIC-1",
    ],
    "営業": [
        "中小企業診断士", "ITコーディネータ", "販売士(リテールマーケティング)",
        "MOS(Excel)", "ビジネス実務法務検定", "G検定",
    ],
    "スタッフ": [
        "日商簿記2級", "日商簿記1級", "社会保険労務士",
        "給与計算実務能力検定", "ビジネス実務法務検定", "第一種衛生管理者",
        "MOS(Excel)", "ファイナンシャル・プランニング技能士",
    ],
}


# --------------------------------------------------------------------------
# ユーティリティ
# --------------------------------------------------------------------------
def load(input_dir, fname):
    with open(os.path.join(input_dir, fname), encoding="utf-8") as f:
        return json.load(f)


def dump(rel_path, obj):
    path = os.path.join(OUT_ROOT, rel_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path, len(obj)


def rand_date_between(d0: date, d1: date) -> date:
    if d1 <= d0:
        return d0
    return d0 + timedelta(days=random.randint(0, (d1 - d0).days))


# --------------------------------------------------------------------------
# メイン
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default=DEFAULT_INPUT)
    args = ap.parse_args()
    IN = args.input_dir

    employees = load(IN, "employees.json")
    cases = load(IN, "case_history_dummy.json")
    chats = load(IN, "chat_history_dummy.json")
    dailies = load(IN, "daily_report_dummy.json")

    name2id = {e["name"]: e["id"] for e in employees}
    id2emp = {e["id"]: e for e in employees}
    dept_members = defaultdict(list)  # dept -> [employee_id]
    for e in employees:
        dept_members[e["department"]].append(e["id"])

    # -------- 1) people/employees.json : branch + role + section を付与 --------
    out_employees = []
    for e in employees:
        dept = e["department"]
        role = DEPT_ROLE[dept]
        branch = random.choices(BRANCHES, weights=BRANCH_WEIGHTS, k=1)[0]
        # section: 部署内を役職ベースで簡易付与 (部長/課長=第1課, それ以外=第2課)
        section = f"{dept} 第1課" if e["position"] in ("部長", "課長") else f"{dept} 第2課"
        out_employees.append({
            "id": e["id"],
            "name": e["name"],
            "email": e["email"],
            "department": dept,
            "section": section,
            "position": e["position"],
            "branch": branch,
            "role": role,
            "hire_date": e["hire_date"],
            "department_history": e.get("department_history", []),
        })
    emp_branch = {r["id"]: r["branch"] for r in out_employees}

    # -------- トピック → 得意な社員 の推定 (projects + daily から evidence) --------
    # topic_evidence[emp_id][topic] = 重み付き一致スコア
    topic_evidence = defaultdict(lambda: defaultdict(float))

    def match_topics(text):
        hits = []
        for topic, kws in TOPICS.items():
            if any(kw in text for kw in kws):
                hits.append(topic)
        return hits

    # 案件から: 主担当(lead)=1.0。member は後で project_members 構築時に加算
    case_topics = {}  # case_id -> [topic]
    for c in cases:
        case_topics[c["id"]] = match_topics(f"{c['title']} {c['product']} {c['issue']}")

    # リードは一次データ（case_history_dummy.json の name）のまま。**付け替えない。**
    # 一度は部署内で付け替えて専門特化させることを試したが、PR #46 の人手ラベルは
    # 「元のリード割当」を人が読んで付けたものなので、付け替えると外部検証の土台が崩れる。
    # 実測: 付け替えあり = 人手ラベルとの一致 Jaccard 0.68 / 付け替えなし = 0.74。
    # 独立サンプル数は 38 vs 35 で付け替えありが有利だが、外部検証のほうが価値が高いと判断した。
    id2dept = {e["id"]: e["department"] for e in out_employees}
    case_lead = {c["id"]: name2id[c["name"]] for c in cases}

    for c in cases:
        for t in case_topics[c["id"]]:
            topic_evidence[case_lead[c["id"]]][t] += 1.0

    # 日報から: content 一致 = 0.15 (件数が多いので低重み)
    for d in dailies:
        eid = name2id[d["name"]]
        for t in match_topics(d["content"]):
            topic_evidence[eid][t] += 0.15

    # 各トピックの専門家ランキング (スコア降順)。correct_experts の源
    experts_by_topic = {}
    for topic in TOPICS:
        ranked = sorted(
            ((eid, topic_evidence[eid][topic]) for eid in id2emp
             if topic_evidence[eid][topic] > 0),
            key=lambda x: (-x[1], x[0]),
        )
        experts_by_topic[topic] = [eid for eid, _ in ranked]

    # 各社員の得意トピック (プロフィール文用。上位3件)
    emp_top_topics = {}
    for eid in id2emp:
        ranked = sorted(topic_evidence[eid].items(), key=lambda x: (-x[1], x[0]))
        emp_top_topics[eid] = [t for t, _ in ranked[:3]]

    # -------- 2) projects/projects.json + project_members.json --------
    out_projects = []
    out_members = []
    for c in cases:
        out_projects.append({
            "id": c["id"],
            "subject": c["title"],
            "client_company": c["customer_company"],
            "industry": c["industry"],
            "company_size": c["company_size"],
            "client_issue": c["issue"],
            "product": c["product"],
            "negotiation_count": random.randint(1, 10),
            "status": c["status"],
            "remarks": f"{c['industry']}の{c['company_size']}向け。{c['product']}にて「{c['issue']}」の解消を支援。",
            "start_date": c["start_date"],
            "end_date": c["end_date"],
        })
        lead_id = case_lead[c["id"]]
        out_members.append({"project_id": c["id"], "employee_id": lead_id, "role": "lead"})
        # 0〜2 名を member として付与 (lead 除く)。
        # **グローバル random の呼び出し順・回数は従来のまま**にしてある
        # (変えると以降の全データの件数がずれ、他PRのテストを壊すため)。
        pool = [x for x in dept_members[c["department"]] if x != lead_id]
        random.shuffle(pool)
        chosen = pool[:random.randint(0, 2)]
        # #51: 一部を支援部署の担当者へ差し替える。専用RNGなのでストリームを消費しない
        chosen = pick_members(chosen, c, case_topics, dept_members, id2dept, lead_id)
        for mid in chosen:
            out_members.append({"project_id": c["id"], "employee_id": mid, "role": "member"})
            for t in case_topics[c["id"]]:
                topic_evidence[mid][t] += 0.6  # member の証拠

    # member 追加分を experts ランキングへ反映しなおす
    for topic in TOPICS:
        ranked = sorted(
            ((eid, topic_evidence[eid][topic]) for eid in id2emp
             if topic_evidence[eid][topic] > 0),
            key=lambda x: (-x[1], x[0]),
        )
        experts_by_topic[topic] = [eid for eid, _ in ranked]

    # -------- 3) people/employee_profiles.json --------
    out_profiles = []
    for e in out_employees:
        eid = e["id"]
        tops = emp_top_topics.get(eid, [])
        # その社員の案件と日報から具体語を拾う
        my_cases = [c for c in cases if case_lead[c["id"]] == eid]
        prod = my_cases[0]["product"] if my_cases else None
        if tops:
            topic_phrase = "・".join(tops)
            strength = f"特に{topic_phrase}まわりの相談を多く扱っています。"
        else:
            strength = f"{e['department']}の実務全般を担当しています。"
        proj_phrase = f"直近は「{prod}」の案件に携わりました。" if prod else ""
        desc = (
            f"{e['branch']}拠点・{e['department']}（{e['position']}）の{e['name']}です。"
            f"職種は{e['role']}。{strength}{proj_phrase}"
            "困りごとがあればお気軽にご相談ください。"
        )
        out_profiles.append({
            "employee_id": eid,
            "description": desc,
            "updated_at": (SNAPSHOT - timedelta(days=random.randint(0, 120))).isoformat() + "T09:00:00",
        })

    # -------- 4) certifications/certifications.json (~100件) --------
    out_certs = []
    cid = 1
    for e in out_employees:
        role = e["role"]
        pool = CERT_BY_ROLE[role][:]
        random.shuffle(pool)
        n = random.choices([2, 3], weights=[0.5, 0.5], k=1)[0]  # 平均2.5 ≒ 100件
        hire = datetime.strptime(e["hire_date"], "%Y-%m-%d").date()
        for cert_name in pool[:n]:
            acq = rand_date_between(max(hire, date(2015, 1, 1)), date(2025, 12, 31))
            out_certs.append({
                "id": f"cert_{cid:04d}",
                "employee_id": e["id"],
                "name": cert_name,
                "acquired_at": acq.isoformat(),
            })
            cid += 1

    # -------- 5) chat/employee_chat_history.json --------
    out_chat = []
    for m in chats:
        out_chat.append({
            "id": m["id"],
            "sender_employee_id": name2id[m["speaker"]],
            "receiver_employee_id": None,  # チャンネル発言のため受信者は特定しない
            "channel": m["channel"],
            "message": m["message"],
            "sent_at": m["timestamp"],
        })

    # -------- 6) daily_reports/daily_reports.json --------
    # content から課題文(issue)を簡易導出。トピック一致があれば代表的な課題語を、なければ null
    ISSUE_HINT = {
        "セキュリティ": "セキュリティ運用の負荷",
        "サーバー・インフラ運用": "インフラ保守の属人化",
        "社内IT・ヘルプデスク": "問い合わせ対応の集中",
        "ネットワーク・VPN": "接続トラブルの再発",
        "問い合わせ・ヘルプデスク運用": "対応品質のばらつき",
        "経理・決算": "締め作業の負荷集中",
        "人事・採用": "採用リードタイムの長期化",
        "購買・仕入れ": "仕入れコストの上昇",
        "広報・PR": "露出機会の不足",
        "Webマーケティング・広告": "費用対効果の低下",
        "SNS運用": "エンゲージメントの伸び悩み",
        "CRM・営業支援": "営業活動の可視化不足",
        "総務・法務": "契約・規程チェックの負荷",
    }
    # #326: 営業部の得意領域を**案件由来**（lead1.0/member0.6）で求め、SPR 日報の担当トピックに使う。
    # グローバル random 非消費（純粋な集計）。
    sales_ids = {r["id"] for r in out_employees if r["department"] == SALES_DEPT}
    sales_proj_ev = defaultdict(lambda: defaultdict(float))
    for m in out_members:
        eid = m["employee_id"]
        if eid in sales_ids:
            w = 1.0 if m["role"] == "lead" else 0.6
            for t in case_topics[m["project_id"]]:
                sales_proj_ev[eid][t] += w
    sales_rep_topics = {}
    for eid in sales_ids:
        ranked = sorted(sales_proj_ev[eid].items(), key=lambda x: (-x[1], x[0]))
        picked = [t for t, w in ranked if t in SALES_TOPIC_SCRIPT and w >= 1.0]
        sales_rep_topics[eid] = picked or list(SALES_TOPIC_SCRIPT)

    out_daily = []
    for d in dailies:
        eid = name2id[d["name"]]
        if eid in sales_ids:
            # #326: 営業部の日報は SPR 訪問日報フォーマットへ差し替える（他部署は不変）。
            content, issue = sales_spr_report(d, id2emp[eid]["name"], sales_rep_topics[eid])
        else:
            content = d["content"]
            issue = None
            for t in match_topics(content):
                if t in ISSUE_HINT:
                    issue = ISSUE_HINT[t]
                    break
        out_daily.append({
            "id": d["id"],
            "employee_id": eid,
            "report_date": d["reported_at"][:10],
            "content": content,
            "issue": issue,
            "created_at": d["registered_at"],
        })

    # -------- 7) questions/questions.json + answers/answers.json (150ペア) --------
    # 専門家が存在するトピックのみ対象。件数はトピック横断で均す
    usable_topics = [t for t in TOPICS if experts_by_topic[t]]
    # #52: 全トピックへ均等に配らない。NO_ANSWER_TOPICS は過去QAを持たせず、
    # RECALL_RICH_TOPICS は厚くする。合計は 150 のまま。
    qa_topics = [t for t in usable_topics if t not in NO_ANSWER_TOPICS] or usable_topics
    qa_weighted = []
    for t in qa_topics:
        qa_weighted += [t] * (2 if t in RECALL_RICH_TOPICS else 1)
    N = 150
    out_questions = []
    out_answers = []
    base_dt = datetime(2026, 4, 1, 9, 0, 0)
    span_days = (SNAPSHOT - date(2026, 4, 1)).days
    for i in range(N):
        topic = qa_weighted[i % len(qa_weighted)]
        experts = experts_by_topic[topic]
        # responder は上位専門家 (上位3、無ければ全体) から
        responder_id = random.choice(experts[:3] if len(experts) >= 3 else experts)
        # asker は responder 以外。なるべく別部署・別トピックの人 (聞く側)
        asker_pool = [eid for eid in id2emp if eid != responder_id]
        asker_id = random.choice(asker_pool)

        q_created = base_dt + timedelta(days=random.randint(0, span_days),
                                        hours=random.randint(0, 8),
                                        minutes=random.randint(0, 59))
        a_created = q_created + timedelta(hours=random.randint(1, 48))

        # 質問トピックは主トピック + (責任者が得意な近接トピックを最大1件)
        extra = [t for t in emp_top_topics.get(responder_id, []) if t != topic]
        q_topics = [topic] + (extra[:1] if extra and random.random() < 0.4 else [])

        qid = f"q_{i + 1:04d}"
        aid = f"ans_{i + 1:04d}"
        out_questions.append({
            "id": qid,
            "asker_id": asker_id,
            "body": random.choice(QUESTION_TEMPLATES[topic]),
            "topics": q_topics,
            "status": "answered",
            "created_at": q_created.isoformat(),
        })
        out_answers.append({
            "id": aid,
            "question_id": qid,
            "responder_id": responder_id,
            "body": ANSWER_TEMPLATES[topic],
            "created_at": a_created.isoformat(),
            # #52: トピックごとに「過去回答がどれだけ再利用されるか」を変える。
            # 乱数の消費回数は従来と同じ (1回ずつ) にしてある。
            "reuse_count": reuse_for_topic(topic, random.randint(0, 8)),
            "was_helpful": random.random() < helpful_rate_for_topic(topic),
            "topic": topic,
        })

    # -------- 7b) 全40名が最低1回は回答者になるよう是正（少数者の埋没を解消） --------
    # 未カバー社員それぞれについて、その社員が最も活動しているトピックの既存QAを1つ選び、
    # responder を差し替える（差し替え元は複数回答を持つ社員に限り、他者の被覆を崩さない）。
    # 差し替え先が見つからなければ QA を1件追加（合計は微増、README に実数記載）。
    usable_set = set(usable_topics)
    q_by_id = {q["id"]: q for q in out_questions}

    def responder_counts():
        from collections import Counter
        return Counter(a["responder_id"] for a in out_answers)

    for eid in sorted(id2emp):
        counts = responder_counts()
        if counts.get(eid, 0) >= 1:
            continue
        # 当人が活動しているトピック（evidence>0）を優先。無ければ usable 全体。
        prefs = [t for t in emp_top_topics.get(eid, []) if t in usable_set]
        prefs += [t for t in usable_topics
                  if topic_evidence[eid][t] > 0 and t not in prefs]
        if not prefs:
            prefs = usable_topics
        swapped = False
        for t in prefs:
            cands = [a for a in out_answers
                     if a["topic"] == t and counts[a["responder_id"]] > 1]
            if cands:
                a = random.choice(cands)
                a["responder_id"] = eid
                # asker が新 responder と一致したら別の asker に付け替え
                q = q_by_id[a["question_id"]]
                if q["asker_id"] == eid:
                    q["asker_id"] = random.choice([x for x in id2emp if x != eid])
                swapped = True
                break
        if swapped:
            continue
        # 差し替え不可 → QA を1件追加
        t = prefs[0]
        idx = len(out_questions) + 1
        qid = f"q_{idx:04d}"
        aid = f"ans_{idx:04d}"
        q_created = base_dt + timedelta(days=random.randint(0, span_days),
                                        hours=random.randint(0, 8))
        a_created = q_created + timedelta(hours=random.randint(1, 48))
        asker_id = random.choice([x for x in id2emp if x != eid])
        new_q = {
            "id": qid, "asker_id": asker_id,
            "body": random.choice(QUESTION_TEMPLATES[t]),
            "topics": [t], "status": "answered",
            "created_at": q_created.isoformat(),
        }
        out_questions.append(new_q)
        q_by_id[qid] = new_q
        out_answers.append({
            "id": aid, "question_id": qid, "responder_id": eid,
            "body": ANSWER_TEMPLATES[t], "created_at": a_created.isoformat(),
            # #52: トピックごとに「過去回答がどれだけ再利用されるか」を変える。
            # 乱数の消費回数は従来と同じ (1回ずつ) にしてある。
            "reuse_count": reuse_for_topic(topic, random.randint(0, 8)),
            "was_helpful": random.random() < helpful_rate_for_topic(topic), "topic": t,
        })

    # -------- 8) documents/documents.json（社内文書30件・格下げ経路用） --------
    DOC_SOURCES = ["社内ナレッジベース", "業務手順書", "社内FAQ", "運用マニュアル", "提案テンプレート集"]
    out_documents = []
    # #52: 全トピックに一律で文書を作らない。標準手順・規程として文書化される領域に寄せる
    # (= 経路 document が成立する領域)。一律だと route を切り分けられず経路判定精度を測れない。
    doc_topics = [t for t in DOCUMENTED_TOPICS if t in set(usable_topics)]
    if not doc_topics:
        doc_topics = usable_topics[:] if usable_topics else list(TOPICS)
    for i in range(30):
        topic = doc_topics[i % len(doc_topics)]
        kw = TOPICS[topic][0]
        kind = ["手順書", "FAQ", "チェックリスト", "運用ガイド", "提案テンプレート"][i % 5]
        title = f"{topic}{kind}（{kw}）"
        body = (
            f"本ドキュメントは「{topic}」に関する社内{kind}です。"
            f"対象キーワード: {'、'.join(TOPICS[topic])}。"
            f"{ANSWER_TEMPLATES[topic]} "
            "手順・注意点・過去の対応事例をまとめており、"
            "一次対応や提案準備の参照資料として利用してください。"
        )
        out_documents.append({
            "id": f"doc_{i + 1:03d}",
            "title": title,
            "body": body,
            "source": DOC_SOURCES[i % len(DOC_SOURCES)],
            "updated_at": (SNAPSHOT - timedelta(days=random.randint(0, 200))).isoformat(),
        })

    # #296: 型番/製品名を持つ製品スペック文書（doc_031〜）。型番は SudachiPy mode C で
    # 希少なサブトークン（例 FGX90 -> fgx/90・既存コーパス出現0）になり、dense 埋め込みは
    # 型番に無情報。「型番で引く」クエリでは BM25 の exact-match だけが手掛かりになるため、
    # 適応BM25(#114)の利得を測る土台になる。route=document が成立する DOCUMENTED_TOPICS に紐づける。
    # (topic, 型番, 製品カテゴリ, 手順の骨子)
    PRODUCT_DOCS = [
        ("ネットワーク・VPN", "FGX90", "UTM・ファイアウォール",
         "初期設定はWAN/LANのIP設定、ファーム更新、ポリシー投入の順。VPNトンネルはIKEv2で構成する。"),
        ("セキュリティ", "SGD450", "セキュリティゲートウェイ",
         "URLフィルタとサンドボックスを有効化し、定義ファイルを自動更新に設定する。障害時はHAの系切替を確認。"),
        ("サーバー・インフラ運用", "PSV820", "業務サーバー",
         "RAID構成の確認、BIOS/ファーム更新、監視エージェント導入までを初期構築で行う。定期再起動は保守窓で。"),
        ("社内IT・ヘルプデスク", "MFX330", "複合機（コピー機）",
         "紙詰まりは搬送路のローラーを確認し、トナー交換とドラム清掃を手順どおり行う。スキャン送信はSMB設定を確認。"),
        ("社内IT・ヘルプデスク", "NBP14G", "業務ノートPC",
         "キッティングは資産登録、ディスク暗号化、VPNクライアント導入の順。起動不良はバッテリーリセットを試す。"),
        ("ネットワーク・VPN", "WAP600", "無線アクセスポイント",
         "SSIDと認証方式を設定し、チャネルは自動割当を無効化して固定する。電波干渉時は設置位置とチャネルを見直す。"),
    ]
    # updated_at は**決定的**に振る（random を消費しない）。ここで乱数を引くと後続の
    # skills 生成の乱数列がずれ、seed=42 固定なのに skills.json に無関係な差分が出るため。
    for j, (topic, model, category, steps) in enumerate(PRODUCT_DOCS):
        title = f"{category} {model} 設定・トラブル対応手順"
        body = (
            f"本ドキュメントは製品「{category}（型番 {model}）」の社内運用手順書です。"
            f"対象機器: {model}。{steps} "
            f"{model} に関する設定変更・障害切り分け・保守作業の参照資料として利用してください。"
        )
        out_documents.append({
            "id": f"doc_{30 + j + 1:03d}",
            "title": title,
            "body": body,
            "source": "運用マニュアル",
            "updated_at": (SNAPSHOT - timedelta(days=15 * (j + 1))).isoformat(),
            # 型番eval(#296) と gold_source を突き合わせるためのメタ（loader は読まない）。
            "product_model": model,
            "product_topic": topic,
        })

    # -------- 9) self_declared/skills.json（自己申告スキル・弱い証拠 base_score 0.3） --------
    LEVELS = ["初級", "中級", "上級"]
    out_skills = []
    sid = 1
    all_topics = list(TOPICS)
    for e in out_employees:
        eid = e["id"]
        n = random.randint(1, 2)
        real = emp_top_topics.get(eid, [])
        # 実活動と無関係なトピック（evidence 0）
        unrelated = [t for t in all_topics if topic_evidence[eid][t] == 0]
        chosen = []
        # 1件目: 半数は実活動一致、半数はあえて無関係（自己申告は弱い証拠）
        if real and (random.random() < 0.5 or not unrelated):
            chosen.append(random.choice(real))
        elif unrelated:
            chosen.append(random.choice(unrelated))
        elif real:
            chosen.append(random.choice(real))
        if n == 2:
            pool = [t for t in all_topics if t not in chosen]
            if pool:
                chosen.append(random.choice(pool))
        for t in chosen:
            out_skills.append({
                "id": f"skill_{sid:04d}",
                "employee_id": eid,
                "topic": t,
                "level": random.choice(LEVELS),
                "source": "self",
            })
            sid += 1

    # -------- 出力 --------
    outputs = [
        ("people/employees.json", out_employees),
        ("people/employee_profiles.json", out_profiles),
        ("certifications/certifications.json", out_certs),
        ("projects/projects.json", out_projects),
        ("projects/project_members.json", out_members),
        ("chat/employee_chat_history.json", out_chat),
        ("daily_reports/daily_reports.json", out_daily),
        ("questions/questions.json", out_questions),
        ("answers/answers.json", out_answers),
        ("documents/documents.json", out_documents),
        ("self_declared/skills.json", out_skills),
    ]
    print("=== 出力 ===")
    for rel, obj in outputs:
        path, n = dump(rel, obj)
        print(f"  {rel:45s} {n:5d} 件")

    # -------- FK 整合チェック --------
    emp_ids = set(id2emp)
    errors = []

    def check(label, ids):
        bad = [x for x in ids if x not in emp_ids]
        if bad:
            errors.append(f"{label}: {len(bad)} 件が employees に存在しない -> 例 {bad[:5]}")

    check("project_members.employee_id", [m["employee_id"] for m in out_members])
    check("chat.sender_employee_id", [c["sender_employee_id"] for c in out_chat])
    check("daily.employee_id", [d["employee_id"] for d in out_daily])
    check("questions.asker_id", [q["asker_id"] for q in out_questions])
    check("answers.responder_id", [a["responder_id"] for a in out_answers])
    check("certifications.employee_id", [c["employee_id"] for c in out_certs])
    check("employee_profiles.employee_id", [p["employee_id"] for p in out_profiles])
    check("skills.employee_id", [s["employee_id"] for s in out_skills])

    # project_members.project_id が projects に存在
    proj_ids = {p["id"] for p in out_projects}
    bad_pm = [m["project_id"] for m in out_members if m["project_id"] not in proj_ids]
    if bad_pm:
        errors.append(f"project_members.project_id: {len(bad_pm)} 件が projects に無い")
    # answers.question_id が questions に存在
    q_ids = {q["id"] for q in out_questions}
    bad_aq = [a["question_id"] for a in out_answers if a["question_id"] not in q_ids]
    if bad_aq:
        errors.append(f"answers.question_id: {len(bad_aq)} 件が questions に無い")

    print("=== FK 整合チェック ===")
    if errors:
        for e in errors:
            print("  NG:", e)
        raise SystemExit("FK 整合エラーあり")
    print("  OK: 全 FK が employees.id / projects.id / questions.id に解決")

    # -------- 回答者カバレッジ: 全40名が最低1回 responder になっているか --------
    responders = {a["responder_id"] for a in out_answers}
    missing = sorted(emp_ids - responders)
    print("=== 回答者カバレッジ ===")
    print(f"  responder {len(responders)}/40" + (" (OK 全員カバー)" if not missing
          else f"  NG 未カバー: {missing}"))
    if missing:
        raise SystemExit("回答者カバレッジ不足")

    # -------- eval 復元性の確認: topic -> correct_experts(employee_id) --------
    from collections import Counter
    topic_responders = defaultdict(Counter)
    for a in out_answers:
        topic_responders[a["topic"]][a["responder_id"]] += 1
    print("=== topic -> correct_experts (answers から復元、抜粋) ===")
    for topic in list(topic_responders)[:6]:
        experts = [eid for eid, _ in topic_responders[topic].most_common()]
        print(f"  {topic:22s} -> {experts}")
    print(f"  (対象トピック数: {len(topic_responders)} / QAペア: {len(out_answers)})")


if __name__ == "__main__":
    main()
