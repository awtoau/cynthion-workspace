"""The system Python stack matches the pins in scripts/toolchain_env.py.

A blanket `pip install -U` once put luna-usb back to 0.2.3 (id()-named endpoints, builds not
reproducible) and pygreat to 2024.0.5 without anything failing for an hour.
"""

import sys
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from toolchain_env import LUNA_PIN  # noqa: E402


def test_luna_usb_is_the_pinned_commit():
    version = metadata.version("luna-usb")
    # `.dirty` is the amaranth-main probe's patched luna (patches/luna-amaranth-0.6.patch on the pin).
    assert f"git.{LUNA_PIN[:8]}" in version, (
        f"luna-usb {version} is not the pinned {LUNA_PIN[:8]}: "
        f'pip install --no-deps "luna-usb @ git+https://github.com/greatscottgadgets/luna.git@{LUNA_PIN}"')


def test_pygreat_is_from_libgreat_main_not_the_2024_release():
    assert int(metadata.version("pygreat").split(".")[0]) >= 2026


def test_amaranth_is_the_release_the_gateware_is_checked_on():
    assert metadata.version("amaranth") in ("0.5.10", "0.6.0.dev140")
