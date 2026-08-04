# The FPGA_ADV sideband: retiring EIC, attention signalling, and a console

Three questions asked of one wire. The short answers: **EIC has one job, not two, and
it can go**; **a bare attention pulse is implementable and buys nothing the framed
advertisement does not already give**; **a console fits the bandwidth and fits neither
the flash nor the port-ownership mechanism**.

**Retired 2026-08-05.** Superseded by
[`docs/sideband.md`](../../docs/sideband.md), which is the canonical reference; this
file is kept for the flash-budget arithmetic and the per-option evaluation behind #95
and #182. The two documents it indexed are retired alongside it:
[`fpga-adv-sideband.md`](fpga-adv-sideband.md),
[`sideband-soak-results.md`](sideband-soak-results.md). Chip notes remain at
[`docs/chips/samd11-apollo.md`](../../docs/chips/samd11-apollo.md).

Issues: #95 (retire EIC), #88 (drive mode, closed), #176 (verb channel, rescoped),
#87 / #86 / #84 (payloads), #64 / #68 (origin), #73 (flash).

**Measured** below means read out of source at the cited line, or out of
`repos/apollo/firmware/_build/cynthion_d11/firmware.elf` (built 2026-08-03 22:06:28,
same minute as `fpga_adv.c`, so it matches the source read here). **Inferred** means
arithmetic over measured quantities. Anything else says *not determinable from
source*. Apollo paths are relative to `repos/apollo/firmware/src/`.

## 1. The EIC's two jobs — and there is only one

The premise that EIC "was and is needed to force Apollo to switch the control port"
does not survive reading the handler. **EIC does not force anything.** Measured:

```c
void EIC_Handler(void) {                          // fpga_adv.c:754-760
  EIC->INTFLAG.reg = EIC_INTFLAG_EXTINT(1 << 7);
  edge_counter++;
}
```

That is the whole interrupt. It increments a counter and returns. The counter is
sampled into a window every 200 ms by `fpga_adv_task()` (`fpga_adv.c:682-697`), and
the window feeds one predicate:

```c
return window_edges > 2;                          // fpga_adv.c:743
```

The **handoff itself is polled from the main loop**, not driven by the interrupt:

```c
if (fpga_requesting_port() == false) take_over_usb();   // fpga_adv.c:703-707
else if (fpga_usb_allowed)           hand_off_usb();
```

and `fpga_adv_task()` is called on every pass of an unthrottled `while(1)` running at
roughly 200 kHz (`main.c:165`, `main.c:133-136`). So the port switch is *already* a
polled decision over a piece of state. EIC supplies that state; it does not supply
urgency, an interrupt path to `take_over_usb()`, or anything a poll cannot.

**Separated precisely:**

| use | mechanism | replaceable by a poll? |
|---|---|---|
| data — "does the FPGA want the port" | `edge_counter` → `window_edges > 2` | yes, and the consumer is already polled |
| attention — force Apollo to act | **does not exist** | n/a |

The attention job is real, but it is performed by the *advertisement*, not by EIC.
In UART mode that is an unsolicited 4-byte frame from `SidebandAdvertiser`
(`ecp5-test/sideband_advertise.py`), timestamped in `SERCOM1_Handler`
(`fpga_adv.c:797-807`) and compared against `HEARTBEAT_TIMEOUT_MS` 300 at
`fpga_adv.c:739`. EIC's disappearance takes the *edge-counting* source of that state
and nothing else.

**What breaks if EIC goes entirely.** Any gateware that pulses FPGA_ADV but does not
emit the framed pattern loses the port. Concretely, the shipping facedancer
bitstream: `PatternUartStreamer.__init__` defaults to `baud_rate=1_000_000`
(`repos/cynthion/cynthion/python/src/gateware/facedancer/advertiser.py:69`) against
Apollo's SERCOM1 at `ADV_UART_BAUD` 230400 (`fpga_adv.c:81`). A 1 Mbaud frame is not
decodable by a receiver at 230400, so under UART mode that board never advertises.

**This means the breaking change is flipping the default, not deleting EIC.** #95
files them as one item; they are not. Deleting `EIC_Handler` from firmware that is
already in UART mode changes nothing observable.

### #95's three options, evaluated

**(a) Keep EIC reachable via the mode API.** Works, but note what "reachable" means:
`fpga_adv_set_mode()` has exactly one caller, `vendor.c:428`, reached from a host USB
control transfer. Nothing in firmware ever selects a mode. So (a) means a board's
advertisement mechanism is decided by whether a host happened to issue vendor request
`0xC3` — which no shipping host tool does. The mechanism stays live and stays
undiscoverable.

**(b) Automatic fallback if no valid frame appears.** *This one is unsafe, for two
reasons in the source.*

First, **the trigger fires on a healthy board.** `SidebandControl`'s `advertise` bit
resets to 0 and does so deliberately — `ecp5-test/riscv/sideband_csr.py:54-60`
inverts upstream's polarity precisely so a bitstream cannot seize CONTROL on
configuration. A correct, running SoC that has not asked for the port emits no frames
at all. "No valid frame in a window" is therefore the *normal* state, and (b) would
fall back to EIC on every boot.

Second, **the fallback is one-way.** `fpga_adv_command()` refuses unless the mode is
UART:

```c
if (adv_mode != FPGA_ADV_MODE_UART || length > ADV_RESPONSE_MAX) return 0;  // :437
```

Once fallen back, nothing in firmware can issue the command that would prove the
responder is there, so nothing can decide to switch back. Only an external host
request can. A transient fault becomes permanent degradation.

**(c) Drop EIC entirely.** Correct destination, wrong first step. It requires that
something else supplies the port request on every board, which today it does not.

## 2. Attention signalling on an open-drain wire

**Contention is already resolved and is not the obstacle.** Both ends have been
open-drain since #88: the responder never drives high (`ecp5-test/sideband_link.py:293-296`,
`pad_o.eq(0)` with `pad_oe.eq(tx_active & ~tx)`), and Apollo emulates open-drain by
toggling DIR only, with OUT parked low once per byte (`fpga_adv.c:130-139`, `:838-842`).
Two open-drain drivers pulling low together is a low. So the answer to "what happens
when Apollo transmits while the FPGA holds the line low" is: **Apollo's mark bits
become spaces, the responder's stop-bit gate rejects the frame
(`sideband_link.py:276-282`), no reply comes, Apollo times out and may retry.** Data
is lost; nothing is damaged.

**Can Apollo detect an attention assertion while its SERCOM owns the pin?** Yes, and
it nearly does already. The receiver is SERCOM1 PAD3, RX-only, 16× oversampled
(`fpga_adv.c:265-291`). A line held low across a character time frames as `0x00` with
FERR, and `SERCOM1_Handler` already tests for it:

```c
bool corrupt = (SERCOM1->USART.STATUS.reg &
                (SERCOM_USART_STATUS_FERR | SERCOM_USART_STATUS_BUFOVF |
                 SERCOM_USART_STATUS_PERR)) != 0;                  // fpga_adv.c:773-785
```

and then discards it. Setting a flag there is a handful of bytes.

**Is a short pulse distinguishable from a start bit?** No, and the window has no
usable middle:

| low duration at 230400 | what the SERCOM does |
|---|---|
| < ~2.2 µs (half a bit) | below the oversampler's threshold — **not registered at all** |
| ~4.3 µs (one bit) | indistinguishable from a start bit; frames a data byte |
| ≥ 43.4 µs (one character) | frames `0x00` + FERR — reliably detected |

So the only reliable attention assertion is a **break of at least one character
time**, which is not a "short pulse" — it is a deliberate framing violation. That is
detectable, but it aliases with the exact condition the CRC exists to catch: FERR
also means "a byte was corrupted". Disambiguating costs a rule such as *N consecutive
FERRs* or *a break longer than two character times*, and each such rule trades away
some of the "one bit error must not be misread" property that
`HEARTBEAT_TIMEOUT_MS` 300 was chosen for (`fpga_adv.c:62-65`).

**Implementable on this hardware: yes.** Cost is roughly a flag in the existing
corrupt branch on Apollo, and a low-assert FSM on the FPGA gated exactly like
`SidebandAdvertiser` — `hold` on `link.tx_active` and the 20-bit idle guard
(`sideband_advertise.py:107-116`, `:179-181`) — so it cannot land inside a reply.

**But it should not be built**, because the framed advertisement is the same
mechanism done better. Compare:

| | break-as-attention | `SidebandAdvertiser` frame |
|---|---|---|
| unsolicited FPGA→Apollo | yes | yes |
| detected without polling | yes | yes |
| aliases with line corruption | **yes** | no — 4-byte pattern |
| carries a reason | no | can, with a type byte |
| duty on the wire | ≥ 43.4 µs per assertion | 174 µs / 100 ms = 0.17% |

A separate attention layer adds a second, weaker signalling scheme to a wire that
already has one. If more unsolicited signals are wanted, widen the advertisement
frame's alphabet.

**The "cheap I am alive" already exists** and needs nothing new: status bit 6 toggles
on every reply (`sideband_link.py:438`), which is stronger than a repeated value
because a wedged FSM replaying a stale buffer would not change it, and `PING` returns
the CPU's own `tx` byte (`sideband_csr.py:182`). Liveness needs a poll, not a
mechanism — and Apollo has no poll (see §3).

## 3. A console over the sideband — the arithmetic

### The turnaround is not the problem

Exchange time is `(1 + N + 2) × 43.403 µs + 40 µs` for an N-byte payload: one command
byte out, the fixed 40 µs turnaround (`sideband_link.py:214`), then status + payload +
CRC back. A byte is 43.403 µs at 230400 baud. **Inferred, from measured constants:**

| N | exchange | payload rate | of the 23,040 B/s wire ceiling | turnaround's share |
|---|---|---|---|---|
| 1 | 213.6 µs | 4,681 B/s | 20.3% | 18.7% |
| 8 | 517.4 µs | 15,461 B/s | 67.1% | 7.7% |
| 32 | 1,559.1 µs | 20,525 B/s | 89.1% | 2.6% |
| 64 | 2,948.0 µs | 21,709 B/s | 94.2% | 1.4% |
| 128 | 5,725.8 µs | 22,355 B/s | 97.0% | 0.7% |

Read as console rate, N=8 is already 154 kbaud-equivalent and N=64 is 217 k. The
Apollo↔SoC console that exists today runs at `APOLLO_UART_BAUD` 115200
(`ecp5-test/riscv/vexii_hello_soc.py:280`), i.e. 11,520 B/s. **Bandwidth is
sufficient from N=8 upward.** The 40 µs amortises away exactly as expected; it is
never the binding term.

The rate cannot rise above 230400 and that is hardware, not choice: `TXPO` selects
only PAD0 or PAD2 and PA09 is PAD3 on both SERCOM0 and SERCOM1
(`fpga_adv.c:370-376`), so Apollo's transmit is bit-banged one bit per TC1 overflow.
Measured 100/100 at 230400, **1/100 at 460800 with real CRC corruption**
(`fpga_adv.c:67-81`). 23,040 B/s is the ceiling on this board.

### What the protocol change would be

`PAYLOAD_SIZE` is opcode-keyed with no length field (`sideband_link.py:84-87`), and
the docstring is explicit about why: the responder sets `payload_len`, `byte_index`
and `turnaround` from `rx_uart.data` in `IDLE` and returns to `IDLE`, so **there is no
half-parsed condition to abandon and therefore no timeout on the FPGA side**
(`sideband_link.py:18-21`, `:334-364`).

That property survives a console. It does not require a length field:

* Allocate an opcode range as a read family — `0x10`–`0x1F` returning 1..16 bytes,
  say. Frame size is still known from byte one. §11 of the protocol doc already
  prescribes this ("encode the length in the command byte's high bits").
* Put the *valid* count in the first payload byte, and pad the rest. A short FIFO
  still produces a fixed-length frame.
* Carry "bytes waiting" in the status byte, so every poll is also a queue-depth
  report — the same reasoning that already puts health in the status byte.

What genuinely breaks the property is a multi-byte *command*, and only that. It is
avoidable here.

### Flow control and overrun, and where the 16550 actually is

**The sideband is not behind a 16550.** The SoC's two 16550s are the USB CDC console
and the Apollo-facing R14/T14 port (`vexii_hello_soc.py:694-695`). `overrun` and
`frame_error` are driven only for the latter, from the async line:

```python
apollo_uart.overrun.eq(apollo_line.overrun),        # vexii_hello_soc.py:1266-1267
apollo_uart.frame_error.eq(apollo_line.frame_error),
```

The sideband's CPU end is `SidebandControl`, four CSR bytes, and it has **no FIFO in
either direction and no overrun output at all**: `message` is one byte straight from
the `tx` register (`sideband_csr.py:182`), and `rx` is one latched byte with `rxcnt`
as the only evidence a byte was missed (`sideband_csr.py:187-194`). LSR.OE does not
reach this path and cannot without new gateware.

Given that, the flow-control answer splits by direction:

* **FPGA→Apollo is inherently flow-controlled** and this is the one genuine advantage
  of polled half-duplex: nothing moves unless Apollo asks, so Apollo can never be
  overrun. The cost moves to the FPGA, where a console FIFO must absorb a burst
  between polls. A panic backtrace at the SoC's full rate against Apollo's 20 ms
  housekeeping tick (`main.c:59`) implies ~440 bytes of FIFO — cheap in block RAM,
  and the FIFO's own overflow flag would then be the LSR.OE equivalent that has to be
  added.
* **Apollo→FPGA is unprotected and silently lossy today.** A `0x80`–`0xFF` WRITE
  strobes `received` for one cycle (`sideband_link.py:351-355`); if the CPU has not
  read `rx` before the next WRITE, the first byte is gone and only `rxcnt` shows it.
  Seven bits per exchange at 170.2 µs is 5,875 B/s of 7-bit values, or ~2,900 B/s if
  full bytes are split across two exchanges. Fine for typing; poor for paste.

### Why it still fails

**(i) It does not fit.** See §4.

**(ii) It starves the port request.** `SidebandAdvertiser` may only start a frame
after `guard_bits` = 20 bit periods of *continuous* idle-high, where
`line_busy = ~rx | hold` (`sideband_advertise.py:151-154`, `:179-181`). Twenty bit
periods is 86.8 µs. Apollo's `fpga_adv_command()` busy-waits for the whole exchange
(`fpga_adv.c:525-532`), so back-to-back polling leaves inter-exchange gaps far shorter
than that — the guard resets on every command's start bit and never fills. Three
missed frames is 300 ms, which is `HEARTBEAT_TIMEOUT_MS`, so `fpga_requesting_port()`
goes false (`fpga_adv.c:739`) and the next `fpga_adv_task()` pass calls
`take_over_usb()`. **Driving the console hard makes the FPGA lose the CONTROL port.**

There is a second path to the same place: `SERCOM1_Handler` routes *every* byte into
the response collector while `response_want != 0` (`fpga_adv.c:790-795`), so any
advertisement frame that does begin during a poll is consumed as response bytes and
never reaches the matcher.

This is structural, not a tuning problem: the bulk data and the port-ownership signal
share one half-duplex wire, and one of them is gated on the wire being idle.

**(iii) It duplicates a better link that already exists.** The SoC already has a full
NS16550A console to Apollo on R14/T14 at 115200, full duplex, hardware UART at both
ends, with LSR.OE and LSR.FE actually wired (`vexii_hello_soc.py:1214-1275`). Apollo
tunnels it to the host CDC (`console.c:86-134`). The sideband's only advantage over
it is surviving a JTAG session — and R14/T14 are TDI/TMS, so a JTAG session is
precisely when it is unavailable (`chips/samd11-apollo.md`, §"Pin sharing"). The
console-over-sideband proposal is therefore not "a console"; it is "a console during
JTAG", which is a much smaller thing than the effort implies.

## 4. Flash budget

**Measured, from `firmware.elf` and `tmp/logs/apollo_budget_check.log`:**

| | used | limit | enforced ceiling | headroom to the ceiling |
|---|---|---|---|---|
| flash | 13,608 B | 14,336 B | 95% = 13,619 B | **11 bytes** |
| RAM | 3,472 B | 4,096 B | 85% = 3,481 B | **9 bytes** |

That is the answer to §4 on its own. `scripts/apollo_budget_check.py` gates at those
ceilings, so **no addition of any size passes today.** LTO is already on and
load-bearing — it reclaims 2,968 B (`chips/samd11-apollo.md`) — and `-Os` is already
in effect (#73).

**What removing EIC saves. Measured where separable:**

| symbol | size |
|---|---|
| `EIC_Handler` | 24 B text |
| `edge_counter`, `window_edges`, `last_update` | 12 B bss |
| `adv_mode` | 1 B bss |

**Not separable under LTO**, so *not determinable from source*: `fpga_adv_init_eic()`
is inlined into `Reset_Handler` (1,848 B, which is most of `main()` too), the window
block is inlined into the main loop, and the EIC branch of the mode switch is inside
`fpga_adv_set_mode.part.0` (512 B for both branches plus the teardown). Upper bound
24 + 512 + 13 = 549 B if the mode concept goes with it; the UART init is the larger
half of that 512, so the realistic figure is **inferred at 150–400 B**. Measuring it
means building with the code removed, which this review did not do.

**What a console costs on the Apollo side.** The reply path is capped at
`ADV_RESPONSE_MAX` 18 (`fpga_adv.c:143`), with `response[18]` and `vendor.c`'s
`reply[18]` both measured at 18 B bss. N=64 reads need both at 64, i.e. **+92 B RAM**
before any ring buffer. Surfacing it as a second serial device needs `CFG_TUD_CDC 2`
(`tusb_config.h:69`); the existing single interface measures `_cdcd_itf` at **296 B
bss**, so a second is +296 B plus descriptor growth on `desc_configuration` (93 B
today). That is 388 B of RAM against 624 B physically free and 9 B free under the
gate, before the packet layer, before a console ring, and before the FIFO-available
parsing.

**Plainly: the protocol does not fit.** And it will not be made to fit by shrinking
`fpga_adv.c` — the bytes are in TinyUSB (`tud_task_ext.part.0` 1,916 B) and in
`handle_vendor_request_setup.isra.0` (1,932 B), which is where #73 already points.

## 5. What nothing tests

`scripts/sideband_link_sim.py` checks the responder at the pad — 20 checks, all on
`SidebandLink` **alone**. `scripts/sideband_advertise_sim.py` checks
`SidebandAdvertiser` **alone**, 16 checks. Both are in `soc_sims.py`'s list
(`:65-66`). Neither covers what the proposals need:

* **The pad-sharing is untested.** `sideband_debug.py:159-160` OR-s two modules'
  `pad_o`/`pad_oe` and gates `link.rx` on `advertiser.tx_active` (`:143`). Nothing
  elaborates `SidebandDebug` in simulation. The `hold` and idle-guard interaction is
  tested against a *stimulus* that mimics a responder, not against a responder.
* **Back-to-back polling is unsimulated**, so §3's guard-starvation interaction has
  never been observed in either simulation or hardware. Any console or polled-status
  work needs a combined sim that models Apollo's poll duty and asserts the
  advertisement still gets out.
* **Apollo's C has no automated coverage at all.** `repos/apollo/firmware/test/`
  contains one file, `test_apollo_mode.c`. The master, the timeout derivation, the
  drain-before-arm, the collector, the pattern matcher and the mode switch — every
  bug this subsystem has had — are untested.
* **The advertisement has never run on hardware.** `fpga-adv-sideband.md` §9.1 states
  it: "no bitstream has been built with it, and nothing has yet put Apollo into UART
  mode from the host." The 5000/5000 soak covers commands, not advertisement.
* **The measurements cannot be reproduced from this checkout.**
  `scripts/sideband_soak.py` (cited by `sideband-soak-results.md:4`) and
  `scripts/sideband_contention_probe.py` (cited by `fpga_adv.c:114`) are **not in the
  tree.**
* If the port request moves into the status byte (§6), a new test is required that a
  poll *timeout* does not read as "port not wanted" — otherwise a busy Apollo revokes
  the port, which edge counting never did.

## 6. Recommendations

### Q1 — retire EIC edge-counting. **Yes, as the last of four steps.**

Adopt #95 option **(c)**, reject **(b)** outright (§1: it triggers on healthy boards
and is one-way), and treat **(a)** as the transitional state rather than the answer.

Order matters, because Apollo has no poll today — `fpga_adv_command()` has exactly
one caller, `vendor.c:422`, reached from a host control transfer:

1. **Give Apollo a periodic poll.** #176's measurement: 170 µs per STATUS exchange,
   so 100 Hz is ~1.7% of the main loop. This is the piece that does not exist.
2. **Move the port request into the status byte.** Bit 7 is reserved and transmits
   zero (`sideband_link.py:308`); `SidebandControl` already has `advertise` as bit 5
   of `ctrl` (`sideband_csr.py:96`) and currently routes it only to the advertiser.
   Then `fpga_requesting_port()` becomes "the last good poll said so, within a
   timeout".
3. **Retire the unsolicited advertisement frame** once (2) is in. This is what
   removes the idle-guard starvation in §3 and restores "the FPGA never transmits
   unasked", the property that made the link collision-free.
4. **Delete `EIC_Handler`, the counters, and the mode switch.**

Steps 2–3 are the substantive change; step 4 is bookkeeping. **Do not flip the
power-on default before step 2**, because flipping it — not deleting EIC — is what
breaks the facedancer bitstream, whose advertiser is 1 Mbaud (§1).

The trade being accepted: a polled port request cannot be delivered while Apollo is
not polling (a long JTAG session, a wedged main loop), where an unsolicited frame
could. That is a real regression and should be stated in the change, not discovered.

### Q2 — a two-way attention signal. **No.**

Implementable, electrically safe, and cheap — and it duplicates the advertisement
with a weaker signal that aliases with line corruption (§2). The only reliable
assertion is a break of at least one character time, which is already visible to
`SERCOM1_Handler`'s corrupt branch and already discarded there. If unsolicited FPGA→
Apollo signalling beyond "I want the port" is wanted, add a type byte to
`SidebandAdvertiser`'s frame; it already has the collision discipline (`hold`, the
20-bit guard) that a new mechanism would have to reinvent.

Note the tension with Q1: Q1 recommends removing the unsolicited frame, Q2 recommends
that any future unsolicited signal reuse it. Those are consistent only if the
question "does the FPGA ever need to interrupt Apollo, or is a 10 ms poll latency
acceptable" is answered first. **That is the one design decision this review cannot
make from source** — it depends on what a supervisor is expected to react to, and
`fpga_adv_task()`'s existing behaviour (200 ms EIC window, 300 ms UART timeout)
suggests the answer has always been "poll latency is fine".

### Q3 — a full console over the sideband. **No.**

Not on bandwidth — N=8 already beats the existing 115200 console, and the 40 µs
turnaround amortises to under 1% by N=64 (§3). It fails on flash (11 bytes under the
gate, §4), on port ownership (a saturated poll starves the advertisement and Apollo
takes the CONTROL port, §3), and on redundancy (a full 16550 console to Apollo
already exists on R14/T14 with real overrun reporting).

**Build the smaller thing that answers the actual question.** If the goal is "see why
it died while JTAG owns TDI/TMS", that is a fixed-length **log-tail read**, not a
console: an opcode family returning the last N bytes of an SoC-side ring, valid count
in payload byte 0, no CDC on Apollo, surfaced through the vendor path that already
exists (`vendor.c:416-426`). It keeps the stateless property, needs no length field,
needs no second CDC interface, and it is available in exactly the situation the
console is not.

## 7. Found while reading, outside the three questions

Each is read from source and **not verified by build or on hardware.**

1. **The facedancer advertisement may never reach the wire.**
   `ApolloAdvertiserProvider` drives `int_pin.o` and never `int_pin.oe`
   (`luna_soc/gateware/provider/cynthion.py:198-200`), while the platform declares
   `Resource("int", 0, Pins("T6", dir="io"), ...)`
   (`repos/cynthion/.../platform/cynthion_r1_4.py:102`). An undriven `oe` is 0, so the
   pad is never driven and the advertisement is tri-stated. If so, EIC mode is already
   non-functional on that bitstream and the compatibility concern in §1 is moot —
   worth a build and a scope before either conclusion is relied on.
2. **Same path, push-pull semantics.** That provider assigns `.o` from the UART's
   `tx`, so if `oe` were ever asserted it would drive high against Apollo's
   open-drain pull-low — #88's hazard, still live in the facedancer path even though
   #88 is closed on the workspace responder and on Apollo.
3. **The protocol document contradicts the shipping code.**
   `apollo_samd11_mcu/fpga-adv-sideband.md` §1 (`:37`, `:54`, `:68`) and §11 (`:462`)
   argue for push-pull and against open-drain; open-drain shipped, and the soak
   refuted the rate argument. #88's closing comment says the same and it has not been
   picked up.
4. Same document, §3.3 cites `fpga_adv_transceive()` at `fpga_adv.c:437`. The
   function is `fpga_adv_command()` at `:418`; `:437` is the mode guard.
5. **#176's "fixed separately" has not landed here.**
   `ecp5-test/riscv/vexii_hello_soc.py:1286` still passes `SYNC_MHZ * 1e6` to
   `SidebandDebug`, where `GatewareId` at `:772` uses `car.actual_sync_mhz`. Harmless
   at 60 MHz; silent at any frequency the PLL cannot meet exactly.
