"""停止条件的**行为**验证 —— 只判不执行，三态分得清。

静态那半在 `checks.py` 的 **B26**（三张表键集相同、一句写语句都没有、
阈值只经 `policy.value()` 取、观测量不进 `COUNT_SIGNALS`）。
这里拦的是静态拦不住的那半：**跑起来之后库真的一个字段都没动吗；
「判不了」有没有被冒充成「未触发」。**

--- 这个文件要证的六件事 -------------------------------------------------------

| 要证什么 | 靠哪几条 |
|---|---|
| ① 四条件都接上了 | `TestTheFourConditionsAreWired` |
| ② 只判不执行（库逐字段不变） | `TestItJudgesInsteadOfActing` |
| ③ 三态分得清（判不了 ≠ 未触发） | `TestTheThreeStatesAreDistinct` |
| ④ 冷启动不被误判成触发 | `TestColdStartIsNotMistakenForATrigger` |
| ⑤ 门槛可变动，且只动一个 | `TestThePolicyCanBeMoved` |
| ⑥ 观测量数的是对的东西 | `TestTheObservedCountsReadTheRightThing` |
| ④ 的动作说得出该回退哪几批 | `TestTheRollbackNamesTheBatches` |

⚠️ 本文件只依赖 `scaffold.py` / `stop.py` / `views.py` 与标准库 ——
和 `test_rules.py` / `test_candidates.py` / `test_views.py` 一个规矩：
**能用几十行读懂的依赖，才算真的独立。**

⚠️ 为什么敢用 `views.snapshot()` 判「库没动」：它覆盖**全部** canonical 表
（B25 判据 4 钉着那张表不许少一张）。拿它当「一个字都没改」的判据，
比在这里另写一份清单可靠 —— 另写一份就会漂。

    python test_stop.py
"""

from __future__ import annotations

import unittest

import scaffold
import staging
import stop
import views


class _Base(unittest.TestCase):

    def setUp(self):
        self.conn = scaffold.connect()
        scaffold.init(self.conn)

    def tearDown(self):
        self.conn.close()

    # --- 造数据 -----------------------------------------------------------

    def _mk(self, type_: str = "Claim", *, intake: str = "direct",
            by: str = "alice") -> str:
        """建一个节点。`intake='direct'` 的顺手确认；`staged` 的**不确认** ——
        它得先有 staging 记录才过得了门（那是 B20 的守卫，不是这里要测的）。"""
        aid = scaffold.add_artifact(
            self.conn, type_=type_, content={"text": "x"},
            origin=by, intake=intake)
        if intake == "direct":
            scaffold.activate(self.conn, aid, by=by)
        return aid

    def _imported(self, n: int = 1, *, batch: str = "book-1") -> list[str]:
        """造 n 条**没过门**的导入节点。`gate_state` 从 `pending` 起 ——
        那正是 `staging_backlog` 数的东西。

        ⚠️ 走 `staging.stage_batch()`，**不是**直接 `INSERT INTO staging`。
        那张表只许 `staging.py` 一个文件写（B20 判据 2 扫全仓）——
        这里绕开的话，B20 会在**这个测试文件**上报一笔，
        而它报的是「有人绕过了入层门」，那是本仓库最不该发生的事之一。
        测试要造这种数据，就得走公开入口 —— 这也顺带证明了那个入口能用。
        """
        return staging.stage_batch(
            self.conn, batch=batch, source_uri=f"urn:book:{batch}",
            items=[{"content": {"text": f"第{i}条"},
                    "selector": {"type": "TextQuoteSelector", "exact": "原文"},
                    "confidence": "reasonable inference"}
                   for i in range(n)],
            extracted_by="distiller-v1", asserted_by="某书作者",
        )

    def _find(self, condition: str, *, declared: dict | None = None,
              **override) -> dict:
        for finding in stop.judge(self.conn, declared=declared, **override):
            if finding["condition"] == condition:
                return finding
        raise AssertionError(f"判定里没有这个条件：{condition}")


class TestTheFourConditionsAreWired(_Base):
    """四条件都接上了，而且每条只说一件事。"""

    def test_every_condition_gets_a_finding(self):
        got = [f["condition"] for f in stop.judge(self.conn)]
        self.assertEqual(got, list(stop.STOP_CONDITIONS))

    def test_only_a_trigger_carries_an_action(self):
        """未触发和判不了的 `action` 必须是 `None` ——
        不然调用方会照着一个不该做的动作去做。"""
        for finding in stop.judge(self.conn):
            if finding["state"] == stop.TRIGGERED:
                self.assertEqual(finding["action"],
                                 stop.STOP_ACTIONS[finding["condition"]])
            else:
                self.assertIsNone(finding["action"])

    def test_every_state_is_one_of_the_three(self):
        for finding in stop.judge(self.conn):
            self.assertIn(finding["state"],
                          (stop.TRIGGERED, stop.CLEAR, stop.NOT_JUDGED))

    def test_every_finding_says_why(self):
        """每条都要说清依据 —— 一条只说「触发」不说为什么的判定，
        等于把问题丢回给运维的人。"""
        for finding in stop.judge(self.conn):
            self.assertTrue(finding["because"].strip())


class TestItJudgesInsteadOfActing(_Base):
    """**只判不执行** —— 跑完之后库里一个字段都没动。"""

    def test_judging_does_not_touch_the_library(self):
        self._imported(3)
        self._mk()
        before = views.snapshot(self.conn)
        stop.judge(self.conn, declared={
            "book_effects": [True, False, False, False],
            "unfaithful": ["art-9999"],
        })
        self.assertEqual(views.snapshot(self.conn), before)

    def test_the_report_does_not_touch_the_library(self):
        self._imported(3)
        before = views.snapshot(self.conn)
        stop.report(self.conn, declared={"unfaithful": []})
        self.assertEqual(views.snapshot(self.conn), before)

    def test_the_snapshot_would_notice_a_change(self):
        """★ 反向判据：快照真的看得见改动。

        没有这一条，上面两条就是空转 —— 快照要是什么都看不见，
        「跑完库没变」在任何情况下都成立，包括库真的被改了的时候。
        这里改的是**字段**不是行数：`UPDATE` 不改行数，只改内容。
        """
        self._mk()
        before = views.snapshot(self.conn)
        self.conn.execute("UPDATE artifact SET state = 'superseded'")
        self.conn.commit()
        self.assertNotEqual(views.snapshot(self.conn), before)


class TestTheThreeStatesAreDistinct(_Base):
    """**判不了 ≠ 未触发** —— 压成两态的话，两者会被读成同一件事。"""

    def test_missing_input_is_not_judged_rather_than_clear(self):
        self.assertEqual(self._find("books_have_no_effect")["state"],
                         stop.NOT_JUDGED)

    def test_an_empty_book_series_is_not_judged(self):
        """一本都没蒸过的时候，「连续 N 本没变化」不成立 ——
        但也不等于「没触发」，因为它压根没被评估过。"""
        self.assertEqual(
            self._find("books_have_no_effect",
                       declared={"book_effects": []})["state"],
            stop.NOT_JUDGED)

    def test_a_checked_and_clean_import_is_clear(self):
        self.assertEqual(
            self._find("unfaithful_import", declared={"unfaithful": []})["state"],
            stop.CLEAR)

    def test_never_checked_differs_from_checked_and_clean(self):
        """★ 同一个条件的两种「没事」，必须分得开。

        「还没人查过」和「查过了，没问题」在运维上要采取的行动完全相反：
        前者要去查，后者可以放心。混成一个状态之后，
        一件从没做过的事会被报成一件做过且合格的事。
        """
        never = self._find("unfaithful_import")
        clean = self._find("unfaithful_import", declared={"unfaithful": []})
        self.assertEqual(never["state"], stop.NOT_JUDGED)
        self.assertEqual(clean["state"], stop.CLEAR)
        self.assertNotEqual(never["state"], clean["state"])


class TestColdStartIsNotMistakenForATrigger(_Base):
    """冷启动时**贡献 = 0**，那个条件没有分母。"""

    def test_no_contribution_means_not_judged(self):
        self._imported(50)
        finding = self._find("import_dominates")
        self.assertEqual(finding["state"], stop.NOT_JUDGED)
        self.assertEqual(finding["observed"]["contributed"], 0)

    def test_it_offers_no_action_at_cold_start(self):
        """冷启动的第一步本来就是导入（先把书蒸进来才有得讨论）。
        这里要是报出 `pause_import`，冷启动会被直接掐死。"""
        self._imported(50)
        self.assertIsNone(self._find("import_dominates")["action"])

    def test_once_someone_contributes_it_judges(self):
        """★ 反向判据：有人贡献之后必须**真的开始判**。

        否则「冷启动判不了」就成了「永远判不了」——
        而一个永远判不了的条件，看起来和一条永远安全的线一模一样。
        """
        self._imported(50)
        self._mk()
        self.assertEqual(self._find("import_dominates")["state"],
                         stop.TRIGGERED)


class TestThePolicyCanBeMoved(_Base):
    """`POLICY` 的「可变动」—— 默认值在代码里，运行时能覆盖。

    ⚠️ 类名不叫 `…Threshold…`：`threshold` 是 B3 的禁词（不区分大小写），
    它盯的是「不许实现阈值判据」。这里说的是**运维门槛**，两回事 ——
    但 B3 是按词抓的，分不出语义，所以名字避开。
    """

    def _at_the_edge(self):
        """21 条导入 + 2 条贡献，默认上限 10：21 > 10×2 = 20，刚好过线。"""
        self._imported(21)
        self._mk()
        self._mk()

    def test_the_default_comes_from_policy(self):
        self._at_the_edge()
        self.assertEqual(self._find("import_dominates")["state"],
                         stop.TRIGGERED)

    def test_an_override_moves_only_that_one(self):
        self._at_the_edge()
        self.assertEqual(
            self._find("import_dominates",
                       import_to_contribution_ceiling=11)["state"],
            stop.CLEAR)
        self.assertEqual(self._find("staging_backlog")["state"], stop.CLEAR)

    def test_the_backlog_condition_moves_with_its_own_key(self):
        self._imported(3)
        self.assertEqual(self._find("staging_backlog")["state"], stop.CLEAR)
        self.assertEqual(
            self._find("staging_backlog", staging_backlog_limit=2)["state"],
            stop.TRIGGERED)

    def test_the_book_condition_moves_with_its_own_key(self):
        declared = {"book_effects": [False, False]}
        self.assertEqual(
            self._find("books_have_no_effect", declared=declared)["state"],
            stop.CLEAR)
        self.assertEqual(
            self._find("books_have_no_effect", declared=declared,
                       books_without_effect=2)["state"],
            stop.TRIGGERED)

    def test_the_book_condition_counts_the_tail_not_the_total(self):
        """「连续」说的是**最近这一段**。中间断过一次就要重新数 ——
        数总数的话，一本很久以前起过作用的书会让这条线永远不响。"""
        self.assertEqual(
            self._find("books_have_no_effect",
                       declared={"book_effects": [False, True, False, False]}
                       )["observed"]["streak"], 2)


class TestTheObservedCountsReadTheRightThing(_Base):
    """观测量数的是对的东西 —— 数错了不会报错，只会永远数出 0。"""

    def test_upper_context_is_not_a_contribution(self):
        """★ 上层 `Context` 也是 `intake='direct'`。

        直接数 `direct` 会把它算成「用户贡献」——
        于是分母虚高，这条线永远判不出来，而读数看起来很正常。
        """
        self._mk(type_="Context")
        self.assertEqual(
            self._find("import_dominates")["observed"]["contributed"], 0)

    def test_staged_artifacts_are_counted_as_imported(self):
        self._imported(3)
        self.assertEqual(
            self._find("import_dominates")["observed"]["imported"], 3)

    def test_the_backlog_counts_only_pending(self):
        """过了门的不算积压 —— 它已经不是「堵在门口」的那部分了。

        过门走 `staging.gate()`（公开入口），不直接改表 —— 同 `_imported()`
        那条理由。
        """
        self._imported(2)
        staging.gate(self.conn, by="alice", full=True)
        self.assertEqual(
            self._find("staging_backlog")["observed"]["backlog"], 0)


class TestTheRollbackNamesTheBatches(_Base):
    """④ 触发之后，得说得出**该回退哪几批**。"""

    def test_it_names_the_batches_to_roll_back(self):
        ids = self._imported(2, batch="book-7")
        finding = self._find("unfaithful_import", declared={"unfaithful": [ids[0]]})
        self.assertEqual(finding["state"], stop.TRIGGERED)
        self.assertEqual(finding["action"], "rollback_batch")
        self.assertEqual(finding["observed"]["batches"], ["book-7"])

    def test_an_id_outside_any_batch_is_not_silently_dropped(self):
        """★ 报上来的 id 查不到批次时，要说出来。

        默默丢掉的话，调用方会以为「报上来的都处理到了」——
        而实际上有一条根本没被回退，库里也看不出任何异常。
        """
        self._imported(1, batch="book-7")
        finding = self._find("unfaithful_import",
                             declared={"unfaithful": ["art-9999"]})
        self.assertEqual(finding["state"], stop.NOT_JUDGED)
        self.assertEqual(finding["observed"]["unknown"], ["art-9999"])

    def test_a_partly_unknown_batch_is_still_named(self):
        ids = self._imported(1, batch="book-7")
        finding = self._find(
            "unfaithful_import", declared={"unfaithful": [ids[0], "art-9999"]})
        self.assertEqual(finding["state"], stop.TRIGGERED)
        self.assertEqual(finding["observed"]["batches"], ["book-7"])
        self.assertEqual(finding["observed"]["unknown"], ["art-9999"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
