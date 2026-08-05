# RTIC with real moondancer USB code: it runs, and it costs the same as the skeleton said

Issue #115. `docs/rtic-adoption.md` proved RTIC compiles for this machine against
a skeleton whose tasks increment a counter. The objection to that, made on the
issue, was fair: **this core is being built as a USB controller, and an idle
skeleton says nothing about a runtime resident on the dispatch path of a real
workload.** It could be worse than 38% of the I-cache or much better.

It is 38.3%. The idle measurement did not flatter RTIC and did not understate it.

## 1. The hard finding first: this SoC has no USB device controller

**There is nothing here to run moondancer's USB peripheral code against.** The
only USB peripheral in the gateware is `ecp5-test/riscv/ulpi_window.py`, a
four-register window that reads and writes a USB3343's *PHY* registers over the
ULPI bus. It cannot send or receive a packet, has no endpoint FIFOs, no SETUP
register and no interrupt. `src/ulpi.rs` is its whole driver and it is a
diagnostic. The AUX port's USB console is LUNA's `USBSerialDevice`, hard-wired
to a 16550, exposing no CSR and no interrupt of its own.

moondancer needs `USB0`/`USB0_EP_CONTROL`/`USB0_EP_IN`/`USB0_EP_OUT` and the
same three again for USB1 and USB2. None of them exists here.

So a port that ran moondancer's `get_usb_interrupt_event` was not available. What
was available turned out to be more than a shape, and the reason is worth
stating: **`smolusb::control::Control` is written against a trait**, not against
a peripheral. `dispatch_event` takes `&D where D: UsbDriver`, so the entire
control-transfer state machine is hardware-independent and ports verbatim.

## 2. What was ported

`firmware/cynthion-soc/src/usb.rs`, vendored from
`greatscottgadgets/cynthion` per `docs/upstream-boundary.md`'s "do not inherit a
stack to get one file". Both trees are BSD-3-Clause.

| piece | source | status |
|---|---|---|
| `SetupPacket` and its accessors | `smolusb/src/setup.rs` | **real** |
| `Direction`, `Recipient`, `RequestType`, `Request`, `Feature` | `smolusb/src/setup.rs` | **real** |
| `UsbEvent` | `smolusb/src/event.rs` | **real** |
| `Control::dispatch_event`, all sixteen arms | `smolusb/src/control.rs` | **real** |
| descriptor request dispatch | `smolusb/src/device.rs` | **real** control flow, byte tables for content |
| the event queue and the handler that fills it | `moondancer.rs`'s `MachineExternal` | **real** shape |
| the interrupt that starts it | a 16550 RX line through the PLIC | **real hardware interrupt** |
| the FIFO the handler reads a SETUP packet from | the 16550's RBR | **stand-in** |
| what endpoint writes go to | `usb::Endpoints`, a RAM buffer | **stand-in** |

Three adaptations, each forced and each recorded in the module comment:

* **No `log`.** smolusb calls `error!`/`warn!`/`trace!` throughout. This firmware
  has no logging crate and `scripts/soc_irq_log_check.py` forbids a handler
  reaching a console at all, so each call site became a counter that idle prints.
  Nothing is silently dropped — `usb: trace ...` reports all seven.
* **Slices, not iterators.** `write_requested` takes `&[u8]` rather than
  `Iterator<Item = u8>`, because smolusb's iterators exist to walk `zerocopy`
  views over packed descriptor structs and byte tables need no view. Same
  truncation rule, same return value.
* **No Microsoft OS 1.0 branch and no string-table indirection.** Both are
  descriptor *content*; neither changes the state machine.

## 3. It runs

`scripts/soc_usb_test.py` drives a real enumeration at both binaries under QEMU —
bus reset, GET_DESCRIPTOR(Device), SET_ADDRESS(0x25), GET_DESCRIPTOR(Configuration),
GET_DESCRIPTOR(String,0), SET_CONFIGURATION(1), GET_STATUS, and a libgreat vendor
request — as 18 events, and asserts what came back.

    usb: ReceiveSetupPacket(0) -> Send writes 1
    usb: SendComplete(0) -> WaitForZlp writes 1
    usb: ReceivePacket(0) -> Idle writes 1
    usb: ReceiveSetupPacket(0) -> SetAddress writes 2
    ...
    usb: dispatched 18 state Idle address 37 configuration 1
    usb: writes 6 bytes 56 zlps 2 primes 4 stalls 1 halts 0
    usb: trace descriptor 0 configuration 0 feature 0 zlp 0 overflow 0 length 0 state 0
    usb: first 12 01 00 02 00 00 00 40 50 1d 5b 61 04 01 01 02 03 01 (18 bytes)

56 bytes is 18 + 32 + 4 + 2, each truncated against the host's requested length by
the descriptor path's own arithmetic. `address 37` is 0x25, written through
`UsbDriver::set_address` only after the status stage completed, which is
smolusb's `State::SetAddress` doing its job. The first write is the device
descriptor with Cynthion's own 1d50:615b. The vendor request was handed back
rather than swallowed, which is where moondancer's `handle_vendor_request`
begins.

**The two dispatchers produce byte-identical output**, 24 lines each. That is the
check worth having: it says the dispatcher was swapped and the behaviour was not.
20 consecutive runs, no failures.

### Two bugs the run found, both mine

* **A race between two channels.** The report frame had a flag of its own while
  events had a queue, so the handler could set the flag while the consumer was
  past its drain and the summary printed with the last event still queued. It
  read as `dispatched 17` on one run in sixteen. Fixed by putting the report
  through the same queue — one ordered channel instead of two unordered ones.
* **A summary that could precede the line it summarised.** Same shape, fixed by
  draining the journal again after seeing the report.

Neither is in the ported code. Both are the kind of thing that only a run finds,
which is the argument for having done this at all.

## 4. What it costs, measured

`scripts/soc_usb_probe.py`, `opt-level = "z"`, LTO, riscv32imac. Both binaries
run the same state machine over the same events with the same queue and the same
descriptor tables, so the difference is the dispatcher.

| build | `.text` | `.rodata` | `.bss` | `.uninit` | RAM |
|---|---|---|---|---|---|
| usb, superloop | 7,952 | 1,080 | 940 | 0 | 940 |
| **usb, RTIC** | **9,520** | 1,080 | 956 | 1,316 | **2,272** |
| skeleton, cooperative | 984 | 168 | 8 | 0 | 8 |
| skeleton, RTIC | 2,312 | 136 | 24 | 4 | 28 |
| the shell, for scale | 41,400 | 17,064 | 9,656 | 0 | 9,656 |

| what the dispatcher costs | `.text` | RAM | of a 4 KiB I-cache |
|---|---|---|---|
| **with moondancer's control path in the tasks** | **+1,568** | +1,332 | **38.3%** |
| with a counter in the tasks | +1,328 | +20 | 32.4% |

**The headline: the real workload did not make RTIC cheaper.** +1,568 bytes
against +1,328 for the counter skeleton — the runtime grew slightly, because
there is now a `lock` with three resources in it rather than one. The 38% figure
in `docs/soc-concurrency-models.md` survives contact with the workload it was
accused of not modelling.

### `.uninit`, which the earlier tables missed

RTIC puts every `#[shared]` and `#[local]` resource in `.uninit`, not `.bss`,
because `#[init]`'s return value initialises them and zeroing them first would be
waste. The earlier probes reported `.bss` only and so recorded RTIC's RAM cost as
+16 bytes when it is +1,332 — the `Control<1024>` receive buffer, which the
superloop keeps as a local in `main` and therefore on the stack.

This is a transfer, not a new cost: the same 1,316 bytes exist either way. It
matters because `memory.x` reserves an 8 KiB stack floor and calls it "a floor,
not a measurement", so moving a kilobyte out of the stack and into statically
reserved RAM is a change in which budget is being spent, in the direction of the
one that is measured.

## 5. What RTIC did not take over

**The event queue survives adoption, and this is the substantive finding.**

`usb::EventQueue` is a hand-rolled SPSC ring whose correctness is an argument in
a comment. Replacing exactly that kind of argument with a compile-time ceiling is
the case `docs/rtic-adoption.md` §5 makes for RTIC. It cannot do it here: the
producer is the PLIC front end, and **the PLIC front end cannot be an RTIC
task** — `binds =` names a SLIC source, so no RTIC task can bind a hardware
interrupt on this machine. Anything a hardware handler produces reaches RTIC
through something RTIC does not check.

So the split is:

* **Checked by RTIC**: `control`, `endpoints`, `vendor_requests`, shared between
  the task and `#[idle]`, ceiling computed by the compiler.
* **Not checked by anything**: the queue between the handler and the task, and
  the frame accumulator inside the handler.

In the superloop binary those same three values are locals in `main`, and the
argument for why the handler cannot touch them is that it has no reference to
them. That is also a real argument, and it is enforced by the compiler too — just
by ownership rather than by ceiling analysis.

## 6. Shims needed this time: none

The five obstacles `docs/rtic-adoption.md` §3 records — `peripherals = false`,
the hand-written `device` module, the `CoreInterrupt` alias, `use super::device`
inside the app module, and `PROVIDE(_ebss = __ebss)` in both linker scripts —
were all that was needed. **Putting real work in the tasks needed no sixth.**
Both binaries built first try.

The one thing worth noting is that `#[idle]` and the task both wanting three
resources is spelled `(&mut a, &mut b, &mut c).lock(|a, b, c| ...)`, which is
documented and works.

## 7. The shipping image

Untouched, and now checked rather than asserted:
`scripts/soc_feature_isolation_check.py`.

* All 8 `src/bin/*.rs` targets have a `[[bin]]` entry with `required-features`,
  so cargo's auto-discovery cannot put one in the default build.
* `src/main.rs` declares neither `mod usb` nor `mod usb_report`, so neither is
  compiled into the shell.
* The shell's code is identical with `models`, `rtic`, `usbport` and all three
  together on and off: 36,136 bytes of `.text`, 12,238 instructions, same
  fingerprint.
* `./dev.py test` passes with the feature off — 97 checks.

### Why that check compares a fingerprint and not a hash of `.text`

A hash of `.text` says the features DO change the shell, and that answer is
wrong. `-C metadata` includes the enabled feature set, so the crate
disambiguator in every mangled symbol changes, so symbols sort differently, so
every PC-relative call encodes a different displacement. Measured with
`--features models`, which gates nothing the shell compiles: `.text` stayed at
exactly 36,136 bytes and 12,238 instructions, and every one of the 1,432
differing lines was the same instruction calling the same symbol under a
different disambiguator, plus six calls whose target had moved from a positive
displacement to a negative one.

The check therefore normalises addresses, disambiguators and displacement
direction, and fingerprints the sorted result. Reordering is invisible; an added,
removed or altered instruction is not.

## 8. Would I ship it

**Not yet, and not because of anything the port found.** The port worked. What
is missing is unchanged from `docs/rtic-adoption.md` §9:

* **No monotonic** — *and the reason given here was wrong.*
  `rtic-monotonics` 2.2.1 has two RISC-V backends (`esp32c3`, `esp32c6`); what it
  lacks is a CLINT one, which is five methods.
  [`rtic-workload-port.md`](rtic-workload-port.md) §7 has it written and
  measured. This port still covers the event half only.
* **Nothing has run on the board.** Every figure here is a build result or a QEMU
  run. The I-cache question is about *misses*, and `.text` bytes are a proxy for
  it, not a measurement of it.
* **The crate still needs a `[lib]`.** Both binaries reach `usb.rs`, `plic.rs`
  and `target.rs` by `#[path]`, which is fine for a spike and wrong for the
  product.
* **The real driver does not exist and cannot**, on this gateware. Everything
  downstream of `UsbDriver` is proven; everything upstream of it is not, because
  there is no peripheral to prove it against.

### The case against, stated plainly

On the evidence here, RTIC buys **one** thing this firmware does not already
have: a compile-time ceiling on the three values shared between the task and
idle. It costs 1,568 bytes on the hot path of a 4 KiB direct-mapped I-cache, a
dependency graph that goes from 14 packages to 32 with two copies of the `riscv`
crate, and a global critical section on every `pend` — one per event, taken from
inside the PLIC handler.

And it does **not** cover the queue, which is the piece whose correctness
argument is longest and least checked. The thing most worth having checked is the
thing RTIC structurally cannot reach, because the producer is a hardware handler
and RTIC has no hardware handlers on RISC-V.

That is not a reason to reject RTIC. It is a reason to stop describing the
trade as "compile-time correctness for cache", because measured against a real
workload it is "compile-time correctness for *some* of the shared state, for
cache". Decision 19 should say so in those terms, whichever way it goes.

## 9. What is not measured

* **Interrupt latency, for either model.** The obvious candidate was the event
  queue's high-water mark, and it is not usable: the host writes frames faster
  than either guest drains them, so what it records is when the pipe was
  scheduled. Across five runs it moved between 16 and 18 for RTIC and 14 and 18
  for the superloop, ranges overlapping, neither consistently deeper. The test
  excludes it rather than asserting whatever it read on the day.
* **Cache misses.** `.text` bytes, not misses. `./dev.py optlevel` is the tool
  that would, and it needs the board.
* **Anything on hardware.** Nothing here has been programmed.
* **The cost of `critical_section` on every pend.** `timer.rs` reports worst
  lateness and would detect it; neither binary here has a timer.
