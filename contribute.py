"""`§C5` 社区贡献入口 —— 七种粒度，每种一次调用。

设计稿第 9 节把「一个人能往讨论里放什么」拆成七种粒度。这一层是那七种的落地：
**把「我想说的事」翻译成原语调用**，调用方不需要知道节点类型与关系种类的名字。

    contribute.claim(conn, text="……", by="alice")
    contribute.evidence(conn, text="……", target="claim-0001", by="alice")
    contribute.challenge(conn, text="……", target="claim-0001", by="alice")
    contribute.counterexample(conn, text="……", target="claim-0001", by="alice")
    contribute.revision(conn, of="claim-0001", text="……", by="alice")
    contribute.connection(conn, left="claim-0001", right="claim-0002", by="alice")
    contribute.context(conn, text="……", by="alice")

--- 「不需要理解 graph 结构」的可执行形式 ----------------------------------------

阶段 4 的出口判据 ③ 是「首次贡献者不需要理解 graph 结构」。一句要求，三种落法：

| 落法 | 可执行吗 |
|---|---|
| 文档里写「入口会替你选」 | ✗ 那只是一句承诺，没有任何东西盯着它 |
| 入口收 `kind=` / `type_=`，给个好默认值 | ✗ 默认值可以改，调用方照样能传别的进去 |
| ★ 词表**只出现在 `CONTRIBUTIONS` 里**，七个入口签名里一个词表参数都没有 | ✓ |

`CONTRIBUTIONS` 是那张唯一的映射表：粒度 → (节点类型, 关系种类, 方向, 落点状态)。
七个入口**只读它**，签名里没有 `kind` / `type_` / `state` 这些参数 ——
所以「调用方自己挑一个关系种类」这件事**没有地方可以发生**。
B23 盯着这张表与七个签名的形状（静态那半），`test_contribute.py` 盯着行为那半。

--- 确认分档（`§C2.5`）----------------------------------------------------------

七种不是一档。按 `§C2.5` 的四档表分：

| 粒度 | 新增了什么 | 落点 | 依据 |
|---|---|---|---|
| `claim` / `context` | **新断言**（可被独立引用的对象） | `proposed`，**等确认** | 第 2 档，原文点名的就是 Topic / Claim |
| `evidence` / `challenge` / `counterexample` / `connection` | 对已有内容的**标注与关系** | `active`，默认生效 + 可推翻 | 第 3 档 |
| `revision` | 不新增断言，只加一个版本 | 版本表加一行，状态不动 | `§C10` |

⚠️ 落 `proposed` 的两个**不是**「没建出来」—— 它们已经存在、有 id、可被引用，
只是还没被确认。`scaffold.activate()` 是唯一的确认入口，`pending()` 列得出来。

--- 七种里有两个是新的 ----------------------------------------------------------

上游 `arena/debate.py` 已经有 Claim / Evidence / Challenge 的入口。本模块与它**同形**
（同样的节点类型、同样的边、同样的方向），另外补上它没有的两条：

| 粒度 | arena 有没有 | 说明 |
|---|---|---|
| `counterexample` | ✗ | `§C4` 的树上有 `Counterexample`，但关系映射表**没给它边**。这里补 `contradicts`（照 `§C6.3` 的方向：证据 → Claim） |
| `connection` | ✗ | 连两条已有观点。落成 `related_to` 边 —— 边在 `relation` 表里有自己的 `id` / `origin` / `state`，**本身就是 S-node**（`§C3.2`），不是外键字段 |

--- `context` 为什么不是上层 `Context`（需求方 2026-09-28 拍板）-------------------

七种里的 `context` 与 `§C7.1` 的上层 `Context` 节点**不是同一个东西**：

| | 上层 `Context` | 本模块的 `context` |
|---|---|---|
| 谁建 | 只许 `upper.py`（`CONTROL_RULES` 的 `structural` 档） | 社区成员 |
| 建的是什么 | 一批**已确认**节点的归组，名字留空 | 一个新**议题**（`Topic`） |
| 免不免确认 | 免 —— 名字留空，系统一句话都没说 | **不免** —— 新议题是一条新断言 |

需求方定的落法是**后者**：社区成员提一个新议题，落成 `proposed` 的 `Topic`，
走 `§C12.5` 的确认流程。于是 `CONTROL_RULES` 一个字不用改，
「上层节点不许被底层当命题用」这条边界也不动。

--- 单向性 ----------------------------------------------------------------------

本模块**只写底层**：它不 import `upper`，也不读上层任何东西。
贡献进来的是事实（谁说了什么），归组是上层的事 —— 两者不在一个模块里，
所以「上层的相关性判断被写成底层的真值判断」在这条路上**没有入口**。
B23 把这条也钉住了（import 白名单）。
"""

from __future__ import annotations

import sqlite3

import scaffold

CONTRIBUTIONS_VERSION = "community-contributions/1"

# 七种粒度，**顺序即展示顺序**。`CONTRIBUTIONS` 的键必须恰好等于这个元组 ——
# 少一个是「有一种贡献做不出来」，多一个是「有一种没在判据里」。
GRANULARITIES = (
    "claim",           # 一条命题
    "evidence",        # 给某条命题加证据
    "challenge",       # 质询某条命题 / 证据 / 假设
    "counterexample",  # 给某条命题举一个反例
    "revision",        # 改一条已有内容
    "connection",      # 指出两条已有内容之间的关系
    "context",         # 提一个新议题
)

# ★ 这张表是「入口不需要图知识」的**唯一**落点。
#
# 字段（**白名单**，多一个就是让某一种贡献携带别的东西 —— B23 盯着）：
#
#   type_         这种贡献建出来的节点类型；`None` = 不新建节点
#   relation      默认的边；`None` = 不建边
#   choices       可选的边（只有 `evidence` 有多个）。**值必须是关系种类**
#   direction     `to_target`   新建的节点 → 目标
#                 `from_target` 目标 → 新建的节点
#                 `between`     两边都是已有节点，不新建
#                 `None`        没有边
#   target_types  目标允许的节点类型（`§C4` / `§C6.3` 的定义域）
#   state         落点：`proposed` = 等确认；`active` = 默认生效 + 可推翻
#
# ⚠️ 方向的取值**照文档自己的箭头**，不自创：
#   `§C6.3`  Evidence ──supports/contradicts/qualifies──> Claim   → `to_target`
#   `§C4`    Claim    ──challenged_by──> Counterargument         → `from_target`
# 两条方向相反不是笔误。`upper.count_signals()` 读 `challenged_by` 的 `from_id`
# 作为「被质询的那个东西」，正是照 `§C4` 的方向写的。
CONTRIBUTIONS: dict[str, dict] = {
    "claim": {
        "type_": "Claim",
        "relation": None,
        "direction": None,
        "state": "proposed",
    },
    "evidence": {
        "type_": "Evidence",
        "relation": "supports",
        "choices": ("supports", "contradicts", "qualifies"),
        "direction": "to_target",
        "target_types": ("Claim",),
        "state": "active",
    },
    "challenge": {
        "type_": "Counterargument",
        "relation": "challenged_by",
        "choices": ("challenged_by",),
        "direction": "from_target",
        # `§C4` 原本只有 Claim 一条。缺口④ 把它放宽到这三个 —— 理由写在
        # `scaffold.RELATION_KINDS` 与上游 `debate.CHALLENGEABLE` 那里：
        # 真实辩论里最常见的两种有效反驳，反对的是**证据的属性**或**前提本身**。
        "target_types": ("Claim", "Evidence", "Assumption"),
        "state": "active",
    },
    "counterexample": {
        "type_": "Counterexample",
        "relation": "contradicts",
        "choices": ("contradicts",),
        "direction": "to_target",
        "target_types": ("Claim",),
        "state": "active",
    },
    "revision": {
        "type_": None,
        "relation": None,
        "direction": None,
        "state": None,
    },
    "connection": {
        "type_": None,
        "relation": "related_to",
        "choices": ("related_to",),
        "direction": "between",
        "state": "active",
    },
    "context": {
        "type_": "Topic",
        "relation": None,
        "direction": None,
        "state": "proposed",
    },
}

# `CONTRIBUTIONS` 条目的字段**白名单** —— 手法同 `pointer.SELECTOR_FIELDS`。
# 多一个字段（信心、来源、模型输出）就是让某一种贡献携带别的东西，
# 而它照样长得像一条贡献定义。
CONTRIBUTION_FIELDS = (
    "type_", "relation", "choices", "direction", "target_types", "state",
)

# 落点状态只有这两个取值（`None` = 不新建节点）。
# ⚠️ 没有 `"active"` 之外的第三个 —— 尤其**没有** `"superseded"`：
# 建出来的东西不可能一上来就是被取代的。
CONTRIBUTION_STATES = ("proposed", "active")

# 七个入口**共有的**参数：谁提交的。少一个 `by` 就意味着机器可以冒充人。
CONTRIBUTION_ACTOR = "by"

# ⚠️ 入口签名里**不许出现**的词表参数。
#
# 为什么把这条列成清单而不是「看着办」：这七个函数的存在理由就是
# 「调用方不必挑类型与关系」。签名里出现 `kind=` 的那一刻，
# 这一层就退化成了原语的薄包装 —— 而它看起来还是七个漂亮的名字。
# B23 拿这个清单去比对七个签名。
FORBIDDEN_PARAMS = (
    "kind", "type", "type_", "state", "relation", "predicate", "edge", "node",
    "intake", "origin", "digital_source_type", "asserted_by",
)


class ContributionError(scaffold.ScaffoldError):
    """贡献入口的拒绝。**继承 `ScaffoldError`**，调用方只 catch 一个就行。"""


def _spec(granularity: str) -> dict:
    spec = CONTRIBUTIONS.get(granularity)
    if spec is None:
        raise ContributionError(
            f"未知的贡献粒度 {granularity!r} —— 只有 {list(GRANULARITIES)}。"
        )
    return spec


def _check_target(conn: sqlite3.Connection, target: str, spec: dict,
                  granularity: str) -> None:
    """目标必须在定义域里。**定义域是从文档抄的，不是「什么都行」。**

    放开到「`get()` 拿得到的任何东西」等于声明了一个没有依据的定义域 ——
    那和当初只写一条是同一个病，只是方向反过来（缺口④的原话）。
    """
    allowed = spec.get("target_types")
    if allowed is None:
        raise ContributionError(f"{granularity} 不接受目标。")
    row = scaffold.get(conn, target)          # 不存在就抛，不静默
    if row["type"] not in allowed:
        raise ContributionError(
            f"{granularity} 的目标只能是 {list(allowed)}，"
            f"{target} 是 {row['type']}。定义域照文档，不照「什么都行」。"
        )


def _resolve_relation(granularity: str, spec: dict,
                      relation: str | None) -> str | None:
    kind = spec["relation"] if relation is None else relation
    if kind is None:
        return None
    choices = spec.get("choices")
    if choices is None or kind not in choices:
        raise ContributionError(
            f"{granularity} 的边只能是 {list(choices or ())}，收到 {kind!r} —— "
            "关系种类由 CONTRIBUTIONS 定，不由调用方挑。"
        )
    return kind


def _submit(conn: sqlite3.Connection, *, granularity: str, text: str,
            by: str, target: str | None = None,
            relation: str | None = None) -> dict:
    """七条路共用的那一段：建节点 → （按档）确认 → 建边 → 记一笔。

    ⚠️ 为什么 `by` 是必填且**没有默认值**：`§C2.3` 说机器不得
    「依据自身判断生成新事实并写入知识结构」。留一个默认值，
    就等于给「机器自己提交一条贡献」留了一条路 —— 而那条路看起来很正常。
    """
    spec = _spec(granularity)
    kind = _resolve_relation(granularity, spec, relation)
    if spec["direction"] in ("to_target", "from_target"):
        if target is None:
            raise ContributionError(f"{granularity} 必须给一个目标。")
        _check_target(conn, target, spec, granularity)
    if spec["type_"] is None:
        raise ContributionError(
            f"{granularity} 不新建节点 —— 它走 revision() / connection() 那两条。"
        )

    aid = scaffold.add_artifact(
        conn, type_=spec["type_"],
        content={"text": text, "raw_text": text},
        origin=by,
    )
    # 落点照 `§C2.5` 的分档。`activate()` 是唯一入口，且必须写明是谁确认的。
    if spec["state"] == "active":
        scaffold.activate(conn, aid, by=by)

    rid = None
    if kind is not None:
        if spec["direction"] == "from_target":
            from_id, to_id = target, aid
        else:
            from_id, to_id = aid, target
        rid = scaffold.add_relation(conn, kind=kind, from_id=from_id,
                                    to_id=to_id, origin=by)

    scaffold.record_event(conn, f"contribution_{granularity}", by, aid, {
        "target": target, "relation": rid, "kind": kind,
        "version": CONTRIBUTIONS_VERSION,
    })
    conn.commit()
    return {
        "granularity": granularity,
        "artifact": aid,
        "relation": rid,
        "state": spec["state"],
        # 「建出来了」与「算数了」是两件事。这个布尔值就是那个分界，
        # 不是提示语 —— `pending()` 列的就是它那一列。
        "needs_confirmation": spec["state"] == "proposed",
    }


# ---------------------------------------------------------------------------
# 七种粒度
# ---------------------------------------------------------------------------

def claim(conn: sqlite3.Connection, *, text: str, by: str) -> dict:
    """提出一条命题。**不需要挂在任何已有东西上** —— 库里空着也能建。

    这一条对应出口判据 ②：「**没有任何现成论点**时也能创建 Claim」。
    所以它不收目标、不建边、也不要求先有一个 Topic ——
    首个贡献者面对的是一张空库，而空库里没有东西可以「挂上去」。

    落 `proposed`（`§C2.5` 第 2 档：新建可被独立引用的对象必须确认）。
    """
    return _submit(conn, granularity="claim", text=text, by=by)


def evidence(conn: sqlite3.Connection, *, text: str, target: str,
             stance: str = "supports", by: str) -> dict:
    """给某条命题加一条证据，并说明它起什么作用。

    `stance` 是**人话**（支持 / 反驳 / 限定），不是关系种类名 ——
    它落在哪条边上由 `CONTRIBUTIONS` 决定（`§C6.4`：作用属于边，
    不属于证据本身；同一条证据挂到另一条命题上可以是另一种作用）。
    """
    return _submit(conn, granularity="evidence", text=text, by=by,
                   target=target, relation=stance)


def challenge(conn: sqlite3.Connection, *, text: str, target: str, by: str) -> dict:
    """质询一条命题 / 证据 / 假设（`§C3.4`：一个 Artifact + 一条 Relation）。

    落成 `Counterargument`，**不是**给被质询的东西减分 ——
    本系统不产生那种量（`§C6.1`）。**也不是**新建一个叫 `Challenge` 的节点类型：
    缺口④ 的原话是「不要为了绕开一个定义域问题而新增标签」。
    """
    return _submit(conn, granularity="challenge", text=text, by=by, target=target)


def counterexample(conn: sqlite3.Connection, *, text: str, target: str,
                   by: str) -> dict:
    """给某条命题举一个反例。

    `§C4` 的结构树里有 `Counterexample` 这一层，但关系映射表**没给它边** ——
    树上挂着、表上落不了地。这里补 `contradicts`，方向照 `§C6.3`
    （证据 → Claim），与 `evidence(..., stance="contradicts")` 同向：
    反例是**一种证据**，不是另一种主张。
    """
    return _submit(conn, granularity="counterexample", text=text, by=by,
                   target=target)


def revision(conn: sqlite3.Connection, *, of: str, text: str, by: str,
             parent_rev: int | None = None) -> dict:
    """改一条已有内容。**不覆盖** —— 旧版本一行都不会被改（`§C10`）。

    若这条内容已经被并发改出多个分支，`scaffold.revise()` 会**拒绝**自动挑一个。
    那不是报错，那是「没有当前版本」这件事本身 —— 系统不替人选分支。
    """
    _spec("revision")
    rev = scaffold.revise(conn, of, content={"text": text, "raw_text": text},
                          author=by, parent_rev=parent_rev)
    scaffold.record_event(conn, "contribution_revision", by, of, {"revision": rev})
    conn.commit()
    return {
        "granularity": "revision",
        "artifact": of,
        "relation": None,
        "state": None,               # 状态不动：改的是内容，不是「算不算数」
        "needs_confirmation": False,
        "revision": rev,
    }


def connection(conn: sqlite3.Connection, *, left: str, right: str, by: str) -> dict:
    """指出两条已有内容之间的关系。**不新建节点。**

    为什么这就够了、不需要再建一个「连接」节点：本仓库的 `relation` 表
    **本身就是 S-node** —— 它有独立 `id`、有 `origin`、有 `state`，
    可以被推翻（`reject_relation`）。按 AIF，连接是一个**有身份、可被质疑**的东西，
    而不是两个 I-node 之间的一条直连边 —— 这一条刚好对上。
    """
    spec = _spec("connection")
    kind = _resolve_relation("connection", spec, None)
    for aid in (left, right):
        scaffold.get(conn, aid)               # 不存在就抛，不静默
    if left == right:
        raise ContributionError(
            "连接的两端是同一个东西 —— 那不是连接，是自指。"
            "要表达「这条自己有问题」请用 challenge()。"
        )
    rid = scaffold.add_relation(conn, kind=kind, from_id=left, to_id=right,
                                origin=by)
    scaffold.record_event(conn, "contribution_connection", by, left,
                          {"right": right, "relation": rid, "kind": kind,
                           "version": CONTRIBUTIONS_VERSION})
    conn.commit()
    return {
        "granularity": "connection",
        "artifact": None,            # 不新建节点
        "relation": rid,
        "state": "active",
        "needs_confirmation": False,
    }


def context(conn: sqlite3.Connection, *, text: str, by: str) -> dict:
    """提一个新议题。

    ⚠️ 落成 `Topic`，**不是** `§C7.1` 的上层 `Context` 节点 —— 理由见模块开头
    「`context` 为什么不是上层 `Context`」那张表。一句话：上层节点是**归组**，
    免确认的理由是名字留空；而一个新议题是**新断言**，必须走确认。

    落 `proposed`（`§C2.5` 第 2 档 + `§C12.5`：分裂/新议题只能由人确认后创建）。
    """
    return _submit(conn, granularity="context", text=text, by=by)


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def pending(conn: sqlite3.Connection) -> list[dict]:
    """还没被确认的贡献（`state='proposed'` 的节点）。

    出口判据 ①「七种逐个可创建」里，`claim` / `context` 建出来就落在这一列上。
    它值得单独有一个读口，是因为**「建出来了」和「算数了」是两件事** ——
    而这一层是唯一能把两件事分开看的地方。
    """
    rows = conn.execute(
        "SELECT id, type, origin, created_at FROM artifact"
        " WHERE state = 'proposed' ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def describe() -> list[dict]:
    """七种粒度各落成什么 —— **只读常量，不碰库**。

    给界面与测试用：这一层要能自述「每一种贡献会变成什么」，
    而自述的依据必须是 `CONTRIBUTIONS` 本身，不是另抄一份。
    """
    out = []
    for name in GRANULARITIES:
        spec = CONTRIBUTIONS[name]
        out.append({
            "granularity": name,
            "type_": spec["type_"],
            "relation": spec["relation"],
            "choices": spec.get("choices", ()),
            "direction": spec["direction"],
            "state": spec["state"],
            "needs_confirmation": spec["state"] == "proposed",
        })
    return out


__all__ = [
    "CONTRIBUTIONS_VERSION", "GRANULARITIES", "CONTRIBUTIONS",
    "CONTRIBUTION_FIELDS", "CONTRIBUTION_STATES", "CONTRIBUTION_ACTOR",
    "FORBIDDEN_PARAMS", "ContributionError",
    "claim", "evidence", "challenge", "counterexample",
    "revision", "connection", "context",
    "pending", "describe",
]
