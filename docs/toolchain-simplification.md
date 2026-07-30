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
| `ecp5-test/riscv/hello_soc.py:45-46` | `core.blockram`, `cpu.VexRiscv` | real use |
| `ecp5-test/riscv/vexii_hello_soc.py:45` | `core.blockram` | real use |
| `ecp5-test/riscv/cpu_area.py:33-34` | `core.blockram`, `cpu.VexRiscv` | real use |
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

The second-order trap is worse and is documented at `hello_soc.py:37-44`:
importing `luna_soc.gateware.vendor.amaranth_soc` *directly* yields a **different
class object** for `wishbone.Interface` than the `sys.path`-aliased
`amaranth_soc`, so `Decoder.add()` rejects a structurally identical bus. Only the
bare name may be used downstream. Note `ecp5-test/riscv/cpu_area.py:33-37` does
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
PASS  wishbone.Decoder + WishboneCSRBridge (hello_soc.py shape)
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

## Reproducing

```bash
python3 scripts/amaranth_soc_dropin_test.py     # → tmp/logs/amaranth_soc_dropin_test.log
```

Creates `tmp/amaranth-soc-dropin-venv`, installs real amaranth-soc from
`tmp/forks/amaranth-soc` (or git), and runs all six probes. Never touches the
system environment.
