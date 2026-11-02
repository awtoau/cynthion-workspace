# History: upstream, toolchain and install docs

Removed from the live docs because superseded, withdrawn or retired. Text below is verbatim
from the named source file as of its removal. Nothing here is current.

- Why upstreaming is withdrawn: [`../upstream-boundary.md`](../upstream-boundary.md#no-upstreaming)
- Issues: parked plan [#102](https://github.com/awtoau/cynthion-workspace/issues/102), PR-branch container [#200](https://github.com/awtoau/cynthion-workspace/issues/200), stale-doc correction [#193](https://github.com/awtoau/cynthion-workspace/issues/193), install.py retirement [#157](https://github.com/awtoau/cynthion-workspace/issues/157)


---

## Upstream patch plan

Source: `docs/upstream-patch-plan.md`. Withdrawn: luna/cynthion maintainers refuse LLM-generated PRs and issues. Related [#102](https://github.com/awtoau/cynthion-workspace/issues/102).

## Upstream patch plan: Great Scott Gadgets

What we carry locally that upstream might want, in what order it has to be
applied, and what each patch would have to demonstrate on hardware before it
could be sent.

The governing validation rules are in a separate document,
`upstream-patch-process.md` (`upstream-patch-process.md`). Two files rather than
one: this plan is per-repo content that goes stale as commits land, the process
is a fixed procedure that should not change when the inventory does.

**Nothing here has been submitted, and nothing here is approved for submission.**
Sending anything to a repository we do not own needs explicit confirmation
first, per the workspace rules on public and upstream targets.

**Status of the measurements below:** the ROM figures were measured, by building
each ordering in a detached worktree (`scripts/apollo_rom_sizing.py`, logs in
`tmp/logs/apollo_rom_sizing-*.log`). **No hardware was touched and no hardware
test was run** in producing this plan. Every "verified on hardware" claim quoted
from a commit message is that commit author's claim, inherited, not re-confirmed
here — and under the process rules it does not count until re-run as part of a
full sequence.

### Divergence, measured

| Repo | Commits ahead of upstream | Notes |
|---|---|---|
| `repos/apollo` | **34** | `upstream/main` = `04507df`, ours = `0b1529a` |
| `repos/cynthion` | **25** | `upstream/main`, ours = `7fa0c6a` |
| `repos/luna` | **0** | HEAD `6771b6d` is an ancestor of `upstream/main` |
| `repos/facedancer` | **0** | HEAD `9f12988` is an ancestor of `upstream/main` |

Luna and facedancer carry no local work at all. They sit exactly on the
upstream release tags (0.2.3, 3.1.3) and their HEADs are ancestors of
`upstream/main`. There is nothing to submit from either, and no plan is needed
for them beyond a submodule bump when we want newer upstream code.

### Where `docs/upstreamable-patches.md` is wrong or stale

That document was the starting point. Corrections found:

1. **"20 commits ahead" is wrong — it is 34.** The count predates the whole
   sideband, quad-SPI, PLL and configure-speed series.

2. **It covers only apollo.** `repos/cynthion` has 25 local commits and is not
   mentioned. Several are upstream-shaped (a real `#28` log-message bug, a
   duplicate-`ep_iso_in` fix, a clippy-clean pass, a udev rule).

3. **The ROM headroom premise is badly out of date, and in the dangerous
   direction.** The task framing carried an assumption of "around 87% of 14 KB".
   Measured, `upstream/main` builds at **13768 B = 96.04%, 568 bytes free**. The
   old doc's own quoted figures (96.51%, ~500 bytes free) are closer, but it
   presents the ROM-savings commits as "useful if upstream is also tight on that
   part, irrelevant otherwise" — that is backwards. Upstream **is** tight on that
   part, at 568 bytes. The savings patches are not optional garnish; without
   them, or without LTO, several of the other patches cannot land at all.

4. **It omits the `01ae228` ordering constraint entirely** (see below). That is
   the single most important operational fact in the apollo set and it is not
   recorded anywhere.

5. **`4bf7691` is described nowhere, and it is not what its subject says.**
   The subject is "enable LTO, and wire up the sideband command layer" — it
   bundles a general-purpose build-system change with project-specific feature
   work. Any upstream submission has to split it; only the `firmware/Makefile`
   hunk plus the `__udivsi3` fix are upstreamable.

6. **The `trigger_fpga_reconfiguration()` / INITN item is listed under "Ready — a
   clear bug with a verified fix"**, but its own text says "**Not yet
   attempted**". It is a diagnosed bug with a *proposed* fix, which is a
   different thing. Under the process rules it cannot be submitted in that
   state.

7. `12e6d97` (`exit-dfu`) is described in detail but its commit hash is not
   given, and it is presented as verified while having **no test in the suite**
   (see the inventory).

---

### Part 1 — Apollo inventory (34 commits)

`GSG` = genuine upstream bug fix. `OURS` = project scaffolding or investigation.
`SPLIT` = contains both and must be divided before submission.

#### Group A — firmware fixes and ROM savings (upstreamable)

| Commit | What it does | Class | Test today |
|---|---|---|---|
| `39a2213` | Sticky JTAG/UART pin mutual-exclusion lock. On r1.4 the console UART and JTAG share PA10/11/14/15 with no hardware arbitration; a CDC line-coding callback during a JTAG session re-pinmuxes PA14 out from under it and corrupts the session. Adds `apollo_mode` HOLD↔JTAG_PROGRAM lock, gates the three CDC callbacks and `uart_configure_pinmux()`. | **GSG** — real corruption bug on shipping hardware | **Yes.** `firmware/test/test_apollo_mode.c` (host-native, 16 checks) + `tests/test_hardware.py::JTAGUARTExclusionHILTest` (2 HIL tests) |
| `df4a93b` | Host-triggerable boot-to-DFU vendor request `0xed`, plus host `boot_to_dfu()`. Defers the reboot to the ACK stage, because `tud_dfu_runtime_reboot_to_dfu_cb()` arms the WDT and hangs — doing it in the setup handler makes every success look like a USB failure. | **GSG**, with a caveat: upstream may want a different request number | **Yes.** `tests/test_hardware.py::BootToDFUHILTest`, gated behind `APOLLO_TEST_ALLOW_REBOOT=1`, restores the device in `tearDown` |
| `6cc219e` | Gates conflicting vendor requests during JTAG programming. Two-stage: `JTAG_START` takes the pin lock but gates nothing; the first programming-class request escalates to `MODE_JTAG_PROGRAMMING` and only then are reconfigure / force-offline / takeover / debug-SPI refused. Adds `EMERGENCY_RESET` (`0xec`) as the one legal preemption path. | **GSG** — completes the exclusivity model | **Yes.** `test_apollo_mode.c` extended 16 → 31 checks |
| `da564f8` | Two ISR/main-loop races. `fpga_online` written from USB interrupt context, read from `led_task()`, not `volatile`. `edge_counter` read-then-clear in `fpga_adv_task()` was not atomic against `EIC_Handler`, so an edge landing between the two statements is lost, under-counting the window that feeds `fpga_requesting_port()`. | **GSG** — both are live in shipping firmware | **No dedicated test.** Commit rests on "the FPGA USB handoff still works". A race that is lost 1-in-N times is exactly what a pass/fail smoke test cannot see |
| `973fa78` | `CFG_TUD_VENDOR=0` on d11. The vendor *class driver* was compiled in but is never used — Apollo's vendor requests are control transfers via `tud_vendor_control_xfer_cb()`, which is device-stack, not class-driver. No vendor interface in the config descriptor, no `tud_vendor_*()` call anywhere. −520 B ROM, −680 B RAM. | **GSG** — pure dead weight | Covered incidentally by the CLI smoke tests |
| `651b027` | `msft_10_compat_id` / `msft_10_ext_props` declared `const` so they live in `.rodata` not `.data`. −176 B RAM. Never written; only ever an IN-transfer source. | **GSG** | `test_safe_commands_respond_correctly` exercises `GET_MS_DESCRIPTOR` |
| `01ae228` | Four ROM savings: `static const` on the `board_rev` threshold table, the LED pin table (was rebuilt on the stack in four functions), `jtag_deinit()`'s `gpio_pins[]`; and internal linkage for 13 `vendor.c` handlers and 2 `jtag_tap` helpers. −276 B. | **GSG** | No dedicated test; behaviour-preserving by construction, but see the note on `--gc-sections` below |

#### Group B — host-side Python fixes (upstreamable)

| Commit | What it does | Class | Test today |
|---|---|---|---|
| `8054f62` | `FlashBridgeConnection` becomes a context manager instead of relying on `__del__`. Handing the port back is a USB transfer, so doing it in a destructor means libusb I/O at GC or interpreter-shutdown time when the context may be gone — that segfaults instead of reporting, and leaves the port with the FPGA so Apollo appears to have vanished. Upstream issue **[#75](https://github.com/awtoau/cynthion-workspace/issues/75)**. | **GSG** — clear bug, clear fix | **Yes,** `test_4_flash_fast_is_not_broken`, gated behind `APOLLO_TEST_ALLOW_FLASH_FAST=1` |
| `6f16848` | `apollo install-udev`. The shipped rules cover `1209:000a` and `000e` but not `000f`, which is what the flash bridge enumerates as, so `flash-fast` failed with EACCES for non-root users and left the USB stack wedged. | **GSG** (the missing rule certainly is; the *command* is a design choice upstream may want differently) | No automated test — it writes to `/etc/udev/rules.d` |
| `12e6d97` | `apollo exit-dfu`. `boot-to-dfu` had no inverse; a board in the bootloader could only be recovered by physically unplugging it. Uses a zero-length `DFU_DNLOAD` (**not** `DFU_DETACH` — the bootloader has no detach case) and **must** claim the interface or it stalls `LIBUSB_ERROR_PIPE`. Python only. | **GSG** — completes an existing command pair | **No test.** Notable given it is the doc's flagship "ready" item |
| `e034daa` | ECP5 SRAM configuration 1.53× faster. Host: plumb `ignore_response` through the bitstream burst — the burst is write-only but was doing a `GET_IN_BUFFER` per chunk, 739 ms of 2575 ms. Firmware: pipeline SERCOM SPI, queueing the next byte on DRE instead of waiting for RXC. +28 B flash. | **GSG** — real speedup, no behaviour change | **No dedicated test.** Commit cites 6 consecutive runs with DONE set and no error bits. Needs a correctness test, not just a timing one |

#### Group C — test infrastructure (upstreamable, needs reshaping)

| Commit | What it does | Class | Notes |
|---|---|---|---|
| `24b3b7a` | Smoke-tests every apollo CLI command | **GSG-useful** | Upstream has **no** Python test suite — `git ls-tree upstream/main` finds only `apollo_fpga/support/selftest.py` and the d21 selftest C files. These would be a new thing for the project, so shape is upstream's call |
| `daecad7` | Command responses + flash write paths | **GSG-useful** | Adds `APOLLO_TEST_ALLOW_FLASH_WRITE` gating |
| `9c72930` | PAC1954 power-monitor HIL tests | **GSG-useful** | 4 tests |

#### Group D — ours, not upstream's (do not submit)

These are for this project's own investigation. Interesting eventually, not bug
fixes, not proposed.

- **Sideband / FPGA_ADV command link** — `4a46b24`, `a7b8283`, `b48d4bf`,
  `ca32e5b`, `9ec249a`, `6cdc7c3`, `0b1529a`, and the feature hunks of
  `4bf7691`. A whole new protocol between the MCU and the FPGA over the FPGA_ADV
  pin. This is a design proposal, not a fix; upstream issue [#68](https://github.com/awtoau/cynthion-workspace/issues/68) territory.
- **Quad-SPI flash investigation** — `9b8ffb8`, `2418362`, `f42433d`, `1e90e5c`,
  `ace1fd8`, `c3c1911`, `4ead7df`, `42bffe6`, `7a8e304`. Gateware for reading
  the config flash faster, via Glasgow's controller. Exploratory.
- **PLL work** — `0ab00de`, `c3c5fbe`. Variable clock via `ecppll`.
- **`74db0e6`** — synthetic JTAG benchmark on vendor request `0xb9`. A
  measurement tool. It also *moved* to `0xb9` to clear a collision, which is a
  hint that our local request-number allocations have drifted from upstream's
  and need reconciling before any vendor-request patch is sent.

#### The split case

**`4bf7691` must be divided.** As committed it contains:

| Hunk | Upstreamable? |
|---|---|
| `firmware/Makefile`: `-flto=auto -flto-partition=one`, `-Wl,--entry=Reset_Handler -Wl,-u,exception_table` | **Yes** — general build improvement, large win |
| `fpga_adv.c`: fold the per-byte UART timeout to a compile-time constant, removing a runtime `__udivsi3` (266 B of soft division for one divide) | **Yes** — independent ~300 B saving |
| `vendor.c`: `0xFFFE` sub-command issuing a sideband command | **No** — Group D feature work |
| `handle_fpga_adv_mode` → `static` | Belongs with `01ae228`'s linkage pass |

Only the first two go upstream, as **patch A1** below.

---

### Part 2 — the ordering

#### The LTO-first hypothesis: **confirmed, and by a wider margin than expected**

Measured by building every position of each candidate ordering. The app region
is 14336 B (16 KB flash less `BOOTLOADER_SIZE = 0x800` for Saturn-V).

**Chronological order** — the sequence these were developed in:

| Position | ROM | % | Free |
|---|---|---|---|
| baseline `upstream/main` | 13768 | 96.04% | 568 |
| `+39a2213` | 13864 | 96.71% | 472 |
| `+df4a93b` | 13876 | 96.79% | 460 |
| `+6cc219e` | 14104 | **98.38%** | **232** |
| `+973fa78` | 13584 | 94.75% | 752 |
| `+651b027` | 13588 | 94.78% | 748 |
| `+da564f8` | 13616 | 94.98% | 720 |
| `+01ae228` | 13340 | 93.05% | 996 |

It never technically overflows — but it passes through **232 bytes free**, and
it gets there by applying three feature patches to a tree that starts with 568.
That is not a sequence to hand to someone else. Any reviewer who reorders it,
drops one patch, or builds with a different GCC lands in overflow, and the
failure arrives as a link error three patches deep.

**LTO first** (`4bf7691`'s Makefile hunk, then the savings, then the features):

| Position | ROM | % | Free |
|---|---|---|---|
| baseline `upstream/main` | 13768 | 96.04% | 568 |
| `+A1` LTO switch only | 10800 | **75.33%** | **3536** |
| `+973fa78` | 10324 | 72.01% | 4012 |
| `+651b027` | 10324 | 72.01% | 4012 |
| `+39a2213` | 10412 | 72.63% | 3924 |
| `+df4a93b` | 10432 | 72.77% | 3904 |
| `+6cc219e` | 10580 | 73.80% | 3756 |
| `+da564f8` | 10600 | 73.94% | 3736 |
| `+01ae228` | 10556 | 73.63% | 3780 |

**The single LTO patch reclaims 2968 bytes — more than five times the entire
headroom upstream has today.** Worst-case headroom across the whole sequence
goes from 232 B to 3536 B, a 15× improvement, and no position exceeds 75.33%.

So the hypothesis holds, and the reasoning behind it is stronger than "it eases
the juggling": at 568 bytes of headroom, **upstream cannot accept the Group A/B
feature patches at all without either LTO or the savings patches first.** LTO is
not a convenience here, it is the enabling patch. Submitting the pin lock
(`+96 B`), boot-to-DFU (`+12 B`) and vendor gating (`+228 B`) against current
upstream consumes 336 of 568 bytes and leaves the project at 98.38% — upstream
would be right to refuse on that basis alone.

#### The ordering constraint that was found by testing, not by reading

**`01ae228` must come after `39a2213`, `df4a93b` and `6cc219e`.** Putting it
early — which the naive "savings first, features later" reading suggests —
**fails to cherry-pick**:

```
CONFLICT cherry-picking 01ae228 at step '+01ae228 reclaim 276 B'
```

Cause: `01ae228` makes `jtag_deinit()`'s `gpio_pins[]` `static const` and gives
13 `vendor.c` handlers internal linkage. `39a2213` inserts a lock release into
`jtag_deinit()` at the same lines, and `df4a93b`/`6cc219e` add handlers to and
restructure the `vendor.c` dispatch tables. A fine-grained cleanup of a region
cannot precede the structural change to that region.

This is the answer to the "patch that only applies cleanly in one position"
question: **`01ae228` is that patch, and it must be last in the firmware
series.** It is recorded in the script as `STEPS_LTO_FIRST` (fails) versus
`STEPS_LTO_FIRST_FIXED` (passes) so the constraint stays proven rather than
remembered.

#### The proposed order

Three independent series. Apollo firmware must go in this order; the Python
series is order-independent against it; cynthion is a separate repo.

**Series A — Apollo firmware (strictly ordered)**

| # | Patch | Why here |
|---|---|---|
| **A1** | `4bf7691` **split**: `firmware/Makefile` LTO hunk + the `__udivsi3` fix | **First, and it must be first.** Creates the headroom every later patch spends. Also the highest-risk patch in the set, so it wants to be validated alone against a known-good tree rather than on top of four other changes |
| **A2** | `973fa78` disable unused TinyUSB vendor class driver | Pure deletion, no interaction. Second because the two savings patches are the cheapest way to widen the margin further before any behaviour changes |
| **A3** | `651b027` WCID descriptors to flash | Touches `vendor.c` data declarations only. Must precede A4–A6, which restructure `vendor.c` dispatch — the other way round conflicts |
| **A4** | `39a2213` JTAG/UART pin lock | First behaviour change. Introduces `apollo_mode`, which A5 and A6 both build on. Has the best test coverage in the set, so it is the right place to start exercising hardware |
| **A5** | `df4a93b` boot-to-DFU vendor command | Depends on A4 only for `vendor.c` context. Before A6 because A6's gating allow-list explicitly names `BOOT_TO_DFU` as an escape hatch — reversing them means A6 references a request that does not exist yet |
| **A6** | `6cc219e` vendor-request gating | Extends A4's lock with escalation and A5's request set. Semantically last of the mode work |
| **A7** | `da564f8` ISR/main-loop races | Independent of A1–A6 (touches `fpga.c`, `fpga.h`, `fpga_adv.c`). Placed here because it is order-independent and because its own commit message flags that if upstream [#68](https://github.com/awtoau/cynthion-workspace/issues/68) lands, this hunk goes away — so it should be the easiest patch to drop |
| **A8** | `01ae228` reclaim 276 B | **Must be last.** Conflicts in any earlier position (proven above). A cleanup pass over regions A4–A6 restructure |

**Series B — Apollo host-side Python (order-independent)**

No file overlaps with Series A and no ordering constraints among themselves,
except that B1 and B2 both touch `apollo_fpga/commands/cli.py` and are on the
same subject, so they read better together and in this order.

| # | Patch | Why here |
|---|---|---|
| **B1** | `6f16848` `install-udev` (the missing `1209:000f` rule) | The rule is the precondition for testing B2 as a non-root user |
| **B2** | `8054f62` flash-bridge port handback ([#75](https://github.com/awtoau/cynthion-workspace/issues/75)) | The fix proper. Pairs with B1 as the two halves of [#75](https://github.com/awtoau/cynthion-workspace/issues/75) |
| **B3** | `12e6d97` `exit-dfu` | Independent. Touches `__init__.py` and `cli.py`. Naturally follows A5 conceptually (it is the inverse of boot-to-DFU) but has no code dependency |
| **B4** | `e034daa` configure speed-up | **Straddles.** Its `spi.c` hunk is firmware (+28 B) and its `ecp5.py` hunk is host. Either submit as one cross-cutting patch after both series, or split. Recommend submitting last, whole, since the two halves are jointly measured |

**Series C — test infrastructure**

`24b3b7a`, `daecad7`, `9c72930`. All three touch only `tests/test_hardware.py`
and are strictly additive, so they are order-independent among themselves and
against everything else. **But they are the enabling work for the process rules**
— see the note below on the ordering paradox.

#### Order-independence summary

- **Textually conflicting:** `01ae228` against `39a2213`/`df4a93b`/`6cc219e`
  (`jtag_tap.c`, `vendor.c`). `651b027` against A4–A6 if reversed (`vendor.c`).
  The Group D sideband commits against A1's `fpga_adv.c` and `vendor.c` hunks.
- **Semantically conflicting (each fits alone, together they overflow):** the
  A4+A5+A6 trio against `upstream/main` without A1. +336 B against 568 B free is
  survivable; add `e034daa`'s +28 B and any future feature and it is not. This
  is the size interaction the LTO-first ordering exists to remove.
- **Order-independent:** all of Series B against all of Series A. `da564f8`
  against everything (different files). Series C against everything.
- **Applies cleanly in only one position:** `01ae228` — last.

#### The ordering paradox in Series C

Process rule 1 says every patch needs a test. Series C *is* the tests. So Series
C cannot itself satisfy rule 1 in the normal way, and if Series C is submitted
last then A1–A8 were validated against tests that were not yet upstream.

Resolution: **Series C goes first in submission order, even though it is last in
dependency order.** Upstream has no Python test suite at all, so there is
nothing to extend — the suite has to exist before any patch in Series A or B can
be said to have an upstream-visible test. Series C's own validation is that it
passes against unmodified `upstream/main` and that each test fails when its
target patch is reverted (rule: a test that cannot fail is not a test).

---

### Part 3 — per-patch test requirements

What a hardware test would have to *demonstrate* — not "the device still
enumerates", but "the specific broken thing is now not broken". Where a test
exists it is named; where it does not, what it would need to do.

| Patch | Test requirement | Exists? |
|---|---|---|
| **A1** LTO | Must catch the dead-binary failure mode, which does not announce itself. Assert the linked vector table resolves Reset, SysTick, USB, EIC, SERCOM1 and TC1 to real code rather than `Dummy_Handler` (`scripts/apollo/verify_vectors.py` does this and **is untracked in the apollo repo** — it would have to be contributed, or reimplemented, for upstream to have the guard). Then, because LTO rewrites every byte, exercise the paths where a miscompilation hides: the CDC console (TinyUSB FIFO code LTO reshapes via constprop), the EIC/TC1 ISR paths, and a JTAG scan. **A passing link proves nothing here.** | Partial — the guard script exists in the workspace, not in the repo |
| **A2** vendor drv | Prove the removed driver was genuinely unused: enumerate, walk the config descriptor and assert no vendor interface, then confirm every vendor *request* still answers. The risk is not that it breaks loudly but that some request quietly depended on the class driver's presence | Incidental only — wants an explicit descriptor assertion |
| **A3** WCID | `GET_MS_DESCRIPTOR` returns both descriptors byte-identically to before: 40 B compat ID and 142 B ext props, headers intact. A `const` move that corrupted an offset would still return *something* | `test_safe_commands_respond_correctly` touches it; needs the byte-exact comparison |
| **A4** pin lock | Must prove the OS does **not** provide this exclusion: open JTAG via libusb and the CDC interface via `cdc_acm` *simultaneously* (they are independently openable), drive serial traffic during a JTAG session, and assert the session is uncorrupted and serial is refused — then assert serial recovers after `jtag_deinit()` | **Yes** — `JTAGUARTExclusionHILTest` (2 tests) + `test_apollo_mode.c` (16 checks) |
| **A5** boot-to-DFU | Device actually appears as the Saturn-V bootloader afterwards (check VID/PID, not just "the request returned"), and the host sees a *clean* completion — the whole point of the ACK-stage deferral is that a success must not look like a USB error. Must restore the device afterwards or the suite is single-shot | **Yes** — `BootToDFUHILTest`, `APOLLO_TEST_ALLOW_REBOOT=1`, reflashes in `tearDown` |
| **A6** gating | Three things: a plain `jtag-scan` is unaffected (no escalation); a conflicting request *is* refused once escalated; and the gate *releases* — `apollo info` works after the session. Plus `EMERGENCY_RESET` round-trips and genuinely releases the pin lock. Escalation must not drop the pin lock | **Yes** for the state machine (31 checks); the release-after-session path wants an explicit HIL assertion |
| **A7** ISR races | **This is the hard one and no adequate test exists.** A race lost 1-in-N times passes any single-shot test. `fpga_online`: needs the compiler-caching behaviour observed, e.g. assert the flag is `volatile` in the disassembly, since a runtime test cannot reliably provoke it. `edge_counter`: drive advertisement edges at a known rate and assert the counted total matches within tolerance over many windows — an under-count is the symptom. A "handoff still works" check does not test either bug | **No.** Weakest coverage in the set relative to bug severity |
| **A8** ROM savings | Behaviour-preserving by construction, so the test is a size assertion plus proof nothing was discarded. **`--gc-sections` makes the headline number untrustworthy**: an uncalled function looks like a saving while being absent. Assert the expected symbols are present (or verifiably inlined, not dropped) — under LTO a missing symbol may mean inlined, so confirm against disassembly. Then LED, board-rev detect and JTAG paths still function | **No** — needs a symbol-presence assertion, not just a size delta |
| **B1** udev | Rule file contains `1209:000f`; `--print-only` output is parseable; refuses to install without root; errors cleanly on non-Linux. Then the real test: `flash-fast` succeeds **as a non-root user**, which is the actual bug | No automated test |
| **B2** [#75](https://github.com/awtoau/cynthion-workspace/issues/75) handback | Two failure modes, both must be provoked deliberately: (a) transfer fails mid-flash → assert the port went back to Apollo *before* the exception propagated, and Apollo is still on the bus; (b) `close()` after a failed `__init__` touches no USB. Plus no segfault, which means asserting the interpreter exit status, not just the absence of a traceback | **Yes** — `test_4_flash_fast_is_not_broken`, `APOLLO_TEST_ALLOW_FLASH_FAST=1` |
| **B3** `exit-dfu` | Full round trip: `boot-to-dfu`, confirm the bootloader is present, `exit-dfu`, confirm the *application* is running again with no replug. Must also assert the two things that cost debugging cycles: that `DFU_DETACH` is refused (so nobody "simplifies" it back) and that an unclaimed interface stalls `LIBUSB_ERROR_PIPE` | **No.** Flagged as "ready" in the old doc with no test at all |
| **B4** configure speed | Correctness first, speed second: after `configure`, the ECP5 status register reads DONE with no error bits, over enough consecutive runs to catch the dropped-byte corruption the pipelining nearly introduced (its earlier racy version silently corrupted TDO and produced an all-zero status register — so **assert the status register is non-zero and correct**, never just "no exception"). Then assert the speedup as a regression guard | **No dedicated test** |
| **C1–C3** | Each must fail when its target patch is reverted. A test that passes both with and without the fix is not evidence | Self-testing by construction |

---

### Part 4 — Cynthion inventory (25 commits)

Less mature than the apollo set: more scaffolding, fewer clean fixes.

#### Plausibly upstreamable

| Commit | What it does | Class | Test today |
|---|---|---|---|
| `c0144f4` | Fixes a wrong function name in a `write_endpoint` log message (upstream **[#28](https://github.com/awtoau/cynthion-workspace/issues/28)**) | **GSG** — trivial, obviously correct | None needed beyond a build |
| `56a64c2` | Removes a duplicate `ep_iso_in` from the facedancer module hierarchy | **GSG** — an actual elaboration bug | None. Wants a gateware elaboration test |
| `fc81a06` | Makes `make clippy` pass cleanly (**[#35](https://github.com/awtoau/cynthion-workspace/issues/35)**) | **GSG** — lint hygiene | `make clippy` is the test |
| `7378797` | Event queue overflow drops the event instead of spinning | **GSG** — a spin on overflow is a hang | None. Wants a queue-saturation test |
| `6fa3e0a` | Clamps endpoint `max_packet_size` to `EP_MAX_PACKET_SIZE` | **GSG** — bounds check | None |
| `edf35c9` | udev rule for the Apollo flash bridge (`1209:000f`) | **GSG** — same missing rule as apollo `6f16848`; **submit these two together or neither**, they are one bug across two repos | None |
| `482bcf2` | Integer overflow checks in all profiles | Arguable — a policy change with a runtime cost. Upstream's call | None |

Every hash in this document was read from `git log` in this workspace on
2026-07-30 and should be re-confirmed with `git show` before it is placed in a
submission: local history can be rebased, and a wrong hash in a patch series is
an error that survives review.

#### Ours, not upstream's

- `d036598`, `b0c06d7` — rebuilt `moondancer.bin` asset and a `make bin` target
  to produce it. Build plumbing for us.
- `4d8688c`, `131927a` (stack canary), `482bcf2` — fault-injection verbs and
  hardening for our own `#14`/`#16` investigation. The canary is defensible
  upstream but is framed as debug instrumentation.
- `4ab5361` — isochronous IN **skeleton** across gateware, firmware and Python.
  Explicitly incomplete; upstream `#13` work in progress.
- `ef9addb`, `7fa0c6a` — FPGA_ADV UART sideband streamer and the r1.4
  bidirectional-with-pull-up platform change. The cynthion half of the Group D
  sideband work; goes with it or not at all.
- `4fd1f7a`, `5848814`, `efd5009`, `593cf4a` — `apollo-mux.py`, `apollod.py`,
  `reset-cynthion.sh`. Our tooling. **Also: `593cf4a` and `efd5009` add a `.sh`
  file, which violates this workspace's own no-shell-scripts rule** — that is a
  local cleanup item independent of upstreaming.
- `5745112`, `6863fd6`, `1d24249` — `awto.md` and docs naming our project.
  **Never upstreamable**: they carry our project's identity and internal issue
  numbers.
- `24af0a4`, `1d08505`, `c8b591e` — `.gitignore` entries for our `tmp/` and
  `venv/` layout. Project-local.

#### The cynthion prerequisite

Upstream cynthion's local `53d3ea4` "add `__init__` methods to CSR Register
classes for Amaranth 0.5.x" is **exactly the class of change that the
awto-luna-soc work just made redundant.** That work took upstream luna-soc 0.3.2,
backported amaranth-soc `d8b5892` (Python 3.14 CSR annotations), and then
reverted five such `__init__` workarounds — 94 lines across 8 files.

So `53d3ea4` should be **re-examined before any cynthion submission**, and
probably reverted locally rather than sent anywhere: if the amaranth-soc fix is
the right layer, then a workaround in cynthion is debt, not a patch. Sending it
upstream would export a workaround for a bug fixed elsewhere.

---

### Part 5 — what cannot be upstreamed, and why

**Ours by nature — project identity or internal references**
- `5745112`, `6863fd6`, `1d24249` (cynthion `awto.md` and notes): our project
  name, our issue numbers, our hardware bench.
- `.gitignore` changes for our directory layout.
- Anything referencing `awtoau/cynthion-workspace#NN`. Several otherwise-good
  apollo commit messages carry these `Refs:` lines and **every one must be
  rewritten before submission** — an upstream reader cannot resolve them and
  they leak our issue tracker.

**Ours by intent — investigation, not fixes**
- The entire sideband/FPGA_ADV protocol (apollo Group D + cynthion `ef9addb`,
  `7fa0c6a`). A design proposal for a link that does not exist upstream. It
  competes with upstream's own [#68](https://github.com/awtoau/cynthion-workspace/issues/68) direction, so it is a discussion to open, not
  a patch to send.
- Quad-SPI flash work, PLL/`ecppll` work, the JTAG benchmark (`74db0e6`). Tools
  and experiments.
- Fault injection and the iso IN skeleton (cynthion). Incomplete by declaration.

**Cannot be submitted in current state — not a licensing or ownership problem,
a readiness one**
- The **`trigger_fpga_reconfiguration()` / INITN gap.** This is a genuine,
  well-diagnosed upstream bug: `boards/cynthion_d11/fpga.c:52-77` pulses
  PROGRAMN and calls `fpga_set_online(true)` but never releases INITN, which is
  open-drain and must be high for configuration to proceed; on r1.4 it has a
  pull-down and no pull-up, and the only caller of
  `permit_fpga_configuration(true)` is MCU startup. Evidence is strong (status
  register reads Fail with BSE error 0 — attempted and abandoned, not a rejected
  bitstream; same image loads fine over JTAG; every host trigger fails with
  `INITN=0`). **But the fix is unwritten and untested.** Under process rule 2 it
  cannot be submitted. It is the best *candidate* in the whole set and should be
  the next thing implemented — as a patch with a test that provokes the failure
  (trigger a reconfigure after `force-offline` and assert DONE) — not as a
  one-line drive-by.
- `local scripts/apollo/verify_vectors.py` is untracked in the apollo repo but is the
  guard for A1. Either it goes upstream with A1 or A1 ships without its safety
  net. Recommend the former.

**Needs reconciliation before submission**
- **Vendor request numbers.** We have locally allocated `0xed` (boot-to-DFU),
  `0xec` (emergency reset), `0xb9` (JTAG benchmark), and `0xFFFE` as an
  `FPGA_ADV_MODE` sub-command — and `74db0e6` exists partly to *move* a request
  that collided. Upstream owns this number space. Every vendor-request patch
  (A5, A6) must be offered with the number as upstream's choice, not ours.
- The **content scrub** required by the workspace rules for any public or
  upstream target: no `/mnt/...` or `/home/...` paths, no serial numbers, no
  credentials, and no reference to unrelated sensitive work. Commit messages in
  this set contain local absolute paths and `Co-Authored-By` trailers that need a
  decision.

### Recommended submission sequence

1. **Series C** (tests) — must exist first; validated against unmodified
   `upstream/main`.
2. **A1** (LTO) alone — the enabling patch, highest risk, wants isolation.
3. **A2, A3** (savings) — widen the margin.
4. **A4 → A5 → A6** (mode work) — strictly this order.
5. **A7** (races) — once it has a test that can actually fail.
6. **A8** (ROM cleanup) — last, it conflicts anywhere else.
7. **B1 + B2** together ([#75](https://github.com/awtoau/cynthion-workspace/issues/75), both halves), then **B3**, then **B4**.
8. **Cynthion**: `c0144f4`, `fc81a06`, `56a64c2`, `7378797`, `6fa3e0a`, plus
   `edf35c9` paired with apollo B1. After resolving `53d3ea4`.

Each of steps 2–8 is a separate upstream submission, and each one re-runs the
full sequence from patch 1 per the process rules.

---

## Upstream patch validation process

Source: `docs/upstream-patch-process.md`. Withdrawn with the plan above.

## Upstream patch validation process

The rules governing how a patchset destined for Great Scott Gadgets gets
validated before it is offered. Written to be followed by someone who did not
write the patches.

The patchset itself is in `upstream-patch-plan.md` (`upstream-patch-plan.md`).
This document is deliberately separate: the plan goes stale as commits land, the
procedure should not.

### Why this exists

This project has repeatedly produced claims of verification that did not hold —
patches described as "verified on hardware" with no test in the suite, ROM
figures quoted from a build nobody could reproduce, and a headroom premise that
was out by nine percentage points in the direction that breaks things. Upstream
has no way to audit any of that. The cost of a wrong claim is not a failed build;
it is a maintainer's trust, spent once.

So the burden of proof sits here, not with the reviewer.

### The five rules

1. **Every patch is accompanied by a test or tests.**
2. **Tests are run on hardware.** Not simulated, not reasoned about, not
   inferred from a successful build.
3. **The whole patchset tests cleanly twice in a row.**
4. **No fixing as you go.** If patch 3 fails, you do not patch over it and
   continue. The set is re-derived and the full sequence re-run from patch 1.
5. **End-to-end every time.** Apply 1, then 2, then 3, then 4. No shortcuts, no
   starting from the middle, no "this one obviously still applies".

Everything below is the consequence of taking those five seriously. The rules as
stated leave real questions open; leaving them open is how a process gets quietly
reinterpreted at 11pm by someone who wants to be finished.

---

### What "clean" means

A run is clean when **all** of the following hold. Anything else is a failure,
and a failure invokes rule 4.

- Every patch in the sequence applies with no conflict and no manual
  intervention. A conflict resolved by hand is a different patchset — re-derive
  and restart.
- Every position builds. Not just the final one: rule 5 means each intermediate
  tree is a tree upstream could bisect to, so each must compile.
- For the Apollo d11, every position fits the 14336 B application region, and
  the linked image passes the vector-table guard. **A build that links is not a
  build that works** — see the LTO failure mode below.
- Every test associated with every patch applied so far passes. Not just the
  newest patch's tests. A patch that breaks an earlier patch's test is a
  failure of the set, not of the test.
- Zero unexplained skips. A skip is acceptable only where it is *declared in
  advance* for that run with a stated reason (no target device attached, a
  destructive test not enabled). An unexpected skip is a failure, because a
  skipped test proves nothing and reads like a pass in a summary line.
- No new warnings from the firmware build or the linter. Upstream inherits
  warnings; we should not hand them any.

"Clean" specifically does **not** mean "no errors I judged important". The
person running the sequence does not get to triage.

### Flaky tests

**A flaky test is a failed test.** There is no third category.

If a test passes and then fails on the same tree, the run is failed under rule 3
and the *test* is now the defect. Fix the test — or the intermittent bug it just
found, which is the more likely explanation — and start again from patch 1. Do
not re-run the individual test hoping for green; do not mark it as expected-flaky;
do not raise the tolerance until it passes.

This matters most for exactly the patch that most needs it. `da564f8` fixes two
ISR/main-loop races. A race is *by definition* intermittent, so an
intermittently-failing test around it is evidence, not noise. The temptation to
retry-until-green is strongest precisely where retrying destroys the information.

Corollary: a test whose result depends on timing must state its tolerance and
the reason for it, and must fail on the wrong side of that tolerance rather than
warning.

### A test that cannot fail is not a test

Before a patch's test counts as satisfying rule 1, it must be shown to **fail
when the patch is reverted**. Assert-nothing tests are the main way rule 1 gets
satisfied on paper and not in substance — `apollo info` returning without an
exception demonstrates that the device is on the bus, and nothing else.

Record the failing-without-the-patch result alongside the passing-with-it
result. Two data points, or the test is unproven.

### Can a patch that cannot be hardware-tested ever be submitted?

**Default: no.** Rule 2 is the rule this project most needs.

There are three narrow exceptions, and each carries a heavier obligation than a
hardware test would have:

1. **Host-only changes with no device interaction.** `apollo install-udev`
   `--print-only` writes no file and touches no board. Test it as a unit test,
   and state in the submission that it is host-only and why.
2. **Changes whose effect is a property of the built artifact, not its
   behaviour.** `01ae228`'s internal-linkage pass is verified by inspecting the
   linked image. But note the trap: `--gc-sections` discards uncalled functions,
   so a symbol's *absence* can mean "never called" rather than "inlined", and an
   unfinished feature can look like a saving while being missing from the image.
   Under LTO a missing symbol may legitimately mean inlined — so confirm against
   the disassembly, not the symbol table alone.
3. **Changes to hardware we do not have.** `973fa78` is scoped to `cynthion_d11`
   precisely because d21, qtpy, daisho, xplained and pico carry the same unused
   driver but were not tested. That scoping is correct and should be preserved:
   **do not widen a patch to boards you cannot test.** If upstream wants it
   everywhere, that is upstream's call to make with their bench.

In all three cases the submission must say plainly what was and was not
exercised. "Not tested on hardware, here is why, here is what was checked
instead" is a legitimate thing to tell a maintainer. "Verified" when it was not
is not.

What is **never** acceptable: reasoning that a change is too small to break
anything. `da564f8` is 28 bytes and fixes two real bugs; the earlier version of
`e034daa`'s SPI pipelining was a few lines, silently corrupted TDO, and produced
an all-zero status register that a naive test would have read straight past.

### Recording a run so a second party can confirm it happened

A run that is not recorded did not happen. Each run produces a log at
`tmp/logs/upstream-run-<YYYYMMDD-HHMMSS>.log` containing, in order:

- **Provenance.** Upstream base commit SHA, and the SHA of each patch in
  sequence. Not branch names — branches move.
- **Environment.** Compiler version (`arm-none-eabi-gcc --version`), host Python
  version, `apollo` version string, and the OSS CAD Suite version if gateware is
  built. A ROM figure is meaningless without the compiler that produced it.
- **Device identity.** Board revision and the device serial number **for the log
  only** — serials are scrubbed before anything is published, per the workspace
  content rules.
- **Per position:** the patch applied, whether it applied cleanly, the build
  result, ROM/RAM figures against the region, and the full test output including
  skip reasons.
- **A verdict line** naming the run: clean or failed, and if failed, at which
  patch and why.

The bar is that a second party can take the log, re-derive the same sequence
from the recorded SHAs, and get the same numbers. If the log does not contain
enough to do that, it is not a record.

Test output goes in raw. Summarised test output ("all tests passed") is the
format in which unverified claims travel.

### Proving the two clean runs are distinct

Rule 3 says twice in a row. The obvious failure is one run reported twice, which
is also the easiest thing to do by accident when copying a summary between
documents.

Requirements for the second run to count:

- **Separate log file, separate timestamp** in the filename. The timestamps must
  differ by at least the real duration of a run — two logs a few seconds apart
  are one run.
- **The tree is rebuilt from scratch.** `git clean -fdx` on the firmware tree
  and a full rebuild, not an incremental one. An incremental second run mostly
  re-tests the build cache.
- **The sequence is re-applied from the base commit.** Not "the tree is already
  at patch 8, run the tests again" — that tests rule 3 without testing rule 5.
- **The device is power-cycled between runs**, so run 2 starts from a boot state
  rather than inheriting whatever run 1 left behind. This is the only way to
  catch a patch that works only on a warm device.
- **The runs are not interleaved.** Run 1 completes entirely, including its
  verdict line, before run 2 begins.
- Both logs are committed. Two logs in the repo, both referenced by SHA in the
  submission notes.

If the two runs disagree, the set has failed rule 3 and rule 4 applies. Two runs
that disagree also mean something is nondeterministic, which is a finding worth
chasing before submitting anything.

### A patch that passes in only one ordering

Record the constraint, then treat the ordering as part of the patchset.

The set already has one: **`01ae228` cannot be applied before `39a2213`,
`df4a93b` and `6cc219e`** — it conflicts textually, because it makes
`jtag_deinit()`'s pin table `static const` and gives `vendor.c`'s handlers
internal linkage, and those three restructure the same regions.

Rules for such patches:

- The constraint is recorded in the plan with its **cause**, not just its
  existence. "Must be last" is a fact someone will eventually re-litigate;
  "must be last because it cleans up regions that A4–A6 restructure" is an
  argument that survives.
- It is encoded as an executable check, not a note. `scripts/apollo_rom_sizing.py`
  keeps both the failing ordering (`lto-first`) and the passing one
  (`lto-first-fixed`) so the constraint is re-provable on demand rather than
  remembered.
- **A patch that only applies in one position is offered to upstream in that
  position, and the dependency is stated in the submission.** Upstream may
  reorder; they should be told what breaks if they do.
- If a patch's only valid position is one upstream will not accept, the patch
  needs rebasing into a form that stands alone. That is new work, and it
  re-enters the process at patch 1.

Distinguish the two kinds, because they fail differently:

- **Textual conflict** — announces itself at apply time. Cheap to find.
- **Semantic conflict** — every patch applies, every patch builds alone, and
  together they exceed the flash region. This one is silent until the last
  patch, and it is why rule 5's end-to-end requirement is not bureaucratic. The
  Apollo set has exactly this shape: A4+A5+A6 total +336 B against upstream's 568
  B of headroom.

### The size budget is part of "clean"

For any patch touching `cynthion_d11` firmware, the ROM figure at every position
is a test result and goes in the log.

The application region is 14336 B — 16 KB of flash less `BOOTLOADER_SIZE = 0x800`
for Saturn-V. That split is a linker `--defsym` from `board.mk`, not a hardware
boundary, but it is not ours to move: Saturn-V uses 2016 of its 2048 bytes.

Two standing traps:

- **Do not trust a headline ROM figure without checking the symbols are
  present.** `--gc-sections` discards uncalled functions, so an unfinished
  feature can appear to fit while being absent from the image.
- **LTO on this target has a silent failure mode.** A naive enable links cleanly
  and produces a **four byte** binary: the SAMD11 linker script has no `ENTRY()`
  directive and builds its vector table from weak aliases, so the plugin prunes
  the entire program as unreferenced. The roots must be named explicitly with
  `-Wl,--entry=Reset_Handler -Wl,-u,exception_table`. Remove either and the dead
  binary returns **with no diagnostic**.

  Therefore: any run that touches build flags must decode the linked vector
  table and assert Reset, SysTick, USB, EIC, SERCOM1 and TC1 resolve to real code
  rather than `Dummy_Handler`. `scripts/apollo/verify_vectors.py` does this. A size
  figure alone would score a pruned image as an excellent result — which is the
  precise reason this check is mandatory rather than advisory.

### Before anything is sent

Independent of validation, the workspace rules on publishing apply. Upstream is
a repository we do not own, which is the **three-check tier**:

1. Confirm the exact action and the target repository.
2. Show the final text and get a second explicit approval.
3. Explicit confirmation that this should go to someone else's repository rather
   than staying on our fork.

Plus a mandatory content scrub of every patch and every commit message:

- No local filesystem paths (`/mnt/...`, `/home/...`).
- No serial numbers, credentials, or device identifiers.
- No `Refs: awtoau/cynthion-workspace#NN` lines. Several commits in this set
  carry them; an upstream reader cannot resolve them and they leak our tracker.
- No reference to unrelated sensitive work.
- A decision on `Co-Authored-By` trailers.

Vendor request numbers (`0xed`, `0xec`, `0xb9`, `0xFFFE`) are offered as
proposals, never as facts. Upstream owns that number space, and our local
allocations have already collided once.

### Worked example: the luna-soc submission

The awto-luna-soc work is the shape to copy. It took upstream 0.3.2, backported
amaranth-soc `d8b5892` (Python 3.14 CSR annotations), and then reverted five
`__init__` workarounds the backport made redundant — 94 lines across 8 files. Two
stacked PRs, both merged.

The transferable lesson is about what verification means. Every affected class
**imported cleanly whether or not the bug was present**, because the bug
manifested at *construction*. Verification therefore required **constructing 72
CSR Registers**, not importing them.

An import test would have passed on the broken code and reported success. That is
the same failure mode as a firmware image that links cleanly and is four bytes
long, and the same as `apollo info` returning without an exception.

So: **for each patch, ask what the bug's symptom actually is, and make the test
provoke that.** Not the nearest convenient thing that runs without error. The
gap between "it loaded" and "it works" is where every unverified claim in this
project has lived.

---

## Patches worth sending upstream (first draft)

Source: `docs/upstreamable-patches.md`. Superseded by the plan above; its errors are listed in the plan ([#193](https://github.com/awtoau/cynthion-workspace/issues/193)).

## Patches worth sending upstream

> **Superseded for planning purposes.** The full inventory, ordering and
> validation procedure now live in
> `upstream-patch-plan.md` (`upstream-patch-plan.md`) and
> `upstream-patch-process.md` (`upstream-patch-process.md`). This file is kept for
> its per-patch diagnostic write-ups, which are still the best explanation of
> *why* each bug is a bug — the exit-DFU reasoning and the INITN analysis in
> particular.
>
> Known errors in this file, corrected in the plan: apollo is **34** commits
> ahead, not 20; `repos/cynthion` has 25 local commits and is not covered here at
> all; the ROM-savings commits are described as possibly "irrelevant" to upstream
> when upstream in fact builds at **96.04%, 568 bytes free**, which makes them
> load-bearing; and the INITN fix is listed under "Ready — a clear bug with a
> verified fix" while its own text says it is not yet attempted.

`repos/apollo` is 20 commits ahead of upstream. Some of that is local
scaffolding, but several are fixes to real upstream bugs that other Cynthion
users will hit. Tracked here so they are not lost in a submodule bump.

Nothing here has been submitted. Sending anything to a repository we do not own
needs explicit approval first.

### Ready — a clear bug with a verified fix

#### `exit-dfu`: recover from the bootloader without a power cycle

`apollo boot-to-dfu` puts the microcontroller into the Saturn-V bootloader, and
until now nothing brought it back. The board could only be recovered by
physically unplugging it — awkward for a headless or remote setup, and
surprising given the entry command exists.

The bootloader **cannot reset itself out**. It stays put whenever the reset
cause is the watchdog (`saturn-v/main.c:157`), and arming the watchdog is the
only reset Apollo can perform, so every self-reset lands straight back in the
bootloader. `PM_RCAUSE_POR` requires power actually removed.

The way out already exists in the DFU specification. A **zero-length
`DFU_DNLOAD`** is "download complete", and `dfu.c:12` turns it into
`dfuMANIFEST_SYNC` without writing any firmware. That reaches
`dfu_cb_manifest()`, which sets `exit_and_jump`, and the main loop calls
`NVIC_SystemReset()`. That reset sets `PM_RCAUSE_SYST`, which matches none of
the stay-in-bootloader conditions, so the next boot runs the application.

Two details that cost time to find, worth keeping in the patch:

- It must be `DFU_DNLOAD`, not `DFU_DETACH`. The bootloader's DFU
  implementation has no `DFU_DETACH` case at all, so a detach request is
  refused and the device looks unrecoverable.
- The interface **must** be claimed. A DFU control transfer is addressed to an
  interface, and an unclaimed one stalls with `LIBUSB_ERROR_PIPE` — which reads
  as a refused request rather than a missing claim.

Verified on hardware: `boot-to-dfu` then `exit-dfu` returns a working debugger
with no replug. Python only; no firmware change.

This also matters for unattended work. Without it, an agent or a CI job that
lands in the bootloader has no way out and must stop and ask for a human to
replug the board. With `exit-dfu` it recovers itself, so the DFU state is not a
dead end.

#### `trigger_fpga_reconfiguration()` never releases INITN

`boards/cynthion_d11/fpga.c:52-77` resets the JTAG TAP, pulses `PROGRAMN` and
calls `fpga_set_online(true)`, but never touches INITN. INITN is open drain and
must be released high for configuration to proceed; on r1.4 it has a pull-down
and no pull-up, and the only caller of `permit_fpga_configuration(true)` is MCU
startup (`main.c:74` and `:81`).

So the software flag says online while the hardware stays held, and the
documented reconfigure-from-flash command cannot complete after a
`force-offline`.

Diagnosed rather than inferred: the ECP5 status register reads Fail with BSE
error 0 — configuration attempted and abandoned, not a rejected bitstream. The
same image reads back byte-exact from flash and loads fine over JTAG, and every
host trigger fails identically with `INITN=0`.

The fix looks like one line — call `permit_fpga_configuration(true)` from
`trigger_fpga_reconfiguration()` before pulsing PROGRAMN — but it is firmware
and wants its own testing. **Not yet attempted.** See
`docs/chips/ecp5/reconfigure-initn-gap.md`.

#### `flash-fast` port handback ([#75](https://github.com/awtoau/cynthion-workspace/issues/75))

`8054f62`. `FlashBridgeConnection` relied on `__del__` to hand the shared USB
port back to Apollo. Handing the port back is a USB transfer, so doing it from
a destructor means I/O at garbage-collection or interpreter-shutdown time, when
the libusb context may already be gone — that crashed the interpreter rather
than reporting an error, and left the port with the FPGA so Apollo appeared to
have vanished.

Now a context manager, so the port goes back even when the body raises.

#### Two ISR/main-loop races

`da564f8`. `fpga_online` and `edge_counter` are written from an ISR and read
from the main loop without protection.

#### Sticky JTAG/UART pin mutual exclusion

`39a2213`. On r1.4 the UART pins R14/T14 are shared with JTAG TDI/TMS, so the
microcontroller can use either function but not both. Enforced with a lock
rather than left to convention.

### Probably wanted, but shaped for us

- `df4a93b` — the `boot-to-DFU` vendor command itself (0xed). Upstream may
  prefer a different request number.
- `6f16848` — `install-udev`, which fixes a `flash-fast` permission failure.
- `973fa78`, `651b027`, `01ae228` — d11 ROM savings: disabling the unused
  TinyUSB vendor class driver, moving WCID descriptors to flash. Useful if
  upstream is also tight on that part, irrelevant otherwise.
- `24b3b7a`, `daecad7`, `9c72930` — test coverage. Upstream has little, so
  these may need reshaping to fit whatever they would rather have.

### Ours, not upstream's

The quad-SPI flash work (`42bffe6`, `4ead7df`, `c3c1911`), the PLL changes
(`c3c5fbe`, `0ab00de`) and the FPGA_ADV sideband link (`6cdc7c3`, `9ec249a`)
are all for this project's own investigation. They may be interesting upstream
eventually but are not bug fixes and are not proposed here.

### Before submitting anything

Public repositories we do not own need a content scrub — no local filesystem
paths, no serial numbers, no credentials — and explicit confirmation that the
change should go to someone else's repository rather than staying on a fork.

---

## Upstream CI: signal quality and local baseline 2026-08-03

Source: `docs/upstream-ci-workflow.md`. Measurements dated 2026-08-03; superseded by re-running `scripts/tools/upstream_ci.py`.

### What upstream CI actually is

| repo | automated checks on a PR |
|---|---|
| `luna` | GH Actions `simulations`: `unittest discover -t . -s tests` on Python 3.9–3.13. Plus a Jenkins HIL job. |
| `apollo` | GH Actions `python` (`unittest discover -p jtag_svf.py`) and `Firmware` (6-board build matrix). |
| `cynthion` | GH Actions `Python`: unit tests + analyzer elaboration, on 3 OSes × 5 Pythons. |
| `facedancer` | **none** — no `.github/workflows` at all. |
| `luna-soc` | **none** — no `.github/workflows` at all. |

Two things follow, and both matter more than the table:

**The GitHub-side signal is weak.** LUNA's entire Actions gate is 93 unit tests
that run in 2.2 seconds. Anything a patch breaks that is not covered by those 93
tests will go green. Measured, not assumed: PR [#301](https://github.com/awtoau/cynthion-workspace/issues/301) fixes an inverted fanout
direction in `HandshakeExchangeInterface`, and the suite passes identically with
and without the fix (93 tests, OK, both). Upstream CI would not have caught the
bug it fixes.

**The signal that would matter is unavailable.** LUNA's Jenkins
hardware-in-the-loop job is the only check that exercises real hardware, and it
is `ERROR` on every open PR ([#301](https://github.com/awtoau/cynthion-workspace/issues/301), [#303](https://github.com/awtoau/cynthion-workspace/issues/303), [#304](https://github.com/awtoau/cynthion-workspace/issues/304)) with logs behind
`jenkins.greatscottgadgets.com`. We cannot run it and cannot read it.

So upstream CI is worth passing, but it is not worth *submitting for*. Our own
hardware tests remain the real gate — see `docs/upstream-patch-process.md`.

**Fork PRs from first-time contributors do not run Actions until a maintainer
approves them.** PR [#301](https://github.com/awtoau/cynthion-workspace/issues/301) has been open since 2026-03-13 with zero comments, zero
reviews, and no Actions run at all. PRs from known collaborators (miek,
martinling, mndza) run normally. A first submission sits in that queue.

### Local baseline, 2026-08-03

Clean upstream `main`, no patches, Fedora `arm-none-eabi-gcc` 15.2.0, free-threaded
CPython 3.15.0b3, amaranth 0.5.9.

| job | result |
|---|---|
| `luna` sim-tests | PASS — 93 tests, 2.2 s |
| `apollo` host-tests | PASS — 17 tests |
| `apollo` firmware-cynthion | PASS — text 13500, data 260, bss 3268 |
| `apollo` firmware-samd11_xplained | PASS — text 11984 |
| `apollo` firmware-qtpy | PASS — text 12408 |
| `apollo` firmware-cynthion-r0.2 | PASS — text 10056 |
| `apollo` firmware-cynthion-r0.4 | PASS — text 13500 |
| `apollo` firmware-raspberry_pi_pico | **FAIL — pre-existing, ours** |

The pico failure is a local toolchain mismatch, not an upstream defect and not
caused by any patch: Fedora's GCC 15.2.0 against the vendored pico-sdk gives
`declaration for parameter '__uint8_t' but no such parameter` and
`button.c:56: error: expected '{' at end of input`. Upstream's CI pins an older
compiler via `carlosperate/arm-none-eabi-gcc-action`. **Always run the control**
(same job, no patch) before attributing a failure to a patch — that is how this
one was identified.

Note `apollo` upstream main builds cynthion at 13760 B of 14336 B (96.0%),
consistent with the 96.04% recorded in issue [#102](https://github.com/awtoau/cynthion-workspace/issues/102). The flash budget is the
binding constraint on any firmware patch series.

---

## Reporting upstream and luna-soc fork question

Source: `docs/upstream-boundary.md`. Withdrawn: nothing is upstreamed from this workflow.

### Not yet decided

**`luna-soc` fork.** `cynthion` pins `luna-soc` to `awtoau/awto-luna-soc`. The fork existed
for an Amaranth API change and 40+ patched CSR classes; upstream is now **ahead**, so the
amaranth-soc reason is gone. Whether anything else still needs it has not been checked.

### Reporting upstream

**Three clean defects with standalone reproducers**, all in
`greatscottgadgets/luna-soc` — not in `luna`, whose USB gateware has been solid
throughout.

| defect | reproducer | why nobody upstream hit it |
|---|---|---|
| SPI crossbar starvation | `scripts/riscv_flash_crossbar_sim.py` | needs flash memory-mapped **and** arbitrary commands; upstream grants at cycle 0 when the map is idle and never in 600 cycles when it holds `cs` |
| CS hold as a one-cycle `csr.action.W` | ILA capture; CS fragmented into four 8-bit windows during a JEDEC read | moondancer's `read_flash_uuid` writes all 13 command bytes into the 16-deep FIFO before reading, so `r_rdy` never drops |
| `oe = ~tx.rdy` releasing the stop bit | `scripts/soc/soc_serial_sim.py` | the released line RC-charges through a pull-up and usually arrives in time; it presents as intermittent corruption, not a dead link |

The first two are reachable only by memory-mapping flash *and* using arbitrary commands.
Cynthion's own SoC instantiates exactly that combination
(`with_controller=True, with_mmap=True`), so this is their code on their hardware.

The first two are worth reporting as one issue rather than two: the CS bug is the
interesting half, and the explanation of why their firmware survives it is what makes the
report actionable. The UART one stands alone.

Per the workspace rules, upstream repos need three checks before anything is filed. Draft
locally first.

---

## Local CI with act

Source: `docs/ci_cd_workflows.md`. Retired: nothing runs on GitHub Actions; local gate is `scripts/check.py`.

### CI/CD Workflows

#### Local Testing with act

**Tool:** act (GitHub Actions runner for Docker)  
**Repository:** https://github.com/nektos/act  
**Cost:** Free (local execution)

##### Install act
```bash
./scripts/install.py ci-install
## or manually:
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | bash
```

##### List Workflows
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo"
./scripts/install.py ci-list   # Via install.py
## or
act -l                         # Via act directly
```

##### Run Jobs
```bash
## List all jobs
act -l

## Run specific job
act -j firmware-build

## Run with custom Docker image
act -j firmware-build -P ubuntu-latest=ubuntu:22.04

## Simulate run (dry-run)
act --dry-run
```

##### Matrix Builds
```bash
## Run all matrix combinations
act -j build-and-test

## For Cynthion (3 OS × 5 Python = 15 jobs locally)
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
act  # Runs all (some may fail if OS unsupported locally)
```

#### Vendor Workflows (Existing CI)

**Apollo:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo"
act -l                    # Lists: firmware-build, host
act -j firmware-build     # Runs: make get-deps all
```

**Cynthion:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
act -l                    # Lists: build-and-test (15 jobs)
act -j build-and-test     # Runs: Python tests (all versions)
```

**Saturn-V:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-saturn-v"
act -j firmware
```

**Luna:**
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-luna"
act -j build              # Simulations
```

---

---

## GitHub Actions

Source: `docs/github_actions.md`. Retired as a separate page; the rule lives in the root README and the upstream-workflow table in `upstream-boundary.md`.

### GitHub Actions

#### This workspace runs nothing on GitHub

There is no `.github/workflows/` in this repo, deliberately. The `fast` and
`full` workflows were deleted on 2026-07-28 and must not be reinstated.

Checks run locally instead — see `scripts/check.py` (`../scripts/check.py`) and the
README (`../README.md#checks-run-locally-not-on-github`):

```bash
./scripts/check.py --fast
```

Reasons this is local rather than hosted:

- The checks that matter need the real toolchain, the real free-threaded 3.15t
  interpreter, and (for anything beyond compilation) real hardware. A cloud
  runner can only approximate the first two and never the third.
- Hosted runs were actively misleading. Both flutter jobs carried
  `continue-on-error: true`, so they reported green for months while failing —
  6 analyzer warnings and a failing smoke test went unnoticed the whole time.
- Running locally is faster than queueing: the full fast set is ~3 s.

If something needs automating, extend `scripts/check.py`. Do not add a workflow.

#### Upstream workflows in the submodules

The submodule repos are separate repositories — mostly forks tracking upstream —
and keep their own CI. Those are **not** covered by the rule above; deleting them
would diverge the forks from upstream and cause merge conflicts on the next sync.

| Repo | Workflow | What it does |
|------|----------|--------------|
| `awto-apollo` | `firmware.yml` | Apollo firmware for 6 board variants; push, PR, merge_group |
| `awto-cynthion` | `python.yml` | Python package, 3 OS × 5 Python versions (15 jobs); push, PR, weekly |
| `awto-saturn-v` | `build.yml` | Bootloader on 2 platforms; push, PR, weekly |
| `awto-luna` | `simulate.yml` | Gateware simulation |
| `awto-packetry` | 2 workflows | Build + AppImage packaging |

None of them build FPGA bitstreams, moondancer firmware, or the
analyzer/facedancer gateware — that work only ever happens locally, which is
another reason the local runner is the real check.

---

## Installation and build guide (old)

Source: `docs/install.md`. Retired: described the `cyn` CLI, `awto-apollo` paths, `apollod`, `act` and `scripts/install.py setup` ([#157](https://github.com/awtoau/cynthion-workspace/issues/157)).

## Cynthion Workspace Installation & Build Guide

### Overview

This workspace uses forked repositories under `${REPOS_ROOT:-$HOME/git/awtoau}`. Use workspace-relative or `$HOME`-based paths rather than machine-specific absolute paths naming a particular user or mount point; `scripts/check.py paths` enforces it.

### Which installer, and why there are two

**Setting up a machine is `./dev.py setup`**, which runs `scripts/machine_setup.py`:
distro packages, submodules, the Rust target, the Python packages, udev.

`scripts/install.py` is the older monolithic installer and its `setup` half is
superseded by the above. It is kept for the subcommands that have no replacement
— `versions`, `versions-check`, `toolchain-install`, `toolchain-status` — because
`machine_setup.py` deliberately does not install the FPGA toolchain. The
`setup`/`rebuild`/`clone-repos` sections below are historical: nothing in the
repo invokes them, and `./dev.py audit` reports the file as `documented`,
meaning this page is the only thing keeping it reachable. See [#157](https://github.com/awtoau/cynthion-workspace/issues/157).

### Documentation Ownership

- This document is the canonical install, toolchain, and build-system reference.
- Phase tracking and success criteria live in apollo_serial_architecture_redesign_plan.md (`apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md`).
- Patch and change history lives in patchset_overview.md (`patchset/patchset_overview.md`).

### Version and Update Checks

Run these early during setup and whenever the environment drifts:

```bash
./scripts/install.py versions
./scripts/install.py versions-check
```

Use this to:
- Capture local versions for system tools, FPGA toolchain, Python packages, and repo commits
- Compare local versions with upstream releases
- Keep a baseline for later diff checks

Suggested workflow:

```bash
## Save baseline after a known-good setup
./scripts/install.py versions > tmp/versions-baseline.txt

## Compare later state
./scripts/install.py versions > tmp/versions-current.txt
diff tmp/versions-baseline.txt tmp/versions-current.txt

## Check upstream updates
./scripts/install.py versions-check
```

---

### Prerequisites

#### System Requirements
- **OS**: Linux x86_64 (tested on Fedora 44)
- **Disk Space**: 5+ GB free (repos + builds)
- **RAM**: 8+ GB (16 GB recommended for synthesis)

#### Required System Packages

**Install all at once (Fedora/RHEL):**
```bash
sudo dnf install -y \
  arm-none-eabi-gcc-cs \
  binutils \
  bison \
  boost-devel \
  clang \
  cmake \
  curl \
  dfu-util \
  eigen-devel \
  flex \
  gawk \
  gcc gcc-c++ \
  git \
  jq \
  libreadline-devel \
  make \
  openocd \
  pkg-config \
  readline-devel \
  tcl tcl-devel \
  zlib-devel
```

**Or on Debian/Ubuntu:**
```bash
sudo apt-get update && sudo apt-get install -y \
  arm-none-eabi-gcc \
  binutils \
  bison \
  build-essential \
  clang \
  cmake \
  curl \
  dfu-util \
  flex \
  gawk \
  git \
  jq \
  libeigen3-dev \
  libboost-all-dev \
  libreadline-dev \
  openocd \
  pkg-config \
  tcl tcl-dev \
  zlib1g-dev
```

#### Core Prerequisites (Detailed)

| Tool | Version | Purpose | Check | Note |
|------|---------|---------|-------|------|
| **Build Tools** | | | | |
| GCC/G++ | 15+ | C/C++ compilation | `gcc --version` | Comes with build-essential |
| Make | 4.0+ | Build system | `make --version` | Standard |
| CMake | 3.10+ | Cross-platform builds | `cmake --version` | For some components |
| Clang | 18+ | LLVM compiler | `clang --version` | Optional, used by Yosys |
| Binutils | latest | Binary utilities | `ld --version` | Part of build toolchain |
| **Hardware Dev** | | | | |
| Git | 2.30+ | Version control | `git --version` | Required for repos |
| ARM GCC | 15.x | ARM Cortex-M | `arm-none-eabi-gcc --version` | Apollo firmware |
| **HDL/FPGA Tools** | | | | |
| Flex | 2.6+ | Lexer generator | `flex --version` | For Yosys/nextpnr |
| Bison | 3.0+ | Parser generator | `bison --version` | For Yosys/nextpnr |
| pkg-config | 0.29+ | Library finder | `pkg-config --version` | Build configuration |
| **Libraries** | | | | |
| boost-devel | 1.70+ | C++ libraries | `dpkg -l \| grep boost` | Needed for some tools |
| libreadline | 7.0+ | Interactive input | Check: `/usr/include/readline` | For CLI tools |
| zlib | latest | Compression | Check: `/usr/include/zlib.h` | Bitstream compression |
| Eigen3 | 3.3+ | Linear algebra | Check: `/usr/include/eigen3` | Optional, for analysis |
| **Programming** | | | | |
| Python | 3.15t | Gateware framework | `python3 --version` | Free-threaded; Amaranth, cynthion |
| Rustup | latest | RISC-V firmware | `rustc --version` | moondancer |
| **Utilities** | | | | |
| curl | 7.50+ | HTTP download | `curl --version` | Fetch releases |
| jq | 1.5+ | JSON parser | `jq --version` | Parse GitHub API |
| dfu-util | latest | USB DFU flashing | `dfu-util --version` | Optional: for flashing |
| OpenOCD | 0.10+ | JTAG debugging | `openocd --version` | Optional: for debugging |
| TCL | 8.5+ | Scripting | `tclsh --version` | For some EDA tools |

#### Rust RISC-V Target

```bash
## Required for moondancer firmware build
rustup target add riscv32imac-unknown-none-elf
rustup target list --installed  # Verify
```

#### OSS CAD Suite (FPGA Toolchain)

**Option 1: Automatic Installation (Recommended)**
```bash
./scripts/install.py toolchain-install
```

**Option 2: Manual Installation**
```bash
## Download latest release
wget https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz

## Extract
tar xzf oss-cad-suite-linux-x64-20260522.tgz -C ~/opt/

## Verify
source ~/opt/oss-cad-suite/environment
yosys --version         # → 0.65+57
nextpnr-ecp5 --version  # → 0.10-74-gee605e2b
```

#### Pre-Flight Checklist

```bash
## Check Python (must be free-threaded 3.15t)
python3 --version   # → Python 3.15.0b3
python3 -c "import sys; print('free-threaded' if not sys._is_gil_enabled() else 'GIL ENABLED - wrong build')"

## Check Rust
rustc --version && rustup target list --installed | grep riscv

## Check ARM toolchain
arm-none-eabi-gcc --version  # → GCC 15.x

## Check build tools
gcc --version && make --version && cmake --version

## Check toolchain
source ~/opt/oss-cad-suite/environment && yosys --version
```

#### Early Failure Recovery (Run Before `setup`)

If pre-flight checks fail, fix these first instead of continuing:

##### Python 3.15t Not Found

No distro packages free-threaded 3.15 yet — it is a beta. Install a managed
build:

```bash
uv python install 3.15t
```

Or build from source with `--disable-gil`. Either way the interpreter must
report a free-threading build:

```bash
python3 -c "import sys; print(sys._is_gil_enabled())"   # → False
```

`scripts/install.py` picks the interpreter itself, preferring `python3.15t`
and falling back to `python3.14t` / `python3.15` / `python3.14` if a machine is
mid-upgrade. Override with `CYN_PYTHON=/path/to/python`.

##### Rust RISC-V Target Missing
```bash
rustup target add riscv32imac-unknown-none-elf
rustup target list --installed
```

##### OSS CAD Suite Download/Install Failure
```bash
## Check connectivity to release endpoint
curl -I https://github.com/YosysHQ/oss-cad-suite-build/releases/latest

## Manual fallback install
wget https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-05-22/oss-cad-suite-linux-x64-20260522.tgz
tar xzf oss-cad-suite-linux-x64-20260522.tgz -C ~/opt/

## Verify tools
source ~/opt/oss-cad-suite/environment
yosys --version
nextpnr-ecp5 --version
```

##### Build Artifacts Not Found
```bash
./scripts/install.py status
ls -la tmp/*.log
find "${REPOS_ROOT:-$HOME/git/awtoau}" -name "*.elf" -o -name "*.bin"
```

##### Optional CI Tooling (`act`) Fails

Only needed if you run local GitHub Actions workflows.

```bash
mkdir -p ~/.local/bin
curl -o ~/.local/bin/act \
  https://github.com/nektos/act/releases/download/v0.2.60/act_Linux_x86_64
chmod +x ~/.local/bin/act
export PATH="$HOME/.local/bin:$PATH"
act --version
```

If `act` fails due to Docker:

```bash
sudo systemctl start docker
sudo usermod -aG docker $USER
docker ps
```

If you still need a deeper diagnosis after these first-pass fixes, see the
[Known Issues & Workarounds](#known-issues--workarounds) section below.

#### Reference: CI/Docker Configuration

The following are sourced from official CI workflows and Docker configurations:

**GitHub Actions Workflows:**
- `awto-apollo/.github/workflows/firmware.yml` — Apollo firmware CI
- `awto-cynthion/.github/workflows/python.yml` — Gateware CI (Python 3.9-3.13)
- `awto-saturn-v/.github/workflows/build.yml` — Saturn-V bootloader CI

**Docker Image (Ubuntu 22.04):**
- Source: `awto-luna/Dockerfile`
- Includes: Full Ubuntu build-essential, boost, Eigen3, OpenOCD, DFU-util
- Installs OSS CAD Suite automatically from latest GitHub release

See respective `.github/workflows/*.yml` files for exact CI setup details.

---

### Repository Structure

```
${REPOS_ROOT:-$HOME/git/awtoau}/
├── awto-apollo/              # Debug controller firmware (ARM)
│   ├── firmware/
│   │   ├── Makefile          # Build via: make APOLLO_BOARD=cynthion [target]
│   │   ├── src/
│   │   │   ├── boards/       # Board-specific code
│   │   │   │   ├── cynthion_d11/
│   │   │   │   ├── cynthion_d21/
│   │   │   │   └── ...
│   │   │   └── ...
│   │   └── lib/
│   │       └── tinyusb/      # Vendored (submodule, requires init)
│   └── apollo_fpga/          # Host-side Python module
│
├── awto-cynthion/            # Gateware (Python/Amaranth) + moondancer firmware (Rust)
│   ├── cynthion/python/      # Gateware project
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   ├── cynthion/     # Python package root
│   │   │   │   ├── gateware/
│   │   │   │   │   ├── analyzer/
│   │   │   │   │   ├── facedancer/
│   │   │   │   │   ├── platform/
│   │   │   │   │   │   ├── cynthion_r0_2.py
│   │   │   │   │   │   └── ... (other revisions)
│   │   │   │   │   └── ...
│   │   │   │   └── ...
│   │   │   └── ...
│   │   └── ...
│   ├── firmware/
│   │   └── moondancer/       # RISC-V firmware (Rust/Cargo)
│   │       └── Cargo.toml
│   └── ...
│
├── awto-luna/                # Luna USB framework (dependency)
├── awto-facedancer/          # Facedancer USB device framework (dependency)
└── ...
```

### Canonical Toolchain

#### Environment Setup

Before any build, source the OSS CAD Suite environment:

```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
```

**Location**: `${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}`  
**Version**: 2026-05-22  

| Tool | Version | Commit |
|------|---------|--------|
| Yosys | 0.65+57 | 9d0cdb855 |
| nextpnr-ecp5 | 0.10-74-gee605e2b | ee605e2b |
| Trellis | (bundled) | - |

Verify:
```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
yosys --version        # → 0.65+57
nextpnr-ecp5 --version # → 0.10-74-gee605e2b
```

#### Python & Dependencies

- **Python**: 3.15.0b3 (system, free-threaded / no-GIL)
- **Amaranth**: 0.5.x (gateware HDL framework)
  - ✅ Tested compatible with Python 3.15t
  - Elaboration of analyzer gateware succeeds
- **RISC-V Toolchain**: riscv32-unknown-elf (for moondancer firmware)
- **ARM Toolchain**: arm-none-eabi-gcc (GCC 15.2.0, for Apollo)

Verify Python and gateware:
```bash
python3 --version  # → Python 3.15.0b3
which riscv32-unknown-elf-gcc
which arm-none-eabi-gcc
```

### Quick Operations with install.py

Use `install.py` for common setup and recovery flows:

```bash
## Fail-fast prerequisite check (recommended first step)
./scripts/install.py prereqs

## Full setup: clone repos, init submodules, install deps, build all
./scripts/install.py setup

## Parallel setup (faster on multi-core hosts)
./scripts/install.py --parallel setup
./scripts/install.py --parallel --jobs 2 setup

## Just clone repos without building
./scripts/install.py --repo-only setup

## Rebuild everything (clean + setup)
./scripts/install.py rebuild

## Check workspace status
./scripts/install.py status

## Clone repos to custom location
./scripts/install.py --repos-path /path/to/repos clone-repos

## CI helpers
./scripts/install.py ci-install
./scripts/install.py ci-list

## Live troubleshooting
tail -f ./tmp/logs/install-*.log
grep ERROR ./tmp/logs/install-*.log
```

**Options:**
- `--dry-run` — Show what would happen without executing
- `--verbose` — Show full command output
- `--no-build` — Setup repos but skip builds
- `--no-submodules` — Skip git submodule initialization
- `--repos-path /path` — Use custom repos directory

#### Local CI Smoke Test (Apollo)

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo"
act -l
act -j firmware-build
```

---

### Manual Build Steps

If you prefer to build components individually:

#### Phase 1.1: Apollo Firmware

**Board**: Cynthion d11 (ATSAMD11)  
**Compiler**: arm-none-eabi-gcc (GCC 15.2.0)  
**Dependencies**: TinyUSB (vendored in `lib/tinyusb/`)

##### Setup (one-time)

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo/firmware"
## Initialize submodules (TinyUSB)
make APOLLO_BOARD=cynthion get-deps
```

The `get-deps` target will:
- Initialize `lib/tinyusb/` submodule
- Download any external dependencies
- Prepare build artifacts directory

##### Build

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-apollo/firmware"

## Clean build
make APOLLO_BOARD=cynthion clean
make APOLLO_BOARD=cynthion

## Install to connected Cynthion (requires Saturn-V bootloader)
make APOLLO_BOARD=cynthion dfu
```

**Output**: `build/cynthion_d11/apollo_debug_soc.elf` and `.bin`

##### Build Configuration

- `APOLLO_BOARD=cynthion` → Maps to `cynthion_d11` internally
- Alternative: `APOLLO_BOARD=luna` (alias for cynthion)
- `BOARD_REVISION_MAJOR=X BOARD_REVISION_MINOR=Y` → Override auto-detection (needed for r0.5 and older)
- Apollo flashing uses the Saturn-V DFU bootloader on the connected Cynthion. The device must enumerate as `1d50:60e6` for `make APOLLO_BOARD=cynthion dfu` to work; `1d50:615b` is analyzer/facedancer mode and is not the Apollo flash target.

##### Canonical Flash Commands (Do Not Mix Paths)

Use these two commands as the supported paths:

```bash
## Flash Apollo SAMD11 firmware over USB DFU.
cyn apollo flash

## Flash ECP5 gateware through Apollo transport.
cyn fpga flash
```

Implementation details:
- `cyn apollo flash` runs `make APOLLO_BOARD=cynthion dfu` in `awto-apollo/firmware`.
- `cyn fpga flash` runs `apollo configure <.../top.bit>`.

#### Phase 1.2: moondancer Firmware (Rust/RISC-V)

**Target**: RISC-V RV32IM (VexRiscv on FPGA)  
**Compiler**: Rust + riscv32-unknown-elf-gcc  
**Build System**: Cargo

```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion/firmware/moondancer"

## Build release
cargo build --release

## Output: target/riscv32imac-unknown-none-elf/release/moondancer
```

**Note**: The target is defined in `.cargo/config.toml` — do NOT pass `--target` manually.

#### Phase 1.3: Gateware (Analyzer)

**Framework**: Amaranth (Python HDL)  
**Synthesis**: Yosys → nextpnr-ecp5 → trellis → bitstream  
**Platform**: Cynthion r0.2 (ECP5 LFE5U-12F)

##### Setup (one-time)

Packages install into the **default 3.15t environment** — there is no virtualenv
to create or activate. Run everything with plain `python3`.

```bash
cd /path/to/cynthion-workspace

## Editable installs into system 3.15t
python3 -m pip install -e repos/cynthion/cynthion/python -e repos/facedancer

## Gateware builds additionally need the FPGA toolchain on PATH
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
```

Console scripts (`cynthion`, `apollo`) land in the 3.15t `bin/` directory. That
directory must come **before** `~/.local/bin` on PATH, or a stale shim from an
older interpreter will shadow them and fail with `ModuleNotFoundError`:

```bash
export PATH="$HOME/opt/cpython-315t/bin:$PATH"
```

Verify the whole chain resolves to one interpreter:

```bash
python3 -c "import sys, cynthion, apollo_fpga, facedancer; \
  print(sys.version.split()[0], 'GIL' if sys._is_gil_enabled() else 'free-threaded')"
which cynthion apollo   # both under opt/cpython-315t/bin
```

##### Build

```bash
## Test elaboration (dry-run)
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 \
  python3 -m cynthion.gateware.analyzer.top --dry-run

## Full synthesis (produces .bit file)
LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 \
  python3 -m cynthion.gateware.analyzer.top
```

**Output**: Bitstream file (location depends on Amaranth build output)

#### Phase 1.4: Gateware (Facedancer)

Same as Analyzer, but with facedancer module:

```bash
source "${OSS_CAD_SUITE:-$HOME/opt/oss-cad-suite}/environment"
LUNA_PLATFORM=cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2 \
  python3 -m cynthion.gateware.facedancer.top --dry-run
```

### Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `LUNA_PLATFORM` | `cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2` | Gateware build target |
| `APOLLO_BOARD` | `cynthion` | Apollo firmware board selection |
| `BOARD_REVISION_MAJOR` | (auto-detect for r0.6+) | Hardware revision override |
| `BOARD_REVISION_MINOR` | (auto-detect for r0.6+) | Hardware revision override |
| `ECP5_SPEED_GRADE` | `8` (default) | ECP5 speed grade |

### Temporary Files

All build artifacts and logs go to `./tmp/` per CLAUDE.md rules:

```bash
## Logs
./tmp/apollo-build.log
./tmp/apollo-get-deps.log
./tmp/amaranth-py314-test.log
./tmp/moondancer-build.log
./tmp/gateware-analyzer-build.log
./tmp/gateware-facedancer-build.log
```

### Known Issues & Workarounds

#### TinyUSB Submodule Not Initialized

**Error**: `lib/tinyusb/examples/make.mk: No such file or directory`

**Solution**: Run `make APOLLO_BOARD=cynthion get-deps` before building.

#### LUNA_PLATFORM Not Found

**Error**: `ModuleNotFoundError: No module named 'cynthion'`

**Solutions**:
1. Ensure cynthion package is installed: `python3 -m pip install -e repos/cynthion/cynthion/python`
2. Use full platform path: `cynthion.gateware.platform.cynthion_r0_2:CynthionPlatformRev0D2`
3. Source OSS CAD Suite environment first

#### apollo-mux Runtime Context Trap (REPL works, `riscv` fails)

**Symptom pattern**:
- `apollo-mux` starts and connects to socket
- REPL responds to `help`
- `riscv` commands fail with `No module named 'cynthion'`

This usually means runtime context mismatch, not hardware failure.

**Distinguish the three common causes** (diagnose in this order):
1. **Package missing**: `python -c "import cynthion"` fails in all terminals.
   ```bash
   python3 -c "import cynthion; print(cynthion.__file__)"
   ```
2. **Wrong interpreter**: package is installed in one Python, but `apollo-mux` runs under another.
   ```bash
   which python3
   python3 -m pip show cynthion
   ```
3. **Wrong cwd/PYTHONPATH context**: `apollo-mux` starts from a context where the expected package path is not visible.

**Minimal reproducible failure sequence**:
```bash
## Example of a context mismatch that can fail on riscv command paths.
python3 /path/to/awto-cynthion/scripts/apollo-mux.py \
  --socket /path/to/awto-cynthion/tmp/apollod.sock --no-spinner -v
```

**Known-good sequence**:
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"

## System 3.15t holds the editable cynthion package - no venv involved.
python3 -m pip install -e cynthion/python

## Validate import path before launching REPL.
python3 - <<'PY'
import cynthion
print(cynthion.__file__)
PY

## Launch with the same interpreter used for the import check.
python3 scripts/apollo-mux.py \
  --socket "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion/tmp/apollod.sock" --no-spinner -v
```

**Validation checklist**:
1. `apollo-mux` shows socket connected.
2. Import check prints a valid `cynthion` module path.
3. `riscv canary` executes without import-path errors.
4. If command still fails, check device mode/API compatibility separately from Python environment.

#### Facedancer Prebuilt-Bitstream Fallback Runbook

Use this only when the local facedancer gateware build/toolchain path is blocked
and you need bring-up continuity.

**Prechecks**:
```bash
## 1) Confirm toolchain/build-path failure signature first.
## Typical signatures:
## - missing FPGA toolchain binaries
## - facedancer asset build fails

## 2) Confirm baseline USB mode before switching.
lsusb | rg -i '1d50:615b|1d50:615c'

## 3) Confirm a candidate prebuilt bitstream exists.
find "${REPOS_ROOT:-$HOME/git/awtoau}" -path '*/assets/*' -name 'facedancer.bit'
```

**Known fallback command pattern**:
```bash
cd "${REPOS_ROOT:-$HOME/git/awtoau}/awto-cynthion"
cynthion run \
  --bitstream /absolute/path/to/facedancer.bit facedancer
```

**Postchecks**:
```bash
## 1) Verify USB identity/mode changed as expected.
lsusb | rg -i '1d50:615b|1d50:615c'

## 2) Verify command path sanity.
cynthion run -h
```

**When this fallback is appropriate**:
1. You need immediate debug/bring-up continuity.
2. The canonical gateware build path is temporarily unavailable.

**Risks and limitations**:
1. Version drift: the prebuilt artifact may not match the current source tree.
2. Reproducibility gap: behavior cannot be attributed to local commit build output.
3. Trust boundary: only use artifacts from known local repos with provenance.

**Recovery to canonical path**:
1. Restore the local FPGA toolchain and successful `make assets` flow.
2. Re-run with the locally built facedancer artifact.
3. Record toolchain and artifact provenance in notes/logs.

### Next Steps

Track current phase completion criteria and phase 2+ planning in:
- apollo_serial_architecture_redesign_plan.md (`apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md`)

### References

- [Apollo README](https://github.com/greatscottgadgets/apollo) - Official build instructions
- [Cynthion Hardware](https://github.com/greatscottgadgets/cynthion-hardware) - Board schematics
- [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) - FPGA toolchain releases
- [Amaranth HDL](https://amaranth-lang.org/) - Python HDL framework

---

## Can we drop luna_soc? (plan and investigation log)

Source: `docs/toolchain-simplification.md`. Plan steps, recommendation and 2026-07-30 hardware state superseded; live facts are in the rewritten file.

## Can we drop `luna_soc`?

**Recommendation: a middle path. Do not drop `luna_soc` yet, but stop treating
it as a workspace-wide dependency — it is only needed by the facedancer SoC
build.** Our own `gateware/` designs can move to real `amaranth-soc` today, and
that removes the import-order hack, the annotation patch script, and most of the
confusion. The fork stays only until the facedancer SoC is dealt with separately.

Established 2026-07-30 by `scripts/amaranth_soc_dropin_test.py`. Evidence in
`tmp/logs/amaranth_soc_dropin_test.log`. Every claim below was tested, not
reasoned.

### Summary of what was measured

| Question | Answer |
|---|---|
| Is `luna_soc` a hard dependency of `cynthion`? | **Yes, unconditionally** — `import luna_soc` at `cynthion/python/src/__init__.py:9`, top level, not in a try/except |
| Does the **analyzer** gateware need it? | **No.** Zero references in `gateware/analyzer/` |
| Does the **facedancer** gateware need it? | **Yes** — 5 import sites in `gateware/facedancer/top.py` |
| Do *our* `gateware/` designs need it? | **No**, apart from the sys.path aliasing side effect |
| Is real `amaranth-soc` a drop-in for what we use? | **Yes** — all 6 probes pass |
| Is the py3.14 CSR annotation bug fixed upstream? | **Yes** — class-level `csr.Field` annotations work on real amaranth-soc under 3.15t |
| Upstream test suite | **295 passed** in 0.51 s, in the throwaway venv |

### 1. What actually pulls `luna_soc` in

`repos/cynthion/cynthion/python/pyproject.toml:47`:

```
    "luna-soc @ git+https://github.com/awtoau/awto-luna-soc.git@main",
```

So it is a hard install dependency pinned to our fork, and `cynthion/__init__.py:9`
imports it unconditionally with the comment *"Make sure all luna_soc's vendored
libraries are available"*.

**A false result worth recording**, because it is the kind of mistake this
project has been burned by. A first attempt used a `sys.meta_path` blocker with
`find_module()` and reported that `import cynthion` succeeded with luna_soc
blocked. That was wrong: `find_module` is the **removed** legacy import protocol
and was silently ignored, so nothing was ever blocked. Re-run with `find_spec`:

```
BREAKS   cynthion -> ImportError luna_soc blocked: luna_soc
BREAKS   cynthion.gateware.analyzer.top -> ImportError
BREAKS   cynthion.gateware.facedancer.top -> ImportError
```

`import cynthion` is a hard fail without luna_soc. Aliasing the vendored
`amaranth_soc` onto `sys.path` first does **not** help — both still break —
because the bare `import luna_soc` statement itself is what fails, regardless of
whether its stated purpose is already satisfied.

### 2. What we use from it

Our own designs, all four sites:

| File | Import | Why |
|---|---|---|
| `gateware/soc/top.py:45` | `core.blockram` | real use |
| `scripts/patch_amaranth_soc_annotations.py:110` | `core.blockram` | aliasing only, to locate the file it patches |

So the genuine surface is exactly two things:

- `luna_soc.gateware.core.blockram` — **127 lines**
- `luna_soc.gateware.cpu.VexRiscv` + `InterruptController` — **149 + 45 = 194 lines**, plus 5 pre-built VexRiscv `.v` files under `gateware/cpu/verilog/vexriscv/`

**321 lines of Python we would have to carry, against 4078 lines of vendored
`amaranth_soc` we would stop carrying.** That is the whole trade.

#### The import-order hack, and why it exists

`luna_soc/__init__.py:9-20` — the package's own comment calls it a "mildly evil
hack":

```python
try:
    try:
        import amaranth_soc
        import amaranth_stdio
    except:
        path = os.path.join(os.path.dirname(...), "gateware", "vendor")
        sys.path.append(path)
```

Importing a luna_soc peripheral is what appends the vendor directory to
`sys.path`, which is what makes the bare name `amaranth_soc` resolvable. Hence
`import luna_soc.gateware.core.blockram` before `from amaranth_soc import csr`,
or `ModuleNotFoundError`.

The second-order trap is worse:
importing `luna_soc.gateware.vendor.amaranth_soc` *directly* yields a **different
class object** for `wishbone.Interface` than the `sys.path`-aliased
`amaranth_soc`, so `Decoder.add()` rejects a structurally identical bus. Only the
the ordering but carries no explanatory comment — the one site missing the note.

If real `amaranth-soc` is installed standalone, the `try` succeeds, the vendor
path is never appended, and **the entire hazard disappears**.

### 3. Is real `amaranth-soc` a drop-in?

Tested in a throwaway venv at `tmp/amaranth-soc-dropin-venv` (the system
environment was not touched). All six probes pass:

```
PASS  import + versions                      amaranth_soc = 0.1.dev1+g3e3d8b7
PASS  class-level csr.Field annotations (the py3.14 bug)
PASS  csr.Builder + csr.Bridge (multiplexed.py shape)
PASS  wishbone.Decoder + WishboneCSRBridge
PASS  things only luna_soc provides          (blockram/cpu correctly absent)
PASS  REAL multiplexed.py elaborated against real amaranth-soc
```

The last one is the decisive result. the retired I2C mux prototype was imported
**unmodified**, with `luna_soc` replaced by an empty stub module so the aliasing
import became a no-op, and it **elaborated to 4670 lines of RTLIL**. The
peripheral is already portable; its luna_soc line is pure accident.

#### The py3.14 annotation bug is fixed upstream

This is the finding that most changes the picture. Real amaranth-soc handles the
class-level annotation form under free-threaded 3.15t:

```
class Control(csr.Register, access="w"):
    start : csr.Field(csr.action.W, unsigned(1))
=> Control fields: [('start',), ('stop',)]        PASS
```

So `scripts/patch_amaranth_soc_annotations.py` — and the 40-odd `__init__`
rewrites in `repos/cynthion` commit `53d3ea4` that worked around the same bug —
exist only to compensate for the **vintage of the vendored copy**, not for a real
upstream defect. On the live environment that script now reports:

```
already patched -- annotationlib path present
```

It is a no-op today and would be unnecessary, not merely already-applied, on real
amaranth-soc.

#### Divergence, quantified

Upstream `amaranth-soc` at `3e3d8b7`, in the throwaway venv: **295 passed, 6
subtests passed, 0.51 s**. The vendored copy reports `__version__ == "unknown"`
and fails 13 / does not collect 39 of the same suite. They have measurably
diverged, and the vendored side is the worse one.

### 4. What breaks

Concretely, if `luna_soc` were removed outright today:

**a) `import cynthion` fails.**
```
ImportError: No module named 'luna_soc'   at cynthion/python/src/__init__.py:9
```
This takes down the CLI, the analyzer, `cynthion run`, everything — even though
the analyzer gateware itself contains **zero** luna_soc references.

**b) The facedancer SoC build fails.** `gateware/facedancer/top.py` needs five
things that are genuinely not in amaranth-soc and never will be:
`blockram`, `spiflash` (incl. `ECP5ConfigurationFlashInterface`,
`SPIPHYController`), `timer`, `uart`, `usb2`, `InterruptController`, `VexRiscv`,
`provider.cynthion`, and `top_level_cli`. This is the real blocker and it is not
small.

**c) Our three `riscv/` designs lose `blockram` and `VexRiscv`** — 321 lines,
plus the pre-built `.v` files.

**d) `pip install amaranth-soc` from PyPI does not get you amaranth-soc.**
It installs a **placeholder at version `0` with no modules**; `import
amaranth_soc` then raises `ModuleNotFoundError`. The real package is
distributed from git only:

```
amaranth-soc @ git+https://github.com/amaranth-lang/amaranth-soc.git
```

This cost a full test cycle to discover and would silently defeat anyone
following the obvious instruction. It is also an argument *against* the
"depend on the real package directly" framing being as clean as it sounds —
you trade a git pin on our fork for a git pin on upstream, not for a PyPI
version.

### Recommendation

**Do not drop `luna_soc` wholesale. Do three narrower things instead.**

The proposal as written — "drop luna_soc, depend on real amaranth-soc" — fails
on (b): the facedancer SoC needs a peripheral library that amaranth-soc does not
contain. But the proposal's *goals* are almost entirely achievable without that.

#### Step 1 — install real `amaranth-soc` alongside, and the hack evaporates

Add real `amaranth-soc` to the environment. luna_soc's own `try: import
amaranth_soc` then **succeeds**, the vendor path is never appended, and every
downstream user gets the real package. This alone:

- kills the load-bearing import order at all five sites,
- makes `scripts/patch_amaranth_soc_annotations.py` unnecessary rather than
  merely already-run,
- ends the two-different-`wishbone.Interface`-class-objects trap,
- and needs **no change to `cynthion` at all**.

This is the highest value for the lowest risk and should be done first. It needs
verifying that luna_soc's peripherals work against real amaranth-soc rather than
their vendored vintage — the CSR/wishbone probes above say the API shapes match,
but the facedancer build is the real test.

#### Step 2 — cut our own designs loose

`gateware/probes/i2c/multiplexed.py`: delete the `import luna_soc...blockram` line and
its 6-line comment. Proven to work — that is what the RTLIL probe did.

The three `riscv/` designs: vendor `blockram.py` (127 lines) into `gateware/`
and import `VexRiscv` explicitly. 321 lines total. Worth doing because it makes
our designs independent of the fork.

#### Step 3 — only then consider the fork

Once steps 1 and 2 land, the fork `awtoau/awto-luna-soc` is needed for exactly
one thing: the facedancer SoC peripheral library. And its reason for existing is
already gone — HEAD `368174f` merged the py314-annotations fix, and real
amaranth-soc does not have that bug. So the fork can likely be **replaced by the
upstream `luna-soc` pin**:

```diff
-    "luna-soc @ git+https://github.com/awtoau/awto-luna-soc.git@main",
+    "luna-soc~=0.3.2",
```

which is what `debris/code/awto-cynthion-reference/.../pyproject.toml:47` already
had before the fork. **Verify before applying:** confirm upstream luna-soc 0.3.2
builds facedancer against real amaranth-soc. If it does, the fork, the
vendoring, and the patch script all go at once.

#### If you want the `cynthion` patch anyway

To make `cynthion` importable without luna_soc — worth it because the analyzer
does not need it and currently cannot run without it — the change is one line at
`cynthion/python/src/__init__.py:9`:

```diff
-# Make sure all luna_soc's vendored libraries are available
-import luna_soc
+# luna_soc is only needed by the facedancer SoC gateware, which imports it
+# directly. Importing it here made the analyzer and the CLI fail without a
+# dependency neither one uses. Its only purpose was to alias luna_soc's
+# vendored amaranth_soc onto sys.path; when real amaranth-soc is installed
+# that aliasing is both unnecessary and undesirable.
+try:
+    import luna_soc
+except ImportError:
+    pass
```

and the matching `pyproject.toml` move of `luna-soc` from `dependencies` into an
optional `[project.optional-dependencies] facedancer` extra. This is a patch to a
repo we own (`awtoau/awto-cynthion`), so it is a local commit, not an upstream
submission.

### Sequencing

1. Install real `amaranth-soc` (git, not PyPI) in the live environment; confirm the facedancer build still passes. **Biggest win, no code change.**
2. Drop the aliasing import from `multiplexed.py`. Free.
3. Vendor `blockram.py` for the `riscv/` designs. 127 lines.
4. Test upstream `luna-soc~=0.3.2` against real amaranth-soc. If it builds, drop the fork and delete `scripts/patch_amaranth_soc_annotations.py`.
5. Optionally make `cynthion`'s luna_soc import conditional so the analyzer stops depending on the SoC stack.

Steps 1–3 are safe and can be done immediately. Step 4 is the one that actually
retires the fork, and it is a build test, not a judgement call.

### Done 2026-07-31: the `cynthion` package is out of `gateware/`

A sixth step, not in the list above, turned out to be the cheapest of all and
has been taken.

The question this document asks is "can we drop `luna_soc`", and the answer kept
being "not while `cynthion` depends on it". But we were never using `cynthion`
for anything except **one class**: `CynthionPlatformRev1D4`, the r1.4 pin map.
Getting it dragged in the whole stack, because `CynthionPlatform` inherits
`LUNAApolloPlatform` → `LUNAPlatform` from `luna`, and the `cynthion` package
pins `luna-soc` to the fork.

That pin map is board wiring. It changes when the hardware revision changes,
which for r1.4 is never. So it is now vendored at `gateware/board/`
and the dependency is gone from our gateware.

#### What it cost

| File | Lines | What |
|---|---|---|
| `cynthion_r1_4.py` | 229 | the pin map, byte-identical to upstream from the class declaration down; the extra lines over upstream's 206 are the header explaining not to edit it |
| `core.py` | 158 | `CynthionPlatform`, ~60 lines of code where upstream had 134, the balance being the record of what was dropped and why |
| `resources.py` | 77 | `LEDResources` and `ULPIResource`, inlined |
| `__init__.py` | 24 | re-export, plus the naming warning |
| **total** | **488** | replacing the `cynthion` → `luna` → `luna-soc`-fork chain |

Imports are `amaranth` and `amaranth.build` only.

#### What turned out to be dead weight

Checked rather than assumed, which mattered — the guesses were not all right.

**`toolchain_program`, `toolchain_flash`, `toolchain_erase`,
`_ensure_unconfigured`** — dead. Every `build()` call site in this workspace
passes `do_program=False`; the FPGA is configured through `apollo_fpga` directly
by our own scripts. Dropping them also drops `apollo_fpga` as a platform
dependency.

**`LUNAPlatform` in its entirety** — `create_usb3_phy`, `get_led`,
`request_optional` and `NullPin` are portability shims for designs that target
several boards. Nothing here uses them. `LUNAApolloPlatform` contributed exactly
one live method, `port_sharing`; its `apollo_gateware_phy` is unused.

**`clock_domain_generator = LunaECP5DomainGenerator`** — already superseded by
`VariableClockDomainGenerator`, which is why it was safe to drop the default.

#### What was load-bearing, against expectation

**`pseudo_power_supply_fragment`** is the one to keep. r1.4 strands I/O balls to
VCCIO and GND that must be *driven* to source and sink additional supply
current, and Amaranth leaves an unrequested pin undriven. Cut it and the board
runs on less supply than it was designed for, with no build error to say so.
It needs `prepare()` alongside it, which is the only reason that override
survived.

**`toolchain_prepare`** sets `--freq 38.8`, the SPI configuration clock the
board reads its bitstream at on power-on. Not cosmetic.

**`DEFAULT_CLOCK_FREQUENCIES_MHZ`** is read by
`gateware/probes/adv_speed/adv_speed_gateware.py` to size a UART divisor.

#### Two things the plan did not anticipate

**`amaranth_boards` is not installed on this machine.** It is vendored *inside*
the `cynthion` package, which injects it into `sys.modules` from its `__init__`
under a comment reading "Mildly evil hack". So `import amaranth_boards` only
works once `cynthion` has been imported — a platform depending on it would still
be pulling `cynthion` in, just invisibly. Only two constructors are used,
`LEDResources` and `ULPIResource`, both a dozen lines of pure `amaranth.build`,
so they are copied verbatim into `resources.py`. Verbatim and not tidied: they
build the pin records the map is expressed in, and a "cleaner" version that
constructed a subsignal differently would silently change the pin map.

**The package cannot be called `platform`.** `gateware/` goes on `sys.path`, so
a package named `platform` there shadows the **standard library** `platform`
module for the whole process. `amaranth/tracer.py` imports it. Found by doing it
and watching the stdlib import resolve to our `__init__.py`. Hence
`board`. Do not rename it back.

#### Proof

`scripts/platform_vendor_compare.py` does not diff source — a copy is exactly
the change that reviews clean and is wrong on the bench. It runs the real
place-and-route both ways, upstream platform and vendored, on the same design
text, and compares what nextpnr decided:

```
blinky: utilisation identical -- TRELLIS_COMB=39, TRELLIS_FF=28, TRELLIS_IO=59, DCCA=1, GSR=1, ...
blinky: pin assignment identical -- 59 pins located
wide:   utilisation identical -- TRELLIS_COMB=61, TRELLIS_FF=28, TRELLIS_IO=105, DCCA=1, GSR=1, ...
wide:   pin assignment identical -- 105 pins located
```

`wide` exercises both ULPI PHYs, HyperRAM, QSPI flash, the sideband pins and
both Type-C controllers, so the 105 located pins cover the bulk of the resource
list where a transposition would be easiest to miss by eye. Utilisation alone
would not catch a swapped pin; the per-signal ball comparison is the part that
does.

Five designs then elaborated to a bitstream against the vendored platform:
`hyperram_identify`, `hyperram_regfuzz`, `vexii_bench_soc` and `bitstream_sink`. `scripts/check.py` is 6/6.

```bash
python3 scripts/platform_vendor_compare.py   # → tmp/logs/platform_vendor_compare.log
```

#### What still imports `cynthion`, and why that is correct

Nothing in `gateware/` does, except `soc/top.py`, which was left
alone only because another investigation owned it at the time; its change is the
same one-line import swap.

In `scripts/`, the remaining importers consume upstream's **own gateware and
register maps**, not our board definition, so vendoring the pin map does not
help them: `selftest_leds.py`, `selftest_led_modes.py` and `phy_probe.py` use
`cynthion.selftest.registers`; `check.py` and `install.py` elaborate upstream's
analyzer and facedancer gateware against r0.2 as a toolchain smoke test;
`cyn_main.py` drives the same. `platform_vendor_compare.py` imports it
deliberately, as the baseline it compares against.

**This does not by itself retire the fork.** `luna` is still a real dependency of
`gateware/` for USB (`USBDevice`, `USBSerialDevice`, the stream endpoints),
HyperRAM (`HyperRAMInterface`) and `JTAGRegisterInterface`, and `luna_soc` still
supplies `blockram` and the CPU wrappers. What changed is that the dependency is
now on `luna` for things `luna` actually provides, rather than on the entire
`cynthion` → `luna` → `luna-soc`-fork chain for a list of pin names. Step 4
above is still the one that retires the fork.

### Reproducing

```bash
python3 scripts/amaranth_soc_dropin_test.py     # → tmp/logs/amaranth_soc_dropin_test.log
```

Creates `tmp/amaranth-soc-dropin-venv`, installs real amaranth-soc from
`tmp/forks/amaranth-soc` (or git), and runs all six probes. Never touches the
system environment.

### Hardware state at time of writing

Recorded so a later session can tell whether anything here disturbed the board.
**Nothing in this investigation touched the gateware** — no build, no flash. The
board was only read.

Observed 2026-07-30, Cynthion r1.4, firmware `v1.1.1-35-g74db0e6`:

```
python3 repos/apollo/apollo_fpga/commands/cli.py info
    Hardware: Cynthion r1.4   ADC reading: 3207   USB API 1.2

python3 scripts/sideband_read.py
    4/4 commands CRC OK (PING, STATUS, POWER, DEVICES)
    firmware health: ok=4 crc_fail=0 timeout=0
    PASS

python3 scripts/sideband_read.py --soak 5000
    good 5000  short 0  crc_bad 0   100.00%
    PASS
```

The sideband test bitstream is intact and the link is clean over 5000
transactions. `DEVICES` reports flash Winbond type 0x40 4 MiB and **hyperram
absent**, and `POWER` returns the fixed test pattern rather than real
measurements — both are the expected behaviour of this bitstream, not faults.

---

## Toolchain versions: dated upstream snapshot 2026-08-05

Source: `docs/toolchain-versions.md`. Time-varying; superseded by `gh release list` / PyPI.

### Upstream maintenance status

Relevant because a dormant upstream means patches stay ours indefinitely, and
`docs/upstream-boundary.md` records what has already been replaced.

| upstream | last commit to default branch | latest release |
|---|---|---|
| `greatscottgadgets/apollo` | **2024-12-10** | v1.1.1 (2024-11-23) |
| `greatscottgadgets/luna-soc` | **2025-05-19** | 0.3.2 (2025-05-19) |
| `greatscottgadgets/luna` | 2025-08-22 | 0.2.3 (2025-08-22) |
| `greatscottgadgets/cynthion` | 2026-05-22 | 0.2.5 (2026-05-22) |
| `greatscottgadgets/facedancer` | 2026-05-22 | 3.1.3 (2026-05-22) |
| `amaranth-lang/amaranth` | 2026-07-16 | v0.5.9 (2026-07-16) |
| `amaranth-lang/amaranth-soc` | 2026-05-23 | none — tag `v0.1a` only |
| `amaranth-lang/amaranth-stdio` | 2026-01-27 | none — no tags |
| `amaranth-lang/amaranth-boards` | 2026-08-01 | v0.0.20 (2026-08-01) |
| `YosysHQ/yosys` | 2026-08-04 | v0.67 (2026-07-09) |
| `YosysHQ/nextpnr` | 2026-08-04 | nextpnr-0.10 (2026-03-12) |
| `YosysHQ/prjtrellis` | 2026-05-09 | **1.4 (2023-05-16)** |
| `SpinalHDL/VexiiRiscv` | 2026-07-27 | none — no tags |

None are archived. Apollo's `main` is frozen, but a `hil-ci` branch moved
2026-05-08, so the repository is not dead — only its default branch is.
`amaranth-soc`'s CSR register API is still tracked by an open RFC
([#68](https://github.com/amaranth-lang/amaranth-soc/issues/68)) and has no
frozen release, so it will keep drifting from any vendored snapshot.

Original FPGA toolchain and submodule drift tables:

| tool | in use | upstream | note |
|---|---|---|---|
| OSS CAD Suite | `20260522` | nightly tags, `2026-08-04` latest | nightly-only; there is no stable channel, so "the release" is whichever nightly is pinned |
| Yosys | `0.65+57` (`9d0cdb855`) | `0.67` (2026-07-09) | a post-0.65 dev build, two minor releases behind |
| nextpnr-ecp5 | `0.10-74` (`ee605e2b`) | `0.10` (2026-03-12) | **ahead of the last release**, 74 commits past the tag |
| ecppack / prjtrellis | `1.4-79` (`56bb170`) | `1.4` (2023-05-16) | ahead of the tag; prjtrellis has not cut a release in over three years and its commits are build/packaging cleanups |

| submodule | pinned | fork | upstream default branch | drift |
|---|---|---|---|---|
| `repos/apollo` | `69c6ba8` (2026-08-03) | `awtoau/awto-apollo` | last moved **2024-12-10** | **60 ahead, 0 behind** |
| `repos/cynthion` | `7fa0c6a` (2026-07-28) | `awtoau/awto-cynthion` | 2026-05-22 (`0.2.5`) | 25 ahead, 1 behind |
| `repos/vexiiriscv` | `f8774d4` (2026-07-20) | upstream directly | `dev`, 2026-07-27 | 48 behind |
| `repos/cynthion-hardware` | `e5cf493` | `awtoau/awto-cynthion-hardware` | KiCad sources | — |

---

## git.md: apollo pin on no remote

Source: `docs/git.md`. Resolved: pin `90c8b7b6` is on `awtoau/awto-apollo`; [#373](https://github.com/awtoau/cynthion-workspace/issues/373) closed.

### `repos/apollo`'s pin is on no remote

Pin `90c8b7b6` exists in the superproject's object store and nowhere else;
`git fetch` of it answers `upload-pack: not our ref`. `git submodule update`
cannot repair a checkout that lacks it and a fresh clone cannot build. Worktrees
are unaffected because they share the store rather than fetching.
`worktree-check` reports it as a standing warning. Fix is to push it: [#373](https://github.com/awtoau/cynthion-workspace/issues/373).

Also from `docs/git.md` (superseded): "The workspace vendors seven Great Scott Gadgets repos as git
submodules" (apollo, cynthion, cynthion-hardware, facedancer, luna, packetry, saturn-v); `.gitmodules`
lists four (apollo, cynthion, cynthion-hardware, vexiiriscv). "Eleven submodules, not four" (worktree
setup recursion), "127 MB here against 396 MB of shared history".
