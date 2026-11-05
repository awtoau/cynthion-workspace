"""usb_recover: the Cynthion's hub and port from its sysfs name, who else is on the hub, and the order of recovery."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import _paths  # noqa: E402,F401  (scripts/ subdirectories on sys.path)
import usb_recover  # noqa: E402


def make_device(sysfs, name, vid, pid, bus, dev, ttys=()):
    path = sysfs / name
    path.mkdir(parents=True)
    for field, value in (("idVendor", vid), ("idProduct", pid), ("busnum", bus), ("devnum", dev),
                         ("product", f"product-{name}")):
        (path / field).write_text(f"{value}\n")
    for tty in ttys:
        (path / f"{name}:1.0" / "tty" / tty).mkdir(parents=True)
    return path


def test_hub_and_port_from_the_sysfs_name():
    assert usb_recover.hub_and_port("1-1.3.1.3.3.3") == ("1-1.3.1.3.3", 3)
    assert usb_recover.hub_and_port("1-1.4") == ("1-1", 4)


def test_a_port_on_the_root_hub_is_hub_one_as_uhubctl_names_it():
    assert usb_recover.hub_and_port("1-3") == ("1", 3)


def test_devices_on_a_hub_are_listed_by_port(tmp_path):
    make_device(tmp_path, "1-1.3.1.3.3.1", "04b4", "521a", 1, 25)
    make_device(tmp_path, "1-1.3.1.3.3.3", "1d50", "615c", 1, 76)
    make_device(tmp_path, "1-1.3.1.3.3.2.1", "dead", "beef", 1, 99)      # one level down: not on the hub
    make_device(tmp_path, "1-1.3.1.4.1", "dead", "beef", 1, 98)          # another hub
    found = usb_recover.devices_on_hub("1-1.3.1.3.3", sysfs=tmp_path)
    assert sorted(found) == [1, 3]
    assert found[1]["vid"] == "04b4" and found[3]["devnum"] == 76


def test_cynthion_ports_are_found_by_vendor_and_product(tmp_path):
    make_device(tmp_path, "1-1.3.1.3.3.3", "1d50", "615c", 1, 76)
    make_device(tmp_path, "1-1.3.1.3.3.4", "1d50", "6180", 1, 78)
    make_device(tmp_path, "1-1.3.1.3.3.1", "04b4", "521a", 1, 25)
    assert usb_recover.find_cynthion(sysfs=tmp_path) == ["1-1.3.1.3.3.3", "1-1.3.1.3.3.4"]


def test_a_process_holding_a_usb_node_or_a_serial_port_of_a_device_is_found(tmp_path):
    sysfs, proc = tmp_path / "sys", tmp_path / "proc"
    device = make_device(sysfs, "1-1.3.1.3.3.1", "067b", "2303", 1, 25, ttys=("ttyUSB1",))
    other = make_device(sysfs, "1-1.3.1.3.3.2", "17ef", "30d1", 1, 26)
    (proc / "111" / "fd").mkdir(parents=True)
    (proc / "111" / "fd" / "4").symlink_to("/dev/ttyUSB1")
    (proc / "222" / "fd").mkdir(parents=True)
    (proc / "222" / "fd" / "3").symlink_to("/dev/bus/usb/001/026")
    (proc / "333" / "fd").mkdir(parents=True)
    (proc / "333" / "fd" / "3").symlink_to("/dev/null")
    devices = usb_recover.devices_on_hub("1-1.3.1.3.3", sysfs=sysfs)
    assert usb_recover.holders(devices.values(), proc=proc) == {111: "/dev/ttyUSB1",
                                                               222: "/dev/bus/usb/001/026"}
    assert device.exists() and other.exists()


def test_recovery_stops_at_the_first_step_that_brings_it_back():
    calls = []
    state = {"alive": False}

    def cycle():
        calls.append("cycle")
        state["alive"] = True

    def reset():
        calls.append("reset")

    ok, log = usb_recover.recover(lambda: state["alive"], [("port power cycle", cycle),
                                                           ("hub reset", reset)])
    assert ok and calls == ["cycle"]
    assert "port power cycle" in " ".join(log)


def test_a_healthy_board_is_left_alone():
    calls = []
    ok, log = usb_recover.recover(lambda: True, [("port power cycle", lambda: calls.append(1))])
    assert ok and calls == []


def test_all_steps_are_tried_in_order_and_failure_is_reported():
    calls = []
    ok, log = usb_recover.recover(lambda: False, [("port power cycle", lambda: calls.append("a")),
                                                  ("hub reset", lambda: calls.append("b"))])
    assert not ok and calls == ["a", "b"]


def test_a_step_that_refuses_is_skipped_with_its_reason():
    calls = []

    def refuses():
        raise usb_recover.Skip("pid 111 holds /dev/ttyUSB1")

    ok, log = usb_recover.recover(lambda: False, [("hub reset", refuses),
                                                  ("port power cycle", lambda: calls.append("b"))])
    assert not ok and calls == ["b"]
    assert any("skipped" in line and "pid 111" in line for line in log)


# --- a step that fails or hangs is a failed step, not a crash ------------------------

def test_a_step_that_fails_is_logged_and_the_probe_still_runs():
    state = {"alive": False}

    def fails_but_worked():
        state["alive"] = True
        raise usb_recover.Failed("timed out after 90 s")

    ok, log = usb_recover.recover(lambda: state["alive"], [("port power cycle", fails_but_worked)])
    assert ok
    assert any("failed" in line and "timed out" in line for line in log)


def test_a_failed_step_does_not_stop_the_next_one():
    calls = []

    def fails():
        raise usb_recover.Failed("rc=1")

    ok, log = usb_recover.recover(lambda: False, [("port power cycle", fails),
                                                  ("hub reset", lambda: calls.append("b"))])
    assert not ok and calls == ["b"]


def test_a_command_over_its_limit_is_a_failed_step():
    import pytest
    with pytest.raises(usb_recover.Failed, match="timed out"):
        usb_recover.run_step([sys.executable, "-c", "import time; time.sleep(5)"], limit=0.5,
                             prefix=())


def test_a_command_that_exits_non_zero_is_a_failed_step():
    import pytest
    with pytest.raises(usb_recover.Failed, match="rc=3"):
        usb_recover.run_step([sys.executable, "-c", "import sys; sys.exit(3)"], prefix=())


def test_a_command_that_succeeds_is_quiet():
    usb_recover.run_step([sys.executable, "-c", "pass"], prefix=())


# --- the ladder starts with the cable-free MCU reset ---------------------------------

def test_the_bootloader_reset_is_the_first_rung_and_the_hub_reset_can_be_left_out():
    names = [name for name, _ in usb_recover.build_steps("1-1.3.1.3.3", [3, 4], reset_hub_too=True)]
    assert names == ["bootloader reset", "port power cycle", "hub reset"]
    names = [name for name, _ in usb_recover.build_steps("1-1.3.1.3.3", [3, 4], reset_hub_too=False)]
    assert names == ["bootloader reset", "port power cycle"]


def test_the_bootloader_reset_is_skipped_when_apollo_is_not_on_the_bus(monkeypatch):
    import pytest
    monkeypatch.setattr(usb_recover, "find_apollo", lambda: None)
    with pytest.raises(usb_recover.Skip, match="not on the bus"):
        usb_recover.bootloader_reset()
