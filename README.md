# nested-traceable-discussion-graph

**一个可嵌套、可追溯的讨论图：底层记事实，上层只派生、不倒流。**
**A nested, traceable discussion graph — the lower layer records facts, the upper layer only derives, never flows back.**

[English](README.en.md) · [它是什么](#它是什么) · [两条不变量](#两条不变量) · [怎么验](#怎么验) · [完整论证 `DECLARATION.md`](DECLARATION.md)

> 代码里那些 `§C7.1`、`§C9 #5` 一样的号，指的是 [`DECLARATION.md`](DECLARATION.md) 里的条款 —— **可以逐条对上**。
> 那份文档就是这个结构照着建的东西，一并放在仓库里，不用去别处找。

## 它是什么

一句话：**把一个讨论里的东西分成两层，底层只记「谁说了什么、谁反驳了什么」，上层只负责「把已经被反复质询的东西归到一组」—— 而上层做的一切都不许改到底层一个字。**

这个仓库抽出的是**上层归纳那一层**（以及它依赖的读写原语）：

| 文件 | 是什么 |
|---|---|
| `scaffold.py` | 读写原语：节点、边、版本、事件。**零第三方依赖**（只用 `json` / `sqlite3` / `datetime`） |
| `pointer.py` | 引用定位符 `(uri, selector)` —— 照抄 **W3C Web Annotation**，**只存位置，不存正文** |
| `upper.py` | 上层归纳本身 —— 数信号 → 提候选 → 建节点 → 命名 → 出视图 |
| `rules.py` | 结构规则集 —— `system validation` 的落地形态。判据只能是**已有结构量的合取**，且**没有能力写库** |
| `staging.py` | 蒸馏**入层门** —— 导入层与社区层分开走；DeepRead 八种关系的**显式映射表** |
| `contribute.py` | 社区贡献**七种粒度**的入口 —— 词表只在一张表里，入口签名里一个词表参数都没有 |
| `candidates.py` | **候选关系** —— AI 提的边进得了库，**进不了读数**。入口签名里没有 `state` |
| `policy.py` | 可变动的运维门槛（不是信号，不参与排序） |
| `checks.py` | **24 条**否证检查。**每一条都是可执行的**，不是文档里的形容词 |
| `test_upper.py` / `test_pointer.py` / `test_staging.py` / `test_provenance.py` / `test_rules.py` / `test_contribute.py` / `test_candidates.py` | 行为验证 —— 只依赖本仓库模块与标准库 |
| `test_checks.py` | 证明那 24 条检查**不是空转**的 |
| `DECLARATION.md` | 完整论证。`§7.1` 是双侧框架那一节，`§22` 是上层归纳的实现记录 |

## 导入的与用户说的，走两个口

同一个库里有两类东西，它们的**可变性不同** —— 这一点决定了它们要遵守的规矩也不同：

| | 导入层（书里蒸出来的） | 社区层（用户自己说的） |
|---|---|---|
| 来源 | 外部 —— **书还在** | 内部 —— **不可再生** |
| 出错代价 | 重蒸一遍 | **不可逆** |

所以「不可变」那条约束**只管社区层**。导入层可以整层重建，于是入层门不必挡数量、
只需挡质量。但这个「可整层重建」有前提，而它是**可查的**，不是一句设计意图：

```python
staging.orphan_edges(conn, batch)   # 社区层指进这一批的边
staging.clear_batch(conn, batch=b)  # 有这种边就**默认拒绝**，force=True 才删
```

**没有跨层边 → 删掉这一批，社区层逐字段一致 → 可整层重建。**
有 → 重建会撕开社区层 → 不许自动做，要人看过那份边清单再决定。

门挡在**写 `active` 的唯一入口**上（`scaffold.activate()`），所以
「不走 staging、直接建了再确认」这条路也过不去 —— 而不是只在 `staging.py` 里挡一下。

### 引用只存位置，不存正文

```python
{"uri": "urn:isbn:9780000000000",
 "selector": {"type": "TextQuoteSelector", "exact": "稀缺性来自供给的不可复制"}}
```

理由不是省空间，是**副本无法证明自己等于原文**：原文改了、撤了、换版本，
副本不会跟着变，而读者看不出来。

判据是**字段白名单**，不是长度限制 —— 白名单之外一个字段都放不进去，
于是「顺手把正文也存一份」**没有地方可以发生**。

⚠️ `exact` **允许**：它是定位所需的最小引文，属于 selector 的规定字段。
两者的界在**字段身份**上，不在字数上。

守卫接在 `scaffold._append_revision()` 上 —— 那是 `add_artifact()` 与 `revise()`
**共用的唯一漏斗**。只接在前者上，后者就成了绕路（B14 那条行为验证正是这么抓到的）。

## 它不是什么

先说不做什么，因为它比「做什么」更能说清这个东西：

- **不是聚类引擎。** 它**不做** k-means、谱聚类、embedding 相似度 —— 理由见下面「为什么否掉聚类」。
- **不是排序系统。** 没有排序轴、没有阈值、没有分数。这三条不是「还没做」，是**明令禁止**（`§C7.1` `§C9` #5 #7）。
- **不是投票工具。** 票数、热度、曝光量**不是证据**（不变量 #5）。上层归纳**不读**这些信号。
- **不是完整产品。** 它是一条跑通的结构，不是一套能用的服务 —— 没有前端、没有账号、没有部署。
- **不自动命名。** 节点可以自动建，**但名字必须由人来给** —— 系统一个字都没替你说。

## 两条不变量

这个仓库的全部价值就在这两条上。它们是**可执行的**，不是声明：

### 不变量一：单向性 —— 上层只派生，不倒流

```
底层 ──派生──▶ 上层     允许
上层 ──写成事实──▶ 底层   禁止
```

上层允许影响的**只有三样**：展示顺序 / 默认展开 / 推荐候选。它**不许**写进底层任何字段。

- **静态守**：`checks.py` 的 **B14** —— `upper.py` 里出现 `UPDATE artifact` / `INSERT INTO revision` / `DELETE FROM relation` 就报。
- **行为守**：`test_upper.py` 拦**绕路** —— 走 `scaffold.revise()` 之类的间接写，B14 的正则看不见。

两层加起来才完整。实测：`promote` 只新增 1 个 `ctx-*` 节点 + 3 条 `clustered_into` 边，**底层改动 0 / 删除 0**。

**上层对底层的派生边只有一条**（`clustered_into`），而且这件事**被检查钉住**：
`RELATION_LAYERS` 把 16 种关系分成底层真值边与上层派生边，
B19 断言 `len(RELATION_LAYERS["upper"]) == 1` —— 多一条就是多一条
「上层影响底层」的通道，那是**设计变更**，不是顺手加一条。

### 不变量二：命名归人

节点自动建，**名字由人给**。这不是风格问题 —— 它是这个机制能免掉确认流程的**全部理由**：

> 名字留空，系统就**一句话都没说**。「等谁确认」没有对象，所以不需要确认。

由此推出一条容易被写错的东西：**`PENDING_NAME` 不是占位符，是设计前提。**

- **B15 守**：`Context` 节点的 `text` 不许出现 `PENDING_NAME` 之外的字符串字面量。
- 所以 `state` 管「这条断言有没有被确认」，而「还没起名」是**另一种待定**，由 `name_source` + `PENDING_NAME` 表达。**压进同一个字段就等于说「未命名 = 未确认」**，而那恰好把免确认的理由抹掉了。

## 为什么否掉聚类

这是这个仓库最值得看的一个判断，因为**理由不是「聚类不好」，是「信号选错了」**：

聚类（无论 k-means 还是谱聚类）的驱动信号是**文本相似度**。那是**距离度量**，不在信号白名单里。

> **换算法（k-means → 谱聚类）：信号还是「文本长得像」，一样违规。**
> **换信号（相似度 → 被质询次数）：算法一行不用改，合规了。**

所以：**信号类别比算法选择更根本。**

还有第二条更硬的理由：**簇心是一个新断言。** 「这一簇叫 X」这句话系统说不出来 —— 它凭什么说这几条命题是"同一件事"？那是人的判断。

## 允许的信号（白名单）

只有三种，都是**「某件事发生了多少次」**，不是「多少人说它好」：

| 信号 | 读的是 | 含义 |
|---|---|---|
| `challenge_counts` | `relation:challenged_by` | 某假设被反复质询的次数 |
| `revision_counts` | `revision` | 某关系被反复修正的次数 |
| `dispute_counts` | `relation:contradicts` | 某类争议反复出现的次数 |

**禁止的**：投票数 / 热度 / 参与量 / 曝光量 —— 一个都不在白名单里。

代码里**只写白名单，不写禁词**：`READABLE_TABLES` 列的是"我能读什么"，不是"我不许读什么"。把禁词列出来等于自己撞上 B2。

## `system validation` 不是模型判断，是结构量的**合取**

设计稿里说候选可以由「human / **system validation**」触发，却没说后者是什么。
若它是「模型觉得可以」，那上面那张白名单当场就废了 —— 模型读的是相似度，不是结构量。

所以它被落成**一个声明式的规则集**：每条规则是若干**已有结构量**的合取。

```python
RULES = {
    # A 档也能表达：一个量 ≥ 下限
    "repeatedly_contested": {
        "all_of": (("challenge_counts", ">=", FLOOR),),
        "why": "同一条命题被反复质询",
    },
    # ★ B 档多出来的就是这个：**合取**
    "contested_and_revised": {
        "all_of": (("challenge_counts", ">=", FLOOR),
                   ("revision_counts",  ">=", FLOOR)),
        "why": "既被反复质询、又被反复修正",
    },
}
```

同一份信号上跑两条规则，**两张清单不一样** —— 这就是 B 档多出来的东西。
`test_rules.py` 里有一条用例把它钉住：合取没有比单条件更窄，就说明它什么都没多说。

**分界线是可执行的一条**：

```
合取    →  可解释（哪一条不满足，指得出来）
加权和  →  不可解释，而且必然引入一个标量 → 撞 B5
```

所以 `all_of` 是**元组**、比较符是封闭集合、判定函数里**不许出现算术**。
`checks.py` 的 **B22** 守这条 —— 顺带把「规则条目多带一个字段（信心、来源、模型输出）」
也拦掉：那种字段加上去之后，规则**照样长得像一条结构规则**。

**它免确认，理由是结构而不是纪律**：`evaluate()` 的签名里**没有连接对象**。

```python
def evaluate(signals: dict, *, rules: dict | None = None) -> dict:   # 没有 conn
```

`rules.py` 连 `sqlite3` 都没 import。所以「判定不写库」不是作者记得别写 ——
它是**没有能力写**。行为那半在 `test_rules.py`：跑一遍前后**整库逐行比对**。

`rule` 只能传**规则名**（字符串），必须命中 `RULES`，否则 `RuleError` ——
于是「把模型判断当 promote 条件」这件事**传不进来**，不是「我们约定不传」。

## 七种贡献粒度，入口不许要求你懂图结构

一个人往讨论里能放的东西有七种。它们落成的形状**不一样** —— 有的建节点、有的只建边、
有的只加版本：

| 粒度 | 落成什么 | 落点 |
|---|---|---|
| `claim` | 一条 `Claim` | `proposed`，**等确认** |
| `evidence` | `Evidence` + `supports` / `contradicts` / `qualifies` 边 | `active` |
| `challenge` | `Counterargument` + `challenged_by` 边 | `active` |
| `counterexample` | `Counterexample` + `contradicts` 边 | `active` |
| `revision` | 版本表加一行，**不覆盖** | 状态不动 |
| `connection` | `related_to` 边，**不新建节点** | `active` |
| `context` | 一个新 `Topic` | `proposed`，**等确认** |

调用方看到的是这个：

```python
contribute.claim(conn, text="A 导致 B", by="alice")
contribute.evidence(conn, text="2019 年那项追踪研究", target="claim-0001", by="alice")
contribute.challenge(conn, text="那个样本只覆盖一线城市", target="claim-0001", by="alice")
contribute.connection(conn, left="claim-0001", right="claim-0002", by="alice")
```

**没有一个参数是词表** —— 没有 `kind=`、没有 `type_=`、没有 `state=`。

这不是「约定别传」，是**签名上没得传**：七种粒度到
(节点类型, 关系种类, 方向, 落点) 的映射**只出现在 `CONTRIBUTIONS` 这一张表里**。
静态那半是 **B23**，行为那半是 `test_contribute.py`（31 条）。

`connection` 是七种里唯一不新建节点的 —— 它落成 `relation` 表里的一行。
那行有独立 `id`、有 `origin`、有 `state`，可以被推翻（`reject_relation` 改状态，
**不删行**）。按 AIF，连接是**有身份、可被质疑**的东西，不是两个信息节点之间的一条直连边。

### 为什么 `context` 不是上层那个 `Context`

七种里的 `context` 与上层归纳建的 `Context` 节点**不是同一个东西**：

| | 上层 `Context` | `contribute.context` |
|---|---|---|
| 谁建 | 只许 `upper.py`（`CONTROL_RULES` 的 `structural` 档） | 社区成员 |
| 建的是什么 | 一批**已确认**节点的归组，名字留空 | 一个新**议题**（`Topic`） |
| 免不免确认 | 免 —— 名字留空，系统一句话都没说 | **不免** —— 新议题是一条新断言 |

所以 `CONTROL_RULES` 一个字没改，「上层节点不许被底层当命题用」那条边界也没动。
`test_contribute.py` 里有一条用例跑完全部七种，断言库里**没有** `Context` 节点、
也**没有** `clustered_into` 边 —— 这是单向性在贡献层的落点。
B23 的 import 白名单（只有 `__future__` / `sqlite3` / `scaffold`，**不许 import `upper`**）
是同一件事的静态那半。

### 分档钉成双向的

`§C2.5` 的四档表落到这一层是：**新建可被独立引用的对象必须确认，标注与关系默认生效。**
B23 把它写成一条**双向**判据：

```
落点 == "proposed"   ⟺   类型在 §C2.5 第 2 档点名的名单里
```

双向是必须的，因为要拦的动作正好是**把该确认的改成默认生效**：
把 `claim` 的落点从 `proposed` 改成 `active` 之后，库里看不出任何异常 ——
节点照样有 id、照样能引用，只是「未确认的东西不许算数」在贡献入口上没了。
只判单向的话，这一改一次都不会响。

## 机器提的边进得了库，进不了读数

阶段 5b。AI 可以提议「这两条之间有关系」，但**提议不等于算数**：

```python
r = candidates.record(conn, kind="related_to",
                      left="claim-0001", right="claim-0002",
                      origin="ai:gpt")        # → state='proposed'

candidates.promote(conn, relation_id=r["relation"], by="alice")   # 人点头
candidates.promote(conn, relation_id=r["relation"],
                   rule="repeatedly_contested",
                   signals=upper.count_signals(conn))            # 或规则推
```

`record()` 的签名里**没有 `state`** —— 不是省事，是这个入口的全部意义。
出口判据「AI 产出只能是 candidate」的可执行形式就是
**调用方没有地方填 `active`**（手法同 B14「没有 `conn` 就没法写库」）。

`promote()` 收两条通路，**恰好一条**：两条同时给，事后就查不出这条边是人点头的
还是规则推的（而改判率正是按这个算的）；一条都不给，那是**系统自己提拔自己**。
`rule` 只能是一个**字符串**且必须命中 `RULES` —— 传函数、传模型输出、
传一个没声明过的名字，全部拒绝。规则成立不了也**拒绝，不兜住**：
兜住之后这条边和正常提拔的一模一样。

**候选边不算数这件事有两处保证**，缺一不可：

| | 靠什么 |
|---|---|
| 结构上 | 入口写不出 `active`（签名里没有 `state`） |
| 读数上 | `COUNTED_RELATION_STATES == ("active",)` —— 算数的状态只有这一个 |

`test_candidates.py` 里有一条用例**逐字比对**建候选边前后的 `count_signals()` 输出。

### 这一层原先落不了地，原因在 schema 里

设计稿的原语表写着 `Candidate = 同 Node，state=proposed`，但 `relation` 表的状态
词表原先只有 `active / rejected / superseded` —— **没有 `proposed`**。
于是「一条边在被确认之前」这种状态**根本表达不出来**：

```
ARTIFACT_STATES  = ("proposed", "active", "superseded")              节点有
RELATION_STATES  = ("proposed", "active", "rejected", "superseded")  边补上了
```

节点有、边没有，这个不对称是历史遗留而不是设计。B24 把
「`CANDIDATE_STATE` 真的在 `RELATION_STATES` 里」钉成判据 ——
少了那一条，每一次 `record()` 都会在**运行时**抛。

### 本模块不发明「提议」

`upper.propose_clusters()` 是一个只读的结构提议函数，候选层**没有对应的那一个**。
提议一条**边**需要一条启发式（「两条 claim 引用了同一个来源，所以也许
`related_to`」）—— 那是**发明判据**，而判据形态已经定死在「已有结构量的合取」上。

所以这一层只提供**接收**：谁提的由调用方说，算不算数由人 / 规则定。
「AI 建议候选」那一步在应用层 —— 它调模型，然后把结果送进来。

## 早期阶段它是**休眠**的

这是一个**可证伪的预言**，不是免责声明：

> 在数据量还小的时候，上面那三个信号**全是 0**，候选**一条也出不来**。

所以「跑出 0 条」不是失败，**是文档说的那个结果**。`test_upper.py` 里有一条用例把它钉住 —— 哪天它开始吐候选，要么是真的攒够信号了，要么是判据被放宽了，**两种都该被人看见**。

而且**空结果必须附一句话**：`upper.scan()` 返回 `empty_reason` + `blind_spots` —— **算不出 ≠ 零**。只回一个 `[]` 的话，它和「底层真的没有信号」长得一模一样。

## 怎么验

**零第三方依赖，不需要 `pip install`。** 只要 Python 3（3.13 与 3.14 实测）。

```bash
git clone https://github.com/aic-123/nested-traceable-discussion-graph.git
cd nested-traceable-discussion-graph

python checks.py                      # 24 条否证检查
python -m unittest test_upper         # 上层行为：单向性 / 命名归人 / 视图边界
python -m unittest test_pointer       # 定位符：只存位置，不存正文
python -m unittest test_staging       # 入层门：不过门就进不了 active
python -m unittest test_provenance    # 来源与归因：AI 产出不许伪装成人
python -m unittest test_rules         # 规则集：判定一个字都没写库 / 合取不是析取
python -m unittest test_contribute    # 七种贡献粒度逐个可建 / 空库也能建第一条
python -m unittest test_candidates    # 候选边不进读数 / 只有两条通路能把它变算数
python -m unittest test_checks        # 证明那 24 条检查不是空转
```

`checks.py` 会逐条打印结果。全过时输出：

```
[B14] 上层 → 底层不许写成事实（单向性）    §C7.1 ④      过
[B15] 上层节点不带系统生成的名字（命名归人）  §C2.0 §C7.1 ③  过
...
否证检查全部通过：共 24 条，B1, B10, ... 无命中。
```

> ⚠️ **`test_checks` 会跳过 7 条**，输出 `OK (skipped=7)`。这是**有意**的：
>
> 这 7 条验的是上游产品（arena）的 `vote.py` / `concurrency.py` / `confirm.py` / `samples/`，
> 那几个文件**不在这个仓库里**。跳过的原因会逐条印出来，例如
> `本仓库不含上游文件：vote.py（arena 的投票模块 —— 双侧框架只派生、不读热度信号，不需要它）`。
>
> **跳过而不是删掉，是因为「跳过」和「通过」是两件事。**
>
> 删掉之后 `test_checks` 会报「全过」，但 B4 / B8 / B9 / B12 / B13 这五条检查
> 在这个仓库里**没被验伪过**。跳过（并把原因逐条印出来）就是这份报告
> **如实说出自己验到哪**的方式 —— 一条检查是「过」还是「这里测不了」，报告里分得开。

> **两条「报告卫生」检查 —— 一个假绿、一个假红，各堵一个洞。**
>
> `test_checks` 会往仓库里写临时探针、跑完在 `finally` 里删掉。
> 于是有一种状态：**探针留下了，但这一轮的检查全过。**
>
> 这时候该报什么？报「通过」会让残留污染下一轮；报「失败」则是拿
> 上一轮的事故判这一轮不合格。这个仓库的做法是 **分开归因，并且都要说出来**：
>
> | 用例 | 管什么 | 有残留时 |
> |---|---|---|
> | `Test00NoResidueAtStart` | **上一轮**留下的 | 清掉 + `skipTest`，说明里带绝对路径 |
> | `TestNoResidue` | **这一轮**留下的 | **判红** —— 收尾代码全跑过还有残留，只能是清理漏了路径 |
>
> 判据不是「严不严」，是**「谁的事故」**。
>
> ```
> 跑前残留：['_tmp_probe_zzz.py']
> 本次跑：  OK (skipped=8)      ← 自愈；说明里带绝对路径，**可见，但不判死**
> 跑后残留：[]
> 再跑一次：OK (skipped=7)
> ```
>
> 唯一能制造残留的是 `SIGKILL`（`Ctrl-C` / 进程被杀 / 机器休眠）——
> `finally` 只在进程能跑到收尾时执行。**重复跑、重定向输出都无害。**
> 所以你不需要手工 `rm`，也不会因为这条红而怀疑检查坏了。
>
> 反向验收判据（改这条检查时必须一起验）：**直跑 `TestNoResidue` 且预置残留
> 仍必须 `FAIL`** —— 让「上一轮的事故」自愈，不能顺手把「这一轮的漏清」也改软。

## 几行看懂单向性

```python
import scaffold, upper

conn = scaffold.connect(":memory:")
scaffold.init(conn)

# ... 造一些底层节点和质询边（见 test_upper.py 的 helper）...

# 提议是**只读**的 —— 一个字都不许写
out = upper.propose_clusters(conn)          # → {"candidates": [...], ...}

# 建节点：名字留空，等一个真人来起
made = upper.promote_candidates(
    conn, candidates=out["candidates"], by="alice", debate_id=debate)

# 改的只有名字；依据 / 信号 / 版本原样带走（读-改-写，见 §22）
upper.rename_context(conn, context_id=made[0], name="围绕假设甲的分歧", by="bob")
```

## 上游是什么

这个结构是从一个叫 **Arena** 的多人结构化讨论系统里抽出来的。在那边，下面还有切分、逐条确认、讨论组织、投票、并发装置好几层。

这个仓库**只抽上层归纳那一层**，因为它是那个系统里唯一一个「**AI 只组织、不判断**」这件事最容易被写坏的部位 —— 上层一旦开始往回写，整个系统的立场就变了。

上游仓库：[`aic-123/arena`](https://github.com/aic-123/arena)（AI 是 Secretary / Organizer / Detector，**不是 Judge**）

## 许可

见 [`LICENSE`](LICENSE)。
