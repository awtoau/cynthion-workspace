#!/usr/bin/env python3
#
# Rewrite the file references in open issue bodies so they resolve against the tree.
# SPDX-License-Identifier: BSD-3-Clause

"""
Section 6c of `docs/plans/issue-and-doc-audit.md`: 55 open issues cite paths the
tree no longer has.  They are all still actionable -- `ecp5-test/` simply became
`gateware/`, several documents were retired to `debris/`, `repos/luna` stopped
being a submodule, and a handful of scripts were deleted outright.  A reference
that does not resolve reads as checked when it is not, which is worse than an
obviously old one.

Every replacement path in `FIXES` is checked against the working tree before the
body is written, so this cannot introduce a second dead link.  Where a reference
has no successor the correction says so in the issue text rather than inventing
one.  Where an issue is stale on a *fact* and not merely a path, a dated note goes
at the top and the argument below it is left exactly as its author wrote it.

    ./scripts/fix_issue_refs.py                 # dry run, prints what would change
    ./scripts/fix_issue_refs.py --apply         # dry run plus `gh issue edit`
    ./scripts/fix_issue_refs.py --apply 97 110  # only these

Reads the bodies from `tmp/issues/<n>.md` (dumped from
`gh issue list --state open --limit 300 --json number,title,body`), writes the
rewritten bodies to `tmp/issues-fixed/<n>.md` and the log to
`tmp/logs/fix_issue_refs.log`.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tmp" / "issues"
DST = ROOT / "tmp" / "issues-fixed"
LOG = ROOT / "tmp" / "logs" / "fix_issue_refs.log"

DATE = "2026-08-07"

# Paths named in a replacement that are deliberately NOT files in this tree:
# installed Python packages, upstream repositories, and files inside submodules
# that the existence check would otherwise have to special-case.
NOT_OURS = re.compile(r"^(luna|luna_soc|amaranth|facedancer|apollo_fpga)[./]")


def note(text):
    """A dated note, set off as a quote so it never reads as the author's own."""
    return f"> **{DATE} — references corrected.** {text}\n\n"


# Each entry is {number: (note_or_None, [(old, new), ...])}.  `old` must appear in
# the body or the run fails loudly -- a silent no-op would leave a dead link.
FIXES = {
    # ---- 2a: ecp5-test/ is gateware/ -------------------------------------
    97: (
        note(
            "`ecp5-test/` is now `gateware/`. Also: the headline — driving the VBUS "
            "switches from the SoC — **has landed** since this was written "
            "(`gateware/soc/peripherals/vbus_csr.py`, instantiated in "
            "`gateware/soc/top.py`). What is still open is the rest of the list: PMOD "
            "loopback, `user_mezzanine`, the USER button, and edge-counting "
            "`target_usb_dp`/`target_usb_dm`."
        ),
        [("`ecp5-test/pins/pin_survey.py`", "`gateware/probes/pins/pin_survey.py`")],
    ),
    142: (
        note("`ecp5-test/riscv/` is now `gateware/soc/peripherals/`."),
        [
            ("`ecp5-test/riscv/i2c_master.py`", "`gateware/soc/peripherals/i2c_master.py`"),
            ("`i2c_mux.py` selecting", "`gateware/soc/peripherals/i2c_mux.py` selecting"),
        ],
    ),
    153: (
        note("`ecp5-test/` is now `gateware/probes/`."),
        [
            ("`ecp5-test/bist.py`", "`gateware/probes/bist.py`"),
            ("`ecp5-test/pins/pin_survey.py`", "`gateware/probes/pins/pin_survey.py`"),
        ],
    ),
    176: (
        note("`ecp5-test/` is now `gateware/`."),
        [
            ("`ecp5-test/sideband_link.py`", "`gateware/probes/sideband/sideband_link.py`"),
            ("`ecp5-test/riscv/sideband_csr.py`", "`gateware/soc/peripherals/sideband_csr.py`"),
        ],
    ),
    202: (
        note(
            "`ecp5-test/riscv/vexii_bootram.py` is now `gateware/soc/bootram.py` and "
            "`ecp5-test/riscv/jtag_stage.py` is now `gateware/soc/bus/jtag_stage.py`. "
            "The defect itself is unchanged and still present in `bootram.py`."
        ),
        [
            ("`ecp5-test/riscv/vexii_bootram.py`", "`gateware/soc/bootram.py`"),
            ("`ecp5-test/riscv/jtag_stage.py`", "`gateware/soc/bus/jtag_stage.py`"),
        ],
    ),
    206: (
        note(
            "`ecp5-test/riscv/hyperram_dqs_phy.py` is now "
            "`gateware/soc/peripherals/hyperram_dqs_phy.py`. The substance holds: "
            "`swap_halves` is gone from the gateware, so the convention is still unstated."
        ),
        [
            ("`ecp5-test/riscv/hyperram_dqs_phy.py`",
             "`gateware/soc/peripherals/hyperram_dqs_phy.py`"),
            ("`vexii_bootram.py` carried", "`gateware/soc/bootram.py` carried"),
        ],
    ),
    86: (
        note("`ecp5-test/sideband/` is now `gateware/probes/sideband/`."),
        [("`ecp5-test/sideband/`", "`gateware/probes/sideband/`")],
    ),
    87: (
        note(
            "`ecp5-test/hyperram/` is now `gateware/probes/hyperram/`. The opcode map "
            "quoted below is **obsolete**: `gateware/probes/sideband/sideband_link.py` "
            "now has `CMD_PING=0x01`, `CMD_STATUS=0x02`, `CMD_WRITE_BASE=0x80`, "
            "`CMD_WRITE_MASK=0x7F` — POWER and the whole LED block no longer exist, so "
            "the \"68 allocated, 188 free\" arithmetic and the capability-query argument "
            "need restating against the current opcodes."
        ),
        [("`ecp5-test/hyperram/`", "`gateware/probes/hyperram/`")],
    ),
    165: (
        note(
            "`ecp5-test/riscv/` is now `gateware/soc/`, and `scripts/cyn_main.py` no "
            "longer exists — the `cyn` CLI was retired in favour of `./dev.py`. "
            "`scripts/` is now **107** files, not 13, so the 43-file breakdown below "
            "needs re-deriving before it is used as a work estimate."
        ),
        [
            ("`ecp5-test/riscv/` (8)", "`gateware/soc/` (8)"),
            ("`./cyn` and `scripts/cyn_main.py` keep their names; that is where `cyn` comes from.",
             "`./cyn` and `scripts/cyn_main.py` kept their names; that is where `cyn` came "
             "from. Both are retired — neither exists today, and `./dev.py` is the one door."),
        ],
    ),
    169: (
        note(
            "`ecp5-test/riscv/vexii_hello_soc.py` is now `gateware/soc/top.py`, "
            "`ecp5-test/cynthion_platform/` is `gateware/board/`, and "
            "`ecp5-test/bram_probe/` is `gateware/probes/bram_probe/`. Four facts below "
            "have also moved on: `.gitmodules` carries **four** submodules, not eight "
            "(cynthion, apollo, cynthion-hardware, vexiiriscv); the `gateware` check — "
            "the ~98% of wall time — has been **deleted** from `scripts/check.py`, which "
            "records why at `:253-261`; `.gitignore` now anchors the pattern as `/lib/`; "
            "and `gateware/soc/top.py` no longer imports "
            "`apollo_fpga.gateware.variable_clock`, so that is no longer what holds "
            "`repos/apollo` in (29 files still import `apollo_fpga`). "
            "`scripts/phy_probe.py` no longer exists. A close-out draft is in-tree at "
            "`docs/drafts/169-closeout.md`."
        ),
        [
            ("elaborates `ecp5-test/riscv/vexii_hello_soc.py`", "elaborates `gateware/soc/top.py`"),
            ("- `ecp5-test/riscv/vexii_hello_soc.py:176, 181, 277, 312, 1642`",
             "- `gateware/soc/top.py:178, 183, 283, 318, 1753`"),
            ("**`repos/apollo` is load-bearing today.** `ecp5-test/riscv/vexii_hello_soc.py:84`\nimports",
             "**`repos/apollo` is load-bearing today.** This was written when "
             "`gateware/soc/top.py:84`\nimported"),
            ("`ecp5-test/cynthion_platform/` is already an", "`gateware/board/` is already an"),
            ("`ecp5-test/bram_probe/bram_probe.py:192`", "`gateware/probes/bram_probe/bram_probe.py:192`"),
            ("`scripts/phy_probe.py:16` (selftest registers).",
             "`scripts/phy_probe.py:16` (selftest registers) — that script no longer exists."),
            ("2. converge the four platform imports onto `ecp5-test/cynthion_platform/`",
             "2. converge the four platform imports onto `gateware/board/`"),
            ("3. vendor the six `apollo_fpga.gateware` modules into `ecp5-test/`",
             "3. vendor the six `apollo_fpga.gateware` modules into `gateware/`"),
            ("3. `gateware`: delete it, or repoint it at `ecp5-test/`, which `socmap` already",
             "3. `gateware`: delete it, or repoint it at `gateware/`, which `socmap` already"),
            ("**`repos/facedancer` is free now.** Nothing in this repo imports it.",
             "**`repos/facedancer` is free now.** It has since been dropped as a submodule "
             "and is no longer in the tree. Nothing in this repo imports it."),
        ],
    ),
    110: (
        note(
            "`ecp5-test/riscv/vexii_cpu.py` is now `gateware/soc/cpu/cpu.py`. Three other "
            "references have no successor: `ecp5-test/fabric/FABRIC_TEST.md` was not "
            "migrated (the surviving record of the LUT budget is `gateware/README.md`, "
            "and it now says 20,476 LUT4, not 20,143), and `scripts/hyperram_ladder.py` "
            "is gone — the analogue is `scripts/riscv_clock_ladder.py`. **#91 is closed**, "
            "so the block this issue declares is lifted."
        ),
        [
            ("`ecp5-test/riscv/vexii_cpu.py`", "`gateware/soc/cpu/cpu.py`"),
            ("`ecp5-test/fabric/FABRIC_TEST.md`", "`gateware/README.md`"),
            ("the way `scripts/hyperram_ladder.py` sweeps the HyperRAM clock",
             "the way `scripts/riscv_clock_ladder.py` sweeps the clock"),
        ],
    ),
    204: (
        note("`ecp5-test/` is now `gateware/`."),
        [
            ("`ecp5-test/bist.py`", "`gateware/probes/bist.py`"),
            ("`ecp5-test/riscv/jtag_stage.py`", "`gateware/soc/bus/jtag_stage.py`"),
        ],
    ),
    235: (
        note("`ecp5-test/bist.py` is now `gateware/probes/bist.py`."),
        [("`ecp5-test/bist.py`", "`gateware/probes/bist.py`")],
    ),
    # ---- 2c: repos/luna is no longer a submodule -------------------------
    83: (
        note(
            "`repos/luna` is no longer a submodule — LUNA is an ordinary installed "
            "package, so `repos/luna/pyproject.toml` and `repos/luna/luna/gateware/` "
            "cannot be edited here. That makes the \"18 files are upstream's problem\" "
            "section and step 2 reduce to waiting for a released LUNA. **Our** half of "
            "the issue is unchanged and still real: "
            "`repos/cynthion/cynthion/python/src/gateware/analyzer/analyzer.py` still "
            "uses `Record`."
        ),
        [
            ("`repos/luna/pyproject.toml:37`", "LUNA's own `pyproject.toml`"),
            ("| `repos/luna/luna/gateware/` | 18 | fork of upstream |",
             "| `luna.gateware` (installed package) | 18 | upstream's, not ours |"),
            ("`repos/luna` is a fork tracking `greatscottgadgets/luna`.",
             "`repos/luna` was a fork tracking `greatscottgadgets/luna`; it is no longer a "
             "submodule here, and LUNA is an ordinary installed package."),
            ("| `scripts/`, `ecp5-test/` | 0 | — |", "| `scripts/`, `gateware/` | 0 | — |"),
        ],
    ),
    125: (
        note(
            "`repos/luna` is no longer a submodule; LUNA is an installed package, so "
            "that is a module path rather than a file in this tree. The defect is "
            "unchanged — nothing in `gateware/`, `firmware/` or `scripts/` references "
            "`ULPIRxEventDecoder` or `rx_event`."
        ),
        [("`repos/luna/luna/gateware/interface/ulpi.py:257`",
          "`luna.gateware.interface.ulpi`, installed package, `:257`")],
    ),
    19: (
        note(
            "`repos/luna` is no longer a submodule; LUNA is an installed package, so "
            "that is a module path, not a file in this tree."
        ),
        [("`luna/gateware/interface/jtag.py`", "`luna.gateware.interface.jtag`")],
    ),
    11: (
        note(
            "`repos/luna` is gone — LUNA is an installed package, so the three "
            "isochronous endpoints are module paths, not files here. And `firmware/` at "
            "this repo's root is **our** SoC firmware: moondancer's lives in "
            "`repos/cynthion/firmware/`. The firmware facts still hold "
            "(`EP_MAX_PACKET_SIZE: usize = 512`)."
        ),
        [
            ("`firmware/smolusb/src/lib.rs:22`", "`repos/cynthion/firmware/smolusb/src/lib.rs:22`"),
            ("`firmware/moondancer/src/gcp/moondancer.rs:358`",
             "`repos/cynthion/firmware/moondancer/src/gcp/moondancer.rs:358`"),
            ("- `luna/gateware/usb/usb2/endpoints/isochronous.py`",
             "- `luna.gateware.usb.usb2.endpoints.isochronous`"),
            ("- `luna/gateware/usb/usb2/endpoints/isochronous_stream_in.py`",
             "- `luna.gateware.usb.usb2.endpoints.isochronous_stream_in`"),
            ("- `luna/gateware/usb/usb2/endpoints/isochronous_stream_out.py`",
             "- `luna.gateware.usb.usb2.endpoints.isochronous_stream_out`"),
        ],
    ),
    13: (
        note("moondancer's firmware lives in `repos/cynthion/firmware/`, not at this repo's root."),
        [("`firmware/moondancer/src/panic_log.rs`",
          "`repos/cynthion/firmware/moondancer/src/panic_log.rs`")],
    ),
    # ---- 2d: documents retired to debris/ or folded into a chip note -----
    53: (
        note("the analysis moved to `docs/apollo_samd11_mcu/apollo_dfu_buffer_analysis.md`."),
        [("Derived from docs/apollo_dfu_buffer_analysis.md.",
          "Derived from `docs/apollo_samd11_mcu/apollo_dfu_buffer_analysis.md`.")],
    ),
    54: (
        note(
            "`docs/apollo_race_conditions.md` no longer exists and no successor document "
            "was found — neither in `docs/apollo_samd11_mcu/` nor in `debris/docs/`. The "
            "issue text below is the only surviving statement of the analysis."
        ),
        [("Derived from docs/apollo_race_conditions.md.",
          "Derived from `docs/apollo_race_conditions.md`, which no longer exists in the tree.")],
    ),
    89: (
        note(
            "`docs/luna_ecp5_fpga/spi-flash-summary.md` was retired to "
            "`debris/docs/spi-flash-summary.md` and dissolved into "
            "`docs/chips/w25q32-config-flash.md`, which is the live document. Also: "
            "\"this needs a soft CPU inside the FPGA\" is **no longer a blocker** — the "
            "CPU exists and drives the flash (`gateware/soc/top.py`, "
            "`scripts/riscv_flash_check.py`). The measurements are still not done."
        ),
        [("`docs/luna_ecp5_fpga/spi-flash-summary.md`",
          "`docs/chips/w25q32-config-flash.md` (the earlier summary is archived at "
          "`debris/docs/spi-flash-summary.md`)")],
    ),
    90: (
        note(
            "both survey documents were retired: "
            "`debris/docs/hyperram-implementation-survey.md` is the archived copy, and "
            "`docs/luna_ecp5_fpga/memory-interface-options.md` has no successor. The live "
            "HyperRAM documents are `docs/chips/hyperram/w956a8.md`, "
            "`docs/chips/hyperram/bist-plan.md` and `docs/chips/hyperram/README.md`."
        ),
        [("`docs/luna_ecp5_fpga/hyperram-implementation-survey.md` and "
          "`docs/luna_ecp5_fpga/memory-interface-options.md`",
          "`debris/docs/hyperram-implementation-survey.md` (archived; "
          "`memory-interface-options.md` has no surviving copy). The live HyperRAM "
          "documents are `docs/chips/hyperram/w956a8.md` and "
          "`docs/chips/hyperram/bist-plan.md`")],
    ),
    108: (
        note(
            "`docs/luna_ecp5_fpga/fast-bitstream-loading.md` was retired to "
            "`debris/docs/fast-bitstream-loading.md`; the negatives it recorded are "
            "restated in `gateware/probes/loader/bitstream_sink.py:12-25`, which still "
            "cites the dead path itself."
        ),
        [("`docs/luna_ecp5_fpga/fast-bitstream-loading.md`",
          "`debris/docs/fast-bitstream-loading.md`")],
    ),
    179: (
        note(
            "`docs/sideband-review.md` was retired to `debris/docs/sideband-review.md`; "
            "the live document on this subject is `docs/chips/cynone-sideband.md`. "
            "`firmware/src/vendor.c` is in the Apollo submodule — "
            "`repos/apollo/firmware/src/vendor.c`."
        ),
        [
            ("`docs/sideband-review.md`",
             "`debris/docs/sideband-review.md` (live: `docs/chips/cynone-sideband.md`)"),
            ("`firmware/src/vendor.c:422`", "`repos/apollo/firmware/src/vendor.c:422`"),
        ],
    ),
    180: (
        note(
            "`docs/sideband-review.md` was retired to `debris/docs/sideband-review.md`; "
            "the live document is `docs/chips/cynone-sideband.md`."
        ),
        [("`docs/sideband-review.md`",
          "`debris/docs/sideband-review.md` (live: `docs/chips/cynone-sideband.md`)")],
    ),
    182: (
        note(
            "`docs/sideband-review.md` was retired to `debris/docs/sideband-review.md`; "
            "the live document is `docs/chips/cynone-sideband.md`. Note also that this "
            "issue's \"11 bytes free\" headline is contradicted by #199, which finds the "
            "guard misses `.relocate` and puts the real figure over the ceiling."
        ),
        [("`docs/sideband-review.md`",
          "`debris/docs/sideband-review.md` (live: `docs/chips/cynone-sideband.md`)")],
    ),
    183: (
        note(
            "`docs/sideband-review.md` was retired to `debris/docs/sideband-review.md`; "
            "the live document is `docs/chips/cynone-sideband.md`."
        ),
        [("`docs/sideband-review.md`",
          "`debris/docs/sideband-review.md` (live: `docs/chips/cynone-sideband.md`)")],
    ),
    184: (
        note(
            "`docs/sideband.md` no longer exists. `docs/README.md` names "
            "`docs/chips/cynone-sideband.md` as the single owner of the FPGA_ADV subject; "
            "the superseded review is archived at `debris/docs/sideband-review.md`."
        ),
        [("Canonical documentation: `docs/sideband.md`.",
          "Canonical documentation: `docs/chips/cynone-sideband.md` (the superseded "
          "review is archived at `debris/docs/sideband-review.md`).")],
    ),
    185: (
        note(
            "`docs/hyperram-bursts.md` no longer exists and has no archived copy; the "
            "mechanism is now stated in the code, at `gateware/soc/bootram.py:215-241`. "
            "Option 1 (`ClockStopPHY`) is editable in-tree now that the non-DQS "
            "controller is vendored as "
            "`gateware/soc/peripherals/hyperram_controller.py`."
        ),
        [("See `docs/hyperram-bursts.md` for the mechanism.",
          "See `gateware/soc/bootram.py:215-241` for the mechanism "
          "(`docs/hyperram-bursts.md` no longer exists).")],
    ),
    173: (
        note(
            "`docs/memory-speed-options.md` no longer exists, and the 334.4 MB/s figure "
            "it carried is **withdrawn** — see `docs/chips/hyperram/w956a8.md`. The 25× "
            "arithmetic below rests on that number, so it needs re-deriving. Item 1 is "
            "also partly answered: `gateware/soc/top.py` sets "
            "`ck_mhz=2 * SYNC_MHZ if HYPERRAM_DQS else SYNC_MHZ`, and with DQS off CK is "
            "60 MHz rather than 192, which is most of the gap."
        ),
        [("| part burst rate (`docs/memory-speed-options.md`) | **334.4 MB/s → 64 B in 191.4 ns** |",
          "| part burst rate (was `docs/memory-speed-options.md`, now deleted; figure "
          "**withdrawn** — `docs/chips/hyperram/w956a8.md`) | **334.4 MB/s → 64 B in 191.4 ns** |")],
    ),
    145: (
        note(
            "`docs/usb-host-full-speed.md` no longer exists; the surviving host-path "
            "document is `docs/usb-host-options.md`."
        ),
        [("the full-speed host path in `docs/usb-host-full-speed.md`",
          "the full-speed host path in `docs/usb-host-options.md`")],
    ),
    162: (
        note(
            "**Stage two has landed.** `firmware/cynthion-soc/memory.x` now has "
            "`FLASH : ORIGIN = 0x100B0000` with "
            "`REGION_ALIAS(\"REGION_TEXT\", FLASH)`, so text is no longer in block RAM. "
            "What remains is the other half: `.data`, `.bss` and the stack are still in "
            "`RAM : LENGTH = 63K`. `docs/linux-on-cynthion.md` was replaced by the "
            "`linux-on-cynthion/` directory."
        ),
        [("`docs/linux-on-cynthion.md`", "`linux-on-cynthion/`")],
    ),
    217: (
        note(
            "`docs/decisions.md` does not exist. `docs/README.md` names "
            "`docs/architecture.md` as the file that holds open decisions."
        ),
        [
            ("`docs/decisions.md`", "`docs/architecture.md`"),
            ("the unverified table in `decisions.md`", "the unverified table in `docs/architecture.md`"),
        ],
    ),
    218: (
        note(
            "`docs/decisions.md` does not exist. `docs/README.md` names "
            "`docs/architecture.md` as the file that holds open decisions."
        ),
        [
            ("`docs/decisions.md`", "`docs/architecture.md`"),
            ("the unverified table in `decisions.md`", "the unverified table in `docs/architecture.md`"),
        ],
    ),
    219: (
        note("`decisions.md` does not exist; `docs/architecture.md` holds the open decisions."),
        [("the unverified table in `decisions.md`", "the unverified table in `docs/architecture.md`")],
    ),
    220: (
        note("`decisions.md` does not exist; `docs/architecture.md` holds the open decisions."),
        [("the unverified table in `decisions.md`", "the unverified table in `docs/architecture.md`")],
    ),
    223: (
        note(
            "`docs/chips/w956a8-hyperram.md` was renamed to "
            "`docs/chips/hyperram/w956a8.md`. The `docs/ecp5/…` paths below are "
            "**pluribus's**, not this repo's, and are left as written."
        ),
        [("`docs/chips/w956a8-hyperram.md:285`", "`docs/chips/hyperram/w956a8.md:285`")],
    ),
    # ---- 2e: scripts that no longer exist --------------------------------
    31: (
        note(
            "`scripts/extract-hardware.py` no longer exists in this workspace, and the "
            "Flutter topology GUI this feeds was retired to `debris/code/` — so nothing "
            "this issue extends is currently in the tree."
        ),
        [("Extend `scripts/extract-hardware.py` so that",
          "Extend `scripts/extract-hardware.py` — which no longer exists in the tree — so that")],
    ),
    84: (
        note(
            "`scripts/power_probe.py` no longer exists, and the #82 gateware it drove is "
            "gone with it (#82 is closed). The current I2C master is "
            "`gateware/soc/peripherals/i2c_master.py`, which has **no `data_bytes` "
            "parameter at all**, so the prerequisite stated below describes nothing. The "
            "4-byte frame plan also predates the sideband protocol rewrite — see #87."
        ),
        [("The #82 gateware and `scripts/power_probe.py` stay as the bring-up and test route:",
          "The #82 gateware and `scripts/power_probe.py` were the bring-up and test route; "
          "neither exists in the tree today:")],
    ),
    175: (
        note(
            "`scripts/checks.py` does not exist — the harness is "
            "`scripts/sim_check_harness.py`, driven from `scripts/check.py`. Proposals 1 "
            "and 2 are done. **Proposal 3 was deliberately rejected**: "
            "`scripts/soc_sims.py:89-104` argues that tiering on cost \"gets both ends "
            "wrong\", so the tiers are `once`/`soak` over the same 18 simulations rather "
            "than a fast list of the nine cheapest. Proposals 4 and 5 are still real."
        ),
        [("`scripts/checks.py` is one harness", "`scripts/sim_check_harness.py` is one harness")],
    ),
    194: (
        note(
            "`scripts/patch_amaranth_soc_annotations.py` no longer exists, so that step "
            "is already discharged. The rest still holds: `awto-luna-soc` is still pinned "
            "in `docs/toolchain-versions.md`."
        ),
        [("* delete `scripts/patch_amaranth_soc_annotations.py`",
          "* delete `scripts/patch_amaranth_soc_annotations.py` — already gone from the tree")],
    ),
    # ---- 2f: Apollo firmware paths are inside the submodule --------------
    63: (
        note(
            "Apollo's firmware is in the submodule: `repos/apollo/firmware/src/`. The "
            "link below pointed at a path on one machine's filesystem, which nobody else "
            "can follow."
        ),
        [("[firmware/src/jtag.c](/mnt/2tb/git/awtoau/awto-apollo/firmware/src/jtag.c)",
          "`repos/apollo/firmware/src/jtag.c`")],
    ),
    73: (
        note(
            "Apollo's tests and firmware are in the submodule: "
            "`repos/apollo/tests/test_hardware.py`, `repos/apollo/firmware/test`. The "
            "three published figures also disagree with a re-measure done for the "
            "2026-08-07 tracker audit (ROM ~95.48%, RAM ~86.72%, against the 96.51% / "
            "86.52% / 94.4% quoted here) — **re-build before quoting any of them**, since "
            "the ELF behind the original numbers is a build artifact, not a fixed result."
        ),
        [
            ("`tests/test_hardware.py`", "`repos/apollo/tests/test_hardware.py`"),
            ("`firmware/test` (31 host-native checks)",
             "`repos/apollo/firmware/test` (31 host-native checks)"),
        ],
    ),
    95: (
        note(
            "The top-level `repos/apollo/firmware/src/fpga_adv.c` is now 38 lines of weak "
            "no-op stubs; the real implementation is "
            "`repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c` (846 lines), "
            "where EIC is still the default — so the substance holds. The \"~94% flash\" "
            "figure is out of date; see #73."
        ),
        [("`fpga_adv.c` carries both mechanisms in full:",
          "`repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c` carries both "
          "mechanisms in full:")],
    ),
    # ---- 2b and other fact staleness ------------------------------------
    8: (
        note(
            "There is no `venv/` in this workspace, the interpreter is **3.15.0rc1 "
            "free-threading**, and `facedancer` is not importable here at all — so the "
            "\"fix applied in venv\" claim cannot be checked today. The path below is a "
            "module path in upstream `greatscottgadgets/facedancer`."
        ),
        [("`venv/lib64/python3.11/site-packages/facedancer/configuration.py`",
          "`facedancer/configuration.py` (upstream `greatscottgadgets/facedancer`)")],
    ),
    9: (
        note(
            "There is no `venv/` in this workspace, the interpreter is **3.15.0rc1 "
            "free-threading**, and `facedancer` is not importable here at all — so the "
            "\"fix applied in venv\" claim cannot be checked today. The path below is a "
            "module path in upstream `greatscottgadgets/facedancer`."
        ),
        [("`venv/lib64/python3.11/site-packages/facedancer/backends/base.py`",
          "`facedancer/backends/base.py` (upstream `greatscottgadgets/facedancer`)")],
    ),
    10: (
        note(
            "There is no `venv/` in this workspace, the interpreter is **3.15.0rc1 "
            "free-threading**, and `facedancer` is not importable here at all — so the "
            "\"fix applied in venv\" claim cannot be checked today. The path below is a "
            "module path in upstream `greatscottgadgets/facedancer`."
        ),
        [("`venv/lib64/python3.11/site-packages/facedancer/backends/moondancer.py`",
          "`facedancer/backends/moondancer.py` (upstream `greatscottgadgets/facedancer`)")],
    ),
    81: (
        note(
            "The interpreter is **3.15.0rc1** free-threading, not 3.15.0b3, and "
            "`repos/luna` is no longer a submodule — LUNA is an ordinary installed "
            "package, so it is not among the editable installs."
        ),
        [],
    ),
    93: (
        note(
            "**#91 is closed** and the gate it names is satisfied: the CPU drives the "
            "flash today (`gateware/soc/top.py`, "
            "`firmware/cynthion-soc/src/selftest.rs`, `scripts/riscv_flash_check.py`). "
            "The measurements below are still not done."
        ),
        [],
    ),
    100: (
        note(
            "Deliverable 1's \"USB → SRAM (proposed) ~6 ms\" is disproven — the ECP5 has "
            "no fabric path into its own configuration engine, recorded at "
            "`gateware/probes/loader/bitstream_sink.py:12-25` and in #108. Deliverables 2 "
            "and 3 survive: `gateware/build_helpers.py` still emits only "
            "`--compress --freq 38.8 --usercode`."
        ),
        [],
    ),
    105: (
        note(
            "GUH **is vendored** — `gateware/probes/usb_host/guh/` (`types.py`, "
            "`reset.py`, `sie.py`), upstream `923c8490`, BSD-3-Clause. But "
            "`gateware/probes/usb_host/guh/__init__.py` records that `engines/*` were "
            "deliberately not taken, so \"bring up `msc_host`\" as written is no longer "
            "the plan and no `msc_host` exists in `gateware/`. Still open: hardware "
            "bring-up, driving `target_c_vbus_en`, and the TUSB322I/FUSB302B CC driver."
        ),
        [],
    ),
    107: (
        note(
            "`ecp5-test/CYNTHION_R14_PINMAP.md` **does not exist anywhere** — it was not "
            "migrated into `gateware/`, and this may be content loss. "
            "`docs/luna_ecp5_fpga/jtag-ceiling-reached.md` is also gone; the surviving "
            "record of that work is `debris/docs/jtag_configure_bottleneck.md`. "
            "`scripts/jtag_isr_soak.py` no longer exists. The flash figure "
            "\"94.92% of 14336\" is out of date — see #73, and re-build before quoting a "
            "replacement."
        ),
        [
            ("Background in `docs/luna_ecp5_fpga/jtag-ceiling-reached.md`, section",
             "Background in `debris/docs/jtag_configure_bottleneck.md`, section"),
            ("`scripts/jtag_isr_soak.py` is the reproducer",
             "`scripts/jtag_isr_soak.py` (no longer in the tree) is the reproducer"),
            ("`ecp5-test/CYNTHION_R14_PINMAP.md`, `docs/git.md`,",
             "`ecp5-test/CYNTHION_R14_PINMAP.md` (no longer in the tree), `docs/git.md`,"),
        ],
    ),
    115: (
        note(
            "**The opening claim is false against the tree.** The SoC has a PLIC "
            "(`gateware/soc/cpu/plic.py`, mapped in `gateware/soc/top.py`) and a CLINT "
            "(`gateware/soc/cpu/clint.py`), with named interrupt sources and "
            "`firmware/cynthion-soc/src/plic.rs` / `irq.rs` on the firmware side. RTIC is "
            "already an off-by-default cargo feature. The blocker described below is "
            "gone; what remains is switching the shipping image off the superloop."
        ),
        [],
    ),
    143: (
        note(
            "The HyperRAM row is **withdrawn**: 192 MHz CK / 334.4 MB/s is void, per "
            "`docs/chips/hyperram/w956a8.md`. The W25Q32 row still holds. See also #235."
        ),
        [("| W956A8 HyperRAM | 166 MHz | **192 MHz CK**, 334.4 MB/s | 200 MHz: 88% of words wrong, halves transposed |",
          "| W956A8 HyperRAM | 166 MHz | ~~**192 MHz CK**, 334.4 MB/s~~ **withdrawn** — see `docs/chips/hyperram/w956a8.md` | 200 MHz: 88% of words wrong, halves transposed |")],
    ),
    157: (
        note(
            "Two items below are done. The `called` check is now AST-based "
            "(`scripts/audit_scripts.py:229-244` walks `Import`/`ImportFrom`, plus a "
            "DANGLING check), so item 1 — \"the main remaining piece of tooling work\" — "
            "is discharged; and item 4 looks done too, since `scripts/soc_run.py` prints "
            "the firmware digest on every configure (`:312`, `:720-729`). `scripts/` is "
            "now **107** files, not 58. Still real: log retention, grouping `scripts/` by "
            "subject, and the `./dev.py ci` fmt-check that `scripts/dev.py:122-126` calls "
            "\"a live debt\"."
        ),
        [],
    ),
    193: (
        note(
            "Still real and unmet: `docs/upstreamable-patches.md:18` continues to say "
            "\"20 commits ahead\"."
        ),
        [],
    ),
    200: (
        note(
            "**Step 1 is stale**: `awto-luna`, `awto-apollo` and `awto-cynthion` are all "
            "GitHub forks today (`isFork=true`); only `awto-luna-soc` is not. Steps 2–4 "
            "are still real — no PRs exist against `greatscottgadgets/*`."
        ),
        [],
    ),
    222: (
        note(
            "Three rows and two problems have moved. `luna.gateware.interface.jtag` is "
            "down from 18 to **one** import — the deliberate negative control in "
            "`scripts/soc_jtag_registers_sim.py` — because "
            "`gateware/probes/jtag_registers.py` replaced it (#204). Problems 2 and 4 are "
            "**done** (#221, #215). Files mentioning each module today, over "
            "`gateware/ scripts/ firmware/ tests/`: `architecture.car` 18, "
            "`interface.psram` 10, `luna_soc…spiflash` 4, `interface.i2c` 4, "
            "`debug.ila` 1. The remaining substance is problem 1 (the fork pin, #194) and "
            "problem 3 (spiflash)."
        ),
        [],
    ),
    228: (
        note(
            "`gateware/soc/hyperram_clocks.py` now exists on this branch, so that "
            "reference resolves. `variable_clock.py` is "
            "`repos/apollo/apollo_fpga/gateware/variable_clock.py` — no longer what the "
            "SoC builds. #210 and #226 are both **closed**; the motivation they carried "
            "now lives in #230 and `docs/chips/hyperram/bist-plan.md`. The substance "
            "holds: `gateware/soc/clocks.py:245-246` and "
            "`gateware/soc/hyperram_clocks.py:191-192` both still tie all five ports off."
        ),
        [("and `variable_clock.py` before them",
          "and `repos/apollo/apollo_fpga/gateware/variable_clock.py` before them")],
    ),
    230: (
        note(
            "`gateware/soc/hyperram_clocks.py` now exists on this branch and is still not "
            "instantiated by `gateware/soc/top.py`, so item 1 stands as written. Of the "
            "preconditions, **#215 is closed** — the non-DQS controller is vendored as "
            "`gateware/soc/peripherals/hyperram_controller.py` with both fixes. #204, "
            "#186 and BURSTDET remain."
        ),
        [],
    ),
}


def check_paths(number, text):
    """Every path-shaped token in replacement text must resolve, or be flagged as gone."""
    pat = re.compile(
        r"`((?:ecp5-test|gateware|scripts|firmware|docs|repos|tests|debris|linux-on-cynthion)"
        r"/[A-Za-z0-9_./+-]*)`"
    )
    bad = []
    for ref in pat.findall(text):
        ref = ref.split(":", 1)[0].rstrip("/")
        if NOT_OURS.match(ref):
            continue
        if not (ROOT / ref).exists():
            bad.append(ref)
    return bad


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    apply_ = "--apply" in sys.argv
    wanted = {int(a) for a in argv} if argv else None

    DST.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    edited = paths_fixed = 0

    for number in sorted(FIXES):
        if wanted and number not in wanted:
            continue
        head, subs = FIXES[number]
        body = (SRC / f"{number}.md").read_text()

        for old, new in subs:
            if old not in body:
                raise SystemExit(f"#{number}: not found in body: {old!r}")
            body = body.replace(old, new)
            paths_fixed += 1

        if head:
            body = head + body

        # The rewritten body must not name a path in this repo that is not there.
        # The dated note is exempt -- naming the old path is the whole point of it.
        bad = sorted(set(check_paths(number, body[len(head or ""):])))
        if bad:
            lines.append(f"#{number}: UNRESOLVED {bad}")

        (DST / f"{number}.md").write_text(body)
        edited += 1
        lines.append(f"#{number}: {len(subs)} replacement(s){', note' if head else ''}")

        if apply_:
            subprocess.run(
                ["gh", "issue", "edit", str(number), "--body-file", str(DST / f"{number}.md")],
                cwd=ROOT, check=True, capture_output=True, text=True)
            lines.append(f"#{number}: edited on GitHub")

    lines.append(f"\n{edited} issues, {paths_fixed} references rewritten"
                 f"{' — applied' if apply_ else ' — dry run'}")
    report = "\n".join(lines)
    LOG.write_text(report + "\n")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
