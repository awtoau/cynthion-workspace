# PR 4 — luna: implement `HyperRAMDQSInterface`'s `RECOVERY` state

**Repo:** `greatscottgadgets/luna` · **Branch:**
`awtoau/awto-luna:dqs-recovery-tcshi` · **Base:** upstream `main` ·
**Diff:** 1 file, +31 −4 · not blocking

---

```python
with m.State('RECOVERY'):
    m.d.sync += self.phy.clk_en .eq(0)

    # TODO: implement recovery
    m.next = 'IDLE'
```

This is worse than a missing delay. The FSM's defaults reassert `phy.cs` every
cycle, and `IDLE` only deasserts it in the branch where `start_transfer` is
*low*. A caller that keeps `start_transfer` high — which is what a streaming
engine or a queued memory window does — therefore **never lets CS# go high
between transactions at all**.

Simulated with `start_transfer` held and single-word transactions, 120 cycles:

| | transactions | CS# deasserted runs |
|---|---|---|
| upstream | **0** | `[]` — one continuous CS assertion |
| this branch | 9 | `[2, 2, 2, 2, 2, 2, 2, 2, 2]` |

The part wants **tCSHI, 10 ns of CS# high between transactions**. A violation is
not a failure; it occasionally returns the wrong word — the kind of fault that
gets attributed to the read path for a long time before anyone looks at CS.

## The number

`RECOVERY_CLOCKS = 1`, and CS# ends up deasserted for **two** `sync` cycles: the
one this state holds plus the one `IDLE` spends deciding. At this PHY's 4:1
gearing a `sync` cycle is two CK, so two of them cover 10 ns for any `sync` up
to 100 MHz — CK 200 MHz, past where this interface has been run. A class
attribute, so a faster fabric can raise it without a fork.

It is a floor, not a delay for its own sake: being one cycle generous costs one
cycle, being one cycle short costs a wrong word that nothing reports.

## Scope

Deliberately **not** applied to `HyperRAMInterface`, which carries the identical
TODO. That is the non-DQS path Cynthion ships on today, and adding a cycle to
every transaction there is a change that deserves its own measurement rather
than a drive-by.

## Related

* greatscottgadgets/cynthion#147
