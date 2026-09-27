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
| `upper.py` | The upper induction layer itself — count signals → propose → promote → name → view |
| `checks.py` | 15 falsification checks. **Each one is executable**, not an adjective in a doc |
| `test_upper.py` | Behavioural verification of the upper layer — depends only on `scaffold.py` and the stdlib |
| `test_checks.py` | Proves those 15 checks **aren't vacuous** |
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

python checks.py                      # the 15 falsification checks
python -m unittest test_upper         # upper layer: one-way / naming / view boundary
python -m unittest test_checks        # proves those 15 checks aren't vacuous
```

`checks.py` prints each result. When everything passes:

```
[B14] 上层 → 底层不许写成事实（单向性）    §C7.1 ④      过
[B15] 上层节点不带系统生成的名字（命名归人）  §C2.0 §C7.1 ③  过
...
否证检查全部通过：共 15 条，B1, B10, ... 无命中。
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
