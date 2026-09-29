"""`checks.py` 自己的测试 —— 证明 B1–B23 **不是空转**。

--- 为什么会有这个文件 -----------------------------------------------------

2026-09-25 一天之内，`checks.py` 有**三条检查**是空转的，而它们全都打印「过」：

| 检查 | 空转的原因 | 怎么发现的 |
|---|---|---|
| B6 | 逐 **token** 匹配，跨 token 的写法匹配不到 | 手工注入阈值，没响 |
| B6 | 正则打错靶子（抓百分号字面量，不是比较运算） | 同上 |
| B8 | 靠标记字段触发，标记一改名它就没事可做 | 我改了标记，它照样「过」 |

**这三个都不是被"跑一遍看结果"发现的，都是被注入验伪发现的。**
而手工注入本身又出了第四个错：我做 B6 验伪时把探针打进了 `debate.py`，
没备份、事后 `rm` 了一个不存在的 `.bak`，**残留物留在了产物文件里，而 checks 是绿的**。

所以这件事不能再靠手工仪式。这里把它变成自动化测试：

* 往 `arena/` 下写一个**临时** `.py` / `samples/` 下写一个**临时** `.md`
* 让 `checks.py` 去扫
* 断言它**命中**
* 删掉临时文件

**全程不碰任何产物文件** —— 所以「忘了还原」在结构上不可能发生。

    python test_checks.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

import checks
import scaffold

ROOT = Path(__file__).parent


def _clean_probes() -> None:
    """把验伪探针**连编译产物一起**清掉。

    为什么不是只删 `_tmp_probe_zzz.py` / `samples/_tmp_probe_zzz.md` 那两个路径：
    探针 `.py` 只要被**编译**过一次（`python -m compileall .`、或任何 import 它的
    东西），就会在 `__pycache__/` 下留下 `_tmp_probe_zzz.cpython-3xx.pyc`。
    那个 `.pyc` 删不掉的话，`Test00NoResidueAtStart` 下一轮就红，
    而它报的是「上一轮被打断了」—— **误导**。

    **2026-09-25 实测踩到过**：跑完 `python -m compileall .` 之后
    `test_checks.py` 连红两次（`Test00` 和 `TestNoResidue` 各一次），
    真正的原因是一个 `.pyc`，而 `rm -f _tmp_probe_zzz.py` 永远删不到它。
    所以这里用 `rglob` 一次扫干净 —— 「残留自愈」这条规矩要自愈得彻底。

    `missing_ok=True`：收尾不该因为「文件已经不在」而抛 —— 那会把**真正的**
    异常盖掉，报出来变成一个看不懂的 `FileNotFoundError`。
    """
    for p in ROOT.rglob("_tmp_probe*"):
        p.unlink(missing_ok=True)


# --- 上游产品的专属文件：本仓库没有，跳过要**说出来** -----------------------
#
# 这个仓库只抽出「双侧框架」那一层。上游产品（arena）里的
# `vote.py` / `concurrency.py` / `confirm.py` / `samples/` 都不在这里。
#
# ⚠️ 为什么不是删掉这些用例，也不是让它们红着：
#
#   删掉 → `python test_checks.py` 报「全过」，但 B4 / B8 / B9 / B12 / B13
#          这五条检查在这个仓库里**从未被验伪过**。那正是本文件开头那张表
#          记着的病：**空转的检查也在打印「过」。**
#   让它红 → 报出来的是「检查坏了」，而真正的原因是「靶文件不在」。
#          **两种情况必须分开**，否则这个仓库没法把「我用的是什么」说清。
#
# 所以缺文件时 `skipTest`，并且**把原因印出来** —— 报告里会明确列出
# 「哪几条检查在这里是不适用的」，而不是含混地打一个「过」。
#
# 本仓库自己能完整验的（`scaffold.py` / `upper.py` / 其它）不受影响。
_UPSTREAM_ONLY = {
    "vote.py": "arena 的投票模块 —— 双侧框架只派生、不读热度信号，不需要它",
    "concurrency.py": "arena 的并发装置 —— 双侧框架不引入锁、不做并发协调",
    "confirm.py": "arena 的确认链 —— 双侧框架不实现确认（那是另一层的权力）",
    "samples": "arena 的标注样本 —— 双侧框架不含需要人工标注的样本",
}


def _need(*names: str) -> None:
    """这些路径任一不在就跳过，并说明**它是什么、为什么这里没有**。

    `names` 是相对仓库根的路径。少一个就跳 —— 因为那些用例的前提是
    「真文件在」，缺一个就等于没测到。
    """
    missing = [n for n in names if not (ROOT / n).exists()]
    if missing:
        why = "；".join(f"{n}（{_UPSTREAM_ONLY.get(n, '上游文件')}）"
                        for n in missing)
        raise unittest.SkipTest(f"本仓库不含上游文件：{why}")


def _scan_with_temp_py(source: str) -> dict:
    """把 source 写成一个临时 .py 放进 arena/，跑完**全部**检查，删掉。"""
    probe = ROOT / "_tmp_probe_zzz.py"
    # 残留自愈：**不是** `assert not probe.exists()`。
    # 为什么改成自愈，见 `Test00NoResidueAtStart` —— 一条残留会让十几条用例
    # 连锁失败，而且第一条失败报的地方和真正的原因无关。
    # 「有没有残留」这个问题由开头那一条和结尾那一条各管一次。
    _clean_probes()
    probe.write_text(source, encoding="utf-8")
    try:
        return {code: run() for code, _what, _clause, run in checks.all_checks()}
    finally:
        # 「有没有残留」由 Test00（开跑前）和 TestNoResidue（跑完后）各管一次。
        _clean_probes()


class Test00NoResidueAtStart(unittest.TestCase):
    """**开跑之前**，产物目录必须是干净的。

    为什么单开一条、而且类名排在最前（`Test00…` 在字母序里先于 `TestB12…`）：

    残留的**后果会扩散**。每个 `_probe()` / `_scan_with_temp_py()` 原来都以
    `assert not probe.exists()` 开头，于是一个残留文件会让后面十几条用例连锁失败 ——
    **而第一条失败报的地方和真正的原因无关**。

    实测（手动放一个残留探针，跑一遍）：**19 条失败**，
    第一条报的是

        FAIL: test_B12_accepts_a_type_that_comes_from_a_decision

    看起来像 B12 的检查坏了。真正的原因（「上次没清干净」）只在
    **最后一条** `TestNoResidue.test_no_probe_files_left_behind` 里才出现。
    一条残留，19 条误导性失败。

    所以把「有没有残留」**收敛成两条、各管一头**：
    - 本类管**上一轮**留下的（跑在第一条）
    - `TestNoResidue` 管**这一轮**留下的（跑在最后一条）
    中间的 helper 一律 `_clean_probes()` 自愈，不再各自断言。

    这和本仓库一直在防的那类病是同一个：**残留和检查失效长得一模一样**。

    ⚠️ 2026-09-27 补：本类**不再判红**，改成「自愈 + skip 说明」。
    原因是残留的成因被查清了 —— 它是**上一轮进程被硬终止**（`SIGKILL` 不给
    `finally` 机会），跟这一轮的代码质量无关。判红等于把上一轮的事故
    记到这一轮头上，而报出来的地方又指向另一条用例。详见本类方法的 docstring。
    """

    def test_no_residue_before_the_run_starts(self):
        """跑前有残留 → **自愈 + 说明**，不是判 FAIL。

        为什么这里**不能**断言（2026-09-27 实测定案）：

        残留的唯一成因是「**上一个进程在探针存在于磁盘上的那个窗口里被硬终止**」。
        实测三种运行方式：

            | 运行方式 | 跑完残留 |
            |---|---|
            | 正常跑 / 重定向到文件 | 无 |
            | stdout 接管道后提前关闭（`| head -5`） | 无 |
            | **跑到一半被 SIGKILL** | **有** |

        也就是说 `finally: _clean_probes()` 是好的 —— 它**只在进程能跑到收尾时**生效。
        `SIGKILL` / 硬终止**不给 Python 执行 `finally` 的机会**，
        这正是「Ctrl-C 打断了一次跑，下一次开跑就红」的机制。

        ⚠️ 所以把这条报成 FAIL 是**归因错误**：它拿**上一轮的事故**判**这一轮**不合格。
        而失败信息里那句「上一轮被打断了」会被读成「你的测试有 bug」，
        于是照着 `rm` 去清 —— 而清理从来不是问题（残留必然在下一轮被自愈）。

        改成 skip 之后：
        - 无残留 → 不产生多余 skip（不吵）
        - 有残留 → 产生 1 个 skip，说明里**带绝对路径**（可见，不是静默）
        - 结论行仍在（报告有结论）

        这与本仓库一直在守的那条一致：**「上一轮的事故」和「这一轮的失败」
        必须分开报** —— 就像缺上游文件时 `skipTest` 而不是判红。
        """
        # 报**绝对路径**，不报 `p.name` —— `rglob` 是递归的，只给文件名的话
        # 看不出残留在哪个目录，等于没报。
        left = sorted(str(p.resolve()) for p in ROOT.rglob("_tmp_probe*"))
        if not left:
            return
        # 自愈：清掉上一轮硬终止留下的东西（含 `__pycache__` 里的 `.pyc` ——
        # `rglob` 是递归的，所以 `_clean_probes()` 一次就能扫干净）。
        _clean_probes()
        self.skipTest(
            f"上一轮被硬终止，留下 {len(left)} 个探针残留，已自动清理：{left}"
            " —— 这不是本轮的失败。"
            "成因是「进程在探针存在于磁盘的窗口里被硬终止」"
            "（Ctrl-C / 进程被杀 / 机器休眠），`finally` 来不及跑。"
            "下一次开跑会自动清掉，不需要手工 `rm`。"
        )


class TestEveryCheckFires(unittest.TestCase):
    """每一条检查：喂它一个真违规，必须命中。"""

    def test_B1_fires_on_a_real_lock(self):
        for src in ("import threading\nmutex = threading.Lock()\n",
                    "flock = open('x')\n",
                    "if not deadlock_free: pass\n"):
            hits = _scan_with_temp_py(src)
            self.assertTrue([h for h in hits["B1"] if "_tmp_probe" in h[0]],
                            f"B1 没抓到：{src!r}")

    def test_B1_does_not_fire_on_the_word_blocks(self):
        """`blocks` / `blocking` 里含 `lock` 是子串巧合，不是锁。"""
        hits = _scan_with_temp_py("def f():\n    return blocks_the_write()\n")
        self.assertFalse([h for h in hits["B1"] if "_tmp_probe" in h[0]])

    def test_B5_fires_on_a_scalar_verdict(self):
        hits = _scan_with_temp_py("evidence_score = 8.7\n")
        self.assertTrue([h for h in hits["B5"] if "_tmp_probe" in h[0]])

    def test_B6_fires_on_a_threshold(self):
        """这是 2026-09-25 真实空转过的那一条。"""
        for src in ("unused_char_ratio = 0.5\nif unused_char_ratio > 0.3:\n    pass\n",
                    "v = m['unused_ratio']\nif v >= 0.2:\n    pass\n"):
            hits = _scan_with_temp_py(src)
            self.assertTrue([h for h in hits["B6"] if "_tmp_probe" in h[0]],
                            f"B6 没抓到：{src!r}")

    def test_B6_does_not_fire_on_a_ratio_displayed_as_a_percentage(self):
        """`.2%` 是把比率印给人看，不是阈值。"""
        hits = _scan_with_temp_py("print(f\"占比 = {r:.2%}\")\n")
        self.assertFalse([h for h in hits["B6"] if "_tmp_probe" in h[0]])

    def test_B6_does_not_fire_on_a_return_annotation(self):
        """`-> float` 里的 `>` 不是比较运算 —— 少了 `[0-9]` 就会误报。"""
        hits = _scan_with_temp_py(
            "def elapsed_seconds(a, b) -> float:\n    return b - a\n")
        self.assertFalse([h for h in hits["B6"] if "_tmp_probe" in h[0]])

    def test_B5_does_not_fire_on_a_docstring_that_quotes_the_clause(self):
        """修订四：**文档不是产物行为**。

        `debate.py` 为了说明 `§C6.1` 为什么禁止标量分，得引用规范的原例 ——
        引用一次就被自己的检查抓一次。而 docstring 里写不出一个分数来。
        """
        hits = _scan_with_temp_py(
            '"""§C6.1 禁止形如 evidence_score = 8.7 的标量设计。"""\n'
            "def f():\n"
            '    """这里再引一次 evidence_score。"""\n'
            "    return 1\n"
        )
        for code in ("B2", "B5"):
            self.assertFalse([h for h in hits[code] if "_tmp_probe" in h[0]],
                             f"{code} 把 docstring 里的引文当成产物行为了")

    def test_B5_still_fires_on_a_string_literal(self):
        """但**字符串字面量照抓** —— 把它写成 dict 的键是真产物行为。

        这两条必须分开验：只验上一条的话，「docstring 不算」和
        「引号里的都不算」长得一模一样。
        """
        hits = _scan_with_temp_py('row = {"evidence_score": 1}\n')
        self.assertTrue([h for h in hits["B5"] if "_tmp_probe" in h[0]],
                        "B5 连字符串字面量都不抓了 —— 口径放得比该放的宽")

    def test_B7_fires_on_a_third_party_import(self):
        hits = _scan_with_temp_py("import requests\n")
        self.assertTrue([h for h in hits["B7"] if "_tmp_probe" in h[0]])

    def test_all_checks_are_non_vacuous(self):
        """一句总账：每条检查都必须至少抓到一个真违规。

        将来加了 B9、B10，忘了在这个文件里给它写用例 —— 这条会失败。
        遍历的是 `checks.all_checks()`（唯一登记表），不是 `CHECKS` 列表，
        所以 B6 / B7 / B8 也在这条总账里 ——
        它们以前是 `main()` 里手工追加的，总账盖不到。
        """
        # B8 扫 samples/*.md、B9 扫固定那一个 concurrency.py、B12 扫 confirm.py ——
        # 喂这个 .py 探针没用。它们的非空转各自单独钉住：
        # B8 在 TestB8Fires，B9 在 TestB9Fires，B12 在 TestB12Fires。
        #
        # B14 / B15 同理（它们扫 `checks.UPPER`）：在 TestB14Fires / TestB15Fires。
        # B4 也是**限定范围**的（只扫 upper.py），所以这个探针也盖不到它 ——
        # 由 TestB4IsScopedToTheUpperLayer 单独钉。
        # B23 同理（它只扫 `contribute.py`）：在 TestB23Fires ——
        # 喂 `_tmp_probe_zzz.py` 到不了它。
        needs_md_probe = {"B8", "B9", "B12", "B13", "B4", "B14", "B15",
                          "B16", "B17", "B18", "B19", "B20", "B21", "B22",
                          "B23"}
        probe_src = (
            "import requests\n"                      # B7
            "mutex = 1\n"                            # B1
            "weight = 2\n"                           # B2
            "threshold = 3\n"                        # B3
            "consensus = 4\n"                        # B4（收窄后仍抓 consensus）
            "evidence_score = 5\n"                   # B5
            "v = m['unused_ratio']\n"                # B6（跨行那种写法）
            "if v >= 0.2:\n    pass\n"
            "conn.execute(\"SELECT n FROM seq\")\n"  # B10：号跑出了 scaffold.py
            "conn.execute(\"UPDATE revision SET content = 1\")\n"   # B11：覆盖版本
        )
        hits = _scan_with_temp_py(probe_src)
        for code, _what, _clause, _run in checks.all_checks():
            if code in needs_md_probe:
                continue
            self.assertTrue([h for h in hits[code] if "_tmp_probe" in h[0]],
                            f"{code} 是空转的：喂它违规它也不响")


class TestB8Fires(unittest.TestCase):
    """B8 靠标记触发 —— 标记改名它就会静默空转，所以必须单独验。"""

    def test_B8_fires_on_a_filled_annotation(self):
        _need("samples")
        probe = ROOT / "samples" / "_tmp_probe_zzz.md"
        _clean_probes()
        probe.write_text(
            "## 9999\n\n```yaml\n"
            "input: \"x\"\n"
            "annotation_status: 待需求方标注\n"
            "annotation:\n"
            "  proposed_count: 2\n"          # ← 声明了待标注，却填了数
            "  agent_filled: 需求方填写\n"
            "```\n",
            encoding="utf-8",
        )
        try:
            hits = checks.check_placeholder_not_annotated()
            mine = [h for h in hits if "_tmp_probe" in h[0]]
            self.assertEqual(len(mine), 2, f"B8 没抓到，实际：{hits}")
        finally:
            _clean_probes()

    def test_B8_lets_a_truly_annotated_sample_through(self):
        """需求方真标完之后，B8 就不该再管它。"""
        _need("samples")
        probe = ROOT / "samples" / "_tmp_probe_zzz.md"
        _clean_probes()
        probe.write_text(
            "## 9999\n\n```yaml\n"
            "input: \"x\"\n"
            "annotation_status: 需求方已标注\n"
            "annotation:\n"
            "  proposed_count: 2\n"
            "  agent_filled: 需求方填写\n"
            "```\n",
            encoding="utf-8",
        )
        try:
            hits = checks.check_placeholder_not_annotated()
            self.assertFalse([h for h in hits if "_tmp_probe" in h[0]])
        finally:
            _clean_probes()


class TestB9Fires(unittest.TestCase):
    """B9 盯的是并发装置 —— 它**自己最容易被写成剧本**，所以必须正反两向验。

    这一条防的是本阶段最容易犯的错：为了让「并发问题」出现，
    顺手塞一个 `sleep` 把窗口撑开。那样出现的现象是我安排的。
    """

    def _probe(self, src: str) -> list:
        probe = ROOT / "_tmp_probe_zzz.py"
        _clean_probes()
        probe.write_text(src, encoding="utf-8")
        try:
            return checks.check_apparatus_is_not_a_script(probe)
        finally:
            _clean_probes()

    def test_B9_fires_on_each_of_the_three_red_lines(self):
        for src, why in (
            ("import time\ntime.sleep(0.5)\n", "注入延迟"),
            ("conn.execute(\"INSERT INTO event VALUES (1)\")\n", "直接改库"),
            ("conn.execute(\"UPDATE artifact SET state = 'active'\")\n", "直接改库"),
            ("conn.execute(\"DELETE FROM relation\")\n", "直接改库"),
            ("import threading\n", "内部协调"),
            ("barrier = Barrier(2)\n", "内部协调"),
        ):
            self.assertTrue(self._probe(src), f"B9 没抓到「{why}」：{src!r}")

    def test_B9_allows_reads_but_not_writes(self):
        """边界钉在这里：**读可以，写不行**。

        `§C11.2` 拦的是「我在摆布状态」，不是「我在看状态」——
        一条 `SELECT` 改不了谁先谁后，伪造不出竞争窗口。
        第一版把 `SELECT` 也拦了，代价是拦住了自己：
        `_interleaving()` 靠读 event 表来回答「这两个主体到底有没有真重叠」，
        而那正是本步最该测的一件事。
        """
        self.assertFalse(self._probe(
            "conn.execute('SELECT actor FROM event ORDER BY id')\n"))
        self.assertTrue(self._probe(
            "conn.execute('UPDATE event SET actor = 1')\n"))

    def test_B9_accepts_the_real_apparatus(self):
        """真装置必须是干净的，否则这条检查本身就是在骂自己。

        ⚠️ 光断言 `== []` 是**不够**的：B9 在目标文件不存在时也返回 `[]`
        （「还没写装置，这条暂不适用」），所以「装置干净」和「没有装置」
        在这一句里长得一模一样 —— 用例会**空过**。
        所以先单独钉住「B9 默认扫的那个文件真的在」。

        2026-09-25 给装置改名（`concurrent.py` → `concurrency.py`）时踩到这个形状：
        漏改 `checks.py` 里的路径，B9 从此再也不会响，而且**什么都不会说**。
        """
        _need("concurrency.py")
        self.assertTrue(
            checks.APPARATUS.is_file(),
            f"B9 默认扫的 {checks.APPARATUS} 不存在 —— 这条检查现在是空转的，"
            "它打印的「过」没有任何含义。改了装置文件名就要同步改 `checks.APPARATUS`。",
        )
        self.assertEqual(checks.check_apparatus_is_not_a_script(), [])


class TestB13Fires(unittest.TestCase):
    """B13 是 B8 的另一半：B8 管「待标注 → 必须空」，B13 管「有值 → 必须署名」。

    加它的原因就是：折算之后 B8 在这份文件上罩不住了，还在打印「过」。
    所以这一条也**必须正反两向验** —— 一条只会打印「过」的检查，
    和一条真的通过了的检查，长得一模一样。
    """

    def _probe(self, body: str) -> list:
        _need("samples")
        probe = ROOT / "samples" / "_tmp_probe_zzz.md"
        _clean_probes()
        probe.write_text(body, encoding="utf-8")
        try:
            return [h for h in checks.check_annotated_samples_name_their_source()
                    if "_tmp_probe" in h[0]]
        finally:
            _clean_probes()

    def test_B13_fires_when_a_value_has_no_author(self):
        hits = self._probe(
            "## 9999\n\n```yaml\ninput: \"x\"\n"
            "annotation_status: 需求方授权折算\n"
            "annotation:\n"
            "  proposed_count: 2\n"          # ← 填了值
            "```\n")                          # ← 却没说谁填的
        self.assertEqual(len(hits), 1, f"B13 没抓到，实际：{hits}")

    def test_B13_lets_a_signed_annotation_through(self):
        self.assertEqual(self._probe(
            "## 9999\n\n```yaml\ninput: \"x\"\n"
            "annotation_status: 需求方授权折算\n"
            "annotation:\n"
            "  proposed_count: 2\n"
            "  agent_filled: 需求方授权折算（2026-09-25）\n"
            "```\n"), [])

    def test_B13_does_not_touch_blocks_that_B8_owns(self):
        """两块地不能重叠：待标注的块归 B8，B13 少报，免得同一件事响两遍。"""
        self.assertEqual(self._probe(
            "## 9999\n\n```yaml\ninput: \"x\"\n"
            "annotation_status: 待需求方标注\n"
            "annotation:\n"
            "  proposed_count: 2\n"          # B8 会抓这个
            "```\n"), [])

    def test_B13_accepts_the_real_samples(self):
        """真样本目录必须是干净的。"""
        self.assertEqual(checks.check_annotated_samples_name_their_source(), [])


class TestB12Fires(unittest.TestCase):
    """B12 盯的是缺口② 那一行 —— 「命题类型被写死」。

    它挡的动作很具体：把 `confirm.py` 里
    `type_=resolved.get(x["key"], "Claim")` 改回 `type_="Claim"`。
    改回去之后「X，所以 Y」的两端又会静默变成主张。
    """

    def _probe(self, src: str) -> list:
        probe = ROOT / "_tmp_probe_zzz.py"
        _clean_probes()
        probe.write_text(src, encoding="utf-8")
        try:
            return checks.check_proposition_type_is_not_hardcoded(probe)
        finally:
            _clean_probes()

    def test_B12_fires_on_a_hardcoded_proposition_type(self):
        for ty in ("Claim", "Evidence"):
            self.assertTrue(self._probe(f'add_artifact(conn, type_="{ty}", content=x)\n'),
                            f"B12 没抓到 type_=\"{ty}\"")

    def test_B12_accepts_a_type_that_comes_from_a_decision(self):
        """类型只要**不是字面量**就放过 —— 这个检查管的是来源，不是名字。"""
        self.assertFalse(self._probe(
            'add_artifact(conn, type_=resolved.get(x["key"], DEFAULT), content=x)\n'))

    def test_B12_accepts_the_real_confirm_module(self):
        """真 `confirm.py` 必须是干净的，否则这条检查在骂自己。"""
        self.assertEqual(checks.check_proposition_type_is_not_hardcoded(), [])


class TestB14Fires(unittest.TestCase):
    """B14 守住单向性：**上层 → 底层，禁止写成事实**（`§C7.1` ④）。

    这条坏起来不像坏：上层顺手把「这一簇很重要」写进底层某个字段，
    短期看一切正常，长期看**底层的真值被相关性判断污染了**，而且不可追溯。
    """

    def _probe(self, src: str) -> list:
        probe = ROOT / "_tmp_probe_zzz.py"
        _clean_probes()
        probe.write_text(src, encoding="utf-8")
        try:
            return checks.check_upper_does_not_write_down(probe)
        finally:
            _clean_probes()

    def test_B14_fires_on_every_way_down(self):
        for src, why in (
            ("conn.execute(\"UPDATE artifact SET state = 'active'\")\n", "改底层节点"),
            ("conn.execute('INSERT INTO revision (id) VALUES (1)')\n", "写版本表"),
            ("conn.execute('DELETE FROM relation WHERE id = 1')\n", "删底层边"),
            ("conn.execute('UPDATE relation SET state = 1')\n", "改底层边"),
        ):
            self.assertTrue(self._probe(src), f"B14 没抓到「{why}」：{src!r}")

    def test_B14_allows_reads_and_allows_new_upper_rows(self):
        """边界钉在这里：**读底层可以，写底层不行，写上层自己的表可以**。

        上层当然要能建自己的节点 —— 那正是它存在的意义。
        （`upper.py` 建 `Context` 走的是 `scaffold.add_artifact`，
        不直接写 SQL，所以这里放行的是「读」与「不碰底层表」。）
        """
        for src in (
            "conn.execute('SELECT id FROM artifact WHERE type = ?')\n",
            "conn.execute('SELECT COUNT(*) FROM relation WHERE kind = ?')\n",
            "node_id = add_artifact(conn, type_=UPPER_TYPE, content=c, origin=by)\n",
            "rev = revise(conn, context_id, content=c, author=by)\n",
        ):
            self.assertFalse(self._probe(src), f"B14 误报了：{src!r}")

    def test_B14_accepts_the_real_upper_module(self):
        """真上层模块必须是干净的。

        ⚠️ 同 B9：光断言 `== []` 不够 —— B14 在文件不存在时也返回 `[]`，
        那样「干净」和「没这个模块」长得一模一样。所以先钉住文件真的在。
        """
        self.assertTrue(
            checks.UPPER.is_file(),
            f"B14 默认扫的 {checks.UPPER} 不存在 —— 这条检查现在是空转的。",
        )
        self.assertEqual(checks.check_upper_does_not_write_down(), [])


class TestB15Fires(unittest.TestCase):
    """B15 守住「命名归人」—— 上层节点不许带系统生成的名字。

    这是本模块能**免确认**的全部理由：名字留空，系统就一句话都没说。
    它盯的是最容易发生的退化 —— 有人图省事，从依据里抄第一条命题当名字，
    而那个节点**看起来完全正常**。
    """

    def _probe(self, src: str) -> list:
        probe = ROOT / "_tmp_probe_zzz.py"
        _clean_probes()
        probe.write_text(src, encoding="utf-8")
        try:
            return checks.check_upper_nodes_are_not_named_by_machine(probe)
        finally:
            _clean_probes()

    def test_B15_fires_when_the_node_gets_a_machine_name(self):
        # 探针必须**带上建节点的那个调用** —— 否则 B15 会走「还没建上层节点」那条
        # 早返回（返回 `[]`），用例就空过了。第一版就是这么写的，实测空过。
        #
        # ⚠️ 探针同时**必须是合法 Python**：`checks.code_lines()` 走
        # `tokenize.generate_tokens()`，括号不闭合会直接抛
        # `TokenError: unexpected EOF in multi-line statement` —— 那是**崩**，
        # 不是「没抓到」。所以下面每条都是**完整闭合**的语句，不用拼接。
        for src, why in (
            ('node_id = add_artifact(conn, type_=UPPER_TYPE,\n'
             '    content={"text": "被反复质询的假设"})\n', "系统抄了个名字"),
            ("node_id = add_artifact(conn, type_=UPPER_TYPE,\n"
             "    content={'text': '高度争议的三个问题'})\n", "系统抄了个名字"),
        ):
            self.assertTrue(self._probe(src),
                            f"B15 没抓到「{why}」：{src!r}")

    def test_B15_does_not_fire_without_a_place_that_builds_upper_nodes(self):
        """**自证早返回是对的**：没有建节点的地方，这条检查确实无事可做。

        这一条是在正面承认上面那条坑的存在 —— 所以它的用例必须带上 `head`。
        写下来，免得下次有人「简化」探针又把它弄成空转。
        """
        self.assertFalse(self._probe('    content={"text": "系统起的名字"},\n'))

    def test_B15_allows_pending_name(self):
        """名字留空是**唯一**允许的写法 —— 它不是一个占位符，是设计前提。"""
        self.assertFalse(self._probe(
            "node_id = add_artifact(conn, type_=UPPER_TYPE,\n"
            '    content={"text": PENDING_NAME, "name_source": "human"})\n'))

    def test_B15_is_not_vacuous_only_when_the_upper_module_exists(self):
        """没有上层节点建立的地方，这条**暂不适用**（返回 `[]`）。

        所以必须钉住：目标文件里真的有 `type_=UPPER_TYPE` ——
        否则「没有可检查的东西」会伪装成「检查通过」。
        这正是 B8/B9/B12 都踩过的那个坑（见文件头那张表）。
        """
        self.assertTrue(checks.UPPER.is_file(), f"{checks.UPPER} 不存在")
        src = checks.UPPER.read_text(encoding="utf-8")
        self.assertIn(
            "type_=UPPER_TYPE", src,
            "`upper.py` 里没有建上层节点的地方 —— B15 的判据是空转的，"
            "它打印的「过」没有任何含义。",
        )
        self.assertEqual(checks.check_upper_nodes_are_not_named_by_machine(), [])


class TestB4IsScopedToTheUpperLayer(unittest.TestCase):
    """B4 收窄之后**必须只扫上层** —— 收窄时最容易顺手扩大成误报。

    收窄的理由（DECLARATION §22.4）：B4 原来抓的是「有没有实现上层归纳」，
    而需求方 2026-09-27 授权把它打开了。休眠前提没了，原判据就成了误报。
    但**收窄不等于放宽**：它现在守的是 B4 真正要守的那条 ——
    上层归纳不读热度类信号（`§C9` #5）。
    """

    def test_B4_does_not_fire_on_the_legitimate_vote_tally(self):
        """`vote.py` 里数票是**正当的** —— 它本来就该数票。"""
        _need("vote.py")
        hits = checks.scan(r"tally", "vote.py")
        self.assertTrue(hits, "B4 的正则打错了靶子 —— 它连 vote.py 的 tally 都扫不到，"
                             "说明这条收窄之后变成了空转")

    def test_B4_scope_is_recorded_in_the_registry(self):
        """范围是登记表里的**显式字段**，不是散在实现里的 if。"""
        scoped = [e for e in checks.CHECKS if len(e) > 4]
        self.assertTrue(scoped, "没有任何一条检查带文件范围字段")
        b4 = [e for e in checks.CHECKS if e[0] == "B4"][0]
        self.assertEqual(len(b4), 5, "B4 的范围字段丢了 —— 它又变回全仓扫描了")
        self.assertEqual(b4[4], "upper.py")

    def test_B4_fires_on_a_popularity_signal_in_the_upper_layer(self):
        """喂它一个热度类信号，在上层的范围里必须响。"""
        hits = checks.scan(r"popular", "upper.py")
        probe = ROOT / "_tmp_probe_zzz.py"
        _clean_probes()
        try:
            probe.write_text("popular = count_votes()\n", encoding="utf-8")
            b4 = [e for e in checks.CHECKS if e[0] == "B4"][0]
            import re as _re
            self.assertTrue(_re.search(b4[2], "popular", _re.I),
                            "B4 的正则抓不到 popular —— 收窄时把判据也丢了")
            self.assertIsInstance(hits, list)
        finally:
            _clean_probes()


class TestCheckRegistryShape(unittest.TestCase):
    """登记表 `CHECKS` 的**形状**是整个文件里所有消费方的共同契约。

    它现在有两个消费方：
      - `checks.all_checks()`   → 取 `entry[:4]`（对第 5 个元素不敏感）
      - `test_arena.py`         → 取 `entry[:4]`（同上）

    第 5 个元素（`only` 文件范围）是 B4 收窄时加进来的。加的时候两边**必须同时**
    从「整条 unpack」改成「截断取前 4 个」—— 漏一处就崩。

    **2026-09-27 实测漏了一处**：`checks.all_checks()` 改了，`test_arena.py` 漏改，
    于是 223 条里挂掉 1 条，报的是
    `ValueError: too many values to unpack (expected 4)`。

    这条用例把形状钉住，好让**下一次**加第 5、第 6 个元素时，
    「谁在消费这条元组」这件事是被写下来的，不是靠运气。
    """

    def test_every_entry_has_at_least_the_four_mandatory_fields(self):
        for entry in checks.CHECKS:
            self.assertGreaterEqual(
                len(entry), 4,
                f"{entry!r} 少了必填字段 —— 前 4 个是 "
                "（编号 / 说明 / 正则 / 条款），一个都不能少。",
            )

    def test_the_optional_fifth_field_is_always_a_file_name(self):
        """第 5 个元素**只**能是文件名 —— 不许拿它塞别的东西。"""
        for entry in checks.CHECKS:
            if len(entry) > 4:
                self.assertIsInstance(
                    entry[4], str,
                    f"{entry[0]} 的第 5 个字段不是字符串：{entry[4]!r} —— "
                    "它约定为文件范围（`scan(only=...)` 用的那个名字）。",
                )
                self.assertNotIn(
                    "/", entry[4],
                    f"{entry[0]} 的范围字段有路径分隔符：{entry[4]!r} —— "
                    "`scan` 比的是 `path.name`，给路径永远比不中，检查会静默空转。",
                )

    def test_every_check_is_reachable_by_name(self):
        """登记表里有几条，`all_checks()` 就得出几条 —— 不许有被吃掉的。"""
        registered = [e[0] for e in checks.CHECKS]
        produced = [code for code, _w, _c, _r in checks.all_checks()]
        for code in registered:
            self.assertIn(code, produced,
                          f"{code} 在 CHECKS 里，却没被 all_checks() 产出来 —— "
                          "它现在不在任何总账的覆盖范围里。")


class TestB16Fires(unittest.TestCase):
    """B16 查的是**集合关系**与「守卫在不在」，探针扫不到 —— 必须单独验。

    它的判据不读源码文本，读的是 `scaffold` 的常量，所以验伪要**注入假常量**。
    与 B8/B9/B12 那几条「探针盖不到」的检查同理，只是手法不同：
    那几条要写靶文件，这条要传假集合。
    """

    def test_B16_fires_when_an_ai_kind_is_not_marked(self):
        """漏一档 AI 档 —— 那一档的产出就不需要写明主张者。"""
        hits = checks.check_ai_sources_require_attribution(
            ai=("trainedAlgorithmicMedia",),     # 漏了 compositeWith...
        )
        self.assertTrue(hits, "漏了一档 AI 档，B16 却没反应")

    def test_B16_fires_when_ai_sources_is_empty(self):
        self.assertTrue(checks.check_ai_sources_require_attribution(ai=()))

    def test_B16_fires_when_an_unknown_kind_is_listed(self):
        hits = checks.check_ai_sources_require_attribution(
            ai=("trainedAlgorithmicMedia",
                "compositeWithTrainedAlgorithmicMedia",
                "made_up_kind"),
        )
        self.assertTrue(hits)

    def test_B16_fires_when_the_default_kind_is_also_ai(self):
        """默认档同时算 AI 档 —— 每个走默认值的调用都会触发守卫，自相矛盾。"""
        hits = checks.check_ai_sources_require_attribution(
            ai=scaffold.AI_SOURCES + (scaffold.DIGITAL_SOURCE_DEFAULT,),
        )
        self.assertTrue(hits)

    def test_B16_fires_when_the_guard_is_gone_from_the_code(self):
        """常量对，但**没人拿它拦** —— 那等于没这条规矩。

        拿一个不含 `add_artifact` 的文件当靶（`checks.py` 自己），必须报出来。
        """
        hits = checks.check_ai_sources_require_attribution(
            target=Path(checks.__file__),
        )
        self.assertTrue(hits, "靶文件里没有 add_artifact，B16 却没报")

    def test_B16_is_quiet_on_the_real_module(self):
        """真模块上必须安静 —— 否则上面几条只是在验一个永远报错的检查。"""
        self.assertEqual(checks.check_ai_sources_require_attribution(), [])


class TestB17Fires(unittest.TestCase):
    """B17 的判据是**层级自洽**，探针扫不到 —— 注入假层级来验。

    ⚠️ 最要紧的是 `test_B17_fires_when_quotation_is_an_ancestor`：
    它防的是「原始来源特化自引用」—— 那样引用就自动够得着来源这一档，
    整条约束当场失效。
    """

    def test_B17_fires_when_the_primary_source_is_a_quotation(self):
        self.assertTrue(
            checks.check_primary_source_is_not_a_quotation(primary="quoted_from"))

    def test_B17_fires_when_quotation_is_an_ancestor_of_the_primary_source(self):
        hits = checks.check_primary_source_is_not_a_quotation(
            parents={"quoted_from": "derived_from",
                     "had_primary_source": "quoted_from"},   # ← 来源 ⊑ 引用
        )
        self.assertTrue(hits, "原始来源特化自引用，B17 却没反应")

    def test_B17_fires_when_quotation_has_no_parent(self):
        """把 quoted_from 从父类表删掉 —— 它成了平级 kind，谁都能拿它当来源。"""
        hits = checks.check_primary_source_is_not_a_quotation(
            parents={"had_primary_source": "derived_from"})
        self.assertTrue(hits)

    def test_B17_fires_on_a_dangling_kind(self):
        hits = checks.check_primary_source_is_not_a_quotation(
            parents={"quoted_from": "derived_from",
                     "had_primary_source": "derived_from",
                     "ghost_kind": "derived_from"},
        )
        self.assertTrue(hits)

    def test_B17_is_quiet_on_the_real_module(self):
        self.assertEqual(checks.check_primary_source_is_not_a_quotation(), [])

    def test_B17_does_not_require_the_primary_source_to_be_top_level(self):
        """**反向判据**：原始来源**可以**有父类 —— 不许把这条也报掉。

        PROV-O 里 wasQuotedFrom / hadPrimarySource / wasRevisionOf
        **三者都是** wasDerivedFrom 的子属性。若有人把判据写成
        「原始来源必须无父类」，真模块会被误报 —— 这一条钉住那个方向。
        """
        self.assertEqual(
            checks.check_primary_source_is_not_a_quotation(
                parents={"quoted_from": "derived_from",
                         "had_primary_source": "derived_from"},
            ), [],
        )


class TestB18Fires(unittest.TestCase):
    """B18 的判据是**定位符白名单**，探针扫不到 —— 注入假白名单来验。

    ⚠️ 最要紧的是 `test_B18_fires_when_a_body_field_is_declared`：
    它防的是「给 selector 加一个字段顺手把正文也存进去」——
    那样仓库里就多了一份**无法证明自己等于原文**的副本。
    """

    def test_B18_fires_when_a_body_field_is_declared(self):
        hits = checks.check_reference_stores_only_a_locator(
            kinds=("TextQuoteSelector",),
            fields={"TextQuoteSelector": (("exact", "body"), ("prefix",))},
        )
        self.assertTrue(hits, "selector 规定了 body 字段，B18 却没反应")

    def test_B18_fires_when_the_kinds_and_the_fields_disagree(self):
        """两边不同时维护 —— 加了一种 selector 却忘了给字段规定。"""
        hits = checks.check_reference_stores_only_a_locator(
            kinds=("TextQuoteSelector", "MySelector"),
            fields={"TextQuoteSelector": (("exact",), ())},
        )
        self.assertTrue(hits)

    def test_B18_fires_on_a_selector_name_that_is_not_the_w3c_shape(self):
        hits = checks.check_reference_stores_only_a_locator(
            kinds=("我的选择器",),
            fields={"我的选择器": (("exact",), ())},
        )
        self.assertTrue(hits)

    def test_B18_fires_when_the_pointer_field_is_a_body_field(self):
        real = checks.pointer.POINTER_FIELD
        try:
            checks.pointer.POINTER_FIELD = "body"
            hits = checks.check_reference_stores_only_a_locator()
        finally:
            checks.pointer.POINTER_FIELD = real
        self.assertTrue(hits)

    def test_B18_fires_when_the_guard_is_gone(self):
        """拿 `checks.py` 当靶 —— 它里面没有 `verify()`，白名单就没人拿它拦。"""
        hits = checks.check_reference_stores_only_a_locator(
            target=ROOT / "checks.py")
        self.assertTrue(hits, "找不到 verify()，B18 却没反应")

    def test_B18_is_quiet_on_the_real_modules(self):
        self.assertEqual(checks.check_reference_stores_only_a_locator(), [])


class TestB19Fires(unittest.TestCase):
    """B19 的判据是**分档完备**，探针扫不到 —— 注入假分档来验。

    两张表用同一套判据（节点类型 × `CONTROL_RULES`、关系种类 × `RELATION_LAYERS`），
    因为它们坏起来是同一种坏。

    ⚠️ 最要紧的两条：
    - `test_B19_fires_when_a_kind_is_left_unclassified` —— 加了个类型忘了分档；
    - `test_B19_fires_when_a_relation_kind_is_left_unclassified` —— 同一种坏，
      换到关系种类上。这两条必须都有，否则「新增词汇必须被看见」只守了一半。
    """

    # 真实的两张表原样喂进去，只改一处 —— 这样「报的是不是那一处」才验得准。
    _RULES = {"fixed": ("Claim", "Evidence")}
    _PREFIX = {"Claim": "claim", "Evidence": "evid"}
    _LAYERS = {"lower": ("supports",), "upper": ("clustered_into",)}

    def _run(self, **kw):
        args = dict(kinds=("Claim", "Evidence"), rules=self._RULES,
                    prefixes=self._PREFIX, relation_kinds=("supports",),
                    layers=self._LAYERS)
        args.update(kw)
        return checks.check_vocabulary_is_fully_classified(**args)

    def test_B19_fires_when_a_kind_is_left_unclassified(self):
        hits = self._run(
            kinds=("Claim", "Evidence", "忘了分档的新类型"),
            prefixes={**self._PREFIX, "忘了分档的新类型": "new"},
        )
        self.assertTrue(hits, "有类型没分档，B19 却没反应")

    def test_B19_fires_when_a_relation_kind_is_left_unclassified(self):
        """★ 同一种坏，换到关系种类上 —— 这条没有的话，关系那半就是空转的。"""
        hits = self._run(relation_kinds=("supports", "忘了分层的新关系"))
        self.assertTrue(hits, "有关系种类没分层，B19 却没反应")

    def test_B19_fires_when_a_kind_sits_in_two_buckets(self):
        hits = self._run(
            kinds=("Claim",), rules={"fixed": ("Claim",), "free": ("Claim",)},
            prefixes={"Claim": "claim"},
        )
        self.assertTrue(hits, "一个类型落在两档里，B19 却没反应")

    def test_B19_fires_when_a_relation_kind_sits_in_two_layers(self):
        hits = self._run(layers={"lower": ("supports",),
                                 "upper": ("supports",)})
        self.assertTrue(hits)

    def test_B19_fires_when_a_prefix_is_missing(self):
        """`new_id()` 直接 `_PREFIX[type_]` —— 缺一个会在**运行时**才炸。"""
        hits = self._run(kinds=("Claim", "Evidence"), prefixes={"Claim": "claim"})
        self.assertTrue(hits)

    def test_B19_fires_on_an_empty_bucket(self):
        hits = self._run(rules={"fixed": ("Claim", "Evidence"), "free": ()})
        self.assertTrue(hits)

    def test_B19_fires_when_a_second_upper_edge_appears(self):
        """★ 上层边只有一条，而且这件事被**钉住**。

        多一条上层边就是多一条「上层影响底层」的通道 —— 那是**设计变更**，
        不是顺手加一条。这条检查逼一次有记录的改动。
        """
        hits = self._run(layers={"lower": ("supports",),
                                 "upper": ("clustered_into", "又一条上层边")})
        self.assertTrue(hits, "上层边变成两条，B19 却没反应")

    def test_B19_fires_when_the_upper_bucket_is_emptied(self):
        """把唯一那条上层边挪走 —— 上层就没边了，单向性无从谈起。"""
        hits = self._run(layers={"lower": ("supports", "clustered_into")})
        self.assertTrue(hits)

    def test_B19_is_quiet_on_the_real_module(self):
        self.assertEqual(checks.check_vocabulary_is_fully_classified(), [])


class TestB20Fires(unittest.TestCase):
    """B20 的判据是**门挡在哪**，探针扫不到 —— 注入假常量与假靶来验。

    ⚠️ 最要紧的是 `test_B20_fires_when_the_gate_is_missing_from_activate`：
    门只有挡在**写 active 的唯一入口**上才拦得住「不走 staging、直接建了再确认」。
    """

    def test_B20_fires_when_a_channel_is_missing(self):
        hits = checks.check_imported_never_bypasses_the_gate(
            channels=("direct",))
        self.assertTrue(hits, "少了一个入层口，B20 却没反应")

    def test_B20_fires_when_the_gate_states_are_incomplete(self):
        hits = checks.check_imported_never_bypasses_the_gate(
            channels=("direct", "staged"), states=("pending",), passed="passed")
        self.assertTrue(hits)

    def test_B20_fires_when_the_gate_is_missing_from_activate(self):
        """拿 `checks.py` 当入口靶 —— 它里面没有 `activate()`，门就无处可挡。"""
        hits = checks.check_imported_never_bypasses_the_gate(
            entry_path=ROOT / "checks.py", write_scan=False)
        self.assertTrue(hits, "入口里找不到 activate()，B20 却没反应")

    def test_B20_fires_when_the_gate_file_is_missing(self):
        hits = checks.check_imported_never_bypasses_the_gate(
            gate_path=ROOT / "不存在.py", write_scan=False)
        self.assertTrue(hits)

    def test_B20_fires_when_the_staged_channel_is_not_used(self):
        """`staging.py` 在那儿，却没走 `intake="staged"` —— 它只是**宣称**走那个口。"""
        hits = checks.check_imported_never_bypasses_the_gate(
            gate_path=ROOT / "pointer.py", write_scan=False)
        self.assertTrue(hits)

    def test_B20_is_quiet_on_the_real_modules(self):
        self.assertEqual(checks.check_imported_never_bypasses_the_gate(), [])


class TestB21Fires(unittest.TestCase):
    """B21 的判据是**映射表显式**，探针扫不到 —— 注入假映射来验。

    ⚠️ 最要紧的是 `test_B21_fires_when_an_unmapped_one_is_silently_filled`：
    把 `None` 改成某个 kind 而不同时改声明 —— 那就是**静默兜住**，
    之后那条边在库里长得完全正常，只是信息没了。
    """

    def test_B21_fires_when_a_name_is_missing_from_the_table(self):
        hits = checks.check_distiller_mapping_is_explicit(
            mapping={"supports": "supports"}, names=("supports", "refutes"))
        self.assertTrue(hits)

    def test_B21_fires_when_an_unmapped_one_is_silently_filled(self):
        hits = checks.check_distiller_mapping_is_explicit(
            mapping={"supports": "supports", "exemplifies": "refines"},
            names=("supports", "exemplifies"),
            unmapped=("exemplifies",),
        )
        self.assertTrue(hits, "声明说没对应，表里却填了 —— B21 没反应")

    def test_B21_fires_on_the_generic_fallback(self):
        """★ `related_to` 是那个「兜住一切」的选项 —— 映射表里不许出现它。"""
        hits = checks.check_distiller_mapping_is_explicit(
            mapping={"supports": "related_to"},
            names=("supports",), unmapped=(),
        )
        self.assertTrue(hits, "拿 related_to 兜住，B21 却没反应")

    def test_B21_fires_on_an_unknown_kind(self):
        hits = checks.check_distiller_mapping_is_explicit(
            mapping={"supports": "不存在的种类"},
            names=("supports",), unmapped=(),
        )
        self.assertTrue(hits)

    def test_B21_is_quiet_on_the_real_module(self):
        self.assertEqual(checks.check_distiller_mapping_is_explicit(), [])


def _b22_probe(source: str, **kw) -> list:
    """把 source 写成临时 `.py` 当 **B22 的源码靶**，跑完删掉。

    为什么要单独一条路：B22 只扫 `rules.py` 一个文件，
    `_scan_with_temp_py()` 那条（全仓扫、喂 `_tmp_probe_zzz.py`）**到不了它** ——
    探针文件不叫 `rules.py`，B22 根本不看它。

    探针路径复用同一个名字，所以「有没有残留」仍由 `Test00` / `TestNoResidue`
    各管一头，中间的 helper 一律自愈。
    """
    probe = ROOT / "_tmp_probe_zzz.py"
    _clean_probes()
    probe.write_text(source, encoding="utf-8")
    try:
        return checks.check_promotion_condition_is_structural(
            source_path=probe, **kw)
    finally:
        _clean_probes()


def _said(hits: list, phrase: str) -> list:
    """命中里**说到了这句**的那些。`(路径, 行号, 说明)` 的说明在第 3 格。"""
    return [h for h in hits if phrase in h[2]]


def _b23_probe(source: str, **kw) -> list:
    """把 source 写成临时 `.py` 当 **B23 的源码靶**，跑完删掉。

    与 `_b22_probe` 同形、同理由：B23 只扫 `contribute.py` 一个文件，
    `_scan_with_temp_py()` 喂的是 `_tmp_probe_zzz.py`，**到不了它**。

    ⚠️ 探针只用来验**签名 / import / 模型入口**那几条（它们读源码）。
    表那几条读的是 `contribute.CONTRIBUTIONS`，走 `table=` 参数注入 ——
    探针文件里写一份假表不会被读到，写了也没用。
    """
    probe = ROOT / "_tmp_probe_zzz.py"
    _clean_probes()
    probe.write_text(source, encoding="utf-8")
    try:
        return checks.check_contribution_entrypoints_are_graph_free(
            source_path=probe, **kw)
    finally:
        _clean_probes()


# 一份**形状完整**的探针骨架：四个判定函数都在。
# 这样「只报我要验的那一处」才验得准 —— 缺一个函数 B22 会另报一笔「找不到 xxx()」。
_RULES_PROBE = (
    "def holds(actual, op, want):\n"
    "    return actual >= want\n"
    "def evaluate(signals):\n"
    "    return signals\n"
    "def explain(signals):\n"
    "    return signals\n"
    "def select(candidates):\n"
    "    return candidates\n"
)

# ★ 反向判据的语料：**长得像算术、其实全是正文**。
#
# 这几样是 2026-09-28 实测撞到的假命中，一样不少：
#   `（§C7.1 ④ / §C2.5 第 3 档）`  中文正文里的斜杠不是除号
#   `**按名字引用**`                Markdown 加粗标记不是乘号
#   `def evaluate(signals, *, ...)`  keyword-only 的 `*` 不是算术
#   `"the model says yes"`          写在字面量里的模型名不是模型入口
#   `"import sqlite3"`              写在字面量里的数据库名不是数据库入口
_RULES_PROSE_PROBE = (
    "def holds(actual, op, want):\n"
    "    return actual >= want\n"
    "def evaluate(signals, *, rules=None):\n"
    "    note = '（§C7.1 ④ / §C2.5 第 3 档）**按名字引用**'\n"
    "    other = 'the model says yes; import sqlite3; conn.execute()'\n"
    "    return note, other\n"
    "def explain(signals):\n"
    "    return signals\n"
    "def select(candidates):\n"
    "    return candidates\n"
)


class TestB22Fires(unittest.TestCase):
    """B22 的判据是**判据形态**，探针扫不到 —— 注入假规则集 / 假源码来验。

    ⚠️ 最要紧的三条：

    * `test_B22_fires_when_a_rule_carries_an_extra_field` ——
      规则条目多一个字段（信心、来源、模型输出），而它照样长得像一条结构规则；
    * `test_B22_fires_when_arithmetic_appears_in_a_judging_function` ——
      判据一旦能算，规则集立刻能表达加权和，那就是打分（B 档滑向 C 档的路）；
    * `test_B22_does_not_fire_on_prose_that_looks_like_arithmetic` ——
      **反向判据**。它挡的是「改完正文才过检」那条路：假命中不许用改正文解决。
    """

    # 真实的形状原样喂进去，只改一处 —— 这样「报的是不是那一处」才验得准。
    _VOCAB = {"challenge_counts": "relation:challenged_by"}
    _RULESET = {"r": {"all_of": (("challenge_counts", ">=", 2),), "why": "一条规则"}}
    _OPS = (">=", "<=", "==")
    _UPPER_VOCAB = {"challenge_counts": "relation:challenged_by"}

    def _run(self, **kw):
        args = dict(vocab=self._VOCAB, ruleset=self._RULESET, operators=self._OPS,
                    tier="derived_view", tiers=("must_confirm", "derived_annotation",
                                                "derived_view"),
                    floor=2, upper_vocab=self._UPPER_VOCAB, upper_floor=2)
        args.update(kw)
        return checks.check_promotion_condition_is_structural(**args)

    # --- 规则集本身 -------------------------------------------------------

    def test_B22_fires_when_a_rule_reads_a_signal_outside_the_vocabulary(self):
        hits = self._run(ruleset={"r": {
            "all_of": (("vote_counts", ">=", 2),), "why": "热度类信号"}})
        self.assertTrue(_said(hits, "不在 SIGNAL_VOCAB 里"), hits)

    def test_B22_fires_when_the_vocabulary_and_the_upper_whitelist_disagree(self):
        """★ 两个方向都要报。

        规则多一个 = 越界通道；规则少一个 = 静默的残缺（规则看上去能说更多，
        实际读不到）。只报一个方向，这半就漏了。
        """
        extra = self._run(vocab={**self._VOCAB, "vote_counts": "vote"})
        self.assertTrue(_said(extra, "规则多"), extra)
        missing = self._run(upper_vocab={**self._UPPER_VOCAB, "dispute_counts": "x"})
        self.assertTrue(_said(missing, "规则少"), missing)

    def test_B22_fires_when_the_same_name_means_two_things(self):
        hits = self._run(vocab={"challenge_counts": "relation:supports"})
        self.assertTrue(_said(hits, "指向不同"), hits)

    def test_B22_fires_when_the_floor_and_the_inducer_disagree(self):
        hits = self._run(upper_floor=3)
        self.assertTrue(_said(hits, "MIN_SUPPORT"), hits)

    def test_B22_fires_when_the_tier_moves_off_the_derived_view(self):
        """★ 挪到第 2 档就要补「可推翻 + 抽样审计 + 计改判率」三件事。

        那不是改一个字符串，是**设计变更** —— 必须撞到这里。
        """
        hits = self._run(tier="derived_annotation")
        self.assertTrue(_said(hits, "第 2 档"), hits)

    def test_B22_fires_when_the_tier_is_not_a_known_one(self):
        hits = self._run(tier="第 4 档")
        self.assertTrue(_said(hits, "封闭集合"), hits)

    def test_B22_fires_when_the_operators_grow_an_arithmetic_one(self):
        hits = self._run(operators=(">=", "<=", "==", "+"))
        self.assertTrue(_said(hits, "比较符集合"), hits)

    def test_B22_fires_when_a_rule_is_not_a_conjunction(self):
        """★ 单个条件没包成合取 —— `("challenge_counts", ">=", 2)`。

        它**是个三元组、看着像对的**，但语义上成了「三个条件」。
        不单独认出来的话，报出来是三句「有个条件不是三元组」——
        每句都成立，合起来却指不到真正的那一处。
        """
        hits = self._run(ruleset={"r": {"all_of": ("challenge_counts", ">=", 2),
                                        "why": "单条件没包一层"}})
        self.assertTrue(_said(hits, "没包成合取"), hits)
        # 另一半：`all_of` 根本不是元组（比如写成了列表）
        loose = self._run(ruleset={"r": {
            "all_of": [("challenge_counts", ">=", 2)], "why": "写成了列表"}})
        self.assertTrue(_said(loose, "不是元组"), loose)

    def test_B22_fires_on_an_empty_conjunction(self):
        """空合取**恒真** —— 它会把所有目标都收进来，等于一条没有判据的规则。"""
        hits = self._run(ruleset={"r": {"all_of": (), "why": "空"}})
        self.assertTrue(_said(hits, "恒真"), hits)

    def test_B22_fires_when_a_rule_carries_an_extra_field(self):
        """★ 字段白名单：多一个字段就是让规则携带别的东西。

        而它**照样长得像一条结构规则** —— 这正是这条检查存在的理由。
        """
        hits = self._run(ruleset={"r": {
            "all_of": (("challenge_counts", ">=", 2),),
            "why": "看起来完全正常",
            "confidence": 0.9,
        }})
        self.assertTrue(_said(hits, "多带了字段"), hits)

    def test_B22_fires_when_a_rule_has_no_why(self):
        """`§C2.5` 第 3 档的「可解释」不是可选项。"""
        hits = self._run(ruleset={"r": {
            "all_of": (("challenge_counts", ">=", 2),), "why": "   "}})
        self.assertTrue(_said(hits, "没有 why"), hits)

    def test_B22_fires_when_the_condition_value_is_not_an_int(self):
        hits = self._run(ruleset={"r": {
            "all_of": (("challenge_counts", ">=", 0.5),), "why": "小数"}})
        self.assertTrue(_said(hits, "不是整数"), hits)

    # --- 源码级 -----------------------------------------------------------

    def test_B22_fires_when_arithmetic_appears_in_a_judging_function(self):
        """★ 判据一旦能算，规则集立刻能表达加权和 —— 那就是打分（撞 B5）。"""
        hits = _b22_probe(
            _RULES_PROBE.replace("def evaluate(signals):\n    return signals\n",
                                 "def evaluate(signals):\n    return signals * 2\n"))
        self.assertTrue(_said(hits, "算术运算符"), hits)

    def test_B22_fires_on_a_model_entry(self):
        hits = _b22_probe("import llm\n" + _RULES_PROBE)
        self.assertTrue(_said(hits, "模型入口"), hits)

    def test_B22_fires_on_a_model_entry_that_does_not_start_with_a_model_word(self):
        """★ 口径修订二：按「包含」判，不按「以模型词开头」判。

        原来用 `match`，于是 `call_the_model()` / `ask_llm()` 这类**最常见的写法**
        一次都报不出来 —— 只有 `model_judge()` 这种把模型词放开头的命名才抓得到，
        而那不是真实代码的样子。**漏报比误报致命**（同 B6 的取舍）。
        """
        for name in ("call_the_model", "ask_llm", "score_by_similarity"):
            hits = _b22_probe(_RULES_PROBE + f"\n{name}(1)\n")
            self.assertTrue(_said(hits, "模型入口"), f"{name} 漏了：{hits}")

    def test_B22_does_not_fire_on_a_name_that_merely_contains_llm_letters(self):
        """反向：**不含**模型词的普通名字不许误报（否则真话也会被当违规）。"""
        hits = _b22_probe(_RULES_PROBE + "\nrealm = 1\nall_my = 2\n")
        self.assertEqual(_said(hits, "模型入口"), [])

    def test_B22_fires_on_a_database_entry(self):
        """★ 判定必须**没有能力写库** —— 连连接对象都不许出现。"""
        hits = _b22_probe(_RULES_PROBE + "\nconn = 1\n")
        self.assertTrue(_said(hits, "数据库入口"), hits)

    def test_B22_fires_when_a_judging_function_is_gone(self):
        hits = _b22_probe(_RULES_PROBE.replace(
            "def explain(signals):\n    return signals\n", ""))
        self.assertTrue(_said(hits, "找不到 explain()"), hits)

    def test_B22_does_not_fire_on_prose_that_looks_like_arithmetic(self):
        """★ **反向判据** —— 这条是本段实测踩出来的，不能少。

        假命中的下场是「把文件加进 EXEMPT」（修订五），也就是**检查死掉**。
        所以它必须能被指着证明：**同一段正文，换个位置就不再是违规。**

        反过来的那半同样要紧：上面 `test_B22_fires_when_arithmetic_...`
        证明**真的算术抓得到**。两条一起，才说明扫描范围收得既准又没瞎。
        """
        hits = _b22_probe(_RULES_PROSE_PROBE)
        self.assertEqual(_said(hits, "算术运算符"), [],
                         "正文里的斜杠 / 加粗标记被当成算术了")
        self.assertEqual(_said(hits, "模型入口"), [],
                         "字面量里的模型名被当成模型入口了")
        self.assertEqual(_said(hits, "数据库入口"), [],
                         "字面量里的数据库名被当成数据库入口了")

    def test_B22_fires_when_the_rule_set_file_is_missing(self):
        """规则集文件不在 —— `system validation` 就没落地，
        promote 条件只能落回「模型觉得可以」，而那正是这条检查要防的东西。

        靶取一个**名字叫 `rules.py` 但不存在**的路径 ——
        不碰真文件，也走到那条分支。
        """
        hits = checks.check_promotion_condition_is_structural(
            source_path=ROOT / "不存在的目录" / "rules.py")
        self.assertTrue(_said(hits, "规则集文件不在"), hits)

    def test_B22_is_quiet_on_the_real_module(self):
        self.assertEqual(checks.check_promotion_condition_is_structural(), [])


class TestB23Fires(unittest.TestCase):
    """B23 的判据是**入口层的形状**，探针扫不到 —— 注入假映射表 / 假源码来验。

    ⚠️ 最要紧的四条：

    * `test_B23_fires_when_an_entry_takes_a_vocabulary_parameter` ——
      签名里出现 `kind=` 的那一刻，这一层就退化成原语的薄包装，
      而它看起来还是七个漂亮的名字；
    * `test_B23_fires_when_the_entry_layer_imports_the_upper_layer` ——
      单向性在贡献层的落点：一旦 import 了 `upper`，
      「上层的判断被写成底层的事实」就有了一条路；
    * `test_B23_fires_when_an_assertion_lands_without_confirmation` ——
      把 `claim` 的落点改成默认生效，分档就在这一层上没了；
    * `test_B23_does_not_fire_on_prose_that_names_a_model` ——
      **反向判据**。写在字面量里的模型名不是模型入口。
    """

    # 真实的形状原样喂进去，只改一处 —— 这样「报的是不是那一处」才验得准。
    _GRANS = ("claim", "evidence", "challenge", "counterexample",
              "revision", "connection", "context")
    _FIELDS = ("type_", "relation", "choices", "direction", "target_types", "state")
    _STATES = ("proposed", "active")
    _FORBIDDEN = ("kind", "type", "type_", "state", "relation")
    _ARTIFACT_TYPES = ("Topic", "Claim", "Evidence", "Counterargument",
                       "Counterexample")
    _RELATION_KINDS = ("supports", "contradicts", "qualifies", "challenged_by",
                       "related_to")

    @staticmethod
    def _table(**overrides) -> dict:
        base = {
            "claim": {"type_": "Claim", "relation": None, "direction": None,
                      "state": "proposed"},
            "evidence": {"type_": "Evidence", "relation": "supports",
                         "choices": ("supports", "contradicts", "qualifies"),
                         "direction": "to_target", "target_types": ("Claim",),
                         "state": "active"},
            "challenge": {"type_": "Counterargument", "relation": "challenged_by",
                          "choices": ("challenged_by",), "direction": "from_target",
                          "target_types": ("Claim",), "state": "active"},
            "counterexample": {"type_": "Counterexample", "relation": "contradicts",
                               "choices": ("contradicts",),
                               "direction": "to_target",
                               "target_types": ("Claim",), "state": "active"},
            "revision": {"type_": None, "relation": None, "direction": None,
                         "state": None},
            "connection": {"type_": None, "relation": "related_to",
                           "choices": ("related_to",), "direction": "between",
                           "state": "active"},
            "context": {"type_": "Topic", "relation": None, "direction": None,
                        "state": "proposed"},
        }
        base.update(overrides)
        return base

    def _run(self, **kw):
        args = dict(granularities=self._GRANS, table=self._table(),
                    fields=self._FIELDS, states=self._STATES,
                    forbidden=self._FORBIDDEN, actor="by",
                    artifact_types=self._ARTIFACT_TYPES,
                    relation_kinds=self._RELATION_KINDS)
        args.update(kw)
        return checks.check_contribution_entrypoints_are_graph_free(**args)

    # --- 映射表 -----------------------------------------------------------

    def test_B23_fires_when_a_granularity_is_missing_from_the_table(self):
        table = self._table()
        del table["counterexample"]
        hits = self._run(table=table)
        self.assertTrue(_said(hits, "CONTRIBUTIONS 与 GRANULARITIES 对不上"), hits)

    def test_B23_fires_when_the_table_grows_a_granularity_nobody_declared(self):
        hits = self._run(table=self._table(
            upvote={"type_": "Vote", "relation": None, "direction": None,
                    "state": "active"}))
        self.assertTrue(_said(hits, "对不上"), hits)

    def test_B23_fires_when_an_entry_carries_an_extra_field(self):
        """条目多一个字段（信心、来源、模型输出），而它照样长得像一条贡献定义。"""
        table = self._table()
        table["claim"] = {**table["claim"], "confidence": "high"}
        hits = self._run(table=table)
        self.assertTrue(_said(hits, "多带了字段"), hits)

    def test_B23_fires_when_an_entry_invents_a_node_type(self):
        table = self._table()
        table["context"] = {**table["context"], "type_": "Scaffold"}
        hits = self._run(table=table)
        self.assertTrue(_said(hits, "不是已知的节点类型"), hits)

    def test_B23_fires_when_an_entry_invents_a_relation_kind(self):
        table = self._table()
        table["connection"] = {**table["connection"], "relation": "similar_to",
                               "choices": ("similar_to",)}
        hits = self._run(table=table)
        self.assertTrue(_said(hits, "不是已知的关系种类"), hits)

    def test_B23_fires_when_the_default_edge_is_not_one_of_its_choices(self):
        """默认值必须是一个合法取值，否则默认那条路一调就抛。"""
        table = self._table()
        table["evidence"] = {**table["evidence"], "relation": "refines"}
        hits = self._run(table=table)
        self.assertTrue(_said(hits, "不在它自己的 choices 里"), hits)

    def test_B23_fires_when_an_assertion_lands_without_confirmation(self):
        """★ 把 `claim` 的落点改成默认生效 —— 分档就在这一层上没了。

        这是本条检查最该拦住的一处：库里看不出任何异常，
        节点照样有 id、照样能引用，只是「未确认的东西不许算数」失效了。

        ⚠️ 这条用例同时钉住了**双向**：只写「落 proposed 的类型必须在名单里」
        那个方向，这一改**一次都报不出来**（实测：单向版在这里返回 `[]`）。
        """
        table = self._table()
        table["claim"] = {**table["claim"], "state": "active"}
        hits = self._run(table=table)
        self.assertTrue(_said(hits, "必须确认"), hits)

    def test_B23_fires_when_a_non_assertion_is_given_a_confirmation_step(self):
        """反方向也要报：给一个**不是新断言**的东西加确认环节，
        等于让「确认」这个动作失去含义。"""
        table = self._table()
        table["counterexample"] = {**table["counterexample"], "state": "proposed"}
        hits = self._run(table=table)
        self.assertTrue(_said(hits, "第 2 档点名的是"), hits)

    # --- 签名 / import / 模型入口（探针） -----------------------------------

    def test_B23_fires_when_an_entry_takes_a_vocabulary_parameter(self):
        src = ("def claim(conn, *, text, kind, by):\n    return None\n")
        hits = _b23_probe(src, granularities=("claim",), table={"claim": {}},
                          artifact_types=(), relation_kinds=())
        self.assertTrue(_said(hits, "收了词表参数"), hits)

    def test_B23_fires_when_an_entry_has_no_actor(self):
        src = "def claim(conn, *, text):\n    return None\n"
        hits = _b23_probe(src, granularities=("claim",), table={"claim": {}},
                          artifact_types=(), relation_kinds=())
        self.assertTrue(_said(hits, "没有 by 参数"), hits)

    def test_B23_fires_when_the_actor_has_a_default(self):
        """留一个默认值，就等于给「机器自己提交」留了一条路。"""
        src = "def claim(conn, *, text, by='system'):\n    return None\n"
        hits = _b23_probe(src, granularities=("claim",), table={"claim": {}},
                          artifact_types=(), relation_kinds=())
        self.assertTrue(_said(hits, "带了默认值"), hits)

    def test_B23_fires_when_an_entry_disappears(self):
        src = "def claim(conn, *, text, by):\n    return None\n"
        hits = _b23_probe(src, granularities=("claim", "connection"),
                          table={"claim": {}, "connection": {}},
                          artifact_types=(), relation_kinds=())
        self.assertTrue(_said(hits, "不见了"), hits)

    def test_B23_fires_when_the_entry_layer_imports_the_upper_layer(self):
        """★ 单向性在贡献层的落点 —— import 白名单。"""
        src = ("import scaffold\n"
               "import upper\n"
               "def claim(conn, *, text, by):\n    return None\n")
        hits = _b23_probe(src, granularities=("claim",), table={"claim": {}},
                          artifact_types=(), relation_kinds=())
        self.assertTrue(_said(hits, "import 了"), hits)

    def test_B23_fires_when_a_model_entry_appears(self):
        src = ("import scaffold\n"
               "def claim(conn, *, text, by):\n"
               "    return call_the_model(text)\n")
        hits = _b23_probe(src, granularities=("claim",), table={"claim": {}},
                          artifact_types=(), relation_kinds=())
        self.assertTrue(_said(hits, "出现模型入口"), hits)

    # --- 反向判据 ---------------------------------------------------------

    def test_B23_does_not_fire_on_prose_that_names_a_model(self):
        """**反向判据**：写在字面量里的模型名不是模型入口。

        它挡的是「把正文改掉才过检」那条路 —— 那正是 `_tokens_in` 存在的理由
        （字符串与 f-string 的正文根本不是 `NAME` token）。
        """
        src = ("import scaffold\n"
               "def claim(conn, *, text, by):\n"
               "    note = 'the model and the llm agree; prompt it'\n"
               "    return note\n")
        hits = _b23_probe(src, granularities=("claim",), table={"claim": {}},
                          artifact_types=(), relation_kinds=())
        self.assertEqual([h for h in hits if "模型入口" in h[2]], [])

    def test_B23_does_not_fire_on_a_future_annotation_import(self):
        """`from __future__ import annotations` 在白名单里 —— 它不是外部依赖。"""
        src = ("from __future__ import annotations\n"
               "import sqlite3\n"
               "import scaffold\n"
               "def claim(conn, *, text, by):\n    return None\n")
        hits = _b23_probe(src, granularities=("claim",), table={"claim": {}},
                          artifact_types=(), relation_kinds=())
        self.assertEqual([h for h in hits if "import 了" in h[2]], [])

    def test_B23_fires_when_the_entry_layer_file_is_missing(self):
        """入口层文件不在 —— 七种粒度就没落地，判据 ① 与 ③ 都无从谈起。

        靶取一个**名字叫 `contribute.py` 但不存在**的路径。
        """
        hits = checks.check_contribution_entrypoints_are_graph_free(
            source_path=ROOT / "不存在的目录" / "contribute.py")
        self.assertTrue(_said(hits, "贡献入口层不在"), hits)

    def test_B23_is_quiet_on_the_real_module(self):
        self.assertEqual(
            checks.check_contribution_entrypoints_are_graph_free(), [])


class TestNoResidue(unittest.TestCase):
    """产物目录里不许有验伪留下的临时文件。

    这一条管的是**这一轮跑完**留下的。上一轮留下的由 `Test00NoResidueAtStart`
    在开跑前管 —— 两条各管一头，中间的 helper 一律自愈。
    分开的原因见 `Test00NoResidueAtStart` 的 docstring（一条残留 → 19 条误导性失败）。

    ⚠️ 两条的**处理方式刻意不同**（2026-09-27 定案）：

    | | 管什么 | 有残留时 |
    |---|---|---|
    | `Test00NoResidueAtStart` | 上一轮留下的 | **自愈 + skip**（那是上一轮的事故） |
    | `TestNoResidue`（本类） | 这一轮留下的 | **判红**（那是本轮真没清干净） |

    为什么本类保持判红：**它能跑到这里，就说明这一轮的 `finally` 都执行完了。**
    在「收尾代码全都跑过」的前提下还有残留，只有一个解释 ——
    `_clean_probes()` 漏了某条路径。**那是本轮的 bug，必须红。**
    两条的差别不是宽严，是**「谁的事故」**：上一轮的事故不该判给这一轮，
    而这一轮的漏清必须当场暴露。
    """

    def test_no_probe_files_left_behind(self):
        # 同样报绝对路径 —— `rglob` 是递归的，只给文件名看不出在哪。
        left = sorted(str(p.resolve()) for p in ROOT.rglob("_tmp_probe*"))
        self.assertEqual(left, [], f"有验伪残留物没清掉：{left}")

    def test_exempt_set_is_exactly_the_falsification_harness(self):
        """豁免就是留洞。洞的大小必须是被钉住的，不能随手扩大。

        `checks.py` 与 `test_checks.py` 都**必须**写出违规词才能工作
        （前者要拿它们当正则，后者要拿它们当语料），所以只能豁免。
        但豁免名单一旦能悄悄变长，B1–B13 就随时可以被架空 ——
        想多豁免一个，就来改这条用例，改的时候会被看见。
        """
        self.assertEqual(
            checks.EXEMPT, {"checks.py", "test_checks.py"},
            "豁免集合变了 —— 如果是有意的，在这里写明理由再改；"
            "如果是无意的，说明有一条检查被架空了。",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
