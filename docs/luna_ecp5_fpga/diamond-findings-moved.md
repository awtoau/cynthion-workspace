# Diamond findings live in pluribus

The Diamond 3.14 mining work — 18 harvesters, 2092 webhelp pages, the vendor
BFD dump, `.spd` timing, `.con` topology — is generic ECP5 and toolchain
material with nothing board-specific in it, so it lives in the pluribus repo:

    docs/ecp5/diamond-mining-findings.md      the main report
    docs/ecp5/diamond-oracle.md               Diamond as an oracle for the open flow
    docs/ecp5/diamond-par-isolation-blocked.md   why PAR could not be isolated

Filed as pluribus issues #92 (the `ep5c00`/`sa5p00` tree trap and the source
hierarchy), #93 (BFD: trellis is a strict subset, 13 tiles missing), #94
(capabilities absent from the open flow), #95 (live-silicon opcode probe) and
#96 (loose ends and next-pass order).

The harvesters themselves remain at `scripts/diamond_*.py` here, because they
were written and run against this workspace's toolchain checkout.

**What stays in this repo:** the programming path — JTAG/USB configure speed,
flash partitioning, the INITN gap — because that is board-specific. See
`docs/luna_ecp5_fpga/` and issue #100.
