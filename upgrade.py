"""schema 迁移 —— 让「往库里加东西」这件事有路可走。

工程稿 §11.1 末尾点名过一个阻塞点：

> 本仓**没有迁移机制**（无 `schema_version` / 无 `ALTER TABLE` /
> 无 `PRAGMA user_version`）。H10 的「加一栏 `asserted_by`」与
> H8 的「扩词表」都撞在它上面。

这个模块就是把它补上。补上之后，「往 schema 里加一栏」从
**走不通**变成**有路，但要走一条留痕的路**。

--- 版本载体：`PRAGMA user_version`，不是一张表 -------------------------------

SQLite 原生带一个 4 字节整数头字段 `user_version`，本模块用它当版本号。

**为什么不建一张 `schema_version` 表**：那张表会出现在 `scaffold.SCHEMA` 的
建表清单里，而 B25 判据 4 要求 `views.LOWER_TABLES` **恰好等于**
`scaffold.SCHEMA` 的表集合 —— 加一张表就要动视图那套，纯属自找耦合。
版本号是**库自己的元数据**，不是讨论内容，用原生字段更贴切。

--- 0 表示什么 ---------------------------------------------------------------

    user_version = 0   迁移机制引入**之前**建的库（或一个刚 `CREATE` 的空库）
    user_version = 1   基线 —— `scaffold.SCHEMA` 定义的那个形状
    user_version = n   跑完了第 n 条迁移

⚠️ **0 与 1 的「形状」是同一个东西**，所以 `0 → 1` 是**标记**，不是迁移：
`scaffold.SCHEMA` 里全是 `CREATE TABLE IF NOT EXISTS`，现存库就是它建的。
把 0 认成「需要从零重建」会去动一个本来没坏的库。

这个区分**现在看不出差别**（两种处理结果相同），但它是将来把基线往上抬时的
前提 —— 到那时「0 的库」和「v1 的库」要走不同的路。

--- 只向前，不降级 -----------------------------------------------------------

`user_version` **大于**最新版本时，`to_latest()` **拒绝**并抛 `UpgradeError`。

一个「未来版本」的库被当成旧库改，会把新加的东西弄坏，而且**弄坏的样子很安静**：
库还能开、大部分查询还能跑，只有那一栏不见了。拒绝比猜安全。

本模块**没有 `downgrade()`**，也不打算有。理由同 `§C10`「只增不改」：
回退要靠 `supersede` 留痕，不靠把库改回去。

--- 迁移在一个事务里，所以不能用 `executescript` -------------------------------

⚠️ **这是本模块最容易写错的一处**：`sqlite3.Connection.executescript()`
会**先隐式 COMMIT 再执行**。用它跑迁移 = 每条语句各自落盘，
跑到一半失败就留下一个「前几条生效、后几条没生效」的库 —— 而
`user_version` 还没抬，下次跑会从同一条迁移的开头重来，
**把已经生效的那几条再做一遍**。那不是幂等，那是重复施加。

所以这里用 `conn.execute()` **逐句**跑，整个迁移（含抬版本号）在一个事务里：
要么全成，要么全不成。逐句拆分用 `sqlite3.complete_statement()`。

`PRAGMA user_version = n` 的写入**受事务保护**，会跟着一起回滚。

--- `PRAGMA` 不能参数绑定 ------------------------------------------------------

`conn.execute("PRAGMA user_version = ?", (n,))` 是**语法错误** ——
PRAGMA 的值只能是字面量。所以这里只能拼字符串，
于是 `_set_version()` 里那一次 `int()` 强制转换是**唯一**挡住注入的东西。
它不能省。

--- 迁移为什么写成 SQL 字符串，不写成函数 ---------------------------------------

清单里的每条迁移是 `(version, name, sql)`，SQL 是**字符串**。
这样 B27 能**静态**扫它：`DROP TABLE` / `DELETE FROM` 一眼就能拦。

写成 `apply(conn)` 函数的话，SQL 藏在函数体里，静态扫不到 ——
要拦只能等运行时（那时数据已经没了）。将来真需要 Python 逻辑的迁移，
得**显式**放宽 B27 的扫描范围，那是一次留痕的改动。

--- 拦不住什么 ---------------------------------------------------------------

静态拦不住「迁移的 SQL 写错了」—— 那要靠 `test_upgrade.py` 里的行为用例
（幂等、不丢数据、拒绝降级）。本模块也不判断一条迁移**该不该**做，
那是需求方的决定；它只管**做的时候别把库弄坏**。
"""

from __future__ import annotations

import re
import sqlite3
from typing import NamedTuple


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------

BASELINE_VERSION = 1
"""基线版本 —— `scaffold.SCHEMA` 定义的那个形状就是 v1。

⚠️ 这个数是**规范**，不是事实：它说「v1 就是基线，没有『v1 迁移』」。
B27 判据 2 钉住它 —— 有人想给 v1 写一条迁移，说明他把基线定义搞错了。
"""


class Upgrade(NamedTuple):
    """一条迁移。`sql` 是字符串（见模块 docstring：为了能被静态扫）。"""

    version: int
    name: str
    sql: str


UPGRADES: tuple[Upgrade, ...] = (
    Upgrade(
        version=2,
        name="relation 加 asserted_by —— 谁主张这条边",
        sql="""
-- 给边补一栏 asserted_by：**谁主张这条边**（PROV-O wasAttributedTo）。
--
-- 为什么加：节点那边 asserted_by（谁主张的）与 origin（谁产的）是分开的两栏，
-- 边这边只有 origin。于是「这条算数的边是谁认领的」只能去 event 表 join
-- candidate_promoted 才查得到 —— 而那是**历史事件**，不是边自己的属性。
--
-- 为什么可空：SQLite 的 ALTER TABLE ADD COLUMN 加 NOT NULL 列**必须带
-- DEFAULT**，而给 DEFAULT 就等于给「没人主张」留了个位置 —— 那正是这一栏
-- 要挡的。所以列上不设约束，「算数的边必须有主张者」由 add_relation 判。
-- 同 artifact.asserted_by 的做法（那一栏也是可空的）。
--
-- 列只能加在末尾：ALTER TABLE ADD COLUMN 没有位置参数，新列一定排在
-- created_at 之后，与 artifact 表里 asserted_by 排在中间不同。按列名取值
-- 不受影响，所以不为了对齐顺序去重建表（那要动 B27 的 DROP TABLE 禁令）。
--
-- 存量行留 NULL：它们确实没有这一栏的信息，回填就得编一个值。
ALTER TABLE relation ADD COLUMN asserted_by TEXT;
""",
    ),
)
"""有序迁移清单 —— **只许追加，不许改已发布的那几条**。

改已发布的迁移等于：同一个版本号在两台机器上做不同的事，
而 `user_version` 已经抬过去了，谁也看不出来。

⚠️ 第一条迁移（v2）加 `relation.asserted_by`，它原先被「本仓没有迁移机制」
挡住（工程稿 §11.5 的路 ①）。机制补上之后那条路通了 —— 但**基线 `SCHEMA`
一个字没改**，新栏只由这条迁移加。理由见 `scaffold.init()`：`SCHEMA` 建的是
**v1 形状**，若在 `SCHEMA` 里也加这一栏，新库会被迁移**再加一次**，
`ALTER TABLE` 报 duplicate column。

--- v2 参考了哪些成熟做法（**参考，不是照搬**）---------------------------------

「给已有的表加一栏」不是本仓发明的问题，业界有成熟解法。取用的是它们的
**判断**，不是它们的**步骤** —— 每一步都得在本仓的约束下重新过一遍：

| 成熟做法 | 它怎么说 | 本仓取什么 / 不取什么 |
|---|---|---|
| **expand-contract**（PlanetScale 等迁移手册的通用模式） | schema 变更拆成「先加（向后兼容）→ 迁数据 → 后删」 | **取前半**：加**可空**列，不动存量行。**不取后半**：不回填、不加 `NOT NULL` —— 存量行确实没有这个信息，回填就得**编一个值** |
| **SQLite 官方 `ALTER TABLE`** | 加 `NOT NULL` 列**必须**带一个非 NULL 的 `DEFAULT` | **取它的结论**：正因为「必须带 `DEFAULT`」，这里才**不加 `NOT NULL`** —— 那个 `DEFAULT` 就是「没人主张」的位置，而它正是这一栏要挡的东西 |
| **SQLite 官方 12 步改表流程** | 改列顺序 / 加删约束要「建新表 → 复制数据 → 删旧表 → 重命名新表」 | **不取**：它要 `DROP TABLE`，撞 B27（P6 可逆性：supersede 而非 delete）。所以新列只能排在末尾，不为了对齐顺序去重建表 |
| **RDF reification / RDF-star** | 「边上的属性」的经典答案：把边写成 statement，再给它挂属性 | **取它的诊断**（「边也能有自己的属性」），**不取它的形态**：那是 RDF 三元组的绕法；本仓是 property graph，边上直接加一栏就是它要的效果 |

⚠️ 一句话：成熟做法给的是「**这样加列是安全的**」这个判断，
不是可以直接抄的脚本 —— 它们的默认前提（存量可回填、可以删表重建）
在本仓**两条都不成立**。
"""


def _latest(upgrades: tuple[Upgrade, ...]) -> int:
    return max((BASELINE_VERSION, *(m.version for m in upgrades)))


LATEST_VERSION = _latest(UPGRADES)
"""最新版本。**算出来的**，不是手写的 —— 手写一个会跟清单漂开。"""


class UpgradeError(Exception):
    """迁移拒绝了一次升级。消息面向调用方，说明为什么。"""


# ---------------------------------------------------------------------------
# 版本号读写
# ---------------------------------------------------------------------------

def current_version(conn: sqlite3.Connection) -> int:
    """库现在的版本号。空库（从没建过）读出来是 0。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    """抬高版本号。

    ⚠️ PRAGMA 的值只能是字面量，绑不了参数，所以这里必须拼字符串 ——
    那个 `int()` 是唯一挡住注入的东西，不能省（见模块 docstring）。
    """
    conn.execute(f"PRAGMA user_version = {int(version)}")


# ---------------------------------------------------------------------------
# 逐句拆分（不能用 executescript，见模块 docstring）
# ---------------------------------------------------------------------------

_COMMENT = re.compile(r"--[^\n]*")


def _has_sql(statement: str) -> bool:
    """去掉 `--` 注释与空白之后，还剩东西吗。

    纯注释的片段直接 `execute()` 会报错，而它看起来像一句合法的 SQL。
    """
    return bool(_COMMENT.sub("", statement).strip())


def statements(sql: str):
    """把一段 SQL 拆成一条条语句。

    用 `sqlite3.complete_statement()` 判「够不够一句」——
    它认得字符串字面量里的分号，自己按 `;` 切会在 `DEFAULT ';'` 上切错。
    """
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if _has_sql(buf):
                yield buf.strip()
            buf = ""
    if _has_sql(buf):
        yield buf.strip()


# ---------------------------------------------------------------------------
# 读：要看什么（只读）
# ---------------------------------------------------------------------------

def validate(upgrades: tuple[Upgrade, ...] = UPGRADES) -> None:
    """清单形状不合法就抛 `UpgradeError`。**只读。**

    为什么运行时也要判一次（B27 已经静态判过源码里那一份了）：
    `upgrades=` 是个**公开参数**，调用方能传一份 B27 从没扫过的清单进来。
    静态检查扫的是源码里的那一份，管不到运行时传进来的。

    ⚠️ 最要紧的是**重复版本号**。它跑起来不是「跳过一条」，是**重复施加**：

        v2 第一条跑完，库里已经是 v2 的形状
        v2 第二条读的是「本轮的起始版本」，以为库还是 v1 → 同一件事再做一遍

    `ALTER TABLE ADD COLUMN` 重复施加会报错（列已存在），还算好的；
    换成 `UPDATE` 就是**把数据改了两遍**，而两边都不觉得自己错了。
    """
    versions = [m.version for m in upgrades]

    if len(set(versions)) != len(versions):
        dupes = sorted({v for v in versions if versions.count(v) > 1})
        raise UpgradeError(
            f"迁移清单里版本号重复：{dupes} —— 同一个版本号出现两次，"
            "跑起来会**重复施加**：第一条把库升到该版本，第二条以为库还在上一版，"
            "于是把同一件事又做了一遍。")

    if versions != sorted(versions):
        raise UpgradeError(f"迁移清单不是按版本号递增排的：{versions} —— "
                           "顺序即语义，读清单的人会以为它是另一种顺序。")

    want = list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions)))
    if sorted(versions) != want:
        missing = sorted(set(want) - set(versions))
        extra = sorted(set(versions) - set(want))
        raise UpgradeError(
            f"迁移清单的版本号不连续（缺 {missing}，多 {extra}）—— "
            "跳号最常见的原因是**删掉了一条已经发布过的迁移**："
            "那个版本号在别的机器上已经抬过去了，于是两台机器的 "
            "`user_version` 相同、形状不同，谁也看不出来。")


def pending(conn: sqlite3.Connection, *,
            upgrades: tuple[Upgrade, ...] = UPGRADES) -> tuple[Upgrade, ...]:
    """从当前版本到最新，还差哪几条。**只读。**"""
    validate(upgrades)
    version = current_version(conn)
    return tuple(sorted((m for m in upgrades if m.version > version),
                        key=lambda m: m.version))


def plan(conn: sqlite3.Connection, *,
         upgrades: tuple[Upgrade, ...] = UPGRADES) -> list[str]:
    """给人看的一行行说明。**只读** —— 调它不会动库。"""
    try:
        validate(upgrades)
    except UpgradeError as exc:
        # 只读的那个入口不该抛 —— 它的活是**说清**，不是拦。
        # 拦住是 `to_latest()` 的活。
        return [f"⚠️ 迁移清单不合法：{exc}"]
    version = current_version(conn)
    latest = _latest(upgrades)
    lines = [f"当前 v{version}，最新 v{latest}"]
    if version == 0:
        lines.append(f"v0 → v{BASELINE_VERSION}：标记基线（SCHEMA 的形状，无操作）")
    if version > latest:
        lines.append(f"⚠️ 库是 v{version}，比本代码的 v{latest} 还新 —— "
                     "升级会被拒绝（不许降级）")
        return lines
    todo = pending(conn, upgrades=upgrades)
    if not todo:
        lines.append("没有待跑的迁移")
        return lines
    for m in todo:
        lines.append(f"v{m.version}：{m.name}")
    return lines


# ---------------------------------------------------------------------------
# 写：升级（唯一会改库的地方）
# ---------------------------------------------------------------------------

def _apply(conn: sqlite3.Connection, migration: Upgrade) -> None:
    """跑一条迁移，连同抬版本号，**在一个事务里**。"""
    conn.execute("BEGIN")
    try:
        for statement in statements(migration.sql):
            conn.execute(statement)
        _set_version(conn, migration.version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def to_latest(conn: sqlite3.Connection, *,
              upgrades: tuple[Upgrade, ...] = UPGRADES) -> tuple[int, ...]:
    """把库升到最新版本，返回**实际跑过的**版本号。

    幂等：已经在最新时什么都不做（也不写版本号），返回空元组。
    """
    validate(upgrades)
    version = current_version(conn)
    latest = _latest(upgrades)
    if version > latest:
        raise UpgradeError(
            f"这个库是 v{version}，而这份代码只到 v{latest} —— 拒绝降级。"
            "「未来版本」的库被当成旧库改，会安静地弄坏新加的东西："
            "库还能开、大部分查询还能跑，只有那一栏不见了。")

    done: list[int] = []

    if version == 0:
        # 0 → 基线是**标记**，不是迁移：SCHEMA 全是 CREATE TABLE IF NOT EXISTS，
        # 现存库就是它建的，形状本来就是 v1（见模块 docstring）。
        _set_version(conn, BASELINE_VERSION)
        conn.commit()
        version = BASELINE_VERSION

    for migration in sorted(upgrades, key=lambda m: m.version):
        if migration.version <= version:
            continue
        _apply(conn, migration)
        # ⚠️ 这一行不能省。`_apply()` 改的是**库里**的版本号，不是这个局部变量 ——
        # 不更新它的话，`version` 会一直停在进入循环时的那个值，
        # 于是后面每条迁移都被判成「比当前版本新」，**全部重跑一遍**。
        version = migration.version
        done.append(migration.version)

    return tuple(done)
