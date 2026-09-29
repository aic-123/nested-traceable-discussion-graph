"""社区贡献入口的**行为**验证 —— 七种粒度逐个可建，且入口不需要图知识。

静态那半在 `checks.py` 的 **B23**（映射表完备、字段白名单、签名里没有词表参数、
import 白名单）。这里拦的是静态拦不住的那半：**真的建出来了吗、建成了什么形状。**

--- 这个文件要证的五件事 -----------------------------------------------------

| 要证什么 | 靠哪几条 |
|---|---|
| 七种**逐个**可建，每种有独立用例 | `TestAllSevenCanBeCreated`（7 条，一粒度一条） |
| **空库**里也能创建第一条 Claim | `TestTheFirstContributionNeedsNothing` |
| 首次贡献者**不需要理解图结构** | `TestTheEntryNeedsNoGraphVocabulary` |
| 落点照 `§C2.5` 分档 | `TestTheLandingTierFollowsC25` |
| 贡献层**不碰上层** | `TestTheEntryStaysBelowTheUpperLayer` |

⚠️ 本文件刻意只依赖 `scaffold.py` / `contribute.py` 与标准库 ——
和 `test_rules.py` 一个规矩：**能用几十行读懂的依赖，才算真的独立。**

    python test_contribute.py
"""

from __future__ import annotations

import inspect
import unittest

import contribute
import scaffold


class _Base(unittest.TestCase):

    def setUp(self):
        self.conn = scaffold.connect()
        scaffold.init(self.conn)

    def tearDown(self):
        self.conn.close()

    def _a_claim(self, text: str = "稀缺性来自供给的不可复制", by: str = "alice") -> str:
        return contribute.claim(self.conn, text=text, by=by)["artifact"]

    def _relations(self, kind: str) -> list:
        return self.conn.execute(
            "SELECT * FROM relation WHERE kind = ? ORDER BY id", (kind,)
        ).fetchall()


class TestAllSevenCanBeCreated(_Base):
    """阶段 4 出口判据 ①：**七种逐个可创建，每种有独立用例。**

    一条粒度一条用例 —— 不合并。合并之后「有一种做不出来」会长得像
    「某条断言失败了」，看不出是哪一种。
    """

    def test_claim(self):
        out = contribute.claim(self.conn, text="A 导致 B", by="alice")
        row = scaffold.get(self.conn, out["artifact"])
        self.assertEqual(row["type"], "Claim")
        self.assertEqual(row["origin"], "alice")
        self.assertIsNone(out["relation"])

    def test_evidence(self):
        cid = self._a_claim()
        out = contribute.evidence(self.conn, text="2019 年那项追踪研究", target=cid,
                                  by="bob")
        self.assertEqual(scaffold.get(self.conn, out["artifact"])["type"], "Evidence")
        rel = self._relations("supports")
        self.assertEqual(len(rel), 1)
        # `§C6.3` 的方向：证据 → Claim
        self.assertEqual((rel[0]["from_id"], rel[0]["to_id"]), (out["artifact"], cid))

    def test_challenge(self):
        cid = self._a_claim()
        out = contribute.challenge(self.conn, text="那个样本只覆盖一线城市",
                                   target=cid, by="bob")
        self.assertEqual(scaffold.get(self.conn, out["artifact"])["type"],
                         "Counterargument")
        rel = self._relations("challenged_by")
        # `§C4` 的方向：被质询者 → 质询者
        self.assertEqual((rel[0]["from_id"], rel[0]["to_id"]), (cid, out["artifact"]))

    def test_counterexample(self):
        cid = self._a_claim()
        out = contribute.counterexample(self.conn, text="但 1998 年那批是反例",
                                        target=cid, by="bob")
        self.assertEqual(scaffold.get(self.conn, out["artifact"])["type"],
                         "Counterexample")
        rel = self._relations("contradicts")
        self.assertEqual((rel[0]["from_id"], rel[0]["to_id"]), (out["artifact"], cid))

    def test_revision(self):
        cid = self._a_claim()
        out = contribute.revision(self.conn, of=cid, text="稀缺性来自供给的不可复制（改）",
                                  by="alice")
        self.assertEqual(out["artifact"], cid)
        self.assertEqual(len(scaffold.history_of(self.conn, cid)["revisions"]), 2)

    def test_connection(self):
        left = self._a_claim("供给决定价格")
        right = self._a_claim("需求决定价格")
        out = contribute.connection(self.conn, left=left, right=right, by="carol")
        self.assertIsNone(out["artifact"])          # 不新建节点
        rel = self._relations("related_to")
        self.assertEqual((rel[0]["from_id"], rel[0]["to_id"]), (left, right))

    def test_context(self):
        out = contribute.context(self.conn, text="那场辩论其实是在争什么", by="carol")
        self.assertEqual(scaffold.get(self.conn, out["artifact"])["type"], "Topic")


class TestTheFirstContributionNeedsNothing(_Base):
    """阶段 4 出口判据 ②：**没有任何现成论点时也能创建 Claim。**

    这条判据防的是「入口其实要求先有一个 Topic / Position」——
    那种入口在开发机上永远走得通（库里总有东西），**在空库上第一步就断**。
    所以这条用例**只 `init()`，什么都不预建**。
    """

    def test_a_claim_can_be_the_very_first_thing(self):
        before = self.conn.execute("SELECT COUNT(*) AS n FROM artifact").fetchone()["n"]
        self.assertEqual(before, 0, "空库前提被破坏了，这条用例就白跑了")

        out = contribute.claim(self.conn, text="第一句话", by="alice")
        self.assertTrue(out["needs_confirmation"])
        self.assertEqual(len(contribute.pending(self.conn)), 1)

    def test_a_context_can_also_be_first(self):
        """新议题同样不该要求先有东西 —— 它本身就是「从零开一个议题」。"""
        out = contribute.context(self.conn, text="要不要先讨论 A", by="alice")
        self.assertEqual(scaffold.get(self.conn, out["artifact"])["type"], "Topic")


class TestTheEntryNeedsNoGraphVocabulary(_Base):
    """阶段 4 出口判据 ③：**首次贡献者不需要理解图结构。**

    可执行形式：七个入口的**签名里没有词表参数** —— 调用方没有机会
    自己挑一个节点类型或关系种类。B23 是同一件事的静态那半。
    """

    ENTRY = ("claim", "evidence", "challenge", "counterexample",
             "revision", "connection", "context")

    def test_the_seven_entries_exist_and_are_callable(self):
        for name in self.ENTRY:
            fn = getattr(contribute, name, None)
            self.assertIsNotNone(fn, f"{name}() 不见了 —— 七种就少一种")
            self.assertTrue(callable(fn))
        self.assertEqual(set(self.ENTRY), set(contribute.GRANULARITIES))

    def test_no_entry_takes_a_vocabulary_parameter(self):
        for name in self.ENTRY:
            params = set(inspect.signature(getattr(contribute, name)).parameters)
            clash = sorted(params & set(contribute.FORBIDDEN_PARAMS))
            self.assertEqual(
                clash, [],
                f"{name}() 收了词表参数 {clash} —— 调用方一旦能传它，"
                "这一层就退化成原语的薄包装，而它看起来还是七个漂亮的名字。"
            )

    def test_every_entry_names_who_submitted_it(self):
        """`by` 必填且**没有默认值** —— 有默认值就等于给「机器自己提交」留了条路。"""
        for name in self.ENTRY:
            sig = inspect.signature(getattr(contribute, name))
            self.assertIn(contribute.CONTRIBUTION_ACTOR, sig.parameters, name)
            self.assertIs(sig.parameters[contribute.CONTRIBUTION_ACTOR].default,
                          inspect.Parameter.empty, name)

    def test_describe_says_what_each_one_becomes(self):
        """自述的依据必须是 `CONTRIBUTIONS` 本身，不是另抄一份。"""
        got = contribute.describe()
        self.assertEqual([d["granularity"] for d in got], list(contribute.GRANULARITIES))
        for d in got:
            spec = contribute.CONTRIBUTIONS[d["granularity"]]
            self.assertEqual(d["type_"], spec["type_"])
            self.assertEqual(d["relation"], spec["relation"])


class TestTheLandingTierFollowsC25(_Base):
    """`§C2.5` 的分档：新断言必须确认，标注与关系默认生效。

    改坏它的方式很具体：把 `claim` / `context` 的落点从 `proposed`
    改成 `active`。那之后「未确认的东西不许算数」这条**在贡献入口上就没有了**，
    而库里看不出任何异常 —— 节点照样有 id、照样能引用。
    """

    def test_new_assertions_land_proposed(self):
        for make in (
            lambda: contribute.claim(self.conn, text="x", by="a"),
            lambda: contribute.context(self.conn, text="y", by="a"),
        ):
            out = make()
            self.assertEqual(out["state"], "proposed")
            self.assertTrue(out["needs_confirmation"])
            self.assertEqual(scaffold.get(self.conn, out["artifact"])["state"],
                             "proposed")

    def test_annotations_and_relations_land_active(self):
        cid = self._a_claim()
        out = contribute.evidence(self.conn, text="e", target=cid, by="a")
        self.assertEqual(out["state"], "active")
        self.assertFalse(out["needs_confirmation"])
        self.assertEqual(scaffold.get(self.conn, out["artifact"])["state"], "active")

    def test_only_activate_confirms_them(self):
        """`scaffold.activate()` 是唯一的确认入口 —— 确认之后 `pending()` 少一条。"""
        out = contribute.claim(self.conn, text="x", by="alice")
        self.assertEqual(len(contribute.pending(self.conn)), 1)
        scaffold.activate(self.conn, out["artifact"], by="alice")
        self.assertEqual(contribute.pending(self.conn), [])

    def test_nothing_is_born_active_by_a_side_door(self):
        """落 `proposed` 的那两种，建出来那一刻**不是** active —— 必须有人确认。

        方向别写反：`proposed` 本来就该能被确认**一次**。
        要证明的是「确认是它变成 active 的**唯一**原因」：
        确认之前不是，确认之后是，确认两次被拒（说明没有第二条路）。
        """
        for make in (
            lambda: contribute.claim(self.conn, text="x", by="a"),
            lambda: contribute.context(self.conn, text="y", by="a"),
        ):
            aid = make()["artifact"]
            self.assertEqual(scaffold.get(self.conn, aid)["state"], "proposed")
            scaffold.activate(self.conn, aid, by="a")
            self.assertEqual(scaffold.get(self.conn, aid)["state"], "active")
            with self.assertRaises(scaffold.ScaffoldError):
                scaffold.activate(self.conn, aid, by="a")


class TestTheTargetDomainIsClosed(_Base):
    """目标类型照文档，**不照「什么都行」**（缺口④ 的原话）。"""

    def test_evidence_target_must_be_a_claim(self):
        evid = contribute.evidence(self.conn, text="e", target=self._a_claim(),
                                   by="a")["artifact"]
        with self.assertRaises(contribute.ContributionError):
            contribute.evidence(self.conn, text="e2", target=evid, by="a")

    def test_challenge_may_target_a_claim_or_evidence_or_assumption(self):
        cid = self._a_claim()
        evid = contribute.evidence(self.conn, text="e", target=cid, by="a")["artifact"]
        for target in (cid, evid):
            out = contribute.challenge(self.conn, text="c", target=target, by="a")
            self.assertEqual(scaffold.get(self.conn, out["artifact"])["type"],
                             "Counterargument")

    def test_challenge_may_not_target_a_topic(self):
        topic = contribute.context(self.conn, text="t", by="a")["artifact"]
        with self.assertRaises(contribute.ContributionError):
            contribute.challenge(self.conn, text="c", target=topic, by="a")

    def test_counterexample_target_must_be_a_claim(self):
        evid = contribute.evidence(self.conn, text="e", target=self._a_claim(),
                                   by="a")["artifact"]
        with self.assertRaises(contribute.ContributionError):
            contribute.counterexample(self.conn, text="c", target=evid, by="a")

    def test_a_target_that_does_not_exist_is_rejected(self):
        with self.assertRaises(scaffold.ScaffoldError):
            contribute.challenge(self.conn, text="c", target="claim-9999", by="a")

    def test_a_connection_cannot_be_a_self_reference(self):
        cid = self._a_claim()
        with self.assertRaises(contribute.ContributionError):
            contribute.connection(self.conn, left=cid, right=cid, by="a")

    def test_the_stance_must_be_one_of_the_documented_three(self):
        cid = self._a_claim()
        for stance in ("supports", "contradicts", "qualifies"):
            contribute.evidence(self.conn, text="e", target=cid, stance=stance, by="a")
        with self.assertRaises(contribute.ContributionError):
            contribute.evidence(self.conn, text="e", target=cid,
                                stance="proves_it", by="a")


class TestARelationIsAnObjectNotAField(unittest.TestCase):
    """`§C3.2`：Relation 是**可追踪对象**，不是数据库里的一个外键字段。

    `connection` 是七种里唯一「不新建节点」的 —— 所以它是这条最容易被做坏的地方：
    图省事的话，连接会退化成 `artifact` 表上的一个列。
    """

    def setUp(self):
        self.conn = scaffold.connect()
        scaffold.init(self.conn)
        self.left = contribute.claim(self.conn, text="l", by="a")["artifact"]
        self.right = contribute.claim(self.conn, text="r", by="a")["artifact"]
        self.rid = contribute.connection(self.conn, left=self.left, right=self.right,
                                         by="carol")["relation"]

    def tearDown(self):
        self.conn.close()

    def test_the_edge_has_its_own_identity_and_origin(self):
        row = self.conn.execute("SELECT * FROM relation WHERE id = ?",
                                (self.rid,)).fetchone()
        self.assertEqual(row["origin"], "carol")
        self.assertEqual(row["state"], "active")
        self.assertIsNotNone(row["id"])

    def test_the_edge_can_be_overturned_without_being_deleted(self):
        """`§C2.4`「可拒绝」—— 推翻是改状态，**不是删行**（删了改判率就没法算）。"""
        scaffold.reject_relation(self.conn, self.rid, by="carol")
        row = self.conn.execute("SELECT * FROM relation WHERE id = ?",
                                (self.rid,)).fetchone()
        self.assertIsNotNone(row, "被推翻的边不许消失 —— 改判率要从这里算")
        self.assertEqual(row["state"], "rejected")

    def test_the_connection_does_not_touch_either_endpoint(self):
        """连一条边**不改**两端任何东西 —— 连它们自己的版本数都不该动。"""
        for aid in (self.left, self.right):
            self.assertEqual(len(scaffold.history_of(self.conn, aid)["revisions"]), 1)


class TestRevisionDoesNotOverwrite(_Base):
    """`§C10`：**禁止覆盖式更新。** 旧版本一行都不会被改。"""

    def test_the_old_version_stays_byte_for_byte(self):
        cid = self._a_claim("原来的说法")
        before = scaffold.original_content_of(self.conn, cid)
        contribute.revision(self.conn, of=cid, text="改过的说法", by="alice")
        after = scaffold.original_content_of(self.conn, cid)
        self.assertEqual(before, after, "原文本被覆盖了 —— 那正是 §C10 禁止的")
        self.assertEqual(scaffold.content_of(self.conn, cid)["text"], "改过的说法")

    def test_a_fork_is_refused_rather_than_resolved(self):
        """多头时**拒绝**自动挑一个 —— 挑一个就是替用户做了一次判断（`§C10`）。"""
        cid = self._a_claim("原话")
        heads = scaffold.heads_of(self.conn, cid)
        contribute.revision(self.conn, of=cid, text="分支甲", by="alice",
                            parent_rev=heads[0]["id"])
        contribute.revision(self.conn, of=cid, text="分支乙", by="bob",
                            parent_rev=heads[0]["id"])
        self.assertEqual(len(scaffold.heads_of(self.conn, cid)), 2)
        with self.assertRaises(scaffold.ScaffoldError):
            contribute.revision(self.conn, of=cid, text="丙", by="carol")


class TestTheEntryStaysBelowTheUpperLayer(_Base):
    """单向性的行为那半：贡献进来的是**事实**，归组是上层的事。

    跑完全部七种之后，库里**不该**出现上层节点、也不该出现上层那条边。
    静态那半（不许 import `upper`）在 B23 里。
    """

    def test_running_all_seven_creates_no_upper_structure(self):
        cid = self._a_claim()
        other = self._a_claim("另一条")
        contribute.evidence(self.conn, text="e", target=cid, by="a")
        contribute.challenge(self.conn, text="c", target=cid, by="a")
        contribute.counterexample(self.conn, text="x", target=cid, by="a")
        contribute.revision(self.conn, of=cid, text="改", by="a")
        contribute.connection(self.conn, left=cid, right=other, by="a")
        contribute.context(self.conn, text="新议题", by="a")

        types = {r["type"] for r in
                 self.conn.execute("SELECT type FROM artifact").fetchall()}
        self.assertNotIn("Context", types,
                         "贡献层建出了上层节点 —— 那是 upper.py 的活")
        kinds = {r["kind"] for r in
                 self.conn.execute("SELECT kind FROM relation").fetchall()}
        self.assertNotIn("clustered_into", kinds)

    def test_no_contribution_needs_a_model(self):
        """七种入口**一个都不调模型** —— 它们只把人的话落成结构。"""
        src = inspect.getsource(contribute)
        self.assertNotIn("import upper", src)
        for word in ("openai", "anthropic", "transformers", "embedding"):
            self.assertNotIn(word, src.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
