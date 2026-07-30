# Upstream patch plan: Great Scott Gadgets

What we carry locally that upstream might want, in what order it has to be
applied, and what each patch would have to demonstrate on hardware before it
could be sent.

The governing validation rules are in a separate document,
[`upstream-patch-process.md`](upstream-patch-process.md). Two files rather than
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

## Divergence, measured

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

## Where `docs/upstreamable-patches.md` is wrong or stale

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

## Part 1 — Apollo inventory (34 commits)

`GSG` = genuine upstream bug fix. `OURS` = project scaffolding or investigation.
`SPLIT` = contains both and must be divided before submission.

### Group A — firmware fixes and ROM savings (upstreamable)

| Commit | What it does | Class | Test today |
|---|---|---|---|
| `39a2213` | Sticky JTAG/UART pin mutual-exclusion lock. On r1.4 the console UART and JTAG share PA10/11/14/15 with no hardware arbitration; a CDC line-coding callback during a JTAG session re-pinmuxes PA14 out from under it and corrupts the session. Adds `apollo_mode` HOLD↔JTAG_PROGRAM lock, gates the three CDC callbacks and `uart_configure_pinmux()`. | **GSG** — real corruption bug on shipping hardware | **Yes.** `firmware/test/test_apollo_mode.c` (host-native, 16 checks) + `tests/test_hardware.py::JTAGUARTExclusionHILTest` (2 HIL tests) |
| `df4a93b` | Host-triggerable boot-to-DFU vendor request `0xed`, plus host `boot_to_dfu()`. Defers the reboot to the ACK stage, because `tud_dfu_runtime_reboot_to_dfu_cb()` arms the WDT and hangs — doing it in the setup handler makes every success look like a USB failure. | **GSG**, with a caveat: upstream may want a different request number | **Yes.** `tests/test_hardware.py::BootToDFUHILTest`, gated behind `APOLLO_TEST_ALLOW_REBOOT=1`, restores the device in `tearDown` |
| `6cc219e` | Gates conflicting vendor requests during JTAG programming. Two-stage: `JTAG_START` takes the pin lock but gates nothing; the first programming-class request escalates to `MODE_JTAG_PROGRAMMING` and only then are reconfigure / force-offline / takeover / debug-SPI refused. Adds `EMERGENCY_RESET` (`0xec`) as the one legal preemption path. | **GSG** — completes the exclusivity model | **Yes.** `test_apollo_mode.c` extended 16 → 31 checks |
| `da564f8` | Two ISR/main-loop races. `fpga_online` written from USB interrupt context, read from `led_task()`, not `volatile`. `edge_counter` read-then-clear in `fpga_adv_task()` was not atomic against `EIC_Handler`, so an edge landing between the two statements is lost, under-counting the window that feeds `fpga_requesting_port()`. | **GSG** — both are live in shipping firmware | **No dedicated test.** Commit rests on "the FPGA USB handoff still works". A race that is lost 1-in-N times is exactly what a pass/fail smoke test cannot see |
| `973fa78` | `CFG_TUD_VENDOR=0` on d11. The vendor *class driver* was compiled in but is never used — Apollo's vendor requests are control transfers via `tud_vendor_control_xfer_cb()`, which is device-stack, not class-driver. No vendor interface in the config descriptor, no `tud_vendor_*()` call anywhere. −520 B ROM, −680 B RAM. | **GSG** — pure dead weight | Covered incidentally by the CLI smoke tests |
| `651b027` | `msft_10_compat_id` / `msft_10_ext_props` declared `const` so they live in `.rodata` not `.data`. −176 B RAM. Never written; only ever an IN-transfer source. | **GSG** | `test_safe_commands_respond_correctly` exercises `GET_MS_DESCRIPTOR` |
| `01ae228` | Four ROM savings: `static const` on the `board_rev` threshold table, the LED pin table (was rebuilt on the stack in four functions), `jtag_deinit()`'s `gpio_pins[]`; and internal linkage for 13 `vendor.c` handlers and 2 `jtag_tap` helpers. −276 B. | **GSG** | No dedicated test; behaviour-preserving by construction, but see the note on `--gc-sections` below |

### Group B — host-side Python fixes (upstreamable)

| Commit | What it does | Class | Test today |
|---|---|---|---|
| `8054f62` | `FlashBridgeConnection` becomes a context manager instead of relying on `__del__`. Handing the port back is a USB transfer, so doing it in a destructor means libusb I/O at GC or interpreter-shutdown time when the context may be gone — that segfaults instead of reporting, and leaves the port with the FPGA so Apollo appears to have vanished. Upstream issue **#75**. | **GSG** — clear bug, clear fix | **Yes,** `test_4_flash_fast_is_not_broken`, gated behind `APOLLO_TEST_ALLOW_FLASH_FAST=1` |
| `6f16848` | `apollo install-udev`. The shipped rules cover `1209:000a` and `000e` but not `000f`, which is what the flash bridge enumerates as, so `flash-fast` failed with EACCES for non-root users and left the USB stack wedged. | **GSG** (the missing rule certainly is; the *command* is a design choice upstream may want differently) | No automated test — it writes to `/etc/udev/rules.d` |
| `12e6d97` | `apollo exit-dfu`. `boot-to-dfu` had no inverse; a board in the bootloader could only be recovered by physically unplugging it. Uses a zero-length `DFU_DNLOAD` (**not** `DFU_DETACH` — the bootloader has no detach case) and **must** claim the interface or it stalls `LIBUSB_ERROR_PIPE`. Python only. | **GSG** — completes an existing command pair | **No test.** Notable given it is the doc's flagship "ready" item |
| `e034daa` | ECP5 SRAM configuration 1.53× faster. Host: plumb `ignore_response` through the bitstream burst — the burst is write-only but was doing a `GET_IN_BUFFER` per chunk, 739 ms of 2575 ms. Firmware: pipeline SERCOM SPI, queueing the next byte on DRE instead of waiting for RXC. +28 B flash. | **GSG** — real speedup, no behaviour change | **No dedicated test.** Commit cites 6 consecutive runs with DONE set and no error bits. Needs a correctness test, not just a timing one |

### Group C — test infrastructure (upstreamable, needs reshaping)

| Commit | What it does | Class | Notes |
|---|---|---|---|
| `24b3b7a` | Smoke-tests every apollo CLI command | **GSG-useful** | Upstream has **no** Python test suite — `git ls-tree upstream/main` finds only `apollo_fpga/support/selftest.py` and the d21 selftest C files. These would be a new thing for the project, so shape is upstream's call |
| `daecad7` | Command responses + flash write paths | **GSG-useful** | Adds `APOLLO_TEST_ALLOW_FLASH_WRITE` gating |
| `9c72930` | PAC1954 power-monitor HIL tests | **GSG-useful** | 4 tests |

### Group D — ours, not upstream's (do not submit)

These are for this project's own investigation. Interesting eventually, not bug
fixes, not proposed.

- **Sideband / FPGA_ADV command link** — `4a46b24`, `a7b8283`, `b48d4bf`,
  `ca32e5b`, `9ec249a`, `6cdc7c3`, `0b1529a`, and the feature hunks of
  `4bf7691`. A whole new protocol between the MCU and the FPGA over the FPGA_ADV
  pin. This is a design proposal, not a fix; upstream issue #68 territory.
- **Quad-SPI flash investigation** — `9b8ffb8`, `2418362`, `f42433d`, `1e90e5c`,
  `ace1fd8`, `c3c1911`, `4ead7df`, `42bffe6`, `7a8e304`. Gateware for reading
  the config flash faster, via Glasgow's controller. Exploratory.
- **PLL work** — `0ab00de`, `c3c5fbe`. Variable clock via `ecppll`.
- **`74db0e6`** — synthetic JTAG benchmark on vendor request `0xb9`. A
  measurement tool. It also *moved* to `0xb9` to clear a collision, which is a
  hint that our local request-number allocations have drifted from upstream's
  and need reconciling before any vendor-request patch is sent.

### The split case

**`4bf7691` must be divided.** As committed it contains:

| Hunk | Upstreamable? |
|---|---|
| `firmware/Makefile`: `-flto=auto -flto-partition=one`, `-Wl,--entry=Reset_Handler -Wl,-u,exception_table` | **Yes** — general build improvement, large win |
| `fpga_adv.c`: fold the per-byte UART timeout to a compile-time constant, removing a runtime `__udivsi3` (266 B of soft division for one divide) | **Yes** — independent ~300 B saving |
| `vendor.c`: `0xFFFE` sub-command issuing a sideband command | **No** — Group D feature work |
| `handle_fpga_adv_mode` → `static` | Belongs with `01ae228`'s linkage pass |

Only the first two go upstream, as **patch A1** below.

---

## Part 2 — the ordering

### The LTO-first hypothesis: **confirmed, and by a wider margin than expected**

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

### The ordering constraint that was found by testing, not by reading

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

### The proposed order

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
| **A7** | `da564f8` ISR/main-loop races | Independent of A1–A6 (touches `fpga.c`, `fpga.h`, `fpga_adv.c`). Placed here because it is order-independent and because its own commit message flags that if upstream #68 lands, this hunk goes away — so it should be the easiest patch to drop |
| **A8** | `01ae228` reclaim 276 B | **Must be last.** Conflicts in any earlier position (proven above). A cleanup pass over regions A4–A6 restructure |

**Series B — Apollo host-side Python (order-independent)**

No file overlaps with Series A and no ordering constraints among themselves,
except that B1 and B2 both touch `apollo_fpga/commands/cli.py` and are on the
same subject, so they read better together and in this order.

| # | Patch | Why here |
|---|---|---|
| **B1** | `6f16848` `install-udev` (the missing `1209:000f` rule) | The rule is the precondition for testing B2 as a non-root user |
| **B2** | `8054f62` flash-bridge port handback (#75) | The fix proper. Pairs with B1 as the two halves of #75 |
| **B3** | `12e6d97` `exit-dfu` | Independent. Touches `__init__.py` and `cli.py`. Naturally follows A5 conceptually (it is the inverse of boot-to-DFU) but has no code dependency |
| **B4** | `e034daa` configure speed-up | **Straddles.** Its `spi.c` hunk is firmware (+28 B) and its `ecp5.py` hunk is host. Either submit as one cross-cutting patch after both series, or split. Recommend submitting last, whole, since the two halves are jointly measured |

**Series C — test infrastructure**

`24b3b7a`, `daecad7`, `9c72930`. All three touch only `tests/test_hardware.py`
and are strictly additive, so they are order-independent among themselves and
against everything else. **But they are the enabling work for the process rules**
— see the note below on the ordering paradox.

### Order-independence summary

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

### The ordering paradox in Series C

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

## Part 3 — per-patch test requirements

What a hardware test would have to *demonstrate* — not "the device still
enumerates", but "the specific broken thing is now not broken". Where a test
exists it is named; where it does not, what it would need to do.

| Patch | Test requirement | Exists? |
|---|---|---|
| **A1** LTO | Must catch the dead-binary failure mode, which does not announce itself. Assert the linked vector table resolves Reset, SysTick, USB, EIC, SERCOM1 and TC1 to real code rather than `Dummy_Handler` (`scripts/verify_vectors.py` does this and **is untracked in the apollo repo** — it would have to be contributed, or reimplemented, for upstream to have the guard). Then, because LTO rewrites every byte, exercise the paths where a miscompilation hides: the CDC console (TinyUSB FIFO code LTO reshapes via constprop), the EIC/TC1 ISR paths, and a JTAG scan. **A passing link proves nothing here.** | Partial — the guard script exists in the workspace, not in the repo |
| **A2** vendor drv | Prove the removed driver was genuinely unused: enumerate, walk the config descriptor and assert no vendor interface, then confirm every vendor *request* still answers. The risk is not that it breaks loudly but that some request quietly depended on the class driver's presence | Incidental only — wants an explicit descriptor assertion |
| **A3** WCID | `GET_MS_DESCRIPTOR` returns both descriptors byte-identically to before: 40 B compat ID and 142 B ext props, headers intact. A `const` move that corrupted an offset would still return *something* | `test_safe_commands_respond_correctly` touches it; needs the byte-exact comparison |
| **A4** pin lock | Must prove the OS does **not** provide this exclusion: open JTAG via libusb and the CDC interface via `cdc_acm` *simultaneously* (they are independently openable), drive serial traffic during a JTAG session, and assert the session is uncorrupted and serial is refused — then assert serial recovers after `jtag_deinit()` | **Yes** — `JTAGUARTExclusionHILTest` (2 tests) + `test_apollo_mode.c` (16 checks) |
| **A5** boot-to-DFU | Device actually appears as the Saturn-V bootloader afterwards (check VID/PID, not just "the request returned"), and the host sees a *clean* completion — the whole point of the ACK-stage deferral is that a success must not look like a USB error. Must restore the device afterwards or the suite is single-shot | **Yes** — `BootToDFUHILTest`, `APOLLO_TEST_ALLOW_REBOOT=1`, reflashes in `tearDown` |
| **A6** gating | Three things: a plain `jtag-scan` is unaffected (no escalation); a conflicting request *is* refused once escalated; and the gate *releases* — `apollo info` works after the session. Plus `EMERGENCY_RESET` round-trips and genuinely releases the pin lock. Escalation must not drop the pin lock | **Yes** for the state machine (31 checks); the release-after-session path wants an explicit HIL assertion |
| **A7** ISR races | **This is the hard one and no adequate test exists.** A race lost 1-in-N times passes any single-shot test. `fpga_online`: needs the compiler-caching behaviour observed, e.g. assert the flag is `volatile` in the disassembly, since a runtime test cannot reliably provoke it. `edge_counter`: drive advertisement edges at a known rate and assert the counted total matches within tolerance over many windows — an under-count is the symptom. A "handoff still works" check does not test either bug | **No.** Weakest coverage in the set relative to bug severity |
| **A8** ROM savings | Behaviour-preserving by construction, so the test is a size assertion plus proof nothing was discarded. **`--gc-sections` makes the headline number untrustworthy**: an uncalled function looks like a saving while being absent. Assert the expected symbols are present (or verifiably inlined, not dropped) — under LTO a missing symbol may mean inlined, so confirm against disassembly. Then LED, board-rev detect and JTAG paths still function | **No** — needs a symbol-presence assertion, not just a size delta |
| **B1** udev | Rule file contains `1209:000f`; `--print-only` output is parseable; refuses to install without root; errors cleanly on non-Linux. Then the real test: `flash-fast` succeeds **as a non-root user**, which is the actual bug | No automated test |
| **B2** #75 handback | Two failure modes, both must be provoked deliberately: (a) transfer fails mid-flash → assert the port went back to Apollo *before* the exception propagated, and Apollo is still on the bus; (b) `close()` after a failed `__init__` touches no USB. Plus no segfault, which means asserting the interpreter exit status, not just the absence of a traceback | **Yes** — `test_4_flash_fast_is_not_broken`, `APOLLO_TEST_ALLOW_FLASH_FAST=1` |
| **B3** `exit-dfu` | Full round trip: `boot-to-dfu`, confirm the bootloader is present, `exit-dfu`, confirm the *application* is running again with no replug. Must also assert the two things that cost debugging cycles: that `DFU_DETACH` is refused (so nobody "simplifies" it back) and that an unclaimed interface stalls `LIBUSB_ERROR_PIPE` | **No.** Flagged as "ready" in the old doc with no test at all |
| **B4** configure speed | Correctness first, speed second: after `configure`, the ECP5 status register reads DONE with no error bits, over enough consecutive runs to catch the dropped-byte corruption the pipelining nearly introduced (its earlier racy version silently corrupted TDO and produced an all-zero status register — so **assert the status register is non-zero and correct**, never just "no exception"). Then assert the speedup as a regression guard | **No dedicated test** |
| **C1–C3** | Each must fail when its target patch is reverted. A test that passes both with and without the fix is not evidence | Self-testing by construction |

---

## Part 4 — Cynthion inventory (25 commits)

Less mature than the apollo set: more scaffolding, fewer clean fixes.

### Plausibly upstreamable

| Commit | What it does | Class | Test today |
|---|---|---|---|
| `c0144f4` | Fixes a wrong function name in a `write_endpoint` log message (upstream **#28**) | **GSG** — trivial, obviously correct | None needed beyond a build |
| `56a64c2` | Removes a duplicate `ep_iso_in` from the facedancer module hierarchy | **GSG** — an actual elaboration bug | None. Wants a gateware elaboration test |
| `fc81a06` | Makes `make clippy` pass cleanly (**#35**) | **GSG** — lint hygiene | `make clippy` is the test |
| `7378797` | Event queue overflow drops the event instead of spinning | **GSG** — a spin on overflow is a hang | None. Wants a queue-saturation test |
| `6fa3e0a` | Clamps endpoint `max_packet_size` to `EP_MAX_PACKET_SIZE` | **GSG** — bounds check | None |
| `edf35c9` | udev rule for the Apollo flash bridge (`1209:000f`) | **GSG** — same missing rule as apollo `6f16848`; **submit these two together or neither**, they are one bug across two repos | None |
| `482bcf2` | Integer overflow checks in all profiles | Arguable — a policy change with a runtime cost. Upstream's call | None |

Every hash in this document was read from `git log` in this workspace on
2026-07-30 and should be re-confirmed with `git show` before it is placed in a
submission: local history can be rebased, and a wrong hash in a patch series is
an error that survives review.

### Ours, not upstream's

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

### The cynthion prerequisite

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

## Part 5 — what cannot be upstreamed, and why

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
  competes with upstream's own #68 direction, so it is a discussion to open, not
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
- `local scripts/verify_vectors.py` is untracked in the apollo repo but is the
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

## Recommended submission sequence

1. **Series C** (tests) — must exist first; validated against unmodified
   `upstream/main`.
2. **A1** (LTO) alone — the enabling patch, highest risk, wants isolation.
3. **A2, A3** (savings) — widen the margin.
4. **A4 → A5 → A6** (mode work) — strictly this order.
5. **A7** (races) — once it has a test that can actually fail.
6. **A8** (ROM cleanup) — last, it conflicts anywhere else.
7. **B1 + B2** together (#75, both halves), then **B3**, then **B4**.
8. **Cynthion**: `c0144f4`, `fc81a06`, `56a64c2`, `7378797`, `6fa3e0a`, plus
   `edf35c9` paired with apollo B1. After resolving `53d3ea4`.

Each of steps 2–8 is a separate upstream submission, and each one re-runs the
full sequence from patch 1 per the process rules.
