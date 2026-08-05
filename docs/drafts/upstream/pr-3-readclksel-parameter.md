# PR 3 — luna: make `DQSBUFM`'s `READCLKSEL` a `HyperRAMDQSPHY` parameter

**Repo:** `greatscottgadgets/luna` · **Branch:**
`awtoau/awto-luna:dqs-readclksel-parameter` · **Base:** upstream `main` ·
**Diff:** 1 file, +17 −5 · not blocking

---

Closes a TODO upstream wrote itself. `psram.py` has:

```python
# TODO: may need to tune at runtime by trying different values & checking for BURSTDET high
i_READCLKSEL0=0,
i_READCLKSEL1=1,
i_READCLKSEL2=0,
```

`BURSTDET` is already brought out on `phy.burstdet`, so the *checking* half
exists. Only the *trying* half was missing.

`readclksel` becomes a constructor argument defaulting to `0b010` — exactly what
the three constants encoded, so nothing changes for an existing caller. It
accepts an `int` or a 3-bit `Signal`, so a design can drive it from a register
file and walk all eight taps without a rebuild per tap. That is the difference
between a five-second sweep and eight place-and-route runs, and it is how the
DQS measurements in the port PR were taken.

Assigning through a `Signal(3)` rather than slicing `self.readclksel` is what
makes an `int` and a `Signal` accepted the same way; it also truncates an
out-of-range `int` at elaboration rather than producing a silently wrong tap.

## Ordering

Independent of the Amaranth 0.5 port — different region of the file, no textual
conflict, and it can land in either order. It is only *exercisable* once the
port lands, since until then nothing can construct the PHY at all.

## Related

* greatscottgadgets/cynthion#147
