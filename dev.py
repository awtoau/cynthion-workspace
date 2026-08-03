#!/usr/bin/env python3
"""Root shim so `./dev.py describe` works without knowing the layout.

Python is support tooling in this repo -- the products are Rust firmware,
Amaranth gateware and C -- so the logic lives in `scripts/dev.py` per the awto
placement rule, and this three-line shim is what keeps the canonical
`./dev.py <command>` invocation true anyway.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import dev  # noqa: E402

if __name__ == "__main__":
    sys.exit(dev.main())
