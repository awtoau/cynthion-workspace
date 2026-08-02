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

| | |
|---|---|
| streaming throughput | 220.2 MB/s, 92.8% of theoretical |
| verified clock ceiling | 120 MHz |
| address space | flat linear 0–8 MiB, no die-select handling needed |

The upstream ceiling was recorded as "somewhere between 120 and 240" only because
`LunaECP5DomainGenerator` could not build anything between —
see [`lfe5u-12f-ecp5.md`](lfe5u-12f-ecp5.md#clocking).

Full throughput characterisation and the measurement traps:
[`../luna_ecp5_fpga/hyperram-detailed.md`](../luna_ecp5_fpga/hyperram-detailed.md).

## Wiring on r1.4

12 signals, `IO_TYPE="LVCMOS33"`, `SLEWRATE="FAST"`, DDR on the data path.

| signal | ECP5 pin |
|---|---|
| `clk` P / N (LVCMOS33D differential) | C3 / D3 |
| `dq[0..7]` | F2, B1, C2, E1, E3, E2, F3, G4 |
| `rwds` | D1 |
| `cs` (active low) | B2 |
| `reset` (active low) | C1 |

## How software reaches it

`HyperRAMInterface` / `HyperRAMPHY` from luna, used as-is and working. **There is
no Wishbone peripheral, so a CPU cannot reach it at all** (#90), and the DQS path
is unfinished (#92). Writing that adapter is unavoidable; whether it wraps
upstream's controller or replaces it is open —
[`../upstream-boundary.md`](../upstream-boundary.md).

Three bugs were found in *our own* use of that interface, not in it, and all three
produced plausible wrong answers rather than failures:

- `final_word` must be held rather than pulsed;
- `perform_write` / `write_data` must be held for the whole transfer;
- `CHID` is a single register window, so channel setup is not re-entrant.

## Scripts

| | |
|---|---|
| `scripts/hyperram_identify.py` | ID0/ID1/CR0/CR1 and bank aliasing |
| `scripts/hyperram_regfuzz.py` | the `0x1000` register block, plus a write test |
| `scripts/hyperram_ladder.py`, `hyperram_fifo.py` | throughput and clock ceiling |
| `scripts/fetch_winbond_hyperram.py` | fetches the datasheet into `sources/` |
