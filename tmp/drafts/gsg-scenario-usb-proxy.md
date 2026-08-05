# Scenario 4: USB proxy / MITM

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §4.

## What it is

Forward USB transactions between a target host and a real device attached to the control
computer, with Python filters able to observe or rewrite them in flight. GSG documents it
as a distinct Cynthion scenario with its own page.

## What implements it upstream

**Nothing on the device.** The bitstream and firmware are byte-identical to scenario 3 —
`facedancer.bit` + `moondancer.bin`. The proxy is entirely host-side:

- `facedancer: facedancer/proxy.py::USBProxyDevice`
- `facedancer: facedancer/filters/` — `USBProxySetupFilters`, `USBProxyPrettyPrintFilter`
- Example: `cynthion: cynthion/python/examples/facedancer-usbproxy.py`

`USBProxyDevice` substitutes for an emulated device class; every verb it uses is a verb
scenario 3 already provides.

## Hardware it needs

CONTROL to the control computer, TARGET to the host being proxied, and the **real device**
plugged into a port of the control computer. Three connections and two hosts. On macOS it
needs root, to claim the proxied device away from the OS.

## What porting it would require

**Nothing, beyond scenario 3.** If our SoC presents the moondancer descriptor set and
implements the `moondancer` GCP class faithfully, `USBProxyDevice` works without a line
changed on either side.

The one thing worth checking rather than assuming: proxying is more latency-sensitive than
emulation, because a real device is waiting at the other end and the host's timeouts are
not adjustable. Upstream hits this too — it is why the proxy documentation warns about
enumeration failures. Any per-verb overhead we add relative to Moondancer shows up here
first, so this scenario doubles as the performance regression test for scenario 3.

## How it would be tested

- **QEMU: no.** Nothing device-side is specific to this scenario, so there is nothing new
  to test in QEMU that scenario 3 does not already cover.
- **Simulation: no.** Same reason.
- **Hardware: the only tier, and it needs the most of it** — two hosts and a third real
  device. Of every scenario here, this has the worst test story, and that is inherent
  rather than something we could fix with better tooling.

Practically: this should be a manual acceptance check after scenario 3, with a named
reference device, not an automated test anyone pretends to keep green.

## Verdict

**Free once scenario 3 lands** — but effectively untestable in CI. Track it as an
acceptance criterion for scenario 3 rather than as work in its own right.
