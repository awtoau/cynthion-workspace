# RISC-V Alternatives for Cynthion moondancer

## Purpose

This document consolidates the RISC-V CPU alternatives and evaluation criteria that were previously scattered across planning notes and chat summaries.

Primary source references:
- `docs/implementation_plans/serial_architecture_redesign_plan.md` (Phase 0, P0.1)
- `vexriscv_update_blocked.md`

## Current Baseline

- Current CPU path: VexRiscv (`variant="cynthion+jtag"` in prior gateware flow)
- Current status: stable and retained for now
- Update pressure: there was an attempted VexRiscv/toolchain refresh, but it was deferred due to Scala/sbt/JDK incompatibility and migration risk

Reference: `vexriscv_update_blocked.md`

## Candidate Alternatives

The alternatives originally identified for investigation are:

1. CV32E40P (OpenHW/PULP)
2. CV32E40X (OpenHW/PULP, extended feature set)
3. Rocket Chip (Berkeley)
4. Ibex (lowRISC)
5. VexRiscv (current baseline for comparison)

Reference: `docs/implementation_plans/serial_architecture_redesign_plan.md`

Important clarification for 64-bit review:

- `CV32E40P`, `CV32E40X`, `Ibex`, and the current `VexRiscv` baseline are all RV32-class options, not RV64 options.
- The only historical candidate in the original list that naturally overlaps a 64-bit discussion is `Rocket Chip`.

## Board Constraint That Drives the 64-bit Answer

Cynthion uses a Lattice ECP5 `LFE5U-12F` FPGA.

- FPGA part reference in this workspace: `app/assets/hardware/cynthion.json`
- Platform definitions in the archived upstream tree also target `LFE5U-12F`
- This is a small FPGA for a Linux-capable or MMU-bearing RV64 soft core, especially because Cynthion is not hosting a CPU in isolation: it already needs the surrounding USB gateware, memory fabric, debug path, and SoC integration used by moondancer/facedancer.

## Legacy Full-SoC Review

This section is historical context.

It answers the broader question:

- what if Cynthion keeps most of the existing moondancer/facedancer-style SoC shape?

That is no longer the primary question for this document. The primary question is the stripped-down Linux-only case in `Revised ranking for a bare-minimum RV64 Linux experiment` below.

## 64-bit Options Specifically

### 1. Rocket Chip

Status: technically plausible to investigate, but unlikely to be a good fit on Cynthion's `LFE5U-12F`.

Why:

- Rocket is a real RV64 ecosystem option and is the only 64-bit candidate already named in prior notes.
- Upstream Rocket is a generator-oriented SoC stack, not a small-FPGA-first core.
- Its normal integration model pulls in TileLink, cache hierarchy, debug devices, and a broader SoC framework.
- Even `DefaultSmallConfig` is still a "small Rocket system", not a tiny RV64 microcontroller core.

Practical conclusion:

- If a 64-bit spike must happen, Rocket is one of the few mature choices worth evaluating.
- It should be treated as a low-probability fit for the current board unless a fresh synthesis shows unusually large headroom.

### 2. CVA6 / Ariane

Status: not a realistic fit for Cynthion's FPGA budget.

Why:

- CVA6 is explicitly a 64-bit application-class core with MMU, TLBs, branch prediction, and Unix-like OS support.
- Upstream materials position it on substantially larger FPGA platforms such as Genesys 2 and Zybo-class designs.
- That size/peripheral class is a mismatch for a `12k LUT` ECP5 that already hosts the rest of the Cynthion SoC.

Practical conclusion:

- CVA6 should be considered "too large unless the board changes".

### 3. VexiiRiscv

Status: architecturally interesting, but still unlikely to fit well on the current board.

Why:

- VexiiRiscv is RV32/64 capable and is the natural RV64 descendant of the current VexRiscv family.
- Upstream positioning is from small configurations up to Linux and Debian capable systems, with optional caches, MMU, and dual-issue features.
- That flexibility is valuable, but there is no evidence in this workspace of a known `LFE5U-12F`-sized RV64 configuration compatible with Cynthion's existing integration requirements.

Practical conclusion:

- If someone wants the most strategically aligned RV64 experiment, VexiiRiscv is more attractive than CVA6.
- It is still more likely to need a larger FPGA or a major reduction in surrounding SoC features.

### 4. Minimal RV64 cores outside the current candidate set

Status: possible in theory, but not yet a serious recommendation for this repo.

Why:

- There are niche RV64 soft cores smaller than Linux-capable designs.
- The tradeoff is usually weaker tooling, weaker debug support, weaker ecosystem maturity, or a much larger integration burden.
- For Cynthion, integration cost matters as much as raw LUT count because the existing firmware, PAC/HAL generation, and debug flow already assume the current SoC structure.

Practical conclusion:

- A tiny RV64 core could only make sense as a dedicated research spike, not as the default migration target.

## Legacy Recommendation For "Which 64-bit Options Fit?"

Short answer: no mainstream RV64 option currently looks like a comfortable fit if the broader existing Cynthion SoC shape is preserved.

Recommended ranking:

1. `Do not plan on CVA6` for this board.
2. `Do not assume Rocket fits` without fresh synthesis.
3. `VexiiRiscv is the least bad RV64 experiment`, but still likely too large once combined with the existing Cynthion SoC.
4. `Keep VexRiscv or another RV32 core` if the board stays `LFE5U-12F` and the goal is a practical product path rather than a research spike.

## What Would Change This Legacy Answer

This conclusion should be revisited only if at least one of the following becomes true:

1. A fresh facedancer/moondancer synthesis shows unexpectedly large unused LUT/BRAM headroom on `LFE5U-12F`.
2. The surrounding SoC is simplified enough that CPU area becomes the dominant cost.
3. The hardware target changes to a larger FPGA.

Note: this workspace currently does not have `nextpnr-ecp5` installed, so this document does not include a fresh local synthesis datapoint. The conclusion above is based on the board size in this repo plus the published design class of the candidate RV64 cores.

## Primary Question: Bare-Minimum RV64 Linux

This is the main section to read.

Assume we drop almost everything except:

1. one RV64 core,
2. enough external RAM to boot a very small Linux system,
3. just enough storage / console / interrupt plumbing to bring the kernel up.

Under that narrower constraint, Cynthion becomes more plausible than the earlier full-moondancer analysis suggested.

Why:

- The board still uses the same small `LFE5U-12F` FPGA, so logic budget remains tight.
- But the board also has `8 MB` of external HyperRAM (`S27KS0641`) attached to the FPGA.
- The existing archived gateware already models and tests that HyperRAM interface.
- The existing facedancer SoC also reserves a HyperRAM memory window at `0x20000000`, so external RAM integration is not a hypothetical board feature.

That means the gating constraint is mostly FPGA logic area for a Linux-capable RV64 core plus a minimal memory/controller fabric, not raw DRAM availability alone.

### Revised ranking for a bare-minimum RV64 Linux experiment

#### 1. VexiiRiscv

Best candidate.

Why:

- It is explicitly RV32/64 capable.
- It supports optional `SV39` MMU and is documented upstream as capable of running Linux / Buildroot / Debian.
- Its configuration space spans from relatively small embedded-style systems up to larger Linux-capable systems, which is exactly what matters on a `12k LUT` ECP5.
- Compared with Rocket and CVA6, it is the most natural candidate for trying to shave features down aggressively while still staying in an upstream-supported RV64 family.

Caveat:

- This is still a squeeze. Linux-capable means MMU, page table walker, traps, timer, interrupt path, and some amount of cache or buffering. On this FPGA, the success case is likely a highly stripped single-core configuration with minimal peripherals and very little margin.

#### 2. Rocket Chip

Possible, but second choice.

Why:

- Rocket is a real RV64 Linux-class core and can generate smaller configs.
- However, Rocket's integration style is still heavier and more SoC-opinionated than what you want here.
- If the design target is "one core, tiny memory system, just enough to boot", Rocket usually carries more framework cost than VexiiRiscv.

Practical view:

- Rocket is more plausible in this stripped-down scenario than in the earlier full-Cynthion-SoC scenario.
- It is still not the first core I would try on `LFE5U-12F`.

#### 3. CVA6

Still unlikely.

Why:

- CVA6 is Linux-capable RV64, but it remains squarely application-class.
- Even after removing USB-heavy surrounding logic, CVA6 still looks mismatched to a `12k LUT` ECP5 target.

Practical view:

- If the goal is to maximize the chance of first boot on this board, CVA6 is the wrong place to spend effort.

## Practical conclusion

If the question is:

"Can Cynthion plausibly host some RV64 core that boots a tiny Linux, if we ignore the current SoC and keep only the minimum fabric plus HyperRAM?"

Then the answer is:

- `Yes, plausibly, but only as a very tight experiment.`
- `VexiiRiscv is the strongest candidate.`
- `Rocket is a backup candidate.`
- `CVA6 is still not a good bet.`

## Remaining risks

The `8 MB` HyperRAM makes Linux thinkable, but not comfortable.

- The memory size leaves very little margin for kernel + initramfs + userspace.
- HyperRAM latency and controller cost also matter on a small FPGA.
- So the real question is no longer "impossible or possible"; it is "can a minimal RV64 Linux system still close timing and leave enough area for the RAM and boot plumbing?"

That is a synthesis-and-bringup question, not a pure architecture question.

## Preferred bring-up path: USB network + NFS root

For first Linux bring-up, the most practical path is likely not a USB flash drive on AUX, but a network-served root filesystem over USB.

The idea is:

1. Store a small bootloader, kernel image, and DTB locally in SPI flash.
2. Use HyperRAM as the main system RAM.
3. Expose Cynthion to the host PC as a USB network device.
4. Have the host PC serve the root filesystem over NFS.

In other words, the FPGA system does not need to implement USB host-mode storage first. Instead, it acts as a USB device on a known-good FPGA-connected port, and the host PC provides network services.

### How it works

At boot, the RV64 Linux system would:

1. initialize RAM, timer, interrupt controller, and serial console,
2. bring up a USB device controller,
3. enumerate to the host PC as a USB network gadget,
4. obtain a static IP or use a simple preconfigured point-to-point address pair,
5. mount its root filesystem from the host PC using NFS.

This is the same broad strategy often used by embedded Linux boards during early bring-up, except that the "network cable" is a USB device-side network link rather than physical Ethernet.

### Why this is attractive on Cynthion

- Cynthion already has strong device-side USB infrastructure in the LUNA / facedancer ecosystem.
- This avoids needing a mature USB host controller on AUX before Linux can boot useful userspace.
- It avoids USB mass-storage class and block-device work during the earliest stage.
- The host PC can easily provide a large editable root filesystem, logs, symbols, and test binaries.
- With only `8 MB` of HyperRAM, avoiding a large bundled initramfs is useful.

### What is still required

This path is easier than USB host storage, but it is not free. It still requires:

1. a Linux-capable RV64 core configuration,
2. a working interrupt/timer/console path,
3. a USB device controller path suitable for presenting a USB network function,
4. a minimal Linux configuration with the relevant USB gadget/network support,
5. host-side NFS export and a simple network configuration.

### Likely USB role split

For this approach, the simplest model is:

- one FPGA-connected port remains device-side and connects to the host PC,
- AUX remains available for later experiments or for an eventual USB host-mode effort,
- CONTROL is avoided if Apollo muxing complicates bring-up,
- a non-muxed FPGA-owned port is preferred when possible.

### Why this is lower risk than USB disk on AUX

USB disk on AUX requires all of the following before Linux can use it well:

1. host-mode USB gateware,
2. a host-controller software model Linux can drive,
3. enumeration and transfer scheduling,
4. mass-storage class transport,
5. then block-device and filesystem use.

The network-root path removes the entire host-mode and mass-storage side from the first milestone.

### Practical first milestone

The earliest useful Linux milestone for this option would be:

1. kernel boots from flash,
2. serial console works,
3. USB device-side network link enumerates on the host PC,
4. rootfs mounts over NFS,
5. userspace runs from the host-served filesystem.

If that works, Linux bring-up is no longer blocked on implementing AUX USB host-mode storage.

## Short recommendation

If the goal is specifically "boot a minimal RV64 Linux on Cynthion", the document's recommendation is:

1. Try `VexiiRiscv` first.
2. Use HyperRAM as system RAM.
3. Boot kernel and DTB from flash.
4. Use USB device-side networking plus NFS root before attempting AUX USB host-mode storage.
5. Treat the earlier full-SoC review above as legacy context, not the main decision path.

## Secondary historical context

The following sections are retained from the original broader review and are less important than the stripped-down Linux section above.

## Evaluation Criteria

For this repo, evaluate each option against:

1. Performance
2. Gate count / FPGA resource usage
3. Feature support needed by moondancer and debug flows
4. Integration impact on generated PAC/HAL interfaces
5. Toolchain/build complexity and long-term maintenance risk
6. Compatibility with current Cynthion gateware/firmware architecture

## Impacted Code Areas

Any CPU swap would likely touch at least:

1. `debris/code/awto-cynthion-reference/cynthion/python/src/gateware/facedancer/top.py`
2. `debris/code/awto-cynthion-reference/firmware/moondancer-pac/src/generated.rs`
3. `debris/code/awto-cynthion-reference/firmware/lunasoc-hal/`

These paths are listed here as the known historical integration points from planning notes.

## Why VexRiscv Was Kept (Current Decision)

The documented decision to keep the current VexRiscv baseline was based on:

1. Stable behavior in existing builds and self-tests
2. Toolchain migration scope required for update (Scala/sbt/JDK transition)
3. Regression risk outweighing short-term gain
4. Existing JTAG issue having a workaround

Reference: `vexriscv_update_blocked.md`

## Recommended Next Step (When Revisited)

If CPU migration is resumed, run a structured spike:

1. Recreate a clean, reproducible build environment for each candidate core
2. Capture synthesis/resource/timing metrics side-by-side
3. Validate firmware bring-up and debug/JTAG behavior
4. Compare generated register interfaces and HAL impact
5. Document a final recommendation with measured tradeoffs

## Summary

This file is now the canonical location for RISC-V alternative options and decision context in this workspace.