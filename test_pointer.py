"""定位符的**行为**验证（2026-09-28）。

`checks.py` 的 B18 是**静态**的 —— 它只证明「白名单定义了、守卫在代码里」。
这个文件证明守卫**真的会拦**、而且拦在**唯一那道漏斗**上。

分工同 B14 与 `test_upper.py::test_promote_never_touches_the_lower_layer`：
**静态守代码形状，行为守运行结果。**

⚠️ 这里最要紧的一组用例是 `TestTheGuardIsOnTheOnlyFunnel`。
只验 `add_artifact` 是不够的 —— `revise()` 是**另一条**写入路径，
B14 那条行为验证正是抓到了「走 revise 的间接写静态看不见」。
定位符的守卫接在 `_append_revision()` 上（两条路共用的漏斗），
所以这里必须**两条路都验**。
"""

from __future__ import annotations

import unittest

import pointer
import scaffold


def _fresh():
    conn = scaffold.connect(":memory:")
    scaffold.init(conn)
    return conn


BOOK = "urn:isbn:9780000000000"
PAGE = {"type": "TextQuoteSelector", "exact": "稀缺性来自供给的不可复制"}


class TestTheLocatorIsATuple(unittest.TestCase):
    """定位符就是 (uri, selector)，多一个字段都不行。"""

    def test_a_well_formed_locator_passes(self):
        p = pointer.make_pointer(uri=BOOK, selector=PAGE)
        self.assertEqual(pointer.read({"pointer": p}), p)

    def test_a_uri_without_a_scheme_is_refused(self):
        """相对路径与裸文本都指不到外面那个东西 —— 挡掉。"""
        for bad in ("", "   ", "chapters/3.md", "第 42 页"):
            with self.assertRaises(pointer.PointerError, msg=f"{bad!r} 竟然过了"):
                pointer.make_pointer(uri=bad, selector=PAGE)

    def test_a_selector_without_a_type_is_refused(self):
        with self.assertRaises(pointer.PointerError):
            pointer.make_pointer(uri=BOOK, selector={"exact": "随便一句话"})

    def test_no_pointer_means_no_pointer(self):
        """「没有」与「有但坏」是两件事 —— 没有就返回 None，不抛。"""
        self.assertIsNone(pointer.read({"text": "用户自己写的一条主张"}))
        self.assertIsNone(pointer.target_of({"text": "同上"}))


class TestNoBodyIsStored(unittest.TestCase):
    """★ 不存正文 —— 这是 P5（Reference ≠ Derivation）在数据形状上的样子。"""

    def test_a_body_field_on_the_locator_is_refused(self):
        """「顺手把正文也存一份」—— 它只能表现为一个多余字段，所以拦得住。"""
        for field in ("body", "text", "content", "full_text", "raw"):
            bad = {"uri": BOOK, "selector": PAGE, field: "整章正文……"}
            with self.assertRaises(pointer.PointerError, msg=f"{field} 竟然过了"):
                pointer.verify(bad)

    def test_a_body_field_on_the_selector_is_refused(self):
        """selector 的字段是**封闭**的 —— 多一个就说明有人想往里塞东西。"""
        with self.assertRaises(pointer.PointerError):
            pointer.make_selector(
                kind="TextQuoteSelector", exact="一句话", full="整章正文……")

    def test_the_quote_is_not_a_body_field(self):
        """⚠️ 反向判据：`exact` **必须**允许 —— 它是定位所需的最小引文。

        这条与上面两条是一对。只验「多字段被拦」，会把
        「连 exact 都不许有」也判成对的 —— 那样定位符就指不到任何地方了。
        """
        p = pointer.make_pointer(uri=BOOK, selector=PAGE)
        self.assertEqual(p["selector"]["exact"], PAGE["exact"])

    def test_the_position_selector_is_allowed_too(self):
        s = pointer.make_selector(kind="TextPositionSelector", start=120, end=148)
        self.assertEqual((s["start"], s["end"]), (120, 148))


class TestSelectorShapes(unittest.TestCase):
    """两种 selector 各自的形状 —— 照抄 W3C，不自造。"""

    def test_the_names_are_the_w3c_ones(self):
        for k in pointer.SELECTOR_KINDS:
            self.assertTrue(k.endswith("Selector"), f"{k} 不是 W3C 的命名")

    def test_an_unknown_selector_kind_is_refused(self):
        with self.assertRaises(pointer.PointerError):
            pointer.make_selector(kind="MyOwnSelector", exact="x")

    def test_the_position_selector_wants_non_negative_ints(self):
        for bad in (-1, "120", 12.5, True):
            with self.assertRaises(pointer.PointerError, msg=f"{bad!r} 竟然过了"):
                pointer.make_selector(
                    kind="TextPositionSelector", start=bad, end=200)

    def test_a_reversed_span_is_refused(self):
        with self.assertRaises(pointer.PointerError):
            pointer.make_selector(kind="TextPositionSelector", start=200, end=120)

    def test_an_empty_quote_is_refused(self):
        with self.assertRaises(pointer.PointerError):
            pointer.make_selector(kind="TextQuoteSelector", exact="   ")


class TestTheGuardIsOnTheOnlyFunnel(unittest.TestCase):
    """★ 守卫必须接在 `_append_revision()` 上 —— 两条写入路径共用的那道漏斗。

    只验 `add_artifact` 会漏掉一半：`revise()` 是**另一条**路。
    """

    def test_add_artifact_refuses_a_bad_locator(self):
        conn = _fresh()
        with self.assertRaises(pointer.PointerError):
            scaffold.add_artifact(
                conn, type_="Claim", origin="u1",
                content={"text": "某条主张", "pointer": {"uri": "没有 scheme"}},
            )

    def test_revise_refuses_a_bad_locator_too(self):
        """★ 这一条才是重点 —— 换一条写入路径，守卫还在不在。"""
        conn = _fresh()
        cid = scaffold.add_artifact(conn, type_="Claim", origin="u1",
                                    content={"text": "某条主张"})
        with self.assertRaises(pointer.PointerError):
            scaffold.revise(conn, cid, author="u1", content={
                "text": "某条主张",
                "pointer": {"uri": BOOK, "selector": {"type": "TextQuoteSelector"}},
            })

    def test_a_good_locator_survives_both_paths(self):
        """反向判据：好定位符两条路都得能过 —— 守卫不能拦成「谁都过不去」。"""
        conn = _fresh()
        cid = scaffold.add_artifact(
            conn, type_="Claim", origin="u1",
            content=pointer.attach({"text": "引了书里一句"}, uri=BOOK, selector=PAGE),
        )
        self.assertEqual(scaffold.pointer_of(conn, cid)["uri"], BOOK)
        scaffold.revise(
            conn, cid,
            content=pointer.attach({"text": "改了个说法"}, uri=BOOK, selector=PAGE),
            author="u1",
        )
        self.assertEqual(scaffold.pointer_of(conn, cid)["uri"], BOOK)

    def test_the_reference_does_not_copy_the_target(self):
        """存进去的东西里**没有**任何可以当正文用的字段。"""
        conn = _fresh()
        cid = scaffold.add_artifact(
            conn, type_="Claim", origin="u1",
            content=pointer.attach({"text": "本地这条主张自己的话"}, uri=BOOK, selector=PAGE),
        )
        stored = scaffold.content_of(conn, cid)["pointer"]
        self.assertEqual(sorted(stored), ["selector", "uri"])
        self.assertEqual(sorted(stored["selector"]), ["exact", "type"])


class TestReadingBack(unittest.TestCase):
    def test_target_of_gives_the_uri_and_the_selector_family(self):
        """只到「同一个 uri、同一种定位方式」这一档 —— 不往下猜。"""
        c = pointer.attach({"text": "x"}, uri=BOOK, selector=PAGE)
        self.assertEqual(pointer.target_of(c), (BOOK, "TextQuoteSelector"))

    def test_a_forked_artifact_has_no_single_locator(self):
        """两个分支可能指两个地方 —— 挑一个就是替用户做了一次判断（§C10）。"""
        conn = _fresh()
        cid = scaffold.add_artifact(
            conn, type_="Claim", origin="u1",
            content=pointer.attach({"text": "x"}, uri=BOOK, selector=PAGE),
        )
        root = scaffold.heads_of(conn, cid)[0]["id"]
        scaffold.revise(conn, cid, content=pointer.attach(
            {"text": "分支甲"}, uri=BOOK, selector=PAGE), author="u1", parent_rev=root)
        scaffold.revise(conn, cid, content=pointer.attach(
            {"text": "分支乙"}, uri=BOOK,
            selector={"type": "TextPositionSelector", "start": 1, "end": 9}),
            author="u2", parent_rev=root)
        self.assertEqual(len(scaffold.heads_of(conn, cid)), 2)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.pointer_of(conn, cid)


if __name__ == "__main__":
    # ⚠️ 这个块不能省。少了它，`python test_pointer.py` 什么也不做、退出码 0。
    unittest.main(verbosity=2)
