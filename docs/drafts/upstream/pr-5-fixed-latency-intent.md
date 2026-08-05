# PR 5 — luna: name the fixed-latency choice instead of writing `| 1`

**Repo:** `greatscottgadgets/luna` · **Branch:**
`awtoau/awto-luna:dqs-fixed-latency-intent` · **Base:** upstream `main` ·
**Diff:** 1 file, +21 −6 · not blocking · **no behaviour change**

---

## The judgement asked for: the `| 1` is correct, and the fix is documentation

`with m.If(extra_latency | 1)` in `HyperRAMDQSInterface` makes the else branch
unreachable, and reads like a debugging edit someone forgot to remove. It is
not.

The parts this is used with report `CR0 = 0x8f2f` — fixed latency set. The
device takes the long count on **every** transaction, and RWDS during the
command period is not a statement about that transaction. Honouring it would
sample early and return whatever was on DQ. **Forcing the long branch is the
correct behaviour for this configuration**, and a PR that "fixed" the dead
branch by making it reachable would be introducing a data corruption bug.

So this changes no logic. `| 1` becomes `| self.FIXED_LATENCY`, a class
attribute carrying the reason and — as importantly — the condition under which
it may be cleared: CR0 reprogrammed to variable latency **first**, and then
measured. Two changes, not one. Variable latency saves `LOW_LATENCY_CLOCKS` on
about half of transactions, but a controller that honours RWDS against a part
that does not vary it is not slower, it is wrong.

The FIXME it replaces asked for exactly this:

> our HyperRAM part has a fixed latency, but we could need to detect different
> variants from the configuration register in the future

A subclass setting `FIXED_LATENCY = False` is the shape that takes.

Worth doing because the line has already drawn a bug report as a suspected
performance defect (~30% of fixed overhead); a comment-only change would leave
it reading as a bug forever, and three lines make the FIXME actionable instead.

## Scope

Left alone in `HyperRAMInterface`, which has the same line. That is the path
Cynthion ships on, and it deserves its own change.

## Related

* greatscottgadgets/cynthion#147
