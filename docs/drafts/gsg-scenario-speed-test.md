# Scenario 9: Bulk throughput test

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §9.

## What it is

A bulk IN/OUT speed test used to characterise the USB data path. Not a user-facing feature
— it is how upstream finds out whether the data path is as fast as it should be, and it is
in the factory test set.

## What implements it upstream

- Gateware: `luna: luna/gateware/applets/speed_test.py`. Also shipped prebuilt as
  `speedtest.bit` in the `cynthion-test` factory repo.
- Firmware alternative: `cynthion: firmware/moondancer/examples/bulk_speed_test.rs`, driven
  by `firmware/moondancer/scripts/bulk_speed_test.py`.
- Descriptor strings: manufacturer "Luna Project", product "Bulk Speed Test"
  (`cynthion: shared/usb.toml`).

Note there are two of these — a pure-gateware applet and a SoC firmware example. They
measure different things: the applet measures the USB path, the firmware example measures
the USB path *plus* the CPU's ability to feed it.

## Hardware it needs

CONTROL, or CONTROL plus AUX depending on the variant. One or two cables.

## What porting it would require

Very little, and part of it is already done.

**We have already measured 195.4 Mbps CDC-ACM loopback** through `USBSerialDevice` on
`aux_phy` (`gateware/probes/usb_serial/usb_serial.py`, instantiated in `top.py`).
That is a throughput measurement on this platform's USB path, taken with a design that is
in the tree today.

What upstream's applet adds over that is a bulk endpoint with no CDC framing and a
host-side script that reports a number reproducibly. Both are small.

The design note in `usb_serial.py` is worth carrying over: `max_packet_size` defaults to
64 (the full-speed bulk limit), and leaving it there enumerates at high speed while moving
data at roughly an eighth of the achievable rate. That is the precise failure mode a speed
test exists to catch, and we have already been bitten by the class of it.

The pure-gateware applet variant needs no CPU and no libgreat. The firmware variant needs
scenario 3's peripherals and is not worth doing separately.

## How it would be tested

- **QEMU: no.** No USB in `-M virt`.
- **Simulation: partly, and misleadingly.** A simulation can prove the endpoint moves bytes
  but cannot produce a throughput number that means anything — sim time is not wall time.
  Do not build a "speed test" in `sim` and report its figure; that is exactly the mistake
  of quoting a host TSC as a device measurement.
- **Hardware: the only tier that can produce the number**, and the number is the whole
  point.

The right shape is a hardware measurement, recorded with its conditions, in `docs/`. A
throughput figure without the packet size, the speed negotiated and the host it was
measured against is not a result.

## Verdict

**Portable**, and largely already achieved. Its value is as a regression detector for the
USB path once scenario 1 or 3 exists — a number that moves is the earliest warning that
something in the data path got slower.
