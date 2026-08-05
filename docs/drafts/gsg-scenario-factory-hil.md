# Scenario 11: Factory hardware validation

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §11.

## What it is

Not a user scenario — it is where upstream's real hardware-in-the-loop coverage lives. The
`greatscottgadgets/cynthion-test` repository, driven from the `cynthion` repo's
`Jenkinsfile`, tests a board against a purpose-built fixture before it ships.

It is in this survey because it answers a question the other ten children keep raising:
*how does upstream actually test any of this on hardware?* The answer is "with a rig", and
that is worth knowing before we assume they have a trick we are missing.

## What implements it upstream

`greatscottgadgets/cynthion-test` — `cynthion-test.py`, `selftest.py`, `speedtest.py`,
`calibrate.py`, `check.py`, `ranges.py`, `eut.py`, plus prebuilt `analyzer.bit`,
`selftest.bit`, `speedtest.bit` and `flashbridge.bit` committed into the repo, and firmware
images for the supporting instruments.

## Hardware it needs — and this is the point

- 1 × Cynthion r1.4.0 as the unit under test
- 1 × **Tycho r2.0.0** test fixture
- 1 × **Sasserides r1.0.0** (A and B boards, assembled as a stack with test pins)
- 1 × GreatFET One
- 1 × Black Magic Probe
- 1 × 24 V supply (Mean Well GST25B24-P1J or equivalent)
- 3 × ADT-Link UT6A-UT6B-NC cables with U1 pins 1 and 8 connected
- 10-pin ARM debug cable, 34-pin ribbon cable, PASS and FAIL buttons, a switched hub, and
  mechanical arrangements to hold the board against the pin bed

We have none of the fixture hardware. Tycho and Sasserides are GSG's own designs and are
not off-the-shelf.

## What porting it would require

Not portable, and it should not be treated as a goal. But three things in it are worth
taking without the rig:

1. **`ranges.py` is a specification.** It encodes the expected voltage and current ranges
   for a good board. Those numbers are useful to us as documented expectations even with no
   way to measure them automatically — they say what "good" means.
2. **`selftest.py` and `speedtest.py` are the harness around scenarios 6 and 9.** Reading
   how upstream sequences and interprets those tests is free specification for those
   children.
3. **The Jenkinsfile shows what upstream runs per commit** versus what it runs per board.
   We have no CI running on GitHub, so our equivalent of the per-commit half is
   `./dev.py ci` and it is worth checking whether anything upstream gates on is missing
   from ours.

## How it would be tested

Not applicable — it *is* the test. The relevant observation is about our own coverage:

Upstream's hardware confidence comes from a fixture we do not have. Ours has to come from
somewhere else, and the two available answers are the Amaranth simulations
(`./dev.py sim`, 15 of them, 9 in the pre-commit gate) and `./dev.py test-board` on the one
board we have. That is the argument for **P2 in the master issue** — a ULPI simulation —
being a prerequisite rather than a nice-to-have. We cannot buy hardware coverage the way
upstream does, so we have to simulate what they instrument.

## Verdict

**Needs hardware we lack.** Do not port. Mine `ranges.py`, `selftest.py` and the
`Jenkinsfile` for specification, and close it.
