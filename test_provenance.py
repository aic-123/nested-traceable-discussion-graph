"""来源与归因的**行为**验证（2026-09-28）。

`checks.py` 的 B16 / B17 是**静态**的 —— 它们只证明「守卫与层级定义在」。
这个文件证明守卫**真的会拦**、层级**真的查得出来**。

分工同 B14 与 `test_upper.py::test_promote_never_touches_the_lower_layer`：
**静态守代码形状，行为守运行结果。一层不够** ——
静态拦得住直笔，拦不住绕路。

这个文件是**自包含**的：只依赖 `scaffold.py` / `upper.py` / `policy.py` 与标准库。
"""

from __future__ import annotations

import unittest

import policy
import scaffold
import upper


def _fresh():
    conn = scaffold.connect(":memory:")
    scaffold.init(conn)
    return conn


class TestDigitalSourceGuard(unittest.TestCase):
    """AI 档位必须写明「谁主张的」—— 「AI 产出不许伪装成人」的可执行形式。

    为什么它值得单开一个文件：留空的后果不是报错，是**静默的伪装**。
    一条 AI 转录的书里的论点，若主张者留空，它在库里和「某个用户提的」
    长得一模一样 —— 而来源可区分（P3）就是从这一栏开始的。
    """

    def setUp(self):
        self.conn = _fresh()

    def _add(self, **kw):
        base = dict(type_="Claim", content={"text": "x"}, origin="alice")
        base.update(kw)
        return scaffold.add_artifact(self.conn, **base)

    def test_ai_transcribed_book_needs_an_asserted_by(self):
        """AI 转录的书：主张者是书作者，**必须写出来**。"""
        aid = self._add(
            digital_source_type="compositeWithTrainedAlgorithmicMedia",
            asserted_by="某书作者",
            origin="ai:distiller-v1",
        )
        row = scaffold.get(self.conn, aid)
        self.assertEqual(row["digital_source_type"],
                         "compositeWithTrainedAlgorithmicMedia")
        self.assertEqual(row["asserted_by"], "某书作者")
        # 三栏各答一个问题：转录者是 origin，主张者是 asserted_by。
        self.assertEqual(row["origin"], "ai:distiller-v1")

    def test_ai_output_without_an_asserted_by_is_rejected(self):
        """留空 = 系统替它立论。两档都必须拦。"""
        for kind in scaffold.AI_SOURCES:
            with self.assertRaises(scaffold.ScaffoldError):
                self._add(digital_source_type=kind, origin="ai:distiller-v1")

    def test_blank_asserted_by_is_rejected_too(self):
        """空白串不是「填了」—— 它和留空是同一件事。"""
        with self.assertRaises(scaffold.ScaffoldError):
            self._add(digital_source_type=scaffold.AI_SOURCES[0],
                      asserted_by="   ")

    def test_human_created_defaults_asserted_by_to_origin(self):
        """人档：谁提的主张就是谁记的，缺省取 origin。"""
        aid = self._add()
        row = scaffold.get(self.conn, aid)
        self.assertEqual(row["digital_source_type"],
                         scaffold.DIGITAL_SOURCE_DEFAULT)
        self.assertEqual(row["asserted_by"], "alice")

    def test_unknown_source_kind_is_rejected(self):
        """档位取值必须来自词表 —— 自己造词会被拦。"""
        with self.assertRaises(scaffold.ScaffoldError):
            self._add(digital_source_type="made_up_kind")


class TestSourceTypesComeFromTheVocabulary(unittest.TestCase):
    """档位取值来自 IPTC 词表 —— 这条钉住「不自己造词」。"""

    def test_the_four_kinds_are_the_iptc_local_names(self):
        self.assertEqual(
            tuple(scaffold.DIGITAL_SOURCE_TYPES),
            ("digitalCreation",
             "digitalCapture",
             "compositeWithTrainedAlgorithmicMedia",
             "trainedAlgorithmicMedia"),
        )

    def test_ai_sources_is_a_subset(self):
        for k in scaffold.AI_SOURCES:
            self.assertIn(k, scaffold.DIGITAL_SOURCE_TYPES)

    def test_the_default_is_not_an_ai_kind(self):
        self.assertNotIn(scaffold.DIGITAL_SOURCE_DEFAULT, scaffold.AI_SOURCES)


class TestUpperLayerDeclaresItselfAsAi(unittest.TestCase):
    """★ 上层建的 Context 必须**显式**记成 AI 生成。

    这是本文件最该有的一条：底层原语的默认档位是「人创建」，
    上层若不显式改过来，**AI 的产出就会被记成人的产出** ——
    而那正好是本仓库最防的那件事。
    """

    def setUp(self):
        self.conn = _fresh()
        self.debate = scaffold.add_artifact(
            self.conn, type_="Debate", content={"question": "该不该 A？"},
            origin="alice")
        scaffold.activate(self.conn, self.debate, by="alice")

    def _challenged_claim(self, text="假设甲") -> str:
        claim = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": text}, origin="alice")
        scaffold.activate(self.conn, claim, by="alice")
        topic = scaffold.add_artifact(
            self.conn, type_="Topic", content={"text": "议题"}, origin="alice")
        scaffold.activate(self.conn, topic, by="alice")
        scaffold.add_relation(self.conn, kind="contains",
                              from_id=self.debate, to_id=topic, origin="alice")
        scaffold.add_relation(self.conn, kind="contains",
                              from_id=topic, to_id=claim, origin="alice")
        for i in range(3):
            c = scaffold.add_artifact(
                self.conn, type_="Counterargument",
                content={"text": f"反驳{i}"}, origin="bob")
            scaffold.activate(self.conn, c, by="bob")
            scaffold.add_relation(self.conn, kind="challenged_by",
                                  from_id=claim, to_id=c, origin="bob")
        return claim

    def test_promoted_context_is_marked_as_ai_generated(self):
        self._challenged_claim()
        cands = upper.propose_clusters(self.conn)
        self.assertTrue(cands["candidates"], "没攒出候选 —— 用例的前提没成立")
        made = upper.promote_candidates(
            self.conn, candidates=cands["candidates"], by="alice",
            debate_id=self.debate)
        self.assertEqual(len(made), 1)

        row = scaffold.get(self.conn, made[0])
        self.assertEqual(
            row["digital_source_type"], "trainedAlgorithmicMedia",
            "上层建的节点被记成了非 AI 档 —— AI 的产出伪装成了人的产出。")
        self.assertEqual(row["asserted_by"], "alice")
        self.assertEqual(row["origin"], "alice")


class TestRelationHierarchy(unittest.TestCase):
    """引用与原始来源是**两条不同的边**，且层级关系查得出来。"""

    def setUp(self):
        self.conn = _fresh()

    def _claim(self, text, origin="alice"):
        aid = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": text}, origin=origin)
        scaffold.activate(self.conn, aid, by=origin)
        return aid

    def test_quoted_from_is_not_a_primary_source(self):
        """★ 引用一条书里的论点 —— 它**不是**原始来源。

        这是「引用不许自动升级成知识来源」的可执行形式。
        """
        book = self._claim("书里的论点", origin="ai:distiller-v1")
        mine = self._claim("我的论点")
        scaffold.add_relation(self.conn, kind="quoted_from",
                              from_id=mine, to_id=book, origin="alice")

        kinds = [r[0] for r in self.conn.execute(
            "SELECT kind FROM relation WHERE from_id=?", (mine,))]
        self.assertIn("quoted_from", kinds)
        self.assertNotIn(
            scaffold.PRIMARY_SOURCE_KIND, kinds,
            "引用了它，却被当成了原始来源 —— 引用不许自动升级。")

    def test_had_primary_source_is_a_separate_edge(self):
        """要当原始来源用，得**显式**再挂一条。"""
        book = self._claim("书里的论点", origin="ai:distiller-v1")
        mine = self._claim("我的论点")
        scaffold.add_relation(self.conn, kind="quoted_from",
                              from_id=mine, to_id=book, origin="alice")
        scaffold.add_relation(self.conn, kind=scaffold.PRIMARY_SOURCE_KIND,
                              from_id=mine, to_id=book, origin="alice")

        rows = self.conn.execute(
            "SELECT kind FROM relation WHERE from_id=? ORDER BY kind",
            (mine,)).fetchall()
        self.assertEqual([r[0] for r in rows],
                         ["had_primary_source", "quoted_from"])

    def test_the_primary_source_kind_is_not_a_quotation(self):
        self.assertNotEqual(scaffold.PRIMARY_SOURCE_KIND, "quoted_from")

    def test_the_hierarchy_is_expressible(self):
        """层级查得出来：`quoted_from` 的父类是 `derived_from`。"""
        self.assertEqual(scaffold.RELATION_PARENTS["quoted_from"], "derived_from")


class TestPolicyIsMutable(unittest.TestCase):
    """策略门槛可变动 —— 默认在代码里，运行时可覆盖。"""

    def test_default_values_are_present(self):
        self.assertEqual(policy.value("staging_backlog_limit"), 50)
        self.assertEqual(policy.value("import_to_contribution_ceiling"), 10)

    def test_a_caller_can_override_without_touching_the_code(self):
        self.assertEqual(
            policy.value("staging_backlog_limit", staging_backlog_limit=5), 5)
        # 覆盖是**按次**的，不改默认值。
        self.assertEqual(policy.value("staging_backlog_limit"), 50)

    def test_an_unknown_key_is_refused(self):
        """策略键是封闭集合 —— 顺手加一个键是不允许的。"""
        with self.assertRaises(KeyError):
            policy.value("made_up_key")


if __name__ == "__main__":
    # ⚠️ 这个块不能省。少了它，`python test_provenance.py` **什么也不做、
    # 退出码 0** —— 看起来像「过了」，实际一个用例都没跑。
    # 本仓库的规矩是「检查跑不通 = 声明不成立」，而「跑了但没跑」比跑不通更难发现。
    unittest.main(verbosity=2)
