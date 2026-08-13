# Patches worth sending upstream

> **Superseded for planning purposes.** The full inventory, ordering and
> validation procedure now live in
> [`upstream-patch-plan.md`](upstream-patch-plan.md) and
> [`upstream-patch-process.md`](upstream-patch-process.md). This file is kept for
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

## Ready — a clear bug with a verified fix

### `exit-dfu`: recover from the bootloader without a power cycle

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

### `trigger_fpga_reconfiguration()` never releases INITN

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

### `flash-fast` port handback ([#75](https://github.com/awtoau/cynthion-workspace/issues/75))

`8054f62`. `FlashBridgeConnection` relied on `__del__` to hand the shared USB
port back to Apollo. Handing the port back is a USB transfer, so doing it from
a destructor means I/O at garbage-collection or interpreter-shutdown time, when
the libusb context may already be gone — that crashed the interpreter rather
than reporting an error, and left the port with the FPGA so Apollo appeared to
have vanished.

Now a context manager, so the port goes back even when the body raises.

### Two ISR/main-loop races

`da564f8`. `fpga_online` and `edge_counter` are written from an ISR and read
from the main loop without protection.

### Sticky JTAG/UART pin mutual exclusion

`39a2213`. On r1.4 the UART pins R14/T14 are shared with JTAG TDI/TMS, so the
microcontroller can use either function but not both. Enforced with a lock
rather than left to convention.

## Probably wanted, but shaped for us

- `df4a93b` — the `boot-to-DFU` vendor command itself (0xed). Upstream may
  prefer a different request number.
- `6f16848` — `install-udev`, which fixes a `flash-fast` permission failure.
- `973fa78`, `651b027`, `01ae228` — d11 ROM savings: disabling the unused
  TinyUSB vendor class driver, moving WCID descriptors to flash. Useful if
  upstream is also tight on that part, irrelevant otherwise.
- `24b3b7a`, `daecad7`, `9c72930` — test coverage. Upstream has little, so
  these may need reshaping to fit whatever they would rather have.

## Ours, not upstream's

The quad-SPI flash work (`42bffe6`, `4ead7df`, `c3c1911`), the PLL changes
(`c3c5fbe`, `0ab00de`) and the FPGA_ADV sideband link (`6cdc7c3`, `9ec249a`)
are all for this project's own investigation. They may be interesting upstream
eventually but are not bug fixes and are not proposed here.

## Before submitting anything

Public repositories we do not own need a content scrub — no local filesystem
paths, no serial numbers, no credentials — and explicit confirmation that the
change should go to someone else's repository rather than staying on a fork.
