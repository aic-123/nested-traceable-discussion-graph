"""候选关系的**行为**验证 —— 机器提的边进得了库，但进不了读数。

静态那半在 `checks.py` 的 **B24**（状态在词表里、签名里没有 `state`、
两条通路恰好一条、import 白名单）。这里拦的是静态拦不住的那半：
**建出来之后，读数真的没变吗。**

--- 这个文件要证的八件事 -----------------------------------------------------

| 要证什么 | 靠哪几条 |
|---|---|
| 候选边**不进任何读数** | `TestACandidateNeverCounts` |
| 只有**两条**通路能把它变成算数的，且恰好一条 | `TestOnlyOneRouteToReal` |
| 模型判断**当不了** promote 条件 | `TestTheModelCannotBeTheCondition` |
| 入口**没有能力**写 `active` | `TestTheEntryCannotWriteActive` |
| 候选边**可被否掉**，且否掉不等于推翻 | `TestACandidateIsDismissible` |
| 规则通路**要么说清是哪条规则，要么拒绝** | `TestTheRuleRouteNamesItsRule` |
| 候选层**不碰上层结构** | `TestItStaysBelowTheUpperLayer` |
| 「谁提的」必须说得出是**哪一类**，没有兜底 | `TestWhoProposedItIsNamed` |

⚠️ 本文件刻意只依赖 `scaffold.py` / `candidates.py` / `rules.py` / `upper.py`
与标准库 —— 和 `test_rules.py` / `test_contribute.py` 一个规矩：
**能用几十行读懂的依赖，才算真的独立。**

    python test_candidates.py
"""

from __future__ import annotations

import inspect
import unittest

import candidates
import rules
import scaffold
import upper


class _Base(unittest.TestCase):

    def setUp(self):
        self.conn = scaffold.connect()
        scaffold.init(self.conn)

    def tearDown(self):
        self.conn.close()

    def _mk(self, type_: str, text: str, by: str = "alice") -> str:
        """建一个**已确认**的节点 —— 读数只认 active 的东西。"""
        aid = scaffold.add_artifact(
            self.conn, type_=type_, content={"text": text}, origin=by)
        scaffold.activate(self.conn, aid, by=by)
        return aid

    def _state(self, relation_id: int) -> str:
        return self.conn.execute(
            "SELECT state FROM relation WHERE id = ?", (relation_id,)
        ).fetchone()["state"]

    def _kinds(self, kind: str) -> list:
        return self.conn.execute(
            "SELECT * FROM relation WHERE kind = ? ORDER BY id", (kind,)
        ).fetchall()


# ---------------------------------------------------------------------------
# ① 候选边不进读数
# ---------------------------------------------------------------------------

class TestACandidateNeverCounts(_Base):
    """阶段 5b 出口判据 ①：**AI 产出只能是 candidate。**

    这里验的是那句话的**后果**：一条候选边记进库之后，
    `upper.count_signals()` 的输出**逐字不变**。

    ⚠️ 为什么这条要用 `assertEqual` 比整份字典而不是只比一个数：
    只比一个数的话，候选边从另一个信号漏进读数就查不出来 ——
    而 `count_signals()` 有三个信号，抄漏一处正好是这里要防的事。
    """

    def test_the_state_word_exists_at_all(self):
        """★ 回归：`proposed` 必须真的在关系状态词表里。

        这是阶段 5b 之前**落不了地**的原因 —— 词表里没有它，
        `add_relation(state="proposed")` 会当场抛，而那是运行时才发现的事。
        """
        self.assertIn(candidates.CANDIDATE_STATE, scaffold.RELATION_STATES)

    def test_the_candidate_state_is_not_a_counted_state(self):
        """候选边不算数 —— 这件事有名字（`COUNTED_RELATION_STATES`）。"""
        self.assertNotIn(candidates.CANDIDATE_STATE,
                         scaffold.COUNTED_RELATION_STATES)
        self.assertEqual(scaffold.COUNTED_RELATION_STATES, ("active",))

    def test_a_candidate_challenged_by_edge_does_not_move_the_reading(self):
        """一条候选的 `challenged_by` 边，读数**一个字都不动**。"""
        claim = self._mk("Claim", "稀缺性来自供给的不可复制")
        carg = self._mk("Counterargument", "那个样本只覆盖一线城市", by="bob")
        before = upper.count_signals(self.conn)

        out = candidates.record(self.conn, kind="challenged_by", left=claim,
                                right=carg, origin="ai:gpt")
        self.assertEqual(out["state"], "proposed")
        self.assertTrue(out["needs_confirmation"])
        self.assertEqual(upper.count_signals(self.conn), before)

    def test_a_candidate_contradicts_edge_does_not_move_the_reading(self):
        """换一条信号（`dispute_counts`）再验一遍 —— 三个信号不能只堵一个。"""
        left = self._mk("Claim", "甲")
        right = self._mk("Claim", "乙", by="bob")
        before = upper.count_signals(self.conn)

        candidates.record(self.conn, kind="contradicts", left=left, right=right,
                          origin="ai:gpt")
        self.assertEqual(upper.count_signals(self.conn), before)

    def test_promoting_it_does_move_the_reading(self):
        """★ 上一条的**反向判据**：提拔之后读数**必须**变。

        只验「候选不进读数」的话，一条**从来不进读数**的边也能过 ——
        而那种边等于没建。所以必须同时证明「提拔之后它进来了」。
        """
        claim = self._mk("Claim", "稀缺性来自供给的不可复制")
        carg = self._mk("Counterargument", "那个样本只覆盖一线城市", by="bob")
        out = candidates.record(self.conn, kind="challenged_by", left=claim,
                                right=carg, origin="ai:gpt")
        self.assertEqual(upper.count_signals(self.conn)["challenge_counts"], {})

        candidates.promote(self.conn, relation_id=out["relation"], by="carol")
        self.assertEqual(
            upper.count_signals(self.conn)["challenge_counts"], {claim: 1})

    def test_a_candidate_is_invisible_to_the_upper_layers_support_lookup(self):
        """`upper._supporting_ids()` 也读不到候选边 —— 第二处读数。"""
        claim = self._mk("Claim", "甲")
        carg = self._mk("Counterargument", "乙", by="bob")
        out = candidates.record(self.conn, kind="challenged_by", left=claim,
                                right=carg, origin="ai:gpt")
        self.assertEqual(
            upper._supporting_ids(self.conn, claim, "challenge_counts"), [])
        candidates.promote(self.conn, relation_id=out["relation"], by="carol")
        self.assertEqual(
            upper._supporting_ids(self.conn, claim, "challenge_counts"), [carg])

    def test_proposed_lists_exactly_the_unconfirmed_ones(self):
        a = self._mk("Claim", "甲")
        b = self._mk("Claim", "乙", by="bob")
        r1 = candidates.record(self.conn, kind="related_to", left=a, right=b,
                               origin="ai:gpt")
        r2 = candidates.record(self.conn, kind="contrasts", left=a, right=b,
                               origin="ai:gpt")
        candidates.promote(self.conn, relation_id=r1["relation"], by="carol")

        left = [it["id"] for it in candidates.proposed(self.conn)]
        self.assertEqual(left, [r2["relation"]])

    def test_render_says_they_do_not_count(self):
        a = self._mk("Claim", "甲")
        b = self._mk("Claim", "乙", by="bob")
        candidates.record(self.conn, kind="related_to", left=a, right=b,
                          origin="ai:gpt")
        text = candidates.render(candidates.proposed(self.conn))
        self.assertIn("不算数", text)
        self.assertIn("ai:gpt", text)

    def test_render_of_an_empty_list_distinguishes_nothing_from_failure(self):
        """「没有」和「算不出」不是一回事 —— 空清单必须自述它查了什么。"""
        text = candidates.render(candidates.proposed(self.conn))
        self.assertIn("不是一回事", text)
        self.assertIn(candidates.CANDIDATE_STATE, text)


# ---------------------------------------------------------------------------
# ② 只有两条通路，恰好一条
# ---------------------------------------------------------------------------

class TestOnlyOneRouteToReal(_Base):
    """阶段 5b 出口判据 ②：**promote 只能由人 / 结构规则触发。**"""

    def setUp(self):
        super().setUp()
        self.a = self._mk("Claim", "甲")
        self.b = self._mk("Claim", "乙", by="bob")

    def _a_candidate(self, kind: str = "related_to") -> int:
        return candidates.record(
            self.conn, kind=kind, left=self.a, right=self.b,
            origin="ai:gpt")["relation"]

    def test_a_person_can_promote_it(self):
        rid = self._a_candidate()
        out = candidates.promote(self.conn, relation_id=rid, by="carol")
        self.assertEqual(out["route"], "human")
        self.assertEqual(out["by"], "carol")
        self.assertEqual(self._state(rid), "active")

    def test_a_structural_rule_can_promote_it(self):
        claim = self._mk("Claim", "被反复质询的那条")
        for text in ("质询一", "质询二"):
            scaffold.add_relation(
                self.conn, kind="challenged_by", from_id=claim,
                to_id=self._mk("Counterargument", text, by="bob"), origin="bob")
        rid = candidates.record(self.conn, kind="related_to", left=claim,
                                right=self.a, origin="ai:gpt")["relation"]
        out = candidates.promote(
            self.conn, relation_id=rid, rule="repeatedly_contested",
            signals=upper.count_signals(self.conn))
        self.assertEqual(out["route"], "structural_rule")
        self.assertIsNone(out["by"])
        self.assertEqual(self._state(rid), "active")

    def test_both_routes_at_once_is_refused(self):
        rid = self._a_candidate()
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=rid, by="carol",
                               rule="repeatedly_contested",
                               signals=upper.count_signals(self.conn))
        self.assertEqual(self._state(rid), "proposed")

    def test_no_route_at_all_is_refused(self):
        """一条都不给 = **系统自己提拔自己** —— 这正是要拦的那个动作。"""
        rid = self._a_candidate()
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=rid)
        self.assertEqual(self._state(rid), "proposed")

    def test_the_two_route_names_are_a_closed_set(self):
        self.assertEqual(set(candidates.PROMOTE_ROUTE_NAMES),
                         set(candidates.PROMOTE_ROUTES))

    def test_the_event_records_which_route_it_took(self):
        """事后必须查得出「人点头」还是「规则推的」（`§C2.4` 要算这个比例）。"""
        rid = self._a_candidate()
        candidates.promote(self.conn, relation_id=rid, by="carol")
        rows = scaffold.events_of_kind(self.conn, "candidate_promoted")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor"], "carol")
        self.assertIn('"route": "human"', rows[0]["payload"])

    def test_the_rule_route_names_the_rule_in_the_actor_column(self):
        claim = self._mk("Claim", "被反复质询的那条")
        for text in ("质询一", "质询二"):
            scaffold.add_relation(
                self.conn, kind="challenged_by", from_id=claim,
                to_id=self._mk("Counterargument", text, by="bob"), origin="bob")
        rid = candidates.record(self.conn, kind="related_to", left=claim,
                                right=self.a, origin="ai:gpt")["relation"]
        candidates.promote(self.conn, relation_id=rid,
                           rule="repeatedly_contested",
                           signals=upper.count_signals(self.conn))
        rows = scaffold.events_of_kind(self.conn, "candidate_promoted")
        self.assertEqual(rows[0]["actor"],
                         f"{candidates.RULE_ACTOR_PREFIX}repeatedly_contested")

    def test_the_event_remembers_who_proposed_it(self):
        rid = self._a_candidate()
        candidates.promote(self.conn, relation_id=rid, by="carol")
        payload = scaffold.events_of_kind(
            self.conn, "candidate_promoted")[0]["payload"]
        self.assertIn('"proposed_by": "ai:gpt"', payload)

    def test_an_already_active_relation_cannot_be_promoted_again(self):
        rid = self._a_candidate()
        candidates.promote(self.conn, relation_id=rid, by="carol")
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=rid, by="dave")

    def test_an_unknown_relation_is_refused(self):
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=99999, by="carol")


# ---------------------------------------------------------------------------
# ③ 模型判断当不了 promote 条件
# ---------------------------------------------------------------------------

class TestTheModelCannotBeTheCondition(_Base):
    """阶段 5b 出口判据 ③：**把模型判断当 promote 条件会被拒绝。**

    ⚠️ 落法是「传不进来」，不是「约定别传」：`rule` 只能是一个字符串，
    它会被拿去查 `rules.RULES` 那张常量表。**没有地方放一个函数或一段模型输出。**
    """

    def setUp(self):
        super().setUp()
        self.a = self._mk("Claim", "甲")
        self.b = self._mk("Claim", "乙", by="bob")
        self.rid = candidates.record(
            self.conn, kind="related_to", left=self.a, right=self.b,
            origin="ai:gpt")["relation"]
        self.sig = upper.count_signals(self.conn)

    def _refuse(self, **kw):
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=self.rid, **kw)
        self.assertEqual(self._state(self.rid), "proposed")

    def test_a_callable_is_refused(self):
        self._refuse(rule=lambda signals: True, signals=self.sig)

    def test_a_model_output_looking_string_is_refused(self):
        """模型输出长得就是一段文字 —— 它必须**不是**一个已声明的规则名。"""
        self._refuse(rule="模型认为这两条相关，置信度 0.87", signals=self.sig)

    def test_a_rule_name_that_was_never_declared_is_refused(self):
        self._refuse(rule="looks_similar", signals=self.sig)

    def test_an_empty_rule_name_is_refused(self):
        self._refuse(rule="", signals=self.sig)

    def test_the_rule_name_must_be_declared_in_rules_rules(self):
        """正例：只有 `rules.RULES` 里那一串名字能用。"""
        self.assertTrue(rules.RULES)
        for name in rules.RULES:
            self.assertIsInstance(name, str)

    def test_a_declared_rule_that_does_not_hold_is_refused_not_smuggled(self):
        """规则成立不了 → **拒绝**，不是「兜住当它成立」。

        兜住之后这条边长得和正常提拔的一模一样 —— 那正是这个模块要防的。
        """
        self._refuse(rule="contested_and_revised", signals=self.sig)

    def test_the_rule_route_refuses_without_readings(self):
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=self.rid,
                               rule="repeatedly_contested")
        self.assertEqual(self._state(self.rid), "proposed")


# ---------------------------------------------------------------------------
# ④ 入口没有能力写 active
# ---------------------------------------------------------------------------

class TestTheEntryCannotWriteActive(_Base):
    """签名层面：**没有地方能填 `active`** —— 手法同 B14「没有 `conn` 就没法写库」。"""

    def test_the_signature_has_no_state_parameter(self):
        params = set(inspect.signature(candidates.record).parameters)
        for forbidden in candidates.FORBIDDEN_PARAMS:
            self.assertNotIn(forbidden, params)

    def test_passing_a_state_is_a_type_error(self):
        a = self._mk("Claim", "甲")
        b = self._mk("Claim", "乙", by="bob")
        with self.assertRaises(TypeError):
            candidates.record(self.conn, kind="related_to", left=a, right=b,
                              origin="ai:gpt", state="active")

    def test_the_origin_is_required(self):
        params = inspect.signature(candidates.record).parameters
        self.assertIn("origin", params)
        self.assertIs(params["origin"].default, inspect.Parameter.empty)

    def test_omitting_the_origin_is_a_type_error(self):
        a = self._mk("Claim", "甲")
        b = self._mk("Claim", "乙", by="bob")
        with self.assertRaises(TypeError):
            candidates.record(self.conn, kind="related_to", left=a, right=b)

    def test_every_recorded_edge_lands_on_the_candidate_state(self):
        a = self._mk("Claim", "甲")
        b = self._mk("Claim", "乙", by="bob")
        for kind in ("related_to", "contrasts", "exemplifies"):
            out = candidates.record(self.conn, kind=kind, left=a, right=b,
                                    origin="ai:gpt")
            self.assertEqual(self._state(out["relation"]),
                             candidates.CANDIDATE_STATE)

    def test_an_unknown_kind_is_refused_by_the_primitive_layer(self):
        a = self._mk("Claim", "甲")
        b = self._mk("Claim", "乙", by="bob")
        with self.assertRaises(scaffold.ScaffoldError):
            candidates.record(self.conn, kind="vibes_with", left=a, right=b,
                              origin="ai:gpt")


# ---------------------------------------------------------------------------
# ⑤ 候选边可被否掉
# ---------------------------------------------------------------------------

class TestACandidateIsDismissible(_Base):
    """一条**没人理**的候选边必须能被清掉，否则库会长满噪声 ——
    而「不得不清理自动产出的垃圾」正是让人开始乱删结构的起点。"""

    def setUp(self):
        super().setUp()
        self.a = self._mk("Claim", "甲")
        self.b = self._mk("Claim", "乙", by="bob")

    def test_a_proposed_relation_can_be_rejected(self):
        rid = candidates.record(self.conn, kind="related_to", left=self.a,
                                right=self.b, origin="ai:gpt")["relation"]
        scaffold.reject_relation(self.conn, rid, by="carol")
        self.assertEqual(self._state(rid), "rejected")

    def test_rejecting_is_not_deleting(self):
        """**不删** —— 删掉的话「AI 提了几条、被否了几条」就没法算了。"""
        rid = candidates.record(self.conn, kind="related_to", left=self.a,
                                right=self.b, origin="ai:gpt")["relation"]
        scaffold.reject_relation(self.conn, rid, by="carol")
        self.assertEqual(len(self._kinds("related_to")), 1)

    def test_the_event_remembers_it_was_never_counted(self):
        """`was` 一栏是分界线：否掉一条**候选**不等于**推翻**一条算数的边。"""
        rid = candidates.record(self.conn, kind="related_to", left=self.a,
                                right=self.b, origin="ai:gpt")["relation"]
        scaffold.reject_relation(self.conn, rid, by="carol")
        payload = scaffold.events_of_kind(
            self.conn, "relation_rejected")[0]["payload"]
        self.assertIn('"was": "proposed"', payload)

    def test_overturning_a_counted_edge_still_says_so(self):
        """反向判据：推翻一条**算数**的边，`was` 必须是 `active`。"""
        rid = candidates.record(self.conn, kind="related_to", left=self.a,
                                right=self.b, origin="ai:gpt")["relation"]
        candidates.promote(self.conn, relation_id=rid, by="carol")
        scaffold.reject_relation(self.conn, rid, by="dave")
        payload = scaffold.events_of_kind(
            self.conn, "relation_rejected")[0]["payload"]
        self.assertIn('"was": "active"', payload)

    def test_a_rejected_relation_cannot_be_promoted(self):
        rid = candidates.record(self.conn, kind="related_to", left=self.a,
                                right=self.b, origin="ai:gpt")["relation"]
        scaffold.reject_relation(self.conn, rid, by="carol")
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=rid, by="dave")


# ---------------------------------------------------------------------------
# ⑥ 规则通路要么说清是哪条规则，要么拒绝
# ---------------------------------------------------------------------------

class TestTheRuleRouteNamesItsRule(_Base):
    """`§C2.5` 第 3 档要的「可解释」：**哪一条不满足，指得出来。**"""

    def setUp(self):
        super().setUp()
        self.claim = self._mk("Claim", "被反复质询的那条")
        self.other = self._mk("Claim", "另一条", by="bob")

    def _contest(self, n: int) -> None:
        for i in range(n):
            scaffold.add_relation(
                self.conn, kind="challenged_by", from_id=self.claim,
                to_id=self._mk("Counterargument", f"质询{i}", by="bob"),
                origin="bob")

    def test_the_satisfying_end_is_recorded(self):
        self._contest(2)
        rid = candidates.record(self.conn, kind="related_to", left=self.claim,
                                right=self.other, origin="ai:gpt")["relation"]
        out = candidates.promote(self.conn, relation_id=rid,
                                 rule="repeatedly_contested",
                                 signals=upper.count_signals(self.conn))
        self.assertEqual(out["justified_by"], [self.claim])

    def test_either_end_may_satisfy_it(self):
        """规则是「这条边挂着的那一头成立了」，**两头都算** —— 与顺序无关。"""
        self._contest(2)
        rid = candidates.record(self.conn, kind="related_to", left=self.other,
                                right=self.claim, origin="ai:gpt")["relation"]
        out = candidates.promote(self.conn, relation_id=rid,
                                 rule="repeatedly_contested",
                                 signals=upper.count_signals(self.conn))
        self.assertEqual(out["justified_by"], [self.claim])

    def test_neither_end_satisfying_it_is_refused(self):
        rid = candidates.record(self.conn, kind="related_to", left=self.claim,
                                right=self.other, origin="ai:gpt")["relation"]
        with self.assertRaises(candidates.CandidateError) as ctx:
            candidates.promote(self.conn, relation_id=rid,
                               rule="repeatedly_contested",
                               signals=upper.count_signals(self.conn))
        self.assertIn("不兜住", str(ctx.exception))
        self.assertEqual(self._state(rid), "proposed")

    def test_one_contest_is_not_enough(self):
        """下限是 2（`rules.FLOOR`）—— 1 条不成立。"""
        self._contest(1)
        rid = candidates.record(self.conn, kind="related_to", left=self.claim,
                                right=self.other, origin="ai:gpt")["relation"]
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=rid,
                               rule="repeatedly_contested",
                               signals=upper.count_signals(self.conn))

    def test_the_reading_is_taken_after_the_edge_exists_not_before(self):
        """候选边**不给自己投票**：它还没算数，就不能被拿来当自己算数的理由。

        造法：`claim` 已有 **1** 条生效的质询，再加 **1** 条**候选**质询。
        若候选边参与了读数，`challenge_counts` 就是 2，规则成立 ——
        于是这条边靠「假设自己已经算数」把自己提拔了。下限是 2，所以必须拒。
        """
        self._contest(1)
        rid = candidates.record(self.conn, kind="challenged_by",
                                left=self.claim, right=self.other,
                                origin="ai:gpt")["relation"]
        self.assertEqual(
            upper.count_signals(self.conn)["challenge_counts"][self.claim], 1)
        with self.assertRaises(candidates.CandidateError):
            candidates.promote(self.conn, relation_id=rid,
                               rule="repeatedly_contested",
                               signals=upper.count_signals(self.conn))

    def test_once_it_is_promoted_the_reading_catches_up(self):
        """上一条的反向判据：它**真被**提拔之后，读数必须跟上 ——
        否则上一条可以靠「永远不数它」通过，而那样的边等于没建。
        """
        self._contest(1)
        rid = candidates.record(self.conn, kind="challenged_by",
                                left=self.claim, right=self.other,
                                origin="ai:gpt")["relation"]
        candidates.promote(self.conn, relation_id=rid, by="carol")
        self.assertEqual(
            upper.count_signals(self.conn)["challenge_counts"][self.claim], 2)


# ---------------------------------------------------------------------------
# ⑦ 候选层不碰上层结构
# ---------------------------------------------------------------------------

class TestItStaysBelowTheUpperLayer(_Base):
    """单向性在候选层的落点：**AI 提议得了底层边，提议不了上层结构。**"""

    def test_an_upper_edge_cannot_be_proposed(self):
        a = self._mk("Claim", "甲")
        b = self._mk("Claim", "乙", by="bob")
        for kind in scaffold.RELATION_LAYERS["upper"]:
            with self.assertRaises(candidates.CandidateError):
                candidates.record(self.conn, kind=kind, left=a, right=b,
                                  origin="ai:gpt")

    def test_a_self_loop_cannot_be_proposed(self):
        a = self._mk("Claim", "甲")
        with self.assertRaises(candidates.CandidateError):
            candidates.record(self.conn, kind="related_to", left=a, right=a,
                              origin="ai:gpt")

    def test_a_full_round_leaves_no_upper_structure_behind(self):
        """跑完一轮（提议 → 两条通路各提拔一次）之后，
        库里**没有**上层节点，也**没有**上层边。
        """
        claim = self._mk("Claim", "被反复质询的那条")
        for text in ("质询一", "质询二"):
            scaffold.add_relation(
                self.conn, kind="challenged_by", from_id=claim,
                to_id=self._mk("Counterargument", text, by="bob"), origin="bob")
        other = self._mk("Claim", "另一条", by="bob")

        r1 = candidates.record(self.conn, kind="related_to", left=claim,
                               right=other, origin="ai:gpt")["relation"]
        r2 = candidates.record(self.conn, kind="contrasts", left=claim,
                               right=other, origin="ai:gpt")["relation"]
        candidates.promote(self.conn, relation_id=r1, by="carol")
        candidates.promote(self.conn, relation_id=r2,
                           rule="repeatedly_contested",
                           signals=upper.count_signals(self.conn))

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM artifact WHERE type = ?",
                (upper.UPPER_TYPE,)).fetchone()["n"], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM relation WHERE kind = ?",
                (upper.UPPER_RELATION,)).fetchone()["n"], 0)

    def test_the_module_does_not_import_the_upper_layer(self):
        """静态那半在 B24；这里给一条**跑得起来**的复述，方便单独读这个文件。"""
        source = inspect.getsource(candidates)
        self.assertNotIn("import upper", source)


# ---------------------------------------------------------------------------
# ⑧ 谁提的必须说得出是**哪一类**（工程稿 §11.5 · 2026-09-29）
# ---------------------------------------------------------------------------

class TestWhoProposedItIsNamed(_Base):
    """「必填」只保证**填了**，「带前缀」才保证**填得对**。

    工程稿 §11.5 记的是：`relation` 表没有 `asserted_by` 栏，所以一条候选边
    「是人提的还是机器提的」只能记在 `origin` 里 —— 而 `origin` 是自由文本，
    写 `gpt` 和写 `alice` 在库里长得一样。这一组钉的就是那个补丁：
    **词表在入口上，且没有兜底类别。**
    """

    def setUp(self):
        super().setUp()
        self.a = self._mk("Claim", "甲")
        self.b = self._mk("Claim", "乙")

    def _record(self, origin: str) -> dict:
        return candidates.record(self.conn, kind="related_to",
                                 left=self.a, right=self.b, origin=origin)

    def test_every_declared_prefix_is_accepted(self):
        for prefix in candidates.ORIGIN_PREFIXES:
            with self.subTest(prefix=prefix):
                out = self._record(prefix + "whoever")
                self.assertEqual(out["state"], candidates.CANDIDATE_STATE)

    def test_a_bare_origin_is_refused(self):
        """⚠️ 反向判据：`origin="bob"` **不许**悄悄通过。"""
        with self.assertRaises(candidates.CandidateError):
            self._record("bob")

    def test_the_prefix_alone_is_not_enough(self):
        """只有类别名不构成归因 —— 要答得出是**哪一个**模型 / 哪一个人。"""
        for prefix in candidates.ORIGIN_PREFIXES:
            with self.subTest(prefix=prefix):
                with self.assertRaises(candidates.CandidateError):
                    self._record(prefix)

    def test_there_is_no_fallback_class(self):
        """没有「其它」这一类 —— 加一个，等于给「分不出来」留了个位置。"""
        for origin in ("", "other:x", "human", "AI:gpt", " ai:gpt", "ai-gpt"):
            with self.subTest(origin=origin):
                with self.assertRaises(candidates.CandidateError):
                    candidates.origin_class(origin)

    def test_the_class_matches_the_prefix(self):
        self.assertEqual(candidates.origin_class("ai:gpt"), "ai")
        self.assertEqual(candidates.origin_class("human:alice"), "human")
        self.assertEqual(candidates.origin_class("import:deepread"), "import")

    def test_the_reading_carries_the_class(self):
        self._record("ai:gpt")
        self._record("human:alice")
        got = [p["origin_class"] for p in candidates.proposed(self.conn)]
        self.assertEqual(got, ["ai", "human"])

    def test_an_old_row_is_not_mistaken_for_having_no_proposer(self):
        """词表是后加的，库里可能躺着 `origin="bob"` 这种老行。

        读数遇到它**不许抛** —— 一抛，整个 `proposed()` 就用不了了，
        而原因跟调用方毫无关系。也**不许**当成「没有提的人」：
        `None` 的意思是「**分不出来**」，那是 `observe.py`「算不出 ≠ 零」
        的同一条要求。
        """
        scaffold.add_relation(
            self.conn, kind="related_to", from_id=self.a, to_id=self.b,
            origin="bob", state=candidates.CANDIDATE_STATE)
        out = candidates.proposed(self.conn)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["origin_class"])
        self.assertEqual(out[0]["origin"], "bob")
        # 而判据本身仍然拒它 —— 「读得出来」和「写得进去」是两件事。
        with self.assertRaises(candidates.CandidateError):
            candidates.origin_class("bob")

    def test_the_boundary_the_vocabulary_does_not_cover(self):
        """⚠️ 说清它**拦不住**什么（同 B24 末尾那条立场）。

        绕开 `record()`、直接走原语层，仍然写得进一条自由文本 `origin`。
        这是**入口级**保证，不是 schema 级 —— 那条路本来就是对所有人开着的，
        本模块不假装它是关的。
        """
        rid = scaffold.add_relation(
            self.conn, kind="related_to", from_id=self.a, to_id=self.b,
            origin="没带前缀的一条", state=candidates.CANDIDATE_STATE)
        self.assertEqual(self._state(rid), candidates.CANDIDATE_STATE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
