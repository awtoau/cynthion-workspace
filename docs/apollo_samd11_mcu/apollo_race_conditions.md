# Apollo concurrency: what preempts what, and what the 2026-05 review got wrong

**Evidence status is marked on every claim** (#61). The previous revision of this
file described three race conditions as confirmed, rated them HIGH, and closed
with *"race conditions in FPGA state management are real."* Checked against
`repos/apollo/firmware/src`, none of the three exist. What does exist is a
different mechanism, in a different file, and it is already fixed.

## The concurrency model, which is the fact everything else depends on

**Confirmed.** Apollo is bare-metal. There is no RTOS, no scheduler and no
threads — `grep -rl "FreeRTOS\|pthread\|xTaskCreate\|osThread" firmware/src`
returns nothing. `main.c:109` is a single `while (1)` calling `tud_task()`, and
every USB vendor request is dispatched from inside that call, on that one stack.

**So there are exactly two contexts**: the main loop, and interrupt handlers. Any
claim of a race must name an ISR, because nothing else can preempt.

**Confirmed.** The only ISRs in the firmware are three, all in
`boards/cynthion_d11/fpga_adv.c`:

| handler | shared with the main loop |
|---|---|
| `EIC_Handler` | `edge_counter` |
| `SERCOM1_Handler` | `pattern_position`, `last_heartbeat`, `response`, `response_len`, `response_want` |
| `TC1_Handler` | `tx_bits_left` |

Every one of those variables is declared `volatile`. The other boards' handlers
(`boards/*/uart.c`) are UART plumbing and touch no FPGA state.

## The one real race, and it is already fixed

**Confirmed, and closed.** `fpga_adv_task()` reads and clears the edge counter as
a pair:

    NVIC_DisableIRQ(EIC_IRQn);
    window_edges = edge_counter;
    edge_counter = 0;
    NVIC_EnableIRQ(EIC_IRQn);

Without the mask, an edge arriving between the read and the clear is silently
dropped, under-counting the window that feeds `fpga_requesting_port()` — whose
threshold is `> 2` — and potentially missing an FPGA USB-takeover request. The
mask is present in the tree and the reasoning is in the code comment.

Note what this was **not**: not a mutex, not an atomic state machine, and not in
`fpga.c` or `vendor.c`. It was a two-statement read-modify-write against an ISR,
which is the only shape a race can take in a system with no threads.

## The three claims from the 2026-05 review

**Refuted.** Not "unverified" — checked, and the code they describe is absent.

### "Multiple USB hosts could simultaneously request TAKE_OVER"

`VENDOR_REQUEST_TAKE_OVER` does not appear anywhere in the firmware; the quoted
`case` is not in `vendor.c`. The request set is `VENDOR_REQUEST_JTAG_*`, plus the
console and mode requests.

The premise fails independently of the code: **Apollo is a USB device**, and a
device has exactly one host. Two hosts cannot address one device port
simultaneously, so no amount of locking is relevant to the scenario described.

### "Another thread could call `fpga_set_state()` mid-transition"

`fpga_set_state(int state)` does not exist. The API in `fpga.c` is
`fpga_set_online(bool)` / `fpga_is_online()`, over a `volatile bool fpga_online`.
And there are no threads — see the model above. Both halves of the claim fail.

### "USB disconnect during SPI transfer"

The **TODO is real**: `debug_spi.c:138` still reads
`// TODO: don't run this on r0.2+ boards?` above an unconditional
`uart_release_pinmux()`. That is an open question and worth answering.

It is **not a race condition**. It is a board-revision conditional that was never
resolved, filed under the wrong heading, which is how it inherited a severity
rating it had not earned.

## Why the review reached those conclusions

It reasoned from a general concurrency checklist rather than from this firmware:
mutexes, atomic compare-and-set, and multi-host contention are the right
questions to ask of a threaded system, and each was asked here without first
establishing that there are threads. The proposed fixes — `mutex_acquire`,
`atomic_compare_and_set`, a `MODE_JTAG_PROGRAM` state machine — would compile
against an RTOS this firmware does not have, on a part with
[single-digit flash headroom](../chips/samd11-apollo.md).

The one defect that was real did not appear on the checklist, because it is
specific to this design: a counter shared with an edge-triggered ISR.

**The prior recommendation — *"implement mutex protection before any other FPGA
state management changes"* — is withdrawn.** There is nothing for a mutex to
protect.

## What remains open

* **`debug_spi.c:138`** — decide whether `uart_release_pinmux()` should run on
  r0.2+ and delete the TODO either way. Needs an r0.2 board or a schematic answer.
* **The mode-exclusivity design** in
  [`apollo_serial_interface_and_mode_exclusivity_design.md`](apollo_serial_interface_and_mode_exclusivity_design.md)
  is worth having on its own merits — JTAG and serial genuinely contend for pins
  (#182) — but it is a **pin-multiplexing** problem, not a concurrency one, and
  should not be justified by the races above.
