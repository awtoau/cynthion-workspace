# USB Host Mode on Cynthion at 480 Mbps — Proposal

**Status:** proposal, no hardware exercised
**Date:** 2026-07-30T19:30:00+10:00; integration design added 2026-08-03T01:30:00+10:00
**Consolidates:** awtoau/cynthion-workspace#101, #105
**Target:** LFE5U-12F (24288 LUT, 56 BRAM), Cynthion r1.4

Sections 0-10 are the original feasibility study: can this board host, and does
anything exist to build on. **Section 11 onwards is the integration design** —
what it takes to attach a host engine to *this* SoC, and it corrects five things
sections 0-10 got wrong. Read section 11.1 before trusting anything above it.

---

## 0. Executive summary

Four findings drive everything below.

1. **LUNA has no host stack.** Not a partial one — the USB 2.0 protocol layer is
   structurally device-only. The ULPI/PHY layer, however, already exposes every
   host control signal it would need.
2. **The board can host.** Cynthion r1.4 can source VBUS to TARGET A and has
   current/voltage monitoring plus Type-C CC control. This is not a board blocker.
3. **A working LUNA-based host exists in another project** — Tiliqua's
   `usb_host.py` (merged, 691 lines) — but it is **full-speed (12 Mbps) only**,
   and a GSG maintainer has pointed at it as *the* starting point for Cynthion.
4. **480 Mbps is where the cost concentrates.** Getting to 12 Mbps host is a
   port-and-adapt job. Getting from 12 to 480 Mbps requires the host side of the
   high-speed chirp handshake, which nobody in this ecosystem has written.

The distance from "no host" to "12 Mbps host" is far shorter than the distance
from "12 Mbps host" to "480 Mbps host". Recommendation is in section 6.

---

## 1. What LUNA has today

### 1.1 It is device-only, by construction

`repos/luna/luna/gateware/usb/usb2/` contains `device.py`, `control.py`,
`endpoint.py`, `request.py`, `transfer.py`, `descriptor.py`, `reset.py`,
`packet.py`. There is no `host.py`, no root-hub model, no SOF generator, no
transaction scheduler, no pipe management, no enumeration driver.

The decisive line is `luna/gateware/usb/usb2/device.py:382`:

```python
# Disable our host-mode pulldowns; as we're a device.
self.utmi.dm_pulldown.eq(0),
self.utmi.dp_pulldown.eq(0),
```

The device stack does not merely omit host support; it actively drives the
host-mode pulldowns off. Host mode is a deliberate non-goal of `USBDevice`.

### 1.2 The asymmetry is in the packet layer

`packet.py` names look symmetric but are not. Each block is one direction only:

| Block | Role | Reusable for host? |
|---|---|---|
| `USBTokenDetector` | **receives** SETUP/IN/OUT/SOF tokens | No — host must *generate* tokens |
| `USBHandshakeDetector` | receives ACK/NAK/STALL | Yes, directly |
| `USBHandshakeGenerator` | sends ACK/NAK/STALL as a device | Partly — host only ever sends ACK |
| `USBDataPacketReceiver` / `Deserializer` | receives DATA0/1 | Yes, directly |
| `USBDataPacketGenerator` | sends DATA0/1, does CRC-16 | Yes, directly |
| `USBDataPacketCRC` | CRC-16 both directions | Yes, directly |
| `USBInterpacketTimer` | bus turnaround timing | Yes — already has HS/FS/LS constants |

So roughly the lower half of the packet layer is direction-agnostic and reusable.
The missing pieces are a **token generator** and everything above it.

`USBInterpacketTimer` is worth calling out as a genuine asset: it already encodes
HS, FS and LS rx-to-tx delays and tx-to-rx timeouts from the USB 2.0 and ULPI 1.1
specs (`_HS_RX_TO_TX_DELAY`, `_HS_TX_TO_RX_TIMEOUT = 92` ULPI cycles, etc.).
Bus turnaround timing at high speed is a classic source of silent failure, and it
is already done and in use.

### 1.3 The HS chirp handshake is device-side only

`reset.py`'s `USBResetSequencer` implements the **device** half of the high-speed
detection handshake: states `DEVICE_CHIRP` (drive chirp K ~2 ms), then
`AWAIT_HOST_K` / `IN_HOST_K` / `AWAIT_HOST_J` / `IN_HOST_J`, counting three valid
K/J pairs before declaring `IS_HIGH_SPEED`.

The host half is the mirror image and **does not exist**: assert SE0, watch for a
device chirp K, then drive at least three alternating K/J pairs, then switch to HS
terminations. This is the single most important gap for a 480 Mbps host, and it is
discussed in section 3.2.

### 1.4 The ULPI layer, by contrast, is already host-capable

`luna/gateware/interface/ulpi.py` exposes the full UTMI+ control surface, and the
docstrings explicitly anticipate host use:

```
I: dp_pulldown -- when set, enables a 15kR pull-down on D+; intended for host mode
I: dm_pulldown -- when set, enables a 15kR pull-down on D+; intended for host mode
```

Also present: `op_mode` (including `RAW_DRIVE` for direct bus driving),
`xcvr_select`, `term_select`, `chrg_vbus` / `dischrg_vbus`, and the OTG control
register (0x0A) written with `id_pullup`, `dp_pulldown`, `dm_pulldown`,
`dischrg_vbus`, `chrg_vbus`, `use_external_vbus_indicator`.

**Conclusion for section 1:** the PHY abstraction needs no work for host mode. The
protocol layer above it needs to be written. This is a favourable split — the
fiddly, timing-critical, vendor-specific layer is the part already done.

---

## 2. What the board supports

From `repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`.

### 2.1 Three full ULPI PHYs

`control_phy`, `aux_phy`, `target_phy` are each declared as a complete
`ULPIResource` — 8-bit data, clk (FPGA-driven, `clk_dir='o'`), dir, nxt, stp,
rst. All three are USB 2.0 HS-capable PHYs with bidirectional ULPI. Nothing about
the ULPI wiring restricts direction of the USB role; ULPI `dir` is bus turnaround,
not host/device role.

### 2.2 VBUS sourcing exists — this is the board fact that matters

```python
# VBUS on each of the Type-C ports can be connected to TARGET A through
# a bidirectional switch. If any of these switches is enabled, TARGET A
# is considered an output.
Resource("target_c_vbus_en",   0, Pins("K5", ...))
Resource("control_vbus_en",    0, Pins("L1", ...))
Resource("aux_vbus_en",        0, Pins("L2", ...))
Resource("target_a_discharge", 0, Pins("K4", ...))
```

Plus `control_vbus_in_en` / `aux_vbus_in_en` for input shutoff.

A host must source VBUS to the device it hosts. Cynthion can: VBUS from CONTROL or
AUX can be switched through to TARGET A, and TARGET A can be actively discharged.
`target_a_discharge` matters more than it looks — safe VBUS sequencing on
disconnect needs it, and it is present.

Supporting infrastructure is also there:

- `power_monitor` — I2C voltage/current monitor, so overcurrent detection and the
  spec-required port power management are achievable.
- `target_type_c` / `aux_type_c` — I2C Type-C controllers with `int` and `fault`
  pins, giving CC line control (Rp/Rd role advertisement) and fault reporting.

### 2.3 No ID pin, and none needed

There is no mini/micro-B `ID` pin, because r1.4 is Type-C. Role is established by
CC line resistors via the Type-C controllers, not by an ID pin. The ULPI
`id_pullup` bit exists in the OTG register but is irrelevant here. This is a
non-issue, noted only because "check the ID pin" was raised as a question.

### 2.4 Board verdict

**The board can act as a USB host.** VBUS sourcing, discharge, current monitoring,
CC role control and an HS-capable PHY are all present on TARGET. There is no
hardware blocker. Everything remaining is gateware plus firmware.

Caveat not verified here: whether the VBUS switch path and TARGET A current limit
meet the 500 mA / 900 mA a spec-compliant host should offer. That is a schematic
and datasheet question, and no hardware was exercised for this proposal. For
hosting a single low-power device it is very unlikely to be a problem.

---

## 3. Prior art — the finding that changes the answer

Section 1 concluded that LUNA has no host stack. That would normally imply a
large from-scratch effort. It does not, because the work has substantially been
done outside LUNA.

### 3.1 GSG has not declined host mode — they have deferred it

`greatscottgadgets/cynthion#230`, "Gateware host mode implementation?", is closed,
but not as a refusal. martinling (GSG member) replied:

> There is a working example of host mode gateware using LUNA
> [in the Tiliqua project](https://github.com/apfaudio/tiliqua/pull/65), which you
> could use as a starting point. At some point we'd like to bring more of that
> functionality into LUNA and add an example for doing this with Cynthion, but
> we're not there yet.

So upstream considers host mode desirable, unimplemented in LUNA, and already
demonstrated elsewhere. `cynthion#174` ("Can Cynthion emulate a host?") was closed
with the same substance: technically possible, no support provided.

### 3.2 `apfaudio/guh` — a USB2 **high-speed** host engine for LUNA

Tiliqua PR #65 (merged 2024-11-05) was the full-speed-only ancestor. That code has
since been extracted and substantially extended into a standalone library,
**`apfaudio/guh`** ("Gateware USB Host"), by the same author. This is the single
most important artifact for this proposal.

From its README:

> `guh` is an experimental gateware library (written in Amaranth HDL), for
> building **custom USB2 high-speed and full-speed host engines** for FPGAs. It
> builds heavily on LUNA, which, whilst being extremely useful for implementing
> USB devices, does not implement USB Host. Eventually (perhaps after a lot of
> cleanup!) `guh` hopes to become part of LUNA in some form.

> As of now, `guh` can enumerate USB2 high-speed (480Mbit) and full-speed (12Mbit)
> devices in pure gateware. [...] I have tested this on USB thumbdrives at 480Mbit
> HS, and MIDI/HID devices at 12Mbit FS.
>
> The enumeration speed is dynamic: the same gateware can support both HS and FS
> devices. As of now, USB hubs can be enumerated *but not the devices behind them*.

Structure (all Amaranth, BSD-3-Clause — the same licence as LUNA):

| Module | Lines | Role |
|---|---|---|
| `guh/usbh/sie.py` | 673 | transaction engine: token packets, SOF generation, SETUP/IN/OUT |
| `guh/usbh/enumerator.py` | 479 | reset, address assignment, descriptor fetch, handoff |
| `guh/usbh/descriptor.py` | 224 | descriptor parsing, endpoint extraction |
| `guh/usbh/reset.py` | 215 | bus reset + **HS/FS speed detection (host-side chirp)** |
| `guh/engines/msc.py` | 743 | mass-storage engine (read-only), runs at HS |
| `guh/engines/keyboard.py` | 163 | HID keyboard engine |
| `guh/engines/midi.py` | 133 | MIDI engine |

Critically, `guh/usbh/reset.py` implements exactly the gap identified in section
1.3 — the **host side** of the high-speed chirp handshake. Its states are
`WAIT-DEVICE-CHIRP-END`, `WAIT-DEVICE-CHIRP-END-SE0`, `SEND-HOST-CHIRP-K`,
`SEND-HOST-CHIRP-J`, alternating K/J and falling back to `USBHostSpeed.FULL` if no
device chirp arrives. This is the hard part of a 480 Mbps host, and it is written.

### 3.3 It already targets Cynthion

This is not a port that needs inventing. GUH's own CI (`.github/workflows/ci.yml`)
builds every example for **both** Tiliqua and Cynthion:

```yaml
platform:
  - name: tiliqua
    env: guh.platform.tiliqua:TiliquaR4R5Platform
  - name: cynthion
    env: cynthion.gateware.platform:CynthionPlatformRev1D4
example: [midi_host, keyboard_host, msc_host]
```

The examples request `control_vbus_en` and `target_phy` — precisely the r1.4
resources confirmed in section 2 — and even carry Cynthion-specific handling
(`# Cynthion has tristate UART TX`). The README documents the Cynthion upload
path via `apollo force-offline`.

Caveat from the README, quoted because it matters for VBUS: the examples
"hard-wire the VBUS output to ON, because this repository does not include drivers
for the I2C Type-C CC controller (TUSB322I)". Proper VBUS/CC handling exists in the
full Tiliqua repo, not in GUH. On Cynthion this is a gap to close (see section 5).

### 3.4 Other prior art, briefly

- **daisho** (GSG): archived since 2015, USB 3.0 SuperSpeed on a different FPGA
  platform. Not applicable.
- **Glasgow**: no USB host gateware found.
- Forks/derivatives of LUNA (`delan/sol_usb`, `hansfbaier/liteusb`,
  `jevinskie/liteluna`, `lambdaconcept/lambdalib`) all carry the same device-only
  `dp_pulldown.eq(0)` line. None implement host.
- `tomverbeure/usb_system`, `Nickster90s/avb-usb-host`: ULPI/UTMI plumbing only.

**GUH is the only credible base, and it is a strong one.**

---

## 4. Measured fabric cost

Not estimated — synthesised. `scripts/usb-host-area.py` builds the GUH examples
for `CynthionPlatformRev1D4` with the workspace toolchain (Yosys 0.65,
nextpnr-ecp5 0.10) and parses nextpnr's utilisation and timing report. Synthesis
and place-and-route only; **no board was touched**.

| Example | Speed | LUT (of 24288) | BRAM | fmax vs 60 MHz |
|---|---|---|---|---|
| `keyboard_host` | FS (HID) | **2184 (8%)** | 0 | 127.57 MHz PASS |
| `midi_host` | FS (MIDI) | **2244 (9%)** | 0 | 150.44 MHz PASS |
| `msc_host` | **HS, 480 Mbps** | **4103 (16%)** | 1 | 117.45 MHz PASS |

Raw data: `tmp/host-research/area-results.json`; log: `tmp/logs/usb-host-area.log`.

Observations that matter:

- **A complete 480 Mbps USB host fits in 16% of the LFE5U-12F.** That includes the
  ULPI PHY interface, transaction engine, chirp/speed detection, enumerator,
  descriptor parser, and a mass-storage class engine with its hexdump and UART.
- The bare host core is smaller than 4103 LUT. `msc_host` carries the MSC engine
  (743 lines, the largest module) plus SCSI/CBW-CSW handling. A host core plus a
  thin transfer interface should land nearer 2500-3000 LUT, judging by the FS
  examples at ~2200 LUT including their own class engines and lookup tables.
- **Timing closes with roughly 2x margin** on the 60 MHz ULPI domain. High-speed
  host operation is not marginal on this part.
- BRAM use is negligible (0-1 of 56), which is the pleasant surprise: BRAM, not
  LUTs, is usually the binding constraint when adding a CPU.

### 4.1 Does it fit alongside a CPU?

Workspace figures for comparison (`docs/gateware-architecture-plan.md`): best CPU
config **6342 LUT and 14 of 56 BRAM**; current test bitstreams 4000-7400 LUT.

| Combination | LUT | % of 24288 | BRAM |
|---|---|---|---|
| HS host engine (as `msc_host`) | 4103 | 17% | 1 |
| RISC-V CPU (best measured) | 6342 | 26% | 14 |
| **Host + CPU** | **~10400** | **~43%** | **~15 / 56** |

That leaves roughly 57% of the fabric and 41 BRAM free. **A 480 Mbps host and a
RISC-V CPU coexist comfortably.** Adding the existing *device* stack on a second
PHY as well (for a host-plus-device or MITM configuration) would still be
plausible, though that combination has not been synthesised here and should be
measured before being promised.

The fabric budget, which was the stated worry, is not the constraint.

---

## 5. What is still missing

GUH is not a finished product, and its author says so plainly ("experimental",
"quite the collection of (functional) hacks", "all interfaces are subject to
change"). Gaps, in rough priority order for Cynthion:

1. **VBUS and Type-C CC control.** GUH's examples hard-wire `control_vbus_en` on
   and ship no TUSB322I driver. Cynthion r1.4 has I2C Type-C controllers on
   TARGET and AUX plus a power monitor; driving them properly (role advertisement,
   overcurrent via `fault`, `target_a_discharge` on teardown) is Cynthion-specific
   work nobody has done. This is the largest genuinely new piece.
2. **No hub support past the first tier.** Hubs enumerate; devices behind them do
   not. A real host needs hub port control and address routing.
3. **Error handling is incomplete.** The README recommends a watchdog to reset the
   enumeration FSM when it stalls. `usb_host.py`'s ancestor carried
   `# FIXME: tolerate rx timeout`. Babble, STALL recovery, retry limits and
   data-toggle resynchronisation all need hardening.
4. **Only three class engines exist** (MSC read-only, HID keyboard, MIDI). Any
   other device class is new work.
5. **No CPU-facing controller.** For software-driven hosting you need a register
   or DMA interface. GUH has an experimental `guh/periph/` Wishbone/CSR peripheral
   with a Rust HAL in `rs/`, requiring `amaranth_soc` — promising but unproven
   here, and not yet integrated with this workspace's SoC.
6. **Split transactions are absent**, so FS/LS devices behind an HS hub will not
   work. Follows from (2).
7. **It is one person's experimental library**, explicitly not accepting
   contributions, with no releases. Depending on it is a real supply-chain
   consideration; vendoring is the mitigation.

Verification status also deserves precision: 27 of GUH's tests pass locally,
including a full host-vs-simulated-device integration test that asserts
`detected_speed == USBHostSpeed.HIGH` for both the MIDI and MSC engines. That is
meaningful evidence the HS path works in simulation. **It is not a hardware
result** — none was produced for this proposal.

---

## 6. Three options

### Option A — Vendor GUH and drive it from gateware
Bring GUH in under `sources/` or as a submodule, build the `msc_host` /
`keyboard_host` examples for r1.4, then add the TUSB322I/VBUS driver and a
watchdog. Host behaviour stays in gateware; a class engine streams to UART, the
CPU, or PSRAM.

- **Cost:** small. The bitstreams already build. Realistically days, not weeks,
  to a demonstrated 480 Mbps enumeration, plus the VBUS/CC work.
- **Area:** ~4100 LUT measured, 1 BRAM. Fits with the CPU.
- **Risk:** low technically. Main risks are the VBUS/CC gap and depending on an
  experimental upstream.
- **Ceiling:** limited to implemented class engines; no hubs, no arbitrary
  software-defined transfers.

### Option B — GUH plus a CPU-visible host controller
Option A, then expose GUH's SIE as a CSR/DMA peripheral so firmware (Rust on the
RISC-V core, reusing moondancer patterns) issues transfers. GUH's `guh/periph/`
and `rs/` are the starting point.

- **Cost:** moderate. Peripheral bring-up, DMA, firmware transfer scheduling,
  descriptor handling in software rather than gateware FSMs.
- **Area:** ~10400 LUT combined (host + CPU), ~15 BRAM. Comfortable.
- **Risk:** medium. Requires `amaranth_soc` integration and is the least proven
  part of GUH.
- **Payoff:** genuine generality — arbitrary devices and classes without new
  gateware per class. This is what "a USB host on Cynthion" usually means.

### Option C — Zephyr's USB host stack over a gateware UHC driver
Run Zephyr on the RISC-V core and write a UHC driver whose backend is GUH's SIE,
inheriting Zephyr's enumeration, hub and class drivers.

- **Cost:** large. See section 7.
- **Area:** Option B's fabric plus a CPU able to run Zephyr, with materially more
  BRAM/external memory for the stack and its buffers.
- **Risk:** high.

---

## 7. Verdict on the Zephyr hypothesis

The hypothesis from #105 was that the Zephyr USB layer is the easiest route. On
the evidence it is **not the easiest route, but the mismatch is not fatal either.**
It is the right destination if the goal is a general-purpose host, and the wrong
starting point.

**The mismatch is real and specific.** Zephyr's host support sits behind the UHC
API (`include/zephyr/drivers/usb/uhc.h`, ~620 lines). Every in-tree UHC driver
binds to a *hardware* host controller:

```
uhc_dwc2.c   uhc_max3421e.c   uhc_mcux_ehci.c   uhc_mcux_ohci.c
uhc_mcux_ip3516hs.c   uhc_mcux_khci.c   uhc_virtual.c
```

There is no "controller in fabric" driver. `uhc_virtual.c` is a loopback onto
Zephyr's virtual USB bus for host-stack testing under `native_sim` — it is not a
bridge to gateware.

**Why it is surmountable.** The UHC API is small and well-shaped for this:
`uhc_init/enable`, `uhc_bus_reset`, `uhc_sof_enable`, `uhc_bus_suspend/resume`,
`uhc_ep_enqueue/dequeue`, plus an event callback. Those map almost one-to-one onto
what GUH already provides — bus reset with chirp, SOF generation, and SETUP/IN/OUT
transactions on an endpoint. Writing `uhc_cynthion.c` against a GUH-backed
peripheral is a legible project, not a research problem. And the payoff is exactly
the list of gaps in section 5: Zephyr brings hub support, class drivers, error
recovery and transfer scheduling that GUH lacks.

**Why it should not be first.** It front-loads the costs:

- Zephyr must be brought up on the VexiiRiscv core first — board port, device
  tree, timers, interrupts, console — none of which is USB work, and none of
  which exists in this workspace today.
- The RAM footprint of Zephyr plus its USB host stack is a different memory
  problem from the current bare-metal firmware, on a part with 56 BRAM.
- You still need Option B's CPU-visible host controller as the driver's substrate.
  **Zephyr does not remove any gateware work; it adds a software layer on top of
  it.**
- Debugging spans three layers at once (gateware SIE, peripheral, Zephyr driver)
  with no known-good reference for the bottom two.

So the ordering is the actual finding: Zephyr is a **superset** of Option B, not
an alternative to it. Its stack cannot help until a controller exists for it to
drive, and building that controller is Option B. Treat Zephyr as a later phase
that Option B is deliberately designed to enable — worth doing if the goal is
hosting arbitrary devices, and pointless as a shortcut to first light.

One correction to the framing in #101, which proposed adapting
"zephyr/freebsd/openbsd/linux code [...] but written in rust" and asked whether
xHCI-style registers are needed. Porting an OS host stack is the Option C tail,
not the head, and xHCI is the wrong model: GUH's author addresses this directly,
using "host **engine**" rather than "host controller" precisely because
"modern USB host controllers like xHCI are complex beasts with sophisticated
control and DMA interfaces". Do not implement xHCI. A purpose-built transfer
interface is far cheaper and entirely sufficient.

---

## 8. Recommendation

**Do it, via Option A now and Option B next. Do not start with Zephyr.**

This is worth doing specifically *because* the expensive parts already exist. The
question in #101/#105 was framed as "can Cynthion host at 480 Mbps and what would
it take", with an implicit fear that the answer was a from-scratch host stack that
would not fit. Both halves of that fear are unfounded:

- The board can host: VBUS switching to TARGET A, discharge, current monitoring
  and CC control are all present.
- A 480 Mbps host engine exists, is BSD-3 licensed, already builds for
  `CynthionPlatformRev1D4`, and measures **4103 LUT (16%) and 1 BRAM** with 2x
  timing margin — leaving ample room for the RISC-V CPU.

Suggested sequence:

1. Vendor GUH (submodule or `sources/`), pin the commit, run its test suite in
   `scripts/check.py`. 27 host tests pass today.
2. Build `msc_host` and `keyboard_host` for r1.4 and bring up on hardware. First
   milestone: enumerate a thumbdrive at high speed and hexdump block 0.
3. Write the TUSB322I/VBUS/CC driver and a watchdog — the real Cynthion-specific
   gap, and a candidate to contribute back.
4. Only then decide between staying gateware-side (A) or exposing a CPU-facing
   controller (B). Revisit Zephyr once B works, if hubs and arbitrary classes are
   actually needed.

Track area at every step against the 24288 LUT / 56 BRAM budget with
`scripts/usb-host-area.py`.

### 8.1 What would make this not worth doing

Stated plainly, since a "buy a different device" conclusion was invited. Walk away
if any of these describe the need:

- **You need a spec-compliant, general-purpose host.** Multi-tier hubs, split
  transactions, isochronous audio/video, full error recovery, power negotiation.
  That is a multi-month effort and the wrong use of an LFE5U-12F. A Raspberry Pi
  or any SoC with a real EHCI/xHCI controller does it today for a few dollars.
- **You need USB 3.x SuperSpeed.** Cynthion's USB3 support is device-side and
  incomplete; a 5 Gbps host is out of scope for this part. Daisho was GSG's
  attempt at SuperSpeed and has been archived since 2015.
- **You need guaranteed high-throughput streaming.** 480 Mbps line rate is not
  480 Mbps of delivered payload. Sustained bulk throughput through fabric, PSRAM
  and a 60 MHz domain needs measuring before anything depends on it.
- **You cannot accept an experimental single-maintainer dependency.** GUH has no
  releases, declines contributions, and warns its interfaces will change. If that
  is unacceptable and you also will not fork and own it, there is no cheap path.
- **The actual goal is capture, not hosting.** If the aim is observing traffic,
  Cynthion's existing analyzer already does that better, and a cheap USB hub plus
  a PC is a simpler "host" than any gateware.

The sweet spot where this *is* worth doing is narrow but real, and it is probably
the actual requirement: **hosting one known device at 480 Mbps, on hardware you
already own, with the whole stack visible and instrumentable.** Reading a
thumbdrive, driving a specific peripheral, or building a MITM that is a real host
on one port and a real device on the other. For that, Option A is cheap and the
fabric numbers say it fits.

---

## 9. Issue consolidation

#101 and #105 are the same request: #101 ("Create host USB device on moon?") asks
for FPGA host support and speculates about OS stacks in Rust and xHCI-style
registers; #105 asks to research a 480 Mbps host and proposes Zephyr. Same goal,
different guesses at the route — and this document answers both.

**Recommendation: keep #105 open, close #101 as a duplicate of #105.**

Reasoning: #105 states the measurable requirement (480 Mbps) and the hypothesis to
test, so it reads as the actionable ticket. #101's specifics are the parts this
research contradicts — xHCI is explicitly the wrong model, and porting an OS host
stack is a late phase rather than the starting point — so keeping it open would
preserve a misleading plan. Its one durable instinct ("must be easier to find luna
or other work if possible") turned out to be exactly right and is captured above.

Retitle #105 to something like "USB host mode at 480 Mbps via GUH" and link this
document. Neither issue was closed or edited in producing this proposal, per the
brief.

---

## 10. References

Local:
- `repos/luna/luna/gateware/usb/usb2/` — device-only stack; `device.py:382`
- `repos/luna/luna/gateware/usb/usb2/reset.py` — device-side chirp only
- `repos/luna/luna/gateware/interface/ulpi.py` — host-capable PHY controls
- `repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`
- `scripts/usb-host-area.py`, `tmp/logs/usb-host-area.log`,
  `tmp/host-research/area-results.json`
- Area baselines: `docs/gateware-architecture-plan.md`

Upstream:
- `apfaudio/guh` — https://github.com/apfaudio/guh (BSD-3-Clause)
- `apfaudio/tiliqua` PR #65 — the full-speed ancestor
- `greatscottgadgets/cynthion` #230 (GSG pointing at Tiliqua), #174
- Zephyr UHC API — `include/zephyr/drivers/usb/uhc.h`, `drivers/usb/uhc/`

Note on `import luna`: it resolves to the interpreter's own
`site-packages/luna`, not `repos/luna`. The checkout was read for this analysis; the installed copy is what
the measured builds ran against. Both are device-only.

**No hardware was touched in producing this proposal. All figures are from
synthesis, place-and-route and simulation.**

---
---

# Part II — Integration design

**Date:** 2026-08-03T01:30:00+10:00
**Baseline:** `uart16550-console` at `c154bc3`
**Question:** not "can it host" (sections 0-10 answered that) but "what shape
does it take inside the SoC that now exists".

The answer is smaller than sections 6-8 assumed. **Take `guh/usbh` only — the
transaction engine and the reset/chirp controller — and write enumeration in
firmware.** That is 2080 LUT and *zero* BRAM, against a design with 12100 LUT
and 14 BRAM to spare.

---

## 11. Five corrections to Part I

| # | Part I said | Actually |
|---|---|---|
| 1 | GUH is BSD-3-Clause (§3.2) | True *now*. At our pinned commit `fbd7077` it was not: `guh/periph/msc.py`, `guh/periph/dma.py` and `guh/util/gearbox.py` carried `SPDX-License-Identifier: CERN-OHL-S-2.0` — strongly reciprocal hardware copyleft, on exactly the SoC-facing files Option B wanted. Reported upstream as `apfaudio/guh#1` (opened 2026-07-28), acknowledged as a copy-paste leftover from Tiliqua, **fixed 2026-07-30 in `923c8490`**. Bump the pin before vendoring anything. |
| 2 | "No CPU-facing controller… GUH has an experimental `guh/periph/` Wishbone/CSR peripheral" (§5.5) | `guh/periph/` is two files and both are mass-storage. Its registers are `cmd_lba`, `cmd_blocks`, `cmd_dir`, `capacity`, `block_size` — a block device, not a transfer interface. There is no way to issue a control transfer or reach an arbitrary endpoint from the CPU. The README calls a generic controller a non-goal. **Option B is new work, not a port.** |
| 3 | VBUS is "the largest genuinely new piece"; GUH will "not get this right for Cynthion" (§5.1) | GUH's examples request `control_vbus_en` and drive it high. On r1.4 that is the correct pin: `control_vbus_en` (L1), `aux_vbus_en` (L2) and `target_c_vbus_en` (K5) each switch *that port's* VBUS through to TARGET-A, and any one of them makes TARGET-A a source. GUH sources from CONTROL, which is the port the PC is on. Nothing to fix. |
| 4 | Write "the TUSB322I/VBUS/CC driver" (§8) | Cynthion r1.4 has **FUSB302B**, not TUSB322I, and `firmware/cynthion-soc/src/fusb302.rs` + `typec.rs` already drive both of them over the muxed I2C bus with a PLIC source each. The host-mode gap is one register field — presenting Rp on TARGET via `SWITCHES0` — not a driver. `fusb302.rs:26-38` already documents why the pull-downs are deliberately off. |
| 5 | "A complete 480 Mbps USB host fits in 16%… 4103 LUT" (§4) | That is `msc_host`, an entire example: host + SCSI engine + hexdump + UART + LUNA's clock generator. The host **core** is 2080 LUT and 0 BRAM. See §12. |

Also updated from Part I §3.1 and §7: nothing has changed upstream. LUNA has no
host commits since 2025-01-01; `greatscottgadgets/cynthion#230` and `#174` have
had no comments since 2025-05 and 2024-09. Zephyr gained `uhc_dwc2.c`
(2026-01-08, Apache-2.0) but it targets the Synopsys DWC2 register file;
`uhc_virtual.c` remains a `native_sim` loopback against Zephyr's own virtual
*device* controller, not a shim a soft controller can hide behind. **No `uhc_*`
driver for any FPGA soft core exists.** Part I §7's verdict stands and is now
better evidenced.

---

## 12. Measured: the host core alone

`scripts/usb-host-core-area.py`. Each configuration is a top level whose only
job is to keep synthesis from folding the core away: every `USBSIEInterface`
input is driven from a free-running LFSR, every output is XOR-reduced onto one
LED pin. Clocking is `VariableClockDomainGenerator(sync_mhz=60)` — the SoC's
generator, not LUNA's — so the domains match the design this would go into. The
baseline is that scaffolding with no host in it, and is subtracted.

| Build | LUT | FF | BRAM | LUTRAM | fmax on the ULPI clock |
|---|---|---|---|---|---|
| baseline (CRG + LED) | 33 | 24 | 0 | 0 | 398.72 MHz |
| `USBSIE(fifo_depth=512)` | 2113 | 458 | **0** | 96 | 125.55 MHz PASS at 60 |
| `+ USBHostEnumerator + parser` | 2943 | 552 | **0** | 128 | 129.22 MHz PASS at 60 |

Deltas: **SIE 2080 LUT / 0 BRAM**; SIE + enumerator **2910 LUT / 0 BRAM**.
`fifo_depth=512` is not the GUH default of 64 — it is what a 512-byte
high-speed bulk packet requires, so the number is not flattered.

Raw data `tmp/host-core-area/core-area-results.json`, log
`tmp/logs/usb-host-core-area.log`. Synthesis and place-and-route only; no board
was opened.

### 12.1 The pleasant surprise is the zero

Both 512×8 FIFOs went to **LUT RAM**, not block RAM — 96 `TRELLIS_RAMW` cells
of 3036. BRAM was the binding constraint on this part (42 of 56 in use), and the
host core does not touch it.

### 12.2 Does it fit

Current SoC, `tmp/vexii_hello/build/top.tim` (2026-08-03T00:25, LFE5U-12F,
CABGA256, speed 8):

| | now | + SIE | + SIE and enumerator |
|---|---|---|---|
| LUT4 (of 24288) | 12201 (50%) | ~14300 (59%) | ~15100 (62%) |
| BRAM (of 56) | 42 (75%) | 42 | 42 |
| LUT RAM (of 3036) | 159 (5%) | 255 (8%) | 287 (9%) |

Plus roughly 200-400 LUT for the CSR/FIFO shim of §14. So the SoC with a
480 Mbps host engine lands near **62-64% of the fabric with the BRAM budget
untouched**.

### 12.3 Timing is not the risk people expect

| clock | current fmax | constraint | margin |
|---|---|---|---|
| `sync` (CPU, Wishbone) | 71.58 MHz | 60 | 19% |
| `usb` (ULPI) | 94.30 MHz | 60 | 57% |
| `jtck` | 161.08 MHz | 20 | 8x |

The host core belongs in `usb`, which has three times the margin of `sync`, and
closes at 125-129 MHz standalone. `sync` is the constrained domain and it
carries the CPU — which is why §13 puts the host in `usb` and pays for a domain
crossing rather than renaming the core into `sync`.

Two caveats. Standalone fmax is not in-situ fmax: at 62% occupancy the router
has less room, and `18c1fa5` established that on this design the critical path
is dominated by routing, not logic (13.64 ns of 16.45 ns before the fix). And
placement here is stochastic — `scripts/soc_timing_sweep.py` exists because a
±9 MHz spread has been observed. **The in-situ number has to be measured, and
that is the first step in §18.**

What `18c1fa5` implies for another decoder window is favourable: the Wishbone
round trip now terminates at `RegisteredResponse`'s flip-flop
(`ecp5-test/riscv/wishbone_pipe.py:96`), so a ninth subordinate adds one address
comparator and one leaf in the ACK gather to a path with ~8 ns of budget rather
than 16. The condition is that the new subordinate's own ACK must be
combinationally cheap — which a CSR multiplexer is.

---

## 13. Where it attaches, and the collision with #120

`target_phy` is the only candidate: `aux_phy` carries the CDC-ACM console
(`vexii_hello_soc.py:958`) and `control_phy` is shared with Apollo. It is also
the port the ULPI register window of #120 sits on, and that is a hard conflict
today, for a reason more basic than bus arbitration.

### 13.1 The conflict is ownership, not arbitration

- `platform.request("target_phy")` may be called once. `vexii_hello_soc.py:1288-1298`
  already calls it and drives `clk`, `rst`, `stp`, `data.o`, `data.oe`
  combinationally from `UlpiRegisters`. There is no mux point.
- `ulpi_window.py:250` hard-wires `data_oe = ~dir_i`, and the window waits for
  `dir` to fall before driving. A host engine receiving a packet holds `dir`
  high, so the window's 4096-cycle (68 µs) timeout fires — a working system that
  reports a broken PHY.
- `target_phy.rst` is tied to `ResetSignal("usb")`, so a host stack cannot
  re-initialise the PHY.

This is #125's point exactly: `ULPIRegisterWindow` is an initialisation-time bus
**master** and cannot coexist with a stack that owns the PHY.

### 13.2 The fix is re-parenting, not arbitration

LUNA's `UTMITranslator` — which GUH's `USBSIE` instantiates
(`guh/usbh/sie.py:346`) — already contains a `ULPIRegisterWindow` and a
`ULPIRxEventDecoder` (`repos/luna/luna/gateware/interface/ulpi.py:805-808`). So
once the host owns the PHY there is already a register window inside it. Do not
add a second master; expose the one that is there.

There is a small, well-shaped upstream patch here:

- `UTMITranslator`'s **docstring already promises** `address`, `read_data`,
  `write_data`, `manual_read`, `manual_write` (`ulpi.py:683-687`).
- Its `__init__` (`ulpi.py:718`) **does not create them**. The ports are
  documented and absent.
- `ULPIControlTranslator` ties `register_window.read_request` to 0
  (`ulpi.py:524`) and drives the window only from an `m.If`/`m.Elif` chain over
  its own registers, with a free `m.Else()` branch at `ulpi.py:519`. That branch
  is where a manual/CPU-driven access goes.

Implementing what the docstring already claims turns `UlpiRegisters` from a
competing master into a client of the translator, and it is a candidate for
`docs/upstreamable-patches.md` in its own right — the bug is that upstream
documents an interface it does not provide.

### 13.3 And it settles #125's open question

#125 asks which PHY the RX CMD status peripheral should watch and who owns it.
`UTMITranslator` already decodes RX CMDs and exposes `line_state`, `vbus_valid`,
`session_valid`, `session_end`, `rx_error`, `host_disconnect`, `id_digital`
(`ulpi.py:694-697`). So the status peripheral taps the translator's outputs, is
instantiated once per translator, and needs no bus access — which is what #125
argued for on first principles, now with a named attachment point.

### 13.4 A host and the analyzer can share one translator

The analyzer attaches a bare `UTMITranslator` to `target_phy` in non-driving
mode (`repos/cynthion/.../analyzer/top.py:277-278, 391-394`). The host attaches
one too. They cannot both `platform.request` the PHY — but they do not need to:
`USBSIE` exposes `self.utmi`, and `USBAnalyzer` consumes UTMI-level signals.
One translator, two consumers. That is the cheapest route to "generate traffic
and capture it in the same bitstream", and it is worth designing for now rather
than retrofitting.

---

## 14. Clocking

GUH is single-domain and says so: `guh/periph/msc.py:62` warns "this currently
assumes 'sync' and 'usb' clock domains are the same". There is no `AsyncFIFO`
anywhere in GUH.

Our SoC has `sync` and `usb` both at 60 MHz but from **different PLL outputs**
(CLKOP and CLKOS, `repos/apollo/apollo_fpga/gateware/variable_clock.py:295-330`),
so nextpnr treats them as unrelated clocks — `usb -> <async>` is already the
worst cross-domain delay in the current build at 11.83 ns.

| Option | Cost | Verdict |
|---|---|---|
| Host in `usb`, cross at the register/FIFO boundary | one four-phase handshake for control, one `AsyncFIFOBuffered` per data direction | **recommended** |
| `DomainRenamer({"usb": "sync"})` over the whole core | zero CDC; GUH's own tests do this (`tests/test_integration.py:39-40`) | adds 2100 LUT to the domain with 19% margin that also carries the CPU |

Both idioms are already in the tree and proven: the four-phase toggle handshake
at `ulpi_window.py:235-311`, and `StreamBuffer` wrapping `AsyncFIFOBuffered` at
`ecp5-test/riscv/stream_buffer.py:96`, in use at `vexii_hello_soc.py:988-991`.

Use the handshake for the transfer registers (a few crossings per transfer, so
latency is free) and the async FIFO for the byte streams (where a per-byte
handshake would be the throughput ceiling instead of the bus).

---

## 15. The software interface

**Not a descriptor ring, and emphatically not xHCI.** A transfer register plus a
FIFO port, because that is already the shape of `USBSIEInterface`
(`guh/usbh/sie.py:47-72`): set `dev_addr`/`ep_addr`/`type`/`data_pid`, strobe
`start`, poll `idle`, move bytes through two 8-bit streams. Exposing it to the
CPU is a register map over that struct, not a translation into someone else's
architecture.

### 15.1 Proposed map

A new Wishbone window, `usb_host` at **0xf0001000, size 0x100** — free, aligned,
inside the declared uncached PMA region (`base=f0000000 size=10000000 main=0`,
`ecp5-test/riscv/vexii_cpu.py:110-113`), and clear of the PLIC at 0xf0400000. It
needs a 32-bit data port, so it takes its own `decoder.add()` rather than
sitting behind the 8-bit `board` CSR decoder.

| off | name | acc | contents |
|---|---|---|---|
| 0x00 | `CTRL` | rw | `enable`, `bus_reset` (strobe), `sof_enable` |
| 0x01 | `ADDR` | rw | `dev_addr[6:0]` |
| 0x02 | `EP` | rw | `ep_addr[3:0]`, `type[1:0]` (SETUP/IN/OUT), `data_pid` |
| 0x03 | `START` | w | strobe — latches the above and runs one transaction |
| 0x04 | `STATUS` | r | `idle`, `reset_active`, `disconnected`, `speed[1:0]` |
| 0x05 | `RESP` | r | `response[2:0]`: NONE/ACK/NAK/STALL/TIMEOUT/CRC_ERROR/RX_OVERFLOW |
| 0x06-07 | `RXLEN` | r | **16-bit**, counted by the shim (see 15.2) |
| 0x08-09 | `FRAME` | r | `sof_frame[10:0]` |
| 0x0c | `FIFO8` | rw | write pushes a TX byte, read pops an RX byte |
| 0x10-13 | `FIFO32` | rw | the same FIFOs, four bytes per bus transaction |
| 0x14 | `FIFO_STATUS` | r | `tx_full`, `tx_empty`, `rx_valid`, `rx_full` |
| 0x15 | `IRQ_EN` | rw | `xfer_done`, `disconnect`, `sof` |
| 0x16 | `IRQ_PEND` | r/w1c | same bits |

PLIC source 6 (`Plic(sources=5)` → `6` at `vexii_hello_soc.py:650`; sources 1-5
are taken, 6-31 are free). The PAC follows automatically —
`scripts/soc_generate_pac.py` reads `decoder.bus.memory_map` and
`interrupt_sources`, so `pac::base::USB_HOST` and `USB_HOST_IRQ` appear without
anything being hand-written.

### 15.2 Four traps in the GUH interface, worth writing down

- **`status.rx_len` is 8 bits** (`guh/usbh/sie.py:616`). It wraps on a 512-byte
  high-speed packet. GUH's own MSC engine ignores it and counts the stream
  itself (`guh/engines/msc.py`, `rx_byte_idx = Signal(10)`). The shim must count
  bytes drained from the RX FIFO, not forward `rx_len`.
- **Data toggle is the caller's job.** `xfer.data_pid` goes straight to the
  transmitter; the SIE never inspects a received PID and never toggles. For a
  general-purpose host that is work for firmware — and for a fuzzing or MITM
  tool it is a feature, because a deliberately wrong toggle is now expressible.
- **No completion strobe and no interrupt.** GUH is poll-only. Synthesise the
  interrupt in the shim from the rising edge of `status.idle`, latched and held
  level-high, because the PLIC has no edge detector (`vexii_plic.py:46-56`).
- **NYET is reported as ACK** (`sie.py:643-645`) and there is no PING protocol.
  High-speed bulk OUT flow control is therefore approximate; firmware sees a
  successful ACK where the device asked for a retry.

### 15.3 Throughput: what a register interface can and cannot carry

| | payload ceiling | basis |
|---|---|---|
| USB HS bulk | **53.2 MB/s** | 13 × 512 B per 125 µs microframe |
| USB FS bulk | 1.22 MB/s | 19 × 64 B per 1 ms frame |
| Wishbone, 32-bit, 60 MHz | 120 MB/s | 2 cycles/transfer after `18c1fa5` |
| `FIFO8` register port | ~4-8 MB/s (estimate) | one uncached byte access per byte; 8-15 CPU cycles each |
| `FIFO32` register port | ~10-20 MB/s (estimate) | four bytes per access, same per-access cost |

The two estimates are **not measured** — the bus cycle count is known, the
VexiiRiscv stall on an uncached `iobus` load is not. Measuring it is a firmware
loop against the CLINT tick and needs the board.

Consequence, which is the real answer to "what would the CPU drive":

- **Control transfers, enumeration, HID/interrupt endpoints, and all of
  full-speed** fit comfortably in a register interface. This covers every job in
  §17 except sustained bulk.
- **Sustained high-speed bulk does not.** A register port reaches perhaps a
  third of line rate at best.

### 15.4 Tier two, when sustained bulk is actually needed

GUH's `guh/periph/dma.py` is a general Wishbone **initiator** with `cti`/`bte`
bursts, independent of the MSC peripheral it was split out of, and BSD-3-Clause
as of `923c8490`. It would attach to our arbiter as a fourth master alongside
`cpu.ibus`/`dbus`/`iobus` and write straight into the existing 64 KiB block RAM
at 0x0.

Its cost is its staging FIFO: `_DATA_FIFO_WORDS` defaults to 8 KiB, which is
about 4 of the 14 free BRAMs, and it is a tunable. Target block RAM first —
HyperRAM is the obvious destination for large buffers but has no Wishbone
adapter (#90) and an unfinished DQS path (#92).

**Do not build tier two first.** It is only worth it once a measured PIO number
proves it necessary, and §15.3's estimate is not that measurement.

---

## 16. Enumeration belongs in firmware

`USBHostEnumerator` costs +830 LUT over the bare SIE and should not be taken.

- It **discards the descriptors**. The device descriptor is thrown away except
  `bMaxPacketSize0`; the configuration descriptor is streamed through a parser
  that keeps one endpoint number and one `wMaxPacketSize` and nothing else
  (`guh/usbh/enumerator.py:108, 122-129`; `guh/usbh/descriptor.py:54-68`).
- The parser is **specialised at construction time** to one interface class
  (`enumerator.py:64`), so "which driver" is a synthesis-time decision.
- Device address `0x12` and configuration `1` are **hard-coded**
  (`enumerator.py:64`, `:441-446`).

Firmware enumeration over the SIE is the same five control transfers, gives raw
descriptor bytes, handles any class, and can retry — roughly 400 lines of
`no_std` Rust against a peripheral we control, using the deferred event ring
(`firmware/cynthion-soc/src/events.rs`) so a handler records and the main loop
prints. That is the pattern the rest of this firmware already uses.

So the vendoring boundary is narrow and specific:

| From `apfaudio/guh` | Take? | Why |
|---|---|---|
| `guh/usbh/sie.py` | **yes** | the transaction engine; the thing that does not exist elsewhere |
| `guh/usbh/reset.py` | **yes** | the host half of the HS chirp; the actual hard part |
| `guh/usbh/types.py` | **yes** | shared enums, trivial |
| `guh/usbh/enumerator.py` | no | §16 |
| `guh/usbh/descriptor.py` | no | follows from the enumerator |
| `guh/engines/*` | no | fixed-function classes; firmware does this |
| `guh/periph/msc.py` | no | a block device, not a host controller |
| `guh/periph/dma.py` | later | tier two only, §15.4 |

That is ~1200 lines of Amaranth, BSD-3-Clause, pinned. Vendoring rather than
depending is what `docs/upstream-boundary.md` prescribes ("do not inherit a
stack to get one file — vendor the file"), and it is the right call twice over
here: GUH has no releases, no tags, one author, an explicit "interfaces will
change" warning, and a stated policy of not taking contributions.

---

## 17. What this unlocks, per Cynthion's actual jobs

| job | what a host engine gives | what it does not |
|---|---|---|
| **Analysis** | a *known stimulus*. Host and analyzer share one `UTMITranslator` on `target_phy` (§13.4), so the analyzer stops being only an observer and becomes a test instrument: deterministic replay, deliberate protocol violations, traffic on demand with no third-party device in the loop | nothing for capture fidelity. The analyzer already captures better than a host engine could, and a hub plus a PC is a simpler traffic source if that is all you want |
| **Emulation** | **the missing half, and the strongest case.** Facedancer emulates a *device* to someone else's host. Nothing today lets Cynthion drive a *device under test* — exercise it, fuzz it, replay a capture at it, hold it in a state a real host would not | it is not a general-purpose host: no hubs past tier one, no split transactions, no isochronous |
| **Passthrough / MITM** | a real host on TARGET and a real device on another PHY, so traffic can be *modified*, not just observed. `greatscottgadgets/luna#302` is an open question about exactly this | needs both stacks in one bitstream and a spare PHY — `aux_phy` carries the console today. Projected ~18k LUT and ~45 BRAM, **not measured**, and that is the number to get before promising it |

Not served at all: USB 3.x, spec compliance, multi-tier hubs, guaranteed
sustained throughput. Part I §8.1's walk-away list stands unchanged.

---

## 18. Recommendation, and the first concrete step

**Do it, in the shape of §15-16: vendor `guh/usbh`'s three files, expose the
SIE through a CSR and FIFO peripheral, enumerate in firmware.** Not Part I's
Option A (a gateware class engine proves the fabric works but leads nowhere),
not Option C.

**First step, before a line of the shim is written:** bump the GUH pin to
`923c8490`, then build the `sie` configuration of `scripts/usb-host-core-area.py`
*inside* `HelloSoC` and read the in-situ LUT count and `sync`/`usb` fmax. §12's
2080 LUT is a standalone figure at 9% occupancy; the number that decides the
design is the one at 60%, and it costs one build to get. Run
`scripts/soc_timing_sweep.py` alongside it, because a single placement is not
evidence on this design.

Then, in order:

1. **Mux `target_phy`.** Move the pad wiring at `vexii_hello_soc.py:1288-1298`
   behind an owner select, or better, implement §13.2's `UTMITranslator` manual
   register access so `UlpiRegisters` becomes a client. This unblocks #120 and
   #125 as well as the host.
2. **VBUS and CC.** No `*_vbus_en` pin is driven anywhere in the SoC today.
   Add `control_vbus_en`, `target_c_vbus_en`, `aux_vbus_en` and
   `target_a_discharge` to the existing `gpio` peripheral, and present Rp on
   TARGET through `fusb302.rs`. Both are small because the hard parts — an I2C
   mux, a working FUSB302B driver, a power monitor — already exist.
3. **The CSR/FIFO shim and the interrupt**, §15.
4. **Firmware enumeration**, §16. Milestone: print a device descriptor.
5. **Measure the PIO byte rate** against the CLINT tick, and only then decide
   whether §15.4's DMA is needed.

The AUX-to-TARGET loopback proposed on #105 remains the right acceptance test,
with one revision: it needs `aux_phy` free, and `aux_phy` is the console. Either
move the console to `control_phy` behind `ApolloAdvertiser` for that test, or
accept a third-party device for first light and defer the loopback.

---

## 19. What could not be determined

- **In-situ area and fmax.** Every figure in §12 is standalone. The SoC has not
  been built with the host core in it.
- **The PIO byte rate** (§15.3). The bus cycle count is known; the CPU's stall
  on an uncached `iobus` access is not, and it dominates.
- **Host-plus-device in one bitstream** (§17). The ~18k LUT / ~45 BRAM figure is
  addition, not synthesis.
- **Whether the CONTROL VBUS switch actually delivers to TARGET-A on this
  board.** `control_vbus_in_en` is `PinsN` and its post-configuration state was
  not traced. Board question, and no board was touched.
- **Whether any other high-speed host core exists.** The survey of non-GitHub
  forges was still running when this was written. One near-miss was confirmed
  and is worth recording (below); nothing high-speed was found, and the
  recommendation would only change if something were.

### 19.1 The one real alternative, and why it does not apply

SpinalHDL ships a complete OHCI controller —
`lib/src/main/scala/spinal/lib/com/usb/ohci/`, seven files including
**`UsbOhciWishbone.scala`**. On paper it is everything §15 has to build by hand:
a published standard register map, an existing Linux/BSD/Zephyr driver for it,
DMA descriptors already specified, a Wishbone wrapper, and the same HDL
ecosystem as our CPU. It is the thing #101 was reaching for.

It is **full and low speed only** — `UsbOhci.scala` carries `lowSpeed` and an
`fs` timer constant and nothing else, because OHCI is an FS/LS specification.
The high-speed companion is EHCI, and no open EHCI implementation was found.

So the choice is stark and worth stating plainly: **12 Mbps with a standard
register map and off-the-shelf drivers, or 480 Mbps with a bespoke interface.**
#105 asked for 480, so this note is a record of the road not taken rather than a
recommendation — but if the requirement ever softens to full speed, this is the
better engineering, and it deserves a fresh look rather than a port of §15.
Its licence was not confirmed (the repository reports `NOASSERTION`) and would
have to be before any use.

---

## 20. Artifacts

- `scripts/usb-host-core-area.py` — the §12 measurement.
- `scripts/usb-host-area.py` — Part I §4's example-level measurement.
- `tmp/host-core-area/core-area-results.json`, `tmp/logs/usb-host-core-area.log`.
- GUH checkout: `tmp/host-research/guh` (pinned `fbd7077`; bump to `923c8490`).

**Part II touched no hardware either. All figures are from synthesis and
place-and-route.**
