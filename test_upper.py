"""上层归纳的**行为**验证 —— 只派生，不倒流。

这个文件是**自包含**的：只依赖 `scaffold.py` 与标准库。
它不引用 `debate` / `confirm` / `vote` 任何一个词 ——
那些模块在上游产品里存在，但**本机制不需要它们**。

这不是为了「少几个 import」。是因为本体想证明的那件事
（上层只派生、绝不到流）如果依赖了别的模块，读者就得先相信那些模块才敢相信这条断言。
现在它只依赖 `scaffold.py` 的读写原语 —— **能用几十行读懂的依赖，才算真的独立。**

静态那半在 `checks.py` 的 B14（拦直笔写回底层），这里拦**绕路**：
走 `scaffold.revise()` 之类的间接写，B14 的正则看不见。
两层加起来才完整。
"""

from __future__ import annotations

import json
import re
import unittest

import checks
import scaffold
import upper
from scaffold import ScaffoldError


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = scaffold.connect(":memory:")
        scaffold.init(self.conn)
        # 造一个 Debate 当锚点 —— 上层节点得挂在它下面才到得了视图。
        # 这里直接走底层 API：本文件刻意不引 `debate`（见模块开头）。
        self.debate = scaffold.add_artifact(
            self.conn, type_="Debate", content={"question": "该不该 A？"},
            origin="alice")
        scaffold.activate(self.conn, self.debate, by="alice")

    def _claim(self, text="假设甲", *, anchor=True) -> str:
        """造一条 active 的命题。`anchor=False` 时**不挂**任何 Topic（用来测悬空）。"""
        claim = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": text}, origin="alice")
        scaffold.activate(self.conn, claim, by="alice")
        if anchor:
            topic = scaffold.add_artifact(
                self.conn, type_="Topic", content={"text": "议题"}, origin="alice")
            scaffold.activate(self.conn, topic, by="alice")
            scaffold.add_relation(self.conn, kind="contains",
                                  from_id=self.debate, to_id=topic, origin="alice")
            scaffold.add_relation(self.conn, kind="contains",
                                  from_id=topic, to_id=claim, origin="alice")
        return claim

    def _challenged_claim(self, *, n=3, text="假设甲", anchor=True) -> str:
        """造一条**已被质询 n 次**的命题。"""
        claim = self._claim(text, anchor=anchor)
        for i in range(n):
            c = scaffold.add_artifact(
                self.conn, type_="Counterargument",
                content={"text": f"反驳{i}"}, origin="bob")
            scaffold.activate(self.conn, c, by="bob")
            scaffold.add_relation(self.conn, kind="challenged_by",
                                  from_id=claim, to_id=c, origin="bob")
        return claim

    # --- 底层快照：单向性的地基 ------------------------------------------
    def _lower_snapshot(self, ids: list[str]) -> dict:
        q = ",".join("?" * len(ids))
        return {
            "rev": sorted(map(tuple, self.conn.execute(
                f"SELECT id, artifact_id, parent_rev, content FROM revision"
                f" WHERE artifact_id IN ({q})", ids).fetchall())),
            "state": sorted(map(tuple, self.conn.execute(
                f"SELECT id, state FROM artifact WHERE id IN ({q})", ids).fetchall())),
        }

    def _challenger_ids(self, claim: str) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT to_id FROM relation WHERE from_id=? AND kind='challenged_by'",
            (claim,))]

    def _one_context(self) -> str:
        claim = self._challenged_claim()
        cands = upper.propose_clusters(self.conn)
        made = upper.promote_candidates(
            self.conn, candidates=cands["candidates"], by="alice",
            debate_id=self.debate)
        self.assertEqual(len(made), 1)
        return made[0]

    # --- ① propose 是纯读 -----------------------------------------------
    def test_propose_writes_nothing_at_all(self):
        """提议是**只读**的。一个字都不许写 —— 连事件都不该多。

        「提议」是高频动作（每次重算都会跑），「建」是低频动作。
        混在一起的话，每次重算都会多出一批节点；而「不得不清理自动产出的垃圾」
        正是让人开始乱删结构的起点。
        """
        claim = self._challenged_claim()
        ids = [claim] + self._challenger_ids(claim)
        before = self._lower_snapshot(ids)
        n_art = self.conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
        n_rel = self.conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0]
        n_rev = self.conn.execute("SELECT COUNT(*) FROM revision").fetchone()[0]

        out = upper.propose_clusters(self.conn)
        self.assertTrue(out["candidates"], "造了 3 次质询却没出候选 —— 用例没测到东西")

        self.assertEqual(self._lower_snapshot(ids), before, "propose 动了底层")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM artifact").fetchone()[0], n_art)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM relation").fetchone()[0], n_rel)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM revision").fetchone()[0], n_rev)

    # --- ② promote 只加不删、只写上层 ------------------------------------
    def test_promote_never_touches_the_lower_layer(self):
        """单向性的可执行形式：**底层一字不少，一条没改。**"""
        claim = self._challenged_claim()
        ids = [claim] + self._challenger_ids(claim)
        before = self._lower_snapshot(ids)
        rel_before = {r[0] for r in self.conn.execute("SELECT id FROM relation")}

        cands = upper.propose_clusters(self.conn)
        made = upper.promote_candidates(
            self.conn, candidates=cands["candidates"], by="alice",
            debate_id=self.debate)

        self.assertTrue(made, "没建出任何上层节点")
        # 底层内容 / 状态 / 版本 **逐字节相同**
        self.assertEqual(self._lower_snapshot(ids), before,
                         "promote 改了底层（内容 / 状态 / 版本）")
        # 底层**原来的边一条没删**
        rel_after = {r[0] for r in self.conn.execute("SELECT id FROM relation")}
        self.assertTrue(rel_before <= rel_after,
                        f"promote 删了底层边：{rel_before - rel_after}")
        # 新增的边**只有** upper 那一种
        added_kinds = {r[0] for r in self.conn.execute(
            "SELECT DISTINCT kind FROM relation "
            "WHERE id NOT IN (%s)" % ",".join(map(str, rel_before)))}
        self.assertTrue(
            added_kinds <= {"clustered_into", "contains"},
            f"promote 加了 {upper.UPPER_RELATION} / contains 之外的边："
            f"{added_kinds} —— 上层不许往底层加真值边")
        # 而 `contains` 那条**必须**是 Debate → Context（挂上去），
        # 不是从底层命题指过去 —— 那才是「往底层加真值边」。
        ctx = made[0]
        anchor = self.conn.execute(
            "SELECT from_id, to_id FROM relation"
            " WHERE kind='contains' AND to_id=?", (ctx,)).fetchone()
        self.assertEqual(anchor["from_id"], self.debate,
                         "contains 边的起点不是 Debate —— 上层节点挂错地方了")

    def test_promote_refuses_evidence_that_is_not_active_yet(self):
        """⚠️ 硬检查：**上层归纳不许把「未确认」提拔成「已确认」。**

        那是确认环节的权力。这是本模块最危险的那种越权 ——
        它看起来只是「顺手把这条也并进去」，实际是**替人确认了一条命题**。
        """
        claim = self._challenged_claim()
        raw = scaffold.add_artifact(
            self.conn, type_="Counterargument",
            content={"text": "还没确认的反驳"}, origin="bob")
        scaffold.add_relation(self.conn, kind="challenged_by",
                              from_id=claim, to_id=raw, origin="bob")
        self.assertEqual(scaffold.get(self.conn, raw)["state"], "proposed")

        with self.assertRaises(ScaffoldError) as cm:
            upper.promote_candidates(
                self.conn, by="alice", debate_id=self.debate,
                candidates=[{
                    "signal": "challenge_counts", "evidence_ids": [raw],
                    "seed_id": claim, "seed_type": "Claim", "n": 1,
                    "name_source": "human", "name": upper.PENDING_NAME,
                }])
        self.assertIn("active", str(cm.exception))

    def test_a_signal_outside_the_whitelist_is_refused(self):
        """信号白名单。**票数不是证据** —— 热度类信号一律拒。"""
        claim = self._challenged_claim()
        for bad in ("vote_counts", "view_counts", "popularity"):
            with self.assertRaises(ScaffoldError, msg=f"{bad} 被放过了"):
                upper._supporting_ids(self.conn, claim, bad)

    # --- ③ 命名归人 ------------------------------------------------------
    def test_a_new_context_is_born_unnamed_and_active(self):
        """上层节点**建出来就是 active**，但**没有名字**。

        这两件事必须分开，压进同一个字段就错了：
          - `state` 管「这条断言有没有被确认」
          - 「还没起名」是**另一种待定**，由 `name_source` + `PENDING_NAME` 表达

        上层节点不是一条新断言（它是底下已有命题的归组边），
        所以它**不 pending 任何待批的断言** —— 既然系统一个字都没说，
        「等谁确认」就没有对象。这正是「免确认」那条设计的理由。

        ⚠️ 若留成 `proposed`：消费方会把它当成「还没被批准的断言」，
        上层结果就被降格成待批提案，而它本来就允许影响展示顺序。
        """
        made = self._one_context()
        node = scaffold.get(self.conn, made)
        self.assertEqual(node["state"], "active",
                         "上层节点不是待批提案 —— 它建出来就该是 active")
        content = scaffold.content_of(self.conn, made)
        self.assertEqual(content["text"], upper.PENDING_NAME)
        self.assertEqual(content["name_source"], "human")

    def test_renaming_keeps_the_evidence_and_the_signal(self):
        """⚠️ **改名只改名字，其余字段原样带走。**

        `revise()` 换的是**整个 content**（那是对的 —— 它是通用版本接口），
        所以 `rename_context()` 必须**读-改-写**。

        **实测踩过这个坑**：第一版只传了 `{"text": name, "name_source": "human"}`，
        改完名之后 `signal` 变成 `None`、`evidence_ids` 变成 `[]` ——
        **依据全丢了**，而视图照样正常印出名字，看起来完全没问题。
        这正是「坏起来不像坏」：改完名之后，就再也说不清
        「这个上层节点当初是根据什么长出来的」。
        """
        ctx = self._one_context()
        before = scaffold.content_of(self.conn, ctx)
        self.assertTrue(before["evidence_ids"], "用例前提：建出来时是有依据的")

        upper.rename_context(self.conn, context_id=ctx, name="起个名字", by="alice")

        after = scaffold.content_of(self.conn, ctx)
        self.assertEqual(after["text"], "起个名字")
        self.assertEqual(after["evidence_ids"], before["evidence_ids"],
                         "改名把依据弄丢了")
        self.assertEqual(after["signal"], before["signal"],
                         "改名把信号类型弄丢了")
        self.assertEqual(after["induction_version"], before["induction_version"],
                         "改名把规则集版本弄丢了")
        # 视图上也必须还看得见 —— 「字段还在」和「视图印得出」是两件事
        view = upper.ordered_view(self.conn, self.debate)["contexts"][0]
        self.assertEqual(view["evidence_ids"], before["evidence_ids"])
        self.assertEqual(view["signal"], before["signal"])

    def test_renaming_is_a_revision_and_does_not_touch_the_lower_layer(self):
        """命名走 `revise`（不覆盖）—— 而且**不碰底层**。"""
        ctx = self._one_context()
        claim = self.conn.execute(
            "SELECT from_id FROM relation WHERE kind='challenged_by'"
        ).fetchone()[0]
        ids = [claim] + self._challenger_ids(claim)
        before = self._lower_snapshot(ids)

        upper.rename_context(self.conn, context_id=ctx, name="围绕假设甲的分歧",
                             by="alice")
        self.assertEqual(scaffold.content_of(self.conn, ctx)["text"],
                         "围绕假设甲的分歧")
        self.assertEqual(self._lower_snapshot(ids), before, "命名碰了底层")
        # 不覆盖：旧的那一版（PENDING_NAME）还在 revision 表里。
        # ⚠️ `history_of` 返回的是 **dict**（`{"revisions": [...], "heads": ...}`），
        # 不是列表 —— 第一版这里写成 `len(revs)` 量出来是 6（字典的键数），
        # 看着像「有 6 个版本」，其实是量错了东西。
        revs = scaffold.history_of(self.conn, ctx)["revisions"]
        self.assertEqual(len(revs), 2, "命名没走 revise —— 旧版本被覆盖了")
        self.assertEqual([r["content"]["text"] for r in revs],
                         [upper.PENDING_NAME, "围绕假设甲的分歧"])

    def test_renaming_twice_keeps_both_versions(self):
        ctx = self._one_context()
        upper.rename_context(self.conn, context_id=ctx, name="第一版", by="alice")
        upper.rename_context(self.conn, context_id=ctx, name="第二版", by="bob")
        revs = scaffold.history_of(self.conn, ctx)["revisions"]
        self.assertEqual(len(revs), 3, "二次改名覆盖了上一版")
        self.assertEqual([r["content"]["text"] for r in revs][-2:],
                         ["第一版", "第二版"])
        # 谁改的也留得住
        self.assertEqual([r["author"] for r in revs][-2:], ["alice", "bob"])

    def test_an_empty_name_and_the_placeholder_are_both_refused(self):
        """`PENDING_NAME` **不是占位符，是设计前提** —— 不许拿它当名字。

        空名也一样拒：留空是**建的时候**的状态，不是**命名的时候**的输入。
        """
        ctx = self._one_context()
        with self.assertRaises(ScaffoldError):
            upper.rename_context(self.conn, context_id=ctx, name="  ", by="alice")
        with self.assertRaises(ScaffoldError):
            upper.rename_context(self.conn, context_id=ctx,
                                 name=upper.PENDING_NAME, by="alice")

    def test_renaming_a_lower_layer_node_is_refused(self):
        """`rename_context` 只管上层节点 —— 拿底层节点调它要报。"""
        claim = self._challenged_claim()
        with self.assertRaises(ScaffoldError):
            upper.rename_context(self.conn, context_id=claim, name="x", by="alice")

    # --- ④ 视图只影响那三样 ----------------------------------------------
    def test_the_view_only_offers_order_expansion_and_candidates(self):
        """上层允许影响的**全部三种**：顺序 / 默认展开 / 推荐候选。

        视图里**不许**出现任何「哪条命题更重要 / 更对」的字段。
        """
        ctx = self._one_context()
        v = upper.ordered_view(self.conn, self.debate)
        self.assertEqual([c["context"]["id"] for c in v["contexts"]], [ctx])
        keys = set()
        for c in v["contexts"]:
            keys |= set(c.keys())
        self.assertTrue(
            keys <= {"context", "name", "named", "signal", "evidence_ids",
                     "history"},
            f"视图多出了字段：{keys} —— 上层不许表达「谁更重要」")
        # 拿 `checks.py` 自己的规则断言，不另抄一份禁词
        flat = json.dumps(v, ensure_ascii=False)
        for entry in checks.CHECKS:
            self.assertIsNone(re.search(entry[2], flat, re.I),
                              f"上层视图里出现了 {entry[0]} 要挡的东西")

    def test_the_context_is_reachable_from_its_debate(self):
        """挂在 Debate 下 —— 否则任何 `ordered_view` 都到不了它。

        「存在但谁也看不见」比不存在更坏：它占着 id，却不在任何一张视图里。
        """
        ctx = self._one_context()
        row = self.conn.execute(
            "SELECT r.from_id FROM relation r"
            " WHERE r.to_id=? AND r.kind='contains' AND r.state='active'",
            (ctx,)).fetchone()
        self.assertIsNotNone(row, "上层节点没挂在任何东西下 —— 它是悬空的")
        self.assertEqual(row["from_id"], self.debate)

    def test_an_unanchored_context_leaves_a_trace(self):
        """没有锚点时**必须留痕**，不许静默吞掉。

        症状会是「节点建出来了，但任何视图都看不见它」。留一笔事件，
        让失败**看起来像失败**，而不是像「本来就啥也没有」。

        ⚠️ 造这个场景要**真的让它没有锚点**：挂在 Topic 下的命题，
        `_anchor_of()` 顺着 `contains` 往上走**找得到** Debate。
        所以第一版那种造法其实**测的是有锚点的那条路**，断言直接空过了。
        这里用 `anchor=False` 造一条**不挂任何 Topic** 的命题。
        """
        claim = self._challenged_claim(anchor=False)
        self.assertIsNone(upper._anchor_of(self.conn, claim),
                          "这条命题居然有锚点 —— 用例没测到悬空那一路")

        cands = upper.propose_clusters(self.conn)
        self.assertTrue(cands["candidates"])
        upper.promote_candidates(self.conn, candidates=cands["candidates"],
                                 by="alice")
        kinds = {r[0] for r in self.conn.execute(
            "SELECT kind FROM event WHERE kind LIKE 'upper%'")}
        self.assertIn("upper_context_unanchored", kinds,
                      "悬空的上层节点没有留痕 —— 失败和成功长得一模一样")

    # --- ⑤ 空结果必须附一句话 --------------------------------------------
    def test_an_empty_scan_says_why_and_what_it_cannot_see(self):
        """**算不出 ≠ 零。**

        这条不许只回一个 `[]` 了事 —— 那和「底层真的没有信号」长得一样。
        """
        out = upper.scan(self.conn, self.debate)
        self.assertEqual(out["contexts"], [])
        self.assertIsNotNone(out["empty_reason"], "空结果没说为什么空")
        self.assertTrue(out["blind_spots"], "空结果没列盲区")
        self.assertIn("候选", out["note"])


class TestUpperLayerIsDormantOnAYoungCorpus(Base):
    """阶段本机制**应当**是休眠的 —— 这是一个**可证伪的预言**。

    文档白纸黑字说早期全程休眠。所以「跑出 0 条」不是失败，
    **是文档说的那个结果**。这条用例把它钉住：
    哪天它开始吐出候选，要么是真的攒够信号了，要么是判据被放宽了 ——
    两种都该被人看见。
    """

    def test_challenge_counts_stay_empty_when_nobody_challenges(self):
        conn = scaffold.connect(":memory:")
        scaffold.init(conn)
        d = scaffold.add_artifact(conn, type_="Debate",
                                  content={"question": "Q"}, origin="alice")
        scaffold.activate(conn, d, by="alice")
        # 8 段**互不重复**的原文：没有重复质询、没有重复修正
        for i in range(8):
            c = scaffold.add_artifact(
                conn, type_="Claim", content={"text": f"这是第 {i} 段不同的话。"},
                origin=f"u{i}")
            scaffold.activate(conn, c, by=f"u{i}")
        sig = upper.count_signals(conn)
        self.assertEqual(sig["challenge_counts"], {},
                         "没人质询过任何东西，却有挑战计数")


if __name__ == "__main__":
    unittest.main(verbosity=2)
