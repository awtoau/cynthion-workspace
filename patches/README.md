# Patches against tools this repo does not own

Each patch here is a change to an upstream tool that a build needs, kept as a
tracked file because the built binary lives in `tmp/` and `tmp/` is disposable.
**The patch is the durable part; the binary is not.**

Where a patch belongs upstream, the issue that tracks that is named below.

## yosys-opt-clean-const-lookups.patch

- `opt_clean` data race: parallel workers read two `dict`s through non-const `operator[]` / `at()`, which rehash.
- Upstream: [YosysHQ/yosys#6418](https://github.com/YosysHQ/yosys/issues/6418). Not posted. Tests, risks: [`docs/yosys-opt-clean-race.md`](../docs/yosys-opt-clean-race.md).
- Applies to yosys `b8d84f823` (0.70+329): `git apply`.

## apollo-jtag-tail-overrun.patch

- Our Apollo fork (`repos/apollo`, branch `fix/jtag-tail-overrun`, commits `703e450` `a476c69` `acfba56` `973717f` `a16c6b8`): no scan or benchmark writes outside the `comms` region (a discard scan's bit-banged tail, a capturing scan's tail byte, the benchmark chunk), with the host tests.
- Not an upstream tool, kept as a file for the same reason: the commits live in a submodule. Root cause and why no test saw it: [`docs/board-usb-recovery.md`](../docs/board-usb-recovery.md).
- Applies on top of `4ccf7e7`: `git -C repos/apollo apply ../../patches/apollo-jtag-tail-overrun.patch`.

## yosys-abc9-unlisted-ports-fix.patch, yosys-abc9-unlisted-ports-test.patch

- `abc9` ties input ports a `abc9_bypass` cell instance leaves unconnected to 0 (`M` and `CE` of `TRELLIS_FF`). Regression from yosys `ae085b637` (PR #6437).
- Fix: `techmap_autopurge` on the `prep_bypass` template module's inputs. Test: `tests/arch/ecp5/trellis_ff_unused_ports.ys`, which fails on yosys 0.70+329 and main and passes on 0.65+57.
- Upstream: not posted. Cause, results, why yosys's tests pass: [`docs/yosys-070-upgrade.md`](../docs/yosys-070-upgrade.md).
- Apply to yosys `8f3cc3d0c` (also `b8d84f823`): `git apply` each.
