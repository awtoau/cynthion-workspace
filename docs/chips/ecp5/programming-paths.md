# Every way code gets onto this board, and every way it boots

Cynthion r1.4. Three things execute code here — the ECP5 fabric, the RISC-V SoC
inside it, and the SAMD11 — and there is a different path into each. This file
is the map; the depth is in the files it links.

| this file owns | owned elsewhere |
|---|---|
| the list of paths, and what each costs | [`flashing.md`](flashing.md) — the JTAG link, the two destinations, USB mode switching |
| which paths destroy the running design | [`flash-partitioning.md`](flash-partitioning.md) — boot image selection, slot layout, partition format, recovery |
| the boot chain from power-on to `.text` | [`qspi-boot-time.md`](qspi-boot-time.md) — `--spimode`/`--freq`, and why boot time is unmeasured |
| what `USRMCLK` forecloses | [`../w25q32-config-flash.md`](../w25q32-config-flash.md) — the part, its speed ceilings, the write arithmetic |

## Where code can be put

| what | what runs on it | path in |
|---|---|---|
| ECP5 configuration SRAM | the bitstream | JTAG, from Apollo |
| W25Q32 configuration flash, `0x0` | the bitstream the ECP5 self-loads | JTAG, or the FPGA-hosted flash bridge |
| W25Q32 configuration flash, `0xb0000` | the SoC's `.text` + `.rodata` | JTAG |
| ECP5 block RAM, `0x0` | `firmware/cynthion-boot`, 500 bytes | packed into the bitstream — no path of its own |
| SAMD11 | the Apollo debug firmware itself | USB DFU (Saturn-V bootloader) |

HyperRAM is the fifth memory a transfer can land in, and today nothing executes
from it — see [staging](#5-staging-into-hyperram).

## Transports

| transport | reaches | notes |
|---|---|---|
| Apollo JTAG | ECP5 SRAM, ECP5 TAP, config flash (bit-banged through Apollo's SPI-over-JTAG) | needs Apollo owning the shared USB port, `1d50:615c` |
| SPI-over-JTAG debug tunnel | registers in the running design | on r1.4 this **is** `device.spi`; there is no hard debug SPI above r0.2 ([`apollo_fpga/__init__.py:137,343`](../../../repos/apollo/apollo_fpga/__init__.py#L137)) |
| ER1 user-JTAG sink | HyperRAM staging area | the workspace's own tap, [`gateware/soc/bus/jtag_stage.py`](../../../gateware/soc/bus/jtag_stage.py); ER2 is the CPU debug module |
| the SoC's own USB/serial console | HyperRAM staging area | needs a running, answering shell |
| FlashBridge over USB bulk | config flash, offset 0 only | its own device, `1209:000f`, on the **CONTROL** port |
| USB DFU | SAMD11 program flash | device must be in the Saturn-V bootloader, `1d50:60e6` |

---

## Programming paths

### 1. `apollo configure <file>` — bitstream into SRAM

* Writes: FPGA configuration SRAM. Volatile; a power cycle restores whatever
  flash holds.
* [`repos/apollo/apollo_fpga/commands/cli.py:137`](../../../repos/apollo/apollo_fpga/commands/cli.py#L137). Follows with
  `allow_fpga_takeover_usb()`, so the device re-enumerates and any open Apollo
  handle dies.
* **Design survives: no.** It is replaced.
* **1.54 s** for a 432,412-byte `top.bit` = 274 KiB/s — wall time between
  `soc_run.py`'s "gateware built" and "configured" lines, which brackets the
  `cli.py` process spawn as well as the transfer. `tmp/logs/dev.log`,
  2026-08-07T12:50:39.6→12:50:41.2+10:00, direct USB port.
* The isolated transfer is faster and is measured separately: **322.2 ms for
  122,880 bytes at 1024-byte chunks, shift path only** —
  [`apollo-configure-speed-investigation.md`](../../apollo_samd11_mcu/apollo-configure-speed-investigation.md).
  The remaining gap in that path is USB transaction cost, not JTAG clock.
* Needs: Apollo on the port.

### 2. `apollo flash-program [--offset N] <file>` — into SPI flash

* Writes: configuration flash at `--offset`, **default 0**.
* **It destroys the running design first.**
  [`cli.py:170`](../../../repos/apollo/apollo_fpga/commands/cli.py#L170) calls
  `ensure_unconfigured()`
  ([`cli.py:152`](../../../repos/apollo/apollo_fpga/commands/cli.py#L152)),
  which is `programmer.unconfigure()`. This is not incidental — see
  [what `USRMCLK` forecloses](#what-usrmclk-forecloses).
* **3.33 s for 58,940 bytes at `0xb0000` = 17.3 KiB/s.** Erase was 1 × 32 KiB
  block + 7 × 4 KiB sectors, 231 pages programmed, `repos/apollo` `90c8b7b`
  driven by [`scripts/soc_run.py`](../../../scripts/soc_run.py). Reproduced at 3.34 s the next morning.
  `tmp/logs/dev.log` 2026-08-06T22:21:34 and 2026-08-07T11:04:53+10:00.
* Of that, **≈0.53 s is the W25Q32JV** and the rest is USB round trips at
  3.00 ms each. The arithmetic, and what would close it, is in
  [`../w25q32-config-flash.md`](../w25q32-config-flash.md) §Performance — do
  not re-derive it here. [#100](https://github.com/awtoau/cynthion-workspace/issues/100).
* Leaves the FPGA unconfigured. [`scripts/soc_run.py`](../../../scripts/soc_run.py) follows every flash write
  with a full `apollo configure`.
* Four more Apollo commands take the design down the same way: `flash-erase`
  and `flash-read` call `ensure_unconfigured()`, `flash-info` calls
  `force_fpga_offline()`, and `force-offline` is the operation.

### 3. `apollo flash-program --fast` — via the SPI bridge

* Writes: configuration flash, **offset 0 only**.
* [`cli.py:249`](../../../repos/apollo/apollo_fpga/commands/cli.py#L249).
  Builds (or reuses from `~/.cache/apollo/build/<digest>/`) a `FlashBridge`
  bitstream, `configure`s it into SRAM, hands the shared USB port to the FPGA,
  then writes flash over USB bulk with Apollo out of the data path.
* **Design survives: no.** Not by `ensure_unconfigured` — by being overwritten
  with the bridge.
* **The bridge is on the CONTROL port**, not TARGET:
  `platform.apollo_gateware_phy` is `control_phy` on `CynthionPlatformRev1D4`,
  so it cannot enumerate without a host cable in CONTROL. No state of the
  running design affects this either way. Commit `8c1c4f8`.
* Needs: the `1209:000f` udev rule (`apollo install-udev`), `yosys` and
  `nextpnr-ecp5` on PATH for the first build, and the `luna` package.
* `flash-fast` is a deprecated alias
  ([`cli.py:336`](../../../repos/apollo/apollo_fpga/commands/cli.py#L336)).
* **Defect:** both spellings declare `--offset` and neither honours it.
  `program_flash_fast` calls `programmer.flash(bitstream)` with no offset
  ([`cli.py:321`](../../../repos/apollo/apollo_fpga/commands/cli.py#L321)) and
  the parameter defaults to 0
  ([`apollo_fpga/ecp5.py:714`](../../../repos/apollo/apollo_fpga/ecp5.py#L714)).
  So `--fast` cannot write the `0xb0000` firmware slot, silently.

### 4. `apollo reconfigure` — REFRESH from flash

* [`cli.py:408`](../../../repos/apollo/apollo_fpga/commands/cli.py#L408) →
  `device.soft_reset()` → `REQUEST_RECONFIGURE` → Apollo pulses `PROGRAMN`.
* **It does not complete on this board.** `INITN` is open-drain with a
  pull-down and no pull-up on r1.4, and nothing releases it outside MCU
  startup, so configuration is attempted and abandoned — status Fail, BSE error
  0. [`reconfigure-initn-gap.md`](reconfigure-initn-gap.md) has the diagnosis and
  the one-line fix.
* Consequence: **boot-from-flash cannot be triggered from the host.** A power
  cycle is the only way to exercise it. Nothing in `./dev.py` uses this
  command; `scripts/soc_run.py --firmware-only` reconfigures with a full
  `apollo configure` of the same bitstream instead.

### 5. Staging into HyperRAM

Three front ends, one destination: the HyperRAM staging area, and a header of
magic + length + CRC32 written last so a partial run leaves a header the
bootloader ignores.

| front end | needs | transport |
|---|---|---|
| [`scripts/soc_jtag_stage.py`](../../../scripts/soc_jtag_stage.py) | a configured FPGA carrying this design (ER1 signature `0x4a53`); no console, no running CPU | one `shift_data` per image, CPU held in reset |
| console `load <hex>` | a running, answering shell | serial, byte at a time |
| [`scripts/soc_payload.py`](../../../scripts/soc_payload.py) | as above | the same console |

* The layout is [`firmware/cynthion-soc/src/hyperram.rs`](../../../firmware/cynthion-soc/src/hyperram.rs), compiled into the
  bootloader *and* the shell, so no front end can disagree with the reader.
* **Design survives: yes.** No bitstream rebuild, no flash write.
* **It installs nothing today.** `_image_start` is `0x100B0000` — the
  memory-mapped flash window — so the bootloader verifies the staged image and
  reports `FlashResident` (status 6) rather than copying it. A store into a
  read window does not program flash, and jumping afterwards would run whatever
  flash already held while reporting success.
  [`firmware/cynthion-boot/src/main.rs`](../../../firmware/cynthion-boot/src/main.rs), [`firmware/cynthion-boot/memory.x`](../../../firmware/cynthion-boot/memory.x).
* Staging throughput is **not measured** in this tree.
  `scripts/soc_jtag_stage.py --benchmark` exists and times the streamed path
  against the per-word register path in the same session; no run of it is
  recorded.
* `--clear` removes the header. It does not put back an image already copied —
  a reconfigure does.

### 6. Apollo debug-SPI — `spi`, `spi-inv`, `spi-reg`, `jtag-spi`, `jtag-reg`

* **Not a programming path.** These read and write *registers inside the
  running design*. No memory, no flash, no code.
* On r1.4 all five are the same wire: `create_jtag_spi` returns the
  SPI-over-JTAG tunnel for every revision above r0.2, and `device.spi` is set
  to it ([`apollo_fpga/__init__.py:137`](../../../repos/apollo/apollo_fpga/__init__.py#L137)).
  The separate `DebugSPIConnection` is r0.1/r0.2 only.
* **Design survives: yes** — and it must already be running, or there is
  nothing to answer.

### 7. Proposed, not built — flash write from the SoC

Needs no Apollo, no JTAG, and no downtime at all: the CPU writes its own next
firmware over the TARGET USB interface while the design keeps running.

| half | state |
|---|---|
| gateware | **present.** [`gateware/soc/peripherals/flash.py`](../../../gateware/soc/peripherals/flash.py) `HoldableSPIController` gives the CPU arbitrary SPI transactions with a chip select that latches across CSR writes. Exposed as `SPI0` at `0xf0000100`. (Upstream's CS is a write-strobe pulse and collapses between transfers, which is a separate all-zeros JEDEC result from the one below — the file's own header has the capture.) |
| firmware | **absent.** [`firmware/cynthion-soc/src/memory.rs`](../../../firmware/cynthion-soc/src/memory.rs) says so in its own header: reading all three regions is safe, writing is not, and no `write` verb exists. |

What the shell can do today is `flash read <hex>` and `flash id` — and `flash
id` is **not** a JEDEC read. It prints word 0 through the memory map
(`615000ff` on a programmed part) plus the window size; the JEDEC sequence
needs the SPI controller driven by hand. [#93](https://github.com/awtoau/cynthion-workspace/issues/93) tracks the write and soak work,
[#234](https://github.com/awtoau/cynthion-workspace/issues/234) the downtime this would remove.

### Other paths worth knowing

| path | what it does |
|---|---|
| [`scripts/bram_patch.py`](../../../scripts/bram_patch.py) | rewrites block RAM init in a **built** bitstream by location: 22 s against 95 s for a resynthesis, of which 20 s is the source check (`--no-verify-source` gives ~2 s and gives up the guarantee). Still needs `apollo configure` afterwards. |
| `apollo svf <file>` | plays an SVF over JTAG. |
| `cynthion flash --bitstream` | `force_fpga_offline`, flash at offset 0, `soft_reset` — upstream's wrapper for path 2. [`commands/util.py:122`](../../../repos/cynthion/cynthion/python/src/commands/util.py#L122) |
| `cynthion flash --soc-firmware` | flash at `0xb0000`, the same slot this SoC's `.text` uses. [`util.py:172`](../../../repos/cynthion/cynthion/python/src/commands/util.py#L172) |
| `cynthion run` | `configure` into SRAM, no flash. [`util.py:186`](../../../repos/cynthion/cynthion/python/src/commands/util.py#L186) |
| `cynthion flash --mcu-firmware`, `make APOLLO_BOARD=cynthion dfu` | **the fifth thing on the board**: USB DFU into the SAMD11's own program flash, through the Saturn-V bootloader at `1d50:60e6`. `apollo enter-dfu` / `exit-dfu` move between the two. [`util.py:142`](../../../repos/cynthion/cynthion/python/src/commands/util.py#L142), [`install.md`](../../install.md) |
| [`scripts/flash_backup.py`](../../../scripts/flash_backup.py) | read-only, chunked, each chunk read twice and compared. |

---

## What `USRMCLK` forecloses

`SCK` to the configuration flash exists on one ball, N9 = MCLK/CCLK, and user
logic reaches it only through the `USRMCLK` macro. Every instantiation on this
board hardcodes the tristate control to 0:

| file | line | where it lives |
|---|---|---|
| `luna_soc/gateware/core/spiflash/phy.py` | 243 | installed package, not this tree |
| `luna/gateware/interface/flash.py` | 50 | installed package, not this tree |
| [`repos/apollo/apollo_fpga/gateware/qspi_flash.py`](../../../repos/apollo/apollo_fpga/gateware/qspi_flash.py#L180) | 180 | submodule |

`i_USRMCLKTS = 0` drives the pin unconditionally. So **a design that talks to
the flash holds MCLK for as long as it runs**, and the configuration logic can
never reach the part underneath it.

* Verified on hardware, 2026-08-07: a JEDEC ID read taken with the design live
  returns all zeros.
* This is why every flash write costs the design, and why
  `ensure_unconfigured()` in path 2 is correct rather than merely cautious.
* It also bounds the read side — no `ODDRX1F` at that site, so SCK ≤ the fabric
  clock of the domain that generates it, never 2×. The full argument, and what
  a board revision would buy, is
  [`../w25q32-config-flash.md`](../w25q32-config-flash.md) §2.
* [#234](https://github.com/awtoau/cynthion-workspace/issues/234) is the issue: preload the next bitstream and switch, instead of a full
  configure every time.

---

## Boot paths

```
power-on / PROGRAMN
  └─ ECP5 config engine reads flash from 0x0        (or BOOTADDR — flash-partitioning.md)
     └─ bitstream configures the fabric AND initialises block RAM
        └─ block RAM 0x0 = firmware/cynthion-boot, 500 bytes, and NOTHING else
           └─ CPU releases from reset_addr = RAM_BASE = 0x0
              └─ bootloader: read staging header from HyperRAM, checksum, jump
                 └─ 0x100B0000: .text + .rodata, read from flash +0xb0000
```

### The bitstream carries the bootloader only

* [`gateware/soc/top.py:1698`](../../../gateware/soc/top.py#L1698) packs `bootloader + zero-fill + image` into the
  block RAM initialiser. With `.text` in flash the image half is **0 bytes** —
  `soc_run.py` reports `Rust firmware: 0 bytes (e3b0c44298fc) [nothing in block
  RAM]`.
* That is why a firmware change needs no synthesis: `./dev.py fw` writes flash
  and reconfigures the *same* bitstream. [`scripts/soc_run.py`](../../../scripts/soc_run.py) refuses
  `--firmware-only` if any section still loads via the bitstream, because
  skipping the build would then leave block RAM one edit behind flash while
  reporting success.

### The bootloader's decision table

[`firmware/cynthion-boot/src/main.rs`](../../../firmware/cynthion-boot/src/main.rs). Every path ends in the same instruction —
jump to the image region — and nothing branches on the reason.

| what it found | status | what it does |
|---|---|---|
| no magic in HyperRAM | `NoMagic` (1) | jump |
| magic, CRC mismatch | `Crc` (2) | jump |
| length 0, or past the image region | `Length` (3) | jump |
| HyperRAM never answered | `Silent` (4) | jump |
| a good image, and the region is block RAM | `Ran` (0) | copy, then jump |
| a good image, and the region is the flash window | `FlashResident` (6) | **do not copy**, jump |
| this image panicked | `Panicked` (5) | jump |

`FlashResident` is the ordinary case on the board as built — see
[staging](#5-staging-into-hyperram). It is not an error: it
means the staged image was fine and is not what is running.

Two breadcrumbs, no console: the status word at `_boot_status` = `0x3fc`
(marked `"BOT"` in its high 24 bits, read back by the shell's `info`), and a
coarsened byte in the `BOARD_SIDEBAND` CSR, which reaches Apollo on pin T6
without USB, console or JTAG.

The verify pass and the copy pass are two passes over HyperRAM on purpose: a
single pass that checksummed while copying would have overwritten the image
region before discovering the CRC was wrong.

### The image's memory map

[`firmware/cynthion-soc/memory.x`](../../../firmware/cynthion-soc/memory.x):

| region | origin | length | contents |
|---|---|---|---|
| BOOT | `0x00000000` | 1 KiB | `cynthion-boot` and its stack |
| RAM | `0x00000400` | 63 KiB | `.data`, `.bss`, stack |
| FLASH | `0x100B0000` | 3392 KiB | `.text`, `.rodata` |

* `0x100B0000` = `FLASH_BASE` `0x10000000` + `0xb0000`, moondancer's
  established firmware slot, clear of the bitstream at offset 0.
* Running `.text` from there requires `FLASH_CACHED` — the PMA region is
  declared `exe=1` only when it is `main=1`
  ([`gateware/soc/top.py`](../../../gateware/soc/top.py)), so the uncached configuration cannot fetch
  instructions from the window at all. One decision, two settings.
* The bootloader jumps to an **address**, not a symbol. `_image_start` in
  [`firmware/cynthion-boot/memory.x`](../../../firmware/cynthion-boot/memory.x) and `ORIGIN(FLASH)` in
  [`firmware/cynthion-soc/memory.x`](../../../firmware/cynthion-soc/memory.x) are held together by
  `scripts/soc_generate_pac.py --check`.

### What has not been measured

* **Configuration from flash**, in any `--spimode` or at any `--freq`. The
  `INITN` gap blocks host-triggered reconfiguration, so no boot-time figure
  exists — [`qspi-boot-time.md`](qspi-boot-time.md).
* **Staging throughput** over ER1, and the console `load` path.
* **Page program, sector erase and block erase on the part**, independently of
  the transport — [#93](https://github.com/awtoau/cynthion-workspace/issues/93).

---

## Comparison

| path | writes | where | time | design survives | needs |
|---|---|---|---|---|---|
| `apollo configure` | bitstream | FPGA SRAM (volatile) | **1.54 s** / 432,412 B, direct USB port | no — replaced | Apollo on the port |
| `apollo flash-program` | anything | flash, any 256 B-aligned offset | **3.33 s** / 58,940 B = 17.3 KiB/s | **no — `ensure_unconfigured()` first** | Apollo on the port |
| `apollo flash-program --fast` | anything | flash, **offset 0 only** | not measured here | no — overwritten by the bridge | cable in CONTROL, `1209:000f` udev rule, FPGA toolchain |
| `apollo reconfigure` | nothing | — | — | no — and it never completes | a working `INITN`, which this board has not got |
| [`scripts/soc_jtag_stage.py`](../../../scripts/soc_jtag_stage.py) | firmware image | HyperRAM staging area | not measured; one JTAG scan, no rebuild | **yes** — CPU held and released | a configured FPGA carrying this design |
| console `load` / [`scripts/soc_payload.py`](../../../scripts/soc_payload.py) | firmware image | HyperRAM staging area | not measured; serial-bound | **yes** | a running, answering shell |
| `apollo spi` / `jtag-spi` / `spi-reg` | register values | the running design | per-transaction USB latency | **yes** — and it must be running | a design that answers |
| [`scripts/bram_patch.py`](../../../scripts/bram_patch.py) | block RAM init | a built `.bit` on disk | 22 s (2 s with `--no-verify-source`) | n/a — then `apollo configure` | a matching build directory |
| `cynthion flash --mcu-firmware` | Apollo firmware | SAMD11 program flash | not measured here | n/a — the FPGA is not involved | Saturn-V bootloader, `1d50:60e6` |
| **SoC writes flash itself** | firmware | flash, any offset | — | **yes, and no downtime at all** | **firmware that does not exist** ([#93](https://github.com/awtoau/cynthion-workspace/issues/93), [#234](https://github.com/awtoau/cynthion-workspace/issues/234)) |

## Related

* [`flashing.md`](flashing.md) · [`flash-partitioning.md`](flash-partitioning.md) ·
  [`qspi-boot-time.md`](qspi-boot-time.md) · [`reconfigure-initn-gap.md`](reconfigure-initn-gap.md) ·
  [`../w25q32-config-flash.md`](../w25q32-config-flash.md)
* [`config-engine-probe.md`](config-engine-probe.md) — what the configuration
  engine will and will not accept from a running design. **The ECP5 has no
  fabric path into configuration**, so a design cannot replace itself.
* [`../../hardware.md`](../../hardware.md) — the board index
