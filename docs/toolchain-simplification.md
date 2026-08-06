# Can we drop `luna_soc`?

**Recommendation: a middle path. Do not drop `luna_soc` yet, but stop treating
it as a workspace-wide dependency — it is only needed by the facedancer SoC
build.** Our own `ecp5-test/` designs can move to real `amaranth-soc` today, and
that removes the import-order hack, the annotation patch script, and most of the
confusion. The fork stays only until the facedancer SoC is dealt with separately.

Established 2026-07-30 by `scripts/amaranth_soc_dropin_test.py`. Evidence in
`tmp/logs/amaranth_soc_dropin_test.log`. Every claim below was tested, not
reasoned.

## Summary of what was measured

| Question | Answer |
|---|---|
| Is `luna_soc` a hard dependency of `cynthion`? | **Yes, unconditionally** — `import luna_soc` at `cynthion/python/src/__init__.py:9`, top level, not in a try/except |
| Does the **analyzer** gateware need it? | **No.** Zero references in `gateware/analyzer/` |
| Does the **facedancer** gateware need it? | **Yes** — 5 import sites in `gateware/facedancer/top.py` |
| Do *our* `ecp5-test/` designs need it? | **No**, apart from the sys.path aliasing side effect |
| Is real `amaranth-soc` a drop-in for what we use? | **Yes** — all 6 probes pass |
| Is the py3.14 CSR annotation bug fixed upstream? | **Yes** — class-level `csr.Field` annotations work on real amaranth-soc under 3.15t |
| Upstream test suite | **295 passed** in 0.51 s, in the throwaway venv |

## 1. What actually pulls `luna_soc` in

`repos/cynthion/cynthion/python/pyproject.toml:47`:

```
    "luna-soc @ git+https://github.com/awtoau/awto-luna-soc.git@main",
```

So it is a hard install dependency pinned to our fork, and `cynthion/__init__.py:9`
imports it unconditionally with the comment *"Make sure all luna_soc's vendored
libraries are available"*.

**A false result worth recording**, because it is the kind of mistake this
project has been burned by. A first attempt used a `sys.meta_path` blocker with
`find_module()` and reported that `import cynthion` succeeded with luna_soc
blocked. That was wrong: `find_module` is the **removed** legacy import protocol
and was silently ignored, so nothing was ever blocked. Re-run with `find_spec`:

```
BREAKS   cynthion -> ImportError luna_soc blocked: luna_soc
BREAKS   cynthion.gateware.analyzer.top -> ImportError
BREAKS   cynthion.gateware.facedancer.top -> ImportError
```

`import cynthion` is a hard fail without luna_soc. Aliasing the vendored
`amaranth_soc` onto `sys.path` first does **not** help — both still break —
because the bare `import luna_soc` statement itself is what fails, regardless of
whether its stated purpose is already satisfied.

## 2. What we use from it

Our own designs, all four sites:

| File | Import | Why |
|---|---|---|
| `ecp5-test/riscv/vexii_hello_soc.py:45` | `core.blockram` | real use |
| `ecp5-test/i2c/multiplexed.py:69` | `core.blockram` | **aliasing only** — `# noqa: F401 (aliases amaranth_soc)` |
| `scripts/patch_amaranth_soc_annotations.py:110` | `core.blockram` | aliasing only, to locate the file it patches |

So the genuine surface is exactly two things:

- `luna_soc.gateware.core.blockram` — **127 lines**
- `luna_soc.gateware.cpu.VexRiscv` + `InterruptController` — **149 + 45 = 194 lines**, plus 5 pre-built VexRiscv `.v` files under `gateware/cpu/verilog/vexriscv/`

**321 lines of Python we would have to carry, against 4078 lines of vendored
`amaranth_soc` we would stop carrying.** That is the whole trade.

### The import-order hack, and why it exists

`luna_soc/__init__.py:9-20` — the package's own comment calls it a "mildly evil
hack":

```python
try:
    try:
        import amaranth_soc
        import amaranth_stdio
    except:
        path = os.path.join(os.path.dirname(...), "gateware", "vendor")
        sys.path.append(path)
```

Importing a luna_soc peripheral is what appends the vendor directory to
`sys.path`, which is what makes the bare name `amaranth_soc` resolvable. Hence
`import luna_soc.gateware.core.blockram` before `from amaranth_soc import csr`,
or `ModuleNotFoundError`.

The second-order trap is worse:
importing `luna_soc.gateware.vendor.amaranth_soc` *directly* yields a **different
class object** for `wishbone.Interface` than the `sys.path`-aliased
`amaranth_soc`, so `Decoder.add()` rejects a structurally identical bus. Only the
the ordering but carries no explanatory comment — the one site missing the note.

If real `amaranth-soc` is installed standalone, the `try` succeeds, the vendor
path is never appended, and **the entire hazard disappears**.

## 3. Is real `amaranth-soc` a drop-in?

Tested in a throwaway venv at `tmp/amaranth-soc-dropin-venv` (the system
environment was not touched). All six probes pass:

```
PASS  import + versions                      amaranth_soc = 0.1.dev1+g3e3d8b7
PASS  class-level csr.Field annotations (the py3.14 bug)
PASS  csr.Builder + csr.Bridge (multiplexed.py shape)
PASS  wishbone.Decoder + WishboneCSRBridge
PASS  things only luna_soc provides          (blockram/cpu correctly absent)
PASS  REAL multiplexed.py elaborated against real amaranth-soc
```

The last one is the decisive result. `ecp5-test/i2c/multiplexed.py` was imported
**unmodified**, with `luna_soc` replaced by an empty stub module so the aliasing
import became a no-op, and it **elaborated to 4670 lines of RTLIL**. The
peripheral is already portable; its luna_soc line is pure accident.

### The py3.14 annotation bug is fixed upstream

This is the finding that most changes the picture. Real amaranth-soc handles the
class-level annotation form under free-threaded 3.15t:

```
class Control(csr.Register, access="w"):
    start : csr.Field(csr.action.W, unsigned(1))
=> Control fields: [('start',), ('stop',)]        PASS
```

So `scripts/patch_amaranth_soc_annotations.py` — and the 40-odd `__init__`
rewrites in `repos/cynthion` commit `53d3ea4` that worked around the same bug —
exist only to compensate for the **vintage of the vendored copy**, not for a real
upstream defect. On the live environment that script now reports:

```
already patched -- annotationlib path present
```

It is a no-op today and would be unnecessary, not merely already-applied, on real
amaranth-soc.

### Divergence, quantified

Upstream `amaranth-soc` at `3e3d8b7`, in the throwaway venv: **295 passed, 6
subtests passed, 0.51 s**. The vendored copy reports `__version__ == "unknown"`
and fails 13 / does not collect 39 of the same suite. They have measurably
diverged, and the vendored side is the worse one.

## 4. What breaks

Concretely, if `luna_soc` were removed outright today:

**a) `import cynthion` fails.**
```
ImportError: No module named 'luna_soc'   at cynthion/python/src/__init__.py:9
```
This takes down the CLI, the analyzer, `cynthion run`, everything — even though
the analyzer gateware itself contains **zero** luna_soc references.

**b) The facedancer SoC build fails.** `gateware/facedancer/top.py` needs five
things that are genuinely not in amaranth-soc and never will be:
`blockram`, `spiflash` (incl. `ECP5ConfigurationFlashInterface`,
`SPIPHYController`), `timer`, `uart`, `usb2`, `InterruptController`, `VexRiscv`,
`provider.cynthion`, and `top_level_cli`. This is the real blocker and it is not
small.

**c) Our three `riscv/` designs lose `blockram` and `VexRiscv`** — 321 lines,
plus the pre-built `.v` files.

**d) `pip install amaranth-soc` from PyPI does not get you amaranth-soc.**
It installs a **placeholder at version `0` with no modules**; `import
amaranth_soc` then raises `ModuleNotFoundError`. The real package is
distributed from git only:

```
amaranth-soc @ git+https://github.com/amaranth-lang/amaranth-soc.git
```

This cost a full test cycle to discover and would silently defeat anyone
following the obvious instruction. It is also an argument *against* the
"depend on the real package directly" framing being as clean as it sounds —
you trade a git pin on our fork for a git pin on upstream, not for a PyPI
version.

## Recommendation

**Do not drop `luna_soc` wholesale. Do three narrower things instead.**

The proposal as written — "drop luna_soc, depend on real amaranth-soc" — fails
on (b): the facedancer SoC needs a peripheral library that amaranth-soc does not
contain. But the proposal's *goals* are almost entirely achievable without that.

### Step 1 — install real `amaranth-soc` alongside, and the hack evaporates

Add real `amaranth-soc` to the environment. luna_soc's own `try: import
amaranth_soc` then **succeeds**, the vendor path is never appended, and every
downstream user gets the real package. This alone:

- kills the load-bearing import order at all five sites,
- makes `scripts/patch_amaranth_soc_annotations.py` unnecessary rather than
  merely already-run,
- ends the two-different-`wishbone.Interface`-class-objects trap,
- and needs **no change to `cynthion` at all**.

This is the highest value for the lowest risk and should be done first. It needs
verifying that luna_soc's peripherals work against real amaranth-soc rather than
their vendored vintage — the CSR/wishbone probes above say the API shapes match,
but the facedancer build is the real test.

### Step 2 — cut our own designs loose

`ecp5-test/i2c/multiplexed.py`: delete the `import luna_soc...blockram` line and
its 6-line comment. Proven to work — that is what the RTLIL probe did.

The three `riscv/` designs: vendor `blockram.py` (127 lines) into `ecp5-test/`
and import `VexRiscv` explicitly. 321 lines total. Worth doing because it makes
our designs independent of the fork.

### Step 3 — only then consider the fork

Once steps 1 and 2 land, the fork `awtoau/awto-luna-soc` is needed for exactly
one thing: the facedancer SoC peripheral library. And its reason for existing is
already gone — HEAD `368174f` merged the py314-annotations fix, and real
amaranth-soc does not have that bug. So the fork can likely be **replaced by the
upstream `luna-soc` pin**:

```diff
-    "luna-soc @ git+https://github.com/awtoau/awto-luna-soc.git@main",
+    "luna-soc~=0.3.2",
```

which is what `debris/code/awto-cynthion-reference/.../pyproject.toml:47` already
had before the fork. **Verify before applying:** confirm upstream luna-soc 0.3.2
builds facedancer against real amaranth-soc. If it does, the fork, the
vendoring, and the patch script all go at once.

### If you want the `cynthion` patch anyway

To make `cynthion` importable without luna_soc — worth it because the analyzer
does not need it and currently cannot run without it — the change is one line at
`cynthion/python/src/__init__.py:9`:

```diff
-# Make sure all luna_soc's vendored libraries are available
-import luna_soc
+# luna_soc is only needed by the facedancer SoC gateware, which imports it
+# directly. Importing it here made the analyzer and the CLI fail without a
+# dependency neither one uses. Its only purpose was to alias luna_soc's
+# vendored amaranth_soc onto sys.path; when real amaranth-soc is installed
+# that aliasing is both unnecessary and undesirable.
+try:
+    import luna_soc
+except ImportError:
+    pass
```

and the matching `pyproject.toml` move of `luna-soc` from `dependencies` into an
optional `[project.optional-dependencies] facedancer` extra. This is a patch to a
repo we own (`awtoau/awto-cynthion`), so it is a local commit, not an upstream
submission.

## Sequencing

1. Install real `amaranth-soc` (git, not PyPI) in the live environment; confirm the facedancer build still passes. **Biggest win, no code change.**
2. Drop the aliasing import from `multiplexed.py`. Free.
3. Vendor `blockram.py` for the `riscv/` designs. 127 lines.
4. Test upstream `luna-soc~=0.3.2` against real amaranth-soc. If it builds, drop the fork and delete `scripts/patch_amaranth_soc_annotations.py`.
5. Optionally make `cynthion`'s luna_soc import conditional so the analyzer stops depending on the SoC stack.

Steps 1–3 are safe and can be done immediately. Step 4 is the one that actually
retires the fork, and it is a build test, not a judgement call.

## Done 2026-07-31: the `cynthion` package is out of `ecp5-test/`

A sixth step, not in the list above, turned out to be the cheapest of all and
has been taken.

The question this document asks is "can we drop `luna_soc`", and the answer kept
being "not while `cynthion` depends on it". But we were never using `cynthion`
for anything except **one class**: `CynthionPlatformRev1D4`, the r1.4 pin map.
Getting it dragged in the whole stack, because `CynthionPlatform` inherits
`LUNAApolloPlatform` → `LUNAPlatform` from `luna`, and the `cynthion` package
pins `luna-soc` to the fork.

That pin map is board wiring. It changes when the hardware revision changes,
which for r1.4 is never. So it is now vendored at `ecp5-test/cynthion_platform/`
and the dependency is gone from our gateware.

### What it cost

| File | Lines | What |
|---|---|---|
| `cynthion_r1_4.py` | 229 | the pin map, byte-identical to upstream from the class declaration down; the extra lines over upstream's 206 are the header explaining not to edit it |
| `core.py` | 158 | `CynthionPlatform`, ~60 lines of code where upstream had 134, the balance being the record of what was dropped and why |
| `resources.py` | 77 | `LEDResources` and `ULPIResource`, inlined |
| `__init__.py` | 24 | re-export, plus the naming warning |
| **total** | **488** | replacing the `cynthion` → `luna` → `luna-soc`-fork chain |

Imports are `amaranth` and `amaranth.build` only.

### What turned out to be dead weight

Checked rather than assumed, which mattered — the guesses were not all right.

**`toolchain_program`, `toolchain_flash`, `toolchain_erase`,
`_ensure_unconfigured`** — dead. Every `build()` call site in this workspace
passes `do_program=False`; the FPGA is configured through `apollo_fpga` directly
by our own scripts. Dropping them also drops `apollo_fpga` as a platform
dependency.

**`LUNAPlatform` in its entirety** — `create_usb3_phy`, `get_led`,
`request_optional` and `NullPin` are portability shims for designs that target
several boards. Nothing here uses them. `LUNAApolloPlatform` contributed exactly
one live method, `port_sharing`; its `apollo_gateware_phy` is unused.

**`clock_domain_generator = LunaECP5DomainGenerator`** — already superseded by
`VariableClockDomainGenerator`, which is why it was safe to drop the default.

### What was load-bearing, against expectation

**`pseudo_power_supply_fragment`** is the one to keep. r1.4 strands I/O balls to
VCCIO and GND that must be *driven* to source and sink additional supply
current, and Amaranth leaves an unrequested pin undriven. Cut it and the board
runs on less supply than it was designed for, with no build error to say so.
It needs `prepare()` alongside it, which is the only reason that override
survived.

**`toolchain_prepare`** sets `--freq 38.8`, the SPI configuration clock the
board reads its bitstream at on power-on. Not cosmetic.

**`DEFAULT_CLOCK_FREQUENCIES_MHZ`** is read by
`ecp5-test/adv_speed/adv_speed_gateware.py` to size a UART divisor.

### Two things the plan did not anticipate

**`amaranth_boards` is not installed on this machine.** It is vendored *inside*
the `cynthion` package, which injects it into `sys.modules` from its `__init__`
under a comment reading "Mildly evil hack". So `import amaranth_boards` only
works once `cynthion` has been imported — a platform depending on it would still
be pulling `cynthion` in, just invisibly. Only two constructors are used,
`LEDResources` and `ULPIResource`, both a dozen lines of pure `amaranth.build`,
so they are copied verbatim into `resources.py`. Verbatim and not tidied: they
build the pin records the map is expressed in, and a "cleaner" version that
constructed a subsignal differently would silently change the pin map.

**The package cannot be called `platform`.** `ecp5-test/` goes on `sys.path`, so
a package named `platform` there shadows the **standard library** `platform`
module for the whole process. `amaranth/tracer.py` imports it. Found by doing it
and watching the stdlib import resolve to our `__init__.py`. Hence
`cynthion_platform`. Do not rename it back.

### Proof

`scripts/platform_vendor_compare.py` does not diff source — a copy is exactly
the change that reviews clean and is wrong on the bench. It runs the real
place-and-route both ways, upstream platform and vendored, on the same design
text, and compares what nextpnr decided:

```
blinky: utilisation identical -- TRELLIS_COMB=39, TRELLIS_FF=28, TRELLIS_IO=59, DCCA=1, GSR=1, ...
blinky: pin assignment identical -- 59 pins located
wide:   utilisation identical -- TRELLIS_COMB=61, TRELLIS_FF=28, TRELLIS_IO=105, DCCA=1, GSR=1, ...
wide:   pin assignment identical -- 105 pins located
```

`wide` exercises both ULPI PHYs, HyperRAM, QSPI flash, the sideband pins and
both Type-C controllers, so the 105 located pins cover the bulk of the resource
list where a transposition would be easiest to miss by eye. Utilisation alone
would not catch a swapped pin; the per-signal ball comparison is the part that
does.

Five designs then elaborated to a bitstream against the vendored platform:
`hyperram_identify`, `hyperram_regfuzz`, `vexii_bench_soc` and `bitstream_sink`. `scripts/check.py` is 6/6.

```bash
python3 scripts/platform_vendor_compare.py   # → tmp/logs/platform_vendor_compare.log
```

### What still imports `cynthion`, and why that is correct

Nothing in `ecp5-test/` does, except `riscv/vexii_hello_soc.py`, which was left
alone only because another investigation owned it at the time; its change is the
same one-line import swap.

In `scripts/`, the remaining importers consume upstream's **own gateware and
register maps**, not our board definition, so vendoring the pin map does not
help them: `selftest_leds.py`, `selftest_led_modes.py` and `phy_probe.py` use
`cynthion.selftest.registers`; `check.py` and `install.py` elaborate upstream's
analyzer and facedancer gateware against r0.2 as a toolchain smoke test;
`cyn_main.py` drives the same. `platform_vendor_compare.py` imports it
deliberately, as the baseline it compares against.

**This does not by itself retire the fork.** `luna` is still a real dependency of
`ecp5-test/` for USB (`USBDevice`, `USBSerialDevice`, the stream endpoints),
HyperRAM (`HyperRAMInterface`) and `JTAGRegisterInterface`, and `luna_soc` still
supplies `blockram` and the CPU wrappers. What changed is that the dependency is
now on `luna` for things `luna` actually provides, rather than on the entire
`cynthion` → `luna` → `luna-soc`-fork chain for a list of pin names. Step 4
above is still the one that retires the fork.

## Reproducing

```bash
python3 scripts/amaranth_soc_dropin_test.py     # → tmp/logs/amaranth_soc_dropin_test.log
```

Creates `tmp/amaranth-soc-dropin-venv`, installs real amaranth-soc from
`tmp/forks/amaranth-soc` (or git), and runs all six probes. Never touches the
system environment.

## Hardware state at time of writing

Recorded so a later session can tell whether anything here disturbed the board.
**Nothing in this investigation touched the gateware** — no build, no flash. The
board was only read.

Observed 2026-07-30, Cynthion r1.4, firmware `v1.1.1-35-g74db0e6`:

```
python3 repos/apollo/apollo_fpga/commands/cli.py info
    Hardware: Cynthion r1.4   ADC reading: 3207   USB API 1.2

python3 scripts/sideband_read.py
    4/4 commands CRC OK (PING, STATUS, POWER, DEVICES)
    firmware health: ok=4 crc_fail=0 timeout=0
    PASS

python3 scripts/sideband_read.py --soak 5000
    good 5000  short 0  crc_bad 0   100.00%
    PASS
```

The sideband test bitstream is intact and the link is clean over 5000
transactions. `DEVICES` reports flash Winbond type 0x40 4 MiB and **hyperram
absent**, and `POWER` returns the fixed test pattern rather than real
measurements — both are the expected behaviour of this bitstream, not faults.
