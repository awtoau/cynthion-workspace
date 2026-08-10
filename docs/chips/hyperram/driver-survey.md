# HyperRAM software and SoC drivers, 2026-08-10

Who drives this part from software, what their init sequences say, and which
simulation models are usable in the open flow.

**Scope.** This is the *software* half. RTL controllers are
[`controller-survey.md`](controller-survey.md); the part is
[`w956a8.md`](w956a8.md); the vendor documents are
[`../../../sources/README.md`](../../../sources/README.md).

**Surveyed 2026-08-10** with `gh search code` over GitHub plus the vendor SDK repos.
Every claim below is from reading the file, not from its README.

## Code that names `W956A8MBYA`

Four upstreams, and only four.

| where | file | what it is |
|---|---|---|
| [zephyrproject-rtos/zephyr](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/memc/memc_mcux_flexspi_w956a8mbya.c) | `drivers/memc/memc_mcux_flexspi_w956a8mbya.c` + `dts/bindings/mtd/nxp,imx-flexspi-w956a8mbya.yaml` | the only OS driver for this exact part. NXP FlexSPI, i.MX RT |
| [JayHeng/imxrt-tool-ram-initscript](https://github.com/JayHeng/imxrt-tool-ram-initscript/blob/master/imxrt1060_flexspi_ad_b1_hyperram_init_w956a8mbya.jlinkscript) | `imxrt1060_flexspi_{ad,sd}_b1_hyperram_init_w956a8mbya.jlinkscript` | J-Link register-poke init: PLL, FlexSPI LUT, MPU |
| [aesc-silicon/elements-nafarr](https://github.com/aesc-silicon/elements-nafarr/blob/main/hardware/scala/nafarr/memory/hyperbus/sim/W956A8MBYA.scala) | `hardware/scala/nafarr/memory/hyperbus/sim/W956A8MBYA.scala` | SpinalHDL behavioural model, CERN-OHL-W |
| [nxp-mcuxpresso/mcux-component](https://github.com/nxp-mcuxpresso/mcux-component) | `gen_hal/zephyr/dts/bindings/mtd/nxp,imx-flexspi-w956a8mbya.yaml` | the binding, vendored |

Everything else in a code search is a Zephyr fork.

### The Zephyr driver never configures the device

`memc_mcux_flexspi_w956a8mbya.c` is 190 lines and its whole init is: install four LUT
sequences, reset the controller, read ID0, log it.

- LUT opcodes confirm the command set — `0xA0` read, `0x20` write, `0xE0` register
  read, `0x60` register write, all `8PAD` DDR.
- CA is split `RADDR_DDR 0x18` + `CADDR_DDR 0x10` — 24 + 16 = 40 bits, with the top
  8 in the opcode byte.
- `DUMMY_RWDS_DDR 0x07` — **7 dummy cycles**, i.e. the POR default latency code.
- **No CR0 or CR1 write anywhere.** It runs the part on POR defaults (`0x8F2F`), and
  every timing knob — `cs-interval`, `cs-hold-time`, `data-valid-time`,
  `ahb-write-wait-interval` — is a devicetree property on the *controller*, not a
  device register.
- `get_vendor_id()` reads 4 bytes from register address 0 and keeps the low 16 — ID0.
  It is a liveness check; nothing branches on the value.

So the one OS driver for this part proves the default configuration works and says
nothing about tuning it. It also has no tCSM handling, which for a memory-mapped AHB
master means the controller's own burst length is what keeps transactions short.

### NXP runs this part at 163.86 MHz with default CR0

The J-Link script's comment at the PLL write is explicit — *"Changes from 24 MHz →
327.72 MHz, which results FlexSPI to 163.86 MHz"* — and no CR0 write follows. The
highest third-party clock documented against this exact part, on the POR 7-clock
latency, and consistent with the 166 MHz grading of the `6I` on our board.

## Byte addresses vs word addresses — `0x1000` means two different registers

The trap most likely to be hit when porting a constant out of an SoC driver.

| convention | ID0 | CR0 | CR1 | used by |
|---|---|---|---|---|
| **word** (CA[31:24] × 0x800) | `0x0000` | `0x0800` | `0x0801` | the datasheets' own address map; our probes |
| **byte** (word × 2) | `0x0000` | `0x1000` | `0x1002` | STM32 OCTOSPI drivers |

Three sources agree independently:
[`stm32_is66wvh8m8.h`](https://github.com/STMicroelectronics/stm32-mw-extmem-mgr/blob/main/custom/memories/stm32_is66wvh8m8.h)
writes drive strength to `0x1000` calling it CFR0;
[`IS66WVHxxM8.h`](https://github.com/codead/STM32H7_OSIP_HyperRAM/blob/master/HyperRAM/IS66WVHxxM8/IS66WVHxxM8.h)
defines `DIR0 0x0000`, `DIR1 0x0002`, `CR0 0x1000`, `CR1 0x1002`; and GreenWaves'
[`hyperram.c`](https://github.com/GreenWaves-Technologies/gap_sdk/blob/master/rtos/pmsis/bsp/ram/hyperram/hyperram.c)
read-modify-writes CR0 at `0x1000` (`0x80001000` once the register-space flag is on).

**Why it bites here specifically.** Word `0x1000` on the Winbond part is the
Manufacturer Information Register — read-only, and documented in
[`../../../sources/README.md`](../../../sources/README.md). A constant lifted from an
STM32 driver into a word-addressed probe lands on the MIR, and the symptom is a
register write that is silently refused while other writes on the same path succeed.
That is exactly the symptom this repo already recorded.

Second cross-vendor trap in the same registers: **CR1[1:0] does not mean the same
thing per vendor.** ISSI encodes refresh-interval *multipliers* (`00` = 2×, `01` = 4×,
`10` = default, `11` = 1.5×); Winbond encodes absolute tCSM (`01` = 4 µs, `10` = 1 µs
and reserved on our part).

## SoC HyperBus controllers, and what each exposes

| SoC / OS | controller | driver | device config from software? |
|---|---|---|---|
| NXP i.MX RT | FlexSPI | Zephyr `memc_mcux_flexspi_*`, MCUXpresso SDK | no — LUT + controller timings only |
| ST STM32 L4+/L5/H7/U5 | OCTOSPI HyperBus mode | `HAL_OSPI_HyperbusCfg`, `stm32-mw-extmem-mgr` | **yes** — `RwRecoveryTimeCycle`, `AccessTimeCycle`, `LatencyMode`, plus arbitrary register writes |
| ADI MAX32 | HPB | Zephyr [`memc_max32_hpb.c`](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/memc/memc_max32_hpb.c) | **yes** — `config-regs` / `config-reg-vals` devicetree pairs, `latency-cycles`, `fixed-read-latency`, per-direction CS setup/hold/high |
| TI K3 (AM65x, J721e, J7200) | HBMC | Linux `drivers/mtd/hyperbus/hbmc-am654.c`, u-boot `drivers/mtd/hbmc-am654.c` | HyperFlash only |
| Renesas RZ/A, RZ/N | RPC-IF | Linux `drivers/mtd/hyperbus/rpc-if.c` | HyperFlash only |
| GreenWaves GAP8/GAP9 | uDMA HyperBus | [`rtos/pmsis/bsp/ram/hyperram/hyperram.c`](https://github.com/GreenWaves-Technologies/gap_sdk/blob/master/rtos/pmsis/bsp/ram/hyperram/hyperram.c) | **yes — the only one that retunes latency** |

**Upstream Linux and u-boot have no HyperRAM driver at all.** `drivers/mtd/hyperbus/`
is an MTD subsystem — `hyperbus-core.c` registers a CFI flash — so both TI and Renesas
entries are HyperFlash. There is no host-OS prior art to copy for the RAM case; the
prior art is all in RTOS memory-controller drivers and in RTL.

**ADI's MAX32 driver is the best shape to copy.** It is the only one that treats
"write these registers at init" as data rather than code: `config-regs` and
`config-reg-vals` are matched-length devicetree arrays with a `BUILD_ASSERT` on the
lengths, and latency/CS timings sit beside them. That is the interface
[#319](https://github.com/awtoau/cynthion-workspace/issues/319) wants for our CR0/CR1
sequencer.

### GreenWaves ships variable latency and a 3-clock count, deliberately

`hyperram.c:114-122` read-modify-writes CR0 and does two things this repo has only
discussed:

    reg &= ~(1 << 3);   // "Activate variable latency to avoid additionnal
                        //  latency when there is no refresh"
    reg &= ~0xf0;
    reg |=  0xe0;       // "Use 3 cycles of latency instead of the default 6 cycles"

- Clearing `CR0[3]` is **option 1** in [`w956a8.md`](w956a8.md), in production, with
  the reason stated as the reason we predicted.
- `0xe0` = 3 clocks is **option 6**, which that page says *do not* — and the two do
  not conflict: 3 clocks is rated to 85 MHz and GAP8's uDMA HyperBus runs well below
  that. It is evidence for "the short codes are real", not for using them at our CK.

### ST's init raises the clock *after* configuring the device

`stm32_is66wvh8m8.h` is a 105-line data structure and the ordering in it is the
interesting part:

- `StartupConfig.Frequency = 50 MHz` → write CFR0 `0x7000` under mask `0x7000`
  (drive strength → 19 Ω) → `EXEC_OPT_CFG` → `OptionalConfig.Frequency = 100 MHz`.
- `LatencyMode = FIXED`, `AccessTimeCycle = 6`, `RwRecoveryTimeCycle = 6`,
  `WriteZeroLatency = ON`, `WrapSize = NOT_SUPPORTED`.

Configure slow, then raise the clock, is the vendor-blessed sequence — and it is what
a ceiling probe should do rather than configuring at the target clock. The drive
strength being the *one* register they bother to change is also a data point for
option 4 in [`w956a8.md`](w956a8.md).

## Simulation models

| model | language | licence | fidelity |
|---|---|---|---|
| [nafarr `W956A8MBYA.scala`](https://github.com/aesc-silicon/elements-nafarr/blob/main/hardware/scala/nafarr/memory/hyperbus/sim/W956A8MBYA.scala) | SpinalHDL | CERN-OHL-W-2.0 | data path only — 8 MB array, CA decode, RWDS write mask. **No register space, no ID0/CR0, no tCSM, no refresh.** Latency is a hardcoded cycle count tuned to their own PHY (`writeLatency = 14`, and 104 in the non-DDR variant) |
| [`sergz72/FPGA` `hyperram_emulator.v`](https://github.com/sergz72/FPGA/blob/master/common/hyperram_emulator.v) | Verilog | — | open, synthesisable, usable with Icarus/Verilator |
| [GVSoC `hyperram`](https://github.com/gvsoc/gvsoc-core/blob/main/models/devices/hyperbus/hyperram.py) | Python wrapper over C++ | Apache-2.0 | models an S27KS0641; the behaviour is in `hyperram_impl`, not the Python |
| Winbond `W956X8MBY_verilog_p.zip` | Verilog | Winbond | **unusable** — `pragma protect data_method = "aes128-cbc"`, `encrypt_agent = "Model Technology"`. ModelSim/VCS/NC only |

**The vendor model's plaintext half is worth more than its encrypted half.**
`Config-AC.v` ships unencrypted inside the same zip and holds the full AC parameter
set per grade, including a **250 MHz** block the datasheet has no column for, plus
`tCSM` = 4000 ns below 85 °C / 1000 ns above and a `` `define LA_85C `` to switch it.
Filed at `sources/models/`, documented in
[`../../../sources/README.md`](../../../sources/README.md).

**No open model implements the register space.** nafarr, sergz72 and GVSoC all model
the array and the CA; none answers a register read with plausible ID0/CR0 contents.
Anything that exercises `hyperram_identify.py` in simulation has to be written here.

## What is worth taking

1. **ADI's `config-regs` shape** for the CR0/CR1 init sequencer
   ([#319](https://github.com/awtoau/cynthion-workspace/issues/319)) — register writes
   as a data table with a length assertion, not open-coded states.
2. **ST's configure-then-accelerate ordering** for the ceiling probe: set CR0 at a
   clock the part is unambiguously rated for, *then* raise CK.
3. **Zephyr's LUT** as an independent check on our CA construction — it is the same
   `0xA0`/`0x20`/`0xE0`/`0x60` and the same 7-cycle default latency we drive.
4. **Nothing for the read path.** No software driver calibrates capture; they all sit
   behind a hard macro that does it. Same conclusion as
   [`controller-survey.md`](controller-survey.md) reached for RTL.
