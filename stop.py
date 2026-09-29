"""停止条件 —— `POLICY` 的四个触发点。**只判不执行。**

贯穿项（工程稿 §〇 Q6 + §5.4 + §八）。定位**早就给过**，这里只是把它落成可执行的：

    §〇 Q6   停止阈值**可变动**，落 `POLICY` 常量          → `policy.py` 已落地
    §5.4     四条件 + 触发后动作                            → 本模块
    §八      P7 是**行为**指标，不能否证，**只能监控**      → 决定了本模块不是 B 条检查

--- 本模块只判，不执行 -------------------------------------------------------

四个动作（暂停导入 / 停书籍路线 / 暂停蒸馏 / 回退批次）**都是应用层的开关**。
本模块给的是「**该做什么**」，不是「已经做了什么」。

为什么不做成「判完就执行」：那些开关是**运行时状态**，不是事实。
本仓是事实层 —— 多一条写路径就多一个能悄悄改库的地方，
而 `§C10`「只增不改」的社区层是不可再生的。同 `policy.py` 的理由：
门槛经常要调，不该进事实层。

所以本模块**一句写语句都没有**（B26 判据 3 静态钉着），
`judge()` 从头到尾只有 `SELECT`。

--- ★ 三态，不是两态 ---------------------------------------------------------

    触发    该做动作了
    未触发  判了，不用做
    判不了  缺输入 / 没有分母 —— **不等于「没触发」**

这条与 `candidates.origin_class_or_none()` 是同一个手法：
**「分不出来」和「没有」是两件事**。压成两态之后，
「这个条件还没法判」会被读成「这个条件安全」，而两者要采取的行动完全相反。

最要紧的一处：**冷启动时 `贡献 = 0`，比值没有分母**。
压成两态的话，冷启动会立刻「触发暂停导入」—— 而冷启动的第一步本来就是导入
（先把书蒸馏进来，才有得讨论）。三态把这种情况报成「判不了」，
并**说清原因**，而不是让它冒充一个结论。

--- 度量口径（`§T4` 留白项：自选 + 声明 + 可回退）----------------------------

工程稿 §5.4 写的是「导入 : 贡献 比值」，**没给精确口径**。本模块自选如下：

    导入 = `intake='staged'` 的 artifact
    贡献 = `intake='direct'` 且 **type 不是上层独有**的 artifact

⚠️ 分母**不能**直接数 `intake='direct'` —— `scaffold.INTAKE_CHANNELS` 里
`direct` 的口径是「社区贡献、上层 Context、以及**一切非蒸馏来源**」。
上层 Context 也是 `direct`，把它算成「用户贡献」会让这个条件永远判不出来。
所以这里减去 `scaffold.UPPER_ONLY_TYPES`。

⚠️ 两边都数**全部 state**（含还没 activate 的 `proposed`）—— 比的是**体量**，
不是「有多少条已经算数了」。只数一边、或一边只数 `active`，
比出来的东西就不叫「导入压过贡献」了。

换口径只改 `observe()` 一个函数 —— 这就是「可回退」的可执行形式。

--- 两个条件需要外部输入 -----------------------------------------------------

四条件里只有两条是**本仓自己算得出**的：

    算得出  ① 导入压过贡献（数 artifact）③ staging 积压（数 staging）
    要外部  ② 连续 N 本书蒸馏后没变化   ④ 某个导入节点「书里没有」

② 要的是**跨批次历史**，④ 要的是**人查证的结果**。两者本仓都不存 ——
存了就要新增写路径。所以它们由调用方经 `declared` 传进来（见 `judge()`）。

⚠️ 传进来的**是观测量，不是结论**：
② 收的是「每本书蒸馏后有没有变化」的序列，本模块自己数**末尾连续几本没变**；
④ 收的是「被查出有问题的节点 id」，本模块自己去查它们属于哪些批次。
调用方不替本模块下判定。

--- 与 P7 观测点的分工（工程稿 §八）------------------------------------------

    P7 三指标   观测点：**只记**，不得与任何数比较（B6）
    POLICY     运维门槛：**只触发动作**，不写回字段、不参与排序、不碰内容结构

`observe()` 是「只记」那一半（纯读，给读数用）；
`judge()` 是「过门槛」那一半。两半都**不碰内容结构**。

⚠️ 观测量**不得**进 `upper.COUNT_SIGNALS` —— 那会撞 B4（上层不读热度类信号）。
B26 判据 5 钉着这一点。
"""

from __future__ import annotations

import sqlite3

import policy
import scaffold
import staging

# 四个条件的名字。**封闭集合** —— 加一个要先改这里、`STOP_ACTIONS`、
# `STOP_POLICY_KEYS` 三处，B26 判据 1/2 会当场报。
STOP_CONDITIONS = (
    "import_dominates",      # ① 导入压过贡献 —— 退化成消费平台
    "books_have_no_effect",  # ② 书蒸馏完没有变化 —— 书籍路线走到头了
    "staging_backlog",       # ③ 门堵住了
    "unfaithful_import",     # ④ 导入的东西不可信
)

# 每个条件触发后**该做什么**。动作名是**应用层的开关名**，本模块不执行它。
STOP_ACTIONS = {
    "import_dominates": "pause_import",       # 暂停导入，只开社区入口
    "books_have_no_effect": "drop_book_route",  # 停止书籍路线，回到社区冷启动
    "staging_backlog": "pause_distill",       # 暂停蒸馏，先清门
    "unfaithful_import": "rollback_batch",    # 该批次全部回退，查蒸馏器
}

# 每个条件读哪个 `POLICY` 键。`None` = 无门槛（有就触发）。
#
# ⚠️ 键名必须真的在 `policy.POLICY` 里 —— B26 判据 1 双向钉住
# （每个条件有键、每个键被用上）。写错一个名字的话，
# `policy.value()` 会在**运行时**抛 KeyError，而那时停止条件已经该报没报了。
STOP_POLICY_KEYS = {
    "import_dominates": "import_to_contribution_ceiling",
    "books_have_no_effect": "books_without_effect",
    "staging_backlog": "staging_backlog_limit",
    "unfaithful_import": None,
}

# 三态。**不是布尔** —— 见模块开头。
TRIGGERED = "triggered"
CLEAR = "clear"
NOT_JUDGED = "not_judged"

# `judge()` 收的外部观测量。**封闭集合**：多一个键说明有人在往这里塞结论。
DECLARED_INPUTS = ("book_effects", "unfaithful")

# 两个来源口。⚠️ 名字必须与 `scaffold.INTAKE_CHANNELS` 逐字一致 ——
# B26 判据 4 钉着。写成别的词不会报错，只会**永远数出 0**。
STAGED_CHANNEL = "staged"
DIRECT_CHANNEL = "direct"

# 观测量 —— 就是 `observe()` 返回的那几个键。**封闭集合**。
#
# ⚠️ 这些**不得**进 `upper.COUNT_SIGNALS`：那是**上层**的信号白名单，
# 混进去等于让上层读运维门槛（工程稿 §八「观测点不得进 COUNT_SIGNALS」，
# 撞 B4）。B26 判据 5 拿它比对白名单，判据 6 再拿它比对 `observe()` 的实现 ——
# 两处一起，常量与实现就不会悄悄漂开。
OBSERVED_KEYS = ("imported", "contributed", "staging_backlog")


def observe(conn: sqlite3.Connection) -> dict:
    """本仓**算得出**的观测量。纯读 —— 一行写语句都没有。

    这一半是工程稿 §八 说的「观测点：**只记**」。它不做任何比较，
    比较在 `judge()` 里（那是 `POLICY` 那一半）。
    """
    imported = conn.execute(
        "SELECT COUNT(*) AS n FROM artifact WHERE intake = ?",
        (STAGED_CHANNEL,),
    ).fetchone()["n"]

    # 分母要减掉上层独有类型 —— 理由见模块开头「度量口径」。
    # 用占位符而不是拼字符串：`UPPER_ONLY_TYPES` 将来加类型时这里自动跟上。
    upper = tuple(scaffold.UPPER_ONLY_TYPES)
    marks = ",".join("?" * len(upper))
    contributed = conn.execute(
        f"SELECT COUNT(*) AS n FROM artifact"
        f" WHERE intake = ? AND type NOT IN ({marks})",
        (DIRECT_CHANNEL, *upper),
    ).fetchone()["n"]

    backlog = conn.execute(
        "SELECT COUNT(*) AS n FROM staging WHERE gate_state = ?",
        (staging.GATE_PENDING,),
    ).fetchone()["n"]

    return {
        "imported": int(imported),
        "contributed": int(contributed),
        "staging_backlog": int(backlog),
    }


def _finding(condition: str, state: str, *, because: str, **observed) -> dict:
    """一条判定记录。`action` 只在**触发**时给出 —— 未触发/判不了时是 `None`。"""
    return {
        "condition": condition,
        "state": state,
        "action": STOP_ACTIONS[condition] if state == TRIGGERED else None,
        "because": because,
        "observed": observed,
    }


def _judge_import_dominates(seen: dict, override: dict) -> dict:
    imported, contributed = seen["imported"], seen["contributed"]
    if contributed == 0:
        return _finding(
            "import_dominates", NOT_JUDGED,
            because="还没有社区贡献 —— 这个条件没有分母，判不了。"
                    "冷启动的第一步本来就是导入（先把书蒸馏进来才有得讨论），"
                    "把它读成「触发」会把冷启动直接掐死。",
            imported=imported, contributed=contributed)
    ceiling = policy.value("import_to_contribution_ceiling", **override)
    if imported > ceiling * contributed:
        return _finding(
            "import_dominates", TRIGGERED,
            because=f"导入 {imported} 条，社区贡献 {contributed} 条，"
                    f"超过了上限 {ceiling}（导入 > 上限 × 贡献）。"
                    "用户不再贡献，平台在往消费端退化。",
            imported=imported, contributed=contributed, ceiling=ceiling)
    return _finding(
        "import_dominates", CLEAR,
        because=f"导入 {imported} 条 : 贡献 {contributed} 条，在上限 {ceiling} 之内。",
        imported=imported, contributed=contributed, ceiling=ceiling)


def _judge_books_have_no_effect(declared: dict, override: dict) -> dict:
    effects = declared.get("book_effects")
    if effects is None:
        return _finding(
            "books_have_no_effect", NOT_JUDGED,
            because="调用方没有给 `book_effects`（每本书蒸馏后 P7 有没有变化的序列）"
                    "—— 这个条件要的是跨批次历史，本仓不存它。")
    effects = list(effects)
    if not effects:
        return _finding(
            "books_have_no_effect", NOT_JUDGED,
            because="还没有蒸馏过书 —— 序列是空的。"
                    "「连续 N 本没变化」在没有书的时候不成立，也不等于「没触发」。",
            books=0)
    # 数**末尾**连续几本没有变化。只看末尾 —— 中间断过就重新计数，
    # 因为「连续」这个词说的是「最近这一段」。
    streak = 0
    for changed in reversed(effects):
        if changed:
            break
        streak += 1
    limit = policy.value("books_without_effect", **override)
    if streak >= limit:
        return _finding(
            "books_have_no_effect", TRIGGERED,
            because=f"最近连续 {streak} 本书蒸馏完，P7 三个指标都没有变化"
                    f"（门槛 {limit}）。书籍路线已经没有新东西可给了。",
            books=len(effects), streak=streak, limit=limit)
    return _finding(
        "books_have_no_effect", CLEAR,
        because=f"末尾连续 {streak} 本没有变化，还没到门槛 {limit}"
                f"（总共 {len(effects)} 本）。",
        books=len(effects), streak=streak, limit=limit)


def _judge_staging_backlog(seen: dict, override: dict) -> dict:
    backlog = seen["staging_backlog"]
    limit = policy.value("staging_backlog_limit", **override)
    if backlog > limit:
        return _finding(
            "staging_backlog", TRIGGERED,
            because=f"staging 里积压了 {backlog} 条没过门，超过上限 {limit}。"
                    "门堵住了 —— 再加导入只会让积压更长。",
            backlog=backlog, limit=limit)
    return _finding(
        "staging_backlog", CLEAR,
        because=f"staging 里积压 {backlog} 条，在上限 {limit} 之内。",
        backlog=backlog, limit=limit)


def _batches_of(conn: sqlite3.Connection, ids: list[str]) -> tuple[list[str], list[str]]:
    """这些节点分别属于哪些批次。返回 `(批次, 查不到批次的 id)`。

    第二项要单独返回，不能默默丢掉：**「这个节点不在任何批次里」**
    和「它在一个批次里」是两件事，而丢掉之后调用方会以为
    所有报上来的节点都处理到了。
    """
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT batch, artifact_id FROM staging WHERE artifact_id IN ({marks})",
        ids,
    ).fetchall()
    batches = sorted({r["batch"] for r in rows})
    seen_ids = {r["artifact_id"] for r in rows}
    unknown = [i for i in ids if i not in seen_ids]
    return batches, unknown


def _judge_unfaithful_import(conn: sqlite3.Connection, declared: dict) -> dict:
    bad = declared.get("unfaithful")
    if bad is None:
        return _finding(
            "unfaithful_import", NOT_JUDGED,
            because="调用方没有给 `unfaithful`（被查出「书里没有」的节点 id 列表）"
                    "—— 这要人去核对原文，本仓查不出来。")
    bad = list(bad)
    if not bad:
        return _finding(
            "unfaithful_import", CLEAR,
            because="查证过了，没有发现「书里没有」的节点。", checked=0)
    batches, unknown = _batches_of(conn, bad)
    if not batches:
        return _finding(
            "unfaithful_import", NOT_JUDGED,
            because=f"报上来 {len(bad)} 个节点，但一个都不在 staging 里 —— "
                    "查不到它们属于哪一批，回退不了。要么 id 给错了，"
                    "要么这些不是导入来的节点。",
            reported=len(bad), unknown=unknown)
    return _finding(
        "unfaithful_import", TRIGGERED,
        because=f"{len(bad)} 个导入节点被查出「书里没有」，落在 {len(batches)} 个批次里。"
                "整批回退，并回头查蒸馏器 —— 一条错的说明这一批都可能错。",
        reported=len(bad), batches=batches, unknown=unknown)


def judge(
    conn: sqlite3.Connection, *,
    declared: dict | None = None, **override,
) -> list[dict]:
    """按 `POLICY` 判四个条件。**只读、不写、不执行动作。**

    返回四条判定，顺序与 `STOP_CONDITIONS` 一致 —— 每条都是
    `{condition, state, action, because, observed}`。

        `state` 是 `TRIGGERED` / `CLEAR` / `NOT_JUDGED` 三者之一；
        `action` 只在触发时非 `None`。

    `declared` 是**调用方补的外部观测量**（见 `DECLARED_INPUTS`）：

        declared = {
            "book_effects": [True, False, False, False],   # ② 每本书有没有带来变化
            "unfaithful":   ["art-0007"],                  # ④ 被查出「书里没有」的节点
        }

    ⚠️ 传的是**观测量**，不是结论 —— ② 的「连续几本」由本模块自己数，
    ④ 的「哪些批次」由本模块自己查。

    `**override` 直接透传给 `policy.value()` —— 这就是「可变动」的落地方式：

        judge(conn, staging_backlog_limit=10)   # 只调这一个，其余用默认
    """
    declared = dict(declared or {})
    seen = observe(conn)
    return [
        _judge_import_dominates(seen, override),
        _judge_books_have_no_effect(declared, override),
        _judge_staging_backlog(seen, override),
        _judge_unfaithful_import(conn, declared),
    ]


_MARK = {TRIGGERED: "触发", CLEAR: "未触发", NOT_JUDGED: "判不了"}


def report(conn: sqlite3.Connection, *, declared: dict | None = None, **override) -> str:
    """给人看的一行行。**不改任何东西** —— 同 `judge()`，纯读。"""
    lines = []
    for finding in judge(conn, declared=declared, **override):
        state = _MARK[finding["state"]]
        action = f" → {finding['action']}" if finding["action"] else ""
        lines.append(f"[{state}] {finding['condition']}{action}")
        lines.append(f"         {finding['because']}")
    return "\n".join(lines)


__all__ = [
    "STOP_CONDITIONS", "STOP_ACTIONS", "STOP_POLICY_KEYS",
    "TRIGGERED", "CLEAR", "NOT_JUDGED", "DECLARED_INPUTS", "OBSERVED_KEYS",
    "STAGED_CHANNEL", "DIRECT_CHANNEL",
    "observe", "judge", "report",
]
