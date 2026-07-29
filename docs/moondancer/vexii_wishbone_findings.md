# VexiiRiscv on Wishbone: what building it actually showed

Verified by generating the core, not by reading the source. Two of these
contradict the implementation plan.

## The Wishbone bridges are real

`--fetch-wishbone --lsu-wishbone --lsu-l1-wishbone` produces a core with **two
Wishbone masters, 11 signals each**:

    FetchL1WishbonePlugin_logic_bus_{CYC,STB,ACK,WE,ADR,DAT_MISO,DAT_MOSI,SEL,ERR,CTI,BTE}
    LsuL1WishbonePlugin_logic_bus_{...}

`ADR` is 30 bits with 32-bit data, which is exactly the shape moondancer's
decoder and `luna_soc`'s `VexRiscv` component already use. 33 of 39 top-level
ports are Wishbone; the rest are `clk`, `reset`, `rdtime` and three interrupt
inputs.

So the "41 native ports across three buses" problem is real for the default
configuration and disappears entirely on the Wishbone path.

## Atomics and the cacheless Wishbone bridge are mutually exclusive

`LsuCachelessBridge.scala:203`:

    class LsuCachelessBusToWishbone(up : LsuCachelessBus) extends Area{
      assert(!up.p.withAmo)

Generating with `--with-rva --lsu-wishbone` and no data cache fails with an
elaboration assertion. There is **no equivalent assertion in `LsuL1Bridge.scala`**,
and generating with `--with-rva` plus `--with-lsu-l1 --lsu-l1-wishbone`
succeeds.

**This inverts the plan's milestone order.** The plan proposed starting
cacheless and treating caches as a later optimisation. Moondancer's firmware
targets `riscv32imac` (`moondancer/Cargo.toml:23`), and the A is atomics, so on
the Wishbone path a data cache is not optional:

| configuration | atomics | works |
|---|---|---|
| cacheless + Wishbone | required by firmware | **no** |
| L1 cached + Wishbone | required by firmware | yes |

Either the first milestone carries a data cache, or the firmware drops to
`riscv32imc` and gives up atomics.

## The flags are per-path, not global

`--fetch-wishbone` dispatches at `Param.scala:932` for the cacheless fetch path
and at `Param.scala:972` for the cached one. Passing `--fetch-wishbone
--lsu-wishbone` while caches are enabled silently produces a **native** core:
the flags are accepted, the bridges are never instantiated, and the only
symptom is that the generated Verilog still has `FetchL1Plugin_logic_bus_*`
ports.

The cached path needs `--lsu-l1-wishbone` as a separate flag. There is no
warning when the combination does nothing.

## Area, with the repaired wrapper

The sweep's cached rows were measured through a wrapper that tied the L1
`cmd_ready` low, so those cores could never fetch and were pruned. With that
fixed:

| configuration | LUT | BRAM |
|---|---|---|
| i4k + d4k, as measured before | 967 | — |
| i4k + d4k, wrapper repaired | **5212** | 14 |
| cacheless | 4072 | 8 |

A core with caches is larger than one without, which the earlier figures
inverted by more than 5x.
