# Dynamic probe of the ECP5 configuration engine

Date: 2026-07-29. Part: LFE5U-12F (IDCODE `0x21111043`) on Cynthion, via Apollo JTAG.

All prior ECP5 fuzzing in this project is **static**: Verilog to Diamond to bitstream,
then diff the bits. Nothing executes, and a bit's meaning is inferred from where it
lands. This exercise is **dynamic**: real opcodes issued to the configuration engine on
live silicon, recording what the hardware does. It tests capability rather than encoding,
and it can overturn conclusions static analysis cannot reach -- a documented absence is
not the same as a hardware refusal.

Scripts: `scripts/ecp5_opcodes.py`, `ecp5_cmd_probe.py`, `ecp5_cmd_sweep.py`,
`ecp5_targeted.py`, `ecp5_verify_reads.py`, `ecp5_control.py`, `ecp5_analyze.py`.
Raw results: `tmp/ecp5_cmd_sweep/`. Logs: `tmp/logs/`.

---

## The methodological finding, which came first and matters most

**An opcode issued without walking the TAP through RUN-TEST/IDLE is indistinguishable
from an opcode the silicon does not implement. Both read back as completely inert.**

Apollo's `_execute_command` parks the TAP in `DRPAUSE`/`IRPAUSE`. Several ECP5
configuration commands only take effect once the TAP passes through RUN-TEST/IDLE.
Apollo's own `configure()` calls `chain.run_test(2)` after each configuration command,
which is why configuration works; a probe harness that omits it measures its own plumbing.

Observed, same opcode, same payload, same device state, the only difference being the
TAP walk:

| Sequence | Status after |
|---|---|
| `ISC_ENABLE`, no `run_test` | `0x00200100` (unchanged) |
| `ISC_ENABLE`, then `run_test(2)` | `0x00200F10` (`ISC_ENABLE`, `WRITEABLE`, `READABLE`, `JTAG_ACTIVE` set) |

The first version of this sweep ran without idling and reported *every* configuration
opcode as inert, including ones that demonstrably work. That result was an artifact of
the harness. A **positive control** (`ecp5_control.py`) now gates the conclusions: it
drives `ISC_ENABLE` / `ISC_ERASE` / `ISC_DISABLE` and asserts the expected status
transitions are visible. If it fails, no "inert" reading from that run is interpretable.
It failed, was diagnosed, and the sweep was re-run. **Every result below is from the
corrected harness with a passing control.**

The general lesson for negative results on this project: a null observation is only
evidence once a positive control proves the measurement can detect a non-null.

---

## Opcode source

Lattice's own programming procedure, installed locally, is authoritative and supersedes
guessing from Project Trellis:

    ~/lscc/diamond/3.14/data/vmdata/database/xpga/ecp5/LatticeECP5.svp

It declares **104 opcodes** (98 distinct codes; several names alias one code). Apollo's
`ecp5.py` enum knows a small subset -- it only ever covered what `configure()`/`flash()`
need.

Also consulted, as a second independent implementation: OpenOCD `src/pld/` (`ecp5.c`,
`certus.c`, `lattice_cmd.h`), mirrored at `${GIT_MIRROR:-$HOME/git_mirror}/openocd`.

### Vendor file declares but never uses

`LSC_ISCAN` (0xDF), `LSC_EBR_READ` (0xB0), `LSC_EBR_WRITE` (0xB2), `LSC_READ_CRC` (0x60),
`LSC_READ_SED_CRC` (0xA4). Precisely the opcodes bearing on the open questions here get a
code and a name from the vendor and nothing else -- no sequencing, no payload length, no
preconditions. Lattice's own tooling never issues them.

---

## Safety gating (what was deliberately not issued)

24 opcode codes were excluded and never sent. These write OTP fuses, security cells,
feature rows, trim or manufacturing data; on this part those are permanent and have no
erase path. Includes `LSC_PROG_OTP` (0xF9), `LSC_PROG_FEABITS` (0xF8),
`LSC_PROG_CIPHER_KEY` (0xF3), `ISC_PROGRAM_SECURITY` (0xCE).

Two gating decisions are worth recording because name-trust would have caused damage:

- **0xD1 is aliased.** It is `LSC_READ_TRIM` -- and also `LSC_PROG_TRIM` and
  `LSC_PROG_MES`. Issuing it hoping to read trim could instead write analog trim fuses.
  Safety is therefore resolved **per opcode code, taking the worst classification among
  all aliases**, never per name. 0xD0 (`LSC_STORE` / `LSC_PROG_PES`) is the same shape.
- **0x7D `LSC_DEVICE_CTRL` was NOT swept.** The name suggests a benign control register.
  OpenOCD's `lattice_certus_erase_device()` shows it is an **erase-arm**: 0x7D at IRPAUSE
  with DR=8, then 0x7D at IDLE with DR=0, then `ISC_ERASE`. Which fuse banks an armed
  erase reaches on an LFE5U-12F is not documented; the vendor `.svp` declares 0x7D without
  ever using it, and OpenOCD's *ECP5* path never uses it either -- only the Certus path
  does, and Certus has internal non-volatile configuration the ECP5 lacks. Two
  implementations declining to use it on this part, for an operation of unknown blast
  radius, was sufficient reason to leave it alone. **It was never issued in any form.**

No `FlashOpcode` was issued and nothing wrote flash; the partition table and boot image at
offset 0 were untouched throughout.

---

## Observations

Device survived every sweep. Nothing hung, nothing wedged, no recovery was needed, and
`DONE=1` with a running design at the end.

### Commands that changed the status register (configured device, DONE=1)

| Opcode | Name | Status transition | Effect |
|---|---|---|---|
| 0xC6 | `ISC_ENABLE` | `0x00200100` → `0x00200F10` | sets `ISC_ENABLE`, `WRITEABLE`, `READABLE`, `JTAG_ACTIVE` |
| 0x74 | `LSC_ENABLE_X` | `0x00200100` → `0x00200F11` | as above, plus bit 0 |
| 0x79 | `LSC_REFRESH` | `0x00200100` → `0x00201E00` | **clears `DONE`**, sets `BUSY` -- reboots the device |
| 0x26 | `ISC_DISABLE` | `0x00200F10` → `0x00200100` | clears the four bits `ISC_ENABLE` set |
| 0x0E | `ISC_ERASE` | (with ISC enabled) clears `DONE`, sets `BUSY` | erases SRAM |

`LSC_REFRESH` is the only single command observed to drop `DONE` from a running design
without ISC being enabled first.

### Registers that return real data

Verified by `ecp5_verify_reads.py`: value must be stable across repeats, survive an
interleaved `BYPASS`, and be consistently anchored across read lengths.

| Opcode | Name | Value | Note |
|---|---|---|---|
| 0xE0 | `IDCODE_PUB` | `0x21111043` | LFE5U-12 |
| 0x16 | `LSC_IDCODE_PRV` | `0x21111043` | "private" IDCODE, same value; **not in Apollo's enum** |
| 0x19 | `LSC_UIDCODE_PUB` | `0x001b808604602635` | **real 64-bit unique device ID**; not in Apollo's enum |
| 0x11 | `LSC_READ_PES` | `0x0a000600` | electronic signature; not in Apollo's enum |
| 0x9F | `LSC_I2CI_SR_RD` | `0x20` | I2C status; not in Apollo's enum |
| 0x1C | `LSC_PRELOAD`/`LSC_SAMPLE` | `0xaaaaaaa4` unconfigured, `0xb2aa1144` configured | boundary-scan chain; **tracks the running design** |
| 0x2C | `INTEST` | `0xb3fa1144` configured | boundary scan |
| 0x3C | `LSC_READ_STATUS` | status word | |

`LSC_UIDCODE_PUB` is the most immediately useful: a stable per-die serial readable over
JTAG with no fabric involvement, which Apollo does not currently expose.

### Reads that FAILED verification (artifacts, not registers)

`ISC_READ` (0x80), `LSC_VERIFY_INCR_RTI` (0x6A), `ISC_ADDRESS_SHIFT` (0x42),
`LSC_PROG_INCR_RTI` (0x82) returned plausible-looking non-zero values that were **not
stable across repeats**. A naive sweep would have reported these as working registers.
They are shift-path residue. This is why the verification step exists.

### The IDCODE artifact

After `EXTEST` (0x15), `EXTEST_PULSE` (0x2D) or `EXTEST_TRAIN` (0x2E), the *following*
`LSC_READ_STATUS` returns `0x21111043` -- the IDCODE, not a status word. Decoded naively
this reads as a dramatic status change (`DONE` cleared, `BUSY` set, several undocumented
bits set). It is a TAP instruction-register selection effect, not a configuration status
change, and is reported separately by `ecp5_analyze.py`.

### Inert opcodes

70 of the 73 swept codes returned zeros and changed no status, at every payload length
tried, in both device states, with the harness verified working by the positive control.
This includes all five the vendor declares but never uses.

---

## Answers to the questions posed

**1. `JUMP` (0x7E) — inert.** Bare, and with 8/16/24/32-bit address arguments including
non-zero addresses: no response, no status change, `DONE` never dropped, no reboot. Note
`JUMP` **does not appear in Lattice's ECP5 procedure at all**; it is a Trellis-only name.
*Inference:* nothing supports runtime boot selection via this opcode on this part. The
static conclusion (BOOTADDR is build-time fuses) is not overturned.

**2. Background SPI / `LSC_PRELOAD`.** `LSC_PROG_SPI` (0x3A, Apollo's
`LSC_ENTER_BACKGROUND_SPI`) with Apollo's own `0x68FE` unlock code against a running
design: accepted without error, no status change, `DONE` stayed set. `LSC_PRELOAD` is
0x1C and is **boundary scan** (`LSC_SAMPLE` shares the code) -- it returns live pin state
and is not a reconfiguration mechanism.

**3. Configuration writes while `DONE=1` — silently ignored.** `LSC_BITSTREAM_BURST`
(0x7A), `LSC_PROG_INCR_RTI` (0x82) and `LSC_PROG_INCR_CMP` (0xB8) issued to a running
device, with and without a preceding `LSC_INIT_ADDRESS`: **no `FAIL`, no `BUSY`, no
`INVALID_COMMAND`, `DONE` never dropped, the design kept running.** The engine neither
accepts nor refuses them -- it does nothing observable. To get a state change, ISC must be
enabled first, and `ISC_ENABLE`/`ISC_ERASE` then clear `DONE` and stop the design.
*Inference:* partial or background reconfiguration of a running design is not reachable
through this command interface. Configuration requires taking the design down.

**4. `LSC_PROG_SED_CRC` (0xA2) — inert**, as is `LSC_READ_SED_CRC` (0xA4) before and
after it, at 1/2/4/8-byte reads. `LSC_READ_CRC` (0x60) likewise, including after
`LSC_RESET_CRC` (0x3B). No CRC value was obtainable over JTAG by any sequence tried.

**5. Status register beyond the documented bits.** Across all states only these bits were
ever observed to move: 0 (undocumented, set by `LSC_ENABLE_X`), 4 `JTAG_ACTIVE`,
8 `DONE`, 9 `ISC_ENABLE`, 10 `WRITEABLE`, 11 `READABLE`, 12 `BUSY`, 21 `STANDARD_PRE`.
No undocumented bit moved except bit 0. Bits 13 `FAIL`, 22, 26, 27, 28 never set once --
notably, nothing in this entire sweep provoked an error flag.

OpenOCD names three status bits Apollo does not, now recorded in the decoder: bit 14
`STATUS_FEA_OTP`, bits 6 and 17 (`STATUS_ERROR_BITS` `0x00020040`), bits 24-27 "BSE
Error". None were observed set on this device.

**Bonus, not asked for:** `LSC_EBR_READ` (0xB0) is **inert** -- with no address, after
`LSC_INIT_ADDRESS`, and after `LSC_WRITE_BUS_ADDR`, at 1-32 byte reads, both configured
and ISC-enabled. `LSC_ISCAN` (0xDF) is inert at 1-128 bytes. Reading block RAM or
performing readback over JTAG was not achievable by any sequence tried here.

---

## Bugs found in Apollo

`ECP5Programmer.read_id()` decodes the part ID **little-endian**, so an LFE5U-12F reads
back as `0x43101121` and misses `PART_NAMES`, printing "Unrecognized FPGA (43101121)".
The wire bytes are `21 11 10 43`; big-endian gives `0x21111043`, which is in the table.
`_read_status()` and `_read_usercode()` on adjacent lines both use `byteorder='big'`
correctly -- only `read_id` differs. Cosmetic, but it makes every ECP5 look unrecognised.

---

## Honest limits of these negatives

The inert results are stronger than they were, because the positive control proves the
harness can observe the engine changing state, and because the sweep covers the vendor's
own opcode list rather than a guess. They are still bounded by:

- Only payload lengths 0/1/4/8 bytes were swept generically (plus vendor-specified widths
  and targeted longer reads). An opcode needing a specific longer argument could be missed.
- Preconditions were followed where the vendor file documents them; for the five
  declared-but-never-used opcodes, no vendor sequence exists, so "inert" means "inert
  under every sequence tried here", not "unimplemented in silicon".
- One device, one part variant (LFE5U-12F). Security/OTP-related reads returning zero may
  reflect unprogrammed fuses on this specific part rather than an unimplemented opcode.
- 24 codes were never issued by choice. Their behaviour is unknown and deliberately so.
