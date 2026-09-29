"""《选型声明》(`§T4.2`) 里承诺的可执行否证检查。

`§T4.2`：B 类的第 4 项不能只是说法，必须落成一条可执行的否证检查。
        **检查跑不通 = 声明不成立 = 停下提问。**

`§C13.B`：只有可执行的检查能挡住套话式自证。

    python checks.py          # 全过 → 退出码 0；有命中 → 退出码 1

--- 检查口径（写清楚，免得它变成走过场）------------------------------------

用 `tokenize` 定位注释位置，然后**逐行**扫，丢掉**注释和 docstring**。
字符串**字面量照扫** —— 所以把 `"score"` 写成 dict 的键也会被抓到。
docstring 为什么不扫，见 修订四。
被扫的是**产物代码**：`*.py`，含测试。测试里也不许出现这些标识符 ——
所以 `test_arena.py` 里方法名如果撞上，就改名，而不是把测试排除掉。

**只有两个例外**（`EXEMPT`）：本文件与 `test_checks.py` —— 它们是验伪装置本身，
不写违规词就干不了活。这个集合由 `test_checks.py` 里一条用例钉死。

**为什么是逐行而不是逐 token**：逐 token 时 `a['ratio'] > 0.3` 会被切成
`a` / `[` / `'ratio'` / `]` / `>` / `0.3` 六个 token，**任何跨 token 的写法都匹配不到**，
检查会静默空转。这一点是注入测试暴露出来的（见 DECLARATION.md §4）。
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from functools import partial
from pathlib import Path

# ⚠️ 本文件**只有十三条检查** import 了被检查的模块（B16–B26），其余全是纯静态扫源码。
# 理由：那几条查的是**词汇表定义的自洽性** —— 那是「规格」，读常量就是读规格，
# 比用正则去抠源码里的字面量准得多（正则抠字面量改个格式就失效）。
# 它们不引入运行时依赖：`candidates` / `contribute` / `pointer` / `policy` /
# `scaffold` / `staging` / `stop` / `rules` / `upper` / `views` 都是本地模块，
# 且只用标准库。
import candidates
import contribute
import pointer
import policy
import rules
import scaffold
import staging
import stop
import upper
import views

from _console import force_utf8

ROOT = Path(__file__).parent

# 不在扫描范围内的**只有这两个文件**，因为它们是**验伪装置本身**：
#   checks.py       必须写出这些词，才能拿它们当检查用；
#   test_checks.py  必须写出真的违规语料，否则没法证明检查不是空转。
# 两个都是「不写违规就干不了活」的文件。**产物代码与产物测试照扫**（含 test_arena.py）。
# 这是个洞，所以钉住：test_checks.py 有一条用例断言这个集合**恰好**是这两个名字 ——
# 将来想多豁免一个，必须改那条用例，改的时候会被看见。
# 依据见 DECLARATION.md §4.3。
EXEMPT = {"checks.py", "test_checks.py"}

# (编号, 说明, 触发的正则, 触碰的条款)
CHECKS = [
    # `(?<!b)lock` 而不是 `lock`：`blocks` / `blocking` 里含 "lock" 是子串巧合，
    # 而 `deadlock` / `FileLock` / `flock` 是真锁，仍要抓到。
    ("B1", "不引入锁",
     r"(?<!b)lock|mutex|semaphore", "§C11.2 §C11.3"),
    ("B2", "无权重 / 评分 / 名次 / 真值",
     r"weight|score|rank|truth", "§C6.1 §C9 #4 #5 #7"),
    ("B3", "不实现自动分裂 / 自动合并 / 阈值",
     r"auto_split|auto_merge|threshold", "§C12.5 §C9 #9"),
    # ⚠️ B4 收窄过一次（2026-09-27）。原判据 `consensus|cluster|emergence`
    # 抓的是「有没有实现上层归纳」——`§C7.1` 当年说 MVP 全程休眠，
    # 所以「出现这些词」就等于「越界」。需求方 2026-09-27 授权把上层**打开**，
    # 休眠前提没了，原判据就变成误报（它会把合规实现一起打掉）。
    #
    # 收窄时必须**只对准上层**，不能全仓扫：`tally` / `vote_count` 在
    # `vote.py` 里是正当的（投票本来就该数票）。所以这条只扫 `upper.py`，
    # 判据是「**上层归纳的信号不许是热度类**」。
    # 全仓那条在 B2（无权重/评分/名次/真值），两条不重叠。
    ("B4", "上层归纳不读热度类信号（`vote.py` 数票是正当的，不在此列）",
     r"consensus|emergence|tally|vote_count|voted_count|public_preference"
     r"|popular\w*|\bhot\b|trending|\bviews\b|exposure",
     "§C7.1 §C9 #5", "upper.py"),
    ("B5", "无标量分",
     r"evidence_score|truth_score|total_score|verdict", "§C6.1 §C9 #7"),
    # `§T2` 第 12 步引入发号器之后新加的。它是 §C9 #10 的可执行形式：
    # 版本表只许加行，不许覆盖 —— `revision` 没有 UPDATE 路径，这条盯着它别长出来。
    ("B11", "版本表只增不改（无覆盖式更新）",
     r"UPDATE\s+revision\b", "§C10 §C9 #10"),
]

# B6 需要**跨行跟变量**，一行正则做不到，所以单独一个函数。见 check_no_threshold。
_METRIC_WORD = re.compile(r"(?:ratio|rate|duration|elapsed|cost)\w*", re.I)
_COMPARISON = re.compile(r"(?:>=|<=|>|<)\s*[0-9]")
_ASSIGNED = re.compile(r"^\s*(\w+)\s*=")

_TRIVIAL = (tokenize.NL, tokenize.COMMENT)


def _docstring_lines(src: str) -> set[int]:
    """docstring 占的行号。

    判据：一个 STRING token，且它**前面没有任何实义 token**（文件开头也算）。
    所以 `x = "score"` 不算 —— 那前面有 `x` 和 `=`。

    为什么要单独认它：见 修订四。**文档不是产物行为** ——
    docstring 里写不出一个分数来，而 B2 / B5 要抓的是产物**在产生**权重与评分。
    写成字符串当 dict 键仍然照抓（那是产物行为）。
    """
    lines: set[int] = set()
    fresh = True                      # 还没遇到实义 token
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in _TRIVIAL:
            continue
        if tok.type == tokenize.STRING and fresh:
            lines.update(range(tok.start[0], tok.end[0] + 1))
        if tok.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            fresh = True
        else:
            fresh = False
    return lines


def code_lines(path: Path):
    """(行号, 该行源码去掉注释与 docstring 后的部分)。

    注释起始列由 `tokenize` 给出；docstring 整行去掉。
    """
    src = path.read_text(encoding="utf-8")
    cut: dict[int, int] = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            cut.setdefault(tok.start[0], tok.start[1])
    doc = _docstring_lines(src)
    for n, line in enumerate(src.splitlines(), 1):
        if n in doc:
            continue
        yield n, (line[:cut[n]] if n in cut else line).rstrip()


def scan(pattern: str, only: str | None = None) -> list[tuple[str, int, str]]:
    """全仓扫一条正则。`only` 给文件名时只扫那一个。

    为什么要 `only`：有两条检查的**判据相同、适用范围不同**。
    `tally` 在 `vote.py` 里是正当的（投票本来就该数票），
    在 `upper.py` 里就是越界（上层归纳不许读热度）。全仓扫必然误报，
    所以允许把范围缩到一个文件 —— 但**必须显式写出来**，不许默认缩。
    """
    rx = re.compile(pattern, re.I)
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in EXEMPT or "__pycache__" in path.parts:
            continue
        if only is not None and path.name != only:
            continue
        for line, text in code_lines(path):
            if rx.search(text):
                hits.append((path.relative_to(ROOT).as_posix(), line, text.strip()))
    return hits


def check_no_threshold() -> list[tuple[str, int, str]]:
    """`§C7.2` `§T0.3`：观测点的值不得与任何数比较（不设阈值 / 报警线）。

    这一条**空转过两次**，所以写清楚它跟以前有什么不同：

    1. 最早抓的是百分号字面量 —— 靶子错了，命中的是 `{x:.2%}` 这种**显示格式**，
       而真正的失败模式（拿观测点的值和数比）它根本看不见。
    2. 改成逐行盯比较运算，但**逐行看不见跨行**：

           v = m['unused_ratio']       ← 这行把 v 标成「沾了观测点」
           if v >= 0.2:                ← 阈值在这一行，老版本完全没反应

       「先取出来再比」恰恰是最常见的写法。所以这里**跟局部变量**：
       一行里出现观测点词、且在那个位置做了赋值，就把被赋的名字记下来；
       之后任何拿它比较的行都算命中。

    代价：**会过度报告**。比如 `v` 沾过观测点之后，再 `if v < 5`（其实是个下标边界）
    也会被记一笔。这是刻意的取舍 —— 本检查是否证用的，**漏报是致命的，误报只是吵**。

    --- 与 `policy.py` 的边界（2026-09-28 加）--------------------------------

    这条管的是**观测点**：「换说法率」「改判率」这类从 event 表算出来的比率。
    它防的是**单向性违反** —— 把算出来的评估结果写回 artifact 字段。

    `policy.py` 的 `POLICY` **不是观测点**：那是外部的**运维门槛**，
    读的是「导入条数与社区贡献条数的比」这类跨层的量，结果只触发
    「暂停导入」这类开关，**不写回任何字段**、不参与排序、不影响内容展示。

    ⚠️ 但本检查是**按词抓的**（`ratio` / `rate` / `duration` / `elapsed` / `cost`
    与数字比较），分不出语义。所以 `POLICY` 的键名**刻意避开这些词** ——
    这是**实现限制**，不是语义边界。若哪天这条改成能读语义的判据，规避可以撤掉。
    """
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in EXEMPT or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        tainted: set[str] = set()
        for n, text in code_lines(path):
            found = _COMPARISON.search(text)
            if found:
                left = text[:found.start()]
                if _METRIC_WORD.search(left) or any(
                    re.search(rf"\b{re.escape(t)}\b", left) for t in tainted
                ):
                    hits.append((rel, n, text.strip()))
            assigned = _ASSIGNED.match(text)
            if assigned and _METRIC_WORD.search(text):
                tainted.add(assigned.group(1))
    return hits


def check_no_new_dependency() -> list[tuple[str, int, str]]:
    """`§T5`：引入新依赖是必须停下来的阻断项。所以这里证明没引。"""
    rx = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)")
    # 本地模块**按整个仓库算**，不只顶层 —— `samples/align.py` 也是本仓库的模块，
    # `from align import ...` 不是引第三方。扫的是 rglob，这里就得跟着 rglob，
    # 否则 B7 会把子目录里的本地导入报成新依赖（2026-09-25 真报了一次假）。
    local = {p.stem for p in ROOT.rglob("*.py")}
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = rx.match(line)
            if not m:
                continue
            top = m.group(1).split(".")[0]
            if top in local or top in sys.stdlib_module_names:
                continue
            hits.append((path.relative_to(ROOT).as_posix(), n, line.strip()))
    return hits


PENDING = "annotation_status: 待需求方标注"


def check_placeholder_not_annotated() -> list[tuple[str, int, str]]:
    """`§C5.5.1` 第 2 条：标注不得由 AI 生成。

    判据：**自己声明了「待需求方标注」的区块，标注区必须是空的。**
    把 `annotation_status` 改成别的值（也就是需求方真标了）之后，这条就不再管它。

    这是防「顺手把 proposed_count 补上，让它看起来像真样本」——
    一旦补上，后面所有一致率都是空转的，而且**看起来很正常**。

    ⚠️ 本检查靠标记触发，所以**标记一旦改名，它会静默空转**。
    2026-09-25 就从 `agent-placeholder` 改成过 `annotation_status`；
    改的时候必须同时确认它还能抓到东西。见 DECLARATION.md §4.2。

    ⚠️⚠️ **2026-09-25 第二次发生，而且这次是主动发生的。**
    需求方授权把标签文件折算成标注，`inputs.md` 里 20 条的
    `annotation_status` 全部离开了 `待需求方标注` ——
    于是**这条检查在那份文件上一条都罩不住了，而它照样打印「过」。**
    这就是上面那句警告说的病，只是这次不是改名改掉的，是**流程正常往前走**走掉的。
    补的是 `check_annotated_samples_name_their_source`（B13）——
    管「有值 → 必须署名」。见 DECLARATION.md §11。
    """
    samples = ROOT / "samples"
    if not samples.is_dir():
        return []
    hits = []
    for path in sorted(samples.glob("*.md")):
        block, start = [], 1
        for n, line in enumerate(
            [*path.read_text(encoding="utf-8").splitlines(), "## __end__"], 1
        ):
            if line.startswith("## "):
                _judge_placeholder(path, start, block, hits)
                block, start = [], n
            block.append((n, line))
    return hits


def _judge_placeholder(path, start, block, hits):
    if not any(PENDING in ln for _, ln in block):
        return
    rel = path.relative_to(ROOT).as_posix()
    for n, ln in block:
        s = ln.strip()
        filled = (s.startswith("proposed_count:") and "null" not in s) or (
            s.startswith("agent_filled:") and "未标注" not in s
        )
        if filled:
            hits.append((rel, n, s))


def _blocks(path):
    """把一个 .md 按 `## ` 切成区块，产出 (起始行号, [(行号, 行)])。"""
    block, start = [], 1
    for n, line in enumerate(
        [*path.read_text(encoding="utf-8").splitlines(), "## __end__"], 1
    ):
        if line.startswith("## "):
            yield start, block
            block, start = [], n
        block.append((n, line))


def check_annotated_samples_name_their_source() -> list[tuple[str, int, str]]:
    """`§C5.5.1` 第 2 条的**另一半** —— B8 只挡了一半。

    B8 管的是「声明待标注 → 标注区必须空」。
    另一半是「**标注区一旦有值 → 必须说清这是谁的**」。
    加它的直接原因：2026-09-25 需求方授权把标签文件折算成标注之后，
    `inputs.md` 里 20 条全部离开了 B8 的状态 ——
    **B8 照样打印「过」，但它在那份文件上一条都罩不住了。**

    这正是 B8 自己的 docstring 警告过的病（靠标记触发，改了标记就静默空转）。
    空转不可怕，**可怕的是它长得和通过一模一样**。所以补上这另一半：
    填了值就必须署名，没署名就响。

    ⚠️ 它挡不住「`agent_filled` 写了但写的是假话」。那测不出来，见 `§C2.4`：
    四项都是**能力**，不是**义务**。
    """
    samples = ROOT / "samples"
    if not samples.is_dir():
        return []
    hits = []
    for path in sorted(samples.glob("*.md")):
        for start, block in _blocks(path):
            if any(PENDING in ln for _, ln in block):
                continue                       # 这一块归 B8 管，不重复报
            named = any(ln.strip().startswith("proposed_count:")
                        and "null" not in ln for _, ln in block)
            if not named:
                continue
            if not any(ln.strip().startswith("agent_filled:")
                       and ln.strip() != "agent_filled:" for _, ln in block):
                hits.append((path.relative_to(ROOT).as_posix(), start,
                             "有 proposed_count 却没有 agent_filled —— 值是谁填的，没说"))
    return hits


# 并发装置的三条红线（`§C11.2` 的判据表）。**只扫这一个文件。**
#
# 为什么不并进 B1 那个全仓正则：这三条**只对并发装置成立**。
# `scaffold.py` 里满是 SQL 才是对的 —— 它本来就是存储层；
# 而并发装置里出现一条 INSERT，那就是「手工补状态」，是剧本不是并发。
# 全仓扫会把它扫成误报，误报到最后就是关掉它 —— 那比不扫更糟。
# 见 DECLARATION.md §4.4。
_SLEEP = re.compile(r"\bsleep\s*\(", re.I)
# **只拦写，不拦读。** `§C11.2` 的原话是「直接**改**数据库 / 手工补状态」——
# 拦的是"我在摆布状态"，不是"我在看状态"。读不出假象来：
# 一条 SELECT 改不了谁先谁后，也就伪造不出一个竞争窗口。
# 第一版我把 SELECT 也拦了，**结果拦住了自己**：`_interleaving()` 要读 event 表
# 才能回答「这两个主体到底有没有真重叠」——那是本步**最该测**的一件事。
# 一条严到必须绕过自己的检查，最后的下场就是被关掉。见 DECLARATION §4.4。
_SQL_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|REPLACE\s+INTO)\b",
                        re.I)
_COORD = re.compile(r"\b(threading|multiprocessing|asyncio|Barrier|Semaphore)\b")

# B9 默认扫的那一个文件。**提成常量**，因为它有一个「空转会静默」的坑：
# `check_apparatus_is_not_a_script()` 在目标文件不存在时返回 `[]`（「暂不适用」），
# 于是「装置是干净的」和「根本没有装置」长得一模一样 —— 而
# `test_checks.py::test_B9_accepts_the_real_apparatus` 正好断言 `== []`，
# 它会**空过**。2026-09-25 给装置改名时踩到这个形状：改 `concurrent.py` →
# `concurrency.py` 而漏了这里，B9 就再也不会响了，而且什么都不会说。
# 所以路径提成常量，好让用例能单独钉住「这个文件真的在」。
APPARATUS = ROOT / "concurrency.py"

# 上层模块（`§C7.1`）。B14 / B15 扫它。
#
# 同样的理由提成常量：两条检查在**文件不存在**时都返回 `[]`（「暂不适用」），
# 于是「上层是干净的」和「根本没有上层模块」长得一模一样，用例会**空过**。
# 这正是 B9 上面那段注释记着的坑，B14 / B15 是同一个形状 ——
# 所以它们的用例里都先单独钉住「这个文件真的在」。
UPPER = ROOT / "upper.py"


def check_apparatus_is_not_a_script(
    target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """`§C11.2` 判据表：禁止注入延迟、禁止直接改数据库、禁止协调两个主体。

    本步**唯一**允许的写法是：起两个真进程，让它们各自调产品 API，
    然后看发生了什么。任何一处 `sleep` / 写库 / 同步原语，都是我在替它们决定
    谁先谁后 —— 那样打出来的「问题」是我安排的，不是系统暴露的。

    **只拦写，不拦读** —— 理由写在 `_SQL_WRITE` 上面。

    判据是**文件里有没有这些东西**，不是「跑起来像不像并发」。
    后者测不出来；前者一查就知道。

    `target` 可覆盖，只为了 `test_checks.py` 能拿临时探针验这条不是空转 ——
    跟 B8 一个道理：**只扫一个固定文件，跟扫一个改名就消失的标记，是同一种脆。**
    """
    target = target or APPARATUS
    if not target.is_file():
        return []                      # 还没写并发装置 —— 这条暂不适用，不是「过」
    hits = []
    for n, line in code_lines(target):
        for rx, why in ((_SLEEP, "注入延迟"), (_SQL_WRITE, "直接改数据库"),
                        (_COORD, "在装置内部做协调")):
            if rx.search(line):
                hits.append((target.name, n, f"{line.strip()}   ← {why}"))
    return hits


def check_allocator_stays_an_allocator() -> list[tuple[str, int, str]]:
    """`§T4.1` B 类自证（`§T2` 第 12 步）：**发号器不许携带任何判断。**

    这一步选的是「让 SQLite 原子发号」—— 一次**锁策略**层面的选择，
    所以按 B 类规矩，得落成一条可执行的否证检查，证明它不触碰不变量。

    它可能触碰的是 `§C9` **#5（Popularity = Evidence）** 与
    **#7（Evidence 自带固定 truth score）**：这两个不变量管的是
    「不许把某个**量**读成判断」。而 `seq.n` 恰好就是一个量。

    它现在安全，是因为 **它出不了 `scaffold.py`** —— 号只被用来拼成一个字符串
    （`claim-0001`），拼完这个数就没了，没有任何视图、计数、排序读过它。
    哪一天有人把 `seq.n` 拎出来放进 `view()` 或 `tally()`，
    它就从「发号器」变成了「一个可排序、可比大小的数」—— 那正是 #5 / #7 要防的路。

    判据：**`seq` 这张表只在一个文件里被提到**。
    这是它的**全部**约束，也正是我敢说它不违反 #5 / #7 的理由。
    """
    hits = []
    for path in sorted(ROOT.glob("*.py")):
        if path.name in EXEMPT:
            continue
        for n, line in code_lines(path):
            if re.search(r"\b(FROM|INTO|UPDATE|JOIN|TABLE)\s+seq\b", line, re.I):
                if path.name != "scaffold.py":
                    hits.append((path.name, n, line.strip()))
    return hits


# 缺口②：「命题的节点类型不许被写死」。**只扫这一个文件。**
#
# 为什么只扫 `confirm.py`：`type_="Topic"`、`type_="Counterargument"` 这类字面量
# 在 `debate.py` 里全是**对的** —— 建 Mechanism 就是建 Mechanism。
# 会出错的是**命题**的类型。
#
# ⚠️ 2026-09-25 需求方把缺口② 从第 1 档改成第 2 档（默认生效 + 可推翻 + 抽检 +
# 计改判率）之后，**这条检查的理由变了，得说清楚，不能让旧理由留着糊过去**：
#
#   改之前它挡的是「引擎自决」—— 定完才写，所以写死一个类型 = 替人做了选择。
#   改之后**默认本来就是 Claim**，写死 `type_="Claim"` 和走候选表
#   在「默认那一下」产出完全相同的字节。**那半个理由没有了。**
#
#   它现在挡的是第 2 档的第三条「**可推翻**」：
#   走 `chosen.get(...)` 时，`resolve_types()` 里人给的值能到达 artifact；
#   写死 `type_="Claim"` 时，用户改过的那一笔会**只进事件表、不进结构** ——
#   改判记录下来了，改判没生效。那比第 1 档更糟：它看起来像是尊重了判断。
#
# 这个检查**有洞**，说清楚：`t = "Claim"; add_artifact(type_=t, ...)` 绕得过去。
# 洞不补 —— 补它要靠数据流分析，成本远超收益。它挡的是**改回去那个动作本身**，
# 不是「有人存心绕」。见 DECLARATION §10.3。
_HARDCODED_PROPOSITION_TYPE = re.compile(
    r"""type_\s*=\s*["'](?:Claim|Evidence)["']""")


def check_proposition_type_is_not_hardcoded(
    target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """缺口②：命题的节点类型不许写死 —— 写死就等于**推翻不了**。

    改坏它的方式很具体：把 `confirm()` 里那句
    `type_=chosen.get(x["key"], "Claim")` 换回 `type_="Claim"`。
    换回去之后，`resolve_types()` 里人给的 `Evidence` 到不了 artifact ——
    事件表里记着「用户改判了」，结构里还是 `Claim`。
    这个检查就盯那一行。

    它**只**保证这一点。第二档里「默认生效」「计改判率」两条不归它管
    （前者本来就是行为，后者由 `types_defaulted` 事件保证，见下面的注释）。

    `target` 可覆盖，为的是 `test_checks.py` 能注入探针证明它不空转 ——
    跟 B8 / B9 一个道理。
    """
    target = target or (ROOT / "confirm.py")
    if not target.is_file():
        return []                      # 还没写确认链 —— 这条暂不适用，不是「过」
    return [(target.name, n, line.strip())
            for n, line in code_lines(target)
            if _HARDCODED_PROPOSITION_TYPE.search(line)]


_UPPER_WRITES_DOWN = re.compile(
    r"""\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+(?:artifact|revision|relation)\b""",
    re.IGNORECASE)


def check_upper_does_not_write_down(
    target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """`§C7.1` ④ 单向性：**上层 → 底层，禁止写成事实**。

    这是本仓库里最该机器守的一条，因为它**坏起来不像坏**：
    上层归纳顺手把「这一簇很重要」写进底层的某个字段，
    短期看一切正常（排序确实变好了），长期看**底层的真值被相关性判断污染了** ——
    而那个污染不可追溯（底层字段没有「这是上层写的」这个标记）。

    判据刻意做得**又笨又窄**：`upper.py` 里出现
    `UPDATE artifact` / `INSERT INTO revision` / `DELETE FROM relation` 之类就报。

    为什么只扫 `upper.py`：底层模块当然要写库（`scaffold.py` 就是干这个的）。
    这条盯的是**上层那一侧不许自己动手**。

    ⚠️ 它**不能**代替 `test_arena.py` 里那条行为验证。
    静态扫只能拦「直接写 SQL」；走 `scaffold.revise()` 之类的**间接写**它看不见。
    两条一起才是完整的：静态拦直笔，行为测试拦绕路。

    `target` 可覆盖，为的是 `test_checks.py` 能注入探针证明它不空转。
    """
    target = target or (ROOT / "upper.py")
    if not target.is_file():
        return []                      # 还没有上层模块 —— 暂不适用，不是「过」
    return [(target.name, n, line.strip())
            for n, line in code_lines(target)
            if _UPPER_WRITES_DOWN.search(line)]


def check_upper_nodes_are_not_named_by_machine(
    target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """上层节点**不许带系统生成的名字** —— 这一条是本模块能免确认的全部理由。

    需求方 2026-09-27 定的那一档：节点可以自动建，但**名字由人来给**。
    因为「这一簇叫什么」那句话，系统自己说不出来（说了就是替用户立论，`§C2.0`）。
    名字留空，系统就**一句话都没说**。

    判据：`upper.py` 里给 `Content` 节点写 `text` 的地方，
    不许出现 `PENDING_NAME` 之外的字符串字面量。

    它盯的是最容易发生的那种退化：有人图省事，从依据里挑第一条命题
    抄成名字 —— 那样节点自动带了一个系统选的名字，而**它看起来完全正常**。
    """
    target = target or (ROOT / "upper.py")
    if not target.is_file():
        return []
    src = target.read_text(encoding="utf-8")
    if 'type_=UPPER_TYPE' not in src:
        return []                      # 还没建上层节点 —— 暂不适用
    hits = []
    for n, line in code_lines(target):
        if "PENDING_NAME" in line:
            continue
        if _MACHINE_NAME_LITERAL.search(line):
            hits.append((target.name, n, line.strip()))
    return hits


# 给上层节点的 `text` 赋一个写死的字符串字面量 = 系统给它起了名。
_MACHINE_NAME_LITERAL = re.compile(r"""["']text["']\s*:\s*["'][^"']+["']""")


def check_ai_sources_require_attribution(
    *, kinds: tuple | None = None, ai: tuple | None = None,
    default: str | None = None, target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """AI 产出的档位必须**能**被要求写明「谁主张的」—— 而且这条要求真的在。

    这条防的是**AI 产出伪装成人**（设计稿里那个「AI 转录的书算哪一档」的问题）。
    两个判据，缺一不可：

    **判据一：档位集合自洽。** 三条，都是结构性的，不依赖具体值：

    - `AI_SOURCES ⊆ DIGITAL_SOURCE_TYPES` —— 不许有指向不存在档位的名字；
    - `AI_SOURCES` **非空** —— 空集合等于「没有任何产出需要署名」；
    - `DIGITAL_SOURCE_DEFAULT` **不在** `AI_SOURCES` 里 —— 默认档是人创建，
      若它同时被算成 AI 档，守卫就自相矛盾（每个走默认值的调用都会触发它）。

    **判据二：守卫真的在。** 光有常量不算，得有人拿它拦 ——
    `add_artifact` 函数体里必须同时出现 `AI_SOURCES` / `asserted_by` / `raise`。

    为什么这条**不能**用扫正则做：它查的是「集合关系」和「函数体里有没有某个守卫」，
    正则抠源码字面量改个排版就失效。所以它读 `scaffold` 的常量。

    ⚠️ 它**不能**代替 `test_provenance.py` 那条行为验证。
    静态只证明「守卫的代码在」；守卫**真的会拦**由行为测试证明。
    """
    kinds = kinds if kinds is not None else scaffold.DIGITAL_SOURCE_TYPES
    ai = ai if ai is not None else scaffold.AI_SOURCES
    default = default if default is not None else scaffold.DIGITAL_SOURCE_DEFAULT
    target = target or (ROOT / "scaffold.py")

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str) -> None:
        hits.append((target.name, 1, msg))

    if not ai:
        flag("AI_SOURCES 是空的 —— 等于没有任何产出需要写明主张者")
    for k in ai:
        if k not in kinds:
            flag(f"AI_SOURCES 里的 {k!r} 不是已知的 digital_source_type")
    if default in ai:
        flag(
            f"默认档位 {default!r} 同时被算成 AI 档 —— "
            "那会让每个走默认值的调用都触发署名守卫，自相矛盾"
        )

    # 名字里含 `Algorithmic` 的档位，必须**全部**被算成 AI 档。
    #
    # 这条为什么是**结构性**的而不是硬编码：IPTC 词表给 AI 相关的档位都用
    # 同一个词根命名（trainedAlgorithmicMedia / compositeWithTrainedAlgorithmicMedia /
    # algorithmicallyEnhanced / algorithmicMedia）。所以判据可以落在命名规律上 ——
    # 漏掉一个，那一档的产出就不需要署名，而它照样是 AI 产出。
    for k in kinds:
        if "algorithmic" in k.lower() and k not in ai:
            flag(
                f"{k!r} 的名字表明它是 AI 档，却没被算进 AI_SOURCES —— "
                "那一档的产出不需要写明主张者"
            )

    body, start = _function_body(target, "add_artifact")
    if start is None:
        flag("找不到 add_artifact —— 守卫无从谈起")
    else:
        joined = "\n".join(t for _, t in body)
        missing = [w for w in ("AI_SOURCES", "asserted_by", "raise") if w not in joined]
        if missing:
            flag(
                f"add_artifact 里看不到守卫（缺 {'/'.join(missing)}）—— "
                "常量定义了 AI 档，却没人拿它拦"
            )
    return hits


def check_primary_source_is_not_a_quotation(
    *, kinds: tuple | None = None, parents: dict | None = None,
    primary: str | None = None,
) -> list[tuple[str, int, str]]:
    """引用**不许**被当成原始来源 —— 这是「引用 ≠ 派生」的可执行形式。

    PROV-O 给的是一棵**层次**树，不是互斥二分：

        wasDerivedFrom
          ├── wasQuotedFrom        ← 引用：派生的**弱特化**
          └── hadPrimarySource     ← 原始来源：最强的**一档**

    所以「能不能当原始来源用」只读 `had_primary_source`，**不读 `quoted_from`**。
    这条把它写成结构判据，不靠命名自觉：

    1. `quoted_from` **必须**有父类 —— 它是弱特化，不是平级的 kind。
    2. `PRIMARY_SOURCE_KIND` 与 `quoted_from` **不是同一个值**。
    3. ★ **引用不得出现在原始来源的祖先链上** —— 两者是**兄弟**，不是父子。
    4. 父类表里的 key / value 都必须是已知 kind —— 不许指向不存在的种类。

    ⚠️ 判据里**没有**「原始来源必须是顶层」这一条 —— 因为它本来就不是：
    PROV-O 里 `wasQuotedFrom` / `hadPrimarySource` / `wasRevisionOf`
    **三者都是** `wasDerivedFrom` 的子属性。原始来源**也有父类**，
    真正的判据是「**引用不能是它的祖先**」。

    四条合起来就是一句话：**引用与原始来源是同一族的两个兄弟档，
    而原始来源永远不是「某种引用」。**

    改坏它的方式很具体：把 `PRIMARY_SOURCE_KIND` 改成 `"quoted_from"`，
    或把 `quoted_from` 从 `RELATION_PARENTS` 里删掉（那样它就不再是弱特化，
    变成一个平级的 kind，谁都可能拿它当来源用）。
    """
    kinds = kinds if kinds is not None else scaffold.RELATION_KINDS
    parents = parents if parents is not None else scaffold.RELATION_PARENTS
    primary = primary if primary is not None else scaffold.PRIMARY_SOURCE_KIND

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str) -> None:
        hits.append(("scaffold.py", 1, msg))

    if primary not in kinds:
        flag(f"PRIMARY_SOURCE_KIND={primary!r} 不是已知的 relation kind")
    if "quoted_from" not in kinds:
        flag("缺 quoted_from —— 引用没有种类，这条检查无从落地")
    if "quoted_from" not in parents:
        flag(
            "quoted_from 没有父类 —— 引用必须是派生的**弱特化**"
            "（PROV-O：wasQuotedFrom ⊑ wasDerivedFrom）"
        )
    if primary == "quoted_from":
        flag("PRIMARY_SOURCE_KIND 指向了 quoted_from —— 引用不是原始来源")

    # ★ 核心判据：引用**不得**出现在原始来源的祖先链上。
    #
    # 为什么这是核心：若 `had_primary_source ⊑ quoted_from`（原始来源是引用的一种），
    # 「引用」就成了「原始来源」的**上位** —— 于是任何引用都自动够得着来源这一档，
    # 「引用不许升级成来源」这条约束当场失效。
    #
    # ⚠️ 原始来源**自己可以有父类**：PROV-O 里 wasQuotedFrom / hadPrimarySource /
    # wasRevisionOf **三者都是** wasDerivedFrom 的子属性。所以判据不是
    # 「原始来源必须顶层」，而是「**引用不能是它的祖先**」—— 两者是**兄弟**，不是父子。
    chain, seen = [], set()
    cur = parents.get(primary)
    while cur and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = parents.get(cur)
    if "quoted_from" in chain:
        flag(
            f"引用出现在原始来源 {primary!r} 的祖先链上"
            f"（{' → '.join([primary] + chain)}）—— "
            "那等于说「原始来源是引用的一种」，引用就自动够得着来源这一档了"
        )

    for child, parent in parents.items():
        if child not in kinds:
            flag(f"父类表的 key {child!r} 不是已知 kind")
        if parent not in kinds:
            flag(f"父类表的 value {parent!r} 不是已知 kind")
    return hits


# ---------------------------------------------------------------------------
# 阶段 2 / 3 新增的四条（2026-09-28）
# ---------------------------------------------------------------------------

# 「承载正文」的字段名。pointer 的规定字段里出现任何一个，就是又开始存正文了。
#
# ⚠️ 注意 `exact` **不在**这张表里 —— 它是 `TextQuoteSelector` 的规定字段，
# 是**定位所需的最小引文**，不是正文。两者的界在**字段身份**上，不在字数上：
# 「另存一份正文」会表现为一个**白名单之外**的字段，那个会被拦。
_BODY_FIELDS = ("body", "text", "content", "fulltext", "full_text", "bodytext", "raw")


def check_reference_stores_only_a_locator(
    *, kinds: tuple | None = None, fields: dict | None = None,
    body: tuple | None = None, target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """引用**只存定位符，不存正文**（阶段 2 的出口判据 ④）。

    为什么这条不是「省空间」那种优化，而是结构约束：**副本无法证明自己等于原文**。
    原文改了、撤了、换版本，副本不会跟着变，而读者看不出来。
    存定位符则相反 —— 仓库里**没有**任何能被误当成原文的东西。

    它的可执行形式是**字段白名单**，不是长度限制（长度限制是阈值，会滑进 B3 那一族）。

    判据：

    1. `SELECTOR_KINDS` 与 `SELECTOR_FIELDS` 的键**恰好相等** ——
       加一种 selector 必须同时改两处，于是它一定出现在 diff 里。
    2. 每个 selector 名以 `Selector` 结尾（W3C 命名），不自造名字。
    3. ★ **规定字段里不得出现承载正文的字段名** —— 这就是「不存正文」本身。
    4. `POINTER_FIELD` 非空，且它自己不是正文承载字段名。
    5. `verify` / `verify_selector` 的函数体里必须出现 `raise PointerError` ——
       光有白名单不算，得有人拿它拦。
    6. `scaffold._append_revision` 的函数体里必须出现 `pointer` ——
       **写入时守卫真的接上了**。⚠️ 接在那里而不是 `add_artifact` 里，
       是因为 `revise()` 是另一条写入路径（B14 的行为验证正是抓到了
       「走 revise 的间接写静态看不见」）。

    改坏它的方式很具体：给 `TextQuoteSelector` 加一个 `"full"` 字段存全文，
    或者把 `verify()` 里那段拒绝多余字段的代码删掉。两条都会被这里抓到。
    """
    kinds = kinds if kinds is not None else pointer.SELECTOR_KINDS
    fields = fields if fields is not None else pointer.SELECTOR_FIELDS
    body = body if body is not None else _BODY_FIELDS
    target = target or (ROOT / "pointer.py")

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str, where: str | None = None) -> None:
        hits.append(((where or target.name), 1, msg))

    if set(kinds) != set(fields):
        only_kinds = sorted(set(kinds) - set(fields))
        only_fields = sorted(set(fields) - set(kinds))
        flag(
            f"SELECTOR_KINDS 与 SELECTOR_FIELDS 对不上"
            f"（只在 KINDS 里：{only_kinds}；只在 FIELDS 里：{only_fields}）"
        )

    for k in kinds:
        if not (isinstance(k, str) and k.endswith("Selector")):
            flag(f"selector 名 {k!r} 不以 Selector 结尾 —— 本系统照抄 W3C，不自造名字")

    for k, spec in fields.items():
        required, optional = spec
        declared = [*required, *optional]
        clash = sorted(set(declared) & set(body))
        if clash:
            flag(
                f"{k} 的规定字段里有 {clash} —— 那是承载正文的字段名。"
                "定位符只存位置：正文去 uri 取，别在仓库里留一份副本。"
            )

    if not pointer.POINTER_FIELD or pointer.POINTER_FIELD in body:
        flag(f"POINTER_FIELD={pointer.POINTER_FIELD!r} 不是一个像样的键名")

    for fn in ("verify", "verify_selector"):
        fn_body, start = _function_body(target, fn)
        if start is None:
            flag(f"pointer.py 里找不到 {fn}() —— 白名单没有人拿它拦")
        elif "raise PointerError" not in "\n".join(t for _, t in fn_body):
            flag(f"{fn}() 里没有 raise PointerError —— 它只定义了形状，没有拦")

    guard, gstart = _function_body(ROOT / "scaffold.py", "_append_revision")
    if gstart is None:
        flag("scaffold.py 里找不到 _append_revision()", where="scaffold.py")
    elif "pointer" not in "\n".join(t for _, t in guard):
        flag(
            "_append_revision() 里看不到 pointer —— 定位符的写入时守卫没接上。"
            "接在这一层才有用：它是 add_artifact 与 revise **共用**的唯一漏斗。",
            where="scaffold.py",
        )
    return hits


def check_vocabulary_is_fully_classified(
    *, kinds: tuple | None = None, rules: dict | None = None,
    prefixes: dict | None = None,
    relation_kinds: tuple | None = None, layers: dict | None = None,
) -> list[tuple[str, int, str]]:
    """**加一个节点类型或一种关系，必须同时分档** —— 不许悄悄加。

    这条盯的不是「有哪些类型」，而是「**新增词汇这件事有没有被看见**」。
    一张词汇表加了一项却忘了分档，后果不是报错，是它**默认落在最松的那一档上**
    （没人管）。而那件事在 diff 里几乎看不出来 —— 一条 `+ "xxx",` 而已。

    两张表用同一套判据，因为它们坏起来是同一种坏：

        `ARTIFACT_TYPES` × `CONTROL_RULES`     节点类型分档
        `RELATION_KINDS` × `RELATION_LAYERS`   关系种类分层

    判据：

    1. 每一档的并集**恰好等于**对应词汇表 —— 少了（忘了分档）多了（分档里写了不存在的）都报。
    2. 同一张表内各档**两两不交** —— 落在两档里等于两套规矩同时生效。
    3. 每一档**非空** —— 空档位等于没有这一档（判据就落空了）。
    4. `UPPER_ONLY_TYPES` / `INSTANCE_OF_REQUIRED_TYPES` 与对应那一档**逐字相等**
       （它们是给别处读的复述，复述走样就失去意义）。
    5. `_PREFIX` 的键集合恰好等于 `ARTIFACT_TYPES` ——
       加了类型忘了给前缀，会在**运行时** `KeyError`（`new_id()` 直接 `_PREFIX[type_]`）。
    6. ★ **上层边只有一条，而且这件事被钉住。** 见下方注释。

    ⚠️ 判据 6 是**刻意的硬编码**，理由与 `EXEMPT` 被钉住一样：
    上层对底层的影响面越小，单向性越容易守住。多一条上层边是**设计变更**，
    不是顺手加一条 —— 所以它必须撞到这条检查，逼一次有记录的改动。
    """
    kinds = kinds if kinds is not None else scaffold.ARTIFACT_TYPES
    rules = rules if rules is not None else scaffold.CONTROL_RULES
    prefixes = prefixes if prefixes is not None else scaffold._PREFIX
    relation_kinds = (
        relation_kinds if relation_kinds is not None else scaffold.RELATION_KINDS)
    layers = layers if layers is not None else scaffold.RELATION_LAYERS

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str) -> None:
        hits.append(("scaffold.py", 1, msg))

    def classify(what: str, universe, buckets: dict) -> None:
        covered: list[str] = []
        for bucket, members in buckets.items():
            if not members:
                flag(f"{what}：{bucket!r} 这一档是空的 —— 空档位等于没有这一档")
            covered.extend(members)

        dupes = sorted({t for t in covered if covered.count(t) > 1})
        if dupes:
            flag(f"{what}：{dupes} 同时落在多个档里 —— 两套规矩同时生效，等于没有规矩")

        missing = sorted(set(universe) - set(covered))
        if missing:
            flag(
                f"{what}：{missing} 在词汇表里，却没分档 —— "
                "它默认落在没人管的那一档上，而这在 diff 里几乎看不出来。"
                "加一项就要分档，这是这条检查存在的全部理由。"
            )
        unknown = sorted(set(covered) - set(universe))
        if unknown:
            flag(f"{what}：{unknown} 在分档表里，却不是已知词汇")

    classify("节点类型", kinds, rules)
    classify("关系种类", relation_kinds, layers)

    if tuple(rules.get("structural", ())) != tuple(scaffold.UPPER_ONLY_TYPES):
        flag("UPPER_ONLY_TYPES 与 CONTROL_RULES['structural'] 不一致 —— 复述走样了")
    if tuple(rules.get("instance_of_required", ())) != tuple(scaffold.INSTANCE_OF_REQUIRED_TYPES):
        flag("INSTANCE_OF_REQUIRED_TYPES 与对应那一档不一致 —— 复述走样了")

    if set(prefixes) != set(kinds):
        flag(
            f"_PREFIX 与 ARTIFACT_TYPES 对不上"
            f"（缺前缀：{sorted(set(kinds) - set(prefixes))}；"
            f"多余前缀：{sorted(set(prefixes) - set(kinds))}）—— "
            "`new_id()` 直接 `_PREFIX[type_]`，缺一个会在运行时 KeyError。"
        )

    upper_edges = tuple(layers.get("upper", ()))
    if len(upper_edges) != 1:
        flag(
            f"上层边有 {len(upper_edges)} 条（{list(upper_edges)}），不是 1 条 —— "
            "多一条上层边就是多一条「上层影响底层」的通道，那是**设计变更**。"
            "若确实要加，改这条检查并在工程稿「两条轴」一节里说明为什么。"
        )
    return hits


def check_imported_never_bypasses_the_gate(
    *, channels: tuple | None = None, states: tuple | None = None,
    passed: str | None = None, gate_path: Path | None = None,
    entry_path: Path | None = None, write_scan: bool = True,
) -> list[tuple[str, int, str]]:
    """导入层的节点**不得绕过入层门**直接算数（阶段 3 的出口判据 ②）。

    门挡在两处，缺一处就是摆设：

        `scaffold.activate()`   ← 唯一能写 `active` 的入口。挡在这里，
                                  才挡得住「不走 staging、直接建了再确认」
        `staging.py`            ← 唯一写 `staging` 表的模块。挡在这里，
                                  才挡得住「跳过记录」

    判据：

    1. 常量自洽：`staged` / `direct` 两个口都在，`GATE_STATES` 含 `passed`。
    2. ★ **`staging` 这张表只在一个文件里被写** —— 形状同 B10（发号器出不了
       `scaffold.py`）。任何别处出现 `INSERT INTO staging` 就报。
    3. `staging.py` 里必须出现 `intake="staged"` —— 它真的走那个口，
       而不是建完之后**宣称**自己走了。
    4. `scaffold.activate()` 的函数体里必须同时出现 `staged` 与 `GATE_PASSED`
       —— 门真的挡在唯一入口上。

    ⚠️ 它挡不住的，说清楚：调用方把 `intake` 填成 `"direct"` 就绕过去了。
    那是**撒谎**，不是漏洞。这条（静态）与 `test_staging.py`（行为）合起来
    才是完整的，形状同 B14。
    """
    channels = channels if channels is not None else scaffold.INTAKE_CHANNELS
    states = states if states is not None else scaffold.GATE_STATES
    passed = passed if passed is not None else scaffold.GATE_PASSED

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str, where: str = "scaffold.py") -> None:
        hits.append((where, 1, msg))

    for need in ("direct", "staged"):
        if need not in channels:
            flag(f"INTAKE_CHANNELS 里没有 {need!r} —— 两个口必须都在，缺一个就没法区分")
    if passed not in states or scaffold.GATE_PENDING not in states:
        flag(f"GATE_STATES={states} 不完整 —— 门的三个状态要都在")

    writer = None
    if write_scan:
        for path in sorted(ROOT.glob("*.py")):
            if path.name in EXEMPT:
                continue
            for n, text in code_lines(path):
                if re.search(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+staging\b", text, re.I):
                    writer = path.name
                    if path.name != "staging.py":
                        hits.append((path.name, n, text.strip()))

        if writer is None:
            flag(
                "没有任何模块写 `staging` 表 —— 门没有落点，这条检查无从谈起。",
                where="（全仓）",
            )

    gate_src = gate_path or (ROOT / "staging.py")
    if not gate_src.is_file():
        flag("staging.py 不在 —— 入层门还没有实现", where="（全仓）")
    else:
        joined = "\n".join(t for _, t in code_lines(gate_src))
        if 'intake="staged"' not in joined:
            flag('staging.py 里没有 intake="staged" —— 它没有真的走那个口',
                 where="staging.py")

    entry = entry_path or (ROOT / "scaffold.py")
    body, start = _function_body(entry, "activate")
    if start is None:
        flag(f"在 {entry.name} 里找不到 activate() —— 门无处可挡",
             where=entry.name)
    else:
        joined = "\n".join(t for _, t in body)
        for word in ("staged", "GATE_PASSED"):
            if word not in joined:
                flag(f"activate() 里看不到 {word} —— 门没挡在写 active 的唯一入口上",
                     where=entry.name)
    return hits


def check_distiller_mapping_is_explicit(
    *, mapping: dict | None = None, names: tuple | None = None,
    unmapped: tuple | None = None, kinds: tuple | None = None,
) -> list[tuple[str, int, str]]:
    """蒸馏的关系映射**必须显式** —— 没对应的不许偷偷兜住（阶段 3）。

    DeepRead 的八种关系与本仓库的 `RELATION_KINDS` 不是一套。
    落 staging 时保留原词、入层时映射 —— 于是映射表是**唯一的转换点**，
    而转换点最怕的失败模式是：**拿一个万能的 kind 兜住**。

    兜住之后会发生什么：一条具体的论证关系（「这条是那条的一个例子」）
    退化成「这两条有点关系」。**库里那条边长得完全正常**，只是信息没了。
    没有报错、没有告警、没有人会去看 —— 这是本仓库一直在防的那类病。

    判据：

    1. 映射表的键**恰好等于** DeepRead 的八种 —— 少一种说明上游变了没人管，
       多一种说明有人自己造了名字。
    2. ★ **值为 `None` 的集合，必须等于显式声明的 `UNMAPPED_DEEPRED`** ——
       这样「把 None 改成某个 kind」必须同时改两处，一定出现在 diff 里。
       ⚠️ 2026-09-28 之后两个集合都是空的（八种全部有了对应）。
       **判据是「两集合相等」而不是「非空」**，所以哪天有人把某格改回 `None`
       却忘了改声明，这条立刻会报 —— 空不等于失效。
    3. 每个非 `None` 的值必须是已知 kind；元组则每一项都要是，且非空。
    4. ★ **`related_to` 不得出现在映射表的值里** —— 它是那个「兜住一切」的选项。
       DeepRead 的八种里没有一种是「泛泛相关」，所以拿它兜住必然是丢信息。
    5. `map_relation()` 的函数体里必须有 `raise` —— 没对应就要停，
       不能返回一个缺省值。返回缺省值等于**静默兜住**。

    ⚠️ 判据 2 与 5 合起来才说明「那个 `None` 分支还在」。
    只有判据 2 的话，两集合同时为空时它就没在验任何东西了 ——
    所以 `test_staging.py` 里另有一条**行为**用例，往映射表里塞一个 `None`
    并断言 `map_relation()` 真的抛错。静态守形状，行为守它真的会拦。
    """
    mapping = mapping if mapping is not None else staging.DEEPRED_RELATION_MAP
    names = names if names is not None else staging.DEEPRED_RELATIONS
    unmapped = unmapped if unmapped is not None else staging.UNMAPPED_DEEPRED
    kinds = kinds if kinds is not None else scaffold.RELATION_KINDS

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str) -> None:
        hits.append(("staging.py", 1, msg))

    if set(mapping) != set(names):
        flag(
            f"映射表与 DeepRead 八种对不上"
            f"（缺：{sorted(set(names) - set(mapping))}；"
            f"多：{sorted(set(mapping) - set(names))}）"
        )

    declared = {k for k, v in mapping.items() if v is None}
    if declared != set(unmapped):
        flag(
            f"值为 None 的是 {sorted(declared)}，"
            f"而 UNMAPPED_DEEPRED 声明的是 {sorted(unmapped)} —— 两处必须一致。"
            "不一致说明有人把「没有对应」偷偷改成了某个 kind，"
            "而那正是这条检查要拦的动作。"
        )

    for name, mapped in mapping.items():
        values = mapped if isinstance(mapped, tuple) else (mapped,)
        if mapped is None:
            continue
        if not values:
            flag(f"{name!r} 映射到了一个空元组")
        for v in values:
            if v not in kinds:
                flag(f"{name!r} 映射到 {v!r}，而它不是已知的 relation kind")
            if v == "related_to":
                flag(
                    f"{name!r} 被映射到 related_to —— 那是「兜住一切」的那个选项。"
                    "DeepRead 的八种里没有一种是泛泛相关，所以拿它兜住必然是丢信息。"
                )

    body, start = _function_body(ROOT / "staging.py", "map_relation")
    if start is None:
        flag("staging.py 里找不到 map_relation()")
    elif "raise" not in "\n".join(t for _, t in body):
        flag(
            "map_relation() 里没有 raise —— 没对应时它会返回一个缺省值，"
            "那等于**静默兜住**，而兜住之后那条边在库里长得完全正常。"
        )
    return hits


def _tokens_in(path: Path, lo: int = 0, hi: int = 10 ** 9):
    """该文件里落在 `[lo, hi)` 行区间内的 token。**字符串与 f-string 的正文不是 token。**

    ⚠️ **B22 用 token 而不是正则，是被实测逼出来的**（2026-09-28）。
    第一版拿正则扫 `[+*/]` 与模型入口词，`rules.py` 一跑就报了 4 处「出现算术」——
    **4 处全是提示语里的中文标点与 Markdown 加粗标记**：
    `（§C7.1 ④ / §C2.5 第 3 档）` 里那个斜杠、`**按名字引用**` 里那两个星号。
    全是假命中。按修订五那条规矩（**假命中不许用豁免压下去**），改的是检查的范围。

    改成「先去掉字面量再扫」仍然不稳：f-string 在 3.12 之后**不是 STRING token**，
    得单独处理嵌套。直接看 token 更短也更准 ——
    字符串正文根本不是 `OP` / `NAME`，天然落在扫描范围外。
    """
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if lo <= tok.start[0] < hi:
            yield tok


# 算术只在这四个**判定函数**里禁。`render()` 不在内 ——
# 它里面的 `"=" * 64` 是排版，不是计算。把排版也算进去，
# 这条检查就会逼着人为了过检而改排版，那也是**假命中**。
_RULES_PURE_FUNCS = ("holds", "evaluate", "explain", "select")

# 出现即报的算术运算符。`+` / `*` 这类一旦能用在读数上，
# 规则集立刻能表达加权和 —— 那就是打分（撞 B5）。
_ARITH_OPS = frozenset({
    "+", "-", "*", "/", "//", "%", "**",
    "+=", "-=", "*=", "/=", "//=", "%=", "**=",
})

# 出现即报的名字：**模型入口**。
#
# 三个地方用它：B22（promote 条件的判据里）、B23（贡献入口）、B24（候选入口）。
#
# ⚠️ **只定义一次。** 原先 B22 与 B23 各写了一份**逐字相同**的正则 ——
# 那种抄两遍的字面量正是本仓库一直在防的：改一处漏一处，于是
# 「同一个词在两个检查里答案不同」，而两边都看起来正常。
#
# 只扫 `NAME` token —— 所以写在提示语里不算，写在代码里才算。
_MODEL_ENTRY_NAMES = re.compile(
    r"(model|llm|embed|similar|predict|infer|neural|prompt)\w*", re.I)
_RULES_DB_NAMES = frozenset({
    "sqlite3", "conn", "connection", "cursor", "executemany", "execute", "commit",
})

# 规则条目的**字段白名单**。同 `pointer.SELECTOR_FIELDS` 的手法：
# 「不许多存什么」的判据是**结构性的**（白名单之外一个字段都放不进去），
# 不是「扫一遍看看有没有敏感词」。加一个 `confidence` 之类的字段
# 等于让规则携带模型判断 —— 那就撞到这条检查存在的全部理由上。
_RULES_SPEC_FIELDS = ("all_of", "why")


def check_promotion_condition_is_structural(
    *, vocab: dict | None = None, ruleset: dict | None = None,
    operators: tuple | None = None, tier: str | None = None,
    tiers: tuple | None = None, floor: int | None = None,
    upper_vocab: dict | None = None, upper_floor: int | None = None,
    source_path: Path | None = None,
) -> list[tuple[str, int, str]]:
    """promote 条件**不许含模型判断**（阶段 5）—— 它的判据形态必须只由结构量构成。

    这条盯的是设计稿第 10 节那个空洞：`human / system validation` 里的
    **`system validation` 到底是什么**。若它是「模型觉得可以」，
    B14 那条不变量（上层不读热度类信号）当场就没了 ——
    因为「模型觉得可以」读的是文本相似度之类的**非结构量**。

    需求方 2026-09-28 拍板走 **B 档**：规则集允许把**已有结构信号的组合**写成判据。
    B 档与 C 档（从攻击图算接受集）的分界线是**可执行的一条**：

        规则的输出只能是「进不进清单」，不得是「有多好」

    所以判据里只有**合取**，没有加权和。一旦有人加算术，它就从结构判据变成了打分。

    判据：

    1. `TIER` 必须是 `derived_view`（`§C2.5` 第 3 档），且落在 `C25_TIERS` 里。
       本模块不写库、不下断言，所以免确认；挪到第 2 档就要补
       可推翻 + 抽样审计 + 计改判率三件事 —— 那是**设计变更**，必须撞到这里。
    2. `OPERATORS` **恰好**是那三个比较符 —— 规则集只许比较。
    3. ★ `SIGNAL_VOCAB` 与 `upper.COUNT_SIGNALS` **相等**（名字与读法都要）。
       「允许系统读哪些结构量」只能有一个答案：多一个是**越界通道**，
       少一个是**静默的残缺**。这是「两集合相等」那条老手法的第三次使用。
    4. ★ `FLOOR` 与 `upper.MIN_SUPPORT` 相等 —— 同一个结构下限有两处定义，
       分叉就会出现「归纳说够、规则说不够」这种没人解释得清的状态。
    5. 每条规则的 `all_of` 是**非空元组**，每个条件是三元组，
       信号名在词表内、比较符在 `OPERATORS` 内、比较值是整数。
    6. 每条规则必须有非空的 `why` —— 判定清单要能自述，
       那是 `§C2.5` 第 3 档「可解释」的落地，不是可选项。
    6b. 规则条目的字段是**白名单**：只有 `all_of` 与 `why`。
       多一个字段（信心、来源、模型输出）就是让规则携带别的东西，
       而它照样长得像一条结构规则。同 `pointer.SELECTOR_FIELDS` 的手法。
    7. 源码级（**看 token，不看正则**）：判定函数里没有算术运算符；
       全文没有模型入口名、没有数据库入口名。

    ⚠️ 判据 7 的最后一条是这条检查最要紧的地方，值得单独说：
    **`rules.py` 连数据库都碰不到**，所以「判定不写库」不是靠作者记得别写 ——
    它是**结构上没有能力写**。行为那半在 `test_rules.py`：
    跑一遍前后快照比对。

    --- 口径修订一：从正则改成 token（2026-09-28，当天）------------------------

    第一版拿正则扫 `[+*/]` 与模型入口词，`rules.py` 一跑报了 **7 处假命中**：

    | 报的 | 真相 |
    |---|---|
    | `evaluate()` / `select()` 里出现算术 ×4 | 提示语里的 `（§C7.1 ④ / §C2.5 第 3 档）` 与 `**按名字引用**` —— 中文正文里的斜杠不是除号，Markdown 加粗标记不是乘号 |
    | `evaluate()` / `explain()` / `select()` 里出现算术 ×3 | **`def` 行里的 `*,`（keyword-only 标记）** —— 它不是算术 |

    **一条真命中都没有。** 按修订五那条规矩（假命中不许用豁免压下去），
    改的是**检查的范围**：先去掉字面量（f-string 在 3.12 之后不是 STRING token，
    不好处理），最后干脆**直接看 token** —— 字符串正文根本不是 `OP` / `NAME`，
    而 `def` 那一行从扫描区间里排除。

    ⚠️ 静态拦不住什么，说清楚（同 B14 / B18 / B20 的既有立场）：
    它拦不住「调用方自己算一个分再传进 `select(rule=...)`」——
    但 `select` 只收**规则名**，模型判断传不进来（`RuleError`）。
    真正绕得过去的是「把模型判断写进 `SIGNAL_VOCAB` 的某个信号名背后」，
    而那要改 `upper.count_signals()` —— 那是另一条会被 B14 盯上的路。

    --- 口径修订二：模型入口按「包含」判，不按「以它开头」判（2026-09-28，当天）----

    写 B23 时发现这条的口径太窄：`_MODEL_ENTRY_NAMES.match()` 只认
    **名字以模型词开头**的标识符，于是

        call_the_model(text)      ← 漏
        ask_llm(prompt)           ← 漏
        score_by_similarity(x)    ← 漏

    这三种**一次都报不出来**，而它们恰恰是最常见的写法。`match` 抓到的是
    `model_judge()` 这种把模型词放在开头的命名 —— 那不是真实代码的样子。

    改 `search`。理由与 B6 的注释同一条：**否证检查漏报是致命的，误报只是吵。**
    反向判据不受影响 —— 字面量里的模型名不是 `NAME` token（见 `_tokens_in`）。
    """
    source_path = source_path or (ROOT / "rules.py")
    is_real = source_path.name == "rules.py"
    if is_real and not source_path.is_file():
        return [("rules.py", 1,
                 "规则集文件不在 —— `system validation` 就没落地，"
                 "promote 条件只能是模型判断，那是这条检查要防的东西。")]

    vocab = rules.SIGNAL_VOCAB if vocab is None else vocab
    ruleset = rules.RULES if ruleset is None else ruleset
    operators = rules.OPERATORS if operators is None else operators
    tier = rules.TIER if tier is None else tier
    tiers = rules.C25_TIERS if tiers is None else tiers
    floor = rules.FLOOR if floor is None else floor
    upper_vocab = upper.COUNT_SIGNALS if upper_vocab is None else upper_vocab
    upper_floor = upper.MIN_SUPPORT if upper_floor is None else upper_floor

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str, n: int = 1) -> None:
        hits.append((source_path.name, n, msg))

    # 1 档位
    if tier not in tiers:
        flag(f"判定档位 {tier!r} 不在 {list(tiers)} 里 —— 档位是 `§C2.5` 的封闭集合。")
    elif tier != "derived_view":
        flag(
            f"判定档位是 {tier!r}，不是 'derived_view' —— 本模块不写库、不下断言，"
            "所以它免确认；挪到第 2 档（派生标注）就要补「可推翻 + 抽样审计 + 计改判率」"
            "三件事。那是设计变更，不是顺手改一个字符串。"
        )

    # 2 比较符
    if set(operators) != {">=", "<=", "=="}:
        flag(
            f"比较符集合是 {sorted(operators)}，不是恰好那三个 —— "
            "规则集只许比较。加算术运算符（`+` / `*` / 平均）等于把它变成打分，撞 B5。"
        )

    # 3 词表与上层白名单相等
    if set(vocab) != set(upper_vocab):
        flag(
            f"规则词表与 upper.COUNT_SIGNALS 对不上"
            f"（规则多：{sorted(set(vocab) - set(upper_vocab))}；"
            f"规则少：{sorted(set(upper_vocab) - set(vocab))}）—— "
            "「允许系统读哪些结构量」只能有一个答案：多一个就是越界通道，"
            "少一个就是静默的残缺（规则看上去能说更多，实际读不到）。"
        )
    elif dict(vocab) != dict(upper_vocab):
        flag("两边键一样、读的东西不一样 —— 同一个信号名在两张表里指向不同的表 / 事件。")

    # 4 结构下限
    if floor != upper_floor:
        flag(
            f"规则集下限是 {floor}，upper.MIN_SUPPORT 是 {upper_floor} —— "
            "它们是同一个结构下限（「一条依据的簇不叫簇」）。两处定义分叉之后，"
            "会出现「归纳说够、规则说不够」这种没人解释得清的状态。"
        )

    # 5 / 6 规则集本身
    for rid in sorted(ruleset):
        spec = ruleset[rid]
        extra = sorted(set(spec) - set(_RULES_SPEC_FIELDS))
        if extra:
            flag(f"规则 {rid!r} 多带了字段 {extra} —— 规则条目的字段是**白名单**"
                 f"（只有 {list(_RULES_SPEC_FIELDS)}）。多一个字段就是让规则"
                 "携带别的东西（信心、来源、模型输出），而它照样长得像一条结构规则。")
        conditions = spec.get("all_of")
        if not isinstance(conditions, tuple):
            flag(f"规则 {rid!r} 的 all_of 不是元组 —— 单个条件会让「合取」"
                 "这件事在形状上消失，而形状正是 B 档与 C 档的分界。")
            continue
        if not conditions:
            flag(f"规则 {rid!r} 的 all_of 是空的 —— 空合取恒真，"
                 "它会把所有目标都收进来，等于一条没有判据的规则。")
        # ⚠️ 一种很常见的写法错误：单条件忘了包一层 ——
        # `all_of = ("challenge_counts", ">=", 2)`。它**是个三元组、看着像对的**，
        # 但语义上它成了「三个条件」，而三个都不是三元组。
        # 不单独认出来的话，报出来的是三句「有个条件不是三元组：'challenge_counts'」——
        # 每句都成立，合起来却指不到真正的那一处。
        elif (len(conditions) == 3 and isinstance(conditions[0], str)
                and isinstance(conditions[1], str)):
            flag(f"规则 {rid!r} 的 all_of 看起来是**单个条件没包成合取**："
                 f"{conditions!r} —— 要写成 `((信号, 比较符, 值),)` 才算一条规则。"
                 "这不是格式问题：**形状就是「合取」这件事本身**。")
            continue
        for cond in conditions:
            if not isinstance(cond, tuple) or len(cond) != 3:
                flag(f"规则 {rid!r} 里有个条件不是 (信号, 比较符, 值) 三元组：{cond!r}")
                continue
            name, op, want = cond
            if name not in vocab:
                flag(f"规则 {rid!r} 读 {name!r} —— 它不在 SIGNAL_VOCAB 里。"
                     "词表是封闭的：能读什么只能有一个答案。")
            if op not in operators:
                flag(f"规则 {rid!r} 用了比较符 {op!r}，它不在 OPERATORS 里。")
            if isinstance(want, bool) or not isinstance(want, int):
                flag(f"规则 {rid!r} 的比较值 {want!r} 不是整数 —— "
                     "判据只许跟结构量的次数比。")
        why = spec.get("why")
        if not (isinstance(why, str) and why.strip()):
            flag(f"规则 {rid!r} 没有 why —— 判定清单要能自述。"
                 "`§C2.5` 第 3 档的「可解释」不是可选的。")

    # 7 源码级（**看 token，不看正则** —— 理由见 `_tokens_in`）
    for name in _RULES_PURE_FUNCS:
        body, start = _function_body(source_path, name)
        if start is None:
            flag(f"{source_path.name} 里找不到 {name}() —— 判定的形状就没人守了。")
            continue
        hi = (body[-1][0] + 1) if body else (start + 1)
        # ⚠️ 从 `start + 1` 起，**跳过 def 那一行**：`*,`（keyword-only 标记）
        # 与 `**kwargs` 里的星号也是 `OP`，它们不是算术。实测被这两个报过假命中。
        for n, op in [(t.start[0], t.string)
                      for t in _tokens_in(source_path, start + 1, hi)
                      if t.type == tokenize.OP and t.string in _ARITH_OPS]:
            flag(f"{name}() 里出现算术运算符 {op!r} —— 判据只许比较，"
                 "不许把两个读数合起来算。一旦能算，规则集立刻能表达加权和，"
                 "那就是打分（撞 B5）。", n)

    for tok in _tokens_in(source_path):
        if tok.type != tokenize.NAME:
            continue
        # ⚠️ `search` 不是 `match` —— 见下方「口径修订二」。
        if _MODEL_ENTRY_NAMES.search(tok.string):
            flag("出现模型入口 —— promote 条件不许含模型判断（阶段 5 出口判据 ③）。"
                 "「模型觉得可以」读的是相似度之类的非结构量，B14 那条不变量当场就没了。",
                 tok.start[0])
        elif tok.string in _RULES_DB_NAMES:
            flag(f"出现数据库入口 {tok.string!r} —— 判定必须**没有能力写库**，"
                 "否则它就不是 `§C2.5` 第 3 档（免确认）而是第 2 档。", tok.start[0])
    return hits


def _function_body(path: Path, name: str):
    """(函数体的 (行号, 文本) 列表, 起始行号)。

    从 `def <name>(` 那一行之后起，到下一个顶格 `def ` 或文件末尾止。
    找不到时返回 `([], None)`。
    """
    body: list[tuple[int, str]] = []
    start = None
    for n, text in code_lines(path):
        if start is None:
            if text.lstrip().startswith(f"def {name}("):
                start = n
            continue
        if text.lstrip().startswith("def "):
            break
        body.append((n, text))
    return body, start


def _ast_defs(path: Path) -> dict:
    """该文件顶层的函数定义：名字 → `ast.FunctionDef` 节点。

    为什么这里用 `ast` 而不是 B22 那套 token / 正则：这条要判的是**签名**——
    「哪个参数带了默认值」在 token 流里是看不见的（得自己对齐 `=` 与参数位置）。
    `ast` 是标准库，不引入新依赖（B7 盯着）。B22 那边继续用 token，
    因为它的判据是「函数体里有没有算术」，那件事 token 就够，不必上 `ast`。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _imported_tops(path: Path) -> list[str]:
    """该文件 import 的顶层模块名（相对导入不计）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            out.append((node.module or "").split(".")[0])
    return sorted(set(out))


_CONTRIB_IMPORT_WHITELIST = ("__future__", "sqlite3", "scaffold")
_CONTRIB_DIRECTIONS = ("to_target", "from_target", "between", None)
# `§C2.5` 第 2 档**原文点名**的那几个：新建可被独立引用的对象必须确认。
# 落 `proposed` 的类型必须落在这里面 —— 这一条是**刻意的硬编码**，
# 理由与 B19 判据 6（上层边只有一条）和 `EXEMPT` 被钉住一样：
# 分档一旦能自由漂，「哪几种贡献需要确认」就没有唯一答案了。
_C25_MUST_CONFIRM = ("Topic", "Claim", "Subtopic")
_CONTRIB_REQUIRED_FIELDS = ("type_", "relation", "direction", "state")

# --- B24（阶段 5b 候选关系）用的常量 -----------------------------------------
_CAND_IMPORT_WHITELIST = ("__future__", "sqlite3", "rules", "scaffold")
# 「算数」的状态 —— 刻意的硬编码，理由同 B19 判据 6 与 B23 的 `_C25_MUST_CONFIRM`：
# 多一个算数的状态，就是多一条「机器提的边自动进读数」的通道，
# 而那是设计反转级的改动，不是顺手加一条。
_CAND_COUNTED_STATES = ("active",)


def check_contribution_entrypoints_are_graph_free(
    *, granularities: tuple | None = None, table: dict | None = None,
    fields: tuple | None = None, states: tuple | None = None,
    forbidden: tuple | None = None, actor: str | None = None,
    artifact_types: tuple | None = None, relation_kinds: tuple | None = None,
    source_path: Path | None = None,
) -> list[tuple[str, int, str]]:
    """社区贡献入口不许要求调用方懂图结构（阶段 4 的出口判据 ③）。

    出口判据 ③ 原文是「**首次贡献者不需要理解 graph 结构**」。
    这句话**默认不可执行** —— 它长得像一条体验要求，谁都能声称满足了。
    可执行的形式只有一个：

        七种粒度 → (节点类型, 关系种类, 方向, 落点) 的映射**只出现在一张常量表里**，
        七个入口的签名里**一个词表参数都没有**。

    于是「调用方自己挑一个关系种类」这件事**没有地方可以发生** ——
    不是「约定别挑」，是签名上没得挑。这跟 B14「没有 `conn` 就没法写库」
    是同一个做法：把纪律换成**能力上的不可能**。

    判据：

    1. ★ `CONTRIBUTIONS` 的键与 `GRANULARITIES` **相等**。
       少一个是「有一种贡献做不出来」，多一个是「有一种没在判据里」。
       这是「两集合相等」那条老手法的第五次使用。
    2. 每条的字段 ⊆ `CONTRIBUTION_FIELDS`（**白名单**，同 `pointer.SELECTOR_FIELDS`），
       且四个必填字段齐全。多一个字段（信心、来源、模型输出）就是让某一种贡献
       携带别的东西，而它照样长得像一条贡献定义。
    3. 取值封闭：`type_` ∈ `ARTIFACT_TYPES` ∪ {None}、`relation` / `choices` ⊆
       `RELATION_KINDS`、`direction` ∈ 四个取值、`state` ∈ `CONTRIBUTION_STATES` ∪ {None}。
       **不许在这一层发明词表** —— 加了新种类必须先过 B19。
    4. ★ 分档**双向**钉住：`state == "proposed"` ⟺ 类型在 `§C2.5` 第 2 档的名单里。
       把 `claim` 的落点改成默认生效，就等于把「未确认的东西不许算数」
       从贡献入口上拿掉了 —— 而库里看不出任何异常。
       只写单向会漏掉**正是这条要拦的那个动作**（实测：单向版一次都报不出来）。
    5. 七个入口函数**逐个存在**；签名里**没有** `FORBIDDEN_PARAMS`；
       `by` 必填且**没有默认值**（有默认值就是给「机器自己提交」留了条路）。
    6. ★ import 白名单只有 `__future__` / `sqlite3` / `scaffold` ——
       **尤其不许 import `upper`**。贡献层只写底层，归组是上层的事；
       两者不在一个模块里，「上层的判断被写成底层的事实」在这条路上
       就**没有入口**。这是单向性在贡献层的落点（B14 的邻居）。
    7. 源码级（**看 token**）：全文没有模型入口名。
       七种入口一个都不该调模型 —— 它们只把人的话落成结构。

    ⚠️ 静态拦不住什么，说清楚（同 B14 / B18 / B20 / B22 的既有立场）：
    它拦不住「有人绕开这七个函数，直接调 `scaffold.add_artifact`」——
    那本来就是允许的（那是原语层）。这条盯的是**这一层自己的形状**：
    入口不许把词表漏出去。
    """
    source_path = source_path or (ROOT / "contribute.py")
    is_real = source_path.name == "contribute.py"
    if is_real and not source_path.is_file():
        return [("contribute.py", 1,
                 "贡献入口层不在 —— 七种粒度就没落地，"
                 "阶段 4 的出口判据 ① 与 ③ 都无从谈起。")]

    granularities = contribute.GRANULARITIES if granularities is None else granularities
    table = contribute.CONTRIBUTIONS if table is None else table
    fields = contribute.CONTRIBUTION_FIELDS if fields is None else fields
    states = contribute.CONTRIBUTION_STATES if states is None else states
    forbidden = contribute.FORBIDDEN_PARAMS if forbidden is None else forbidden
    actor = contribute.CONTRIBUTION_ACTOR if actor is None else actor
    artifact_types = scaffold.ARTIFACT_TYPES if artifact_types is None else artifact_types
    relation_kinds = scaffold.RELATION_KINDS if relation_kinds is None else relation_kinds

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str, n: int = 1) -> None:
        hits.append((source_path.name, n, msg))

    # 1 两集合相等
    if set(table) != set(granularities):
        flag(f"CONTRIBUTIONS 与 GRANULARITIES 对不上"
             f"（表里多：{sorted(set(table) - set(granularities))}；"
             f"表里少：{sorted(set(granularities) - set(table))}）—— "
             "「有哪几种贡献」只能有一个答案：少一个是做不出来，"
             "多一个是没在判据里。")

    # 2 / 3 / 4 每一条贡献定义
    for name in sorted(table):
        spec = table[name]
        extra = sorted(set(spec) - set(fields))
        if extra:
            flag(f"贡献 {name!r} 多带了字段 {extra} —— 条目的字段是**白名单**"
                 f"（只有 {list(fields)}）。多一个字段就是让某一种贡献携带别的东西"
                 "（信心、来源、模型输出），而它照样长得像一条贡献定义。")
        missing = sorted(set(_CONTRIB_REQUIRED_FIELDS) - set(spec))
        if missing:
            flag(f"贡献 {name!r} 少了必填字段 {missing} —— 缺了它，"
                 "「这一种落成什么」就有一半没有答案。")

        t = spec.get("type_")
        if t is not None and t not in artifact_types:
            flag(f"贡献 {name!r} 落成 {t!r}，它不是已知的节点类型 —— "
                 "这一层不许发明词表，加类型必须先过 B19。")
        kind = spec.get("relation")
        if kind is not None and kind not in relation_kinds:
            flag(f"贡献 {name!r} 用了 {kind!r}，它不是已知的关系种类。")
        for c in spec.get("choices", ()):
            if c not in relation_kinds:
                flag(f"贡献 {name!r} 的可选边里有 {c!r}，它不是已知的关系种类。")
        if kind is not None and spec.get("choices") and kind not in spec["choices"]:
            flag(f"贡献 {name!r} 的默认边 {kind!r} 不在它自己的 choices 里 —— "
                 "默认值必须是一个合法取值，否则默认那条路一调就抛。")
        if spec.get("direction") not in _CONTRIB_DIRECTIONS:
            flag(f"贡献 {name!r} 的 direction 是 {spec.get('direction')!r}，"
                 f"不在 {list(_CONTRIB_DIRECTIONS)} 里。")
        st = spec.get("state")
        if st is not None and st not in states:
            flag(f"贡献 {name!r} 的落点是 {st!r}，不在 {list(states)} 里。")
        # ★ 分档**双向**钉住：`state == "proposed"` ⟺ 类型在 `§C2.5` 第 2 档的名单里。
        #
        # 为什么必须双向（这条是实测补的）：只写「落 proposed 的类型必须在名单里」
        # 那个方向，把 `claim` 的落点从 `proposed` 改成 `active` **一次都报不出来** ——
        # 而那一改正是这条要拦的动作：它把「未确认的东西不许算数」
        # 从贡献入口上拿掉了，库里却看不出任何异常（节点照样有 id、照样能引用）。
        if t is not None:
            must_confirm = t in _C25_MUST_CONFIRM
            if st == "proposed" and not must_confirm:
                flag(f"贡献 {name!r} 落 `proposed`，但它的类型是 {t!r} —— "
                     f"`§C2.5` 第 2 档点名的是 {list(_C25_MUST_CONFIRM)}。"
                     "落点是分档的一部分：给一个不是新断言的东西加确认环节，"
                     "等于让「确认」这个动作失去含义。")
            elif must_confirm and st != "proposed":
                flag(f"贡献 {name!r} 的类型是 {t!r}，它在 `§C2.5` 第 2 档的名单里，"
                     f"落点却是 {st!r} —— 新建可被独立引用的对象**必须确认**。"
                     "改成默认生效之后，「未确认的东西不许算数」就在这一层上没了，"
                     "而库里看不出任何异常。")

    # 5 七个签名（ast）
    defs = _ast_defs(source_path)
    for name in granularities:
        node = defs.get(name)
        if node is None:
            flag(f"{name}() 不见了 —— 七种粒度就少一种。")
            continue
        pos = list(node.args.posonlyargs) + list(node.args.args)
        kw = list(node.args.kwonlyargs)
        params = [a.arg for a in pos + kw]
        clash = sorted(set(params) & set(forbidden))
        if clash:
            flag(f"{name}() 收了词表参数 {clash} —— 调用方一旦能传它，"
                 "这一层就退化成原语的薄包装，而它看起来还是七个漂亮的名字。")
        if actor not in params:
            flag(f"{name}() 没有 {actor} 参数 —— 谁提交的就记不下来，"
                 "而「机器自己提交一条贡献」看起来和这一模一样。")
            continue
        with_default = [a.arg for a, d in
                        zip(pos[-len(node.args.defaults):], node.args.defaults) if d]
        with_default += [a.arg for a, d in zip(kw, node.args.kw_defaults) if d]
        if actor in with_default:
            flag(f"{name}() 的 {actor} 带了默认值 —— 留一个默认值，"
                 "就等于给「机器自己提交」留了一条路，而那条路看起来很正常。")

    # 6 import 白名单
    for top in _imported_tops(source_path):
        if top not in _CONTRIB_IMPORT_WHITELIST:
            flag(f"import 了 {top!r} —— 贡献层的 import 白名单只有 "
                 f"{list(_CONTRIB_IMPORT_WHITELIST)}。"
                 "**尤其不许 import upper**：贡献进来的是事实，归组是上层的事，"
                 "两者不在一个模块里，「上层判断被写成底层事实」才没有入口。")

    # 7 模型入口（**看 token** —— 理由见 `_tokens_in`）
    #
    # ⚠️ 用 `search` 不用 `match`（口径修订，见 B22 的同款说明）：
    # `match` 只认「名字以模型词开头」，于是 `call_the_model()` / `ask_llm()`
    # 这类写法**一次都报不出来** —— 而它们恰恰是最常见的写法。
    # 否证检查漏报是致命的（B6 的注释里写得很清楚），所以这里按「包含」判。
    for tok in _tokens_in(source_path):
        if tok.type == tokenize.NAME and _MODEL_ENTRY_NAMES.search(tok.string):
            flag(f"出现模型入口 {tok.string!r} —— 七种入口一个都不该调模型，"
                 "它们只把人的话落成结构。调模型的那一层是上层的归组，不是这一层。",
                 tok.start[0])
    return hits


def check_candidates_cannot_make_themselves_true(
    *, source_path: Path | None = None,
    state: str | None = None, routes: tuple | None = None,
    route_names: dict | None = None, forbidden: tuple | None = None,
    counted: tuple | None = None, relation_states: tuple | None = None,
    origin_prefixes: tuple | None = None,
) -> list[tuple[str, int, str]]:
    """候选关系不许把自己变成算数的（阶段 5b 出口判据 ①）。

    出口判据 ① 原文是「**AI 产出只能是 candidate**」。这句话的**后果**是：
    一条机器提的边进得了库，但**进不了读数**。两件事合起来才叫落地：

        结构上：候选入口**没有能力**写 `active`（签名里没有 `state`）
        读数上：`active` 是**唯一**算数的状态（`COUNTED_RELATION_STATES`）

    只做前一件，候选边可能被当成 active 数进去；只做后一件，
    入口可以随手指派状态。**两件都要。**

    判据：

    1. ★ `CANDIDATE_STATE` 必须**真的在** `scaffold.RELATION_STATES` 里。
       少了这一条，`record()` **每一次调用**都会在运行时抛 `ScaffoldError`
       —— 而那是跑起来才发现的事。阶段 5b 之前**正是**这个状态：
       节点有 `proposed`、边没有，于是「一条边在被确认之前」根本表达不出来。
       这条判据把那个空洞钉住，不让它悄悄回来。
    2. ★ `COUNTED_RELATION_STATES` **恰好是 `("active",)`**，且
       `CANDIDATE_STATE` **不在**里面。前者是刻意的硬编码（理由同 B19 判据 6
       与 B23 的分档名单）：多一个算数的状态，就是多一条「机器提的边自动算数」
       的通道 —— 那是设计反转级的改动，必须撞检查、留一次有记录的改动。
    3. `record()` 存在；签名里**没有** `FORBIDDEN_PARAMS`；`origin` 必填且
       **没有默认值**（留默认值等于给「没人提过这条边」留个位置）。
    4. `promote()` 存在；签名里**没有** `FORBIDDEN_PARAMS`；`PROMOTE_ROUTES`
       的每一个都**存在且带默认值**（两条通路都必须是可选的，
       「恰好一条」才判得出来）；`PROMOTE_ROUTE_NAMES` 与 `PROMOTE_ROUTES` 同集。
    5. ★ `ORIGIN_PREFIXES` 是一组**封闭**前缀（非空、每个形如 `ai:`），
       且 `record()` 的**函数体里真的调了** `origin_class()`。
       签名要求 `origin` 只保证「填了」，这一条才保证「填得对」——
       少了它，库里又会回到「`gpt` 和 `alice` 长得一样」，而 5b 的全部意义
       就是「机器提议、人来提拔」。
    6. ★ import 白名单只有 `__future__` / `sqlite3` / `rules` / `scaffold` ——
       **尤其不许 import `upper`**。候选层只提议底层边；归组是上层的独占权限
       （B19 钉着「上层边只有一条」）。这是单向性在候选层的落点（B14 的邻居）。
    7. 源码级（**看 token**）：全文没有模型入口名。
       本模块**不许**自己调模型 —— 「AI 建议候选」那一步在应用层，
       核心层只负责**接收**它送来的结果。

    ⚠️ 静态拦不住什么，说清楚（同 B14 / B18 / B20 / B22 / B23 的既有立场）：
    它拦不住「有人绕开 `record()`，直接 `add_relation(state='active')`」——
    那本来就是允许的（那是原语层）。这条盯的是**这一层自己的形状**：
    候选入口不许有指派状态的能力。
    """
    source_path = source_path or (ROOT / "candidates.py")
    is_real = source_path.name == "candidates.py"
    if is_real and not source_path.is_file():
        return [("candidates.py", 1,
                 "候选关系层不在 —— 阶段 5b 的出口判据 ①②③ 都无从谈起。")]

    state = candidates.CANDIDATE_STATE if state is None else state
    routes = candidates.PROMOTE_ROUTES if routes is None else routes
    route_names = (candidates.PROMOTE_ROUTE_NAMES if route_names is None
                   else route_names)
    forbidden = candidates.FORBIDDEN_PARAMS if forbidden is None else forbidden
    counted = (scaffold.COUNTED_RELATION_STATES if counted is None else counted)
    relation_states = (scaffold.RELATION_STATES if relation_states is None
                       else relation_states)
    prefixes = (candidates.ORIGIN_PREFIXES if origin_prefixes is None
                else origin_prefixes)

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str, n: int = 1) -> None:
        hits.append((source_path.name, n, msg))

    # 1 候选状态必须真的在词表里
    if state not in relation_states:
        flag(f"CANDIDATE_STATE 是 {state!r}，它不在 RELATION_STATES "
             f"{list(relation_states)} 里 —— 少了这一条，`record()` "
             "**每一次调用**都会在运行时抛，而那是跑起来才发现的事。"
             "阶段 5b 之前正是这个状态：节点有 `proposed`、边没有，"
             "于是「一条边在被确认之前」根本表达不出来。")

    # 2 候选不算数，且「算数」只有 active
    if tuple(counted) != _CAND_COUNTED_STATES:
        flag(f"COUNTED_RELATION_STATES 是 {tuple(counted)}，"
             f"不是 {_CAND_COUNTED_STATES} —— 「哪些状态算数」多一个，"
             "就多一条「机器提的边自动进读数」的通道，而库里看不出任何异常"
             "（边有 id、查得到、长得完全正常）。")
    if state in counted:
        flag(f"候选状态 {state!r} 被列进了 COUNTED_RELATION_STATES —— "
             "那等于说「AI 提的边自动算数」，出口判据 ① 当场没了。")
    unknown = sorted(set(counted) - set(relation_states))
    if unknown:
        flag(f"COUNTED_RELATION_STATES 里有 {unknown}，它们不是已知的关系状态 —— "
             "算数的状态必须是真状态，否则那句 SQL 永远匹配不上，"
             "而读数会静默变成空。")

    # 3 / 4 两个签名（ast）
    defs = _ast_defs(source_path)

    def params_of(name: str) -> tuple[list[str], dict]:
        node = defs[name]
        pos = list(node.args.posonlyargs) + list(node.args.args)
        kw = list(node.args.kwonlyargs)
        defaults: dict[str, object] = {}
        for a, d in zip(pos[-len(node.args.defaults):], node.args.defaults):
            defaults[a.arg] = d
        # ⚠️ `kw_defaults` 用 **`None` 本身**表示「这个参数没有默认值」——
        # 而「默认值是 `None`」长成 `ast.Constant(value=None)`。
        # 两者必须分开：写 `defaults[a.arg] = d` 会把**每一个必填的
        # keyword-only 参数**都记成「带了默认值」—— 实测 B24 第一版
        # 就是这样把 `record(..., origin)` 报成了漏。
        for a, d in zip(kw, node.args.kw_defaults):
            if d is not None:
                defaults[a.arg] = d
        return [a.arg for a in pos + kw], defaults

    rec = defs.get("record")
    if rec is None:
        flag("record() 不见了 —— 候选边就没有入口了。")
    else:
        names, defaults = params_of("record")
        clash = sorted(set(names) & set(forbidden))
        if clash:
            flag(f"record() 收了 {clash} —— 出现 `state=` 的那一刻，"
                 "这个入口就能写出 `active`，出口判据 ① 当场没了，"
                 "而它看起来还是个「候选入口」。")
        if "origin" not in names:
            flag("record() 没有 origin 参数 —— 一条候选边「是谁提的」"
                 "就没地方记，而它看起来和正常的一条一模一样。")
        elif "origin" in defaults:
            flag("record() 的 origin 带了默认值 —— 留一个默认值等于给"
                 "「没人提过这条边」留了个位置，而它看起来很正常。")

    pro = defs.get("promote")
    if pro is None:
        flag("promote() 不见了 —— 候选边就永远算不了数。")
    else:
        names, defaults = params_of("promote")
        clash = sorted(set(names) & set(forbidden))
        if clash:
            flag(f"promote() 收了 {clash} —— 那是让调用方**直接指派状态**，"
                 "等于绕开「恰好一条通路」。")
        for route in routes:
            if route not in names:
                flag(f"promote() 没有 {route!r} 参数 —— "
                     f"通路 {list(routes)} 就少了一条。")
            elif route not in defaults:
                flag(f"promote() 的 {route!r} 是必填的 —— 两条通路都必须"
                     "**可选**，否则「恰好一条」这个判据根本不成立。")
        if set(routes) != set(route_names):
            flag(f"PROMOTE_ROUTES {list(routes)} 与 PROMOTE_ROUTE_NAMES "
                 f"{sorted(route_names)} 不是同一个集合 —— "
                 "前者说「有几条路」，后者说「路上记什么名字」，"
                 "分叉之后 `event` 里会出现一条没人认得的路。")

    # 5 `origin` 的词表（阶段 5b 补 · 2026-09-29 · 工程稿 §11.5）
    #
    # 这一条补的是判据 3 管不到的那一半：签名要求 `origin`，只保证**填了**；
    # 「填得对」要另有一条判据，否则自由文本里写 `gpt` 和写 `alice`
    # 在库里长得一样。
    if not prefixes:
        flag("ORIGIN_PREFIXES 是空的 —— 那 `origin` 就退化成自由文本，"
             "一条候选边「是人提的还是机器提的」在库里长得一样。")
    for p in prefixes:
        if not (isinstance(p, str) and len(p) > 1
                and p.endswith(candidates.ORIGIN_SEPARATOR)):
            flag(f"ORIGIN_PREFIXES 里的 {p!r} 不是一个前缀 —— "
                 f"要形如 'ai{candidates.ORIGIN_SEPARATOR}'。"
                 "判前缀用的是 `startswith`，形状不对就会漏判或误判，"
                 "而两种都看不出来。")
    if rec is not None:
        called = {n.func.id for n in ast.walk(rec)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if "origin_class" not in called:
            flag("record() 的**函数体里没有调** `origin_class()` —— "
                 "那 `origin` 就只保证「填了」，不保证「填得对」。"
                 "必填 ≠ 填得对，这正是工程稿 §11.5 要补的那一件事："
                 "签名照样要求 `origin`，而库里又回到「`gpt` 和 `alice` "
                 "长得一样」。")

    # 6 import 白名单
    for top in _imported_tops(source_path):
        if top not in _CAND_IMPORT_WHITELIST:
            flag(f"import 了 {top!r} —— 候选层的 import 白名单只有 "
                 f"{list(_CAND_IMPORT_WHITELIST)}。"
                 "**尤其不许 import upper**：候选层只提议底层边，"
                 "归组是 `upper.py` 的独占权限（B19 钉着「上层边只有一条」）。")

    # 7 模型入口（**看 token**，用 `search` 不用 `match` —— 理由见 B22 口径修订二）
    for tok in _tokens_in(source_path):
        if tok.type == tokenize.NAME and _MODEL_ENTRY_NAMES.search(tok.string):
            flag(f"出现模型入口 {tok.string!r} —— 候选层不许自己调模型。"
                 "「AI 建议候选」那一步在应用层，核心层只负责**接收**"
                 "它送来的结果。", tok.start[0])
    return hits


# --- B25（阶段 6 物化视图）用的常量 -------------------------------------------

# 一条写语句的目标表。**判据 1 就是拿它比 `VIEW_TABLE`。**
_VIEW_WRITE = re.compile(
    r"\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE)

# 视图层**允许调用**的 `scaffold.*` —— 只有只读的两个。
#
# ⚠️ 白名单，不是黑名单。黑名单（列出 `add_artifact` / `activate` / ...）会漏掉
# 将来新增的写函数；白名单是**新函数默认被挡住**。
# 手法同 `pointer.SELECTOR_FIELDS`：把「不许」写成「只有这些」。
_VIEW_READ_ONLY_SCAFFOLD = ("now", "get")

# 判据 ② 必须**每次重建都跑**，不能只在测试里。这两个函数体里都要调它。
_VIEW_GUARD = "_lower_must_not_move"


def _view_columns(schema_text: str, table: str) -> list[str] | None:
    """从建表语句里抠出**列名**。表级约束（`UNIQUE (...)`）不算列。

    ⚠️ 不能直接按 `,` 切：`UNIQUE (view, position)` 自己就带一个逗号，
    切完会多出一个叫 `position)` 的「列」—— 于是白名单判据**永远**报一笔假命中，
    而那条报错读起来完全合理。

    ⚠️ 找不到那张表时返回 **`None`**，不是空列表：
    「schema 里没有这张表」和「这张表一列都没有」是两件事，
    混起来会让检查在探针喂错 schema 时直接 `IndexError` ——
    **检查崩掉比报错更糟**，报错至少还指得出问题在哪。
    """
    marker = f"{table} ("
    if marker not in schema_text:
        return None
    body = schema_text.split(marker, 1)[1].rsplit(");", 1)[0]
    body = re.sub(r"UNIQUE\s*\([^)]*\)", "", body)
    return [chunk.strip().split()[0] for chunk in body.split(",")
            if chunk.strip()]


def _canonical_tables(schema_text: str) -> list[str]:
    """`scaffold.SCHEMA` 里的建表清单 —— **唯一**的事实来源。"""
    return sorted(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", schema_text))


def check_the_view_layer_cannot_write_back(
    *, source_path: Path | None = None,
    view_table: str | None = None, fields: tuple | None = None,
    lower: tuple | None = None, schema: str | None = None,
    view_schema: str | None = None,
    guard: str = _VIEW_GUARD,
    read_only: tuple = _VIEW_READ_ONLY_SCAFFOLD,
) -> list[tuple[str, int, str]]:
    """物化视图**永不回写写模型**（阶段 6 的出口判据 ①②③）。

    照抄 CQRS 的 read model。这条是本仓库里**最容易变成一句空话**的那类承诺 ——
    「视图只是缓存」听起来不可能出错，于是没人防它；而真正的失败方式很安静：
    重建的时候顺手记一条事件、或者干脆把正文抄一份进视图表。
    两种都让**缓存变成了第二份真相**，而它看起来还是张缓存表。

    判据：

    1. ★ 源码里**每一条**写语句（`INSERT INTO` / `UPDATE` / `DELETE FROM`）
       的目标**只能是** `VIEW_TABLE`。这是「投影永不回写写模型」的静态落点。
    2. ★ `VIEW_TABLE` **不在** `scaffold.SCHEMA` 的建表清单里 ——
       存储轴（Canonical / Materialized）与写权限轴是**两条正交的轴**（工程稿 §七）。
       混进去之后，「删了就没了」和「删了零损失」就同时挂在同一张表上。
    3. ★ `VIEW_SCHEMA` 建的列**恰好**是 `VIEW_FIELDS`（字段白名单，手法同
       `pointer.SELECTOR_FIELDS`）。多一列 `text` / `content` / `body`，
       就是把底层对象**复制**进来了 —— 而副本无法证明自己等于原文。
    4. ★ `LOWER_TABLES` **恰好等于** `scaffold.SCHEMA` 的表集合。
       少一张，那张表被视图改了也看不出来 —— 而快照是判据 ②③ 的全部依据。
    5. ★ `rebuild()` 与 `rebuild_all()` 的**函数体里都调了** `_lower_must_not_move()`
       —— 判据 ② 落在**运行时**（每次重建自比一次），不是只落在测试里。
       只写签名不写调用的话，「重建不动底层」就退化成一句注释。
    6. ★ `scaffold.*` 的调用只许是**只读**的（`now` / `get`）。
       判据 1 只看得见**直接写 SQL**；`scaffold.record_event()` 这类**间接写**
       它一个字都看不见 —— 而那正是最可能发生的一种（「顺手记一条事件」）。
       两条一起才完整，分工同 B14 与 `test_upper` 那条行为用例。

    ⚠️ 静态拦不住什么，说清楚（同 B14 / B18 / B20 / B22 / B23 / B24 的既有立场）：
    它拦不住「绕开本模块，直接用 `scaffold` 写底层」—— 那本来就是允许的。
    这条盯的是**视图层自己的形状**。
    """
    source_path = source_path or (ROOT / "views.py")
    is_real = source_path.name == "views.py"
    if is_real and not source_path.is_file():
        return [("views.py", 1,
                 "物化视图层不在 —— 阶段 6 的出口判据 ①②③ 都无从谈起。")]

    view_table = views.VIEW_TABLE if view_table is None else view_table
    fields = views.VIEW_FIELDS if fields is None else fields
    lower = views.LOWER_TABLES if lower is None else lower
    schema = scaffold.SCHEMA if schema is None else schema
    view_schema = views.VIEW_SCHEMA if view_schema is None else view_schema

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str, n: int = 1) -> None:
        hits.append((source_path.name, n, msg))

    # 1 写语句只许碰视图表
    for n, line in code_lines(source_path):
        for target in _VIEW_WRITE.findall(line):
            if target != view_table:
                flag(f"写语句的目标是 {target!r}，不是视图表 {view_table!r} —— "
                     "视图是**纯读缓存**（CQRS read model），投影永不回写写模型。"
                     "这条不拦的话，缓存会变成第二份真相，而它看起来还是张缓存表。",
                     n)

    # 2 视图表不许混进 canonical schema
    canonical = _canonical_tables(schema)
    if view_table in canonical:
        flag(f"{view_table!r} 出现在 `scaffold.SCHEMA` 里 —— "
             "存储轴与写权限轴是两条**正交的轴**（工程稿 §七）："
             "canonical 的意思是「删了就没了」，视图的意思是「删了零损失」，"
             "两句话都成立的东西不存在。")

    # 3 列名白名单
    declared = _view_columns(view_schema, view_table)
    if declared is None:
        flag(f"`VIEW_SCHEMA` 里找不到建表语句 {view_table!r} —— "
             "视图表建不出来，而「找不到」和「一列都没有」是两件事。")
    else:
        extra = [c for c in declared if c not in fields]
        missing = [c for c in fields if c not in declared]
        if extra:
            flag(f"视图表多出列 {extra} —— 白名单是 {list(fields)}。"
                 "多一列 `text` / `content` / `body`，就是把底层对象**复制**进来了，"
                 "而副本无法证明自己等于原文（`pointer.py` 那条理由，同一条）。")
        if missing:
            flag(f"视图表少了列 {missing} —— 白名单是 {list(fields)}。"
                 "少一列会让某一类视图行存不下来，而插入时才发现。")

    # 4 快照必须覆盖全部 canonical 表
    if sorted(lower) != canonical:
        flag(f"LOWER_TABLES 是 {sorted(lower)}，而 `scaffold.SCHEMA` 里是 "
             f"{canonical} —— 快照是判据 ②③ 的全部依据，少一张表，"
             "那张表被视图改了也看不出来。")

    # 5 守卫必须在每次重建里跑（ast）
    defs = _ast_defs(source_path)
    for name in ("rebuild", "rebuild_all"):
        node = defs.get(name)
        if node is None:
            flag(f"{name}() 不见了 —— 视图就没法整批重建了。")
            continue
        called = {n.func.id for n in ast.walk(node)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if guard not in called:
            flag(f"{name}() 的函数体里没有调 {guard}() —— "
                 "判据 ②「重建前后 lower 逐字段一致」就只在测试里成立，"
                 "而它本该**每次重建都自证一次**。只写签名不写调用，"
                 "那句话就退化成一条注释。")

    # 6 只读的 scaffold 调用（ast）—— 判据 1 看不见间接写
    for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == "scaffold" and fn.attr not in read_only):
            flag(f"调了 `scaffold.{fn.attr}()` —— 视图层只许调只读的 "
                 f"{list(read_only)}。判据 1 只看得见**直接写 SQL**，"
                 "`scaffold.record_event()` 这类**间接写**它看不见 —— "
                 "而「顺手记一条事件」正是最可能发生的那一种。", node.lineno)

    return hits


# --- B26（贯穿项：停止条件）用的常量 -------------------------------------------

# 停止条件层**允许** import 的模块。**白名单，不是黑名单。**
#
# ⚠️ 特别地，**不许 import `upper`** —— 那是上层。停止条件是运维门槛，
# 一旦它能读上层的信号，工程稿 §八 那条分工（观测点只记 / POLICY 只触发动作）
# 就当场作废。白名单写法的好处：将来新增一个模块，**默认是被挡住的**。
_STOP_ALLOWED_IMPORTS = ("__future__", "policy", "scaffold", "sqlite3", "staging")

# 阈值**只能**经这个函数取。
#
# 直接下标 `policy.POLICY[...]` 就等于把门槛写死在调用点 ——
# 而「可变动」正是这个模块存在的全部理由（工程稿 §〇 Q6）。
_STOP_VALUE_FN = "value"

# `judge()` 的函数体里必须真的调它。只写签名不写调用，
# 「本模块只判不执行」就退化成一句注释。
_STOP_OBSERVE_FN = "observe"


def _stop_imports(path: Path) -> list[str]:
    """本模块 import 了哪些**顶层**模块。`from __future__ import ...` 也算一个。"""
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module.split(".")[0])
    return sorted(set(mods))


def _stop_returned_keys(path: Path, fn_name: str) -> list[str] | None:
    """某个函数里 `return {...}` 那个字典的**键**。找不到就返回 `None`。

    ⚠️ 返回 `None` 而不是空列表 —— 同 `_view_columns()` 那条理由：
    「没找到那个 return」和「return 了一个空字典」是两件事，
    混起来会让检查在函数被改名之后**静默通过**。
    """
    node = _ast_defs(path).get(fn_name)
    if node is None:
        return None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            return [k.value for k in sub.value.keys if isinstance(k, ast.Constant)]
    return None


def check_stop_conditions_cannot_act_on_their_own(
    *, source_path: Path | None = None,
    conditions: tuple | None = None, actions: dict | None = None,
    keys: dict | None = None, observed: tuple | None = None,
    policy_keys=None, count_signals=None, intake=None,
    staged: str | None = None, direct: str | None = None,
    allowed_imports: tuple = _STOP_ALLOWED_IMPORTS,
) -> list[tuple[str, int, str]]:
    """停止条件**只判不执行**（贯穿项 · 工程稿 §5.4 四条件）。

    四条件里两个动作听起来都无害 —— 「暂停导入」「暂停蒸馏」。
    真正的失败方式很安静：**让这个模块自己去做那件事**。
    那一刻它就从一个只读的判定器变成了一条写路径，而它看起来还是个判定器。

    判据：

    1. ★ `STOP_CONDITIONS` / `STOP_ACTIONS` / `STOP_POLICY_KEYS` **三张表的键集相同**
       （双向），且动作名**非空、两两不同**。少一处就是「这个条件触发后没人知道该干什么」；
       两个条件共用一个动作名，事后看不出到底是哪条被触发了。
    2. ★ `STOP_POLICY_KEYS` 里**非 `None`** 的值都真的在 `policy.POLICY` 里。
       写错一个名字，`policy.value()` 会在**运行时**抛 `KeyError` ——
       而那时停止条件已经该报没报了。`None` 只许出现在「无门槛」那条。
    3. ★ import 白名单：只许 `_STOP_ALLOWED_IMPORTS` 里的。**尤其不许 `upper`** ——
       读了上层信号，工程稿 §八 那条分工就作废。
    4. ★ 两个来源口的名字在 `scaffold.INTAKE_CHANNELS` 里。
       写成别的词**不会报错**，只会永远数出 0 —— 而 0 长得像个正常读数。
    5. ★ `OBSERVED_KEYS` 与 `upper.COUNT_SIGNALS` **不相交**。
       观测量混进上层信号白名单 = 让上层读运维门槛（工程稿 §八，撞 B4）。
    6. ★ `observe()` 返回的字典**恰好**是 `OBSERVED_KEYS`。
       常量与实现漂开之后，判据 5 守的是一张过期的名单。
    7. ★ 源码里**一句写语句都没有**（`INSERT INTO` / `UPDATE` / `DELETE FROM`）。
       这是「只判不执行」的静态落点 —— 本模块从头到尾只该有 `SELECT`。
    8. ★ 阈值只经 `policy.value()` 取：代码行里**不许出现 `POLICY[`**。
       直接下标等于把门槛写死在调用点，「可变动」当场失效。
    9. ★ `judge()` 的**函数体里真的调了** `observe()`。
       只写签名不写调用的话，「观测与判定分开」就退化成一句注释
       （手法同 B24 判据 5、B25 判据 5）。

    ⚠️ 静态拦不住什么，说清楚（同 B14 / B18 / B20 / B22 / B23 / B24 / B25 的既有立场）：
    它拦不住「调用方拿到 `action` 之后自己乱做」—— 那是应用层的事。
    这条盯的是**停止条件层自己的形状**：它不许长出执行能力。
    """
    source_path = source_path or (ROOT / "stop.py")
    is_real = source_path.name == "stop.py"
    if is_real and not source_path.is_file():
        return [("stop.py", 1,
                 "停止条件层不在 —— 工程稿 §5.4 的四条件没有落点。")]

    conditions = stop.STOP_CONDITIONS if conditions is None else conditions
    actions = stop.STOP_ACTIONS if actions is None else actions
    keys = stop.STOP_POLICY_KEYS if keys is None else keys
    observed = stop.OBSERVED_KEYS if observed is None else observed
    policy_keys = set(policy.POLICY) if policy_keys is None else set(policy_keys)
    count_signals = (set(upper.COUNT_SIGNALS) if count_signals is None
                     else set(count_signals))
    intake = scaffold.INTAKE_CHANNELS if intake is None else intake
    staged = stop.STAGED_CHANNEL if staged is None else staged
    direct = stop.DIRECT_CHANNEL if direct is None else direct

    hits: list[tuple[str, int, str]] = []

    def flag(msg: str, n: int = 1) -> None:
        hits.append((source_path.name, n, msg))

    # 1 三张表键集相同 + 动作名可用
    want = set(conditions)
    for label, table in (("STOP_ACTIONS", actions), ("STOP_POLICY_KEYS", keys)):
        got = set(table)
        extra, missing = sorted(got - want), sorted(want - got)
        if extra:
            flag(f"{label} 里有 {extra}，它们不在 STOP_CONDITIONS 里 —— "
                 "多出来的那一项永远不会被读到，而它看起来是个正经配置。")
        if missing:
            flag(f"{label} 少了 {missing} —— 那个条件触发之后，"
                 "没人知道该做什么 / 该比哪个门槛。")
    names = [a for a in actions.values() if isinstance(a, str) and a]
    if len(names) != len(actions):
        flag("STOP_ACTIONS 里有空的动作名 —— 触发之后无事可做，"
             "而这个条件仍然会报「触发」。")
    if len(set(names)) != len(names):
        flag(f"STOP_ACTIONS 的动作名有重复：{sorted(names)} —— "
             "两个条件共用一个名字，事后看不出到底是哪条被触发了。")

    # 2 门槛键真的存在（None 只许出现在无门槛那条）
    for condition, key in keys.items():
        if key is None:
            continue
        if key not in policy_keys:
            flag(f"{condition} 读的门槛键 {key!r} 不在 `policy.POLICY` 里 —— "
                 f"现有：{sorted(policy_keys)}。"
                 "写错名字的话，`policy.value()` 会在运行时抛 KeyError，"
                 "而那时这个条件已经该报没报了。")

    # 3 import 白名单
    for mod in _stop_imports(source_path):
        if mod not in allowed_imports:
            flag(f"import 了 {mod!r}，不在白名单 {list(allowed_imports)} 里。"
                 + ("尤其 `upper` 不许 —— 停止条件是运维门槛，"
                    "读了上层信号，工程稿 §八 那条分工就作废。"
                    if mod == "upper" else ""))

    # 4 来源口名字真的在词表里
    for name in (staged, direct):
        if name not in intake:
            flag(f"来源口名字 {name!r} 不在 `scaffold.INTAKE_CHANNELS` "
                 f"{list(intake)} 里 —— 写成别的词**不会报错**，"
                 "只会永远数出 0，而 0 长得像个正常读数。")

    # 5 观测量不许进上层信号白名单
    overlap = sorted(set(observed) & count_signals)
    if overlap:
        flag(f"观测量 {overlap} 出现在 `upper.COUNT_SIGNALS` 里 —— "
             "那是上层的信号白名单，混进去等于让上层读运维门槛"
             "（工程稿 §八「观测点只记不算」，撞 B4）。")

    # 6 observe() 返回的键恰好是 OBSERVED_KEYS
    returned = _stop_returned_keys(source_path, _STOP_OBSERVE_FN)
    if returned is None:
        flag(f"`{_STOP_OBSERVE_FN}()` 里找不到 `return {{...}}` —— "
             "判据 5 守的那张名单就没法跟实现对上了。")
    elif sorted(returned) != sorted(observed):
        flag(f"`{_STOP_OBSERVE_FN}()` 返回 {sorted(returned)}，"
             f"而 OBSERVED_KEYS 是 {sorted(observed)} —— 两处漂开之后，"
             "判据 5 守的是一张过期的名单。")

    # 7 一句写语句都没有
    for n, line in code_lines(source_path):
        for target in _VIEW_WRITE.findall(line):
            flag(f"有写语句，目标是 {target!r} —— 停止条件**只判不执行**，"
                 "四个动作都是应用层的开关。多一条写路径，"
                 "这个模块就从判定器变成了能悄悄改库的东西。", n)

    # 8 阈值只经 policy.value() 取
    for n, line in code_lines(source_path):
        if "POLICY[" in line:
            flag("直接下标取了 `POLICY[...]` —— 门槛要经 "
                 f"`policy.{_STOP_VALUE_FN}()` 取，"
                 "否则「可变动」当场失效（工程稿 §〇 Q6）。", n)

    # 9 judge() 里真的调了 observe()
    judge_fn = _ast_defs(source_path).get("judge")
    if judge_fn is None:
        flag("judge() 不见了 —— 四条件就没有判定入口了。")
    else:
        called = {n.func.id for n in ast.walk(judge_fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if _STOP_OBSERVE_FN not in called:
            flag(f"judge() 的函数体里没有调 {_STOP_OBSERVE_FN}() —— "
                 "「观测与判定分开」就只在文档里成立。")

    return hits


def all_checks():
    """(编号, 说明, 条款, 返回命中的函数) —— **唯一的登记表**。

    存在理由：`main()` 和 `test_checks.py` 都从这里取。
    以前 B6 / B7 / B8 是在 `main()` 里手工追加的，于是
    `test_all_checks_are_non_vacuous` 那条总账**盖不到它们** ——
    也就是说，将来它们变成空转，总账仍然是绿的。
    那正是这个文件要防的病。现在加一条检查只要改这一处。
    """
    for entry in CHECKS:
        code, what, pattern, clause = entry[:4]
        only = entry[4] if len(entry) > 4 else None
        yield code, what, clause, partial(scan, pattern, only)
    yield "B6", "观测点只记不算——不拿它的值与任何数比较", "§C7.2 §T0.3", check_no_threshold
    yield "B7", "第三方依赖为零", "§T4.3 §T5", check_no_new_dependency
    yield "B8", "声明待标注的样本，标注区为空", "§C5.5.1", check_placeholder_not_annotated
    yield ("B9", "并发装置不注入延迟 / 不直接改库 / 不内部协调", "§C11.2",
           check_apparatus_is_not_a_script)
    yield ("B10", "发号器不携带判断（号出不了 scaffold.py）", "§C9 #5 #7",
           check_allocator_stays_an_allocator)
    yield ("B12", "命题类型不许写死 —— 写死就推翻不了（confirm.py 无类型字面量）",
           "§T0.3 §C2.5 缺口②", check_proposition_type_is_not_hardcoded)
    yield ("B13", "标注区有值的样本，必须说清值是谁填的（B8 的另一半）", "§C5.5.1",
           check_annotated_samples_name_their_source)
    yield ("B14", "上层 → 底层不许写成事实（单向性）", "§C7.1 ④",
           check_upper_does_not_write_down)
    yield ("B15", "上层节点不带系统生成的名字（命名归人）", "§C2.0 §C7.1 ③",
           check_upper_nodes_are_not_named_by_machine)
    yield ("B16", "AI 档位必须写明谁主张的（AI 产出不许伪装成人）",
           "§T4 留白 · 2026-09-28", check_ai_sources_require_attribution)
    yield ("B17", "引用不许被当成原始来源（引用 ⊑ 派生，但不是原始来源）",
           "§T4 留白 · 2026-09-28", check_primary_source_is_not_a_quotation)
    yield ("B18", "引用只存定位符，不存正文（副本无法证明自己等于原文）",
           "§T4 留白 · 2026-09-28", check_reference_stores_only_a_locator)
    yield ("B19", "加一个节点类型或关系种类必须同时分档（不许悄悄加）",
           "§T4 留白 · 2026-09-28", check_vocabulary_is_fully_classified)
    yield ("B20", "导入节点不得绕过入层门（staging 只许一个文件写）",
           "§T4 留白 · 2026-09-28", check_imported_never_bypasses_the_gate)
    yield ("B21", "蒸馏关系映射必须显式（没对应的不许偷偷兜住）",
           "§T4 留白 · 2026-09-28", check_distiller_mapping_is_explicit)
    yield ("B22", "promote 条件不许含模型判断（判据只能是结构量的合取）",
           "§C2.5 第 3 档 · 2026-09-28", check_promotion_condition_is_structural)
    yield ("B23", "贡献入口不许要求调用方懂图结构（映射只在一张表里，签名无词表）",
           "§C2.5 · 2026-09-28", check_contribution_entrypoints_are_graph_free)
    yield ("B24", "候选关系不许把自己变成算数的（入口无 state，算数只有 active）",
           "§C2.5 第 3 档 · 2026-09-28", check_candidates_cannot_make_themselves_true)
    yield ("B25", "视图层永不回写写模型（写语句只碰视图表，列名白名单，快照覆盖全表）",
           "§C7.1 ④ · 2026-09-29", check_the_view_layer_cannot_write_back)
    yield ("B26", "停止条件只判不执行（三张表键集相同，一句写语句都没有，阈值只经 policy.value 取）",
           "§5.4 停止条件 · 2026-09-29", check_stop_conditions_cannot_act_on_their_own)


def main() -> int:
    failed, codes = [], []
    for code, what, clause, run in all_checks():
        codes.append(code)
        hits = run()
        print(f"[{code}] {what:<34} {clause:<22} "
              f"{'过' if not hits else f'命中 {len(hits)}'}")
        for path, line, text in hits:
            print(f"       {path}:{line}  {text}")
        if hits:
            failed.append(code)

    print()
    if failed:
        print(f"否证检查未通过：{', '.join(failed)} —— 《选型声明》不成立，停下提问。")
        return 1
    # 以前这里印的是 `{codes[0]}–{codes[-1]}`。加了 B11 之后那句话就**错了** ——
    # B11 不在末尾，却会被那句话盖进「B1–B10」这个区间里。
    # 一句话说得比它知道的多，就是本仓库一直在防的那个病。改成照实报集合。
    print(f"否证检查全部通过：共 {len(codes)} 条，{', '.join(sorted(codes))} 无命中。")
    return 0


if __name__ == "__main__":
    force_utf8()
    raise SystemExit(main())
