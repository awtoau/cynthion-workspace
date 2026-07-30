# USB Host Mode on Cynthion at 480 Mbps — Proposal

**Status:** proposal, no hardware exercised
**Date:** 2026-07-30T19:30:00+10:00
**Consolidates:** awtoau/cynthion-workspace#101, #105
**Target:** LFE5U-12F (24288 LUT, 56 BRAM), Cynthion r1.4

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

Note on `import luna`: it resolves to
`/home/dan/opt/cpython-315t/lib/python3.15t/site-packages/luna`, not
`repos/luna`. The checkout was read for this analysis; the installed copy is what
the measured builds ran against. Both are device-only.

**No hardware was touched in producing this proposal. All figures are from
synthesis, place-and-route and simulation.**
