# Upstream patch validation process

The rules governing how a patchset destined for Great Scott Gadgets gets
validated before it is offered. Written to be followed by someone who did not
write the patches.

The patchset itself is in [`upstream-patch-plan.md`](upstream-patch-plan.md).
This document is deliberately separate: the plan goes stale as commits land, the
procedure should not.

## Why this exists

This project has repeatedly produced claims of verification that did not hold —
patches described as "verified on hardware" with no test in the suite, ROM
figures quoted from a build nobody could reproduce, and a headroom premise that
was out by nine percentage points in the direction that breaks things. Upstream
has no way to audit any of that. The cost of a wrong claim is not a failed build;
it is a maintainer's trust, spent once.

So the burden of proof sits here, not with the reviewer.

## The five rules

1. **Every patch is accompanied by a test or tests.**
2. **Tests are run on hardware.** Not simulated, not reasoned about, not
   inferred from a successful build.
3. **The whole patchset tests cleanly twice in a row.**
4. **No fixing as you go.** If patch 3 fails, you do not patch over it and
   continue. The set is re-derived and the full sequence re-run from patch 1.
5. **End-to-end every time.** Apply 1, then 2, then 3, then 4. No shortcuts, no
   starting from the middle, no "this one obviously still applies".

Everything below is the consequence of taking those five seriously. The rules as
stated leave real questions open; leaving them open is how a process gets quietly
reinterpreted at 11pm by someone who wants to be finished.

---

## What "clean" means

A run is clean when **all** of the following hold. Anything else is a failure,
and a failure invokes rule 4.

- Every patch in the sequence applies with no conflict and no manual
  intervention. A conflict resolved by hand is a different patchset — re-derive
  and restart.
- Every position builds. Not just the final one: rule 5 means each intermediate
  tree is a tree upstream could bisect to, so each must compile.
- For the Apollo d11, every position fits the 14336 B application region, and
  the linked image passes the vector-table guard. **A build that links is not a
  build that works** — see the LTO failure mode below.
- Every test associated with every patch applied so far passes. Not just the
  newest patch's tests. A patch that breaks an earlier patch's test is a
  failure of the set, not of the test.
- Zero unexplained skips. A skip is acceptable only where it is *declared in
  advance* for that run with a stated reason (no target device attached, a
  destructive test not enabled). An unexpected skip is a failure, because a
  skipped test proves nothing and reads like a pass in a summary line.
- No new warnings from the firmware build or the linter. Upstream inherits
  warnings; we should not hand them any.

"Clean" specifically does **not** mean "no errors I judged important". The
person running the sequence does not get to triage.

## Flaky tests

**A flaky test is a failed test.** There is no third category.

If a test passes and then fails on the same tree, the run is failed under rule 3
and the *test* is now the defect. Fix the test — or the intermittent bug it just
found, which is the more likely explanation — and start again from patch 1. Do
not re-run the individual test hoping for green; do not mark it as expected-flaky;
do not raise the tolerance until it passes.

This matters most for exactly the patch that most needs it. `da564f8` fixes two
ISR/main-loop races. A race is *by definition* intermittent, so an
intermittently-failing test around it is evidence, not noise. The temptation to
retry-until-green is strongest precisely where retrying destroys the information.

Corollary: a test whose result depends on timing must state its tolerance and
the reason for it, and must fail on the wrong side of that tolerance rather than
warning.

## A test that cannot fail is not a test

Before a patch's test counts as satisfying rule 1, it must be shown to **fail
when the patch is reverted**. Assert-nothing tests are the main way rule 1 gets
satisfied on paper and not in substance — `apollo info` returning without an
exception demonstrates that the device is on the bus, and nothing else.

Record the failing-without-the-patch result alongside the passing-with-it
result. Two data points, or the test is unproven.

## Can a patch that cannot be hardware-tested ever be submitted?

**Default: no.** Rule 2 is the rule this project most needs.

There are three narrow exceptions, and each carries a heavier obligation than a
hardware test would have:

1. **Host-only changes with no device interaction.** `apollo install-udev`
   `--print-only` writes no file and touches no board. Test it as a unit test,
   and state in the submission that it is host-only and why.
2. **Changes whose effect is a property of the built artifact, not its
   behaviour.** `01ae228`'s internal-linkage pass is verified by inspecting the
   linked image. But note the trap: `--gc-sections` discards uncalled functions,
   so a symbol's *absence* can mean "never called" rather than "inlined", and an
   unfinished feature can look like a saving while being missing from the image.
   Under LTO a missing symbol may legitimately mean inlined — so confirm against
   the disassembly, not the symbol table alone.
3. **Changes to hardware we do not have.** `973fa78` is scoped to `cynthion_d11`
   precisely because d21, qtpy, daisho, xplained and pico carry the same unused
   driver but were not tested. That scoping is correct and should be preserved:
   **do not widen a patch to boards you cannot test.** If upstream wants it
   everywhere, that is upstream's call to make with their bench.

In all three cases the submission must say plainly what was and was not
exercised. "Not tested on hardware, here is why, here is what was checked
instead" is a legitimate thing to tell a maintainer. "Verified" when it was not
is not.

What is **never** acceptable: reasoning that a change is too small to break
anything. `da564f8` is 28 bytes and fixes two real bugs; the earlier version of
`e034daa`'s SPI pipelining was a few lines, silently corrupted TDO, and produced
an all-zero status register that a naive test would have read straight past.

## Recording a run so a second party can confirm it happened

A run that is not recorded did not happen. Each run produces a log at
`tmp/logs/upstream-run-<YYYYMMDD-HHMMSS>.log` containing, in order:

- **Provenance.** Upstream base commit SHA, and the SHA of each patch in
  sequence. Not branch names — branches move.
- **Environment.** Compiler version (`arm-none-eabi-gcc --version`), host Python
  version, `apollo` version string, and the OSS CAD Suite version if gateware is
  built. A ROM figure is meaningless without the compiler that produced it.
- **Device identity.** Board revision and the device serial number **for the log
  only** — serials are scrubbed before anything is published, per the workspace
  content rules.
- **Per position:** the patch applied, whether it applied cleanly, the build
  result, ROM/RAM figures against the region, and the full test output including
  skip reasons.
- **A verdict line** naming the run: clean or failed, and if failed, at which
  patch and why.

The bar is that a second party can take the log, re-derive the same sequence
from the recorded SHAs, and get the same numbers. If the log does not contain
enough to do that, it is not a record.

Test output goes in raw. Summarised test output ("all tests passed") is the
format in which unverified claims travel.

## Proving the two clean runs are distinct

Rule 3 says twice in a row. The obvious failure is one run reported twice, which
is also the easiest thing to do by accident when copying a summary between
documents.

Requirements for the second run to count:

- **Separate log file, separate timestamp** in the filename. The timestamps must
  differ by at least the real duration of a run — two logs a few seconds apart
  are one run.
- **The tree is rebuilt from scratch.** `git clean -fdx` on the firmware tree
  and a full rebuild, not an incremental one. An incremental second run mostly
  re-tests the build cache.
- **The sequence is re-applied from the base commit.** Not "the tree is already
  at patch 8, run the tests again" — that tests rule 3 without testing rule 5.
- **The device is power-cycled between runs**, so run 2 starts from a boot state
  rather than inheriting whatever run 1 left behind. This is the only way to
  catch a patch that works only on a warm device.
- **The runs are not interleaved.** Run 1 completes entirely, including its
  verdict line, before run 2 begins.
- Both logs are committed. Two logs in the repo, both referenced by SHA in the
  submission notes.

If the two runs disagree, the set has failed rule 3 and rule 4 applies. Two runs
that disagree also mean something is nondeterministic, which is a finding worth
chasing before submitting anything.

## A patch that passes in only one ordering

Record the constraint, then treat the ordering as part of the patchset.

The set already has one: **`01ae228` cannot be applied before `39a2213`,
`df4a93b` and `6cc219e`** — it conflicts textually, because it makes
`jtag_deinit()`'s pin table `static const` and gives `vendor.c`'s handlers
internal linkage, and those three restructure the same regions.

Rules for such patches:

- The constraint is recorded in the plan with its **cause**, not just its
  existence. "Must be last" is a fact someone will eventually re-litigate;
  "must be last because it cleans up regions that A4–A6 restructure" is an
  argument that survives.
- It is encoded as an executable check, not a note. `scripts/apollo_rom_sizing.py`
  keeps both the failing ordering (`lto-first`) and the passing one
  (`lto-first-fixed`) so the constraint is re-provable on demand rather than
  remembered.
- **A patch that only applies in one position is offered to upstream in that
  position, and the dependency is stated in the submission.** Upstream may
  reorder; they should be told what breaks if they do.
- If a patch's only valid position is one upstream will not accept, the patch
  needs rebasing into a form that stands alone. That is new work, and it
  re-enters the process at patch 1.

Distinguish the two kinds, because they fail differently:

- **Textual conflict** — announces itself at apply time. Cheap to find.
- **Semantic conflict** — every patch applies, every patch builds alone, and
  together they exceed the flash region. This one is silent until the last
  patch, and it is why rule 5's end-to-end requirement is not bureaucratic. The
  Apollo set has exactly this shape: A4+A5+A6 total +336 B against upstream's 568
  B of headroom.

## The size budget is part of "clean"

For any patch touching `cynthion_d11` firmware, the ROM figure at every position
is a test result and goes in the log.

The application region is 14336 B — 16 KB of flash less `BOOTLOADER_SIZE = 0x800`
for Saturn-V. That split is a linker `--defsym` from `board.mk`, not a hardware
boundary, but it is not ours to move: Saturn-V uses 2016 of its 2048 bytes.

Two standing traps:

- **Do not trust a headline ROM figure without checking the symbols are
  present.** `--gc-sections` discards uncalled functions, so an unfinished
  feature can appear to fit while being absent from the image.
- **LTO on this target has a silent failure mode.** A naive enable links cleanly
  and produces a **four byte** binary: the SAMD11 linker script has no `ENTRY()`
  directive and builds its vector table from weak aliases, so the plugin prunes
  the entire program as unreferenced. The roots must be named explicitly with
  `-Wl,--entry=Reset_Handler -Wl,-u,exception_table`. Remove either and the dead
  binary returns **with no diagnostic**.

  Therefore: any run that touches build flags must decode the linked vector
  table and assert Reset, SysTick, USB, EIC, SERCOM1 and TC1 resolve to real code
  rather than `Dummy_Handler`. `scripts/verify_vectors.py` does this. A size
  figure alone would score a pruned image as an excellent result — which is the
  precise reason this check is mandatory rather than advisory.

## Before anything is sent

Independent of validation, the workspace rules on publishing apply. Upstream is
a repository we do not own, which is the **three-check tier**:

1. Confirm the exact action and the target repository.
2. Show the final text and get a second explicit approval.
3. Explicit confirmation that this should go to someone else's repository rather
   than staying on our fork.

Plus a mandatory content scrub of every patch and every commit message:

- No local filesystem paths (`/mnt/...`, `/home/...`).
- No serial numbers, credentials, or device identifiers.
- No `Refs: awtoau/cynthion-workspace#NN` lines. Several commits in this set
  carry them; an upstream reader cannot resolve them and they leak our tracker.
- No reference to unrelated sensitive work.
- A decision on `Co-Authored-By` trailers.

Vendor request numbers (`0xed`, `0xec`, `0xb9`, `0xFFFE`) are offered as
proposals, never as facts. Upstream owns that number space, and our local
allocations have already collided once.

## Worked example: the luna-soc submission

The awto-luna-soc work is the shape to copy. It took upstream 0.3.2, backported
amaranth-soc `d8b5892` (Python 3.14 CSR annotations), and then reverted five
`__init__` workarounds the backport made redundant — 94 lines across 8 files. Two
stacked PRs, both merged.

The transferable lesson is about what verification means. Every affected class
**imported cleanly whether or not the bug was present**, because the bug
manifested at *construction*. Verification therefore required **constructing 72
CSR Registers**, not importing them.

An import test would have passed on the broken code and reported success. That is
the same failure mode as a firmware image that links cleanly and is four bytes
long, and the same as `apollo info` returning without an exception.

So: **for each patch, ask what the bug's symptom actually is, and make the test
provoke that.** Not the nearest convenient thing that runs without error. The
gap between "it loaded" and "it works" is where every unverified claim in this
project has lived.
