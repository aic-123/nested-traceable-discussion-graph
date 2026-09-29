"""Scaffold 最小层 —— 对象与关系（`§C3`）。

本文件只做三件事的存储与访问：**Artifact / Relation / Revision**。
Evidence 与 Challenge 不另建机制 —— 按 `§C3.3` `§C3.4`，它们是
「Artifact 的类型」+「Relation 的组合」。

--- 硬约束：违反即实现错误 -------------------------------------------------

`§C3.2`  Relation 是**可追踪对象**（有身份、有来源、可追溯），不是外键字段。
          → relation 是独立表、有自增身份、有 origin、有 state、能被 rejected。
`§C10`   禁止覆盖式更新。每个版本一行，**只增不改**。
          → revision 表没有 UPDATE 路径；并发修改自然形成分叉（多个 head）。
#3        机器产出不得直接成为 verified knowledge。
          → `verified` 只有 `grant_verified()` 一条写入路径，且必须带 hook 名。
`§C2.4`   每条 AI 产出的结构边：可追溯 / 可拒绝 / 可修改 / 可计量。
          → relation 有 origin(追溯) / state(拒绝) / superseded_by(修改) / 表本身可计量。
分层不变量 #11
          本文件**不得出现任何讨论行为**（确认、投票、质询、分裂）。
          那些属于 Arena 层。本文件不认识「用户」「投票」「分歧」这些概念。

--- 反向约束（本文件刻意不做的事）------------------------------------------

- 不排序、不聚合、不打分。任何评分 / 权重 / 名次字段都不存在（`§C6.1` `§C9` #5 #7）。
- 不加锁。`§C11.2` 要求先让真实并发把问题打出来，再决定引入哪些机制。
- 不校验内容对错。只保证结构自洽（借鉴参考实现「校验器不判内容」的立场）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

# ⚠️ 本模块 import `pointer`，方向是**单向**的：`pointer` 不 import 任何本地模块。
# 这样 `_append_revision()` 能在**序列化那一刻**验定位符，而不会有循环依赖。
# 为什么必须在这一层验：`add_artifact()` 与 `revise()` 都从这里过 ——
# 它是内容落库的**唯一漏斗**。放在这里，就没有第二条路能存进一个坏定位符。
import upgrade
import pointer

# ---------------------------------------------------------------------------
# 权威常量
# ---------------------------------------------------------------------------

# `§C3.1` 的 Artifact 类型清单，取并集 —— 见 DECLARATION.md §4「文档冲突的处理」。
# `§C3.1` 列了 7 类，但 `§C4` 的 Debate 结构树用到了 Mechanism / Assumption /
# Counterexample / Counterargument，`§C3.4` 又要求 Challenge 是 Relation + Artifact 的组合。
# 所以 `§C3.1` 当**下界**读，不当闭集读。
ARTIFACT_TYPES = (
    "Topic",           # §C3.1
    "Position",        # §C4 —— §C3.1 的枚举漏了它，见下方注释
    "Claim",           # §C3.1
    "Evidence",        # §C3.1
    "Argument",        # §C3.1
    "Debate",          # §C3.1
    "Vote",            # §C3.1
    "Subtopic",        # §C3.1
    "Mechanism",       # §C4
    "Assumption",      # §C4
    "Counterexample",  # §C4
    "Counterargument", # §C4
    "Challenge",       # §C3.4
    "Context",         # §C7.1 —— 上层节点，见下方注释
)

# ⚠️ `Context` 是**上层**节点（`§C7.1` 的 Context Scaffold），
# 与上面那些**底层**类型有一条硬边界，不能混：
#
#     底层（Topic/Position/Claim/Evidence…）  记事实。确认环节授予 active（§C2.5）
#     上层（Context）                        只派生。**不得写底层任何字段**（§C7.1 ④）
#
# 它进这张表的理由与 `Position` 不同 —— `Position` 是 `§C4` 画漏了；
# `Context` 是 `§C4` **没规定**（`§C7.1` 只说了机制，没说节点叫什么类型），
# 所以走 `§T4` 的留白项：自选 + 声明 + 可回退。它不在任何文档的枚举里，
# **这一点必须说清**，别让它看起来像 `§C3.1` 漏了一个。

# ⚠️ `Position` 不在 `§C3.1` 的枚举里，但 `§C4` 的结构树**第一行就用它**：
#
#     Topic
#     ├── Position / Claim        ← 这一行
#     │   ├── Evidence ...
#     └── Opposing Claim
#
# 与 `Mechanism` / `Assumption` / `Counterexample` / `Counterargument` 属于**同一类漏项**
# —— 那几个也不在 `§C3.1` 里，而 `§C4` 的树全用到了。既有的处理是
# 「把 `§C3.1` 当**下界**读，不当闭集读」（DECLARATION §5），此处沿用同一条，
# 不新增争议：它和那四个是同一个判决，不是一次新的放宽。
#
# `§C4` 那句「必须逐条落地，不可合并」针对的是**关系映射表**，
# 但树的形状本身也是结构要求 —— `Position` 是树上一个确定的层
# （需求方 2026-09-27 明确要求「论点一层」，并认可二元对立暂代多立场）。

# ---------------------------------------------------------------------------
# 类型分档（CONTROL_RULES · 2026-09-28）
# ---------------------------------------------------------------------------

# 上面那张 `ARTIFACT_TYPES` 只说「**有哪些**类型」，没说「**谁有权建、建的时候必须带什么**」。
# 这一张补上。四档，按控制强度从紧到松排：
#
#   fixed                受控词汇。建的时候**必须显式给出类型**，不许走默认、不许推断。
#                        为什么紧：这些都是命题与论证的结构件。类型推断错了，
#                        不是「记错了一个字段」—— 是**替人立论**（`§C2.0`）。
#   structural           只许**上层**建（`upper.py`）。见 `UPPER_TYPE`。
#                        为什么紧：它一进底层类型表就会被误当成底层的命题。
#   instance_of_required 可以建，但**必须挂在父节点上**（`contains` 边）。
#                        为什么：`Debate` / `Subtopic` 是容器，不挂父节点就不是容器，
#                        而一个悬空的容器在查询里长得跟命题一样。
#   free                 无附加约束。`Vote` 是讨论行为的记录，本来就不该被约束。
#
# ⚠️ 这张表与 `ARTIFACT_TYPES` 必须**恰好**互相覆盖（并集相等、四档两两不交）。
#    那条判据由 B19 盯着。加一个新类型却忘了分档，B19 会报 ——
#    这正是这张表存在的理由：**加类型这件事不许悄悄发生。**
CONTROL_RULES: dict[str, tuple[str, ...]] = {
    "fixed": (
        "Topic", "Position", "Claim", "Evidence", "Argument",
        "Mechanism", "Assumption", "Counterexample", "Counterargument",
        "Challenge",
    ),
    "structural": ("Context",),
    "instance_of_required": ("Debate", "Subtopic"),
    "free": ("Vote",),
}

# `Context` 只许上层建 —— 这条判据的落点是 `upper.UPPER_TYPE`。
# 在这里复述一次是为了让 B19 能读它，不必 import 上层模块（方向也不对）。
UPPER_ONLY_TYPES = CONTROL_RULES["structural"]

# 建的时候必须挂父节点的类型（`contains` 边）。
INSTANCE_OF_REQUIRED_TYPES = CONTROL_RULES["instance_of_required"]

# `§C3.2` 的六种，并上 `§C4` / `§C6.3` 要求但未列入的四种。同上，取并集。
RELATION_KINDS = (
    "contains",        # §C3.2
    "derived_from",    # §C3.2
    "supports",        # §C3.2 · §C4 · §C6.3
    "contradicts",     # §C3.2 · §C4 · §C6.3
    "refines",         # §C3.2
    "related_to",      # §C3.2 · §C8.2
    "qualifies",       # §C4 · §C6.3 —— 不在 §C3.2 的六种里
    "assumes",         # §C4  Claim → Assumption
    "explains",        # §C4  Claim → Mechanism
    "challenged_by",   # §C4  Claim → Counterargument
    # ---- 缺口①（需求方样本标注暴露，2026-09-25）----------------------------
    # 「A 导致 B」这个命题与它两端的关系。原来没有种类，`confirm.py` 拿
    # `related_to` 兜住 —— 而 `related_to` 恰恰**丢失因果方向**，等于没表达。
    # 文件里写的是「需要**一类**…请定名」。这里给了**两种**，理由：
    # 一种关系做不到「同一端既可能是因、又可能是果」都要能说
    #   B1  n1（强度大）是 n3 的**因**
    #   B4  n1（成绩好）是 n2 的**果**
    # 方向是这一族关系唯一的载荷，一种就等于退回 `related_to`。
    # 方向照文件自己的箭头：**端 → 因果主张**（B1 写的是 `n1 ─?→ n3`）。
    # 名称用文件给的两个词：前提 / 结论。
    "causal_premise",    # 缺口①  因 → 因果主张（「这条是该因果主张的前提」）
    "causal_conclusion", # 缺口①  果 → 因果主张
    # ---- `§C7.1` 上层（Context Scaffold）—— 需求方 2026-09-27 ----------------
    # ⚠️ **这一条与前两种性质不同，必须分清**：前两种是底层边（记事实），
    # 这一条是**上层边**（只派生）。它不是底层的真值边 —— 整批删掉，
    # 底层一字不少。`§C7.1` ④ 的单向性约束就落在这条边不许反向。
    #
    # 名字取「被并进某个上下文」的意思，不取「相似于」：
    # 「相似」是一种距离判断，而本层只用结构量（见 upper.py 模块开头）。
    "clustered_into",    # §C7.1  底层节点 → 上层 Context
    # ---- 引用与原始来源（PROV-O 对齐，2026-09-28）----------------------------
    # 这两条**不在任何现有条款里** —— `§C3.2` 的六种、`§C4` 的四种都没有它们。
    # 按 `§T4` 走**留白项**：自选 + 声明 + 可回退。
    #
    # 为什么需要：书籍蒸馏会把大量**外部已有的论证**引进来，而「引用了它」
    # 和「由它推导出」是两件事。PROV-O 已经给了标准答案，不自造：
    #
    #     wasQuotedFrom        ⊑  wasDerivedFrom      ← 引用是派生的**弱特化**
    #     hadPrimarySource     ⊑  wasDerivedFrom      ← 原始来源是最强的**一档**
    #
    # ⚠️ 关键：`quoted_from` 与 `had_primary_source` **不是互斥的两个选项**，
    # 是**同一族里强弱不同的两档**。所以「能不能当原始来源用」的判据
    # 只读 `had_primary_source`，**不读 `quoted_from`** ——
    # 见 `PRIMARY_SOURCE_KIND` 与 `check_primary_source_is_not_a_quotation`。
    "quoted_from",        # PROV-O wasQuotedFrom      —— 引用了某个外部对象
    "had_primary_source", # PROV-O hadPrimarySource   —— 可当原始来源用
    # ---- 例示与对照（2026-09-28 · 需求方拍板）--------------------------------
    # 这两条**不是设计稿里的**，是 DeepRead 八种关系搬过来时暴露的缺口。
    #
    # 起因：「其他问题你先找有没有可以参考的成熟方案，不拍脑袋决定」——
    # 蒸馏关系映射表里 `exemplifies` / `contrasts` 两格填的是 `None`，
    # 即「本仓库没有位置放，停下来问人」。需求方拍板：**补这两个种类**。
    #
    # 为什么不并进现有的：
    #   exemplifies  `refines` 是「更细」，不是「是一个实例」；
    #   contrasts    `contradicts` 太强（对照恰恰**不**矛盾）、
    #                `qualifies` 不对（限定是缩小适用范围，不是并置比较）。
    # 并进去不是「近似」，是**把这两种关系从库里抹掉** ——
    # 而抹掉之后那条边在库里长得完全正常。
    #
    # ⚠️ 两条都**不进** `RELATION_PARENTS`。理由：那张表编码的是
    # **派生族**（PROV-O 对齐：quoted_from / had_primary_source ⊑ derived_from）。
    # 这两条是**论证关系**，不是派生的特化 —— 给它们编一个父类
    # 是**发明层级**，而层级一旦编错，B17 那条判据就跟着错。
    "exemplifies",        # 「这条是那条的一个例子」  方向：例子 → 被例示者
    "contrasts",          # 「这条与那条构成对照」    方向：并置比较，不判高下
)

# 关系种类的**层**（2026-09-28 加）。
#
# 分的是**写权限轴**（底层 / 上层）—— 不是 `intake` 那条**来源轴**
# （社区 / 导入）。两条轴正交，见工程稿「两条轴必须分开命名」。
#
# 为什么要有它：`clustered_into` 的注释里早就写了「这一条与前两种性质不同，
# 必须分清」—— 那是一句**没有可执行形式**的话。加 `exemplifies` / `contrasts`
# 时顺手把它落成结构，否则「加一种边、忘了它是哪一层」这件事会一直悄悄发生。
#
# ⚠️ `upper` 那一档只有一条，而且**应当一直只有一条**：
# 上层对底层的影响面越小，「单向性」越容易被守住。
# 哪天上层需要第二条边，那是**设计变更**，不是顺手加一条。
RELATION_LAYERS: dict[str, tuple[str, ...]] = {
    # 底层真值边：记事实。删掉任何一条，底层就少一个事实。
    "lower": (
        "contains", "derived_from", "supports", "contradicts", "refines",
        "related_to", "qualifies", "assumes", "explains", "challenged_by",
        "causal_premise", "causal_conclusion",
        "quoted_from", "had_primary_source",
        "exemplifies", "contrasts",
    ),
    # 上层派生边：只表达「这一簇属于这个候选上下文」。
    # **整批删掉，底层一字不少** —— 这就是单向性约束的可执行形式。
    "upper": ("clustered_into",),
}

# 关系层级的**父类表**（2026-09-28 定）。
#
# 为什么是常量而不是一张表 —— 五条理由，按分量排：
#   1. **一致性**：`ARTIFACT_TYPES` / `RELATION_KINDS` / `ARTIFACT_STATES` /
#      `RELATION_STATES` / `COUNT_SIGNALS` **全是代码常量**。只有层级进表，
#      就会出现「同一类东西两种存法」。
#   2. **改动留痕**：改常量走 git diff —— 谁改的、什么时候、为什么，全在提交历史里。
#      改表则不留痕，除非再写一条 event，**而「记录改表的动作」本身又要写表**。
#   3. **少一条写路径**：表是可写的，存在一张可写的层级表，就存在一条
#      「谁都能改层级」的路径。常量不是运行时状态，**AI 没有路径改它** ——
#      这与 B14 的精神一致：不变量越少依赖运行时状态，越稳。
#   4. **规模不匹配**：层级只有 3 条、2 层。用递归 CTE 查这个，是过度设计。
#   5. **B17 更简单**：`dict.get()` 一层，不需要递归 CTE 与环检测。
#
# 唯一会让它变成表的条件：关系种类膨胀到几十种**且**需要运行时改层级。
# 但即便到那时，**层级变更应该是一次有记录的设计变更，不是运行时数据** ——
# 所以这条条件基本不会出现。
RELATION_PARENTS = {
    "quoted_from":        "derived_from",
    "had_primary_source": "derived_from",
}

# 「能不能当**原始来源**用」的**唯一**判据。
#
# ⚠️ 不是 `quoted_from` —— 引用是派生的弱特化，**不等于**原始来源。
# 这个区分就是 `§T4` 留白项里「引用不许自动升级成知识来源」那句的可执行形式。
PRIMARY_SOURCE_KIND = "had_primary_source"

# Artifact 生命周期（`§C3.1`「具有独立身份、状态、生命周期」）。
# 唯一能写 active 的路径是 Arena 层的用户确认 —— 见 arena/debate.py。
ARTIFACT_STATES = ("proposed", "active", "superseded")

# 「**从哪个口进来的**」（2026-09-28）。
#
# 为什么需要它，而不是从 `digital_source_type` 推：
# 那两个问题**不是一回事**。`upper.py` 建的 Context 是 `trainedAlgorithmicMedia`
# （完全由 AI 生成），而蒸馏某本书产出的 AI 归纳**也是** `trainedAlgorithmicMedia` ——
# 来源档位一模一样，**层级却不同**：前者是上层派生，后者是导入层。
# 用来源档推层级，必然把这两者混成一个。
#
# 于是单开一栏，各答一个问题：
#   digital_source_type   内容**怎么产生的**（IPTC 词表）
#   intake                **从哪个口进来的**（本表）
#   state                 现在**处在什么生命周期**
INTAKE_CHANNELS = (
    "direct",   # 直接写入：社区贡献、上层 Context、以及一切非蒸馏来源
    "staged",   # 经**入层门**进来：书籍蒸馏等批量导入
)

# 门的三个状态。`passed` 之外都不许 activate —— 见 `activate()` 的守卫。
GATE_STATES = ("pending", "passed", "rejected")
GATE_PENDING = "pending"
GATE_PASSED = "passed"

# Relation 状态（`§C2.4` 可拒绝 / 可修改）。
#
# ⚠️ 四个状态**不是一个「有效 / 无效」的开关** —— 它们答的是同一个问题
# （这条边算不算数），但**不算数的理由各不相同**：
#
#   proposed     还没被确认  —— 未来可能算数（阶段 5b 的候选边）
#   active       算数
#   rejected     被推翻了    —— 已判定不算数
#   superseded   被取代了    —— 有后继者替它算数
#
# 压成一档会丢掉「为什么不算数」，而事后追责时「还没确认」与「已被推翻」
# 是两件完全不同的事。
#
# 机器产出的边默认 `active` —— 这是 `§C2.5` 的「派生标注：默认生效 + 可推翻」，
# **不是**「默认正确」。它随时可以被 rejected，改判率就是从这里算的。
#
# ⚠️ 与 `ARTIFACT_STATES` 的差别是**刻意的**，不是漏了：节点有 `proposed`，
# 边原本没有 —— 于是「一条边在被确认之前」这种状态**根本表达不出来**，
# 而那就是阶段 5b 之前「候选关系」落不了地的原因。2026-09-28 补上。
RELATION_STATES = ("proposed", "active", "rejected", "superseded")

# ★ 上面四个状态里，**哪些算数** —— 即进 `upper.count_signals()` 的读数。
#
# ⚠️ 只有 `active`。另外三个一律不算数，且理由各不相同（见上表）。
#
# 为什么要把「算数」单列成一张表：`count_signals()` 与 `_supporting_ids()` 里
# 那句 `r.state = 'active'` 是一个**被抄了两遍的字面量**。抄漏一处，
# 候选边就会悄悄进读数 —— 而库里看不出任何异常（边有 id、能查、长得正常）。
# 那张表把「算数」变成一个有名字的声明，B24 再把它与 `RELATION_STATES`
# 的关系钉死（两两不交、并集相等），并钉住 `CANDIDATE_STATE` 不在里面。
COUNTED_RELATION_STATES = ("active",)

# Evidence 状态（`§C12.2` 的 Evidence Status 一栏）。
# `§C9` #3：AI 生成内容不得直接成为 verified knowledge。
STATUS_DEFAULT = "unresolved"
STATUS_HOOK_ONLY = "verified"

# ---------------------------------------------------------------------------
# 来源分档（2026-09-28）
# ---------------------------------------------------------------------------

# 「这条内容**怎么产生的**」—— 抄 IPTC `digitalSourceType` 受控词表，**逐字不改**。
#
# 为什么不自造：原先只有 `BOOK` / `COMMUNITY` 两档，但 **AI 转录的书归哪一档答不上来** ——
# 填 `BOOK` 就把 AI 的转述伪装成书籍原文（两档方案防住了「伪装成社区」，没防住这个）。
# IPTC 的词表恰好有**天然的一对**：
#
#     compositeWithTrainedAlgorithmicMedia   用生成式 AI **编辑 / 转换**过
#     trainedAlgorithmicMedia                完全由生成式 AI **生成**
#
# 这一对正是本系统最需要的那条界线。词表由 IPTC 维护、C2PA 已采纳 ——
# 引用它比自己发明一套更省事，也更容易被别人认出来。
#
# ⚠️ 这里**只取本系统用得上的四档**，不是词表全集（词表还有 negativeFilm /
# print / screenCapture 等二十来个媒体场景的词，与本系统无关）。
# 取子集是刻意的：**判据要窄**，宽了就会有人拿无关档位糊弄过去。
DIGITAL_SOURCE_TYPES = (
    "digitalCreation",                        # 人用**非生成式**工具创建
    "digitalCapture",                         # 从真实来源采集（书里的原文摘录）
    "compositeWithTrainedAlgorithmicMedia",   # 用生成式 AI 编辑过（**AI 转录的书**）
    "trainedAlgorithmicMedia",                # 完全由生成式 AI 生成（**AI 归纳的 Context**）
)

# 默认档位：`add_artifact` 是**底层写原语**，调用方是「记一条事实」，默认取人创建。
# AI 产出**必须显式声明** —— 这符合「AI 产出不得伪装成人」的方向。
DIGITAL_SOURCE_DEFAULT = "digitalCreation"

# 哪几档是 **AI 产出**。判据是「**必须**写明谁主张的」（见 `add_artifact` 的守卫）。
#
# 理由：AI 转录的东西，它的主张者**必须被指明** —— 是书作者，还是 AI 自己。
# 留空就等于**系统替它立论**，而那正是 `§C2.0` 与上层「命名归人」同一条禁令。
AI_SOURCES = (
    "compositeWithTrainedAlgorithmicMedia",
    "trainedAlgorithmicMedia",
)

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _now() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def now() -> str:
    """当前时间戳（`TS_FORMAT`）。

    公开给同仓库的其他层用 —— **时间格式只在这一处定义**。
    别处自己 `strftime` 一份，就会出现两种格式，而它们在字符串排序下
    恰好也能排，于是这件事直到跨年才会暴露。
    """
    return _now()


class ScaffoldError(Exception):
    """结构层拒绝了一次写入。消息面向调用方，说明违反了哪一条。"""


# ---------------------------------------------------------------------------
# 建库
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact (
    id          TEXT PRIMARY KEY,          -- 独立身份（§C3.1）
    type        TEXT NOT NULL,             -- §C3.1 类型清单
    state       TEXT NOT NULL,             -- 生命周期
    status      TEXT NOT NULL,             -- Evidence 状态；默认 unresolved
    origin      TEXT NOT NULL,             -- 产生者，可追溯（§C2.4 / #9）
    -- ---- 来源与归因（2026-09-28）----------------------------------------
    -- 三栏各答一个问题，**合成一个必然丢一个**：
    --   digital_source_type  内容**怎么产生的**     （IPTC 词表，见上）
    --   asserted_by          **谁主张的**           （PROV-O wasAttributedTo）
    --   origin               **谁产生这条记录的**   （PROV-O wasGeneratedBy）
    --
    -- 书籍场景三栏各不相同：类型 = AI 转录、主张者 = 书作者、记录者 = 转录者。
    -- 压成一栏就再也答不上「这条到底是书说的，还是 AI 说书说的」。
    digital_source_type TEXT NOT NULL DEFAULT 'digitalCreation',
    asserted_by TEXT,                      -- 人档默认取 origin；AI 档必填（见 add_artifact）
    -- ---- 入层口（2026-09-28）--------------------------------------------
    -- 「从哪个口进来的」。与 `digital_source_type` **不是一回事** —— 见常量区。
    -- ⚠️ `staged` 的节点在过门之前**不许 activate**（见 activate() 的守卫）。
    intake      TEXT NOT NULL DEFAULT 'direct',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revision (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    parent_rev  INTEGER,                   -- NULL=根；并发修改产生多个子 → 分叉（§C10）
    content     TEXT NOT NULL,             -- JSON：规范化文本 + 结构化字段
    author      TEXT NOT NULL,             -- 谁改的（§C7.2「谁改了什么」）
    created_at  TEXT NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifact(id)
);

CREATE TABLE IF NOT EXISTS relation (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,           -- §C3.2 的六种 ∪ §C4 的四种
    from_id       TEXT NOT NULL,
    to_id         TEXT NOT NULL,
    origin        TEXT NOT NULL,           -- 可追溯：这条边是谁产的
    state         TEXT NOT NULL,           -- 生命周期：proposed / active / rejected / superseded
    superseded_by INTEGER,                 -- 可修改：被哪条 relation 取代
    created_at    TEXT NOT NULL,
    FOREIGN KEY (from_id) REFERENCES artifact(id),
    FOREIGN KEY (to_id)   REFERENCES artifact(id)
);

-- 观测点的事实记录（`§C7.2`）。
-- ⚠️ 这里只记**事实**（谁、何时、做了什么），不记任何评估结果。
-- 「换说法率」「改判率」等比率是**上层派生视图**，从这张表算出来，
-- **不得**反向写成 artifact 的字段 —— 那是 §C7.1 ④ 禁止的单向性违反。
CREATE TABLE IF NOT EXISTS event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    actor       TEXT NOT NULL,
    subject_id  TEXT,
    payload     TEXT NOT NULL,             -- JSON
    created_at  TEXT NOT NULL
);

-- 身份计数器（`§T2` 第 12 步引入）。
-- 为什么是**一张表**而不是加锁：见 `next_seq()` 的说明。
-- 它只存「发到几号了」这一个数，不参与任何讨论语义 —— 所以它属于 Scaffold 层。
CREATE TABLE IF NOT EXISTS seq (
    name        TEXT PRIMARY KEY,
    n           INTEGER NOT NULL
);

-- 入层门（`§T4` 留白项 · 2026-09-28）。
--
-- 一次蒸馏 = 一个 `batch`。同一本书重蒸会拿到新的 batch，**旧的不会被覆盖** ——
-- 于是「这本书蒸过几轮、每轮过没过门」是一条可查的事实，不是记忆。
--
-- ⚠️ 这张表**不是**「导入层的内容」—— 内容在 artifact 里。
-- 它只记「这一条是从哪个口进来的、门过没过」。所以它答不了
-- 「这条命题说了什么」，只答得了「这条命题能不能算数」。
CREATE TABLE IF NOT EXISTS staging (
    artifact_id TEXT PRIMARY KEY,
    batch       TEXT NOT NULL,             -- 一次蒸馏一个批次号
    source_uri  TEXT NOT NULL,             -- 蒸的是哪本书（绝对 URI）
    gate_state  TEXT NOT NULL,             -- pending / passed / rejected
    gate_by     TEXT,                      -- 谁过的门；未过为 NULL
    gate_note   TEXT,                      -- 过门时的一句话，可空
    created_at  TEXT NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifact(id)
);

CREATE INDEX IF NOT EXISTS idx_staging_batch ON staging(batch);
CREATE INDEX IF NOT EXISTS idx_staging_gate  ON staging(gate_state);

CREATE INDEX IF NOT EXISTS idx_rev_artifact ON revision(artifact_id);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relation(from_id);
CREATE INDEX IF NOT EXISTS idx_rel_to   ON relation(to_id);
CREATE INDEX IF NOT EXISTS idx_event_kind ON event(kind);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    """建库到**最新版本**。

    `SCHEMA` 建出来的是**基线形状**（v1）；基线之后还有迁移的话，
    由 `upgrade.to_latest()` 接着往上跑。

    新库与「迁移机制引入之前的老库」走的是同一条路 —— 两者形状相同，
    因为 `SCHEMA` 里全是 `CREATE TABLE IF NOT EXISTS`。所以这里不需要
    分支判断「这是新库还是老库」：`user_version` 是 0 就标记基线，是几就从几往上跑。
    """
    conn.executescript(SCHEMA)
    conn.commit()
    upgrade.to_latest(conn)


# ---------------------------------------------------------------------------
# 身份
# ---------------------------------------------------------------------------

_PREFIX = {
    "Topic": "topic", "Position": "pos", "Claim": "claim", "Evidence": "evid",
    "Argument": "arg", "Debate": "debate", "Vote": "vote",
    "Subtopic": "sub", "Mechanism": "mech", "Assumption": "assume",
    "Counterexample": "cex", "Counterargument": "carg", "Challenge": "chal",
    "Context": "ctx",      # §C7.1 上层节点 —— 前缀独立，一眼分出上下层
}


def next_seq(conn: sqlite3.Connection, name: str) -> int:
    """**原子地**取下一个序号。全系统发身份只有这一条路。

    --- 为什么有它（`§T2` 第 12 步：先暴露，再引入机制）--------------------

    原来的写法是「**数一行数，再拼一个名字**」：`SELECT COUNT(*)` → `f"{p}-{n+1}"`。
    那是**两步**，两个进程会在中间那段缝里算出同一个 id：
    一个写成功，另一个 `IntegrityError: UNIQUE constraint failed`。
    不是缝有多宽的问题 —— 这个写法**在任何宽度下都是错的**。

    --- 换掉的是写法，不是加了把锁 -----------------------------------------

    `UPDATE ... RETURNING` 由 SQLite 自己保证「读-改-写」不可分割，
    所以那条缝**根本不存在**了，而不是被锁盖住。
    于是 `§C11.3` 禁止的分布式锁、消息队列、Redis 一个都没引，零新依赖。

    这也正是判据表要的顺序：**先让真并发把它打出来（第 10/11 步），
    再引入成熟方案（第 12 步）**。没有第 11 步那条记录，这个改动就是凭空的。

    --- 两点行为变化，明说 -------------------------------------------------

    1. **号是「领」走的，不是「数」出来的**：调一次就前进一格，
       领了不用就留个空号。以前是纯函数式的（同状态给同一个答案），
       现在不是 —— 这是它换来的原子性的代价，也是它唯一可能的形态。
    2. **并发下的编号不再可预测**（谁先领谁拿小号）。
       编号本来就只是身份，不是顺序也不是名次（`§C9` #5），所以这不损失什么；
       但**顺序跑仍然是逐号递增的**，从空库跑到链尾依旧可复现。

    3. ⚠️ **领号会开一个写事务，调用方必须提交。**
       这是第 14 步加压时才发现的一条**新契约**，而它现在**没有任何地方强制**：
       老写法是纯 `SELECT`，只拿读锁、谁都不挡；新写法拿的是**写锁**，
       所以在「领了号」到「commit」之间，**整个库别的写者都进不来**。
       真实调用路径（`add_artifact` / `_insert_draft` → `propose`）都是领完马上提交，
       实测 8 进程 × 12 轮零异常；但**如果将来有人在 `new_id()` 之后忘了提交，
       症状会是别人那边 `database is locked`**，而且看起来跟发号器毫无关系。
       这条契约只在文档里，是靠不住的 —— 记在这里，先不当成已解决。

    `INSERT OR IGNORE` 只用来播种一行计数器：名字是主键、值恒为 0，
    所以「忽略冲突」丢不掉任何数据 —— 它只会跳过重复播种。
    """
    conn.execute("INSERT OR IGNORE INTO seq (name, n) VALUES (?, 0)", (name,))
    row = conn.execute(
        "UPDATE seq SET n = n + 1 WHERE name = ? RETURNING n", (name,)
    ).fetchone()
    return int(row["n"])


def new_id(conn: sqlite3.Connection, type_: str) -> str:
    """`前缀-四位序号`。同一类型内递增，从空库跑到链尾时可复现。

    序号怎么来的见 `next_seq()` —— 那是这一步唯一改掉的东西。
    **对外的样子一个字没变**：`claim-0001` 还是 `claim-0001`。
    """
    if type_ not in ARTIFACT_TYPES:
        raise ScaffoldError(f"未知 Artifact 类型：{type_}（见 §C3.1 ∪ §C4）")
    return f"{_PREFIX[type_]}-{next_seq(conn, type_):04d}"


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

def add_artifact(
    conn: sqlite3.Connection,
    *,
    type_: str,
    content: dict,
    origin: str,
    state: str = "proposed",
    digital_source_type: str = DIGITAL_SOURCE_DEFAULT,
    asserted_by: str | None = None,
    intake: str = "direct",
) -> str:
    """建一个 Artifact，并落它的第 1 个 revision。

    `state` 默认 `proposed` —— 机器产出的东西先落这里。
    转 `active` 只有 Arena 层的用户确认那一条路（`§C5`：未经确认不得写入 Scaffold）。

    --- 来源与归因（2026-09-28）-----------------------------------------------

    `digital_source_type` 说**内容怎么产生的**；`asserted_by` 说**谁主张的**；
    `origin` 说**谁产生这条记录的**。三栏各答一个问题。

    两条守卫：

    1. **AI 档位必须写明 `asserted_by`。** AI 转录 / 生成的东西，它的主张者
       必须被指明（书作者，或 AI 自己）。留空就等于**系统替它立论** ——
       和上层「名字由人给」（B15）是同一条禁令，只是这里管的是「谁说的」。
    2. `asserted_by` 缺省时**取 `origin`**。人档下这两者本来就是同一个 ——
       用户提的主张就是用户记的。**AI 档不允许走到这条缺省**（守卫 1 会先拦）。

    --- 入层口（2026-09-28）---------------------------------------------------

    `intake="staged"` 的节点**在过门之前不许 activate**。但这里**不拦**它 ——
    落 staging 这一步本来就要建节点，拦了就没法落。门在 `activate()` 那一端。
    """
    if state not in ARTIFACT_STATES:
        raise ScaffoldError(f"未知 state：{state}")
    if state == "active":
        raise ScaffoldError(
            "add_artifact 不得直接建 active 对象。"
            "active 只能由用户在确认环节授予（§C5「未经确认不得写入 Scaffold」）。"
        )
    if intake not in INTAKE_CHANNELS:
        raise ScaffoldError(f"未知 intake：{intake}（见 INTAKE_CHANNELS）")
    if digital_source_type not in DIGITAL_SOURCE_TYPES:
        raise ScaffoldError(
            f"未知 digital_source_type：{digital_source_type} —— "
            f"取值照 IPTC 词表，本系统只用这几档：{DIGITAL_SOURCE_TYPES}"
        )
    who = (asserted_by or "").strip()
    if digital_source_type in AI_SOURCES and not who:
        raise ScaffoldError(
            f"{digital_source_type} 必须写明 asserted_by（谁主张的）。"
            "AI 产出的东西主张者留空，等于系统替它立论 —— "
            "是书作者就写书作者，是 AI 自己就写 AI。"
        )
    aid = new_id(conn, type_)
    now = _now()
    conn.execute(
        "INSERT INTO artifact"
        " (id, type, state, status, origin, digital_source_type, asserted_by,"
        "  intake, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (aid, type_, state, STATUS_DEFAULT, origin,
         digital_source_type, who or origin, intake, now),
    )
    _append_revision(conn, aid, None, content, origin)
    conn.commit()
    return aid


def get(conn: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM artifact WHERE id = ?", (artifact_id,)
    ).fetchone()
    if row is None:
        raise ScaffoldError(f"不存在的 Artifact：{artifact_id}")
    return row


def activate(conn: sqlite3.Connection, artifact_id: str, *, by: str) -> None:
    """proposed → active。**唯一**入口，且必须写明是谁确认的。

    --- 入层门守卫（2026-09-28）-----------------------------------------------

    `intake='staged'` 的节点，**没过门就不许 active** —— 哪怕有人点名要确认它。

    为什么这条要挡在**这里**而不是写在 `staging.py` 里：`staging.py` 挡的是
    「走 staging 的人忘了过门」；挡不住「**压根不走 staging、直接建了再 activate**」。
    只有落在 `active` 的唯一入口上，两条路才一起被挡。

    ⚠️ 它挡不住的是什么，说清楚：调用方把 `intake` 填成 `direct` 就绕过去了。
    那是**撒谎**，不是漏洞 —— 静态那半（B20）盯的就是「非 staging 模块里
    出现导入档位的字面量」。两条一起才是完整的，形状同 B14（静态拦直笔 + 行为拦绕路）。
    """
    row = get(conn, artifact_id)
    if row["state"] != "proposed":
        raise ScaffoldError(
            f"{artifact_id} 当前是 {row['state']}，只有 proposed 能被确认。"
        )
    if row["intake"] == "staged":
        gate = conn.execute(
            "SELECT gate_state FROM staging WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if gate is None:
            raise ScaffoldError(
                f"{artifact_id} 是经入层口进来的（intake='staged'），"
                "却没有 staging 记录 —— 门还没落，不能确认。"
            )
        if gate["gate_state"] != GATE_PASSED:
            raise ScaffoldError(
                f"{artifact_id} 的入层门还是 {gate['gate_state']!r}，不能确认。"
                "导入层的东西必须先过门（`staging.gate()`）—— "
                "门没过就 active，等于把没过眼的蒸馏产物当成已确认知识。"
            )
    conn.execute(
        "UPDATE artifact SET state = 'active' WHERE id = ?", (artifact_id,)
    )
    record_event(conn, "artifact_confirmed", by, artifact_id, {"type": row["type"]})
    conn.commit()


# ---------------------------------------------------------------------------
# Revision（§C10：记录，不停止，不限制）
# ---------------------------------------------------------------------------

def _append_revision(
    conn: sqlite3.Connection, artifact_id: str,
    parent_rev: int | None, content: dict, author: str,
) -> int:
    """内容落库的**唯一漏斗** —— 所以定位符的写入时守卫放在这里。

    ⚠️ 为什么不在 `add_artifact()` 里验：那样只有「新建」被守住，
    `revise()` 是一条**独立**的写入路径（B14 那条行为验证正是抓到了
    「走 revise 的间接写静态看不见」）。放在漏斗上，两条路一起守。
    """
    if isinstance(content, dict) and pointer.POINTER_FIELD in content:
        pointer.verify(content[pointer.POINTER_FIELD])
    cur = conn.execute(
        "INSERT INTO revision (artifact_id, parent_rev, content, author, created_at)"
        " VALUES (?,?,?,?,?)",
        (artifact_id, parent_rev, json.dumps(content, ensure_ascii=False), author, _now()),
    )
    return int(cur.lastrowid)


def revise(
    conn: sqlite3.Connection, artifact_id: str, *,
    content: dict, author: str, parent_rev: int | None = None,
) -> int:
    """加一个新版本。**不覆盖旧版本** —— 旧版本一行都不会被改。

    `parent_rev=None` 时挂到当前 head。若此时有两个 head（已分叉），
    必须显式指定 `parent_rev` —— 系统不会替你挑一个，
    因为挑一个就是替用户做了一个判断。
    """
    heads = heads_of(conn, artifact_id)
    if parent_rev is None:
        if len(heads) > 1:
            raise ScaffoldError(
                f"{artifact_id} 已有 {len(heads)} 个分支 "
                f"({[h['id'] for h in heads]})，必须显式指定 parent_rev。"
                "系统不替你选分支 —— 那是一次判断。"
            )
        parent_rev = heads[0]["id"] if heads else None
    rev = _append_revision(conn, artifact_id, parent_rev, content, author)
    record_event(conn, "artifact_revised", author, artifact_id, {"revision": rev})
    conn.commit()
    return rev


def history_of(conn: sqlite3.Connection, artifact_id: str) -> dict:
    """一个 Artifact 的完整版本谱系（`§C10` 的目标形态：Current State + Revision History）。

    **全部版本一条不略**（「记录，不停止，不限制」），按 id —— 也就是录入顺序，
    不是「最新最前」那种名次。

    分叉点与多头都标出来，而且**不替你挑**：
    多头时 `current` 是 `None`，不是任选一个。挑一个就是替用户做了一次判断 ——
    与 `revise()` 在多头时拒绝自动挂版本是同一个行为（`§C10`）。

    `§C10` 末段：用户**不能**删除已发送的话。所以这里没有、也不会有删除入口。
    """
    rows = conn.execute(
        "SELECT * FROM revision WHERE artifact_id = ? ORDER BY id", (artifact_id,)
    ).fetchall()
    if not rows:
        raise ScaffoldError(f"{artifact_id} 一个版本都没有")

    children: dict[int, list[int]] = {}
    for r in rows:
        if r["parent_rev"] is not None:
            children.setdefault(r["parent_rev"], []).append(r["id"])
    heads = [r["id"] for r in rows if not children.get(r["id"])]
    forks = [rid for rid, kids in children.items() if len(kids) > 1]

    if len(heads) == 1:
        note = f"当前状态是第 {heads[0]} 版；此前 {len(rows) - 1} 版全部保留，一条没删。"
    else:
        note = (
            f"这里有 {len(heads)} 个分支：{heads}。"
            "**系统不替你挑一个** —— 哪一版算数要人来看。"
            "两版都没有被丢弃（`§C10`：记录，不停止，不限制）。"
        )
    return {
        "artifact": artifact_id,
        "revisions": [
            {"id": r["id"], "parent_rev": r["parent_rev"], "author": r["author"],
             "created_at": r["created_at"], "content": json.loads(r["content"]),
             "is_head": r["id"] in heads}
            for r in rows
        ],
        "heads": heads,
        "branch_points": forks,
        "current": heads[0] if len(heads) == 1 else None,
        "note": note,
    }


def content_of(conn: sqlite3.Connection, artifact_id: str) -> dict:
    """当前版本的 content。**多头时拒绝挑一个**（`§C10`）。

    要读「用户当初提交的那一段」用 `original_content_of()`；
    要读「现在算哪一版」用这个，且它会在分叉时直接拒绝 ——
    那不是报错，那是**没有当前版本**这件事本身。
    """
    heads = heads_of(conn, artifact_id)
    if not heads:
        raise ScaffoldError(f"{artifact_id} 一个版本都没有")
    if len(heads) > 1:
        raise ScaffoldError(
            f"{artifact_id} 有 {len(heads)} 个分支 {[h['id'] for h in heads]}，"
            "没有「当前版本」。系统不替你挑一个（`§C10`）。"
        )
    return json.loads(heads[0]["content"])


def original_content_of(conn: sqlite3.Connection, artifact_id: str) -> dict:
    """第 1 个版本的内容。

    `§C2.2.1`：**原文本本身不可修改，必须原样保存**（「可追溯」依赖它）。
    所以凡是「指回用户当初提交的那段话」的派生视图，都该读这一版。

    取根版本（`parent_rev IS NULL`）而**不是** head：并发改出分支后 head 有两个，
    而 `revise` 明文拒绝替用户在两个分支里挑一个 —— 读视图就更不该挑。
    原文本只有一份，按定义取得到，不存在挑的问题。
    """
    row = conn.execute(
        "SELECT content FROM revision WHERE artifact_id = ? AND parent_rev IS NULL"
        " ORDER BY id LIMIT 1",
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise ScaffoldError(f"{artifact_id} 一个版本都没有")
    return json.loads(row["content"])


def heads_of(conn: sqlite3.Connection, artifact_id: str) -> list[sqlite3.Row]:
    """没有子节点的版本 = 当前状态。多于一个 = 发生了并发修改，两条都留着。"""
    return conn.execute(
        "SELECT r.* FROM revision r"
        " WHERE r.artifact_id = ?"
        "   AND NOT EXISTS (SELECT 1 FROM revision c WHERE c.parent_rev = r.id)"
        " ORDER BY r.id",
        (artifact_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Relation（§C3.2：可追踪对象，不是外键字段）
# ---------------------------------------------------------------------------

def add_relation(
    conn: sqlite3.Connection, *,
    kind: str, from_id: str, to_id: str,
    origin: str, state: str = "active",
    asserted_by: str | None = None,
) -> int:
    """加一条结构边。**它有自己的身份、来源和状态。**

    `origin` 必填且区分人 / 机器 —— `§C2.4` 的「可计量」靠它：
    「AI 判定 vs 用户改判」的比例，分子分母都从这一列来。

    --- 谁产的 vs 谁主张的（2026-09-29，迁移 v2）-------------------------------

    两栏答两个问题，`origin` 答不了后一个：

        origin       这条边**是谁产的**     （PROV-O wasGeneratedBy）
        asserted_by  这条边**是谁主张的**   （PROV-O wasAttributedTo）

    对**候选边**，这两个问题本来就该有两个答案：AI 提的（`origin="ai:…"`），
    但**没人主张** —— 所以候选边的 `asserted_by` 必须是 `NULL`。它变成算数的
    那一刻（`candidates.promote()`）才被认领，认领者写进这一栏。

    两条守卫：

    1. **算数的边必须有主张者**（`state == 'active'`）。缺省取 `origin` ——
       人建的边、导入的边、上层派生边，建它的人就是主张它的人。
    2. **还没算数的边不许有主张者**（`state != 'active'`）。给它写上，等于
       系统替一条**还没被任何人认领**的边立论 —— 同 `add_artifact` 那条
       「AI 档不许留空 `asserted_by`」，方向相反，禁的是同一件事。

    `state` 默认 `active`（`§C2.5` 派生标注：默认生效 + 可推翻）。
    ⚠️ 但**候选边不要走这里**：`candidates.record()` 的签名里没有 `state`，
    所以它写不出 `active` —— 那是阶段 5b 出口判据 ① 的落地形态。
    这一层的默认值留给**直接写入**（人建的边、上层派生边）。
    """
    if kind not in RELATION_KINDS:
        raise ScaffoldError(f"未知关系种类：{kind}")
    if state not in RELATION_STATES:
        raise ScaffoldError(f"未知 relation state：{state}")
    get(conn, from_id)
    get(conn, to_id)
    who = (asserted_by or "").strip()
    if state == "active":
        who = who or origin
    elif who:
        raise ScaffoldError(
            f"state={state!r} 的边不许有 asserted_by（收到 {who!r}）—— "
            "一条还没算数的边被写上主张者，等于**系统替它立论**。"
            "主张者要等它算数那一刻由提拔它的人写（candidates.promote()）。"
        )
    now = _now()
    cur = conn.execute(
        "INSERT INTO relation"
        " (kind, from_id, to_id, origin, state, asserted_by, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (kind, from_id, to_id, origin, state, who or None, now),
    )
    rid = int(cur.lastrowid)
    record_event(conn, "relation_added", origin, from_id,
                 {"relation": rid, "kind": kind, "to": to_id})
    conn.commit()
    return rid


def reject_relation(conn: sqlite3.Connection, relation_id: int, *, by: str) -> None:
    """用户推翻一条边（`§C2.4`「可拒绝」）。

    只改 relation 自己的状态，**不删** —— 删掉的话改判率就没法算了，
    而改判率是 `§C2.4` 明确要求计量的。

    ⚠️ **`proposed` 也收**（2026-09-28，阶段 5b）。两个入口不是同一件事，
    但它们落在同一个状态上，因为「这条边不算数了」只有一个答案：

        active    → rejected   推翻一条**已经算数**的边（改判）
        proposed  → rejected   否掉一条**还没算数**的候选边（不采纳）

    两者的区别**必须留在 `event` 的 payload 里**（`was` 一栏），
    否则 `§C2.4` 要计的那个比例会把「AI 提了一条、没人理它」
    和「AI 判定被用户改掉」算成同一件事 —— 而它们完全不同。
    """
    row = conn.execute(
        "SELECT * FROM relation WHERE id = ?", (relation_id,)
    ).fetchone()
    if row is None:
        raise ScaffoldError(f"不存在的 relation：{relation_id}")
    if row["state"] not in ("proposed", "active"):
        raise ScaffoldError(
            f"relation {relation_id} 是 {row['state']}，只有 proposed / active 能被推翻。"
        )
    conn.execute(
        "UPDATE relation SET state = 'rejected' WHERE id = ?", (relation_id,)
    )
    record_event(conn, "relation_rejected", by, row["from_id"],
                 {"relation": relation_id, "kind": row["kind"],
                  "origin": row["origin"], "was": row["state"]})
    conn.commit()


def relation_ancestors(kind: str) -> list[str]:
    """一条 kind 的祖先链（不含它自己），从近到远。

    只走 `RELATION_PARENTS` 一层表 —— 它现在只有两条边、两层，
    但写成循环是为了**将来加一条边时不用改这里**。
    环由 `seen` 兜住（真出现环会停下并返回已走过的部分，不挂死）。
    """
    chain, seen = [], {kind}
    cur = RELATION_PARENTS.get(kind)
    while cur and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = RELATION_PARENTS.get(cur)
    return chain


def is_primary_source(kind: str) -> bool:
    """「这条边**能不能当原始来源用**」—— **唯一**判据。

    ⚠️ 只读 `PRIMARY_SOURCE_KIND`，**不读** `quoted_from`。
    引用是派生的弱特化，够不着来源这一档 —— 这就是 P5（Reference ≠ Derivation）
    在代码里的样子。B17 盯着这个方向：**引用不许出现在原始来源的祖先链上**。
    """
    return kind == PRIMARY_SOURCE_KIND


def pointer_of(conn: sqlite3.Connection, artifact_id: str) -> dict | None:
    """这条对象指向外面哪个东西的哪一段；不指向外部就返回 `None`。

    ⚠️ 多头时 `content_of()` 会直接拒绝（`§C10` 不替人挑版本）——
    定位符也就读不出来。这是**对的**：两个分支可能指两个地方，
    挑一个就是替用户做了一次判断。
    """
    return pointer.read(content_of(conn, artifact_id))


# ---------------------------------------------------------------------------
# Evidence 状态（#3：机器不得自授 verified）
# ---------------------------------------------------------------------------

def grant_verified(conn: sqlite3.Connection, artifact_id: str, *, hook: str) -> None:
    """**唯一**能把 status 写成 verified 的入口，且必须具名一个外部钩子。

    `§C9` #3 禁止 AI 生成内容直接成为 verified knowledge。
    这里把它落成一条机制：不是靠调用方自觉，是**没有别的写入路径**。
    """
    if not hook or not hook.strip():
        raise ScaffoldError("grant_verified 必须写明是哪个钩子授予的。")
    get(conn, artifact_id)
    conn.execute(
        "UPDATE artifact SET status = ? WHERE id = ?", (STATUS_HOOK_ONLY, artifact_id)
    )
    record_event(conn, "status_granted", f"hook:{hook}", artifact_id,
                 {"status": STATUS_HOOK_ONLY})
    conn.commit()


def set_status(conn: sqlite3.Connection, artifact_id: str, value: str, *, by: str) -> None:
    """非钩子路径。写 verified 一律拒绝。"""
    if value == STATUS_HOOK_ONLY:
        raise ScaffoldError(
            f"{by} 不能把 {artifact_id} 写成 verified —— "
            "verified 只能由外部钩子授予（§C9 #3）。走 grant_verified()。"
        )
    get(conn, artifact_id)
    conn.execute("UPDATE artifact SET status = ? WHERE id = ?", (value, artifact_id))
    conn.commit()


# ---------------------------------------------------------------------------
# 观测点（§C7.2）
# ---------------------------------------------------------------------------

def record_event(
    conn: sqlite3.Connection, kind: str, actor: str,
    subject_id: str | None, payload: dict | None = None,
) -> None:
    """记一条事实。**只记发生了什么，不记这说明了什么。**

    `§C7.2`：观测点应尽量记录既有事件，不新增需要用户配合的动作。
    因此本函数只被已有动作调用（重写 / 改判 / 放弃 / 确认），
    **没有任何一个 event kind 是为了采集而新造的用户动作。**
    """
    conn.execute(
        "INSERT INTO event (kind, actor, subject_id, payload, created_at)"
        " VALUES (?,?,?,?,?)",
        (kind, actor, subject_id, json.dumps(payload or {}, ensure_ascii=False), _now()),
    )


def events_of_kind(conn: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    """给上层派生视图用。**只读**。"""
    return conn.execute(
        "SELECT * FROM event WHERE kind = ? ORDER BY id", (kind,)
    ).fetchall()
