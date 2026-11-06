# Test gateware reusable by the CPU build

Goal: test peripherals attach to a CPU bus, not only to a host-driven `JTAGRegisterInterface` register file (which cannot be attached to a CPU).

## Mastership: one bus, one master per build

CPU and sideband as co-masters through an arbiter does not work: two masters that can both write a peripheral need a lock protocol, and neither a 6-LED display nor a flash controller has one.

| build | bus master | sideband role |
|---|---|---|
| test bitstream (no CPU) | the sideband responder | it is the master |
| CPU build | the CPU | an I2C peripheral on the CPU's bus, with an interrupt line |

- With a CPU the host (Apollo, FPGA_ADV) still reaches the sideband, but writes go through the CPU, which alone knows whether a peripheral is mid-transaction.
- The sideband as a CPU peripheral with an interrupt carries the port request, commands and user data: it replaces the EIC ([#95](https://github.com/awtoau/cynthion-workspace/issues/95), open). Modes and wire contention: [chips/cynone-sideband.md](chips/cynone-sideband.md#8-the-second-job-the-control-port-request).

## I2C: one controller, three pin-sets

- r1.4 has three physically separate I2C buses because both FUSB302Bs sit at `0x22`. Bus table, pins, addresses: [hardware.md](hardware.md#the-buses); chips: [chips/fusb302b-type-c.md](chips/fusb302b-type-c.md), [chips/pac1954-power-monitor.md](chips/pac1954-power-monitor.md).
- One master with a 2-bit bus select driving three pin-sets replaces three controllers ([#98](https://github.com/awtoau/cynthion-workspace/issues/98), [#121](https://github.com/awtoau/cynthion-workspace/issues/121), done, on silicon).
  - `gateware/soc/peripherals/i2c_mux.py`: select + four Type-C signals. `i2c_master.py` has an `idle` output so the select cannot move mid-transfer.
  - Each FUSB302B `int` and each DPO2036 `FAULTB` has its own interrupt source ([#135](https://github.com/awtoau/cynthion-workspace/issues/135); `FAULTB` edge-triggered): one controller means one device at a time on the bus, not one source. See [architecture.md](architecture.md), [soc-interrupts.md](soc-interrupts.md).
  - The handler masks, it does not clear: clearing needs ~1 ms of I2C on the controller the foreground also uses. Normal context clears the device, acknowledges the pending bit, re-enables the source. Level-trap detail: [chips/fusb302b-type-c.md](chips/fusb302b-type-c.md#interrupts).
- LEDs are a CSR register, not a fake I2C device: six wires in the same fabric; a serial protocol would add a state machine, a byte-time of latency and an error path to one combinational assignment. Uniformity is worth paying for at a bus boundary, not inside the chip.

## Peripherals as bus components

Each peripheral has a CSR interface; the register file becomes a bridge so one implementation serves both builds:

    host over JTAG -> JTAG-to-Wishbone bridge -> |
                                                 |-> peripheral (CSR)
    CPU                                        -> |

The SoC reaches its console this way already.

Order (cheapest, most load-bearing first): multiplexed I2C master (done) -> LEDs as CSR with override/release semantics -> flash (QSPI, most bus-shaped).

## Scope

- All three USB ports as 480 Mbps CDC: proves all three can come up; the shipping bitstream configures ports differently. Largest item: three device stacks against 24288 LUTs with a CPU needing ~6000.
- HyperRAM: register interface on the [#581](https://github.com/awtoau/cynthion-workspace/issues/581) core at CK 180, see [docs/chips/hyperram/soc-peripheral.md](chips/hyperram/soc-peripheral.md) ([#588](https://github.com/awtoau/cynthion-workspace/issues/588)).
- FUSB302B and die temperature over the sideband: `gateware/probes/pins/fusb302_id.py` reads both controllers; missing are sideband commands ([chips/cynone-sideband.md](chips/cynone-sideband.md#4-commands)). Temperature needs the `DTR` primitive, instantiated nowhere.
- Flash benchmarking driven by the CPU.

## Area reference

- SoC area: [soc-size-review.md](soc-size-review.md). Sideband test bitstream with responder, flash and power monitor: 123920 bytes.
- Whether three USB stacks + CPU + caches + HyperRAM fit an LFE5U-12F has not been synthesised together.
