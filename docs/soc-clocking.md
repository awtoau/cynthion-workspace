# SoC clocking

Clock plan, owned here. Other docs link, they do not restate.

## Plan

| domain | source | value |
|---|---|---|
| `usb` | discrete 60 MHz oscillator (A8) passed straight through; FPGA sources the ULPI clock (`clk_dir='o'` on all three PHY resources in `cynthion_r1_4.py`) | 60.000 MHz exact |
| `sync` (CPU, Wishbone) | PLL from the oscillator; `SYNC_MHZ` | default 60 (`CYNTHION_SYNC_MHZ`, `gateware/soc/variant.py:64`) |
| `fast` | second output (CLKOS, not CLKOS2) of the SAME PLL as `sync` | exactly 2x its fabric domain (HyperRAM DQS 4:1 gearing) |
| HyperRAM CK | `hyperram_clocks.HyperRAMDomains`: second PLL, oscillator as reference; not wired into `top.py` yet (#230) | see [chips/hyperram/w956a8.md](chips/hyperram/w956a8.md) |

- Code: `gateware/soc/clocks.py` (`SocClocks`), `gateware/soc/hyperram_clocks.py`.
- 60 MHz appears in `clocks.py` once, as the USB PHY's requirement; it is not the CPU's number.
- `sync` choices, 63-130 MHz: 13 integer frequencies in PLL spec from a 60 MHz reference: `70 72 75 80 84 90 96 100 105 108 110 120 130`. The other 55 divide the reference below `PFD_MIN_MHZ` (jitter not guaranteed).
- CPU working ceiling is **unmeasured**: [#110](https://github.com/awtoau/cynthion-workspace/issues/110) (Diamond vs nextpnr). nextpnr achieved 92 MHz at a 100 MHz request and 96 at 110; whether either runs correctly is open.
  - Any "RISC-V tops out ~75 MHz" claim rests on a withdrawn measurement (`StreamBuffer` bug, now fixed; ladder not re-run).

## PLL divider trap (why `usb` is not on the PLL)

- `VariableClockDomainGenerator` (`repos/apollo/apollo_fpga/gateware/variable_clock.py`) derives `usb` by `usb_div = int(round(vco_mhz / 60.0))` from the same VCO as `sync`.
- Only `sync` = 60, 100, 120 MHz give exactly 60 MHz in 60-130 MHz (2 MHz steps); the rest are 1-5% off (e.g. 90 MHz -> `usb` 63.000, +5%).
- ULPI is source-synchronous with no start bit or resync: a 90 MHz build places, packs, configures and never appears on the USB bus, while a 100 MHz build enumerates. It looks identical to a missed-timing design.
- `variable_clock.py` refuses to build outside 0.5%. Never put the PHY back on a PLL.
- nextpnr states it in `top.tim`: `Derived frequency constraint of 63.0 MHz for net aux_phy_0__clk__o`.

## Reproducing and harness gotchas

    ./debris/scripts/nextpnr_allow_fail_ladder.py --frequencies 90 100 110
    ./debris/scripts/riscv_verify_bitstream.py tmp/nextpnr_allow_fail/100/top.bit

- Ladder re-places the same netlist with `--timing-allow-fail`, packs it, flashes it; effort identical, only the verdict changes.
- Boot banner is transient: enumeration after reconfigure takes ~0.47 s and the banner prints inside that window. Configure, wait for tty, then open the port misses it.
- Holding the port open across reconfigure fails: the node is torn down (`SerialException`).
- `riscv_verify_bitstream.py` reopens as fast as the node appears and reports INCONCLUSIVE on a lost race; it passes the 60 MHz control. A harness must pass its own control.
- Re-run needs a readout that is not the console.

Links: [debris/scripts/nextpnr_allow_fail_ladder.py](../debris/scripts/nextpnr_allow_fail_ladder.py), [debris/scripts/riscv_verify_bitstream.py](../debris/scripts/riscv_verify_bitstream.py). `diamond_riscv_ladder.py` no longer exists in the tree.
Withdrawn ladder results and the full divider table: [history/soc-usb.md](history/soc-usb.md).
