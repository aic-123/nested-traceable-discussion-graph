"""`§C2.5` 候选关系 —— AI 提的边**算不算数由人定，不由模型定**。

阶段 5b。设计稿第 10 节把 promote 拆成「human / system validation」，
第 9 节又把 AI 的位置定在 **Exploratory 区**：

    may help discovery.  **It shouldn't silently become institutional truth.**

本模块是那句话的落地：一条**由机器提出**的边进得了库，但**进不了读数**。

--- 三条出口判据，落成什么 -----------------------------------------------------

| 判据 | 落地形态 |
|---|---|
| ① AI 产出只能是 candidate | `record()` 的签名里**没有 `state`**，函数体恒写 `proposed` |
| ② promote 只能由人 / 结构规则触发 | `promote()` 恰好收 `by` 或 `rule` 之一，两个都给或都不给都拒 |
| ③ 把模型判断当 promote 条件会被拒绝 | `rule` 只能是**字符串**，且必须命中 `rules.RULES` —— 传不进函数 |

① 与 B14「没有 `conn` 就没法写库」是同一个做法：**把纪律换成能力上的不可能**。
不是「约定别写 active」，是这个入口里没有地方能填 active。

--- 这一层原先**落不了地**，原因在 schema 里（2026-09-28 实测）----------------

设计稿的原语表写着：

    Candidate | 同 Node，state=proposed + promote_record | promote 前不得进 lower 的有效区

但 `relation` 表的状态词表原先只有 `active / rejected / superseded` ——
**没有 `proposed`**。于是「一条边在被确认之前」这种状态**根本表达不出来**：
`scaffold.add_relation(state="proposed")` 会当场抛 `ScaffoldError`。

    ARTIFACT_STATES  = ("proposed", "active", "superseded")              节点有
    RELATION_STATES  = ("proposed", "active", "rejected", "superseded")  边补上了

节点有、边没有，这个不对称是历史遗留而不是设计 —— 补上它，5b 才有地方落。
B24 把「`CANDIDATE_STATE` 真的在 `RELATION_STATES` 里」钉成判据：
少了那一条，本模块**每一次调用**都会抛，而那是运行时才发现的事。

--- 候选边「不算数」，由两处同时保证 --------------------------------------------

    结构上：`scaffold.COUNTED_RELATION_STATES == ("active",)`
    读数上：`upper.count_signals()` 与 `_supporting_ids()` 只读 `r.state = 'active'`

两条都必须成立。只改一处的话，候选边会悄悄进读数 —— 而库里看不出任何异常
（边有 id、查得到、长得完全正常）。`test_candidates.py` 里有一条用例
**逐字比对**建候选边前后的 `count_signals()` 输出，把这件事验成行为。

--- 本模块**不发明「提议」** ----------------------------------------------------

`upper.propose_clusters()` 是一个只读的结构提议函数，本模块**没有对应的那一个**。

理由：提议一条**边**需要一条启发式（「两条 claim 引用了同一个来源，
所以也许 `related_to`」）—— 那是**发明判据**，而判据形态已经在 5a 定死在
「已有结构量的合取」上（H5 · B 档）。核心层凭空长出一条启发式，
就是绕开那次拍板。

所以本模块只提供**接收**：谁提的（`origin`）由调用方说，算不算数由人 / 规则定。
「AI 建议候选」那一步是应用层（`arena/`）的事 —— 它调模型，然后把结果送进来。

--- `origin` 为什么必填、且没有默认值 ------------------------------------------

`relation` 表没有 `asserted_by` 栏（那一栏在 `artifact` 上）。所以一条候选边
「**是谁提的**」只能记在 `origin` 里。既然它是唯一的载体，就**不许有默认值** ——
留一个默认值等于给「没人提过这条边」留了个位置，而它看起来很正常。

⚠️ 已知边界（记在工程稿 §11.5，**没有替你决定**）：`origin` 是自由文本，
没有词表约束它必须写出「人 / 机器」。B16 对 **artifact** 有硬要求
（AI 档必须写明 `asserted_by`），对 **relation** 没有对应的一条。
要不要给边补一栏，是一次 schema 变更，不在本阶段。
"""

from __future__ import annotations

import sqlite3

import rules
import scaffold

CANDIDATES_VERSION = "candidate-relations/1"

# 候选边能落的**唯一**状态。
#
# ⚠️ 它必须真的在 `scaffold.RELATION_STATES` 里 —— B24 钉住。
# 少了那一条，本模块每一次 `record()` 都会在运行时抛。
CANDIDATE_STATE = "proposed"

# promote 的两条通路。**恰好一条** —— 见 `promote()`。
PROMOTE_ROUTES = ("by", "rule")

# 两条通路各自的**名字**，记进 `event`。
#
# 为什么要名字：`§C2.4` 要算「AI 判定 vs 用户改判」的比例。那条边是
# 「人点头的」还是「规则推的」，事后必须查得出来 —— 否则后者会被算进前者，
# 而那个比例正是这个系统用来证明自己没在当裁判的东西。
PROMOTE_ROUTE_NAMES = {"by": "human", "rule": "structural_rule"}

# 走规则通路时 `event.actor` 的前缀 —— 一眼看出「这条边是规则提拔的」。
RULE_ACTOR_PREFIX = "rule:"

# `record()` 签名里**不许出现**的参数。
#
# 与 `contribute.FORBIDDEN_PARAMS` 的差别：那一层连 `kind` 都不许有
# （七种粒度各对应一种边，由 `CONTRIBUTIONS` 定）；这一层 `kind` **必须**有 ——
# 「这两条之间是什么关系」正是 AI 要提的那件事，核心层没有依据替它选。
#
# 不许有的是**状态与来源档**：`state` 一出现，这个入口就能写出 `active`，
# 出口判据 ① 当场没了 —— 而它看起来还是个「候选入口」。
FORBIDDEN_PARAMS = (
    "state", "status", "intake", "digital_source_type", "asserted_by",
)


class CandidateError(scaffold.ScaffoldError):
    """候选关系的拒绝。**继承 `ScaffoldError`**，调用方只 catch 一个就行。"""


def _upper_kinds() -> tuple:
    """上层边。`upper.py` 的独占权限 —— 候选边不许碰。"""
    return scaffold.RELATION_LAYERS["upper"]


def record(conn: sqlite3.Connection, *, kind: str, left: str, right: str,
           origin: str) -> dict:
    """把一条**候选边**记进库。它存在、有 id、查得到，但**不算数**。

    ⚠️ **签名里没有 `state`。** 这不是省事，是这个入口的全部意义：
    出口判据 ①「AI 产出只能是 candidate」的可执行形式就是
    「调用方**没有地方**填 `active`」。手法同 B14「没有 `conn` 就没法写库」。

    ⚠️ **不许提上层边**（`scaffold.RELATION_LAYERS["upper"]`，现在只有
    `clustered_into`）。AI 提议一条底层边是探索，提议一条上层边是
    **替系统做归组** —— 那是 `upper.py` 的独占权限，B19 钉着「上层边只有一条」。
    两条合起来说的是同一件事：**AI 提议不了上层结构。**

    ⚠️ `origin` 必填、**没有默认值**。理由见模块开头那一节。
    """
    if kind in _upper_kinds():
        raise CandidateError(
            f"{kind!r} 是上层边 —— 候选边不许提上层结构。"
            "归组是 `upper.py` 的独占权限（B19 钉着「上层边只有一条」）。"
        )
    if left == right:
        raise CandidateError(
            f"候选边的两端是同一个东西（{left}）—— 一条边指向自己不表达"
            "任何关系，只会让读数多一条噪声。"
        )

    rid = scaffold.add_relation(
        conn, kind=kind, from_id=left, to_id=right,
        origin=origin, state=CANDIDATE_STATE,
    )
    scaffold.record_event(conn, "candidate_recorded", origin, left, {
        "relation": rid, "kind": kind, "to": right,
        "version": CANDIDATES_VERSION,
    })
    conn.commit()
    return {
        "relation": rid,
        "kind": kind,
        "state": CANDIDATE_STATE,
        # 「记下来了」与「算数了」是两件事。这个布尔值就是那个分界 ——
        # 不是提示语，`proposed()` 列的就是它那一列。
        "needs_confirmation": True,
    }


def proposed(conn: sqlite3.Connection) -> list[dict]:
    """还没被确认的候选边。**只读。**"""
    rows = conn.execute(
        "SELECT id, kind, from_id, to_id, origin, created_at FROM relation"
        " WHERE state = ? ORDER BY id",
        (CANDIDATE_STATE,),
    ).fetchall()
    return [dict(r) for r in rows]


def promote(conn: sqlite3.Connection, *, relation_id: int,
            by: str | None = None, rule: str | None = None,
            signals: dict | None = None) -> dict:
    """把一条候选边变成**算数**的边。两条通路，**恰好一条**。

        promote(conn, relation_id=7, by="alice")                    # 人点头
        promote(conn, relation_id=7, rule="repeatedly_contested",
                signals=upper.count_signals(conn))                  # 结构规则推

    ⚠️ **为什么必须恰好一条**：两条同时给，事后就查不出这条边到底是
    「人点头的」还是「规则推的」—— 而 `§C2.4` 要算的那个比例正是按这个分的。
    一条都不给更直接：那等于**系统自己提拔自己**。

    ⚠️ **`rule` 只能是一个字符串**，且必须命中 `rules.RULES`。
    这就是出口判据 ③ 的落地形态：不是「我们约定不传模型判断」，而是
    **传不进来** —— 唯一能传的是一个字符串，它会被拿去查一张常量表。
    传函数 / 传模型输出 / 传一个没声明过的名字，全部 `CandidateError`。

    ⚠️ 走规则通路必须给 `signals`，且**这条边的某一端**必须满足那条规则。
    两端都不满足 → **拒绝，不兜住**（兜住之后这条边和正常提拔的一模一样）。
    满足的那几端记在返回值的 `justified_by` 里 —— `§C2.5` 第 3 档要的「可解释」。
    """
    given = [name for name, value in (("by", by), ("rule", rule))
             if value is not None]
    if len(given) != 1:
        raise CandidateError(
            f"promote 必须恰好给一条通路（{list(PROMOTE_ROUTES)}），"
            f"收到 {given or '一条都没给'}。"
            "两条同时给 → 事后查不出是人点头还是规则推的；"
            "一条都不给 → 那是系统自己提拔自己。"
        )

    row = conn.execute(
        "SELECT * FROM relation WHERE id = ?", (relation_id,)
    ).fetchone()
    if row is None:
        raise CandidateError(f"不存在的 relation：{relation_id}")
    if row["state"] != CANDIDATE_STATE:
        raise CandidateError(
            f"relation {relation_id} 是 {row['state']!r}，不是候选 —— "
            f"只有 {CANDIDATE_STATE!r} 能被 promote。"
        )

    justified: list[str] = []
    if by is not None:
        actor, route = by, PROMOTE_ROUTE_NAMES["by"]
    else:
        # ⚠️ 顺序要紧：**先**判规则名，**再**要 signals。
        #
        # 反过来的话，传一个函数进来会撞到「没给 signals」——
        # 而真正该报的是「promote 条件只能按名字引用一条声明过的规则」。
        # 出口判据 ③ 拦的就是前者，报错必须指着它，否则读者会去补 signals，
        # 补完再撞一次，而那条信息从头到尾没出现过。
        if rule not in rules.RULES:
            raise CandidateError(
                f"{rule!r} 不是声明过的规则。现有：{sorted(rules.RULES)} —— "
                "promote 条件只能**按名字引用一条声明过的规则**，"
                "不能是函数、不能是模型输出。"
            )
        if signals is None:
            raise CandidateError(
                "走规则通路必须给 signals —— 规则读的是结构量，"
                "没有读数就没法判它成不成立。"
            )
        ends = {row["from_id"], row["to_id"]}
        result = rules.evaluate(signals, rules={rule: rules.RULES[rule]})
        justified = sorted({m["target"] for m in result["matches"]} & ends)
        if not justified:
            raise CandidateError(
                f"这条候选边的两端（{sorted(ends)}）都不满足规则 {rule!r} —— "
                "拒绝，不兜住：兜住之后这条边和正常提拔的一模一样。"
            )
        actor, route = f"{RULE_ACTOR_PREFIX}{rule}", PROMOTE_ROUTE_NAMES["rule"]

    conn.execute("UPDATE relation SET state = 'active' WHERE id = ?",
                 (relation_id,))
    scaffold.record_event(conn, "candidate_promoted", actor, row["from_id"], {
        "relation": relation_id, "kind": row["kind"], "to": row["to_id"],
        "route": route, "rule": rule, "justified_by": justified,
        "proposed_by": row["origin"],
        "version": CANDIDATES_VERSION,
    })
    conn.commit()
    return {
        "relation": relation_id,
        "state": "active",
        "route": route,
        "by": by,
        "rule": rule,
        "justified_by": justified,
        "proposed_by": row["origin"],
    }


def render(items: list[dict]) -> str:
    """印成人看的。只读 —— 和 `upper.render()` / `rules.render()` 一个规矩。"""
    lines = [
        "=" * 64,
        f"候选关系 {CANDIDATES_VERSION}"
        f"（`§C2.5`：{CANDIDATE_STATE}，**不算数**）",
        "",
    ]
    if not items:
        lines += [
            "  没有待确认的候选边。",
            "",
            "  ⚠️ 「没有」和「算不出」不是一回事 —— 这里只查了 "
            f"`relation.state = {CANDIDATE_STATE!r}` 的那一批。",
        ]
    for it in items:
        lines.append(
            f"  #{it['id']}  {it['from_id']} ──{it['kind']}──> {it['to_id']}")
        lines.append(f"        提的人：{it['origin']}    {it['created_at']}")
    lines += [
        "",
        "⚠️ 这些边**不算数**：不进任何读数，也不影响任何排序。",
        "   算不算数由 `promote()` 定 —— 人能点头，规则能自动，模型两样都不能。",
    ]
    return "\n".join(lines)


__all__ = [
    "CANDIDATES_VERSION", "CANDIDATE_STATE", "PROMOTE_ROUTES",
    "PROMOTE_ROUTE_NAMES", "RULE_ACTOR_PREFIX", "FORBIDDEN_PARAMS",
    "CandidateError",
    "record", "proposed", "promote", "render",
]
