"""schema 迁移的**行为**验证 —— 只向前，且不许把库弄坏。

静态那半在 `checks.py` 的 **B27**（版本号递增连续、只一处能写、
`init()` 真的调了 `to_latest()`、迁移里没有 `DROP` / `DELETE`）。
这里拦的是静态拦不住的那半：**跑起来之后库真的没坏吗** ——
迁移幂等吗、会不会丢数据、跑到一半失败留不留半成品。

--- 这个文件要证的八件事 -------------------------------------------------------

| 要证什么 | 靠哪几条 |
|---|---|
| ① 基线标记：0 → 1，且老库走同一条路 | `TestTheBaselineGetsStamped` |
| ② 幂等：跑第二遍什么都不做 | `TestItIsIdempotent` |
| ③ 升级链真的会跑（用注入的假清单） | `TestTheUpgradeChainReallyRuns` |
| ④ 拒绝降级，且拒绝时库没被动 | `TestItRefusesToDowngrade` |
| ⑤ 事务性：失败的迁移不留半成品 | `TestAFailedUpgradeLeavesNothingBehind` |
| ⑥ 迁移不丢数据 | `TestItDoesNotLoseData` |
| ⑦ 坏清单运行时也拒（不只靠静态） | `TestABrokenChecklistIsRefused` |
| ⑧ 只读的那几个真的只读 | `TestTheReadOnlyPartsDoNotWrite` |

外加 `TestStatementsSplitsCorrectly` —— `statements()` 是机制的地基，
它切错了上面七条全都在验一个假的东西。

⚠️ 本文件只依赖 `scaffold.py` / `upgrade.py` / `views.py` 与标准库 ——
和 `test_rules.py` / `test_candidates.py` / `test_views.py` / `test_stop.py`
一个规矩：**能用几十行读懂的依赖，才算真的独立。**

⚠️ 为什么敢用 `views.snapshot()` 判「库没动」：它覆盖**全部** canonical 表
（B25 判据 4 钉着那张表不许少一张）。拿它当「一个字都没改」的判据，
比在这里另写一份清单可靠 —— 另写一份就会漂。

    python test_upgrade.py
"""

from __future__ import annotations

import unittest

import upgrade
import scaffold
import views


# 注入用的假清单。**刻意不是 `upgrade.UPGRADES`** —— 真清单现在是空的
# （基线之后还没有一条需要走的迁移），拿它测等于什么都没测。
#
# 用 `ALTER TABLE ... ADD COLUMN` 是因为它**只加不删**，
# 正好是 P6 可逆性允许的那一类，也正好是 H10 被否掉的那条要做的事。
_FAKE = (
    upgrade.Upgrade(2, "加一栏 probe2",
                      "ALTER TABLE artifact ADD COLUMN probe2 TEXT;"),
    upgrade.Upgrade(3, "再加一栏 probe3",
                      "ALTER TABLE artifact ADD COLUMN probe3 TEXT;"),
)


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _row_counts(conn) -> dict:
    return {table: len(rows) for table, rows in views.snapshot(conn).items()}


class _Base(unittest.TestCase):

    def setUp(self):
        self.conn = scaffold.connect()
        scaffold.init(self.conn)

    def tearDown(self):
        self.conn.close()

    # --- 造数据 -----------------------------------------------------------

    def _mk(self, n: int = 1) -> list:
        """建 n 个已确认的节点，返回 id 列表。"""
        out = []
        for i in range(n):
            aid = scaffold.add_artifact(
                self.conn, type_="Claim", content={"text": f"第 {i} 条"},
                origin="alice", intake="direct")
            scaffold.activate(self.conn, aid, by="alice")
            out.append(aid)
        return out

    def _ahead(self, version: int = 5):
        """造一个「未来版本」的库 —— 只能直接写 PRAGMA，
        因为 `upgrade` 没有、也不该有「把库标成任意版本」的公开入口。"""
        self.conn.execute(f"PRAGMA user_version = {int(version)}")
        self.conn.commit()


class TestTheBaselineGetsStamped(_Base):
    """0 → 1 是**标记**，不是迁移。老库和新库走同一条路。"""

    def test_a_fresh_library_is_stamped_by_init(self):
        """`init()` 之后版本必须已经是最新 —— 这是 B27 判据 5 的运行时对面。"""
        self.assertEqual(upgrade.current_version(self.conn), upgrade.LATEST_VERSION)

    def test_a_pre_migration_library_reads_as_zero(self):
        """迁移机制引入之前建的库：只有表，没有版本号。"""
        old = scaffold.connect()
        old.executescript(scaffold.SCHEMA)
        old.commit()
        try:
            self.assertEqual(upgrade.current_version(old), 0)
            upgrade.to_latest(old)
            self.assertEqual(upgrade.current_version(old), upgrade.BASELINE_VERSION)
        finally:
            old.close()

    def test_the_old_library_keeps_its_tables(self):
        """把 0 认成「需要从零重建」会去动一个本来没坏的库 —— 表必须还在。"""
        old = scaffold.connect()
        old.executescript(scaffold.SCHEMA)
        old.commit()
        try:
            before = _row_counts(old)
            upgrade.to_latest(old)
            self.assertEqual(_row_counts(old), before)
        finally:
            old.close()

    def test_an_empty_connection_reads_as_zero(self):
        """连表都没有的连接，版本号是 0（不是「1」也不是报错）。"""
        empty = scaffold.connect()
        try:
            self.assertEqual(upgrade.current_version(empty), 0)
        finally:
            empty.close()


class TestItIsIdempotent(_Base):
    """跑第二遍什么都不做 —— 幂等不是「结果一样」，是**压根不写**。"""

    def test_the_second_run_reports_nothing_done(self):
        self.assertEqual(upgrade.to_latest(self.conn), ())

    def test_the_second_run_does_not_touch_the_library(self):
        before = views.snapshot(self.conn)
        upgrade.to_latest(self.conn)
        self.assertEqual(views.snapshot(self.conn), before)

    def test_a_fully_upgraded_library_has_nothing_pending(self):
        """跑完假清单之后，`pending()` 必须是空的 —— 否则下次还会重跑。"""
        upgrade.to_latest(self.conn, upgrades=_FAKE)
        self.assertEqual(upgrade.pending(self.conn, upgrades=_FAKE), ())


class TestTheUpgradeChainReallyRuns(_Base):
    """用注入的假清单验升级路径 —— 手法同 `test_checks` 的探针。"""

    def test_it_runs_every_migration_in_order(self):
        self.assertEqual(upgrade.to_latest(self.conn, upgrades=_FAKE), (2, 3))

    def test_the_columns_really_appear(self):
        """版本号抬上去了，**形状也得真的变** —— 只看版本号是在验一个数字。"""
        upgrade.to_latest(self.conn, upgrades=_FAKE)
        self.assertTrue({"probe2", "probe3"} <= _columns(self.conn, "artifact"))

    def test_it_resumes_from_the_current_version(self):
        """已经到 v2 的库，只跑 v3 —— 不是从头再来一遍。"""
        upgrade.to_latest(self.conn, upgrades=_FAKE[:1])
        self.assertEqual(upgrade.current_version(self.conn), 2)
        self.assertEqual(upgrade.to_latest(self.conn, upgrades=_FAKE), (3,))

    def test_a_partially_upgraded_library_only_sees_the_rest(self):
        upgrade.to_latest(self.conn, upgrades=_FAKE[:1])
        self.assertEqual([m.version for m in
                          upgrade.pending(self.conn, upgrades=_FAKE)], [3])


class TestItRefusesToDowngrade(_Base):
    """「未来版本」的库被当成旧库改，会安静地弄坏新加的东西。"""

    def test_it_raises_on_a_newer_library(self):
        self._ahead(5)
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.to_latest(self.conn)

    def test_the_refusal_leaves_the_library_alone(self):
        """★ 拒绝之后**什么都不许做** —— 拒了但顺手改了一半是最坏的。"""
        self._ahead(5)
        before = views.snapshot(self.conn)
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.to_latest(self.conn, upgrades=_FAKE)
        self.assertEqual(views.snapshot(self.conn), before)
        self.assertEqual(upgrade.current_version(self.conn), 5)

    def test_the_plan_says_why_it_will_refuse(self):
        """`plan()` 是只读的，但它得**说得出**为什么升不上去。"""
        self._ahead(5)
        said = "\n".join(upgrade.plan(self.conn))
        self.assertIn("拒绝", said)

    def test_a_library_at_the_latest_version_is_not_a_downgrade(self):
        """等于最新版本不算降级（边界：`>` 不是 `>=`）。"""
        upgrade.to_latest(self.conn)
        self.assertEqual(upgrade.current_version(self.conn), upgrade.LATEST_VERSION)
        self.assertEqual(upgrade.to_latest(self.conn), ())


class TestAFailedUpgradeLeavesNothingBehind(_Base):
    """★ 这个文件最要紧的一组。

    `executescript()` 会**先隐式 COMMIT 再执行**，用它跑迁移 =
    每条语句各自落盘，跑到一半失败就留下「前几条生效、后几条没生效」的库，
    而 `user_version` 还没抬 —— 下次跑会从同一条的开头重来，
    **把已经生效的那几条再做一遍**。那不是幂等，那是重复施加。
    """

    _BROKEN = (
        upgrade.Upgrade(2, "前半句好，后半句坏",
                          "ALTER TABLE artifact ADD COLUMN probe_ok TEXT;\n"
                          "ALTER TABLE 没有这张表 ADD COLUMN x TEXT;"),
    )

    def test_it_raises(self):
        with self.assertRaises(Exception):
            upgrade.to_latest(self.conn, upgrades=self._BROKEN)

    def test_the_first_statement_is_rolled_back(self):
        """前半句加的那一栏**不许留下**。"""
        try:
            upgrade.to_latest(self.conn, upgrades=self._BROKEN)
        except Exception:
            pass
        self.assertNotIn("probe_ok", _columns(self.conn, "artifact"))

    def test_the_version_is_not_raised(self):
        """版本号没抬 —— 所以下次会从同一条重来，而库还是干净的原样。"""
        try:
            upgrade.to_latest(self.conn, upgrades=self._BROKEN)
        except Exception:
            pass
        self.assertEqual(upgrade.current_version(self.conn),
                         upgrade.BASELINE_VERSION)

    def test_the_library_is_untouched(self):
        before = views.snapshot(self.conn)
        try:
            upgrade.to_latest(self.conn, upgrades=self._BROKEN)
        except Exception:
            pass
        self.assertEqual(views.snapshot(self.conn), before)


class TestItDoesNotLoseData(_Base):
    """P6 可逆性：迁移只加不删。B27 判据 6 静态钉着 SQL，
    这里验的是**跑完之后行还在**。"""

    def test_the_rows_survive(self):
        self._mk(3)
        before = _row_counts(self.conn)
        upgrade.to_latest(self.conn, upgrades=_FAKE)
        self.assertEqual(_row_counts(self.conn), before)

    def test_the_content_survives(self):
        """行数没变还不够 —— 内容也得还是那几条。"""
        ids = self._mk(3)
        upgrade.to_latest(self.conn, upgrades=_FAKE)
        got = {r["id"] for r in self.conn.execute("SELECT id FROM artifact")}
        self.assertTrue(set(ids) <= got)

    def test_a_library_with_data_still_upgrades(self):
        """有数据的库也要能升 —— 空库能升不代表这个。"""
        self._mk(2)
        self.assertEqual(upgrade.to_latest(self.conn, upgrades=_FAKE), (2, 3))


class TestABrokenChecklistIsRefused(_Base):
    """坏清单**运行时也拒**，不只是靠 B27 静态拦。

    为什么两处都要：B27 扫的是**源码里那一份**清单，而 `upgrades=`
    是个公开参数 —— 调用方能传一份 B27 从没扫过的清单进来。

    ⚠️ 重复版本号是这里最要紧的一条，因为它**看起来无害**：
    「同一个版本号写了两条」像是笔误，实际跑起来是**重复施加** ——
    第二条读的是「本轮的起始版本」，以为库还在上一版，于是同一件事再做一遍。
    """

    _DUPE = (
        upgrade.Upgrade(2, "第一条 v2", "ALTER TABLE artifact ADD COLUMN a2 TEXT;"),
        upgrade.Upgrade(2, "第二条 v2", "ALTER TABLE artifact ADD COLUMN b2 TEXT;"),
    )
    _GAP = (
        upgrade.Upgrade(2, "v2", "ALTER TABLE artifact ADD COLUMN a2 TEXT;"),
        upgrade.Upgrade(4, "v4（跳过了 v3）",
                          "ALTER TABLE artifact ADD COLUMN b2 TEXT;"),
    )
    _UNSORTED = (
        upgrade.Upgrade(3, "v3", "ALTER TABLE artifact ADD COLUMN a2 TEXT;"),
        upgrade.Upgrade(2, "v2", "ALTER TABLE artifact ADD COLUMN b2 TEXT;"),
    )

    def test_a_duplicate_version_is_refused(self):
        with self.assertRaises(upgrade.UpgradeError) as ctx:
            upgrade.to_latest(self.conn, upgrades=self._DUPE)
        self.assertIn("重复", str(ctx.exception))

    def test_a_gap_is_refused(self):
        with self.assertRaises(upgrade.UpgradeError) as ctx:
            upgrade.to_latest(self.conn, upgrades=self._GAP)
        self.assertIn("不连续", str(ctx.exception))

    def test_an_unsorted_checklist_is_refused(self):
        with self.assertRaises(upgrade.UpgradeError) as ctx:
            upgrade.to_latest(self.conn, upgrades=self._UNSORTED)
        self.assertIn("递增", str(ctx.exception))

    def test_the_refusal_leaves_the_library_alone(self):
        """★ 清单坏了是**拒绝**，不是「跑到哪算哪」。"""
        before = views.snapshot(self.conn)
        for broken in (self._DUPE, self._GAP, self._UNSORTED):
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.to_latest(self.conn, upgrades=broken)
        self.assertEqual(views.snapshot(self.conn), before)
        self.assertEqual(upgrade.current_version(self.conn),
                         upgrade.BASELINE_VERSION)

    def test_pending_refuses_a_broken_checklist_too(self):
        """`pending()` 返回的是「要跑什么」—— 清单坏了，那个答案没有意义。"""
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.pending(self.conn, upgrades=self._DUPE)

    def test_plan_reports_instead_of_raising(self):
        """★ `plan()` 只读，它的活是**说清**，不是拦 —— 所以它报，不抛。"""
        said = "\n".join(upgrade.plan(self.conn, upgrades=self._DUPE))
        self.assertIn("不合法", said)


class TestTheReadOnlyPartsDoNotWrite(_Base):
    """`plan()` / `pending()` 是给人看的，必须只读。"""

    def test_plan_does_not_touch_the_library(self):
        before = views.snapshot(self.conn)
        upgrade.plan(self.conn)
        self.assertEqual(views.snapshot(self.conn), before)

    def test_pending_does_not_touch_the_library(self):
        before = views.snapshot(self.conn)
        upgrade.pending(self.conn, upgrades=_FAKE)
        self.assertEqual(views.snapshot(self.conn), before)

    def test_plan_does_not_stamp_an_unstamped_library(self):
        """★ 一个 v0 的库，光看 plan 不该被标记成 v1 —— 看不是做。"""
        old = scaffold.connect()
        old.executescript(scaffold.SCHEMA)
        old.commit()
        try:
            upgrade.plan(old)
            self.assertEqual(upgrade.current_version(old), 0)
        finally:
            old.close()

    def test_plan_mentions_the_baseline_stamp(self):
        old = scaffold.connect()
        old.executescript(scaffold.SCHEMA)
        old.commit()
        try:
            self.assertIn("基线", "\n".join(upgrade.plan(old)))
        finally:
            old.close()


class TestStatementsSplitsCorrectly(unittest.TestCase):
    """`statements()` 是这套机制的地基 —— 它切错了，上面全在验假的东西。

    为什么不用 `executescript()`：它会先隐式 COMMIT（见 `upgrade.py` 模块
    docstring）。所以逐句拆分这一步不能省，也就不能不管它对不对。
    """

    def test_it_splits_on_statement_ends(self):
        got = list(upgrade.statements("CREATE TABLE t (x TEXT);\nCREATE INDEX i ON t(x);"))
        self.assertEqual(len(got), 2)

    def test_a_semicolon_inside_a_string_is_not_a_split(self):
        """★ `DEFAULT ';'` 里的分号不是语句结束 ——
        按 `;` 硬切会在这里切错，切出来的两半都跑不了。"""
        got = list(upgrade.statements("CREATE TABLE t (x TEXT DEFAULT ';');"))
        self.assertEqual(len(got), 1)
        self.assertIn("';'", got[0])

    def test_a_trailing_statement_without_a_semicolon_survives(self):
        """最后一句没写分号也得跑 —— 悄悄丢掉它是最坏的失败方式。"""
        got = list(upgrade.statements("ALTER TABLE artifact ADD COLUMN probe TEXT"))
        self.assertEqual(len(got), 1)

    def test_pure_comments_are_skipped(self):
        """纯注释的片段 `execute()` 会报错，而它看起来像一句合法的 SQL。"""
        self.assertEqual(list(upgrade.statements("-- 只有注释\n")), [])

    def test_a_comment_before_a_statement_is_kept_with_it(self):
        got = list(upgrade.statements("-- 说明\nCREATE TABLE t (x TEXT);"))
        self.assertEqual(len(got), 1)

    def test_an_empty_string_yields_nothing(self):
        self.assertEqual(list(upgrade.statements("")), [])


if __name__ == "__main__":
    unittest.main()
