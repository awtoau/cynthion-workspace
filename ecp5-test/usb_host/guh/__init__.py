#
# Vendored from apfaudio/guh. Do not edit these files.
# SPDX-License-Identifier: BSD-3-Clause

"""
`guh/usbh` at upstream commit `923c8490`, taken verbatim.

    upstream: https://github.com/apfaudio/guh
    commit:   923c8490993193f3d5613f07cb4041992769716d
    licence:  BSD-3-Clause (see LICENSE beside this file)

## What is here, and what is deliberately not

| file | what |
|---|---|
| `types.py` | the UTMI/speed enums the other two share |
| `reset.py` | bus reset and the **host** side of the high-speed chirp |
| `sie.py` | the transaction engine: tokens, SOF, SETUP/IN/OUT, handshakes |

Not taken: `enumerator.py` and `descriptor.py` (they throw the descriptors away,
hard-code the device address, and specialise the parser at synthesis time --
`docs/usb-host-proposal.md` section 16), `engines/*` (fixed-function classes),
`periph/*` (a block device, not a host controller).

## Why vendored rather than depended on

`docs/upstream-boundary.md`: do not inherit a stack to get one file. GUH has no
releases, no tags, one author, an explicit "interfaces will change" warning and
a stated policy of not accepting contributions. The pin is the mitigation.

The commit also matters for licence reasons: at `fbd7077`, the pin the proposal
was written against, three SoC-facing files carried CERN-OHL-S-2.0. That was a
copy-paste leftover, reported as `apfaudio/guh#1` and fixed in `923c8490` --
which is why the pin is that commit and not an earlier one.

## The rule for changes

These files stay byte-identical to upstream so a later pin bump is a diff rather
than an archaeology exercise. Anything we need that they do not do goes in a
module beside this package -- `model.py` today, the CSR shim next -- or upstream.
Simulation needs the timing constants scaled; `model.py` does that by patching
the class attributes at run time rather than by editing the source.
"""
