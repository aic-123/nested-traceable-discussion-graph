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


UPGRADES: tuple[Upgrade, ...] = ()
"""有序迁移清单 —— **只许追加，不许改已发布的那几条**。

改已发布的迁移等于：同一个版本号在两台机器上做不同的事，
而 `user_version` 已经抬过去了，谁也看不出来。

⚠️ 现在**是空的**，这不是没写完 —— 是「基线之后还没有一条需要走的迁移」。
被迁移机制挡住的那条（H10 给 `relation` 加 `asserted_by`）是**需求方的决定**，
不是本模块的。机制先立起来，第一条真迁移由那个决定带出来。
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
