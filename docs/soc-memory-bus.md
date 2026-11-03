# SoC memory bus

Wishbone classic (registered feedback) between VexiiRiscv and the subordinates. Facts that bound any bus change.

**Index:** [`upstream-boundary.md`](upstream-boundary.md) · [`riscv-core-build.md`](riscv-core-build.md) · [`soc-interrupts.md`](soc-interrupts.md)

HyperRAM is not on this bus: register interface on the #581 core at CK 180, see [docs/chips/hyperram/soc-peripheral.md](chips/hyperram/soc-peripheral.md) (#588).

## Wishbone B4 pipelined
- `amaranth_soc` supports it: `wishbone/bus.py:32` `Feature.STALL`; `Signature.__init__` adds it (`:127-128`); `_FeatureShim` (`:256-322`) adapts pipelined/classic both ways. `Arbiter` re-evaluates grant only under `~cyc` (`:499`), so it is pipelined-correct. Read from amaranth_soc `0.1a1.dev32+g3e3d8b7`.
- `Decoder` is not: `elaborate` (`:403-442`) selects `ack`, `dat_r`, `err` and `cyc` inside `m.Switch(adr)`, so with beats outstanding responses route by the live address. Safe only while a burst stays in one window.
- VexiiRiscv will not emit it: every `toWishboneConfig()` sets `useSTALL = false` (`fetch/FetchCachelessBus.scala:51`, `fetch/FetchL1Bus.scala:65`, `execute/lsu/LsuCachelessBus.scala:77`, `execute/lsu/LsuL1Bus.scala:439`); SpinalHDL `isPipelined = useSTALL` (`Wishbone.scala:64-66`). The bridges implement the classic handshake in Scala (`LsuL1Bus.scala:367`).
- A pipelined subordinate under a classic master buys nothing. Cost of changing the master: four VexiiRiscv bridges plus an issue-order `Decoder`.

## `RegisteredResponse` (`wishbone_pipe.py`)
- Landed `d440f03`; +420 LUT, +33 FF. Load-bearing; cuts `arbiter.grant` -> address mux -> decoder compare -> ACK gather -> arbiter mux -> PMA check.
- Withholds `stb` after `ack` (`sub.stb.eq(intr.stb & ~answered)`, `:149`) so a side-effecting register is not strobed twice.
- On a classic bus beat N+1 cannot be presented before N's ack, so the added register costs one cycle per beat. A skid buffer cannot hide it: it does not know the next `adr`. A speculative-address shim works for linear-burst reads only; writes need `dat_w` from the master.

## AXI4 / TileLink
- VexiiRiscv flags (`Param.scala:754-759`): `--fetch-axi4`, `--fetch-wishbone`, `--lsu-axi4`, `--lsu-wishbone`, `--lsu-l1-axi4`, `--lsu-l1-wishbone`. TileLink only via `soc/TilelinkVexiiRiscvFiber.scala:104-108`; `toBmb()` has no callers.
- `amaranth_soc` has no AXI4 and no TileLink (only `wishbone` and `csr`): no decoder, arbiter or interconnect to attach the other subordinates to.
- Cache line is fixed at 64 bytes (`Param.scala:936`).
