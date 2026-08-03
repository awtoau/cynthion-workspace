#!/usr/bin/env python3
#
# The freshness check in bram_patch.py: #155's acceptance, watched not assumed.
# SPDX-License-Identifier: BSD-3-Clause

"""What `bram_patch.refresh_firmware` does with a stale, missing or foreign image.

#155 asks for one thing to be demonstrated rather than asserted: *a deliberately
stale `tmp/rust_fw.bin` makes the patcher fail or regenerate.* That is what these
tests stale on purpose and then watch.

`soc_run` is substituted rather than run. The real derivation is `objcopy` over a
cross-compiled ELF, which needs a toolchain and a built crate; what is under test
here is the DECISION -- derive, refuse, or deliberately skip -- and the decision is
this module's, not objcopy's.
"""

import hashlib
import io
import sys
import types
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bram_patch  # noqa: E402


STALE = b"\xde\xad\xbe\xef" * 4
FRESH = b"\xca\xfe\xba\xbe" * 8


class FreshnessTests(unittest.TestCase):

    def setUp(self):
        self.handle = io.StringIO()
        self._real_soc_run = sys.modules.get("soc_run")

        # Point the default at a temporary file. These tests write a deliberately
        # stale image to whatever `DEFAULT_FIRMWARE` names, and that is the real
        # `tmp/rust_fw.bin` -- so without this, running the suite would silently
        # corrupt the artifact of whatever build was in progress. Clobbering an
        # intermediate behind someone's back is the exact failure #155 is about,
        # and it would be a poor way to test the fix for it.
        self._real_default = bram_patch.DEFAULT_FIRMWARE
        self._tmp = TemporaryDirectory()
        bram_patch.DEFAULT_FIRMWARE = Path(self._tmp.name) / "rust_fw.bin"

    def tearDown(self):
        bram_patch.DEFAULT_FIRMWARE = self._real_default
        self._tmp.cleanup()
        if self._real_soc_run is None:
            sys.modules.pop("soc_run", None)
        else:
            sys.modules["soc_run"] = self._real_soc_run

    # -- helpers ---------------------------------------------------------------

    def _fake_soc_run(self, elf_exists=True, writes=FRESH, fails=False):
        """A stand-in whose `derive_bram_bin` rewrites DEFAULT_FIRMWARE.

        `refresh_firmware` only asks the ELF whether it exists, so the two cases
        are two real paths -- one that is there and one that is not -- rather than
        a Path subclass with a patched `exists`. Subclassing Path to lie about one
        method reaches into its internals and broke on 3.15; a path that genuinely
        does not exist cannot.
        """
        module = types.ModuleType("soc_run")
        module.ELF = (ROOT / "README.md" if elf_exists
                      else ROOT / "tmp" / "test-no-such-compiled-firmware.elf")

        def derive_bram_bin(emit):
            emit("Rust firmware: derived by the stand-in")
            if fails:
                return None, None
            bram_patch.DEFAULT_FIRMWARE.write_bytes(writes)
            return [".text"], [".rodata"]

        module.derive_bram_bin = derive_bram_bin
        sys.modules["soc_run"] = module
        return module

    def _args(self, **over):
        base = dict(firmware=bram_patch.DEFAULT_FIRMWARE, no_derive=False)
        base.update(over)
        return Namespace(**base)

    def _stale_default(self):
        """Put a deliberately stale image where the tool defaults to reading one."""
        bram_patch.DEFAULT_FIRMWARE.parent.mkdir(parents=True, exist_ok=True)
        bram_patch.DEFAULT_FIRMWARE.write_bytes(STALE)

    # -- the acceptance criterion ---------------------------------------------

    def test_a_stale_default_image_is_regenerated_before_it_is_read(self):
        """#155's headline: stale in, fresh out, and nothing silently proceeds."""
        self._stale_default()
        self._fake_soc_run(writes=FRESH)

        bram_patch.refresh_firmware(self._args(), self.handle)

        self.assertEqual(bram_patch.DEFAULT_FIRMWARE.read_bytes(), FRESH,
                         "the stale image survived the freshness check")

    def test_the_report_names_the_bytes_that_will_be_compared(self):
        """`0 of them changed` must be qualified by an identity, not stand alone."""
        self._stale_default()
        self._fake_soc_run(writes=FRESH)

        bram_patch.refresh_firmware(self._args(), self.handle)

        log = self.handle.getvalue()
        self.assertIn(hashlib.sha256(FRESH).hexdigest()[:12], log)
        self.assertIn(f"{len(FRESH)} bytes", log)
        self.assertIn("written ", log)

    def test_no_compiled_firmware_refuses_rather_than_patching_what_is_lying_there(self):
        self._stale_default()
        self._fake_soc_run(elf_exists=False)

        with self.assertRaises(bram_patch.Refuse) as caught:
            bram_patch.refresh_firmware(self._args(), self.handle)

        self.assertIn("no compiled firmware", str(caught.exception))
        self.assertEqual(bram_patch.DEFAULT_FIRMWARE.read_bytes(), STALE,
                         "a refusal must not have written anything")

    def test_a_failed_derivation_refuses_rather_than_falling_back(self):
        """objcopy failing must not leave the previous image looking usable."""
        self._stale_default()
        self._fake_soc_run(fails=True)

        with self.assertRaises(bram_patch.Refuse) as caught:
            bram_patch.refresh_firmware(self._args(), self.handle)

        self.assertIn("could not derive", str(caught.exception))

    # -- the deliberate escapes -----------------------------------------------

    def test_no_derive_leaves_the_image_alone_and_says_so_loudly(self):
        self._stale_default()
        self._fake_soc_run(writes=FRESH)

        bram_patch.refresh_firmware(self._args(no_derive=True), self.handle)

        self.assertEqual(bram_patch.DEFAULT_FIRMWARE.read_bytes(), STALE)
        log = self.handle.getvalue()
        self.assertIn("--no-derive", log)
        self.assertIn("#155", log)

    def test_an_explicit_firmware_is_the_callers_own_file_and_is_not_rewritten(self):
        self._stale_default()
        self._fake_soc_run(writes=FRESH)
        mine = bram_patch.DEFAULT_FIRMWARE.parent / "test-explicit-image.bin"
        mine.write_bytes(STALE)
        try:
            bram_patch.refresh_firmware(self._args(firmware=mine), self.handle)
            self.assertEqual(mine.read_bytes(), STALE)
            self.assertIn("given explicitly", self.handle.getvalue())
        finally:
            mine.unlink()

    def test_a_missing_explicit_firmware_refuses(self):
        self._fake_soc_run()
        missing = bram_patch.DEFAULT_FIRMWARE.parent / "test-absent-image.bin"
        self.assertFalse(missing.exists())

        with self.assertRaises(bram_patch.Refuse):
            bram_patch.refresh_firmware(self._args(firmware=missing), self.handle)


class RelTests(unittest.TestCase):
    """`rel` exists so reporting a path never crashes on an unusual invocation."""

    def test_a_path_inside_the_repo_is_reported_relative(self):
        self.assertEqual(bram_patch.rel(ROOT / "tmp" / "x.bin"), Path("tmp/x.bin"))

    def test_a_path_outside_the_repo_is_reported_whole_rather_than_raising(self):
        outside = Path("/etc/hostname")
        self.assertEqual(bram_patch.rel(outside), outside)


if __name__ == "__main__":
    unittest.main()
