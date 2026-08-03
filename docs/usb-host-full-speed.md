# A full-speed USB host on Cynthion — the short path

**Date:** 2026-08-03T12:00:00+10:00
**Baseline:** `uart16550-console` at `bf819e3`
**Question:** not "can it host at 480" (`docs/usb-host-proposal.md` answered that) but
"what is the shortest path to a host that enumerates a device and that Linux can
reach, if full speed is acceptable".

**No board was touched. Every number here is from datasheets, from the KiCad
netlist, from source already checked out in this tree, or from a synthesis run
performed on a standalone netlist.**

---

## 0. The answer in one paragraph

Take **SpinalHDL's `UsbOhci`**, which is already checked out at
`repos/vexiiriscv/ext/SpinalHDL/lib/src/main/scala/spinal/lib/com/usb/ohci/`,
is **MIT**, and is built by a Scala toolchain this workspace already runs. It
costs a measured **3736 LUT4, 1442 FF and one block RAM** on ECP5 for the whole
Wishbone-wrapped controller including its PHY. Drive it through the **TARGET
USB3343 put into 6-pin FS/LS serial mode**, because that mode presents a
transceiver-style `tx_enable` / `tx_data` / `tx_se0` / `rx_dp` / `rx_dm`
interface that is a **signal-for-signal match** to the interface SpinalHDL's PHY
already expects. No PMOD adapter, no ULPI back end, no board modification, no
new clock. Then read a device descriptor from bare-metal Rust. Linux mounting a
stick is a separate, later, and *cheaper-than-you-think* decision — and it does
not require Linux on the SoC.

**Section 3 is the crux.** The ULPI PHYs do not block this. They very nearly
solve it.

---

## 1. The recommended first step

**Build `UsbOhciWishbone` inside `HelloSoC` and read the numbers. No board, no
firmware, no wiring.**

```
sbt "runMain spinal.lib.com.usb.ohci.UsbOhciWishbone \
     --netlist-directory <out> --netlist-name UsbOhciWishbone \
     --port-count 1 --phy-frequency 60000000 --dma-width 32"
```

then instantiate the emitted Verilog the way `ecp5-test/riscv/vexii_cpu.py:276`
already instantiates `VexiiRiscv.v` — `platform.add_file` plus an `Instance` —
add `wb_ctrl` as a decoder subordinate and `wb_dma` as a fourth arbiter master,
and run `scripts/soc_timing_sweep.py`.

### What it costs

One sbt run and a handful of nextpnr runs. The machinery all exists:
`scripts/usb-host-core-area.py` did exactly this shape of measurement for GUH,
and `scripts/emit_verilog.py` / `vexii_cpu.py` already carry SpinalHDL-generated
Verilog into an Amaranth design.

### What it proves, and why it is the *right* first step

Area is not the risk. **Timing is.** The measured standalone routed fmax of the
OHCI control path is **62.9 MHz on a -6 part**, and this SoC's `sync` domain —
which carries the CPU and the Wishbone fabric — currently closes at only
**71.58 MHz against a 60 MHz constraint**. Machdyne, who ship this core on an
ECP5, run their system clock at **40 MHz**. So the single question that decides
the whole design is whether a 60 MHz `sync` still closes with the OHCI's control
and DMA logic in it, at 66% occupancy, on a design where `18c1fa5` established
that the critical path is dominated by routing rather than logic.

If it does not close, the mitigations are known and ordered:

1. Our part is **speed grade 8**, not the -6 the 62.9 MHz was measured on.
2. Put the OHCI's `frontCd` on **`usb`** (94.30 MHz fmax, 57% margin) instead of
   `sync`, and cross the Wishbone. The core is already built for two domains —
   `UsbOhciWishbone.scala:52` takes `frontCd` and `backCd` as constructor
   arguments and `CtrlCc` does the PHY-side crossing for you.
3. Drop `sync` to 50 MHz.

### The area arithmetic

| | LUT4 (of 24288) | EBR (of 56) |
|---|---|---|
| SoC today (`tmp/vexii_hello/build/top.tim`) | 12201 (50%) | 42 (75%) |
| `+ UsbOhciWishbone`, 1 port | ~15937 (66%) | 43 (77%) |
| `+` 4 KiB uncached DMA scratch (§5.3) | ~15937 (66%) | 45 (80%) |

For comparison, the GUH high-speed SIE measured at 14300 LUT (59%) and 42 EBR.
**OHCI costs about 1600 more LUT and 1–3 more block RAMs than GUH's SIE, and in
exchange gives a published register map, hardware DMA, hardware retry and data
toggling, root-hub port control, support for devices behind hubs, and a driver
that already exists in Linux, U-Boot, BSD and Zephyr — at 12 Mbps instead of
480.**

---

## 2. Resource cost of `UsbOhci` on ECP5 — measured, not estimated

Machdyne and litex-hub publish **no** utilisation figures. Their repos, READMEs,
CI and issues were checked; there is no nextpnr report anywhere. So the numbers
below were produced here, by running `synth_ecp5` + `nextpnr-ecp5` (Yosys
0.65+57, nextpnr 0.10-74) over the pre-generated netlist LiteX actually ships:
`pythondata_misc_usb_ohci/verilog/UsbOhciWishbone_Dw32_Pc1_Pf48000000.v` — one
port, 48 MHz PHY, 32-bit DMA.

| Resource | Count | % of the 24288 LUT4 nextpnr sees on this part |
|---|---|---|
| TRELLIS_COMB (post-pack) | **3736** | 15% |
| TRELLIS_FF | **1442** | 5.9% |
| DP16KD (18 kbit EBR) | **1** | 1 of 56 |
| TRELLIS_RAMW | 0 | — |

Hierarchical split (`-noflatten`), local LUT4/FF:

| Block | LUT4 | FF |
|---|---|---|
| `UsbOhci` core (registers, list processor, DMA engine) | 2546 | 920 |
| `StreamFifo` data buffer | 96 | 95 (+ **the 1 EBR**) |
| three `Crc` instances (CRC5 token, CRC16 data) | 147 | — |
| `UsbLsFsPhy` + `UsbLsFsPhyFilter` (the bit-banged serial front end) | 353 | 151 |
| `BmbToWishbone` (DMA master) + `WishboneToBmb` (ctrl slave) | 33 | 114 |
| `CtrlCc` clock-domain crossing | ~20 | ~70 |

Routed fmax, standalone on a 45F/-6: **ctrl 62.9 MHz, phy 78.3 MHz.**

### The block RAM story is better than this table

That netlist is **frozen at 2021-05-27** — it is the only commit in
`pythondata-misc-usb_ohci`. Its one EBR is a `reg [31:0] logic_ram [0:511]`,
i.e. `fifoBytes = 2048`.

**Current SpinalHDL no longer has it.** `fifoBytes` is now a *dead parameter* —
declared at `UsbOhci.scala:26`, referenced by the three generator Apps, and used
nowhere in the core. The only memory left is

```scala
val ram = Mem.fill(p.storageBursts*wordPerBurst)(Bits(p.dataWidth bits))   // UsbOhci.scala:913
```

which at the defaults (`storageBursts=4`, `dmaLengthWidth=6`, `dataWidth=32`) is
4 × 16 = **64 words × 32 bits = 256 bytes**. On ECP5 that will infer as
distributed RAM or a single EBR, not more. Given that block RAM is the scarce
resource here (42 of 56 in use), **generating from the local `ext/SpinalHDL`
rather than using LiteX's 2021 netlist is worth doing for this reason alone.**

Everything else is pure DMA into system memory. The controller has no packet
buffers of its own.

### The Konfekt fit data point

Machdyne's Konfekt is an **LFE5U-12F**, and Machdyne's own product page says
"24K LUTs when using open-source tools" — the 12F is a harvested 25F die and
`nextpnr-ecp5 --12k` exposes the full 24288 COMB / 56 DP16KD array, which is
exactly the budget this workspace's builds report. The Konfekt ships a working
Linux + USB-host image; `konfekt.dts` contains

```dts
usb0: mac@c0000000 { compatible = "generic-ohci"; reg = <0xc0000000 0x1000>; interrupts = <16>; };
```

and buildroot has a dedicated `litex_vexriscv_usbhost_defconfig`. So **this core,
at this size, on this die, running Linux, is a shipped product.** That is the
strongest single piece of evidence in this document.

(Konfekt and Noir declare one host port; **Schoko declares two**, and no
two-port netlist exists anywhere on GitHub — a Schoko `usb_host` build silently
falls back to regenerating from sbt. Worth knowing if Schoko is ever used as a
reference.)

---

## 3. The crux: are the ULPI PHYs fatal?

**No. And the reason is specific enough to be actionable.**

### 3.1 The USB3343 has a transceiver mode, and it is a signal-for-signal match

Microchip **USB334x datasheet DS00002646A, §6.6 "Full Speed/Low Speed Serial
Modes", pp. 58–59**. Writing bit 0 of the **Interface Control register** (`07h`
write / `08h` set / `09h` clear) puts the PHY into **6-pin FS/LS serial mode**,
in which the eight ULPI data pins are redefined:

| DATA pin | Serial-mode signal | Dir |
|---|---|---|
| DATA[0] | `tx_enable` | in |
| DATA[1] | `tx_data` (differential drive) | in |
| DATA[2] | `tx_se0` | in |
| DATA[3] | `interrupt` | out |
| DATA[4] | `rx_dp` | out |
| DATA[5] | `rx_dm` | out |
| DATA[6] | `rx_rcv` (differential receive) | out |
| DATA[7] | reserved, driven low | out |

**In serial mode the PHY does no encoding at all.** `tx_data`/`tx_se0` set the
line state directly; NRZI, bit-stuffing, SYNC and EOP are the link's job — which
is exactly what a bit-banged controller already produces.

Now compare `UsbLsFsPhyAbstractIo`, the interface SpinalHDL's PHY presents
before it is converted to tristate pins
(`.../com/usb/phy/UsbHubPhy.scala:41-56`):

```scala
case class UsbLsFsPhyAbstractIo() extends Bundle with IMasterSlave {
  val tx = new Bundle { val enable = Bool(); val data = Bool(); val se0 = Bool() }
  val rx = new Bundle { val dp = Bool(); val dm = Bool() }
}
```

`tx.enable` → DATA[0]. `tx.data` → DATA[1]. `tx.se0` → DATA[2]. `rx.dp` →
DATA[4]. `rx.dm` → DATA[5]. Six wires, no glue logic, no state machine, no
translation. `UsbOhciWishbone.scala:91` currently throws this interface away by
calling `.toNativeIo()` to build tristate D+/D-; **the change is to not call it**
and route the bundle out to the ULPI data pins instead.

Both sides are the classic UTMI+ Level 3 / discrete-transceiver interface, which
is why they match. It is not a coincidence, but it is a gift.

### 3.2 The USB3343 supports host mode, and Cynthion already owns the missing piece

- §6.4.1 of the datasheet is titled **"USB334x Host Features"**. Table 5-1 has
  explicit **Host Full Speed** and **Host Low Speed** rows.
- **OTG Control register** (`0Ah`) bits 1 and 2 are `DpPulldown` / `DmPulldown`,
  15 kΩ each, **default `1`**. LUNA already exposes them —
  `repos/luna/luna/gateware/interface/ulpi.py:406-407` declares
  `dp_pulldown`/`dm_pulldown` with `init=1` and comments them "intended for host
  mode".
- The PHY implements host LS keep-alive, the 40-bit long EOP for HS disconnect
  detection, host resume K, and UTMI+ L3 PRE preambles.
- **The one gap is VBUS**: §5.7.3 says plainly that the USB334x "does not
  provide an external output for the DrvVbusExternal ULPI register… the external
  VBUS supply or power switch must be controlled by the Link." On Cynthion the
  FPGA already owns those switches directly — `target_c_vbus_en` (K5),
  `control_vbus_en` (L1), `aux_vbus_en` (L2).

### 3.3 Cynthion's clocking makes serial mode *easier*, not harder

The datasheet warns that the PHY shuts off CLKOUT when entering serial mode.
**On Cynthion that is a non-event**: CLKOUT (pin 2) is strapped to +3V3, which
selects ULPI *Clock Input* mode, and the FPGA drives 60 MHz into REFCLK from the
board oscillator on ball A8. Confirmed two ways — the KiCad net, and
`cynthion_r1_4.py:115-125` declaring `clk_dir='o'` on all three ULPIResources.
The clock is ours and it keeps running.

### 3.4 And no new clock is needed either

`UsbLsFsPhy` derives its bit timing from whatever domain it is placed in:

```scala
val fsRatioExact = (ClockDomain.current.frequency.getValue.toDouble/12e6)
val fsRatio = fsRatioExact.round.toInt
assert(((fsRatio-fsRatioExact)/fsRatioExact).abs < 0.01)     // UsbHubPhy.scala:118-120
```

**60 / 12 = 5 exactly**, and `UsbLsFsPhyFilter` only requires `fsRatio >= 4`. So
the PHY runs in the SoC's existing 60 MHz `usb` domain with `fsRatio = 5`, and
low speed follows at `fsRatio*8 = 40`. Pass `--phy-frequency 60000000` to the
generator and there is **no third PLL output, no 48 MHz, nothing to solve**.
(This also disposes of the concern that `solve_pll` in
`repos/apollo/apollo_fpga/gateware/variable_clock.py` can only produce integer
multiples of `sync` on CLKOS2 — it never needs to.)

### 3.5 What actually has to be built for this

Three things, all small, all bounded:

1. **A serial-mode entry sequencer.** Program Function Control and OTG Control
   to Table 5-1's "Host Full Speed" row (`XcvrSelect=x1`, `TermSelect=1`,
   `OpMode=00`, both pulldowns on), then write Interface Control bit 0. The
   existing ULPI register window from #120 — already proven on `target_phy`,
   already reading vendor `0424` / product `0009` — **is the tool for this**.
   Exit is a single STP pulse, so the mode switch is reversible and the port can
   be handed back to the analyzer.
2. **Per-bit output enables on the ULPI data bus.** In serial mode the FPGA
   drives DATA[0:2] and reads DATA[3:7] *simultaneously and permanently*.
   `ULPIResource("target_phy", data="R2 R1 P2 P1 N3 N1 M2 M1", …)` declares an
   8-bit `dir="io"` port, and Amaranth gives such a port **one shared `oe`**.
   The fix is a platform-level change: declare the eight pins as eight
   individual 1-bit `io` resources and assemble the ULPI byte bus from them for
   the initialisation phase. Mechanical, but it must be done, and it is easy to
   miss until synthesis silently gives you a bus-wide tristate.
3. **STP held low** for the duration, and `RESETB` not pulsed.

### 3.6 The honest alternative, and why it is second

A **ULPI back end for `UsbOhci`** — replacing `UsbLsFsPhy` with something that
speaks ULPI bytes — is *architecturally* very reachable, more so than the earlier
sweep concluded. The seam between the OHCI core and its PHY,
`UsbHubLsFs.Ctrl` (`.../com/usb/phy/UsbHubLsFs.scala:44-64`), is **byte-level,
not bit-level**:

```scala
val tx  = Stream(Fragment(Bits(8 bits)));  val txEop = Bool()
val rx  = CtrlRx()          // Flow of { stuffingError, data[8] } + active
val lowSpeed, usbReset, usbResume, overcurrent, tick = Bool()
val ports = Vec(CtrlPort(), portCount)   // power / reset / suspend / resume / connect / disconnect / lowSpeed
```

and it comes with a ready-made clock-domain crossing (`CtrlCc`). CRC5 and CRC16
are computed *inside the OHCI core*, not the PHY — which is exactly right for
ULPI, where CRC is the link's job. The mapping to LUNA's `UTMITranslator` is
close to one-to-one: `tx` ↔ `tx_data`/`tx_valid`/`tx_ready`, `rx.flow` ↔
`rx_data`/`rx_valid`, `rx.active` ↔ `rx_active`, `stuffingError` ↔ `rx_error`,
`lowSpeed` ↔ `xcvr_select`, and the port control maps onto `line_state`,
`op_mode`, `term_select` and the board's own VBUS pins.

So it is a real option, and it is the *right* option if the design ever wants
high speed on the same port, or wants the analyzer and the host to share one
`UTMITranslator` as `docs/usb-host-proposal.md` §13.4 proposes. But it is
**several hundred lines of new gateware that nobody has ever written** — no ULPI
back end for SpinalHDL's OHCI exists in SpinalHDL, in any fork, or anywhere on
GitHub. Against "least new gateware", six wires beat it.

### 3.7 The PMOD fallback

`user_pmod` 0 and 1 are unused and unrequested, and from the KiCad netlist:

- **Every PMOD signal already has a 33 Ω series resistor** — RN1–RN4, `R_Pack04`
  value `33`, FPGA-side nets `A1`…`B10`, connector-side `PMOD_A1`…`PMOD_B10`.
  33 Ω in series with an LVCMOS33 output is the standard transceiver-less USB
  recipe (Fomu, ULX3S, OrangeCrab, and Machdyne's own boards all do this).
- Pins 5/11 are GND, pins 6/12 are **+3V3**.
- **There is no 5 V on any expansion header.** Mezzanine J5 is the same story:
  pins 15/16 +3V3, pins 2/14/17/29 GND, pins 1 and 30 unconnected, 22 signals,
  no VBUS.

So a PMOD USB-A breakout is: a socket, **two 15 kΩ pull-downs to GND** (host
termination — the FPGA cannot supply these), and **an external 5 V feed**. Four
parts. It sidesteps the PHY completely and has no unspecified timing anywhere.

**Keep this as the fallback, not the first move.** Serial mode is a bitstream
away and uses the real port with real VBUS control; the PMOD needs a soldering
iron and a 5 V source. But if serial mode misbehaves — §7 lists three ways it
might — the PMOD path is fully specified and cannot fail for electrical reasons
you cannot see.

One caveat on provenance: `repos/cynthion-hardware` is at `e5cf493`, whose
commit message is *"import in-progress KiCad work as baseline"*. The 33 Ω and
no-5 V facts are almost certainly stock, but **check them against the released
r1.4 schematic before ordering an adapter.** In the same repo the
`TARGET_FS_MONITOR_D+/-` nets (FPGA balls N4/P3, the `target_usb_dp`/`dm`
resources) carry **only the FPGA pad** in the PCB netlist while the schematic
routes them to the TARGET port sheet — the two disagree, so do not plan around
those pins in either direction without checking the release files.

---

## 4. Cores the earlier sweep missed

| Core | Licence | Speed | PHY interface | Register interface | Verdict |
|---|---|---|---|---|---|
| **Cheshire's pre-generated `UsbOhciAxi4.v`** (pulp-platform/cheshire, `hw/future/`) | **MIT** (the Verilog) / SHL-0.51 (the SV wrapper) | FS/LS | raw pins | **OHCI**, 4 ports, AXI4 DMA master | The same SpinalHDL core, shipped as 784 KB of plain Verilog, taped out at ETH Zürich/Bologna. **A no-Scala fallback**, though we already run sbt. |
| **ultraembedded `core_ulpi_wrapper`** | GPL-3.0 | LS/FS/HS | **UTMI+ ⇄ ULPI**, carries `utmi_dppulldown_i`/`dmpulldown_i` | n/a | The genuine ULPI bridge the sweep was looking for. Paired with `core_usb_host` (UTMI, not raw pins as previously classified) it wires 1:1 to a USB3343. **But: GPL-3.0, custom CSR, no DMA, host proven at FS only, wrapper proven in device mode only.** |
| `ue11-hcd.c`, shipped inside `core_usb_host/linux/` | **GPL-2.0** | — | — | Linux HCD for that custom CSR, derived from `sl811-hcd`, 1798 lines | Out-of-tree, never submitted. Proof that "custom CSR + a Linux driver" is possible, and a measure of the cost: 1798 lines you would maintain forever. |
| **freecores `usbhostslave`** (OpenCores, 2004) | **LGPL-2.1-or-later** | FS/LS | raw pins | custom Wishbone CSR | The most permissively licensed alternative. Quartus 6.0 vintage, unmaintained for 20 years. |
| `freecores/softusb` | **GPL-3.0-only** (previously mislabelled) | FS/LS | raw pins | custom CSR | Shipped in Milkymist One. Copyleft. |
| `emard/usbh_host_hid` (ulx3s-misc) | "GPL", no version, no LICENSE file | LS + FS | raw pins, 27 Ω | **none** — hardwired setup ROM, no CPU interface | Genuinely proven on ECP5 and widely reused, but it is an appliance, not a controller. **Note `dan-rodrigues/icestation-32` is MIT at repo level while vendoring these GPL files** — treat as contaminated. |
| `riscduino usb1_host` | **Licence conflict** — SPDX header Apache-2.0 over retained Ultra-Embedded GPL text | FS | UTMI | ultraembedded CSR | Sky130 shuttle. Do not touch until the conflict is resolved. |
| `mariamtaher2000` UHCI project | **none** | FS (claimed) | UTMI | **UHCI** | The only non-OHCI standard register file found in any HDL. Unlicensed, unverified, testbenches only, `.v~` backups committed. And Linux binds UHCI to PCI anyway. |

### The negative results, which matter as much

- **There is no open-source EHCI. At all.** Searches for `ASYNCLISTADDR` /
  `PERIODICLISTBASE` filtered to HDL return nothing; OpenCores' full 223-project
  communication index has nothing; Gaisler's GRUSBHC is confirmed absent from
  the GPL release (`grlib-gpl-2023.4` ships only `.in`/`.in.vhd` config stubs
  and a LEON C driver — no RTL). No open xHCI either.
- **There is no OHCI RTL other than SpinalHDL's.** A GitHub code search for
  `HcRevision` / `HcControlHeadED` restricted to Verilog/VHDL/SystemVerilog
  returns zero implementations — every hit is a software driver, an emulator, or
  a vendor PAC. Cheshire's SystemVerilog hit is a UVM testbench *for* the
  SpinalHDL core.
- **LiteX still has exactly one host core** (`usb_ohci.py`), and it is a wrapper
  around this same thing. Everything else in `litex/soc/cores/` is device-side.
- **`apfaudio/guh` remains the only Amaranth host gateware in existence.**

### Licence, settled

`docs/usb-host-proposal.md` §19.1 recorded that the SpinalHDL licence "was not
confirmed (the repository reports `NOASSERTION`)". **It is now confirmed, from
the checkout in this tree:** `ext/SpinalHDL/LICENSE` says "The Spinal HDL core is
under the LGPL license / The Spinal HDL lib is under the MIT license", and
`LICENSE_lib` is the MIT text verbatim. `UsbOhci` lives under `lib/`, therefore
**MIT**. The LGPL applies to the elaboration framework, which is a build-time
tool that produces Verilog — the same relationship a compiler has to its output.
That correction should be folded back into §19.1.

---

## 5. The ladder — what each step needs and what it proves

### M0 — the fit build. *No board.*
Section 1. One sbt run, `soc_timing_sweep.py`. **Proves: whether a 60 MHz `sync`
survives.** This is the only step that can kill the plan, so it goes first.

### M1 — the wire, without the OHCI. *Cheapest possible de-risk.*
Using only the existing #120 ULPI register window plus a GPIO read: put
`target_phy` into 6-pin serial mode, enable `target_c_vbus_en`, and read
DATA[4]/DATA[5].

- Nothing attached, pulldowns on → **`rx_dp = 0, rx_dm = 0`** (SE0).
- Full-speed device attached → **`rx_dp = 1, rx_dm = 0`** (J at full speed).
- Low-speed device → the inverse.

**One observation proves serial mode entry, host terminations, VBUS sourcing and
speed detection at once**, and it needs no OHCI core in the bitstream at all. If
this does not work, stop and go to the PMOD (§3.7) before spending anything else.

### M2 — root hub registers.
OHCI in the SoC. Firmware reads `HcRevision` — it must return **`0x10`**. Then
`HcRhStatus` / `HcRhPortStatus[0]`: set `PPS`, watch `CCS` assert on attach and
`LSDA` report the speed. **Proves the register file, the DMA-free control path,
and the port state machine.** Needs: the CSR window (OHCI wants **0x400**, not
the 0x100 §15.1 of the earlier proposal sketched) and a PLIC source.

### M3 — one control transfer. **This is first light.**
An HCCA (256-byte aligned), one ED and three TDs (16-byte aligned) in memory the
CPU and the OHCI both see, then `SET_ADDRESS` followed by
`GET_DESCRIPTOR(DEVICE)`. **Proves the list processor, the DMA master, CRC, the
retry logic and data toggling** — i.e. everything GUH's SIE would have made
firmware do by hand. Roughly 500–800 lines of `no_std` Rust.

### M4 — mass storage.
A bulk ED, Bulk-Only Transport, SCSI `INQUIRY` / `READ CAPACITY` / `READ(10)`.
FS bulk ceiling is 1.22 MB/s (19 × 64 B per 1 ms frame), and DMA means the CPU
is not in the byte path — so unlike the register-port estimate in the earlier
proposal's §15.3, **line rate is reachable here**.

### M5 — "Linux mounts it". Three routes, and the obvious one is the longest.

| | What it needs | Verdict |
|---|---|---|
| **5a. Re-export as a USB mass-storage *device*** on `aux_phy`/`control_phy`. The SoC already runs LUNA's device stack there for the CDC-ACM console; add a Bulk-Only Transport interface and forward SCSI commands to the target stick. | one more bulk endpoint pair of gateware; firmware SCSI forwarding | **Least new gateware of the three.** Linux sees an ordinary USB stick, mounts it with zero custom anything. Recommended. |
| **5b. USB/IP.** Firmware relays URBs over the existing link; a userspace shim on the PC serves the USB/IP protocol on localhost:3240; `usbip attach` binds the **in-tree `vhci-hcd`**. | no gateware; substantial firmware; a PC-side userspace bridge | No kernel code, no DRAM, and it generalises to *any* device class rather than just storage. More firmware than 5a. |
| **5c. Linux on the SoC**, binding `ohci-platform` via `compatible = "generic-ohci"` — the literal reading of the goal. | `--with-mmu` + supervisor on VexiiRiscv (**not currently enabled** — `vexii_cpu.py` generates machine-mode only); the 8 MiB HyperRAM behind a Wishbone adapter (**#90 open, #92 DQS unfinished**); a Linux port; a DTB | Reachable — Konfekt proves it on this die. But **the USB controller is one device-tree node; the blocker is 8 MiB of DRAM that has no bus adapter yet.** |

**So: is the OHCI-to-Linux route reachable on this hardware, or blocked by the
ULPI PHYs? Reachable, and the PHYs are not the blocker.** In order, the real
obstacles are (1) closing timing at 60 MHz, (2) if and only if you insist on
Linux running *on* the ECP5, a HyperRAM Wishbone controller that does not exist.
Routes 5a and 5b get a Linux machine mounting the stick without either.

---

## 6. Two integration details that will bite

### 6.1 Cache coherency is a real problem here

The CPU has an L1 data cache (`--with-lsu-l1`, 64 sets × 1 way) and the 64 KiB
block RAM at `0x0` is cached. **An OHCI DMA write into that RAM is invisible to
the CPU.** LiteX's own `make.py` sets `with_coherent_dma = True` whenever
`usb_host` is in a board's capabilities, precisely because of this — an extra,
unmeasured hardware cost we would not want to pay.

The cheap answer is a **small dedicated DMA scratch region declared
non-cacheable in the PMA** (`vexii_cpu.py:110-113` already declares
`base=f0000000 size=10000000 main=0`). 4 KiB is ample for first light — HCCA is
256 bytes, EDs and TDs are 16 bytes each, and an FS packet is 64 — and costs
2 EBRs of the 14 free. Decide this before writing the driver, not after
debugging it.

### 6.2 Wishbone bursts

`BmbToWishbone` drives `CTI = 010` for incrementing bursts and `111` to end them
(`BmbToWishbone.scala:38`), with 64-byte bursts at the default
`dmaLengthWidth = 6`. A classic Wishbone slave that ignores CTI and simply ACKs
each cycle is still compliant — it just runs slower. At FS's 1.22 MB/s against a
120 MB/s bus, that is irrelevant. **Not a blocker; do not spend time on it.**

---

## 7. What could not be determined

- **In-situ area and fmax.** Everything in §2 is standalone, and the SoC has not
  been built with the OHCI in it. This is M0 and it is one build away.
- **Serial-mode AC timing. There is none published.** The USB334x datasheet's
  Table 4-4 covers synchronous ULPI only. There is **no** `tx_enable`→DP/DM
  turn-on delay, **no** DP/DM→`rx_dp`/`rx_rcv` propagation figure, and **no**
  skew spec, anywhere in the document. For a host that must meet USB 2.0
  full-speed turnaround and EOP budgets this is an unquantified risk that can
  only be settled on hardware. It is the single strongest argument for keeping
  the PMOD fallback (§3.7) alive.
- **Low-speed polarity and edge rates in serial mode.** Table 6-9 says `tx_data`
  is "Tx differential data on DP/DM" without stating whether the PHY applies the
  LS inversion and slew implied by `XcvrSelect=10b`. The mandatory
  configure-then-enter sequence implies it does. Never stated. **Do full speed
  first.**
- **Whether serial mode is sanctioned in a host configuration.** §6.6's stated
  motivation is device-flavoured, but the required pre-configuration explicitly
  includes the Host FS and Host LS rows of Table 5-1 and there is no exclusion.
  Strongly implied, not confirmed.
- **Whether Cynthion's ID strap matters.** Pin 18 is hard-tied to +3V3 on all
  three PHYs (per the KiCad net), and Table 2-2 says "For Host applications ID is
  grounded". §5.7.1.1 has the ID pin feeding only the `IdGnd` comparator and its
  interrupt, and nothing in the datasheet gates terminations or transmit on it —
  so host mode should be reachable by register writes alone. **No documented
  interlock, but unverified in silicon**, and it is the second thing to suspect
  if M1 fails.
- **Current-dev SpinalHDL's actual footprint.** §2's numbers are from LiteX's
  2021 netlist. The `fifoBytes` → `storageBursts` change should make it *smaller*
  in FFs and block RAM, but it was not regenerated.
- **Two-port cost.** No `_Pc2_` netlist exists and sbt was not run. A second
  port is a second `UsbLsFsPhy` port FSM, filter and `HcRhPortStatus` set —
  a few hundred LUT4, estimated, not measured.
- **Whether the r1.4 release schematic agrees with `repos/cynthion-hardware`
  at `e5cf493`** on the PMOD series resistors, the absence of 5 V, and the
  `TARGET_FS_MONITOR` nets. See §3.7.
- **Whether `core_usb_host` + `core_ulpi_wrapper` has ever run as a high-speed
  host** on real hardware. Both READMEs stop short of claiming it.

---

## 8. Recommendation

1. **M0 now**, because it is free and it is the only step that can invalidate
   everything after it.
2. **M1 next**, because it costs one register sequence and settles the single
   largest unknown in this document — whether the USB3343's undocumented-timing
   serial mode actually works as a host on this board.
3. Then M2 → M3. **First light is a device descriptor printed over the console,
   read through a standard OHCI by a bare-metal driver.**
4. Keep the PMOD adapter (§3.7: a socket, two 15 kΩ resistors, and 5 V) as the
   fallback. Do not build it until M1 has failed.
5. For "Linux mounts a stick", plan on **5a** — re-export as a USB MSC device on
   a port we already drive — and treat Linux-on-the-SoC as the separate, larger
   programme it is, gated on HyperRAM (#90/#92) rather than on anything USB.

This does not displace `docs/usb-host-proposal.md`. That document's
recommendation — vendor GUH's SIE for 480 Mbps with a bespoke register interface
— remains the right answer to the question **#105** asked. This is the answer to
a different and easier question, and the two can coexist on the same board: GUH
speaks UTMI through `UTMITranslator`, this speaks six wires in serial mode, and
they are mutually exclusive only in that both want `target_phy`.
