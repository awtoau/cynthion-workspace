# NS16550A — the console UART, in fabric

The register map both of this SoC's consoles answer on. Not a chip: it is
`ecp5-test/riscv/uart16550.py`, instantiated twice, and it is here with the chip
notes because a driver author needs the same thing from it that they need from a
part — which reads change state, and what they change.

**Index:** [`../hardware.md`](../hardware.md)

| | |
|---|---|
| Source | [`../../ecp5-test/riscv/uart16550.py`](../../ecp5-test/riscv/uart16550.py) |
| Driver | `firmware/cynthion-soc/src/uart.rs`, one type for both instances and for QEMU |
| Instances | index 0, USB CDC-ACM on AUX; index 1, async serial on R14/T14 to Apollo |
| Base addresses | `cynthion_soc_pac::base`, generated from the SoC's own memory map — **not written down here** |
| Simulation | `scripts/uart16550_sim.py` (35 assertions) |
| Reference | QEMU's `-M virt` `ns16550a`, which the same driver is tested against |

The offsets below are the NS16550A's own, fixed by the part being copied rather
than by our decoder. What can drift — where the peripheral sits — is generated,
and [`../hardware.md`](../hardware.md) explains why that is never transcribed.

## Every read that changes state

| read | changes | what a driver must do |
|---|---|---|
| **+0 RBR**, DLAB clear | pops the receive FIFO; LSR.DR clears once it empties | check DR first. Reading an empty FIFO returns the previous byte |
| **+2 IIR** | clears the transmit-empty interrupt, and only while IIR is reporting it (id `0b001`) | read it once per interrupt, which is the standard sequence anyway |
| **+5 LSR** | clears OE, PE, FE, BI and the bit 7 summary, and the interrupt they raise | **keep the value.** A poll that discards it discards the only report of a lost byte |

Everything else — IER, LCR, MCR, MSR, SCR, and +0 with DLAB set, which is DLL —
may be read at any rate with no effect at all.

**LSR is at +5 and RBR at +0, in different 32-bit words.** That is what makes a
poll loop structurally unable to reach the data register, whatever the bus does
to the access, and it is why the standard layout was adopted. IIR at +2 *does*
share a word with RBR; what keeps them apart is that VexiiRiscv drives a
single-byte `sel` for a byte access, `amaranth_soc.csr.wishbone` strobes only the
lanes `sel` names, and the peripheral is in a `main=0` PMA region where no cache
line fill reaches it. [`../comparisons.md`](../comparisons.md) decision 4 records
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

## What this is not

  * **No baud rate, on either instance's register map.** DLL and DLM are stored,
    read back, and connected to nothing. Bit timing for the Apollo line lives in
    `serial_line.py`, where the wire was chosen.
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
    seeing a 16550A. [`../comparisons.md`](../comparisons.md) decisions 5 and 21.
