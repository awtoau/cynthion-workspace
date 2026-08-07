# Retired docs

Superseded documents kept for the reasoning they contain, not as current
guidance. Nothing here describes how the code works today.

## hyperram-burst-test-readme.md

Retired 2026-08-05, from `ecp5-test/hyperram/HYPERRAM_TEST_README.md`. The only
index that directory had, and it covered one of its eleven programs — anyone
opening the directory read it as a description of the whole thing.

Superseded by [`../../ecp5-test/hyperram/README.md`](../../ecp5-test/hyperram/README.md),
which points at `./dev.py hyperram`, and by the inventory in
[`../../ecp5-test/README.md`](../../ecp5-test/README.md). Its own subject,
`hyperram_burst_test.py`, is recorded there as retirable: the simulation fails
verification and exits zero, and it has never run on silicon.

Kept rather than deleted for its "expected results" table — 150-200 ns single
word, 2-3 Gbps burst-4. Those were **predictions, never measurements**, and the
measured figures are two to five times off them. A guess that was written down
as an expectation is worth being able to find again. See
[#189](https://github.com/awtoau/cynthion-workspace/issues/189).

## apollo_moondancer_uart_watchdog_design.md, apollo_moondancer_uart_watchdog_workstream.md

Retired 2026-07-28. Proposed an Apollo↔moondancer watchdog over a UART moved to
PA08/PA09, to free the JTAG pins.

Dead for two independent reasons:

- **The relocation is impossible on d11.** PA08 is `FPGA_PROGRAM` and PA09 is
  `PHY_RESET` + `FPGA_ADV`; moving the UART there would cost Apollo the ability
  to reset and detect the FPGA. See
  [#65](https://github.com/awtoau/cynthion-workspace/issues/65). The design doc
  carries a correction banner saying so, but its own replacement proposal (SPI on
  SERCOM2, [#62](https://github.com/awtoau/cynthion-workspace/issues/62)) was
  also not what happened.
- **The problem it solved is solved.** Apollo↔FPGA runtime communication is the
  half-duplex sideband on the existing FPGA_ADV wire, documented in
  [`docs/sideband.md`](../../docs/sideband.md) and shipping. No pin relocation,
  no board change.

Kept because the supervision model and failure-mode analysis were worked out
here and are not recoverable from the code. Closes the duplication complaint in
[#57](https://github.com/awtoau/cynthion-workspace/issues/57).

## fpga-adv-sideband.md, sideband-soak-results.md, sideband-review.md

Retired 2026-08-05, superseded by [`docs/sideband.md`](../../docs/sideband.md),
which is now the single canonical reference for the FPGA_ADV link. The durable
content of all three is folded in.

Kept rather than deleted because each carries reasoning the canonical doc states
only as a conclusion: `fpga-adv-sideband.md` has the original design argument
(including the push-pull case that #88 later overturned — see
[§13](../../docs/sideband.md#13-where-earlier-documents-were-wrong)), plus a
seven-phase test methodology and acceptance criteria that belong in an issue
rather than in `docs/`; `sideband-soak-results.md` has the full soak reasoning
behind the two-line result table; `sideband-review.md` has the flash-budget
arithmetic and symbol-level sizing behind
[#182](https://github.com/awtoau/cynthion-workspace/issues/182), and the
per-option evaluation behind
[#95](https://github.com/awtoau/cynthion-workspace/issues/95). All three are
written as status or as a review of open questions, which
[`docs/README.md`](../../docs/README.md) puts in an issue.

## serial_communication_redesign_decisions.md

Retired 2026-08-05. A May-2026 design review headed "Status: Design Review", with a
current-state analysis and a proposed fix — status and plan, which
[`docs/README.md`](../../docs/README.md) puts in an issue.

Dead for the same two reasons as the watchdog docs above: its central change was to
move the Apollo↔moondancer UART onto **PA08/PA09**, which is impossible — PA08 is
`FPGA_PROGRAM` and PA09 is `FPGA_ADV`
([#65](https://github.com/awtoau/cynthion-workspace/issues/65)) — and the
runtime channel it wanted is the sideband on the existing wire,
[`docs/sideband.md`](../../docs/sideband.md). Its account of FPGA_ADV as
edge-counting-only predates UART mode.

Kept for the commit archaeology: it identifies `4208bc6` (April 2024) as where the
advertiser and USB port switching entered, and reconstructs what the serial path was
meant to be. That is not recoverable from current code.

## hyperram-next-step.md

Retired 2026-08-05. Written as "state as of 2026-08-03" with a next-step list, so
it is the exact shape [`docs/README.md`](../../docs/README.md) excludes; and it had
already aged into a contradiction with
[`docs/linux-on-cynthion.md`](../../docs/linux-on-cynthion.md), which still said the
window did not coalesce bursts. The durable half — the burst-coalescing
implementation, the 748-word tCSM cap, the 51-CK versus 336-CK simulation result, and
why the CSR staging port is kept — is now in
[`docs/chips/hyperram/w956a8.md`](../../docs/chips/hyperram/w956a8.md). The remaining
work is [#90](https://github.com/awtoau/cynthion-workspace/issues/90).
