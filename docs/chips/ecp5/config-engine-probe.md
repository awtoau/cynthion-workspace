# Probing the ECP5 configuration engine on live silicon

Static fuzzing infers what a bit *means* from where it lands in a bitstream.
This is the other kind: issue commands to the configuration engine on a running
device and observe what the hardware does. It tests capability rather than
encoding, and it can settle questions that arguments from absence cannot.

## The methodological finding, which matters more than any single result

**An opcode issued without walking the TAP through RUN-TEST/IDLE is
indistinguishable from an opcode the silicon does not implement.** Both read
back inert.

The first sweep ran without idling and reported *every* configuration opcode as
dead. That was the harness, not the silicon. Same opcode, same payload, same
state:

    ISC_ENABLE without run_test  ->  0x00200100   (unchanged)
    ISC_ENABLE then run_test(2)  ->  0x00200F10   (four bits set)

It was caught only because the probe carried a **positive control** asserting
that `ISC_ENABLE`/`ISC_ERASE`/`ISC_DISABLE` produce their documented
transitions. The control failed, which invalidated the run.

**A null observation is only evidence once the measurement is shown capable of
detecting a non-null.** That generalises well beyond this task, and applies to
several negative results elsewhere in this project.

## Results

Observed first, inference second.

### `JUMP` (0x7E) is inert

Bare, and with 8/16/24/32-bit addresses including non-zero: no response, no
status change, `DONE` never dropped. It also does not appear anywhere in
Lattice's own ECP5 programming procedure.

*Inference:* runtime boot selection is not reachable this way. The static
conclusion — that BOOTADDR is build-time fuses — stands.

### Configuration writes while `DONE=1` are silently ignored

The crux question. `LSC_BITSTREAM_BURST` (0x7A) and
`LSC_PROGRAM_AND_INCREMENT_*` (0x82, 0xB8) issued against a running device
produce **no FAIL, no BUSY, no INVALID_COMMAND, and `DONE` never drops**. The
engine neither accepts nor refuses them.

Getting any state change requires `ISC_ENABLE`, which itself stops the design.

*Inference:* partial or background reconfiguration is not reachable without
taking the running design down.

### Inert under everything tried

`LSC_EBR_READ` (0xB0), `LSC_ISCAN` (0xDF), and both CRC reads — including with
addresses set and ISC enabled. These are among the opcodes the vendor file
**declares but never uses**, so there is no vendor sequence to follow. "Inert"
here means "under everything tried", not "unimplemented".

### Genuine positives Apollo does not expose

| opcode | returns |
|---|---|
| `LSC_UIDCODE_PUB` 0x19 | a real 64-bit die ID |
| `LSC_IDCODE_PRV` 0x16 | real value |
| `LSC_READ_PES` 0x11 | real value |
| `LSC_I2CI_SR_RD` 0x9F | real value |

Verified here directly: the die ID reads `0x001b808604602635` on this board.
None of these four is in Apollo's opcode enum.

### Four false positives, rejected

`ISC_READ`, `LSC_VERIFY_INCR_RTI`, `ISC_ADDRESS_SHIFT` and
`LSC_PROG_INCR_RTI` returned plausible non-zero data that **was not stable
across repeats** — shift-path residue rather than register contents. A sweep
without repetition would have reported these as working registers.

## Safety

`LSC_DEVICE_CTRL` (0x7D) was not issued: OpenOCD shows it arms `ISC_ERASE`.

A second hazard was caught independently. **Opcode 0xD1 is aliased** to
`LSC_READ_TRIM` *and* to `LSC_PROG_TRIM`/`LSC_PROG_MES`. Trusting the
read-sounding name would have written analog trim fuses permanently. Safety is
now resolved per opcode *code*, taking the worst class among aliases; 0xD0 has
the same shape. 24 codes excluded, no flash opcode issued, boot image
untouched.

## Incidental bug in Apollo

`read_id()` decodes IDCODE little-endian, so every ECP5 prints as
`Unrecognized FPGA (43101121)`. The wire bytes are `21 11 10 43`, confirmed
here. Adjacent `_read_status` and `_read_usercode` use big-endian correctly.

## What bounds these negatives

Stronger than they were — a positive control proves the harness can observe the
engine changing state, and the sweep covers the vendor's own opcode list rather
than a guess. Still bounded by:

* Only payload lengths 0/1/4/8 bytes were swept generically, plus vendor-specified
  widths and targeted longer reads. An opcode needing a specific longer argument
  could be missed.
* For the five declared-but-never-used opcodes no vendor sequence exists, so
  "inert" means *inert under every sequence tried here*, not unimplemented.
* One device, one variant. Security/OTP reads returning zero may reflect
  unprogrammed fuses on this part rather than an unimplemented opcode.
* 24 codes were never issued, by choice. Their behaviour is unknown deliberately.

One finding worth naming separately: **`LSC_PRELOAD` (0x1C) is boundary scan**,
sharing its code with `LSC_SAMPLE`. It returns live pin state and is not a
reconfiguration mechanism, which is what its name suggests to a reader looking
for one.

## Scope

The generic sweep used 0/1/4/8-byte payloads plus vendor-specified widths, with
targeted longer reads. An opcode requiring a specific longer argument could
still be hiding — that is the main remaining gap.
