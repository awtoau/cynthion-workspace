# luna_soc dependency: what needs it, what does not

Established 2026-07-30 with `amaranth_soc_dropin_test.py` (now in `debris/scripts/`; throwaway venv, system env untouched). Decision and fork pin: [`upstream-boundary.md`](upstream-boundary.md#luna-soc-fork-pin). Plan steps and investigation log: [`history/upstream-toolchain.md`](history/upstream-toolchain.md).

## What needs `luna_soc`

| question | answer |
|---|---|
| Is it a hard dependency of `cynthion`? | Yes, unconditionally: `import luna_soc` at `cynthion/python/src/__init__.py:9`, top level. `import cynthion` fails without it (also the analyzer and facedancer tops) |
| Does the analyzer gateware use it? | No (zero references in `gateware/analyzer/`) |
| Does the facedancer gateware use it? | Yes: 5 import sites in `gateware/facedancer/top.py` |
| Does our `gateware/` use it? | `luna_soc.gateware.core.spiflash` (`soc/peripherals/flash.py`, `flash_cdc.py`, `soc/top.py`); `blockram` earlier |

- The facedancer SoC needs peripherals `amaranth-soc` does not have: `blockram`, `spiflash` (`ECP5ConfigurationFlashInterface`, `SPIPHYController`), `timer`, `uart`, `usb2`, `InterruptController`, `VexRiscv`, `provider.cynthion`, `top_level_cli`.
- Size of the trade: 321 lines of Python to carry (`blockram` 127, `VexRiscv` 149 + `InterruptController` 45, plus 5 pre-built VexRiscv `.v` files under `gateware/cpu/verilog/vexriscv/`) against 4078 lines of vendored `amaranth_soc` not carried.

## The import-order hack

- `luna_soc/__init__.py:9-20` (its own comment: "mildly evil hack") appends `gateware/vendor` to `sys.path` only when `import amaranth_soc` / `amaranth_stdio` fails. Importing a luna_soc peripheral is therefore what makes the bare name `amaranth_soc` resolvable.
- Hence `import luna_soc.gateware.core.blockram` must precede `from amaranth_soc import csr`, else `ModuleNotFoundError`.
- With real `amaranth-soc` installed, the `try` succeeds, the vendor path is never appended and the hazard disappears. Constraints: [`toolchain-versions.md`](toolchain-versions.md#constraints-that-must-hold).

## Real `amaranth-soc` is a drop-in

Probes, all pass (upstream `3e3d8b7`):

- import + versions (`0.1.dev1+g3e3d8b7`).
- class-level `csr.Field` annotations (the Python 3.14 bug): fixed upstream; works on 3.15t.
- `csr.Builder` + `csr.Bridge`; `wishbone.Decoder` + `WishboneCSRBridge`.
- things only luna_soc provides (`blockram`, cpu) correctly absent.
- the retired I2C mux prototype (`multiplexed.py`) imported unmodified with `luna_soc` stubbed empty: elaborated to 4670 lines of RTLIL.

Upstream suite: 295 passed, 6 subtests, 0.51 s; vendored copy reports `__version__ == "unknown"`, fails 13 and does not collect 39 of the same suite.

`patch_amaranth_soc_annotations.py` (now in `debris/scripts/`) and the 40-odd `__init__` rewrites in cynthion `53d3ea4` compensated for the vendored copy's vintage, not an upstream defect.

## If `luna_soc` were removed outright

- `import cynthion` fails: `ImportError: No module named 'luna_soc'` at `cynthion/python/src/__init__.py:9`, taking down the CLI, analyzer and `cynthion run`.
- The facedancer SoC build fails (list above).
- Our `riscv/` designs lose `blockram` and `VexRiscv` (321 lines).
- Test pitfall: a `sys.meta_path` blocker must implement `find_spec`; `find_module` is the removed legacy protocol and is silently ignored, so nothing is blocked and `import cynthion` falsely appears to succeed. Aliasing the vendored `amaranth_soc` first does not help: the bare `import luna_soc` is what fails.
- Patch for a conditional `import luna_soc` in `cynthion/__init__.py` plus moving `luna-soc` to a `facedancer` optional extra is a local commit to the fork (analyzer and CLI then do not depend on the SoC stack).

## The `cynthion` package is out of `gateware/` (2026-07-31)

- Only `CynthionPlatformRev1D4` (the r1.4 pin map) was ever used from `cynthion`; it inherits `LUNAApolloPlatform` -> `LUNAPlatform` and pins the luna-soc fork. Now vendored in `gateware/board/`: details and what was dead or load-bearing in [`upstream-boundary.md`](upstream-boundary.md#vendored-board-platform).
- Size: `cynthion_r1_4.py` 229 lines (upstream's 206 plus a header), `core.py` 158, `resources.py` 77, `__init__.py` 24; total 488.
- Proof: runs real place-and-route both ways on the same design text. `blinky`: utilisation identical (TRELLIS_COMB=39, TRELLIS_FF=28, TRELLIS_IO=59, DCCA=1, GSR=1), 59 pins identical. `wide` (both ULPI PHYs, HyperRAM, QSPI flash, sideband pins, both Type-C controllers): TRELLIS_COMB=61, TRELLIS_FF=28, TRELLIS_IO=105, 105 pins identical. Utilisation alone would not catch a swapped pin; the per-signal ball comparison does. Script: `platform_vendor_compare.py` (now in `debris/scripts/`).
- `luna` remains a real dependency of `gateware/` for USB (`USBDevice`, `USBSerialDevice`, stream endpoints) and `JTAGRegisterInterface`. This does not by itself retire the fork.
