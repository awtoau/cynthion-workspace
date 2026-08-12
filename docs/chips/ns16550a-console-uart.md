# NS16550A — the console UART, in fabric

The register map both of this SoC's consoles answer on. Not a chip: it is
`gateware/soc/peripherals/uart16550.py`, instantiated twice, and it is here with the chip
notes because a driver author needs the same thing from it that they need from a
part — which reads change state, and what they change.

**Index:** [`../hardware.md`](../hardware.md)

| | |
|---|---|
| Source | [`../../gateware/soc/peripherals/uart16550.py`](../../gateware/soc/peripherals/uart16550.py) |
| Driver | `firmware/cynthion-soc/src/uart.rs`, one type for both instances and for QEMU |
| Instances | index 0, USB CDC-ACM on AUX; index 1, async serial on R14/T14 to Apollo |
| Base addresses | `cynthion_soc_pac::base`, generated from the SoC's own memory map — **not written down here** |
| Simulation | `scripts/uart16550_sim.py` (35 assertions) |
| Reference | QEMU's `-M virt` `ns16550a`, which the same driver is tested against |

The offsets below are the NS16550A's own, fixed by the part being copied rather
than by our decoder. What can drift — where the peripheral sits — is generated,
and [`../hardware.md`](../hardware.md) explains why that is never transcribed.

## Performance

Structure per [`../plans/performance-sections.md`](../plans/performance-sections.md);
cross-cut against every other bus in [`bus-speed-audit.md`](bus-speed-audit.md).

**There is no baud ceiling here, because there is no baud generator.** DLL and
DLM are stored and ignored — they exist so a generic driver's setup sequence
succeeds, not so a rate can be set. Asking "what baud does the console run at" is
asking the wrong peripheral; the answer belongs to whichever transport the
instantiator wired behind it, and the two instances have completely different
ones.

### 1. Theoretical maximum

**The peripheral itself.** Bytes cross on `sync`-clocked stream ports, one per
cycle, so the 16550 could pass **60 MB/s** at `SYNC_MHZ = 60`. Nothing on this
board comes within three orders of magnitude of that, and it is recorded only to
say that the fabric is never the constraint.

| instance | transport | its own ceiling |
|---|---|---|
| **0 — console** | USB CDC-ACM on the AUX PHY | the USB bulk path: 53.2 MB/s protocol maximum, 48.5 MB/s measured on this board |
| **1 — Apollo** | `serial_line.py` on R14/T14 | 115200 8N1 = **11.52 kB/s**, and the pins are JTAG's |

### 2. Achievable on this board

**Instance 0 is packet-rate bound, not byte-rate bound, and that is a deliberate
trade.** The endpoint asserts `serial.tx.last` on every byte
([`../../gateware/soc/top.py`](../../gateware/soc/top.py)), so **one byte leaves
per USB packet**. The reason is latency: a console emits a line and goes quiet,
so waiting to fill a 512-byte packet would hold the banner indefinitely.

The arithmetic of what that costs, from the USB 2.0 microframe:

    13 bulk transactions per 125 us microframe   (the protocol maximum)
    x 8000 microframes per second
    x 1 byte per packet
    = 104 kB/s ceiling

against **53.2 MB/s** if the same endpoint sent full packets — a factor of 512,
spent on interactive latency. This is a rate chosen for a reason other than what
the transport supports, which is exactly the shape
[`bus-speed-audit.md`](bus-speed-audit.md) looks for; the difference is that the
reason is written down, correct, and about a property the byte rate cannot
express.

**Instance 1 is not a rate decision at all.** 115200 matches what the SAMD11
opens ([`console.c`](../../repos/apollo/firmware/src/console.c)) and what every
terminal expects on an Apollo tty. A SERCOM off 48 MHz would reach several
megabaud. What stops it is that **R14/T14 are the ECP5's JTAG TDI/TMS pads** and
PA10/11/14/15 on the MCU are shared three ways — so the mitigation for the link
is a policy of never transmitting unbidden, not a faster wire. See
[`samd11-apollo.md`](samd11-apollo.md).

**What we configure.** The divisor is computed at elaboration from the PLL's
*solved* rate rather than the requested one:

    divisor = int(car.actual_sync_mhz * 1e6 // APOLLO_UART_BAUD)   # = 520 at 60 MHz

which is 115384.6 baud against 115200 — **0.03% error**, where a UART tolerates
about 2%. Reading `actual_sync_mhz` rather than `SYNC_MHZ` is what keeps that
true if the PLL ever has to approximate.

FIFOs, and each is sized where the transport is chosen: console 16/16, Apollo
64 TX / 16 RX. 64 is a line of shell output, because at 115200 a byte is ~87 µs —
four orders of magnitude slower than the CPU can produce them, and without the
buffer a `help` listing would spend its whole length inside the 16550's bounded
write spin, dropping most of itself.

### 3. Measured

| path | conditions | figure | source |
|---|---|---|---|
| CDC-ACM loopback, combinational | high speed, 512-byte packets | 195.4 Mbps = 24.4 MB/s | [`../usb-performance.md`](../usb-performance.md) |
| **the console as actually built** | one byte per packet | **never measured** | nothing has ever timed a banner |
| Apollo line | 115200 8N1, divisor 520 | 11.52 kB/s by construction | — |
| overrun rate on either port | — | counted by `lost` per console, reported by `irq` | [`../hardware.md`](../hardware.md) |

The 24.4 MB/s row is the *transport* with full packets and is **not** what this
peripheral gets. It is here as the ceiling the one-byte-per-packet choice is
measured against, not as a figure for the console.

### 4. The gap, and what closes it

| rank | option | worth | cost |
|---|---|---|---|
| — | a faster baud on instance 0 | **meaningless.** There is no baud generator and no wire | — |
| 1 | pack the console TX endpoint | up to 512× on bulk output | **the banner stops appearing promptly.** A timed flush would keep both, and does not exist |
| 2 | measure what the console actually achieves | it would replace an arithmetic ceiling with a number | a byte-counted flood over the CDC path |
| — | a faster Apollo UART | **wrong lever.** The constraint is that both pins are JTAG's | — |

**Unknown:** the console's real throughput. The 104 kB/s above assumes the host
issues bulk transactions at the protocol maximum, which no CDC-ACM driver does.
Nothing has ever measured it, and until something has, "the console is slow"
remains an impression rather than a finding.

## Every read that changes state

| read | changes | what a driver must do |
|---|---|---|
| **+0 RBR**, DLAB clear | pops the receive FIFO; LSR.DR clears once it empties | check DR first. Reading an empty FIFO returns the previous byte |
| **+2 IIR** | clears the transmit-empty interrupt, and only while IIR is reporting it (id `0b001`) | read it once per interrupt, which is the standard sequence anyway |
| **+5 LSR** | clears OE, PE, FE, BI and the bit 7 summary, and the interrupt they raise | **keep the value.** A poll that discards it discards the only report of a lost byte |

Everything else — IER, LCR, MCR, MSR, SCR, and +0 with DLAB set, which is DLL —
may be read at any rate with no effect at all.

**`irq` is a level, and the interrupt controller must treat it as one.** It stays
high while the FIFO holds a byte, so an edge-triggered input loses an interrupt
whenever a second byte arrives before the first is read.

**LSR is at +5 and RBR at +0, in different 32-bit words.** That is what makes a
poll loop structurally unable to reach the data register, whatever the bus does
to the access, and it is why the standard layout was adopted. IIR at +2 *does*
share a word with RBR; what keeps them apart is that VexiiRiscv drives a
single-byte `sel` for a byte access, `amaranth_soc.csr.wishbone` strobes only the
lanes `sel` names, and the peripheral is in a `main=0` PMA region where no cache
line fill reaches it. [`../architecture.md`](../architecture.md) decision 4 records
the version that hardened against this instead, and why it was reversed.

## Registers

| offset | read | write |
|---|---|---|
| +0 | RBR, or DLL when LCR.DLAB | THR, or DLL when LCR.DLAB |
| +1 | IER, or DLM when LCR.DLAB | same |
| +2 | IIR | FCR |
| +3 | LCR — bit 7 is DLAB | same |
| +4 | MCR | same |
| +5 | LSR | — |
| +6 | MSR — constant `0xb0`, or MCR's outputs when MCR.LOOP | — |
| +7 | SCR | same |

LSR:

| bit | name | meaning here |
|---|---|---|
| 0 | DR | the receive FIFO holds a byte |
| 1 | OE | a byte was destroyed before it reached the peripheral |
| 2 | PE | parity — nothing checks parity, always 0 |
| 3 | FE | a frame arrived with no stop bit and was dropped |
| 4 | BI | break — nothing detects a break, always 0 |
| 5 | THRE | the transmit FIFO is **empty**, so a driver may write 16 bytes |
| 6 | TEMT | the transmitter is empty. No shift register, so the same condition |
| 7 | — | any of bits 1..4; derived, so it clears with them |

## Interrupts

One level-sensitive line per instance, into the PLIC.

| IIR id | condition | enabled by | cleared by |
|---|---|---|---|
| `0b011` | an LSR error bit is set | IER.ELSI | reading LSR |
| `0b010` | LSR.DR | IER.ERBFI | reading RBR until the FIFO empties |
| `0b001` | the transmit FIFO went empty | IER.ETBEI | reading IIR, or writing THR |
| `0b110` | character timeout | — | no timer, never raised |
| `0b000` | modem status | — | MSR is constant, never raised |

IIR reads `0b0001` in its low nibble when nothing is pending, and bits 7:6 mirror
FCR bit 0 so a driver's FIFO probe identifies a 16550A.

The firmware enables ERBFI and nothing else: the shell formats straight into
`Uart::put` with no transmit ring for a THRE interrupt to drain, and an overrun is
picked up by the next LSR read anyway. See `firmware/cynthion-soc/src/irq.rs`.

## Where an overrun comes from

The peripheral cannot see one. Its `sink` applies real backpressure, so a byte
offered while the receive FIFO is full is **held, not dropped** — a full FIFO here
is a stall. `overrun` and `frame_error` are inputs, driven by the transport that
can actually lose a byte:

| instance | transport | can it lose a byte? |
|---|---|---|
| 0, USB CDC | `USBStreamOutEndpoint` → `StreamBuffer` (`usb`→`sync`) | **no.** The endpoint NAKs while its buffer is full and the host retries, so neither input is driven |
| 1, Apollo | `serial_line.SerialLine` → `StreamBuffer` | **yes.** No flow control on a line: `source.valid` is one cycle whatever the sink says, and a frame with a bad stop bit is dropped. Both are reported |

It reaches a person through `firmware/cynthion-soc/src/uart.rs`, which ORs the
bits into a static as it reads LSR — the read happens inside the interrupt
handler, which may not print — and the main loop prints them on the primary
console. The `irq` shell command prints a running `lost` count per console.

## Would a stock driver work

Walked against Linux's `drivers/tty/serial/8250/8250_port.c`, which is the
strictest of the three that matter (Zephyr's `ns16550.c` and U-Boot's
`ns16550.c` are subsets of it).

| what the driver does | here |
|---|---|
| `autoconfig`: MCR = LOOP\|OUT2\|RTS, expect MSR & 0xf0 == DCD\|CTS | passes |
| `autoconfig`: FCR = 1, read IIR bits 7:6 to type the part | reads `0b11`, PORT_16550A |
| `autoconfig`: scratch register write/read | passes |
| `autoconfig_16550a`: NatSemi EXCR1 probe through LCR = 0xE0 | not misdetected |
| `serial8250_do_startup`: read LSR, RX, IIR, MSR to clear | all four behave |
| the TXEN test: IER = THRI, read LSR.TEMT, read IIR.NO_INT | reports an interrupt pending, so `UART_BUG_TXEN` is **not** flagged |
| `serial8250_do_set_termios`: DLAB, divisor, DLAB clear | stored; DLAB gates the FIFO push, so no junk is transmitted |
| `serial8250_handle_irq`: read IIR, then LSR, then act | IIR clears the transmit interrupt, LSR clears the errors |
| `serial8250_tx_chars`: write `tx_loadsz` = 16 bytes on THRE | THRE means FIFO empty, so all 16 fit |
| `serial8250_read_char`: LSR error bits into `icount.overrun` etc. | OE and FE are real; PE and BI never set |
| `__stop_tx`: clear IER.THRI when the ring empties | no storm either way, since IIR clears it |
| FCR receive trigger level, bits 7:6 | ignored — the interrupt fires on one byte, which is stronger |
| `serial8250_break_ctl`: LCR bit 6 | stored, and no break is transmitted |
| `autoconfig_irq`: MCR loopback plus a transmit interrupt | **would not work** — data loopback is absent. Only runs when the driver was not told its interrupt |

So: yes for anything registered from a devicetree or with a fixed type, which is
every path this SoC will meet, and yes through a legacy probe as far as
identifying the part. The single gap is interrupt-line discovery.

## Why 16 bytes, and not 32 or 64

| | 8250 | **16550A** | 16650 / 16750 |
|---|---|---|---|
| FIFO | none | **16 bytes, fixed** | 32 / 64, discoverable |
| driver assumption | — | every driver assumes 16 on seeing 16550A in IIR | needs a depth register, and nothing agrees about them |
| ECP5 mapping | — | 16 × 8 bits → distributed LUT RAM (`TRELLIS_DPR16X4`) | 1024 × 8 → a whole `DP16KD` |

**Depth is a constant, not a parameter.** Making it adjustable means firmware has
to discover it, and the deeper parts buy that discovery with a block RAM on a die
where block RAM is the tight resource.

## Why this is written from the spec rather than vendored

The default is to take a proven implementation and change its back end. Surveyed
for #128 against OpenCores `uart16550` (Verilog, **LGPL 2.1**, ~135 KB) and
RoaLogic `apb4_uart16550` (SystemVerilog, BSD-2, ~65 KB); ours is ~130 lines of
Amaranth on an `amaranth_soc` CSR bus. There is no Amaranth- or Migen-native
16550, so vendoring means a Verilog black box.

Four reasons it stays ours, in order of weight:

* **The back end is the surgery, and in the mature core it is not at the
  boundary.** OpenCores instantiates `uart_transmitter` and `uart_receiver`
  *inside* `uart_regs.v` and derives register semantics from their internals —
  `lsr6` reads the transmitter FSM's `tstate`, `lsr5` its `tf_count`, and PE/FE/BI
  come out of the receive FIFO as tag bits beside each byte. Cutting the bit
  engine off means editing the one file that holds every register meaning:
  modifying the proven part, which forfeits the proof. What would be inherited is
  the register file — the half that is cheap to write and cheap to assert.
* **Neither port wants a stock back end.** The console is a USB CDC byte pipe: no
  baud rate, no start or stop bits, so a stock core could only be left unmodified
  by feeding its serial pins through a serialiser and a matching deserialiser — a
  divisor and a shift register's latency invented so a module could be told it was
  a UART. The Apollo port genuinely is a serial line, but on pins shared with JTAG,
  needing an output enable held across the stop bit, an idle qualifier and a pad
  synchroniser. A stock 16550 has none of those (#113; `peripherals/serial_line.py` is the
  answer).
* **Licence.** The most-proven candidate is LGPL 2.1 against this tree's
  BSD-3-Clause.
* **The memory map would stop being generated.** A black box has no
  `amaranth_soc` memory map, so the peripheral's description goes back to being
  hand-written, losing the generated memory map
  ([`../architecture.md`](../architecture.md)).

**What was taken from the proven core instead: its behaviour, as the
specification.** `uart_regs.v` was read line by line against ours during #128 and
caught two divergences that assertions written from our own understanding would
not have — **THRE means "FIFO empty", not "FIFO has room"**, and IIR's idle
encoding. The driver is also exercised against QEMU's `ns16550a` on every run of
`scripts/soc_test.py`.

**Revisit if** a third transport appears that genuinely is an RS-232 line on
unshared pins, or if character timeout and per-character error tagging turn out to
be wanted. Both argue for the bit engine there is currently no use for.

## What this is not

  * **No baud rate, on either instance's register map.** DLL and DLM are stored,
    read back, and connected to nothing. Bit timing for the Apollo line lives in
    `peripherals/serial_line.py`, where the wire was chosen.
  * **No modem pins.** MCR is stored and ignored; MSR reads a constant `0xb0`,
    "ready", so a driver waiting on CTS terminates. The exception is MCR bit 4
    LOOP, which routes MCR's four output bits into MSR's four status bits —
    Linux's `autoconfig` uses exactly that to decide whether a UART is present
    at all, and abandons the port if the answer is not DCD|CTS. The **data** half
    of loopback is not implemented; what that costs is `autoconfig_irq`, which
    discovers an unknown interrupt line, and no devicetree-registered driver
    runs it.
  * **No 16650/16750 extensions**, no enhanced mode, no receive trigger levels,
    no character timeout, no break detection, no per-character error tagging in
    the FIFO. FIFOs are 16 bytes, fixed, because that is what a driver assumes on
    seeing a 16550A. [`../architecture.md`](../architecture.md) decisions 5 and 21.
