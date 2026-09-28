"""结构规则集 —— 设计稿第 10 节 `system validation` 的落地形态（H5 · B 档）。

设计稿说 promote 可以由「human / system validation」触发，**却没说
`system validation` 是什么**。若它是「模型觉得可以」，那 B14 那条不变量
（上层不读热度类信号）就没了 —— 所以它一直是本仓库里一个**明摆着的空洞**。

本模块把那个洞填成**一个声明式的、只读的结构规则集**：

    规则  =  若干「已有结构信号」的**合取**
    判定  =  「哪些目标满足了哪条规则」—— 一张候选清单

--- 允许什么形态的判据（H5 · B 档，需求方 2026-09-28 拍板）-----------------

| 档 | 判据长什么样 | 取 / 舍 |
|---|---|---|
| A · 只数次数 | 「同一条依据至少 2 条」（现状 `MIN_SUPPORT`） | 太窄 —— 一次只能说一个量 |
| **B · 加结构形状** | 「被反复质询」**且**「被反复修正」 | **取这一档** |
| C · 引入 grounded 语义 | 从攻击图算接受集 | 越线 —— 系统开始当裁判 |

B 档相对 A 档的增量是**合取**：A 只能表达「某个量 ≥ n」，
B 能表达「两个量同时 ≥ n」。多出来的不是算法，是**能不能把两个已知事实并起来说**。

（调研过的两族成熟方案 —— 形式论辩语义 / 真值维护 —— 答的是另一个问题：
「哪些说法站得住」。那是 C 档，见 `参考方案调研-蒸馏与来源分离.md` 第十节。）

--- B 档与 C 档的分界线（可执行的一条）---------------------------------------

**规则的输出只能是「进不进清单」，不得是「有多好」。**

    合取    →  可解释（哪一条不满足，指得出来）
    加权和  →  不可解释，而且必然引入一个标量 → 撞 B5

所以 `all_of` 是**元组**、`OPERATORS` 只有比较符、**没有**算术组合。
一旦有人往规则里加 `+` / `*` / 平均，它就从「结构判据」变成了「打分」。
B22 盯着这条。

--- 为什么它落在 `§C2.5` 第 3 档，不是第 2 档 --------------------------------

第 2 档（**派生标注**）**对已有内容下了断言** —— 「这条边成立」。
那要求可推翻 + 抽样审计 + 计改判率。

本模块**不下断言**：它回答的是「哪些候选满足哪条声明的规则」，
产出是 `§C2.5` 第 3 档点名的四种东西之一（**推荐候选**）。

    判定被写回任何 artifact / relation 字段  →  升到第 2 档（要可推翻 + 抽检 + 计改判率）
    判定只在内存里返回、由调用方展示        →  第 3 档（默认生效 + 可追溯 + 可解释 + 可回退）

**这一条是被结构钉住的，不是被纪律钉住的**：`evaluate()` 的签名里**没有 `conn`**。
它接一个信号字典、返回一个字典 —— **没有能力写库**。
`test_rules.py` 里有一条用例跑一遍前后快照比对，把这件事验成行为。

--- 词表为什么必须和 `upper.COUNT_SIGNALS` 相等 ------------------------------

「允许系统读哪些结构量」这件事**只能有一个答案**。
`upper.COUNT_SIGNALS` 是上层归纳的驱动信号白名单，本模块的 `SIGNAL_VOCAB`
是规则的判据词表 —— 两者回答的是**同一个问题**，所以 B22 断言两者**相等**。

⚠️ 相等，不是包含：

* 多一个 → 规则集能读到一个上层白名单不许读的量，那是**越界通道**；
* 少一个 → 规则集看上去能说更多，实际读不到，那是**静默的残缺**。

同一个理由，`FLOOR` 与 `upper.MIN_SUPPORT` 也被钉成相等 ——
它们是同一个结构下限（「一条依据的簇不叫簇」），分叉就会出现
「归纳说够、规则说不够」这种没人能解释的状态。
"""

from __future__ import annotations

# 换规则必须改版本号 —— 新旧候选清单不会长得一样
# （同 `upper.INDUCER_VERSION` 的理由：留版本是 `§C2.5` 第 3 档的要求之一）。
RULES_VERSION = "structural-rules/1"

# `§C2.5` 的三档，照抄它的名字。**只为了让 `TIER` 有个可比对的封闭集合** ——
# 档位一旦挪动，要求的四件事就变了，那必须撞到 B22，不能悄悄挪。
C25_TIERS = ("must_confirm", "derived_annotation", "derived_view")

# ⚠️ **钉死在第 3 档。**
# 本模块不写库、不下断言，所以它免确认；代价是它**不许被当成结论**。
# 哪一天有人把判定写回 artifact，它就升到第 2 档 —— 那时要补
# 可推翻 + 抽样审计 + 计改判率三件事，改这里会被 B22 拦下。
TIER = "derived_view"

# 规则能读的**全部**结构量。值与 `upper.COUNT_SIGNALS` 逐字相同，B22 钉住。
#
# ⚠️ 这里**没有**票数 / 热度 / 参与量 / 曝光量 —— 不变量 #5（Popularity ≠ Evidence）。
# 也没有 `intake` / `digital_source_type` 这类**来源轴**的量：
# 按来源筛内容是一次**立场选择**（「导入的不算数」），那是产品决定，
# 不是结构判据 —— 见工程稿 11.3 的悬置项。
SIGNAL_VOCAB = {
    "challenge_counts": "relation:challenged_by",   # 某个假设被反复质询
    "revision_counts": "revision",                  # 某条关系 / 节点被反复修正
    "dispute_counts": "relation:contradicts",       # 某类争议反复出现
}

# 判据里**只许出现比较**。没有 `+` / `*` / 平均 —— 见模块开头那条分界线。
OPERATORS = (">=", "<=", "==")

# 结构下限：**不是阈值**。
#
# 阈值是「过了这条线就算重要」—— 那是个判断，`§T0.3` 不许系统定；
# 下限是「一条依据的簇不叫簇」—— 那是「这一簇是否成立」的结构问题。
# 与 `upper.MIN_SUPPORT` 是同一个下限，B22 钉住两者相等。
FLOOR = 2

# 规则集。**这是声明，不是算法** —— 改它等于改判据形态，要留痕。
#
# 每条规则两个字段：
#   all_of  若干 `(信号名, 比较符, 值)` 的**合取**（元组，不许为空）
#   why     这条规则在说什么 —— 判定清单要能自述，那是第 3 档的「可解释」
RULES = {
    # A 档也能表达的一条。**留着它，是为了让「A 是 B 的真子集」可以被指着看** ——
    # 下面两条才是 B 档多出来的。
    "repeatedly_contested": {
        "all_of": (("challenge_counts", ">=", FLOOR),),
        "why": "同一条命题被反复质询 —— 这是「争议反复出现」这条已有信号的下限形态。",
    },
    # ★ B 档相对 A 档的**增量**就在这里：合取。
    "contested_and_revised": {
        "all_of": (("challenge_counts", ">=", FLOOR),
                   ("revision_counts", ">=", FLOOR)),
        "why": ("既被反复质询、又被反复修正 —— 两个**已有**结构量同时成立。"
                "A 档写不出这一条：它一次只能说一个量。"),
    },
    "disputed_and_revised": {
        "all_of": (("dispute_counts", ">=", FLOOR),
                   ("revision_counts", ">=", FLOOR)),
        "why": "既有反复反驳、又被反复修正 —— 同一种合取，换一条信号。",
    },
}


class RuleError(Exception):
    """规则集被越界使用。**拒绝，而不是兜住** —— 兜住之后判定长得完全正常。"""


def _validate(rid: str, spec: dict) -> tuple:
    """把一条规则验成合法的合取，**不合法就抛**。

    ⚠️ 为什么这里必须抛而不是「读不到就当 0」：
    信号名打错一个字母，`signals.get(name, {})` 会给一个空字典，
    于是判定清单是空的 —— **而空清单长得跟「库里确实没有候选」一模一样**。
    那是 `observe.py`「算不出 ≠ 零」那条要求要防的同一件事：
    没测到的东西，不许长得像测到了零。
    """
    conditions = spec.get("all_of")
    if not isinstance(conditions, tuple) or not conditions:
        raise RuleError(
            f"规则 {rid!r} 的 all_of 必须是非空元组 —— "
            "单个条件会让「合取」在形状上消失，空合取则恒真（等于没有判据）。"
        )
    for cond in conditions:
        if not isinstance(cond, tuple) or len(cond) != 3:
            raise RuleError(f"规则 {rid!r} 里有个条件不是 (信号, 比较符, 值) 三元组：{cond!r}")
        name, op, want = cond
        if name not in SIGNAL_VOCAB:
            raise RuleError(
                f"规则 {rid!r} 读 {name!r}，它不在 SIGNAL_VOCAB 里。"
                f"现有：{sorted(SIGNAL_VOCAB)} —— 词表是封闭的，"
                "「允许系统读哪些结构量」只能有一个答案。"
            )
        if op not in OPERATORS:
            raise RuleError(
                f"规则 {rid!r} 用了比较符 {op!r}，它不在 {list(OPERATORS)} 里。"
            )
    return conditions


def holds(actual: int, op: str, want: int) -> bool:
    """一个条件成不成立。比较符是**封闭集合**，不认识的直接抛。"""
    if op not in OPERATORS:
        raise RuleError(
            f"比较符 {op!r} 不在 {list(OPERATORS)} 里。"
            "规则集只许比较 —— 要算加权和，那已经不是结构判据了（撞 B5）。"
        )
    if op == ">=":
        return actual >= want
    if op == "<=":
        return actual <= want
    return actual == want


def _conditions(rule: str) -> tuple:
    if rule not in RULES:
        raise RuleError(
            f"{rule!r} 不是声明过的规则。现有：{sorted(RULES)} —— "
            "规则名是**封闭集合**，加一条要改 `RULES` 并说明理由。"
        )
    return _validate(rule, RULES[rule])


def evaluate(signals: dict, *, rules: dict | None = None) -> dict:
    """对一批结构信号跑规则集。**纯函数 —— 不读库、不写库、不产生可排序的连续量。**

    `signals` 的形状就是 `upper.count_signals()` 的返回：
    `{信号名: {目标 id: 次数}}`。

    ⚠️ **签名里没有 `conn`，这是刻意的。** 判定要落在 `§C2.5` 第 3 档
    （不需确认），唯一站得住的理由就是它**没有能力写库** ——
    那不该靠「作者记得别写」来保证。

    ⚠️ 每条规则先过 `_validate()`：**信号名打错一个字母就抛，不是「读不到当 0」。**
    后者会让空清单长得跟「库里确实没有候选」一模一样。

    ⚠️ `matches` 按 `(规则名, 目标 id)` 排 —— **字母序，不是好坏序**。
    按「满足了几条」排会立刻变成名次，而规则是合取、每条要么全中要么不中，
    本来就没有「几条」这个量（见 `explain()`）。
    """
    rules = RULES if rules is None else rules
    matches = []

    for rid in sorted(rules):
        conditions = _validate(rid, rules[rid])
        # 目标集合取各条件的**并集**：取交集会漏掉「某个信号压根没有这个目标」的情况，
        # 而那正好长得跟「没有候选」一模一样。
        targets = sorted({t for name, _op, _want in conditions
                          for t in signals.get(name, {})})
        for target in targets:
            readings: dict[str, int] = {}
            ok = True
            # 不提前 break：readings 要**完整**，判定清单才解释得清（第 3 档的「可解释」）
            for name, op, want in conditions:
                got = signals.get(name, {}).get(target, 0)
                readings[name] = got
                if not holds(got, op, want):
                    ok = False
            if ok:
                matches.append({
                    "rule": rid,
                    "target": target,
                    "readings": readings,
                    "why": rules[rid]["why"],
                })

    return {
        "version": RULES_VERSION,
        "tier": TIER,
        "rules_used": {
            rid: {"all_of": [list(c) for c in rules[rid]["all_of"]],
                  "why": rules[rid]["why"]}
            for rid in sorted(rules)
        },
        "matches": matches,
        "note": (
            "这是**推荐候选**，不是结论。它一个字都没写库（`§C7.1` ④ / `§C2.5` 第 3 档），"
            "删掉零损失。它只影响「什么排在前面 / 默认展开什么」。"
        ),
    }


def explain(signals: dict, *, rule: str, target: str) -> list[dict]:
    """某个目标**为什么进得来 / 为什么进不来** —— 逐条件给。

    ⚠️ **不给「满足了几条」这个合计。** 一合计就是个分：
    「3 条里满足 2 条」听起来像 66 分，而它本来只该回答「成立 / 不成立」。
    逐条件列出来，人自己看 —— 那才叫可解释。
    """
    detail = []
    for name, op, want in _conditions(rule):
        got = signals.get(name, {}).get(target, 0)
        detail.append({
            "signal": name,
            "operator": op,
            "required": want,
            "actual": got,
            "held": holds(got, op, want),
        })
    return detail


def select(candidates: list[dict], signals: dict, *, rule: str) -> dict:
    """从 `upper.propose_clusters()` 的候选里挑出**满足某条声明规则**的那一批。

    ⚠️ **`rule` 只能是规则名，不能是函数、不能是模型输出。**
    这就是阶段 5 出口判据 ③「把模型判断当 promote 条件会被拒绝」的落地形态：
    不是「我们约定不传模型判断」，而是**传不进来** —— 唯一能传的是一个字符串，
    它必须命中 `RULES`，否则 `RuleError`。

    ⚠️ 这是**筛选**，不是提拔。一个字都没写库；
    要不要真建，仍由 `upper.promote_candidates()` 决定，且必须由人给 `by`。
    """
    if rule not in RULES:
        raise RuleError(
            f"{rule!r} 不是声明过的规则 —— promote 条件只能**按名字引用一条声明过的规则**。"
            f"现有：{sorted(RULES)}"
        )
    result = evaluate(signals, rules={rule: RULES[rule]})
    hit = {m["target"] for m in result["matches"]}
    chosen = [c for c in candidates if c.get("seed_id") in hit]
    chosen.sort(key=lambda c: str(c.get("seed_id", "")))
    return {
        "rule": rule,
        "version": RULES_VERSION,
        "tier": TIER,
        "selected": chosen,
        "note": (
            "这是**筛选**，不是提拔 —— 它只回答「哪些候选满足这条声明的规则」。"
            "要不要建，由 `upper.promote_candidates()` 决定，且必须由人给 `by`。"
        ),
    }


def render(result: dict) -> str:
    """印成人看的。只读 —— 和 `upper.render()` / `hints.scan()` 一个规矩。"""
    lines = [
        "=" * 64,
        f"结构规则集 {result['version']}（`§C2.5` 第 3 档：{result['tier']}）",
        "",
    ]
    if not result["matches"]:
        lines += ["  没有候选满足任何一条规则。", ""]
        # ⚠️ 「没有」和「算不出」不是一回事 —— 所以空结果必须自述它读了什么。
        # 这是 `observe.py`「算不出 ≠ 零」的同一条要求。
        lines.append("  ⚠️ 「没有」和「算不出」不是一回事。规则集只读下面这几个量：")
        lines += [f"     · {name}  ←  {src}" for name, src in sorted(SIGNAL_VOCAB.items())]
        lines.append("")
    for m in result["matches"]:
        lines.append(f"  [{m['rule']}] {m['target']}   读数 {m['readings']}")
        lines.append(f"      {m['why']}")
        lines.append("")
    lines += [
        "⚠️ 这是推荐候选，不是结论。它一个字都没写库，删掉零损失（`§C7.1` ④）。",
    ]
    return "\n".join(lines)


__all__ = [
    "RULES_VERSION", "C25_TIERS", "TIER", "SIGNAL_VOCAB", "OPERATORS",
    "FLOOR", "RULES", "RuleError",
    "holds", "evaluate", "explain", "select", "render",
]
