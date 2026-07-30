# Python toolchain: what is pinned, what is stale, what is a trap

Audited 2026-07-30 while the CSR work kept failing in ways that looked like
version skew. Most of it turned out not to be, and the parts that are wrong are
specific rather than general.

## Installed against latest available

| package | installed | latest | verdict |
|---|---|---|---|
| `amaranth` | **0.5.9** | 0.5.9 | current, nothing to do |
| `amaranth-stdio` | `0.1.dev37+gd296ba4` | no release | git snapshot, no release exists |
| `luna-usb` | 0.2.3 | 0.2.3 | current |
| `facedancer` | 3.1.3 | 3.1.3 | current |
| `usb-protocol` | 0.9.2 | 0.9.2 | current |
| **`luna-soc`** | **0.2.0** | **0.3.2** | **behind, and it is the one that matters** |

So "everything is stale" is not the situation. **One package is behind**, and it
is the one carrying the vendored `amaranth_soc`.

## The pins are ranges, not hard pins

- `cynthion` requires `amaranth~=0.5`, `luna-usb~=0.2.2`
- `luna` requires `amaranth~=0.5.0`, `usb-protocol~=0.9.2`

Compatible-release specifiers, so within a minor series they float. There is
nothing to "release" -- pip already installs the newest compatible version.

## The two things that are actually wrong

**`repos/luna` is checked out and is NOT what gets imported.** `import luna`
resolves to site-packages, so the local checkout is decorative -- editing it
changes nothing that runs. Same class of trap as flashing firmware from one
commit while running Python from another, which cost hours earlier in this
session. Either install it editable or stop treating the checkout as live.

Installed editable (so edits do take effect): `apollo_fpga`, `awto_probe`,
`cynthion`, `facedancer`. Not editable, so edits do nothing: `luna`, `luna_soc`.

**`amaranth_soc` has no version and is vendored.** It lives at
`site-packages/luna_soc/gateware/vendor/amaranth_soc/`, reports version
`unknown`, and is reachable only after importing a `luna_soc` module first --
importing `amaranth_soc` directly raises `ModuleNotFoundError`. Both
`ecp5-test/riscv/hello_soc.py` and `ecp5-test/i2c/multiplexed.py` carry a comment
about that import order because it is invisible otherwise.

## Why bumping luna-soc is not a one-liner

`cynthion` pins it to a fork, not to PyPI:

    luna-soc @ git+https://github.com/awtoau/awto-luna-soc.git@main

The fork exists for a reason recorded in `docs/luna_ecp5_fpga/luna_soc_fix_status.md`
and `luna_soc_amaranth_fix_complete.md`: **8 files and 40-plus CSR classes were
patched for an Amaranth API change.** Upstream 0.3.2 may or may not contain
equivalent fixes; that has not been checked.

So the upgrade path is: check whether 0.3.2 subsumes the fork's patches, and if
it does, drop the fork and take upstream. If it does not, rebase the fork onto
0.3.2. Neither is a version bump -- both need the facedancer gateware build,
which is the thing the original patches were for, re-tested afterwards.

## What this did NOT explain

The CSR failures being chased when this audit started were **mine, not version
skew**, and worth recording so the next person does not go looking at versions:

- `csr.action.RW` fields cannot have their `r_data` driven by gateware. RW means
  software owns the value. Returning a read byte through one is an elaboration
  error. Split write-data and read-data into separate registers.
- `csr.Bridge` **already adds every register in the builder as a submodule**.
  Adding them again raises `DuplicateElaboratable`.

Both are correct-by-design behaviour that reads as a broken dependency.
