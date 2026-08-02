# Winbond W956A8MBYA6I — the HyperRAM

8 MiB of HyperBus DRAM on Cynthion r1.4. Named in
`repos/cynthion/.../gateware/facedancer/top.py:42`; **nothing in this workspace
recorded what the chip was** before #109 — throughput had been characterised in
detail without anyone naming the manufacturer or the density.

**Index:** [`../hardware.md`](../hardware.md)

## 64 Mbit is 8 MiB — read the bits-versus-bytes carefully

This is the single fact most worth not re-deriving. Misreading 64 Mbit as 4 MiB is
what made an ordinary part look like it held twice its marking, and produced
**three successive wrong explanations** before the datasheet settled it.

64 Mbit ÷ 8 = 8 MiB. The storage responds to 8 MiB because that is what a 64 Mbit
part holds. There is no undocumented capacity, no hidden die, and no configuration
trigger to look for.

## Identification and configuration registers

Read by `scripts/hyperram_identify.py`, decoded against
`sources/Winbond-W956A8MBYA-64Mbit-HyperRAM.pdf` (the part's own datasheet; the
ISSI files kept alongside are equivalents for comparison).

| address | register | value | decode |
|---|---|---|---|
| `0x0000` | ID0 | `0x0c86` | see below |
| `0x0001` | ID1 | `0x0001` | die revision |
| `0x0800` | CR0 | `0x8f2f` | normal operation, latency 2, fixed latency, wrapped burst |
| `0x0801` | CR1 | `0xffc1` | distributed refresh controls |

**ID0 `0x0c86` decoded:**

| bits | field | raw | meaning |
|---|---|---|---|
| 15:14 | die address | `00` | die 0 |
| 12:8 | row address bits | `01100` = 12 | **13 bits** — 8192 rows |
| 7:4 | column address bits | `1000` = 8 | **9 bits** — 512 columns |
| 3:0 | manufacturer | `0110` | Winbond, per its table 8 |

**Both count fields are minus-one** — table 5.2 gives `00000` as *"One Row address
bit"*. So 8192 × 512 × 2 = **8 MiB**, and section 8.1.1 states it outright:
*"9 column and 13 row address bits ... 2^22 = 4M words = 8M bytes"*.

## The Manufacturer Information Register — undocumented in the HyperBus spec

HyperBus specifies four registers. Winbond adds a fifth at **`0x1000`**, named in
its section 9.1 table 5 as *"Manufacturer Information Register (0~17) read"*,
**read only**, spanning `0x1000`–`0x1011`. Read by `scripts/hyperram_regfuzz.py`.

| address | value | ASCII (LE) |
|---|---|---|
| `0x1000` | `0x3030` | `00` |
| `0x1001` | `0x3230` | `02` |
| `0x1002` | `0x3739` | `97` |
| `0x1003` | `0x3034` | `40` |
| `0x1004` | `0x0736` | — |
| `0x1005` | `0x4c8d` | — |
| `0x1006` | `0x3320` | reserved per table 6 |
| `0x1007` | `0x3320` | reserved per table 6 |
| `0x1008`–`0x100b` | repeats `0x1000`–`0x1003` | the block is 8 addresses wide |

First eight bytes little-endian read **`00029740`** — a lot or date code. **The
register is named but not defined**: section 9 details ID0/ID1, CR0 and CR1 and
stops, so that reading is plausible rather than vendor-confirmed.

Four artifacts were excluded before believing it:

- **not the dead-bus pattern** — `0x8484` is what memory above 8 MiB and the whole
  top-die register space return;
- **not a mirror of a documented register** — `0x2`/`0x4`/`0x400` return ID0's
  value and `0x802`/`0xc00` return CR0's, so the address decode is incomplete;
- **not the memory array** — stamping memory at word `0x1000` with `0xDEAD` left
  the register reading `0x3030`;
- **not bitstream bleed.**

**It refuses writes**, with a control proving the write path: writing `0x5a5a` left
it at `0x3030` while a CR0 write in the same run read back changed.

**Ten of its eighteen words have never been read** (`0x100c`–`0x1011`). The sweep
stopped where it did because every read cost a gateware build, a flash and a JTAG
read. Queued for the Rust CLI (#109) — a CPU on the bus reads all eighteen in
microseconds.

## Measured behaviour

**New, from `scripts/hyperram_ceiling.py` (2026-08-03).** Everything in this
section is measured on this board unless marked inherited.

| | |
|---|---|
| **highest clean clock** | **CK 192 MHz — 15.7% above the part's 166 MHz rating** |
| **throughput there** | **334.4 MB/s read, 351.1 MB/s write, 87.1% of theoretical** |
| **first failing clock** | CK 200 MHz, and it fails in bulk, not intermittently |
| error rate when clean | 0 in 200 M words per rung, every rung to CK 192 |
| die temperature | DTR code 30, unchanged across the whole sweep |
| address space | flat linear 0–8 MiB *(inherited)* |
| ~~220.2 MB/s, 92.8% of theoretical~~ | *superseded — see tCSM below* |
| ~~verified ceiling 120 MHz~~ | *superseded — was a PLL limit, not the part's* |

### The clock the part sees is not the `sync` clock

The two PHYs gear differently, and a ladder indexed by `sync` compares nothing:

| PHY | gearing | bits per `sync` cycle | device CK |
|---|---|---|---|
| `HyperRAMPHY` | `ODDRX1F`, 2:1 | 16 | `sync` |
| `HyperRAMDQSPHY` | `ODDRX2F`, 4:1 | 32 | **2 × `sync`** |

Measured, not inferred from the primitive: the DQS path returns 209 MB/s at
`sync` 60, which is impossible if CK were 60 MHz — eight lines at DDR cap that
at 120 MB/s.

### 61 clocks are reachable, not 3 — because this design has no ULPI

The ceiling was previously recorded as "somewhere between 120 and 240" because
`LunaECP5DomainGenerator` offers only 60/120/240, and
`VariableClockDomainGenerator` narrows it further: it solves `usb` to exactly
60 MHz in the same pass and refuses anything else, leaving **60, 100 and 120** as
the only `sync` values below 130.

That constraint is real but it is the *ULPI PHY's*, and this design has no ULPI.
Apollo reaches it over JTAG through the SAMD11, and `JTAGRegisterInterface` runs
in `sync` plus a local JTCK domain — no `usb` domain is instantiated at all. Drop
the constraint and the PLL offers **61 `sync` frequencies** between 60 and 260
MHz, because `sync = 60 × CLKFB_DIV / CLKI_DIV` independently of `CLKOP_DIV`.

`hyperram_ceiling_top.py` solves its own PLL for `sync` and `fast` only, for that
reason. Anything with a ULPI must keep using
`VariableClockDomainGenerator` — a wrong `usb` presents as a dead board, not a
timing error.

### The ladder

200 M words verified per rung, 128-word bursts, pattern derived from the device
address. `%` is against 2 bytes per CK, which is what eight lines at DDR give.

| device CK | non-DQS read | | DQS read | | verdict |
|---|---|---|---|---|---|
| 120 MHz | 198.2 MB/s | 82.6% | 209.0 MB/s | 87.1% | both clean |
| 140 MHz | 229.5 MB/s | 82.0% | 243.8 MB/s | 87.1% | both clean |
| 150 MHz | 246.2 MB/s | 82.1% | 261.2 MB/s | 87.1% | both clean |
| 160 MHz | *design will not build* | | 278.6 MB/s | 87.1% | DQS clean |
| 168 MHz | *design will not build* | | 292.6 MB/s | 87.1% | DQS clean, **past rating** |
| 180 MHz | *design will not build* | | 313.5 MB/s | 87.1% | DQS clean |
| **192 MHz** | *design will not build* | | **334.4 MB/s** | **87.1%** | **DQS clean — the ceiling** |
| 200 MHz | *design will not build* | | 348.3 MB/s | 87.1% | **88% of words wrong** |
| 210 MHz | *design will not build* | | — | — | transactions stop completing |
| 220 MHz | *design will not build* | | 378.7 MB/s | 86.1% | 88% of words wrong |

**The non-DQS blank is an FPGA limit, not the part's.** nextpnr treats a missed
constraint as an error, and this design's `sync` closes at about 158 MHz, so
non-DQS bitstreams above 150 MHz do not exist to test. It says nothing about
whether the part would have worked there — the DQS path answers that, and it
did.

### DQS raises the ceiling, and not for the reason it looks like

DQS reaches CK 192 where non-DQS stops at 150, but the mechanism is arithmetic
rather than signal integrity: at a given CK the DQS design clocks the *fabric* at
CK/2, so the FPGA stops being the binding constraint. The DQS design's own `sync`
ceiling is **lower** (~115 MHz against ~158), which is why CK 200 needs `sync`
100 and builds comfortably while non-DQS at CK 160 cannot be built at all.

DQS is also worth 5 percentage points of efficiency at every rung — a constant
87.1% against 82.0% — because `HyperRAMDQSInterface` waits
`HIGH_LATENCY_CLOCKS = 5` where `HyperRAMInterface` waits 14, and those are
`sync` cycles, which are half as frequent.

### What the failure at CK 200 looks like

Not a clean stop and not intermittent: 176,390,902 of 200,621,440 words wrong,
in the first pass, immediately. The first mismatch says what kind of wrong:

    wanted  0xfeff0100      = {~256, 256} at device word 256
    got     0x0100fefd      = {256, ~258}

The two 16-bit halves are transposed and one of them belongs to a neighbouring
word — a **word-boundary slip in the 4:1 gearing**, not noise and not a dead bus.
It is the plausible-wrong-answer shape every other trap on this interface has
had: the data is structurally related to what was written, so a test that
checked only "did something come back" would pass.

The 12% that still matched are not luck — a 32-bit address-derived pattern
matches by chance at 2⁻³², so those are bursts where the slip did not happen.

Above the ceiling the part degrades in two distinct ways rather than one, and
they are not monotonic in the clock:

| CK | what happens |
|---|---|
| 200 MHz | transactions complete at full rate, 88% of words wrong |
| 210 MHz | transactions stop completing — no word is ever returned |
| 220 MHz | transactions complete again, 88% of words wrong |

A stall at 210 between two rungs that both run is worth not explaining away: it
is the read gating failing outright at one alignment while the neighbours only
mis-sample. **Nothing here is a clean stop**, which is the practical warning —
a system clocked past 192 MHz gets corrupt data at full speed, not an error.

These bulk failures are also the positive control the sweep needed. Every rung
to 192 MHz reported exactly zero errors, and zero errors everywhere is equally
consistent with a comparator that never fires; the failures show it does, and
the first-mismatch record shows it reports *what* was wrong correctly.
`hyperram_ceiling_top.py --negative-control` checks reads against the complement
of what was written, for the case where a sweep finds no failure at all.

### BURSTDET never asserted — so "DQS works" is not the claim being made

`DQSBUFM`'s `BURSTDET` stayed low on every DQS rung, at every clock, including
the clean ones. By this workspace's own discriminator that means a clean read
cannot be credited to the strobe having been found.

What *is* established: `DDRDLLA` locked and its settle sequence completed on
every rung; `DATAVALID` — the same block's output — gated the reads, and reads
that were not gated correctly would not have verified; and the path returned
byte-exact data at CK 192, which the non-DQS path cannot reach. The clean-then-
bulk-fail transition between 192 and 200 is also the wrong shape for a fixed
count that merely happened to land right, which would drift gradually.

`READCLKSEL` is upstream's `0b010` and has never been swept. That is the obvious
next experiment and it was not run here.

## tCSM caps the burst, and the 220.2 MB/s figure exceeded it

CR1 reads `0xffc1`, so CR1[1:0] = `01b` = **4 µs tCSM** — the longest CS# may
stay low. Refresh is distributed and cannot run while CS# is low, so a longer
burst is not merely slow, it is outside spec, and it fails by forgetting later
rather than by returning anything wrong at the time.

`hyperram_speed.py` moved 2048 words in one transaction — **17 µs at 120 MHz,
over four times tCSM**. Its 220.2 MB/s is therefore a rate the part is not
specified to sustain, and it is faster than the legal figure precisely because
it amortised the command and latency phases over an illegal burst.

The legal comparison at the same clock is 198.2 MB/s non-DQS / 209.0 MB/s DQS at
CK 120. The headline number went *up* anyway — to 334.4 MB/s — by raising the
clock rather than by lengthening the burst.

Full throughput characterisation and the measurement traps:
[`../luna_ecp5_fpga/hyperram-detailed.md`](../luna_ecp5_fpga/hyperram-detailed.md).

## Wiring on r1.4

12 signals, `IO_TYPE="LVCMOS33"`, `SLEWRATE="FAST"`, DDR on the data path.

| signal | ECP5 pin | DQS group |
|---|---|---|
| `clk` P / N (LVCMOS33D differential) | C3 / D3 | `LDQ8` |
| `dq[0..7]` | F2, B1, C2, E1, E3, E2, F3, G4 | `LDQ8`, except E2 = `LDQSN8` |
| `rwds` | D1 | **`LDQS8` — the group's strobe pin** |
| `cs` (active low) | B2 | `LDQ8` |
| `reset` (active low) | C1 | `LDQ8` |

**Everything is in left DQS group 8, bank 7, and RWDS is on the strobe pin.** So
the ECP5's `DQSBUFM` can reach this part: the board was wired for the DQS read
path. `scripts/hyperram_dqs_pins.py` checks it against the prjtrellis database,
and nextpnr confirms it (`Constrained DQSBUFM 'phy.U$4' to LDQS8`).

E2 carries `dq[5]` on the group's DQSN pin, which is free because RWDS is
single-ended.

## How software reaches it

`HyperRAMInterface` / `HyperRAMPHY` from luna, used as-is and working. **There is
no Wishbone peripheral, so a CPU cannot reach it at all** (#90).

The DQS path (#92) is upstream's controller with our PHY under it, which is the
boundary [`../upstream-boundary.md`](../upstream-boundary.md) settles. **It has
now run on hardware** and reaches CK 192 MHz byte-exact, with the BURSTDET
caveat above.

One bug was found bringing it up, and it is the *fourth* instance of the trap
this page already lists twice. `hyperram_dqs_top.py` raises `perform_write` in
`m.d.sync` on entry to its start state, so it is still low on the cycle
`start_transfer` is asserted — and the controller latches `is_read` from it in
exactly that cycle. The result is a read transaction where a write was intended,
`write_ready` never asserts, and the design hangs: 398 M cycles spent writing and
zero words moved. **`hyperram_dqs_top.py` still has this shape** and has never
been on hardware, so it will hang if run. Drive the control signals
combinationally from the FSM state, as `hyperram_ceiling_top.py` does.

## Timing this design can violate without a symptom

**tCSHI, 10 ns of CS# high between transactions.** Longer than one 120 MHz cycle
(8.33 ns), and `HyperRAMDQSInterface`'s `RECOVERY` state carries
`# TODO: implement recovery` and falls straight through to `IDLE` — so the
controller keeps no gap and the caller must. A violation does not fail; it
occasionally returns the wrong word.

**Fixed latency changes what a latency "fix" is worth.** CR0 `0x8f2f` has the
fixed-latency bit set, so the part takes the long count on every transaction and
RWDS during the command period is not a signal about that transaction. LUNA's
`extra_latency | 1` — reported as a defect in #90 — is therefore the correct
behaviour here. Shortening the latency needs CR0 reprogrammed first, and then
measuring; one without the other reads early.

Three bugs were found in *our own* use of that interface, not in it, and all three
produced plausible wrong answers rather than failures:

- `final_word` must be held rather than pulsed;
- `perform_write` / `write_data` must be held for the whole transfer;
- `CHID` is a single register window, so channel setup is not re-entrant.

## Scripts

| | |
|---|---|
| `scripts/hyperram_dqs_pins.py` | is the DQS group reachable? Device database, no board |
| `scripts/soc_hyperram_sim.py` | the protocol layer against a model of this part |
| `ecp5-test/hyperram/hyperram_dqs_top.py` | DQS bring-up bitstream (`--build` never programs) |
| `scripts/hyperram_identify.py` | ID0/ID1/CR0/CR1 and bank aliasing |
| `scripts/hyperram_regfuzz.py` | the `0x1000` register block, plus a write test |
| `scripts/hyperram_ceiling.py` | **the clock ceiling and throughput, both PHYs, indexed by device CK** |
| `ecp5-test/hyperram/hyperram_ceiling_top.py` | its gateware; `--list` prints the reachable clocks |
| `scripts/hyperram_ladder.py`, `hyperram_fifo.py` | throughput and clock ceiling, `sync`-indexed, 60/120 only |
| `scripts/fetch_winbond_hyperram.py` | fetches the datasheet into `sources/` |
