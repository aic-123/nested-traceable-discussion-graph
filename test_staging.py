"""入层门的**行为**验证（2026-09-28）。

`checks.py` 的 B20 / B21 是**静态**的 —— 它们只证明「常量自洽、门写在代码里」。
这个文件证明门**真的会拦**、映射表**真的会拒绝**、导入层**真的能整层重建**。

分工同 B14 与 `test_upper.py::test_promote_never_touches_the_lower_layer`：
**静态守代码形状，行为守运行结果。**

⚠️ 这里最要紧的两组是：

    TestTheGateBlocksActivation   门挡在**写 active 的唯一入口**上，绕不过去
    TestLayerIsRebuildable        「可整层重建」这句话的前提是**可验证的**，
                                  不是一句设计意图
"""

from __future__ import annotations

import unittest

import scaffold
import staging

BOOK_A = "urn:isbn:9780000000001"
BOOK_B = "urn:isbn:9780000000002"
SEL = {"type": "TextQuoteSelector", "exact": "一句原文"}


def _fresh():
    conn = scaffold.connect(":memory:")
    scaffold.init(conn)
    return conn


def _item(text, **kw):
    it = {"content": {"text": text}, "selector": dict(SEL),
          "confidence": kw.pop("confidence", "reasonable inference")}
    it.update(kw)
    return it


def _batch(conn, batch="b1", uri=BOOK_A, texts=("甲", "乙")):
    return staging.stage_batch(
        conn, batch=batch, source_uri=uri,
        items=[_item(t) for t in texts],
        extracted_by="distiller-v1", asserted_by="某书作者",
    )


class TestTheVocabularyIsCopiedNotInvented(unittest.TestCase):
    """四档置信度与八种关系都是**照抄** —— 逐字不改，改了就跟上游对不上。"""

    def test_the_four_levels_are_the_deepread_words(self):
        self.assertEqual(staging.CONFIDENCE_LEVELS, (
            "author intent", "original facts & data",
            "reasonable inference", "unverifiable",
        ))

    def test_the_eight_relations_are_the_deepread_words(self):
        self.assertEqual(len(staging.DEEPRED_RELATIONS), 8)
        # `depends on` 里那个空格是原词的一部分，不是排版。
        self.assertIn("depends on", staging.DEEPRED_RELATIONS)


class TestMappingIsExplicit(unittest.TestCase):
    """八种 → 本仓库 kind：一对一、一对多、没有对应，三种都要**显式**。"""

    def test_one_to_one_keeps_the_name(self):
        self.assertEqual(staging.map_relation("supports"), "supports")
        self.assertEqual(staging.map_relation("explains"), "explains")

    def test_the_two_lossy_renames_are_named(self):
        """`refutes` / `depends on` 是**有损**映射 —— 损失要说得出来。"""
        self.assertEqual(staging.map_relation("refutes"), "contradicts")
        self.assertEqual(staging.map_relation("depends on"), "assumes")
        self.assertEqual(staging.map_relation("limits"), "qualifies")

    def test_causes_expands_to_two_kinds(self):
        """`causes` 是**一对多** —— 它要展开成一个因果主张 + 两条边。"""
        self.assertEqual(staging.map_relation("causes"), staging.CAUSAL_EXPANSION)

    def test_the_two_without_a_counterpart_are_refused(self):
        """★ `exemplifies` / `contrasts` 在本仓库里没有位置 —— 停下来问人。

        这一条是 B21 的行为那半。硬塞进 `related_to` 也能「跑通」，
        但那条边在库里长得完全正常，只是信息没了。
        """
        for name in ("exemplifies", "contrasts"):
            with self.assertRaises(staging.StagingError, msg=f"{name} 竟然被兜住了"):
                staging.map_relation(name)

    def test_an_unknown_name_is_refused(self):
        """上游改了词表，要停下来看 —— 不能兜住。"""
        with self.assertRaises(staging.StagingError):
            staging.map_relation("关联于")

    def test_the_declared_unmapped_set_matches_the_table(self):
        """声明与表必须一致 —— 改一处不改另一处，B21 会报。"""
        declared = {k for k, v in staging.DEEPRED_RELATION_MAP.items() if v is None}
        self.assertEqual(declared, set(staging.UNMAPPED_DEEPRED))


class TestStagingLandsAsProposed(unittest.TestCase):
    """蒸馏产物落 staging：**全部 proposed，一条都不 active**。"""

    def test_every_staged_node_is_proposed_and_marked_staged(self):
        conn = _fresh()
        ids = _batch(conn)
        self.assertEqual(len(ids), 2)
        for aid in ids:
            row = scaffold.get(conn, aid)
            self.assertEqual(row["state"], "proposed")
            self.assertEqual(row["intake"], "staged")
            self.assertEqual(staging.gate_state_of(conn, aid), scaffold.GATE_PENDING)

    def test_the_source_type_and_attribution_are_recorded(self):
        """三栏各答一个问题：怎么产生的 / 谁主张的 / 谁转录的。"""
        conn = _fresh()
        aid = _batch(conn)[0]
        row = scaffold.get(conn, aid)
        self.assertEqual(row["digital_source_type"], "compositeWithTrainedAlgorithmicMedia")
        self.assertEqual(row["asserted_by"], "某书作者")
        self.assertEqual(row["origin"], "distiller-v1")

    def test_a_staged_node_can_answer_which_page(self):
        """阶段 3 的出口判据 ③：每条导入节点能回答「书里哪一段」。"""
        conn = _fresh()
        aid = _batch(conn)[0]
        self.assertEqual(scaffold.pointer_of(conn, aid)["uri"], BOOK_A)

    def test_a_distilled_node_without_a_selector_is_refused(self):
        """答不上「书里哪一段」的，与 AI 凭空生成无法区分 —— 不收。"""
        conn = _fresh()
        with self.assertRaises(staging.StagingError):
            staging.stage_batch(
                conn, batch="b1", source_uri=BOOK_A,
                items=[{"content": {"text": "甲"}, "confidence": "author intent"}],
                extracted_by="distiller-v1", asserted_by="某书作者",
            )

    def test_a_confidence_level_outside_the_four_is_refused(self):
        conn = _fresh()
        with self.assertRaises(staging.StagingError):
            staging.stage_batch(
                conn, batch="b1", source_uri=BOOK_A,
                items=[_item("甲", confidence="挺可信的")],
                extracted_by="distiller-v1", asserted_by="某书作者",
            )


class TestCausesIsExpanded(unittest.TestCase):
    """`causes` 一条边 → 一个因果主张节点 + 两条边（`§C3.2`：Relation 可追踪）。"""

    def test_one_edge_becomes_a_node_and_two_edges(self):
        conn = _fresh()
        items = [_item("供给不可复制"), _item("价格高")]
        items[0]["relations"] = [{
            "deepread": "causes", "to": 1,
            "via": {"content": {"text": "供给不可复制导致价格高"},
                    "selector": dict(SEL)},
        }]
        ids = staging.stage_batch(
            conn, batch="b1", source_uri=BOOK_A, items=items,
            extracted_by="distiller-v1", asserted_by="某书作者",
        )
        self.assertEqual(len(ids), 2)
        rows = conn.execute(
            "SELECT kind, from_id, to_id FROM relation ORDER BY id").fetchall()
        self.assertEqual(sorted(r["kind"] for r in rows),
                         ["causal_conclusion", "causal_premise"])
        mid = [r["to_id"] for r in rows if r["kind"] == "causal_premise"][0]
        self.assertNotIn(mid, ids, "因果主张节点应当是**新**建的那个")
        self.assertEqual(scaffold.get(conn, mid)["intake"], "staged")

    def test_causes_without_the_middle_node_is_refused(self):
        """展开要一个中间节点 —— 不给就停下来，不许自己编一个。"""
        conn = _fresh()
        with self.assertRaises(staging.StagingError):
            staging.stage_batch(
                conn, batch="b1", source_uri=BOOK_A,
                items=[
                    _item("甲"),
                    _item("乙", relations=[{"deepread": "causes", "to": 0}]),
                ],
                extracted_by="distiller-v1", asserted_by="某书作者",
            )


class TestTheGateBlocksActivation(unittest.TestCase):
    """★ 门挡在**写 active 的唯一入口**上 —— 绕过 staging 也过不去。"""

    def test_a_staged_node_cannot_be_activated_before_the_gate(self):
        conn = _fresh()
        aid = _batch(conn)[0]
        with self.assertRaises(scaffold.ScaffoldError) as ctx:
            scaffold.activate(conn, aid, by="u1")
        self.assertIn("入层门", str(ctx.exception))
        self.assertEqual(scaffold.get(conn, aid)["state"], "proposed")

    def test_it_can_be_activated_after_the_gate(self):
        conn = _fresh()
        ids = _batch(conn)
        staging.gate(conn, by="reviewer", batch="b1", full=True)
        done = staging.promote_passed(conn, by="u1", batch="b1")
        self.assertEqual(sorted(done), sorted(ids))
        for aid in ids:
            self.assertEqual(scaffold.get(conn, aid)["state"], "active")

    def test_a_community_node_is_not_gated(self):
        """反向判据：社区贡献**不该**被这道门挡住 —— 门只管导入层。"""
        conn = _fresh()
        cid = scaffold.add_artifact(conn, type_="Claim", origin="u1",
                                    content={"text": "用户自己说的一句话"})
        scaffold.activate(conn, cid, by="u1")
        self.assertEqual(scaffold.get(conn, cid)["state"], "active")

    def test_a_rejected_batch_never_reaches_active(self):
        conn = _fresh()
        ids = _batch(conn)
        staging.reject_batch(conn, batch="b1", by="reviewer", why="书里查无此句")
        self.assertEqual(staging.promote_passed(conn, by="u1", batch="b1"), [])
        for aid in ids:
            with self.assertRaises(scaffold.ScaffoldError):
                scaffold.activate(conn, aid, by="u1")

    def test_a_staged_row_without_a_gate_record_is_refused(self):
        """intake 说是导入来的，staging 里却没有记录 —— 门没落，不许确认。"""
        conn = _fresh()
        aid = scaffold.add_artifact(
            conn, type_="Claim", origin="distiller-v1",
            content={"text": "绕过 staging 建出来的一条"},
            digital_source_type="compositeWithTrainedAlgorithmicMedia",
            asserted_by="某书作者", intake="staged",
        )
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.activate(conn, aid, by="u1")


class TestGateSampling(unittest.TestCase):
    """首本全过（校准蒸馏器），之后抽样 —— 且抽样是**确定性**的。"""

    def test_the_first_book_is_reviewed_in_full(self):
        conn = _fresh()
        _batch(conn, batch="b1", uri=BOOK_A, texts=tuple(f"第{i}条" for i in range(6)))
        out = staging.gate(conn, by="reviewer", batch="b1")
        self.assertTrue(out["full"])
        self.assertEqual(len(out["passed"]), 6)
        self.assertEqual(out["held"], [])

    def test_the_second_book_is_sampled(self):
        conn = _fresh()
        _batch(conn, batch="b1", uri=BOOK_A, texts=("甲",))
        staging.gate(conn, by="reviewer", batch="b1")
        ids = _batch(conn, batch="b2", uri=BOOK_B,
                     texts=tuple(f"第{i}条" for i in range(20)))
        out = staging.gate(conn, by="reviewer", batch="b2")
        self.assertFalse(out["full"])
        self.assertEqual(len(out["passed"]), 2)      # 10% of 20
        self.assertEqual(len(out["held"]), 18)
        self.assertEqual(sorted(out["passed"] + out["held"]), sorted(ids))

    def test_sampling_is_reproducible(self):
        """抽检必须可复现 —— 否则「这批抽到的那几条有问题」复盘不了。"""
        self.assertEqual(staging.sample_indices(20, 10),
                         staging.sample_indices(20, 10))
        self.assertEqual(staging.sample_indices(20, 100), list(range(20)))

    def test_sampling_never_returns_nothing_when_there_is_something(self):
        """反向判据：一条也不抽等于没有门 —— 哪怕比例算出来小于 1，也要抽一条。"""
        self.assertEqual(len(staging.sample_indices(3, 10)), 1)


class TestLayerIsRebuildable(unittest.TestCase):
    """★ 「导入层可整层重建」的前提是**可验证的**，不是一句设计意图。"""

    def test_clearing_a_batch_leaves_the_community_layer_identical(self):
        conn = _fresh()
        scaffold.add_artifact(conn, type_="Claim", origin="u1",
                              content={"text": "用户自己写的一条"})
        before = staging.layer_snapshot(conn, intake="direct")

        ids = _batch(conn)
        staging.gate(conn, by="reviewer", batch="b1", full=True)
        staging.promote_passed(conn, by="u1", batch="b1")

        out = staging.clear_batch(conn, batch="b1")
        self.assertEqual(out["removed"], len(ids))
        self.assertEqual(out["orphans"], [])
        self.assertEqual(staging.layer_snapshot(conn, intake="direct"), before)

    def test_the_imported_layer_carries_nothing_that_cannot_be_regenerated(self):
        """导入层唯一的「独有信息」是 (batch, source_uri, selector) —— 全都在库里可查。"""
        conn = _fresh()
        _batch(conn)
        rows = conn.execute(
            "SELECT artifact_id, batch, source_uri FROM staging ORDER BY rowid"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["batch"], "b1")
            self.assertEqual(r["source_uri"], BOOK_A)
            self.assertIsNotNone(scaffold.pointer_of(conn, r["artifact_id"]))

    def test_a_batch_with_no_rows_is_a_no_op(self):
        conn = _fresh()
        self.assertEqual(staging.clear_batch(conn, batch="不存在"),
                         {"removed": 0, "orphans": []})


class TestOrphanEdgesBlockRebuild(unittest.TestCase):
    """★ 社区层一旦指进导入层，整层重建就会撕开社区层 —— **不许自动做**。"""

    def _linked(self):
        conn = _fresh()
        ids = _batch(conn)
        staging.gate(conn, by="reviewer", batch="b1", full=True)
        staging.promote_passed(conn, by="u1", batch="b1")
        cid = scaffold.add_artifact(conn, type_="Claim", origin="u1",
                                    content={"text": "社区里引了书里那一句"})
        scaffold.add_relation(conn, kind="quoted_from", from_id=cid, to_id=ids[0],
                              origin="u1")
        return conn, ids, cid

    def test_an_inbound_community_edge_is_reported(self):
        conn, ids, cid = self._linked()
        orphans = staging.orphan_edges(conn, "b1")
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["from_id"], cid)
        self.assertEqual(orphans[0]["to_id"], ids[0])

    def test_clearing_is_refused_while_the_edge_is_there(self):
        conn, _ids, _cid = self._linked()
        with self.assertRaises(staging.StagingError):
            staging.clear_batch(conn, batch="b1")
        # 拒绝之后什么都没动。
        self.assertEqual(len(staging.batch_ids(conn, "b1")), 2)

    def test_forcing_reports_exactly_what_it_tore(self):
        """强删不是「忽略警告」，是「接受社区层被撕开」—— 撕了什么必须交出来。"""
        conn, ids, cid = self._linked()
        out = staging.clear_batch(conn, batch="b1", force=True)
        self.assertEqual(out["removed"], 2)
        self.assertEqual(len(out["orphans"]), 1)
        self.assertEqual(staging.batch_ids(conn, "b1"), [])
        # 社区节点本身**没被删**（只断了那条边）—— 社区层不可再生。
        self.assertEqual(scaffold.get(conn, cid)["state"], "proposed")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) AS n FROM relation WHERE from_id = ?",
                         (cid,)).fetchone()["n"], 0)


if __name__ == "__main__":
    # ⚠️ 这个块不能省。少了它，`python test_staging.py` 什么也不做、退出码 0。
    unittest.main(verbosity=2)
