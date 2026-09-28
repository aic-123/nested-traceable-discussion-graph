"""结构规则集的**行为**验证 —— 判定必须「没有能力写库」。

静态那半在 `checks.py` 的 **B22**（判据形态、词表封闭、档位钉死、没有模型 / 数据库入口）。
这里拦的是静态拦不住的那半：**跑一遍，库有没有被动过。**

⚠️ 本文件刻意只依赖 `scaffold.py` / `upper.py` / `rules.py` 与标准库 ——
和 `test_upper.py` 一个规矩：**能用几十行读懂的依赖，才算真的独立。**

--- 这个文件要证的四件事 -----------------------------------------------------

| 要证什么 | 靠哪几条 |
|---|---|
| 判定**一个字都没写库** | 整库快照比对（`evaluate` / `select` 各一条） |
| 判定**没有能力**写库 | 签名里没有 `conn`；模块里没有数据库入口 |
| **合取**确实说出了单条件说不了的话 | 同一份信号上跑 A 档规则与 B 档规则，两张清单必须不同 |
| 判定**不是分** | `explain` 不给合计；字段是白名单；排序是字母序 |

    python test_rules.py
"""

from __future__ import annotations

import inspect
import unittest

import rules
import scaffold
import upper


def _whole_db(conn) -> dict:
    """整库快照：**每一张表的每一行**。

    为什么不是只比某几张表：判定若真的写了库，它写到哪张表是**不知道的** ——
    只盯 `artifact` / `relation` 就是给这条用例留了个后门。
    """
    out = {}
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        out[name] = sorted(map(tuple, conn.execute(f"SELECT * FROM {name}")))
    return out


def _targets(out: dict) -> set:
    return {m["target"] for m in out["matches"]}


class Base(unittest.TestCase):
    """一个真库：两条命题 ——

    * 一条**既被反复质询、又被反复修正**
    * 一条**只被反复质询**

    ⚠️ 为什么必须两条：B 档与 A 档的差别就在**合取**上。
    只有一条命题的话，合取与单条件会给出同一张清单 —— **那就什么都测不出来。**
    """

    def setUp(self):
        self.conn = scaffold.connect(":memory:")
        scaffold.init(self.conn)
        self.debate = scaffold.add_artifact(
            self.conn, type_="Debate", content={"question": "该不该 A？"},
            origin="alice")
        scaffold.activate(self.conn, self.debate, by="alice")

    def _claim(self, text: str) -> str:
        claim = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": text}, origin="alice")
        scaffold.activate(self.conn, claim, by="alice")
        return claim

    def _challenge(self, claim: str, n: int) -> None:
        for i in range(n):
            c = scaffold.add_artifact(
                self.conn, type_="Counterargument",
                content={"text": f"反驳{i}"}, origin="bob")
            scaffold.activate(self.conn, c, by="bob")
            scaffold.add_relation(self.conn, kind="challenged_by",
                                  from_id=claim, to_id=c, origin="bob")

    def _revise(self, artifact: str, n: int) -> None:
        for i in range(n):
            scaffold.revise(self.conn, artifact,
                            content={"text": f"改写{i}"}, author="alice")

    def _fixture(self) -> tuple[str, str]:
        """(既被质询又被修正的, 只被质询的)。"""
        both = self._claim("假设甲")
        only = self._claim("假设乙")
        self._challenge(both, 3)
        self._revise(both, 2)
        self._challenge(only, 3)
        return both, only

    def _signals(self) -> dict:
        return upper.count_signals(self.conn)

    def _challenger_ids(self, claim: str) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT to_id FROM relation WHERE from_id=? AND kind='challenged_by'",
            (claim,))]

    def _lower_snapshot(self, ids: list[str]) -> dict:
        q = ",".join("?" * len(ids))
        return {
            "rev": sorted(map(tuple, self.conn.execute(
                f"SELECT id, artifact_id, parent_rev, content FROM revision"
                f" WHERE artifact_id IN ({q})", ids).fetchall())),
            "state": sorted(map(tuple, self.conn.execute(
                f"SELECT id, state FROM artifact WHERE id IN ({q})", ids).fetchall())),
        }

    def _only(self, rid: str) -> dict:
        """只留一条规则跑 —— 这样「哪条规则说了什么」才分得开。"""
        return {rid: rules.RULES[rid]}


class TestTheJudgementCannotWrite(Base):
    """`§C2.5` 第 3 档（派生视图）免确认的**全部理由**：它没有能力写库。

    「作者记得别写」不算理由 —— 那是纪律。这一组要把它证成**结构**。
    """

    def test_evaluate_writes_nothing_at_all(self):
        """整库逐行比对：跑一遍 `evaluate`，一行都不许变。"""
        self._fixture()
        sig = self._signals()
        self.assertTrue(sig["challenge_counts"], "造了质询却没有信号 —— 用例没测到东西")

        before = _whole_db(self.conn)
        out = rules.evaluate(sig)
        self.assertTrue(out["matches"], "规则集一条都没匹配上 —— 用例没测到东西")
        self.assertEqual(_whole_db(self.conn), before, "evaluate 动了库")

    def test_select_writes_nothing_either(self):
        """`select` 也一样 —— 它是**筛选**，不是提拔。"""
        self._fixture()
        cands = upper.propose_clusters(self.conn)["candidates"]
        self.assertTrue(cands, "没出候选 —— 用例没测到东西")

        before = _whole_db(self.conn)
        out = rules.select(cands, self._signals(), rule="contested_and_revised")
        self.assertTrue(out["selected"], "筛完一条不剩 —— 用例没测到东西")
        self.assertEqual(_whole_db(self.conn), before, "select 动了库")

    def test_the_evaluator_has_no_way_to_write(self):
        """★ 免确认靠的是**没有能力**，不是纪律。

        签名里没有连接对象，模块里也没有数据库入口 ——
        这两条合起来，`evaluate()` 想写库也无从下手。
        """
        params = set(inspect.signature(rules.evaluate).parameters)
        self.assertNotIn("conn", params)
        self.assertNotIn("db", params)
        for name in ("sqlite3", "connect", "cursor"):
            self.assertFalse(hasattr(rules, name),
                             f"rules 模块里出现了 {name} —— 它就有能力写库了")


class TestTheConjunctionIsTheWholePoint(Base):
    """**B 档相对 A 档的增量就是合取。** 这一组把它证出来。

    若这一组全绿而 `contested_and_revised` 换成单条件也全绿，
    那「用 B 档」这个决定在代码里就没有落点 —— 白拍了。
    """

    def test_the_conjunction_says_something_a_single_count_cannot(self):
        """★ 「用 B 档」在代码里的**可指认落点**。

        同一份信号上跑两条规则：

            repeatedly_contested    （A 档形态，单条件）→ 两条命题都进来
            contested_and_revised   （B 档，合取）      → 只有那条真的又被质询又被修正的

        **两张清单不一样。** A 档写不出这一条 —— 它一次只能说一个量。
        """
        both, only = self._fixture()
        sig = self._signals()

        single = _targets(rules.evaluate(sig, rules=self._only("repeatedly_contested")))
        joint = _targets(rules.evaluate(sig, rules=self._only("contested_and_revised")))

        self.assertEqual(single, {both, only})
        self.assertEqual(joint, {both})
        self.assertTrue(joint < single, "合取没有比单条件更窄 —— 那它什么都没多说")

    def test_a_target_that_satisfies_only_one_condition_does_not_match(self):
        """★ 反向判据：**防止「合取」被悄悄写成「析取」**。

        只满足一个条件的命题进来了，就说明 `all_of` 变成了 `any_of` ——
        而那是**换了一档判据**（B 档退回 A 档的并集），不是实现细节。
        """
        both, only = self._fixture()
        sig = self._signals()
        hits = _targets(rules.evaluate(sig, rules=self._only("contested_and_revised")))
        self.assertNotIn(only, hits, "只满足一个条件也进来了 —— 合取退化成析取了")
        # 反向的反向：它**确实**满足了其中一个条件，
        # 所以「没进来」不是因为「它什么都没满足」（那样这条用例就是空的）
        self.assertGreaterEqual(sig["challenge_counts"].get(only, 0), rules.FLOOR)

    def test_a_rule_filtered_candidate_is_still_safe_to_promote(self):
        """规则筛完的候选，走 `promote_candidates()` 仍然**只写上层**。

        这一步把「B 档判据」接回既有的单向性约束：**规则集只管筛，
        写仍然走原来那条被 B14 盯着的路** —— 判定自己一个字都不写。
        """
        both, only = self._fixture()
        cands = upper.propose_clusters(self.conn)["candidates"]
        picked = rules.select(
            cands, self._signals(), rule="contested_and_revised")["selected"]

        ids = ([both, only] + self._challenger_ids(both)
               + self._challenger_ids(only))
        before = self._lower_snapshot(ids)
        made = upper.promote_candidates(
            self.conn, candidates=picked, by="alice", debate_id=self.debate)
        self.assertEqual(len(made), 1, "筛出来的候选没建出正好一个上层节点")
        self.assertEqual(self._lower_snapshot(ids), before, "promote 动了底层")


class TestTheVocabularyIsClosed(Base):
    """词表封闭 —— 打错一个字母**要抛**，不是「读不到当 0」。

    后者会让空清单长得跟「库里确实没有候选」**一模一样**，
    而那正是 `observe.py`「算不出 ≠ 零」要防的同一件事。
    """

    def test_a_signal_outside_the_vocabulary_is_refused(self):
        bogus = {"x": {"all_of": (("vote_counts", ">=", 2),),
                       "why": "一条读热度类信号的规则"}}
        with self.assertRaises(rules.RuleError):
            rules.evaluate({}, rules=bogus)

    def test_an_unknown_operator_is_refused(self):
        with self.assertRaises(rules.RuleError):
            rules.holds(3, "~=", 2)

    def test_a_condition_that_is_not_a_triple_is_refused(self):
        bogus = {"x": {"all_of": (("challenge_counts", ">="),), "why": "少了一格"}}
        with self.assertRaises(rules.RuleError):
            rules.evaluate({}, rules=bogus)

    def test_select_only_takes_a_declared_rule_name(self):
        """★ 阶段 5 出口判据 ③ 的落地：**模型判断传不进来**。

        不是「我们约定不传模型判断」，而是唯一能传的是一个**字符串**，
        它必须命中 `RULES`。传函数、传一句话、传一个模型的输出，一律 `RuleError`。
        """
        for bad in (lambda c: True, "模型觉得可以", "the model says yes", 1):
            with self.assertRaises(rules.RuleError, msg=f"{bad!r} 竟然被接受了"):
                rules.select([], {}, rule=bad)


class TestTheJudgementIsNotAScalar(Base):
    """**B 档与 C 档的分界线**：规则的输出只能是「进不进清单」，不得是「有多好」。

    一旦能算，规则集立刻能表达加权和 —— 那就是打分（撞 B5）。
    这一组用**形状**来守它，不是用「我们约定不这么写」。

    ⚠️ 类名里刻意没有那个词：B2 是**按子串扫的**（`weight|score|rank|truth`），
    分不出语义。撞上就改名 —— 这是本仓库既有的规矩，
    而不是把 `test_rules.py` 加进 `EXEMPT`（那正是检查死掉的路）。
    """

    def test_explain_gives_no_total(self):
        """★ 逐条件给，**不给「满足了几条」** —— 一合计就是个分。

        「2 条里满足 1 条」读起来像 50 分，而它本来只该回答「成立 / 不成立」。
        """
        both, only = self._fixture()
        detail = rules.explain(self._signals(), rule="contested_and_revised",
                               target=only)
        self.assertEqual(len(detail), 2)
        for row in detail:
            self.assertEqual(
                set(row), {"signal", "operator", "required", "actual", "held"},
                "explain 多给了一个字段 —— 多出来的那个如果是个合计，它就是个分",
            )

    def test_the_field_sets_are_closed(self):
        """判定清单与每条匹配的字段都是**白名单** —— 谁也别想加一个 `confidence`。"""
        self._fixture()
        out = rules.evaluate(self._signals())
        self.assertEqual(set(out),
                         {"version", "tier", "rules_used", "matches", "note"})
        self.assertTrue(out["matches"], "没匹配上 —— 用例没测到东西")
        for m in out["matches"]:
            self.assertEqual(set(m), {"rule", "target", "readings", "why"})

    def test_the_order_is_alphabetical_not_by_how_good(self):
        """排序按 `(规则名, 目标 id)` —— **字母序，不是好坏序**。

        按「满足了几条」或「读数多大」排，它立刻变成名次。
        """
        self._fixture()
        keys = [(m["rule"], m["target"])
                for m in rules.evaluate(self._signals())["matches"]]
        self.assertEqual(keys, sorted(keys),
                         "matches 不是字母序 —— 那它就在按别的什么排")


class TestTheTierIsPinned(Base):
    """档位钉死在第 3 档。挪一档就要补三件事，那是**设计变更**。"""

    def test_the_judgement_is_a_derived_view(self):
        """`§C2.5` 第 3 档：默认生效 + 可追溯 + 可解释 + 可回退。**不需确认。**"""
        self.assertEqual(rules.TIER, "derived_view")
        self.assertIn(rules.TIER, rules.C25_TIERS)

        out = rules.evaluate({})
        self.assertEqual(out["tier"], "derived_view")
        # 「可追溯」：用了哪些规则，判定自己带着
        self.assertEqual(set(out["rules_used"]), set(rules.RULES))

    def test_the_empty_result_says_what_it_can_see(self):
        """**「没有」和「算不出」不是一回事** —— 空结果必须自述它读了什么。"""
        text = rules.render(rules.evaluate({}))
        for name in rules.SIGNAL_VOCAB:
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
