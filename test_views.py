"""物化视图的**行为**验证 —— 纯读缓存，永不回写写模型。

静态那半在 `checks.py` 的 **B25**（写语句只碰视图表、列名白名单、
视图表不在 canonical schema 里、快照覆盖全部 canonical 表）。
这里拦的是静态拦不住的那半：**跑起来之后，底层真的一个字段都没动吗。**

--- 这个文件要证的四件事 -------------------------------------------------------

| 要证什么 | 靠哪几条 |
|---|---|
| ① 视图可**整批重建**，且**不看旧内容** | `TestItCanBeRebuiltWholesale` |
| ② 重建前后 lower **逐字段一致** | `TestTheLowerLayerDoesNotMove` |
| ③ 删光视图 lower **一字不少** | `TestTheLowerLayerDoesNotMove`（同组） |
| 视图**不复制底层对象** | `TestTheViewStoresNoContent` |
| 展开跟着**结构**走，不跟热度走 | `TestExpansionFollowsStructureNotPopularity` |

⚠️ 本文件刻意只依赖 `scaffold.py` / `views.py` 与标准库 ——
和 `test_rules.py` / `test_candidates.py` 一个规矩：
**能用几十行读懂的依赖，才算真的独立。**

    python test_views.py
"""

from __future__ import annotations

import re
import unittest

import scaffold
import views


class _Base(unittest.TestCase):

    def setUp(self):
        self.conn = scaffold.connect()
        scaffold.init(self.conn)
        self.debate = self._mk("Debate", {"question": "该不该 A？"})
        self.topic = self._mk("Topic", {"text": "议题"})
        scaffold.add_relation(self.conn, kind="contains",
                              from_id=self.debate, to_id=self.topic,
                              origin="alice")

    def tearDown(self):
        self.conn.close()

    # --- 造数据 -----------------------------------------------------------

    def _mk(self, type_: str, content: dict, by: str = "alice") -> str:
        aid = scaffold.add_artifact(
            self.conn, type_=type_, content=content, origin=by)
        scaffold.activate(self.conn, aid, by=by)
        return aid

    def _claim(self, text: str, *, under: str | None = None,
               by: str = "alice") -> str:
        c = self._mk("Claim", {"text": text}, by=by)
        scaffold.add_relation(self.conn, kind="contains",
                              from_id=under or self.topic, to_id=c, origin=by)
        return c

    def _challenge(self, claim: str, *, n: int = 1,
                   state: str = "active") -> str:
        """给一条命题挂反驳。`state != 'active'` 时那条边**不算数**。"""
        last = ""
        for i in range(n):
            ca = self._mk("Counterargument", {"text": f"反驳{i}"}, by="bob")
            rid = scaffold.add_relation(
                self.conn, kind="challenged_by",
                from_id=claim, to_id=ca, origin="bob")
            if state != "active":
                self.conn.execute("UPDATE relation SET state = ? WHERE id = ?",
                                  (state, rid))
            last = ca
        self.conn.commit()
        return last

    # --- 读视图 -----------------------------------------------------------

    def _view_rows(self) -> list[dict]:
        return views.rows(self.conn, view=views.view_name(self.debate))

    def _items(self) -> list[str]:
        return [r["item_id"] for r in self._view_rows()]

    def _expanded(self) -> list[str]:
        return [r["item_id"] for r in self._view_rows() if r["expanded"]]


# ---------------------------------------------------------------------------
# ① 视图可整批重建 —— 而且**不看旧内容**
# ---------------------------------------------------------------------------

class TestItCanBeRebuiltWholesale(_Base):
    """「重建」与「增量补丁」的分界：前者不需要任何存量状态。

    增量补丁要一份「上次算到哪」的记录，而那份记录本身会过期 ——
    **过期的缓存看起来和新鲜的完全一样**，这是缓存最坏的失败方式。
    """

    def test_rebuilding_twice_gives_the_same_projection(self):
        self._claim("甲")
        self._claim("乙")
        first = views.rebuild(self.conn, debate_id=self.debate)
        second = views.rebuild(self.conn, debate_id=self.debate)
        self.assertTrue(first["changed"], "第一次重建应当是有内容的")
        self.assertFalse(second["changed"], "第二次重建结果与第一次不同")
        self.assertEqual(first["expanded"], second["expanded"])
        self.assertEqual(first["items"], second["items"])

    def test_rebuilding_does_not_read_the_old_view(self):
        """★ 把视图换成一份**看起来正常**的假内容，重建之后必须长回正确的样子。

        这条是「不看旧内容」的可执行形式 —— 增量补丁会在这里原样保留坏数据。
        """
        a, b = self._claim("甲"), self._claim("乙")
        views.rebuild(self.conn, debate_id=self.debate)

        # 删光，塞一条**指向 debate 自己**的行 —— 结构合法、字段齐全，
        # 只是它不来自任何投影。
        self.conn.execute(f"DELETE FROM {views.VIEW_TABLE} WHERE view = ?",
                          (views.view_name(self.debate),))
        self.conn.execute(
            f"INSERT INTO {views.VIEW_TABLE}"
            " (view, position, item_id, expanded, built_at, version)"
            " VALUES (?,?,?,?,?,?)",
            (views.view_name(self.debate), 0, self.debate, 1,
             "1970-01-01T00:00:00", views.VIEWS_VERSION))
        self.conn.commit()
        self.assertEqual(self._items(), [self.debate])

        views.rebuild(self.conn, debate_id=self.debate)
        self.assertEqual(self._items(), sorted([a, b, self.topic]))
        self.assertEqual(self._expanded(), [])

    def test_rebuild_all_brings_everything_back_after_a_wipe(self):
        """★ 出口判据 ①里「整批」那个词最直接的形式：把表删干净，它自己长回来。"""
        self._claim("甲")
        self._challenge(self._claim("乙"))
        views.rebuild_all(self.conn)          # 先建一次，才有「长回来」可比
        before = [(r["position"], r["item_id"], r["expanded"])
                  for r in self._view_rows()]

        views.clear(self.conn)
        self.assertEqual(self._view_rows(), [], "清空之后不该还有视图行")

        views.rebuild_all(self.conn)
        after = [(r["position"], r["item_id"], r["expanded"])
                 for r in self._view_rows()]
        self.assertEqual(after, before)

    def test_rebuilding_an_unknown_debate_is_refused(self):
        """不存在的讨论**直接抛**，不许静默建出一个空视图 ——
        空视图和「这个讨论确实没内容」在库里长得一样。"""
        with self.assertRaises(scaffold.ScaffoldError):
            views.rebuild(self.conn, debate_id="debate-9999")

    def test_a_view_that_was_never_built_is_empty(self):
        self.assertEqual(self._view_rows(), [])


# ---------------------------------------------------------------------------
# ②③ 重建 / 删除都不动底层
# ---------------------------------------------------------------------------

class TestTheLowerLayerDoesNotMove(_Base):
    """出口判据 ② 与 ③。

    ⚠️ 与 `upper.py` 那条行为用例的差别：`upper` **有能力**写底层，只是不该写；
    本模块**没有那个能力**（除了视图表之外一句写语句都没有）。
    所以这里验的不只是「没写」，还有「**写不进去**」——
    `rebuild()` 每次调用都自比一次，那条路径理论上不可达。
    """

    def _fixture(self) -> None:
        self._claim("甲")
        self._challenge(self._claim("乙"), n=2)

    def test_rebuild_leaves_the_lower_layer_byte_identical(self):
        self._fixture()
        before = views.snapshot(self.conn)
        views.rebuild(self.conn, debate_id=self.debate)
        self.assertEqual(views.snapshot(self.conn), before)

    def test_rebuild_all_leaves_the_lower_layer_byte_identical(self):
        self._fixture()
        before = views.snapshot(self.conn)
        views.rebuild_all(self.conn)
        self.assertEqual(views.snapshot(self.conn), before)

    def test_clear_leaves_the_lower_layer_byte_identical(self):
        """★ 出口判据 ③：**删掉全部视图，lower 一字不少。**"""
        self._fixture()
        views.rebuild(self.conn, debate_id=self.debate)
        before = views.snapshot(self.conn)
        n = views.clear(self.conn)
        self.assertTrue(n, "没删到东西 —— 用例没测到目标")
        self.assertEqual(views.snapshot(self.conn), before)

    def test_rebuilding_records_no_event(self):
        """★ 判据 ② 的一个**直接后果**，单独钉一条用例。

        `event` 是 canonical 表之一，所以「重建时记一条 `view_rebuilt`」
        这条路是**关着的**。不是省事，是判据推出来的：视图是缓存，
        **缓存不留审计轨迹** —— 留了它就不是纯读缓存，「删掉零损失」也不成立
        （删视图会连那些事件一起删掉）。
        """
        self._fixture()
        before = self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
        views.rebuild(self.conn, debate_id=self.debate)
        views.rebuild_all(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0],
            before, "视图层往 event 表写了东西 —— 那它就不是纯读缓存了")

    def test_the_snapshot_covers_every_canonical_table(self):
        """★ 少一张表，那张表被视图改了也看不出来。

        这条与 B25 判据 4 同源，在这里复述一遍是为了让**单独读这个文件的人**
        也能看到那个集合是从哪儿来的：`scaffold.SCHEMA` 才是唯一的事实来源。
        """
        declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)",
                                  scaffold.SCHEMA))
        self.assertEqual(set(views.LOWER_TABLES), declared)
        self.assertNotIn(views.VIEW_TABLE, declared,
                         "视图表混进 canonical schema 了 —— 存储轴混了")

    def test_the_guard_would_notice_a_changed_field(self):
        """★ **反向判据**：守卫真的看得见「改了字段但没改行数」。

        `UPDATE artifact SET state='x'` 不改行数 —— 只数行数的快照看不见它，
        而那是单向性最典型的破坏方式。
        """
        claim = self._claim("甲")
        before = views.snapshot(self.conn)
        n_before = len(before["artifact"])
        self.conn.execute("UPDATE artifact SET state = 'superseded' WHERE id = ?",
                          (claim,))
        self.conn.commit()
        after = views.snapshot(self.conn)
        self.assertEqual(len(after["artifact"]), n_before, "行数没变 —— 正是这条用例要的")
        self.assertNotEqual(after, before, "改了字段却没被快照发现")


# ---------------------------------------------------------------------------
# 视图**不复制底层对象**
# ---------------------------------------------------------------------------

class TestTheViewStoresNoContent(_Base):
    """视图里存的是 **id**，不是正文 —— 理由同 `pointer.py`：

    **副本无法证明自己等于原文。** 存 id 就不会有「指得到但内容旧了」这一档。
    """

    def test_the_view_row_carries_no_text(self):
        """★ 视图行的字段必须落在 `VIEW_FIELDS` 白名单里。"""
        self._claim("甲")
        views.rebuild(self.conn, debate_id=self.debate)
        for row in self._view_rows():
            self.assertLessEqual(set(row), set(views.VIEW_FIELDS), row)

    def test_no_column_can_hold_a_body(self):
        """★ 白名单里**没有**放正文的地方 —— 这是判据，不是文档。"""
        for forbidden in ("text", "content", "body", "raw_text", "quote"):
            self.assertNotIn(forbidden, views.VIEW_FIELDS)

    def test_the_item_ids_point_at_real_artifacts(self):
        self._claim("甲")
        self._challenge(self._claim("乙"))
        views.rebuild(self.conn, debate_id=self.debate)
        for item_id in self._items():
            self.assertTrue(scaffold.get(self.conn, item_id))

    def test_the_view_follows_the_lower_layer_instead_of_freezing_it(self):
        """★ 底层变了，重建之后视图跟着变 —— 视图是**缓存**，不是副本。

        这条与上一条合起来才是「不复制底层对象」的完整意思：
        视图里没有正文可旧，所以它永远不会「内容旧了但看起来正常」。
        """
        claim = self._claim("甲")
        views.rebuild(self.conn, debate_id=self.debate)
        self.assertEqual(self._expanded(), [])

        self._challenge(claim)
        views.rebuild(self.conn, debate_id=self.debate)
        self.assertEqual(self._expanded(), [claim])


# ---------------------------------------------------------------------------
# 展开跟着**结构**走，不跟热度走
# ---------------------------------------------------------------------------

class TestExpansionFollowsStructureNotPopularity(_Base):
    """`§C7.1` ④ 允许上层影响的只有「展示顺序 / 默认展开 / 推荐候选」三件事，
    **没有一件允许读热度**（不变量 #5，B4 盯着 `upper.py` 那条线）。

    所以这里的判据必须是**结构量**：`challenged_by` 是白名单里的结构量。
    """

    def test_a_challenged_node_is_expanded(self):
        claim = self._claim("甲")
        self._challenge(claim)
        views.rebuild(self.conn, debate_id=self.debate)
        self.assertEqual(self._expanded(), [claim])

    def test_the_direction_is_from_not_to(self):
        """★ **反向判据**：`challenged_by` 是 `Claim → Counterargument`（`§C4`），
        所以被质询的是 `from_id`。按 `to_id` 数的话，展开的会是那些反驳本身 ——
        读起来完全说得通，只是标错了对象。实测第一版就是这么写的。
        """
        claim = self._claim("甲")
        counter = self._challenge(claim)
        views.rebuild(self.conn, debate_id=self.debate)
        self.assertIn(claim, self._expanded())
        self.assertNotIn(counter, self._expanded())

    def test_an_inactive_challenge_does_not_expand(self):
        """还没被确认的反驳**不算数** —— 「记下来了」与「算数了」是两件事。"""
        for state in ("proposed", "rejected", "superseded"):
            with self.subTest(state=state):
                conn = scaffold.connect()
                scaffold.init(conn)
                debate = self._mk_on(conn, "Debate", {"question": "？"})
                topic = self._mk_on(conn, "Topic", {"text": "议题"})
                scaffold.add_relation(conn, kind="contains", from_id=debate,
                                      to_id=topic, origin="alice")
                claim = self._mk_on(conn, "Claim", {"text": "甲"})
                scaffold.add_relation(conn, kind="contains", from_id=topic,
                                      to_id=claim, origin="alice")
                ca = self._mk_on(conn, "Counterargument", {"text": "反驳"})
                rid = scaffold.add_relation(conn, kind="challenged_by",
                                            from_id=claim, to_id=ca,
                                            origin="bob")
                conn.execute("UPDATE relation SET state = ? WHERE id = ?",
                             (state, rid))
                conn.commit()
                views.rebuild(conn, debate_id=debate)
                got = [r["item_id"] for r in
                       views.rows(conn, view=views.view_name(debate))
                       if r["expanded"]]
                self.assertEqual(got, [], f"{state} 的边不该让节点展开")
                conn.close()

    def test_an_inactive_member_is_not_listed(self):
        claim = self._claim("甲")
        raw = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": "还没确认"}, origin="bob")
        scaffold.add_relation(self.conn, kind="contains", from_id=self.topic,
                              to_id=raw, origin="bob")
        views.rebuild(self.conn, debate_id=self.debate)
        self.assertIn(claim, self._items())
        self.assertNotIn(raw, self._items())

    def test_the_ordering_is_deterministic(self):
        """同一份底层必须重建出同一份视图 —— 否则「重建」和「重算」是两件事。"""
        for text in ("甲", "乙", "丙"):
            self._claim(text)
        views.rebuild(self.conn, debate_id=self.debate)
        first = self._items()
        views.clear(self.conn)
        views.rebuild(self.conn, debate_id=self.debate)
        self.assertEqual(self._items(), first)
        self.assertEqual(first, sorted(first), "顺序不是按 id 的 —— 不确定")

    # 子用例要自己开连接，所以另给一个不碰 self 的造数口
    @staticmethod
    def _mk_on(conn, type_: str, content: dict, by: str = "alice") -> str:
        aid = scaffold.add_artifact(conn, type_=type_, content=content,
                                    origin=by)
        scaffold.activate(conn, aid, by=by)
        return aid


if __name__ == "__main__":
    unittest.main(verbosity=2)
