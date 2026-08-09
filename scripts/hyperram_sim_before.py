#!/usr/bin/env python3
#
# Run the HyperRAM protocol simulation against an OLDER pair of controllers.
# SPDX-License-Identifier: BSD-3-Clause

"""`soc_hyperram_sim.py`, with the controllers taken from an earlier commit.

    python3 scripts/hyperram_sim_before.py <commit>

A new check that has only ever been run against the fixed design says nothing:
it may be measuring the fix, or it may pass on anything. This loads
`hyperram_controller.py` and `hyperram_dqs_controller.py` from `<commit>` into
`sys.modules` under their real names, leaves the simulation itself at the working
tree, and runs it -- so the failures printed are the checks that can discriminate.

Exit status is the simulation's: non-zero means checks failed, which is the
expected outcome against a commit predating the fix under test. Output goes to
the terminal and to `tmp/logs/dev.log`, like the simulation it runs.

Used for #316 (14 checks fail), #317 (1) and #321 (2); the numbers live in those
commit messages.

`T_CSM_NS`/`T_CSM_MARGIN` are grafted onto the loaded modules: they are imported
by name from the controller and did not exist before #316. Nothing else is
patched, so every behavioural difference is the controllers' own.
"""

import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONTROLLERS = ("hyperram_controller", "hyperram_dqs_controller")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    before = sys.argv.pop(1)

    sys.path[:0] = [str(ROOT / "gateware"),
                    str(ROOT / "gateware" / "soc"),
                    str(ROOT / "gateware" / "probes" / "hyperram"),
                    str(ROOT / "scripts")]

    import peripherals            # the real package, so the parent name resolves

    for name in CONTROLLERS:
        path = f"gateware/soc/peripherals/{name}.py"
        source = subprocess.run(["git", "show", f"{before}:{path}"],
                                capture_output=True, text=True, cwd=ROOT,
                                check=True).stdout
        module = types.ModuleType(f"peripherals.{name}")
        module.__file__ = f"<{before}>:{name}.py"
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        module.T_CSM_NS = getattr(module, "T_CSM_NS", 4000.0)
        module.T_CSM_MARGIN = getattr(module, "T_CSM_MARGIN", 0.9)
        sys.modules[f"peripherals.{name}"] = module
        setattr(peripherals, name, module)

    import soc_hyperram_sim
    print(f"controllers from {before}, simulation from the working tree")
    return soc_hyperram_sim.main()


if __name__ == "__main__":
    sys.exit(main())
