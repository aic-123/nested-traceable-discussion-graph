"""蒸馏入层门（阶段 3）—— **导入层与社区层分开走**。

本模块只做一件事：**让「从书里蒸出来的东西」与「用户自己说的话」
走两条不同的入层路径，并且前一条必须过门。**

    书籍蒸馏 ──▶ staging（state=proposed, intake=staged） ──▶ 门 ──▶ 可确认
    社区贡献 ──▶ 直接落（state=proposed, intake=direct）  ────────▶ 可确认

--- 门为什么可以只有 10% 的抽检率 ---------------------------------------------

因为**导入层与社区层的可变性不同**：

| | 导入层（蒸馏） | 社区层（用户） |
|---|---|---|
| 来源 | 外部 —— **书还在** | 内部 —— **不可再生** |
| 出错代价 | 重蒸一遍 | **不可逆** |

所以 P2（Historical Immutability）**只管社区层**，导入层可以整层重建。
于是门的目的从「防止不可逆污染」降级为「**防止低质内容影响用户判断**」——
后者宽松得多：门只需挡质量，不必挡数量。

⚠️ 降级的**前提**是「导入层真的可整层重建」。这个前提不是口号，
它有可执行形式（见 `clear_batch()` 与 `orphan_edges()`）：
**导入层不许承载任何社区层指过来的边** —— 一旦有，重建就会撕开社区层，
`clear_batch()` 会拒绝并把这些边列出来。

--- DeepRead 八种关系 → 本仓库 kind：**必须显式映射** ---------------------------

蒸馏流程照抄 DeepRead，但它那八种关系与本仓库的 `RELATION_KINDS` **不是一套**。
所以落 staging 时保留 DeepRead 原词，**入层时映射**。

映射表 `DEEPRED_RELATION_MAP` 里三件事都可能出现：

    字符串    —— 一对一，直接用
    元组      —— 一**对多**，要展开（见下面 `causes`）
    None      —— **没有对应**。这时 `map_relation()` 直接抛错。

⚠️ `None` 这一档是这张表的重点。**没有对应就必须停下来问人**，
不许拿 `related_to` 兜住 —— 兜住之后，一条「这条是那条的一个例子」
会变成「这两条有点关系」，而**它在库里长得完全正常**，
只是信息没了。这正是 B21 盯的那件事。

--- 四档置信度：是**分档**，不是打分 ---------------------------------------------

`CONFIDENCE_LEVELS` 照抄 DeepRead，说的是「**原文对这条主张的支持程度**」，
不是「这条主张有多可信」：

    author intent          原文明确表达了作者意图
    original facts & data  原文给了事实或数据
    reasonable inference   原文没直说，是合理推断
    unverifiable           原文里找不到依据

⚠️ 所以它**不得**进 `upper.COUNT_SIGNALS`，**不得**参与排序、**不得**影响展示
（撞 B4：上层不读热度类信号）。它唯一的用途是**给人过门时看一眼** ——
抽检的人看到一批全是 `unverifiable`，就知道这台蒸馏器该修了。
"""

from __future__ import annotations

import sqlite3

import pointer
import policy
from scaffold import (
    GATE_PASSED,
    GATE_PENDING,
    GATE_STATES,
    ScaffoldError,
    activate,
    add_artifact,
    add_relation,
    now,
)

# ---------------------------------------------------------------------------
# 蒸馏的词汇表（照抄，不自造）
# ---------------------------------------------------------------------------

# DeepRead 的四档置信度。**逐字不改** —— 改了就跟上游对不上，
# 而「跟上游对得上」是照抄的全部理由。
CONFIDENCE_LEVELS = (
    "author intent",
    "original facts & data",
    "reasonable inference",
    "unverifiable",
)

# DeepRead 的八种关系。**键名逐字不改**（含 `depends on` 里的空格）。
DEEPRED_RELATIONS = (
    "supports",
    "refutes",
    "causes",
    "explains",
    "depends on",
    "exemplifies",
    "contrasts",
    "limits",
)

# `causes` 的展开结果：本仓库把「A 导致 B」拆成**一个因果主张 + 两条边**
# （见 `scaffold.RELATION_KINDS` 里 `causal_premise` / `causal_conclusion` 的注释）。
# 所以 DeepRead 的一条边，到这里变成三个对象。
CAUSAL_EXPANSION = ("causal_premise", "causal_conclusion")

# ★ 八种 → 本仓库 kind 的**显式**映射表。
#
# 每一条都写清「为什么这么对」，因为这几条**不是机械改名**：
DEEPRED_RELATION_MAP: dict[str, object] = {
    # 一对一，名字都一样 —— 直接对上。
    "supports": "supports",
    "explains": "explains",

    # `refutes` 比 `contradicts` 语气强（反驳 vs 矛盾）。
    # 本仓库没有单独的「反驳」kind，所以并到 `contradicts`。
    # ⚠️ 这是**有损**的：反向映射（`contradicts` → `refutes`）不成立。
    # 保留原词在 staging 的 content 里，就是为了让这一损失**可查**。
    "refutes": "contradicts",

    # `limits`（这条限制了那条的适用范围）≈ `qualifies`（限定）。
    "limits": "qualifies",

    # `depends on`（这条依赖那条）≈ `assumes`（这条假定那条成立）。
    # ⚠️ 这两个不完全等价：依赖是「没有它就不成立」，
    # 假定是「把它当成立」。本仓库只有后者，所以并过去，同样是有损的。
    "depends on": "assumes",

    # 一对多：要建中间节点。见 CAUSAL_EXPANSION。
    "causes": CAUSAL_EXPANSION,

    # ⚠️⚠️ 下面两条**没有对应**，是这张表里最要紧的两个格子。
    #
    #   exemplifies  「这条是那条的一个例子」
    #                本仓库没有任何 kind 表达「实例关系」。`refines` 是「更细」，
    #                不是「是一个实例」；`related_to` 会丢掉全部方向与语义。
    #
    #   contrasts    「这条与那条构成对照」
    #                本仓库有 `contradicts`（矛盾，太强）与 `qualifies`（限定，不对）。
    #                对照关系恰恰是**既不矛盾也不限定**的那种，没有位置放。
    #
    # 两条都填 None：`map_relation()` 遇到就抛错，**停下来问人**。
    # 这不是「还没做」，是**做不了**—— 硬填一个现有 kind 就是编。
    "exemplifies": None,
    "contrasts": None,
}

# 显式声明「哪几条没有对应」。B21 会断言它与映射表里值为 None 的集合**相等**。
#
# 为什么单列一个常量而不是从映射表里算出来：这样「把 None 改成某个 kind」
# 这件事**必须同时改两处** —— 于是它一定出现在 diff 里，一定会被看见。
# 形状同 `scaffold.RELATION_PARENTS`（层级变更必须走有记录的改动）。
UNMAPPED_DEEPRED = ("exemplifies", "contrasts")


class StagingError(Exception):
    """入层门拒绝了一次操作。消息面向调用方，说清违反了什么。"""


def map_relation(name: str):
    """DeepRead 的关系名 → 本仓库的 kind（或展开元组）。**没对应就抛错。**

    抛错是刻意的行为，不是缺省失败：`exemplifies` / `contrasts` 两条
    在本仓库里**没有位置放**，硬塞进 `related_to` 会让信息静默消失 ——
    而消失之后，库里那条边长得跟正常边一模一样。
    """
    if name not in DEEPRED_RELATION_MAP:
        raise StagingError(
            f"{name!r} 不是 DeepRead 的八种关系之一：{DEEPRED_RELATIONS}。"
            "蒸馏产物落 staging 时保留原词，这里只做映射 —— "
            "原词拼错说明上游变了，要停下来看，不能兜住。"
        )
    mapped = DEEPRED_RELATION_MAP[name]
    if mapped is None:
        raise StagingError(
            f"{name!r} 在本仓库里**没有对应**的 relation kind"
            f"（见 UNMAPPED_DEEPRED）。"
            "不许拿 `related_to` 兜住：兜住之后这条边在库里长得完全正常，"
            "而它承载的信息已经没了。要么加一个 kind，要么不收这条边 —— "
            "两者都是要人拍板的动作。"
        )
    return mapped


# ---------------------------------------------------------------------------
# 落 staging
# ---------------------------------------------------------------------------

def stage_batch(
    conn: sqlite3.Connection, *,
    batch: str,
    source_uri: str,
    items: list[dict],
    extracted_by: str,
    asserted_by: str,
    source_type: str = "compositeWithTrainedAlgorithmicMedia",
) -> list[str]:
    """把一批蒸馏产物落进 staging。**全部 `state=proposed`，一条都不 activate。**

    `items` 每一项：

        {
          "content":    {...},                 # 蒸馏出来的文本与结构化字段
          "selector":   {...},                 # 定位到书里哪一段（W3C selector）
          "confidence": "reasonable inference",# 四档之一，必填
          "type":       "Claim",               # 可省，默认 Claim
          "uri":        "urn:...",             # 可省，默认 source_uri
          "relations":  [                      # 可省
              {"deepread": "supports", "to": 3},
              {"deepread": "causes",   "to": 5,
               "via": {"content": {...}, "selector": {...},
                       "confidence": "..."}},   # causes 必须给中间节点
          ],
        }

    ⚠️ `selector` 是**必填**。这不是为了好看 —— 它是阶段 3 的出口判据
    「每条导入节点能回答『书里哪一页』」的可执行形式。
    没有 selector 的蒸馏产物，**事后无法核对它是不是书里真有的**，
    那它就与「AI 凭空生成」无法区分了。
    """
    if not batch or not str(batch).strip():
        raise StagingError("batch 不能为空 —— 一次蒸馏一个批次号，它是可追溯的起点。")
    if not items:
        raise StagingError("一批空的蒸馏产物没有意义，不落 staging。")

    ids: list[str] = []
    for i, it in enumerate(items):
        conf = it.get("confidence")
        if conf not in CONFIDENCE_LEVELS:
            raise StagingError(
                f"第 {i} 条的 confidence={conf!r} 不在四档里：{CONFIDENCE_LEVELS}。"
                "四档是 DeepRead 的原词，逐字不改 —— 改了就跟上游对不上。"
            )
        if not isinstance(it.get("selector"), dict):
            raise StagingError(
                f"第 {i} 条没有 selector —— 蒸馏产物必须能回答「书里哪一段」。"
                "答不上来的，与 AI 凭空生成无法区分。"
            )
        content = dict(it.get("content") or {})
        content["confidence"] = conf
        content = pointer.attach(
            content,
            uri=it.get("uri") or source_uri,
            selector=it["selector"],
        )
        aid = add_artifact(
            conn, type_=it.get("type", "Claim"), content=content,
            origin=extracted_by, digital_source_type=source_type,
            asserted_by=asserted_by, intake="staged",
        )
        _record(conn, aid, batch, source_uri)
        ids.append(aid)

    for i, it in enumerate(items):
        for spec in it.get("relations") or ():
            _wire(conn, ids, i, spec, batch=batch, source_uri=source_uri,
                  extracted_by=extracted_by, asserted_by=asserted_by,
                  source_type=source_type)

    conn.commit()
    return ids


def _record(conn: sqlite3.Connection, artifact_id: str, batch: str, source_uri: str) -> None:
    """给一条节点落 staging 记录。`gate_state` 一律从 `pending` 起。"""
    conn.execute(
        "INSERT INTO staging (artifact_id, batch, source_uri, gate_state, created_at)"
        " VALUES (?,?,?,?,?)",
        (artifact_id, batch, source_uri, GATE_PENDING, now()),
    )


def _wire(
    conn: sqlite3.Connection, ids: list[str], i: int, spec: dict, *,
    batch: str, source_uri: str, extracted_by: str, asserted_by: str,
    source_type: str,
) -> None:
    """把一条 DeepRead 关系落成边。`causes` 要展开，见 `CAUSAL_EXPANSION`。"""
    name = spec.get("deepread")
    mapped = map_relation(name)

    if not isinstance(spec.get("to"), int) or not (0 <= spec["to"] < len(ids)):
        raise StagingError(
            f"关系 {name!r} 的 to={spec.get('to')!r} 不是本批里的下标"
            f"（本批 {len(ids)} 条）。批内的边只许连批内节点 —— "
            "连批外的边要显式说明，不能靠下标猜。"
        )
    src, dst = ids[i], ids[spec["to"]]

    if not isinstance(mapped, tuple):
        add_relation(conn, kind=mapped, from_id=src, to_id=dst, origin=extracted_by)
        return

    # ---- 一对多的那一支：`causes` -----------------------------------------
    via = spec.get("via")
    if not isinstance(via, dict) or not isinstance(via.get("selector"), dict):
        raise StagingError(
            f"关系 {name!r} 在本仓库里要展开成 {CAUSAL_EXPANSION} 两条边，"
            "中间还要一个**因果主张节点**（`§C3.2`：Relation 是可追踪对象，"
            "所以「A 导致 B」这句话本身必须是一个能被质疑的节点）。"
            "请用 via={'content':…, 'selector':…} 给出这个节点。"
        )
    mid_content = dict(via.get("content") or {})
    mid_content["confidence"] = via.get("confidence") or "reasonable inference"
    mid_content = pointer.attach(
        mid_content, uri=via.get("uri") or source_uri, selector=via["selector"],
    )
    mid = add_artifact(
        conn, type_="Claim", content=mid_content, origin=extracted_by,
        digital_source_type=source_type, asserted_by=asserted_by, intake="staged",
    )
    _record(conn, mid, batch, source_uri)
    add_relation(conn, kind=mapped[0], from_id=src, to_id=mid, origin=extracted_by)
    add_relation(conn, kind=mapped[1], from_id=dst, to_id=mid, origin=extracted_by)


# ---------------------------------------------------------------------------
# 门
# ---------------------------------------------------------------------------

def sample_indices(n: int, percent: int) -> list[int]:
    """从 n 条里挑出要人工看的那些下标。**确定性**，不用随机数。

    为什么确定：抽检必须**可复现**。「这批抽到的那几条有问题」这句话，
    如果无法在下一次跑出同一批下标，就复盘不了 —— 是抽签出了问题，
    还是内容出了问题，分不出来。

    ⚠️ 确定性的代价，说清楚：**有人能预测抽到哪几条**，从而只在那几条上做手脚。
    这是已知弱点，不是没想到。要不要换成不可预测的抽法（带记录的可复现随机）
    是一个**待拍板项** —— 见工程稿「悬置」一节。
    """
    if n <= 0:
        return []
    if percent >= 100:
        return list(range(n))
    keep = max(1, min(n, round(n * percent / 100)))
    return sorted({i * n // keep for i in range(keep)})


def gate(
    conn: sqlite3.Connection, *, by: str, batch: str | None = None,
    full: bool | None = None, note: str | None = None, **overrides,
) -> dict:
    """按 `POLICY` 过门：把该看的挑出来标记成 `passed`，其余留 `pending`。

    ⚠️ **过门不等于确认。** 过门只是「这批可以往下走」；
    真正的 `active` 仍然要一次具名的确认动作（`promote_passed()` 走
    `scaffold.activate()` —— 全系统写 `active` 的唯一入口）。
    两者分开，是因为它们**答的不是一个问题**：
    门答「内容质量够不够」，确认答「这条算不算数」。

    「首本书全过」的判据：staging 里**只出现过这一个 source_uri**。
    理由见 `POLICY["gate_first_book_full"]` —— 首本全过是为了**校准蒸馏器**。
    """
    rows = _ungated(conn, batch)
    if not rows:
        return {"passed": [], "held": [], "note": "没有待过门的条目。"}

    if full is None:
        first_book = _distinct_sources(conn) <= 1
        full = bool(policy.value("gate_first_book_full", **overrides)) and first_book

    if full:
        idx = list(range(len(rows)))
    else:
        idx = sample_indices(len(rows), int(policy.value("gate_sample_percent", **overrides)))

    chosen = set(idx)
    passed = [rows[i]["artifact_id"] for i in idx]
    held = [r["artifact_id"] for i, r in enumerate(rows) if i not in chosen]

    for aid in passed:
        conn.execute(
            "UPDATE staging SET gate_state = ?, gate_by = ?, gate_note = ?"
            " WHERE artifact_id = ?",
            (GATE_PASSED, by, note, aid),
        )
    conn.commit()
    return {
        "passed": passed,
        "held": held,
        "full": full,
        "note": (
            f"过门 {len(passed)} 条，留 {len(held)} 条待看。"
            + ("（首本全过：目的是校准蒸馏器，不是防污染）" if full else "")
        ),
    }


def _ungated(conn: sqlite3.Connection, batch: str | None) -> list[sqlite3.Row]:
    """还没过门、也还没被拒的条目。按落库顺序 —— 不是按任何名次。"""
    sql = ("SELECT artifact_id, batch FROM staging WHERE gate_state = ?")
    args: list = [GATE_PENDING]
    if batch is not None:
        sql += " AND batch = ?"
        args.append(batch)
    sql += " ORDER BY rowid"
    return conn.execute(sql, args).fetchall()


def _distinct_sources(conn: sqlite3.Connection) -> int:
    """staging 里出现过几本书。用来判「这是不是第一本」。"""
    row = conn.execute("SELECT COUNT(DISTINCT source_uri) AS n FROM staging").fetchone()
    return int(row["n"])


def gate_state_of(conn: sqlite3.Connection, artifact_id: str) -> str | None:
    """这条节点的门走到哪了。不在 staging 里就返回 `None`（= 不是导入来的）。"""
    row = conn.execute(
        "SELECT gate_state FROM staging WHERE artifact_id = ?", (artifact_id,)
    ).fetchone()
    return row["gate_state"] if row else None


def promote_passed(
    conn: sqlite3.Connection, *, by: str, batch: str | None = None,
) -> list[str]:
    """把**已过门**的导入节点确认为 `active`。走的是 `scaffold.activate()`。

    ⚠️ 刻意复用 `activate()` 而**不另开一条写 active 的路**：
    全系统写 `active` 只有那一个入口，而 `activate()` 自己会检查门
    （`intake='staged'` 且没过门就拒）。所以就算有人绕过本函数直接调
    `activate()`，也过不去 —— 门是挡在入口上的，不是挡在这里的。
    """
    sql = "SELECT artifact_id FROM staging WHERE gate_state = ?"
    args: list = [GATE_PASSED]
    if batch is not None:
        sql += " AND batch = ?"
        args.append(batch)
    done = []
    for row in conn.execute(sql + " ORDER BY rowid", args).fetchall():
        aid = row["artifact_id"]
        if conn.execute("SELECT state FROM artifact WHERE id = ?", (aid,)).fetchone()["state"] != "proposed":
            continue
        activate(conn, aid, by=by)
        done.append(aid)
    return done


def reject_batch(conn: sqlite3.Connection, *, batch: str, by: str, why: str) -> int:
    """整批拒掉。**不删节点** —— 只把门标成 `rejected`。

    为什么不删：这批节点可能已经被社区层引用过（`quoted_from`）。
    删了就把社区层的边撕开了。标记成 `rejected` 之后它们**进不了 active**，
    但「这里曾经有过一批被拒的东西」这条事实留下来了 —— 那是 `§C10` 的
    「记录，不停止，不限制」在这一层的形态。
    """
    cur = conn.execute(
        "UPDATE staging SET gate_state = 'rejected', gate_by = ?, gate_note = ?"
        " WHERE batch = ? AND gate_state = ?",
        (by, why, batch, GATE_PENDING),
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# 可整层重建 —— 前提的可执行形式
# ---------------------------------------------------------------------------

def batch_ids(conn: sqlite3.Connection, batch: str) -> list[str]:
    return [
        r["artifact_id"]
        for r in conn.execute(
            "SELECT artifact_id FROM staging WHERE batch = ? ORDER BY rowid", (batch,)
        ).fetchall()
    ]


def orphan_edges(conn: sqlite3.Connection, batch: str) -> list[dict]:
    """**社区层指进这一批的边**。有它们，整层重建就会撕开社区层。

    这是「导入层可整层重建」这句话的**前提条件**，也是它的可执行形式：

        没有 orphan edge  → 删掉这一批，社区层逐字段一致 → 可整层重建
        有 orphan edge    → 重建会改变社区层 → **不许自动做，要人来看**

    ⚠️ 注意方向：这里找的是「**外面指进来**」的边。
    批**内部**的边（导入节点之间）不算 —— 它们跟着批一起走。
    """
    ids = batch_ids(conn, batch)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, kind, from_id, to_id, origin, state FROM relation"
        f" WHERE (from_id IN ({marks}) AND to_id NOT IN ({marks}))"
        f"    OR (to_id   IN ({marks}) AND from_id NOT IN ({marks}))"
        f" ORDER BY id",
        (*ids, *ids, *ids, *ids),
    ).fetchall()
    return [dict(r) for r in rows]


def layer_snapshot(conn: sqlite3.Connection, *, intake: str) -> dict:
    """一层的完整快照（对象 + 版本 + 层内边）。给「重建后逐字段一致」做比对用。

    只收**层内**的边（两端都在本层）—— 跨层的边属于「两个层之间的关系」，
    它变了不说明本层变了。
    """
    arts = conn.execute(
        "SELECT id, type, state, status, origin, digital_source_type, asserted_by,"
        "       intake, created_at FROM artifact WHERE intake = ? ORDER BY id",
        (intake,),
    ).fetchall()
    ids = [a["id"] for a in arts]
    revs, rels = [], []
    if ids:
        marks = ",".join("?" * len(ids))
        revs = conn.execute(
            f"SELECT id, artifact_id, parent_rev, content, author, created_at"
            f" FROM revision WHERE artifact_id IN ({marks}) ORDER BY id", ids,
        ).fetchall()
        rels = conn.execute(
            f"SELECT id, kind, from_id, to_id, origin, state, superseded_by, created_at"
            f" FROM relation WHERE from_id IN ({marks}) AND to_id IN ({marks}) ORDER BY id",
            (*ids, *ids),
        ).fetchall()
    return {
        "artifacts": [dict(a) for a in arts],
        "revisions": [dict(r) for r in revs],
        "relations": [dict(r) for r in rels],
    }


def clear_batch(conn: sqlite3.Connection, *, batch: str, force: bool = False) -> dict:
    """删掉一整批（可整层重建的第一半）。

    ⚠️ 有 `orphan_edges` 时**默认拒绝**。理由：那些边在**社区层**里，
    删掉导入节点会把它们撕开 —— 而社区层是不可再生的，`§C10` 只增不改。
    所以这件事不许自动发生，要人看过那份边清单再决定（`force=True`）。

    删的范围：这一批的 relation（两端都在批内的）→ revision → staging → artifact。
    **社区层的对象一个都不碰。**

    ⚠️ `force=True` 时会**连那些社区边一起删** —— 因为 `relation.to_id` 有外键，
    不删就删不掉节点（SQLite 会直接拒绝）。所以 `force` 不是「忽略警告」，
    是「**接受社区层被撕开**」：返回值的 `orphans` 就是被撕掉的那份清单，
    调用方有义务把它交给一个真人看。社区层不可再生（`§C10` 只增不改），
    所以这份清单是这次操作唯一留下的痕迹 —— 别把它丢掉。
    """
    ids = batch_ids(conn, batch)
    if not ids:
        return {"removed": 0, "orphans": []}
    orphans = orphan_edges(conn, batch)
    if orphans and not force:
        raise StagingError(
            f"批 {batch} 有 {len(orphans)} 条边从社区层指进来，"
            "删掉这批会把它们撕开。整层重建的**前提**是没有这种边 —— "
            "要强删请显式 force=True，并先看一遍 orphan_edges() 的清单。"
        )
    marks = ",".join("?" * len(ids))
    if orphans:
        # 外键逼着必须一起删。删之前已经抄下来了（就是 orphans）。
        conn.execute(
            f"DELETE FROM relation WHERE id IN ({','.join('?' * len(orphans))})",
            [o["id"] for o in orphans],
        )
    conn.execute(
        f"DELETE FROM relation WHERE from_id IN ({marks}) AND to_id IN ({marks})",
        (*ids, *ids),
    )
    conn.execute(f"DELETE FROM revision WHERE artifact_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM staging  WHERE artifact_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM artifact WHERE id IN ({marks})", ids)
    conn.commit()
    return {"removed": len(ids), "orphans": orphans}


__all__ = [
    "CONFIDENCE_LEVELS", "DEEPRED_RELATIONS", "CAUSAL_EXPANSION",
    "DEEPRED_RELATION_MAP", "UNMAPPED_DEEPRED", "GATE_STATES",
    "StagingError", "map_relation", "stage_batch", "sample_indices",
    "gate", "gate_state_of", "promote_passed", "reject_batch",
    "batch_ids", "orphan_edges", "layer_snapshot", "clear_batch",
]
