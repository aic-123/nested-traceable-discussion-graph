# nested-traceable-discussion-graph

**A nested, traceable discussion graph — the lower layer records facts, the upper layer only derives, never flows back.**
**一个可嵌套、可追溯的讨论图：底层记事实，上层只派生、不倒流。**

[中文](README.md) · [What it is](#what-it-is) · [The two invariants](#the-two-invariants) · [How to verify](#how-to-verify) · [Full argument `DECLARATION.md`](DECLARATION.md)

> The `§C7.1` / `§C9 #5` style markers in the code point at clauses in [`DECLARATION.md`](DECLARATION.md) — **every one of them resolves**.
> That document is what this structure was built against; it ships in this repo so you don't have to go hunting.

## What it is

In one sentence: **split a discussion into two layers — the lower layer only records "who said what, who challenged what"; the upper layer only groups things that have been challenged repeatedly — and nothing the upper layer does may change a single character of the lower layer.**

This repo extracts **the upper induction layer** (plus the read/write primitives it needs):

| File | What it is |
|---|---|
| `scaffold.py` | Read/write primitives: nodes, edges, revisions, events. **Zero third-party dependencies** (only `json` / `sqlite3` / `datetime`) |
| `pointer.py` | Reference locators `(uri, selector)` — copied from **W3C Web Annotation**; stores **position, never the body text** |
| `upper.py` | The upper induction layer itself — count signals → propose → promote → name → view |
| `rules.py` | The structural rule set — what `system validation` actually means. Criteria are **conjunctions of existing structural counts**, and the module **has no ability to write** |
| `staging.py` | The distillation **intake gate** — imported and community layers take different doors; an **explicit mapping table** for DeepRead's eight relations |
| `contribute.py` | Entry points for the **seven contribution granularities** — the vocabulary lives in one table, and no entry signature takes a vocabulary parameter |
| `policy.py` | Changeable operational thresholds (not signals; they never rank anything) |
| `checks.py` | **23 falsification checks. Each one is executable**, not an adjective in a doc |
| `test_upper.py` / `test_pointer.py` / `test_staging.py` / `test_provenance.py` / `test_rules.py` / `test_contribute.py` | Behavioural verification — depends only on this repo's modules and the stdlib |
| `test_checks.py` | Proves those 23 checks **aren't vacuous** |
| `DECLARATION.md` | The full argument. `§7.1` is the two-layer section, `§22` is the implementation record |

## What it is NOT

Starting with what it doesn't do — that says more about this thing than "what it does":

- **Not a clustering engine.** It does **not** do k-means, spectral clustering, or embedding similarity — reasoning below.
- **Not a ranking system.** No ranking axis, no thresholds, no scores. These aren't "not built yet", they are **forbidden** (`§C7.1` `§C9` #5 #7).
- **Not a voting tool.** Vote counts, popularity, impressions are **not evidence** (invariant #5). The upper layer **does not read** those signals.
- **Not a complete product.** It's a structure that runs, not a service — no frontend, no accounts, no deployment.
- **No auto-naming.** Nodes are created automatically, **but the name must come from a human** — the system says nothing on your behalf.

## The two invariants

The entire value of this repo is in these two. They are **executable**, not declarations:

### Invariant 1: One-way — the upper layer only derives, never flows back

```
lower ──derive──▶ upper      allowed
upper ──write as fact──▶ lower   forbidden
```

The upper layer may influence **exactly three things**: display order / default expansion / recommended candidates. It **may not** write into any lower-layer field.

- **Static guard**: **B14** in `checks.py` — any `UPDATE artifact` / `INSERT INTO revision` / `DELETE FROM relation` appearing in `upper.py` fires.
- **Behavioural guard**: `test_upper.py` catches the **detour** — indirect writes through `scaffold.revise()` that B14's regex can't see.

You need both. Measured: `promote` adds exactly 1 `ctx-*` node + 3 `clustered_into` edges, with **0 lower-layer modifications and 0 deletions**.

### Invariant 2: Naming belongs to humans

Nodes are created automatically, **the name comes from a person**. This isn't a style choice — it's the **entire reason** this mechanism can skip the confirmation flow:

> With the name left empty, the system has **said nothing at all**. There's nobody for "who confirms this?" to point at — so no confirmation is needed.

Which yields something easy to get wrong: **`PENDING_NAME` is not a placeholder, it's the design premise.**

- **B15 guards it**: a `Context` node's `text` may not contain any string literal other than `PENDING_NAME`.
- So `state` tracks "has this claim been confirmed", while "not yet named" is a **different kind of pending**, expressed by `name_source` + `PENDING_NAME`. **Compressing them into one field means asserting "unnamed = unconfirmed"** — which incidentally erases the reason the confirmation exemption existed.

## Why clustering was rejected

This is the most interesting judgement in the repo, because **the reason is not "clustering is bad" — it's "the signal was wrong"**:

Clustering (k-means or spectral, doesn't matter) is driven by **text similarity**. That's a **distance metric**, and it isn't on the signal whitelist.

> **Swap the algorithm (k-means → spectral): the signal is still "the text looks alike" — still in violation.**
> **Swap the signal (similarity → challenge count): not a line of the algorithm changes, and it's compliant.**

So: **the class of signal is more fundamental than the choice of algorithm.**

There's a second, harder reason: **a cluster centroid is a new assertion.** The sentence "this cluster is called X" is something the system cannot say — on what grounds would it claim these propositions are "the same thing"? That's a human judgement.

## Allowed signals (the whitelist)

Only three, all of the form **"how many times did something happen"**, never "how many people liked it":

| Signal | Reads from | Meaning |
|---|---|---|
| `challenge_counts` | `relation:challenged_by` | how often a hypothesis was challenged |
| `revision_counts` | `revision` | how often a relation was revised |
| `dispute_counts` | `relation:contradicts` | how often a class of dispute recurred |

**Forbidden**: vote counts / popularity / participation / impressions — none of them are on the whitelist.

The code **writes only the whitelist, never the forbidden words**: `READABLE_TABLES` states "what I may read", not "what I may not". Listing the forbidden words means tripping over B2 yourself.

## `system validation` is not a model judgement — it's a **conjunction** of structural counts

The design document says a candidate may be triggered by "human / **system validation**" and never says what the latter is.
If it were "the model thinks it's fine", the whitelist above would be void on the spot — a model reads similarity, not structural counts.

So it is landed as **a declarative rule set**: every rule is a conjunction of **already-existing structural counts**.

```python
RULES = {
    # expressible at tier A: one count >= a floor
    "repeatedly_contested": {
        "all_of": (("challenge_counts", ">=", FLOOR),),
        "why": "the same proposition was challenged repeatedly",
    },
    # ★ this is what tier B adds: a **conjunction**
    "contested_and_revised": {
        "all_of": (("challenge_counts", ">=", FLOOR),
                   ("revision_counts",  ">=", FLOOR)),
        "why": "repeatedly challenged AND repeatedly revised",
    },
}
```

Run both rules over the same signals and **the two lists differ** — that's what tier B buys.
`test_rules.py` pins it: if the conjunction is no narrower than a single count, it said nothing new.

**The dividing line is one executable sentence:**

```
conjunction  →  explainable (point at the condition that failed)
weighted sum →  not explainable, and it necessarily introduces a scalar → trips B5
```

So `all_of` is a **tuple**, the comparison operators are a closed set, and the judging functions may **contain no arithmetic**.
**B22** in `checks.py` guards this — and along the way it also blocks a rule entry **carrying an extra field** (confidence, source, model output): add one and the rule **still looks exactly like a structural rule**.

**It is exempt from confirmation for a structural reason, not by discipline**: `evaluate()`'s signature has **no connection object**.

```python
def evaluate(signals: dict, *, rules: dict | None = None) -> dict:   # no conn
```

`rules.py` doesn't even import `sqlite3`. So "the judgement does not write to the store" isn't the author remembering not to — it **has no ability to**. The behavioural half lives in `test_rules.py`: it compares **every row of every table** before and after.

`rule` accepts only a **rule name** (a string) that must hit `RULES`, otherwise `RuleError` — so "use the model's judgement as a promote condition" **cannot be passed in**, rather than "we agreed not to".

## Seven contribution granularities, and an entry layer that never asks you about graph structure

There are seven things a person can put into a discussion. They do **not** land in the same shape — some create a node, some only an edge, one only adds a version:

| Granularity | What it becomes | Where it lands |
|---|---|---|
| `claim` | a `Claim` | `proposed`, **awaits confirmation** |
| `evidence` | `Evidence` + a `supports` / `contradicts` / `qualifies` edge | `active` |
| `challenge` | `Counterargument` + a `challenged_by` edge | `active` |
| `counterexample` | `Counterexample` + a `contradicts` edge | `active` |
| `revision` | one row in the version table, **never an overwrite** | state unchanged |
| `connection` | a `related_to` edge, **no new node** | `active` |
| `context` | a new `Topic` | `proposed`, **awaits confirmation** |

Here is what the caller sees:

```python
contribute.claim(conn, text="A causes B", by="alice")
contribute.evidence(conn, text="the 2019 cohort study", target="claim-0001", by="alice")
contribute.challenge(conn, text="that sample only covers tier-one cities", target="claim-0001", by="alice")
contribute.connection(conn, left="claim-0001", right="claim-0002", by="alice")
```

**Not one parameter is vocabulary** — no `kind=`, no `type_=`, no `state=`.

That is not "we agreed not to pass it"; there is **nothing to pass**. The mapping from the seven granularities to (node type, relation kind, direction, landing) lives in **one table**, `CONTRIBUTIONS`. The static half of that is **B23**; the behavioural half is `test_contribute.py` (31 tests).

`connection` is the only one that creates no node — it lands as a row in the `relation` table. That row has its own `id`, its own `origin`, its own `state`, and it can be overturned (`reject_relation` flips the state, **it does not delete the row**). Per AIF a connection is something **with identity, open to challenge**, not a direct edge between two information nodes.

### Why `context` is not the upper-layer `Context`

The `context` granularity and the `Context` node the upper layer induces are **not the same thing**:

| | Upper-layer `Context` | `contribute.context` |
|---|---|---|
| Who creates it | only `upper.py` (the `structural` tier of `CONTROL_RULES`) | a community member |
| What it is | a grouping of **already-confirmed** nodes, name left blank | a new **topic** (`Topic`) |
| Confirmation-exempt | yes — the name is blank, so the system said nothing | **no** — a new topic is a new assertion |

So `CONTROL_RULES` is untouched, and so is the boundary "an upper-layer node may not be mistaken for a lower-layer proposition". One test in `test_contribute.py` runs all seven and asserts the store contains **no** `Context` node and **no** `clustered_into` edge — that is the one-way constraint landing at the contribution layer. B23's import whitelist (`__future__` / `sqlite3` / `scaffold` only, **`upper` forbidden**) is the static half of the same thing.

### The tier is pinned in both directions

`§C2.5`'s four-tier table reduces, at this layer, to: **newly created independently-referenceable objects must be confirmed; annotations and relations take effect by default.** B23 writes that as a **two-way** criterion:

```
landing == "proposed"   ⟺   the type is one of the §C2.5 tier-2 names
```

Two-way is necessary because the action to block is precisely **turning a must-confirm into a takes-effect-by-default**: after changing `claim`'s landing from `proposed` to `active`, nothing in the store looks wrong — the node still has an id, is still referenceable, and only "unconfirmed things do not count" has quietly gone from the entry layer. A one-way check never fires on that change.

## On a young corpus it is **dormant**

This is a **falsifiable prediction**, not a disclaimer:

> While the data volume is still small, all three signals above are **exactly 0**, and **no candidates come out at all**.

So "it produced 0" is not a failure — **it's the result the document predicts**. `test_upper.py` pins this with a test: the day it starts emitting candidates, either it genuinely accumulated enough signal, or the criterion was loosened — **both should be seen by a human**.

And **an empty result must carry a sentence**: `upper.scan()` returns `empty_reason` + `blind_spots` — **"can't compute" ≠ "zero"**. Returning a bare `[]` looks exactly like "the lower layer genuinely has no signal".

## How to verify

**Zero third-party dependencies, no `pip install`.** Just Python 3 (tested on 3.13 and 3.14).

```bash
git clone https://github.com/aic-123/nested-traceable-discussion-graph.git
cd nested-traceable-discussion-graph

python checks.py                      # the 23 falsification checks
python -m unittest test_upper         # upper layer: one-way / naming / view boundary
python -m unittest test_pointer       # locators: position only, never the body
python -m unittest test_staging       # intake gate: no gate, no `active`
python -m unittest test_provenance    # provenance: AI output may not masquerade as human
python -m unittest test_rules         # rule set: nothing written / conjunction ≠ disjunction
python -m unittest test_contribute    # seven granularities, each creatable / empty store too
python -m unittest test_checks        # proves those 23 checks aren't vacuous
```

`checks.py` prints each result. When everything passes:

```
[B14] 上层 → 底层不许写成事实（单向性）    §C7.1 ④      过
[B15] 上层节点不带系统生成的名字（命名归人）  §C2.0 §C7.1 ③  过
...
否证检查全部通过：共 23 条，B1, B10, ... 无命中。
```

> ⚠️ **`test_checks` skips 7 tests** — output is `OK (skipped=7)`. This is **intentional**:
>
> Those 7 verify the upstream product's (arena's) `vote.py` / `concurrency.py` / `confirm.py` / `samples/`,
> and **none of those files are in this repo**. Each skip prints its own reason, e.g.
> `本仓库不含上游文件：vote.py（arena 的投票模块 —— 双侧框架只派生、不读热度信号，不需要它）`.
>
> **Why not just delete them**: deleting would make `test_checks` report "all pass",
> while B4 / B8 / B9 / B12 / B13 would have **never been falsified** in this repo —
> and "a vacuous check also prints 过" is exactly the disease recorded in the table at the top of that file.
> Skipping (rather than faking a pass) is how this report **states honestly how far it actually verified**.

> **Two "report hygiene" checks — one plugs a false green, the other a false red.**
>
> `test_checks` writes temporary probes into the repo and removes them in a `finally` block.
> So there is a state where **a probe is left behind but every check in this run passed.**
>
> What should be reported then? Reporting "pass" lets the residue contaminate the next run;
> reporting "fail" judges this run by the previous run's accident. This repo's answer is
> **attribute separately, and say either one out loud**:
>
> | Test | Covers | On residue |
> |---|---|---|
> | `Test00NoResidueAtStart` | left by the **previous** run | clean up + `skipTest`, message carries the absolute path |
> | `TestNoResidue` | left by **this** run | **fail** — all cleanup ran, so residue means a path was missed |
>
> The criterion is not "how strict" — it is **whose accident it is**.
>
> ```
> residue before run: ['_tmp_probe_zzz.py']
> this run:           OK (skipped=8)   <- self-healed; absolute path in the message — visible, but not fatal
> residue after run:  []
> run again:          OK (skipped=7)
> ```
>
> The only thing that can leave residue is `SIGKILL` (`Ctrl-C` / a killed process /
> the machine sleeping) — a `finally` block only runs if the process reaches its cleanup.
> **Re-running and redirecting output are both harmless.** So you never need to `rm` by hand,
> and you will not mistake this for a broken check.
>
> Reverse acceptance criterion (verify this whenever you touch these checks):
> **running `TestNoResidue` directly with residue planted must still `FAIL`** —
> letting the previous run's accident self-heal must not soften this run's missed cleanup.

## One-way, in a few lines

```python
import scaffold, upper

conn = scaffold.connect(":memory:")
scaffold.init(conn)

# ... build some lower-layer nodes and challenge edges (see test_upper.py helpers) ...

# Proposing is **read-only** — not a character may be written
out = upper.propose_clusters(conn)          # → {"candidates": [...], ...}

# Promote: the name is left empty, awaiting a human
made = upper.promote_candidates(
    conn, candidates=out["candidates"], by="alice", debate_id=debate)

# Only the name changes; evidence / signal / version ride along (read-modify-write, see §22)
upper.rename_context(conn, context_id=made[0], name="围绕假设甲的分歧", by="bob")
```

## What upstream is

This structure was extracted from a multi-party structured discussion system called **Arena**. There, several more layers sit underneath: segmentation, per-proposition confirmation, discussion organisation, voting, a concurrency harness.

This repo **extracts only the upper induction layer**, because that's the one spot in that system where "**the AI organises but does not judge**" is easiest to break — once the upper layer starts writing back, the whole system's position changes.

Upstream: [`aic-123/arena`](https://github.com/aic-123/arena) (AI is Secretary / Organizer / Detector, **not Judge**)

## License

See [`LICENSE`](LICENSE).
