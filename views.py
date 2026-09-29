"""`§C7.1` ④ 局部展开 —— Materialized View。**纯读缓存，永不回写写模型。**

阶段 6。照抄 CQRS 的 read model：**投影只从写模型读，绝不写回去。**

--- 三条出口判据，落成什么 -----------------------------------------------------

| 判据 | 落地形态 |
|---|---|
| ① 视图可**整批重建** | `rebuild()` / `rebuild_all()` **先删再重算**，不看旧内容；返回 `changed` 说明结果是否与上一版相同 |
| ② 重建前后 lower **逐字段一致** | `rebuild()` **每次调用**都比对 `snapshot()` 前后两份 —— 不是只在测试里比 |
| ③ 删掉全部视图，lower **一字不少** | `clear()` 只碰 `VIEW_TABLE`；`snapshot()` 覆盖**全部** canonical 表（B25 钉着这个集合不许少一张） |

② 落在**运行时**而不是只落在测试里，是本模块与 `upper.py` 那条行为用例的差别：
`upper.py` 的「promote 不动底层」只能靠 `test_upper.py` 验 —— 它**有能力**写底层，
只是不该写。本模块**没有那个能力**（`VIEW_TABLE` 之外一句写语句都没有，B25 静态钉着），
所以可以让**每次重建都自证一次**。

--- 视图**不复制底层对象** -----------------------------------------------------

视图里存的是 `item_id` —— **指向 lower 的 id**，不是 `artifact` 的正文。

理由同 `pointer.py`：**副本无法证明自己等于原文**。视图里存一份 `text`，
底层改了它不会跟着变，而读者看不出来。存 id 就不会有这个状态：
id 要么指得到、要么指不到，**没有「指得到但内容旧了」这一档**。

这一条由 B25 判据 3 钉住：`VIEW_SCHEMA` 的列名必须落在白名单里 ——
**多一列 `text` / `content` / `body`，就是把底层对象复制进来了**，
而那张表看起来还是张「视图表」。

--- ⚠️ 判据 ② 的一个直接后果：视图**不写 event** ------------------------------

`event` 是 canonical 表之一。所以「重建时记一条 `view_rebuilt`」这条路是**关着的** ——
记了之后前后快照必然不等，② 当场不成立。

这不是省事，是**判据本身推出来的**：视图是缓存。缓存不留审计轨迹 ——
留了它就不是纯读缓存了，而「删掉零损失」也就不成立（删视图会连那些事件一起删掉）。

--- 它在哪张表里 ---------------------------------------------------------------

`materialized_view` 由**本模块**建，**不在** `scaffold.SCHEMA` 里。

工程稿 §七：存储轴（Canonical / Materialized）与写权限轴（Lower / Upper）
是**两条正交的轴**。把视图表放进 `scaffold.SCHEMA` 会让它看起来像 canonical 的一部分 ——
而 canonical 的意思是「删了就没了」，视图的意思是「删了零损失」。
**两句话都成立的东西不存在**，所以两张表必须分开建。B25 判据 2 钉住这一点。

--- 投影口径是**可替换**的 -----------------------------------------------------

`_project()` 是本模块自己定的**最小**投影：按 id 排序 + 被质询过的默认展开。
它**不是**出口判据的一部分 —— 换一个投影，三条判据照旧成立。

这是刻意的：判据钉的是「**视图不许回写**」，不是「视图长什么样」。
把口径写死成判据，就会在有人想改展示方式时误报。
"""

from __future__ import annotations

import sqlite3

import scaffold

VIEWS_VERSION = "materialized-view/1"

# 视图自己的表。**只有这一张** —— 见 `clear()` 与 B25 判据 1。
VIEW_TABLE = "materialized_view"

# 视图表**允许有的列**（字段白名单，手法同 `pointer.SELECTOR_FIELDS`）。
#
# ⚠️ 白名单之外一列都不许加。多一列 `text` / `content` / `body`，
# 就是把底层对象**复制**进来了 —— 而那张表看起来还是张「视图表」。
# 白名单是判据，不是文档：B25 判据 3 拿它比对 `VIEW_SCHEMA`。
VIEW_FIELDS = ("id", "view", "position", "item_id", "expanded",
               "built_at", "version")

# canonical 表 —— 出口判据 ②③ 要比对的就是这几张，**一张都不许少**。
#
# ⚠️ 少一张，那张表被视图改了也看不出来。B25 判据 4 拿它比对
# `scaffold.SCHEMA` 里的建表清单 —— 将来 `scaffold` 加了新表而这里没跟上，
# 检查会响，而不是静默漏掉一张。
LOWER_TABLES = ("artifact", "revision", "relation", "event", "seq", "staging")

# 默认展开的判据用的边。**只用结构量，不碰热度。**
#
# `§C7.1` ④ 允许上层影响的只有「展示顺序 / 默认展开 / 推荐候选」三件事，
# **没有一件允许读热度**（那是 B4 盯的那条线，不变量 #5）。
# 所以这里的判据是「这条被质询过」—— `challenged_by` 是白名单里的结构量。
EXPAND_KIND = "challenged_by"

VIEW_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {VIEW_TABLE} (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    view     TEXT NOT NULL,
    position INTEGER NOT NULL,
    item_id  TEXT NOT NULL,
    expanded INTEGER NOT NULL,
    built_at TEXT NOT NULL,
    version  TEXT NOT NULL,
    UNIQUE (view, position),
    UNIQUE (view, item_id)
);
"""


class ViewError(scaffold.ScaffoldError):
    """视图层的拒绝。**继承 `ScaffoldError`**，调用方只 catch 一个就行。"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建视图自己的表。**幂等** —— 视图是可以随时删光再建的东西。"""
    conn.executescript(VIEW_SCHEMA)
    conn.commit()


def view_name(debate_id: str) -> str:
    """一个讨论对应一个视图。名字带上来源 id，免得两个讨论的视图混在一起。"""
    return f"debate:{debate_id}"


def snapshot(conn: sqlite3.Connection) -> dict:
    """底层**逐字段**快照：表名 → 行（按 rowid 排序）。

    为什么要逐字段而不是只数行数：`UPDATE artifact SET state='x'` **不改行数**，
    却改了底层。数行数看不见它 —— 而那是单向性最典型的破坏方式
    （顺手把上层的判断写进底层某个字段）。

    前提：`scaffold.init()` 已经跑过。
    """
    out = {}
    for table in LOWER_TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        out[table] = [tuple(r) for r in rows]
    return out


def clear(conn: sqlite3.Connection) -> int:
    """删光**全部**视图。返回删了几行。

    ⚠️ 只碰 `VIEW_TABLE`。这就是出口判据 ③「删掉全部视图，lower 一字不少」
    的可执行形式 —— 它**没有别的表可碰**。
    """
    ensure_schema(conn)
    cur = conn.execute(f"DELETE FROM {VIEW_TABLE}")
    conn.commit()
    return cur.rowcount


def rows(conn: sqlite3.Connection, *, view: str | None = None) -> list[dict]:
    """把视图行读回来。`view=None` 读全部。**只读**（除建表外不写任何东西）。"""
    ensure_schema(conn)
    if view is None:
        cur = conn.execute(
            f"SELECT * FROM {VIEW_TABLE} ORDER BY view, position")
    else:
        cur = conn.execute(
            f"SELECT * FROM {VIEW_TABLE} WHERE view = ? ORDER BY position",
            (view,))
    return [dict(r) for r in cur.fetchall()]


def _project(conn: sqlite3.Connection, debate_id: str) -> list[dict]:
    """把一个讨论展开成**扁平的可见列表**。只带 id 与位置，**不带正文**。

    口径（本模块自己定的最小投影，**不是出口判据的一部分**）：

    * 成员 = 从 `debate_id` 出发、沿 `contains` 边**可达**的、状态 `active` 的节点
      （`contains` 是多层的：Debate → Topic → Claim，所以要递归）；
    * 顺序 = 按 id（**确定性** —— 同一份底层必须重建出同一份视图，
      否则「重建」和「重算」就是两件事了）；
    * 默认展开 = 该节点是**至少一条 active `challenged_by` 边的起点**。

    ⚠️ 方向不要搞反：`challenged_by` 是 `Claim → Counterargument`（`§C4`），
    所以**被质询的是 `from_id`**。按 `to_id` 数的话，被标记展开的会是那些
    反驳本身 —— 而那是一条读起来完全说得通、只是标错了对象的实现。
    实测第一版就是这么写的。

    ⚠️ 递归用 `UNION` 不用 `UNION ALL`：`UNION` 去重，边上万一有环也不会转不出来。
    """
    members = conn.execute(
        "WITH RECURSIVE members(id) AS ("
        "  SELECT to_id FROM relation"
        "   WHERE from_id = ? AND kind = 'contains' AND state = 'active'"
        "  UNION"
        "  SELECT r.to_id FROM relation r JOIN members m ON r.from_id = m.id"
        "   WHERE r.kind = 'contains' AND r.state = 'active'"
        ") SELECT a.id AS id FROM members JOIN artifact a ON a.id = members.id"
        "  WHERE a.state = 'active' ORDER BY a.id",
        (debate_id,),
    ).fetchall()

    out = []
    for position, row in enumerate(members):
        contested = conn.execute(
            "SELECT COUNT(*) FROM relation"
            " WHERE kind = ? AND from_id = ? AND state = 'active'",
            (EXPAND_KIND, row["id"]),
        ).fetchone()[0]
        out.append({
            "item_id": row["id"],
            "position": position,
            "expanded": 1 if contested else 0,
        })
    return out


def _lower_must_not_move(conn: sqlite3.Connection, before: dict) -> None:
    """出口判据 ②：重建前后 lower **逐字段一致**。不一致就抛。

    ⚠️ 它**理论上不可达** —— 本模块除了 `VIEW_TABLE` 之外一句写语句都没有
    （B25 判据 1 静态钉着）。让它可执行，是为了让「不可达」这件事
    **每次重建都自证一次**，而不是靠读代码相信。

    报错时**指出是哪张表动了**：只说「动了底层」的话，读的人还得自己找。
    """
    after = snapshot(conn)
    if after == before:
        return
    moved = sorted(t for t in before if before[t] != after[t])
    raise ViewError(
        f"重建动了底层：{moved} —— 视图是**纯读缓存**（CQRS read model），"
        "投影永不回写写模型。这不是「不该」，是**不能**："
        "本模块除了视图表之外没有第二条写路径。"
    )


def _insert(conn: sqlite3.Connection, name: str, items: list[dict],
            built_at: str) -> None:
    """把一份投影写进视图表。**唯一**的写路径。"""
    conn.executemany(
        f"INSERT INTO {VIEW_TABLE}"
        " (view, position, item_id, expanded, built_at, version)"
        " VALUES (?,?,?,?,?,?)",
        [(name, it["position"], it["item_id"], it["expanded"],
          built_at, VIEWS_VERSION) for it in items],
    )


def _key(item: dict) -> tuple:
    """判「重建后有没有变」用的投影指纹。

    ⚠️ **不含 `built_at`** —— 它每次都不同，含进去的话 `changed` 永远是 `True`，
    那个字段就没有信息了。指纹要的正是「内容有没有变」。
    """
    return (item["position"], item["item_id"], item["expanded"])


def rebuild(conn: sqlite3.Connection, *, debate_id: str) -> dict:
    """**整批重建**这个讨论的视图 —— 先删光该视图的全部行，再按底层重算。

    ⚠️ **不看旧的视图内容。** 这就是「重建」与「增量补丁」的分界：
    增量补丁需要一份「上次算到哪」的状态，而那份状态本身会过期 ——
    **过期的缓存看起来和新鲜的完全一样**，这是缓存最坏的失败方式。

    返回值的 `changed` 说明结果与上一版是否相同。跑两遍，第二遍应当 `False`。
    """
    ensure_schema(conn)
    scaffold.get(conn, debate_id)          # 不存在的讨论直接抛，不静默建空视图

    name = view_name(debate_id)
    before = snapshot(conn)
    old = {_key(r) for r in rows(conn, view=name)}

    conn.execute(f"DELETE FROM {VIEW_TABLE} WHERE view = ?", (name,))
    items = _project(conn, debate_id)
    _insert(conn, name, items, scaffold.now())
    conn.commit()

    after_rows = rows(conn, view=name)
    _lower_must_not_move(conn, before)

    return {
        "view": name,
        "items": len(items),
        "expanded": [r["item_id"] for r in after_rows if r["expanded"]],
        "changed": old != {_key(r) for r in after_rows},
        "lower_unchanged": True,
    }


def rebuild_all(conn: sqlite3.Connection) -> dict:
    """**整批重建全部视图** —— 先把视图表清空，再按底层把所有讨论算一遍。

    与 `rebuild()` 一样**不看旧内容**，区别只是范围。这条是出口判据 ①
    里「整批」那个词最直接的形式：视图层的正确性**不依赖任何存量状态** ——
    把表删干净，它自己就长回来了。
    """
    ensure_schema(conn)
    before = snapshot(conn)
    clear(conn)

    debates = [r["id"] for r in conn.execute(
        "SELECT id FROM artifact WHERE type = 'Debate' AND state = 'active'"
        " ORDER BY id")]
    built_at = scaffold.now()
    total = 0
    for debate_id in debates:
        items = _project(conn, debate_id)
        _insert(conn, view_name(debate_id), items, built_at)
        total += len(items)
    conn.commit()

    _lower_must_not_move(conn, before)
    return {"views": len(debates), "items": total, "lower_unchanged": True}


def render(conn: sqlite3.Connection, debate_id: str) -> str:
    """印成人看的。只读 —— 和 `upper.render()` / `candidates.render()` 一个规矩。"""
    name = view_name(debate_id)
    items = rows(conn, view=name)
    lines = [
        "=" * 64,
        f"物化视图 {name}",
        f"  投影 {VIEWS_VERSION}（`§C7.1` ④：局部展开，纯读缓存）",
        "",
    ]
    if not items:
        lines += [
            "  这个视图是空的。",
            "",
            "  ⚠️ 「还没建过」和「建出来是空的」不是一回事，所以两句都印：",
            "     · 还没建过 —— `rebuild()` 一次都没跑",
            "     · 建出来是空的 —— 跑了，但这个讨论底下没有 active 的成员",
            "",
        ]
    for it in items:
        flag = "展开" if it["expanded"] else "收起"
        lines.append(f"  {it['position']:>3}  {it['item_id']}  [{flag}]")
    lines += [
        "",
        "⚠️ 视图里**只有 id 与位置**，没有正文 —— 存正文就等于存了一份副本，",
        "   而副本无法证明自己等于原文（`pointer.py` 那条理由，同一条）。",
        "   删掉整个视图**零损失**：`rebuild()` 从底层重算一遍就回来了。",
    ]
    return "\n".join(lines)


__all__ = [
    "VIEWS_VERSION", "VIEW_TABLE", "VIEW_FIELDS", "LOWER_TABLES", "EXPAND_KIND",
    "ViewError",
    "ensure_schema", "view_name", "snapshot", "clear", "rows",
    "rebuild", "rebuild_all", "render",
]
