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
- `sync` choices: `reachable()` from the 60 MHz reference gives `60 70 72 75 80 84 90 96 100 105 108 110 120 130` (and 132, 135 past the ceiling). The other integers divide the reference below `PFD_MIN_MHZ` (jitter not guaranteed).
- **Why `sync` is 60:** it equals the oscillator (`solve_pll(60)` = VCO 420 MHz, feedback 7, output div 7), so `sync` and `usb` run at one rate. It is the bottom of the menu, not below it. **Decision (Dan, 2026-11-02): run `sync` at 60 MHz for now, because it is simple** (one rate for CPU and PHY). Not a limit; faster rungs are not being pursued ([#462](https://github.com/awtoau/cynthion-workspace/issues/462)).
- CPU working ceiling is **unmeasured**: [#110](https://github.com/awtoau/cynthion-workspace/issues/110) (Diamond vs nextpnr). nextpnr achieved 92 MHz at a 100 MHz request and 96 at 110; whether either runs correctly is open.
  - Any "RISC-V tops out ~75 MHz" claim rests on a withdrawn measurement (`StreamBuffer` bug, now fixed; ladder not re-run).

## Rated / allowed / actual / gap

| domain | rated | allowed here | actual | gap |
|---|---|---|---|---|
| `usb` | 60.000 (ULPI) | 60.000 only | 60.000 oscillator | none |
| `sync` | PLL menu above | `SYNC_CEILING_MHZ` | 60 | default, unmeasured; CPU ceiling is [#110](https://github.com/awtoau/cynthion-workspace/issues/110) |
| `fast` | 2x `sync` | integer divide of the same VCO | 2x `sync` when DQS / `FLASH_PHY_FAST` | forced by DQS (parked) |
| HyperRAM CK | part rated 166 | second PLL (`hyperram_clocks.py`) | not in the shipping design | [#230](https://github.com/awtoau/cynthion-workspace/issues/230), [#432](https://github.com/awtoau/cynthion-workspace/issues/432) |
| `jtag_tck` | n/a | 405 kHz driven | unconstrained in nextpnr | [#461](https://github.com/awtoau/cynthion-workspace/issues/461) |

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
