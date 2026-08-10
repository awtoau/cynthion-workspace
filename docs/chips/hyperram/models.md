# Simulating the part: two models and one testbench

A HyperRAM you can run without the board. Winbond's own model is the reference;
an open twin runs everywhere else; one testbench drives both and fails if they
disagree.

**Use this when** a register or protocol question can be settled without hardware
— byte order, latency counting, CR0/CR1 semantics, what a violation looks like.
It is **not** a substitute for the board on anything analogue: capture phase,
drive strength, the 200 MHz failure.

| file | what |
|---|---|
| `sources/models/W956X8MBY_verilog_p.zip` | **Winbond's own model.** Vendor IP, gitignored, fetched by the route in [`../../../sources/README.md`](../../../sources/README.md) |
| [`../../../gateware/probes/hyperram/hyperram_model.v`](../../../gateware/probes/hyperram/hyperram_model.v) | **the open twin.** Plain Verilog; Icarus, Verilator, cocotb |
| [`../../../gateware/probes/hyperram/vendor_model_tb.sv`](../../../gateware/probes/hyperram/vendor_model_tb.sv) | the shared testbench; instantiates whichever `` `DUT_MODULE `` names |
| [`../../../scripts/hyperram_vendor_model_sim.py`](../../../scripts/hyperram_vendor_model_sim.py) | the runner, and the regression |

## Running it

    scripts/hyperram_vendor_model_sim.py                 # both, and they must agree
    scripts/hyperram_vendor_model_sim.py --sim icarus    # open twin only, no Diamond needed
    scripts/hyperram_vendor_model_sim.py --grade T250    # the grade the datasheet has no column for
    scripts/hyperram_vendor_model_sim.py --keep          # leave tmp/hyperram-vendor-model for vsim -gui

Exit code is the result: every marker present, zero testbench failures, and the
deliberate tCSM violation reported. 0.7 s for the vendor model, ~1.3 s for both.
Log at `tmp/logs/hyperram_vendor_model_sim.log`.

## The Winbond model

`W956X8MBY_verilog_p.zip` holds a nested zip per supply voltage —
`W956A8MBYA_verilog_p.zip` is the 3.0 V part on this board. Inside:

| file | |
|---|---|
| `W956A8MBYA.modelsim.vp` | the model, encrypted for ModelSim/Questa |
| `W956A8MBYA.vcs.vp`, `.nc.vp` | the same body sealed for Synopsys and Cadence |
| `Config-AC.v` | **plaintext**, and the reason to open the zip even without a simulator |

### It only runs under Questa, and Diamond ships Questa

    `pragma protect data_method  = "aes128-cbc"
    `pragma protect key_keyowner = "Mentor Graphics Corporation"
    `pragma protect key_keyname  = "MGC-VERIF-SIM-RSA-1"

The AES session key is in the file but sealed with Siemens' RSA public key; the
private key lives in the Questa binaries. **Diamond 3.14 bundles Questa Sim
Lattice OEM Edition**, so `~/lscc/diamond/3.14/questasim/bin/vlog` opens it.
Icarus, Verilator, cocotb, GHDL and Aldec Active-HDL never can.

Two flags, neither documented by Winbond:

- **`-sv`** — the protected region is SystemVerilog. Verilog-2001 mode fails with
  *"syntax error in protected region"*, which reads like a decryption failure and
  is not one.
- **`+define+T166`** (or `T85`/`T100`/`T104`/`T133`/`T200`/`T250`) —
  `Config-AC.v`'s AC-parameter block is an `ifdef` chain over the grades **with no
  default branch**. No grade, no timing parameters, and every identifier in the
  protected region comes up undefined.

### What is plaintext, and what is not

Everything except the behaviour. The port list, `` `include "Config-AC.v" ``, and
*every declaration* ship in clear: the memory array, `ID_REG0/1`, `CONFIG_REG0/1`,
`latency_code`, `burst_length`, `refresh_cntr`, and the DPD / hybrid-sleep /
software-reset state with their `realtime` stamps. Only the always-blocks and
tasks are sealed. Reading the declarations tells you the whole state space.

### What it implements

More than any open model. Observed from its own reporting:

- **Register space** — ID0/ID1/CR0/CR1 with correct POR values, decoded aloud
  (*"Manufacturer: Winbond (4'b0110), col addr bits: 9, row addr bits: 13"*).
- **CA decode**, byte by byte, with the command named: *"The decode command:
  Read Register ID0"*.
- **Latency** — type, code and count: *"Latency type: 1 (fixed), Latency code: 7,
  Latency count: 14"*, and it measures the applied clock (*"The clock frequency:
  100.000000 MHz, tck_i: 10.000 ns"*).
- **Timing checks** that fire as `ERROR-DIE0`, including **tCSM** (*"The CE LOW
  period is 5030.000 ns, it should be smaller than 4000.000 ns"*) and tCSDPD.
- **Power states** — power-up to ready after tVCS, RESET# handling, DPD, hybrid
  sleep, software reset.
- **8 MiB array**, announced as `DIE0 address: 'h3F_FFFF ~ 'h00_0000`.

### `Config-AC.v` is worth reading on its own

Plaintext AC parameters per grade, including a **250 MHz** block the datasheet has
no column for (`tACC` 28 ns = 7 × 4 ns, `tCSHI` 6 ns, `tRWR` 28 ns, `tIS`/`tIH`
0.5 ns, `tDSS`/`tDSH` ±0.4 ns), a package default of 200 MHz against a KGD default
of 250, and **`tCSM` = 4000 ns below 85 °C / 1000 ns above** behind
`` `define LA_85C ``.

## The open twin

`hyperram_model.v` is written to the datasheet — not translated from source
nobody can read — and kept honest by the shared testbench.

**Models:** the 8 MiB array, CA decode, register space with POR values, the
CR0[7:4] sparse latency encoding and CR0[3] doubling with the matching CA-period
RWDS level, RWDS as read strobe and as write mask, linear / wrapped / hybrid
bursts, deep power down with RESET# recovery, and a tCSM check.

**Does not model:** setup/hold, tRWR, tRP/tRPH, **refresh** — and therefore the
refresh collision that makes variable latency vary — hybrid sleep, software
reset, or anything analogue. Those stay the vendor model's job, which is why the
pair is the deliverable rather than either one alone.

One number in it is calibrated rather than derived: the data phase starts
`4 + 2 × latency_ck` edges after CS# falls, where the arithmetic suggests
`6 + 2 × latency_ck`. The latency count runs from the last CA *clock*, and the
last two CA bytes share the third clock. The shared testbench is what caught it,
and only on a write — a read self-aligns on RWDS and hides the error.

## Three protocol facts the pair established

Each would present as a hardware fault on the board, and each is a live risk for
the controller work.

1. **RWDS is driven HIGH during the CA period.** That is the extra-latency
   request, not the read strobe. A controller that hunts for the strobe from CS#
   low latches onto it and samples a tristate bus. It matters most with
   **variable latency** (`CR0[3] = 0`), where the CA-period RWDS is the only thing
   that says whether the access takes 1x or 2x — with fixed latency the answer is
   always 2x and the mistake is invisible.
2. **A register write must present its first data byte on the very next edge
   after the CA.** One edge late and the device latches the idle bus as the high
   byte, landing `CR0[15] = 0` and putting the part into **deep power down** —
   after which every later transaction fails for a reason that looks nothing like
   the cause.
3. **A write byte commits on RWDS strictly low, not merely "not high".** A host
   that releases RWDS with its data leaves it floating; treating that as unmasked
   stores `z` into the next address, surfacing later as corruption nowhere near
   the transaction that wrote it.

## Coverage today

| case | vendor | twin | note |
|---|---|---|---|
| ID0 / ID1 / CR0 / CR1 read | yes | yes | all four match the board |
| CR0 write + read-back | yes | yes | drive strength 34 → 67 ohm |
| CR1 write + read-back | yes | yes | **CR1[6] = 0 differential is accepted and reads back 0** |
| memory write + read, low and top word | yes | yes | `0x3fffff` confirms 8 MiB |
| **fixed vs variable latency (`CR0[3]`)** | yes | yes | **28 vs 14 edges, to the edge** — see below |
| **deep power down + RESET# recovery** | yes | yes | `CR0[15] = 0`, device silent until RESET# |
| deliberate tCSM violation | yes | yes | fires at exactly 4 us |
| **wrapped / hybrid burst (`CR0[2]`)** | yes | yes | **hybrid confirmed** — see below |
| **refresh collision forcing 2x latency** | yes | no | vendor only — the twin has no refresh |
| hybrid sleep, software reset | **not exercised** | no | vendor only |

The twin's fidelity is bounded by this table, not by the encryption. Every row
added to the testbench is a row the twin has to get right, and the latency and
DPD rows are checked as exact strings so a drift fails rather than degrades.

### Variable latency, measured

The mechanism [#338](https://github.com/awtoau/cynthion-workspace/issues/338)
turns on, from Winbond's own model and matched by the twin to the edge:

| `CR0[3]` | RWDS during CA | edges from end of CA to first read strobe |
|---|---|---|
| `1` fixed | **high** | **28** = 14 CK = 2 x 7 |
| `0` variable | **low** | **14** = 7 CK |

So under fixed latency the device raises RWDS during every CA and the level
carries no information — a controller can sample it at the wrong moment, or not
at all, and nothing breaks. Under variable latency **that RWDS level is the whole
answer**, and a controller that reads it stale or early times every access
wrongly while still passing every fixed-latency test.

The twin never asks for extra latency because it has no refresh. The vendor model
does, which is why a refresh-collision case has to run against the vendor.

### Hybrid burst leaves the group; it does not circle it

An 18-word wrapped read starting 13 words into a 16-word group, with `CR0[2] = 0`:

    100d 100e 100f 1000 1001 1002 ... [16] = 1010  [17] = 1011

So the group is covered once — critical word first, wrapping at the boundary —
and then the burst **continues linearly into the next group** rather than going
round again. That is what makes it useful for a cache line: the line arrives
critical-word-first and the stream keeps going, which is option 3 in
[`w956a8.md`](w956a8.md). `CR0[2] = 1` selects legacy wrap, which does circle.

Group size is `CR0[1:0]`: 128 / 64 / 16 / 32 bytes for `00`/`01`/`10`/`11`, and
the POR `11b` is 32 bytes = 16 words.

### Latency varies transaction to transaction, and here is how often

Under variable latency the device asks for 2x only when a refresh is due. Over
200 back-to-back reads at 100 MHz against the vendor model, **2 were given the
extra latency** — about 1%. Rare enough that a controller which mishandles it
passes casual testing, and certain enough that it will happen in service.

The twin has no refresh and never asks, so this case runs against the vendor
model only (`+define+VENDOR_ONLY`).

### Deep power down is one bad register write away

`CR0[15] = 0` enters DPD; the device then answers nothing and only RESET# gets it
back (`tRP` 200 ns, `tRPH` 400 ns, and the vendor model warns below `tDPDIN`
3 us). A register write whose first data byte is one edge late writes exactly
this by accident — see the second protocol fact above. Both models are silent in
DPD and both come back with `CR0 = 0x8f2f` after reset.
