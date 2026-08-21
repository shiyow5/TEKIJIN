"""評価セットの「妥当性」を守るテスト（Issue #43）。

普通のテストは実装の正しさを守る。このテストが守るのは**評価セットが測定として成立していること**。
旧 eval_queries.json は FK 整合もトピック網羅も満たしていたが、
route はキーワード5語で100%当たり、正解は「answers を数えるだけ」で100%再現でき、
異常系は0件だった。つまり整合していても測定になっていなかった。

以下の4本が落ちたら、評価セットが「自分に甘いテスト」に戻っている。
設計の根拠は analysis/19_評価データ設計.md。
"""

import importlib.util
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SYN = REPO_ROOT / "fixtures" / "synthetic"
EVAL = SYN / "eval"

# 評価セットが未生成の環境（scripts/build_eval_v2.py 未実行）ではスキップする
pytestmark = pytest.mark.skipif(
    not (EVAL / "eval_person.json").exists(),
    reason="評価セット未生成。`python3 scripts/build_eval_v2.py` を実行してください",
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _topics():
    spec = importlib.util.spec_from_file_location(
        "build_eval_v2", REPO_ROOT / "scripts" / "build_eval_v2.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TOPICS


@pytest.fixture(scope="module")
def person():
    return _load(EVAL / "eval_person.json")


@pytest.fixture(scope="module")
def robustness():
    return _load(EVAL / "eval_robustness.json")


@pytest.fixture(scope="module")
def topics():
    return _topics()


# ---------------------------------------------------------------- 妥当性の4本


def test_no_topic_word_leak_in_hard_layers(person, topics):
    """L2/L3 のクエリに正解トピック名・そのキーワードが混入していないこと。

    混入していると BM25 だけで上位に来てしまい、埋め込みモデルの横並び比較で
    全モデルが飽和して差が出ない（旧セットは 24/40 = 60% が混入していた）。
    """
    leaked = []
    for q in person:
        if q["difficulty"] not in ("L2", "L3"):
            continue
        for t in q["gold_topics"]:
            hits = [w for w in [t, *topics[t]] if w in q["query"]]
            if hits:
                leaked.append((q["id"], t, hits))
    assert not leaked, f"L2/L3 にトピック語が漏れている: {leaked}"


def test_route_not_guessable_from_keywords(person):
    """表層キーワードが route の情報を持っていないこと。

    旧セットは「手順書/FAQ/マニュアル/過去/以前」の5語で 40/40 = 100% 当たった。
    それは経路判定の精度ではなくキーワード検出を測っている。

    判定は**多数クラス（常に person と答える）のベースラインとの差**で見る。
    絶対値で見ると、route 分布が person 偏重なだけで閾値を割ってしまい、
    「表層に情報があるか」を測れない。
    """
    kw = {
        "document": ["手順書", "FAQ", "マニュアル", "ドキュメント", "規程"],
        "prior_answer": ["過去", "以前", "流用", "前回"],
        "none": ["いますか", "探しています", "経験のある方"],
    }
    hit = 0
    for q in person:
        guess = "person"
        for route, words in kw.items():
            if any(w in q["query"] for w in words):
                guess = route
                break
        hit += guess == q["gold_route"]
    keyword_acc = hit / len(person)

    dist = Counter(q["gold_route"] for q in person)
    majority_acc = dist.most_common(1)[0][1] / len(person)

    assert keyword_acc <= majority_acc + 0.10, (
        f"キーワードだけで route が {keyword_acc:.0%}（多数クラス {majority_acc:.0%}）。"
        "表層の言い回しが route を漏らしている"
    )


def test_answers_only_baseline_is_weak(person, topics):
    """「過去回答を topic で数えるだけ」のベースラインの Recall@3 が 0.6 未満であること。

    旧セットではこのベースラインが正解と 100% 一致していた。つまり20行の実装が満点を取れた。
    これは専門性推定の良し悪しを一切測っていない。
    """
    answers = _load(SYN / "answers" / "answers.json")
    by_topic = defaultdict(Counter)
    for a in answers:
        by_topic[a["topic"]][a["responder_id"]] += 1
    global_top = [e for e, _ in Counter(a["responder_id"] for a in answers).most_common(3)]

    scores = []
    for q in person:
        if q["difficulty"] == "L4" or not q["gold_experts"]:
            continue
        hits = [
            t for t, kws in topics.items() if t in q["query"] or any(k in q["query"] for k in kws)
        ]
        if hits:
            c = Counter()
            for t in hits:
                c.update(by_topic[t])
            pred = [e for e, _ in c.most_common(3)]
        else:
            pred = global_top
        gold = set(q["gold_experts"])
        scores.append(len(set(pred) & gold) / min(3, len(gold)))

    recall = sum(scores) / len(scores)
    assert recall < 0.60, (
        f"answers を数えるだけで Recall@3={recall:.3f}。正解と入力が同じ源になっている"
    )


def test_enough_independent_samples(person):
    """独立サンプル数（ユニークな正解集合）が 20 以上であること。

    正解がトピック単位で固定だと、同じトピックのクエリは全て同じ正解になり、
    件数を増やしても統計的なサンプルは増えない（旧セットは40件で実質21件）。

    なお上限は fixtures の構造で決まる。現在の合成データは 10部署×4名で、
    案件が部署単位に割り当たるため、トピック→専門家がほぼ部署に一意に決まる。
    ここを大きく増やすには **fixtures 側で案件を部署横断にする**必要がある。
    """
    uniq = {tuple(sorted(q["gold_experts"])) for q in person}
    assert len(uniq) >= 20, f"独立サンプルが {len(uniq)} 種類しかない"


# ---------------------------------------------------------------- 構成の担保


def test_difficulty_layers_present(person):
    dist = Counter(q["difficulty"] for q in person)
    assert dist == {"L1": 10, "L2": 15, "L3": 10, "L4": 5}, dist


def test_l4_expects_abstain(person):
    for q in person:
        if q["difficulty"] == "L4":
            assert not q["gold_experts"], f"L4 #{q['id']} に正解がある"
            assert q["expect_abstain"], f"L4 #{q['id']} が abstain でない"
            assert q["gold_route"] == "none"


def test_robustness_covers_all_categories(robustness):
    """「答えてはいけない/聞き返すべき」が5類型そろっていること。

    評価項目に「誤回答や想定外の入力への対策」が明文化されている（doc00 §2）。
    旧セットにはこの種の問題が1件も無かった。
    """
    assert len(robustness) == 20
    cats = Counter(r["category"] for r in robustness)
    assert set(cats) == {"out_of_scope", "pii", "insufficient", "no_expert", "adversarial"}, cats
    assert all(r["expect_abstain"] for r in robustness)


def test_foreign_keys_resolve(person):
    emp_ids = {e["id"] for e in _load(SYN / "people" / "employees.json")}
    unknown = sorted({e for q in person for e in q["gold_experts"] if e not in emp_ids})
    assert not unknown, f"未知の employee_id: {unknown}"


def test_queries_are_distinct(person):
    """クエリが実質的に重複していないこと（旧セットは6文型の穴埋めで38/40）。"""
    queries = [q["query"] for q in person]
    assert len(set(queries)) == len(queries)


def test_label_source_recorded(person):
    """どの正解が自動でどれが著述かを、後から説明できること。"""
    allowed = {"auto:project_daily", "authored"}
    bad = {q["label_source"] for q in person} - allowed
    assert not bad, f"未知の label_source: {bad}"


def test_retrieval_set_aligned_with_person(person):
    """層1（検索）が層2（推薦）と対応していること。

    人を外したとき、検索が悪いのかスコアリングが悪いのかを切り分けるために要る。
    """
    retrieval = _load(EVAL / "eval_retrieval.json")
    person_ids = {q["id"] for q in person if q["difficulty"] != "L4"}
    assert {r["id"] for r in retrieval} == person_ids
    assert all(r["gold_chunks"] for r in retrieval), "根拠チャンクが空の項目がある"
    assert not math.isnan(len(retrieval))
