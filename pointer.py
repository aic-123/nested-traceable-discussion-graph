"""引用定位符 —— **只存位置，不存正文**（`§T4` 留白项 · 2026-09-28）。

本模块回答一个问题：**「这条命题指的是外面哪个东西的哪一段？」**

答案固定是两个东西：

    (uri, selector)
     ↑       ↑
     │       └ 段内定位：W3C Web Annotation 的 selector
     └ 对象位置：绝对 URI

--- 为什么必须是这个形状，不能是「复制一段正文」 --------------------------------

设计稿的阶段 2 出口判据里有一条：**不复制被引用对象的正文**。
理由不是省空间，是**可追溯性**：

    复制正文   → 仓库里有一份「看起来是原文」的副本，它**无法证明**自己等于原文。
                 原文改了、被撤了、被换成另一个版本，副本不会跟着变，
                 而读者看不出来。副本越长，越像原文，越危险。
    存定位符   → 仓库里**没有**任何可以被误当成原文的东西。
                 要正文就去 URI 取 —— 取到什么，由那边说了算。

所以这条约束的可执行形式是**字段白名单**，不是长度限制：

    pointer 的字段       ⊆ {uri, selector}
    selector 的字段      ⊆ 该 selector 类型规定的字段

白名单之外一个字段都放不进去 —— 于是「顺手把正文也存一份」这件事
**没有地方可以发生**。⚠️ 判据刻意不做「引文长度」这一类限制：
那是阈值，会滑进 B3 要防的那一族（「过了这条线就算数」）。
字段白名单是**结构判据**，没有线可调。

--- selector 照抄 W3C Web Annotation，不自造 ------------------------------------

`§T4` 留白项的规矩是「自选 + 声明 + 可回退」，但**自选不是随便选** ——
能抄标准就抄标准。W3C Web Annotation Data Model 给文本段两种 selector：

    TextQuoteSelector      exact + prefix + suffix   —— 按内容定位
    TextPositionSelector   start + end               —— 按偏移定位

两种都取：**它们各自会失效的场景不同**。

| | 定位方式 | 失效场景 |
|---|---|---|
| `TextQuoteSelector` | 引文（可带前后文） | 原文改了字 |
| `TextPositionSelector` | 字符偏移 | 前面插了一段，偏移全移 |

⚠️ 关于 `exact` —— 它是 selector 的一部分，**不是**「复制正文」。
它是**定位所需的最小引文**：没有它，同一个偏移在另一个版本上指不到同一句。
两者的界在**字段身份**上，不在字数上：`exact` 属于 selector 的规定字段，
而「另存一份正文」会表现为一个**白名单之外的字段** —— 那个会被拦。

--- 与 `scaffold` 的关系 --------------------------------------------------------

本模块**不 import 任何本地模块**（连 `scaffold` 都不），所以它可以被
`scaffold._append_revision()` 直接调用做写入时守卫，不产生循环依赖。

写入时守卫在 `_append_revision()` 里 —— 那是 `add_artifact` 与 `revise`
**共用的唯一一道**内容序列化入口。放在那里，就没有第二条路能存进一个坏定位符。
"""

from __future__ import annotations

import re

# content 里放定位符的键名。**唯一**的一个。
POINTER_FIELD = "pointer"

# 绝对 URI 的判据：必须有 scheme。
#
# 为什么要求 scheme（而不是「非空字符串就行」）：相对路径与裸文本
# 都无法从本仓库之外被重新定位 —— 而「能被重新定位」是这个字段的**全部用途**。
# 挡掉裸字符串，等于挡掉「看起来填了、其实指不到任何地方」。
_ABSOLUTE_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

# 本系统认的两种 selector。**封闭集合** —— 不认第四种，也不认自定义类型。
SELECTOR_KINDS = (
    "TextQuoteSelector",
    "TextPositionSelector",
)

# 每种 selector 的 (必填字段, 选填字段)。
#
# ⚠️ 这张表就是「不存正文」的全部实现：`verify()` 拿它做**双向**比对 ——
# 必填的缺一个不行，白名单外多一个也不行。多出来的那一个，正是「顺手存了正文」。
SELECTOR_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "TextQuoteSelector":    (("exact",), ("prefix", "suffix")),
    "TextPositionSelector": (("start", "end"), ()),
}


class PointerError(Exception):
    """定位符形状不对。消息面向调用方，说清违反了什么。"""


def make_selector(*, kind: str, **fields):
    """建一个 selector 并当场验。`kind` 必填 —— 不许靠字段猜类型。"""
    selector = {"type": kind}
    selector.update(fields)
    verify_selector(selector)
    return selector


def make_pointer(*, uri: str, selector: dict) -> dict:
    """建一个定位符并当场验。返回**新字典**，不改入参。"""
    p = {"uri": uri, "selector": selector}
    verify(p)
    return p


def verify(pointer) -> None:
    """定位符形状不对就抛 `PointerError`。

    四条判据，逐条对应一种「看起来填了、其实指不到」：

    1. 是个字典，且字段**只有** `uri` / `selector` —— 多一个就是「顺手存了别的东西」。
    2. `uri` 非空、有 scheme —— 见 `_ABSOLUTE_URI`。
    3. `selector` 是字典，`type` 在封闭集合里。
    4. selector 的字段**恰好**是它那一档规定的 —— 缺必填 / 多出白名单，都拦。
    """
    if not isinstance(pointer, dict):
        raise PointerError(f"{POINTER_FIELD} 必须是字典，收到 {type(pointer).__name__}")

    extra = set(pointer) - {"uri", "selector"}
    if extra:
        raise PointerError(
            f"{POINTER_FIELD} 只许有 uri / selector 两个字段，多出了 {sorted(extra)}。"
            "多出来的那个通常就是被引用对象的正文 —— "
            "本仓库只存位置，不存正文（要正文去 uri 取）。"
        )
    if "uri" not in pointer:
        raise PointerError(f"{POINTER_FIELD} 缺 uri")
    if "selector" not in pointer:
        raise PointerError(f"{POINTER_FIELD} 缺 selector")

    uri = pointer["uri"]
    if not isinstance(uri, str) or not _ABSOLUTE_URI.match(uri):
        raise PointerError(
            f"uri 必须是绝对 URI（要带 scheme），收到 {uri!r}。"
            "相对路径与裸文本都指不到外面那个东西 —— 这个字段的全部用途就是能指到。"
        )
    verify_selector(pointer["selector"])


def verify_selector(selector) -> None:
    """selector 单验 —— 给只想换 selector、不换 uri 的调用方用。"""
    if not isinstance(selector, dict):
        raise PointerError(f"selector 必须是字典，收到 {type(selector).__name__}")

    kind = selector.get("type")
    if kind not in SELECTOR_FIELDS:
        raise PointerError(
            f"未知 selector 类型 {kind!r} —— 本系统只认 {SELECTOR_KINDS}"
            "（照抄 W3C Web Annotation Data Model，不自造）。"
        )

    required, optional = SELECTOR_FIELDS[kind]
    allowed = {"type", *required, *optional}

    missing = [f for f in required if f not in selector]
    if missing:
        raise PointerError(f"{kind} 缺必填字段 {missing}")
    extra = set(selector) - allowed
    if extra:
        raise PointerError(
            f"{kind} 不许有字段 {sorted(extra)} —— 它的字段是 {sorted(allowed)}。"
            "多出来的字段没有地方可放：本仓库只存定位所需的那点信息。"
        )

    for f in required:
        v = selector[f]
        if kind == "TextPositionSelector":
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise PointerError(f"{kind}.{f} 必须是非负整数，收到 {v!r}")
        elif not isinstance(v, str) or not v.strip():
            raise PointerError(f"{kind}.{f} 必须是非空字符串，收到 {v!r}")

    if kind == "TextPositionSelector" and selector["start"] > selector["end"]:
        raise PointerError(
            f"TextPositionSelector 的 start({selector['start']}) "
            f"大于 end({selector['end']}) —— 这不是一段。"
        )


def attach(content: dict, *, uri: str, selector: dict) -> dict:
    """把定位符挂进 content，返回**新字典**。

    不改入参 —— 调用方手里的 dict 归调用方，这一条与 `revise()` 不改旧版本同源。
    """
    p = make_pointer(uri=uri, selector=selector)
    out = dict(content)
    out[POINTER_FIELD] = p
    return out


def read(content: dict) -> dict | None:
    """取出定位符；没有就返回 `None`。

    ⚠️ 「没有」与「有但坏」是两件事：没有 = 这条命题不指向外部对象；
    坏 = 写坏了，而它**进不了库**（`_append_revision` 会拦）。所以这里
    见到坏的就照抛，不返回 `None` —— 那会让坏数据长得像「没有」。
    """
    p = (content or {}).get(POINTER_FIELD)
    if p is None:
        return None
    verify(p)
    return p


def target_of(content: dict) -> tuple[str, str] | None:
    """`(uri, selector 类型的短名)` —— 给「这两条指的是不是同一个东西」用。

    ⚠️ 刻意**不返回 selector 全文**：比较两个 selector 是否等价需要
    「同一版本的偏移」这种前提，而那是本层判断不了的事。
    所以这里只到「同一个 uri、同一种定位方式」这一档，不再往下猜。
    """
    p = read(content)
    if p is None:
        return None
    return p["uri"], p["selector"]["type"]
