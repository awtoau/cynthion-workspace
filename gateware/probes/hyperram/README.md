# HyperRAM gateware tops

Eleven standalone designs, each one half of a harness. The other half — the
runner that builds it, loads it and reads its result registers back — is under
`scripts/` with the **same filename**.

    ./dev.py hyperram              what each harness measures, and whether it has ever run
    ./dev.py hyperram <name>       run one

Nothing here is a duplicate of anything in `scripts/`, and three pairs were read
as duplicates in [#189](https://github.com/awtoau/cynthion-workspace/issues/189)
because of the shared names. Build state and silicon evidence for every top are
in [`../../README.md`](../../README.md); `./dev.py hyperram` is the shorter answer.

**None of these exercise the coalescing write path in
[#185](https://github.com/awtoau/cynthion-workspace/issues/185).** Each drives
`HyperRAMInterface` from its own FSM, which supplies a word per cycle, so none of
them can express the fault. The master that bubbles is `HyperRAMWishbone` in
`../riscv/vexii_bootram.py`; the only harness on that path is
`scripts/hyperram_measure.py`, which drives the shipping SoC and has no gateware
here, and the only regression cover is `scripts/soc_hyperram_sim.py`. That is why
the burst-write fault survived a directory of eleven HyperRAM test programs.
