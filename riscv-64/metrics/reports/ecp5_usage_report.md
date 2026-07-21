# ECP5 Usage Report

This report tracks ECP5 resource and timing growth as RV64 features are added.

## Latest Snapshot

- Timestamp: 2026-07-21T15:08:08+00:00
- Commit: 8b987e8
- Tag: runner-logscan-smoke
- Device: LFE5U-12F BG256 speed 8
- LUT: 2351/24288 (9.68%)
- FF: 895/24288 (3.68%)
- BRAM: 0/56 blocks (0.00%), 0.00/126.00 KiB
- Fmax: 95.07 MHz (target 25.00 MHz, pass=yes)
- Notes: full runner with log scan

## Delta Vs Previous

- LUT percent delta: +0.00%
- FF percent delta: +0.00%
- BRAM percent delta: n/a
- BRAM blocks delta: +0 blocks
- BRAM KiB delta: +0.00 KiB
- Fmax delta: +0.00 MHz

## Trend Graphs

```mermaid
xychart-beta
  title "LUT Usage Percent"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "percent" 0 --> 11.13
  line [9.68, 9.68, 9.68, 9.68, 9.68, 9.68]
```

```mermaid
xychart-beta
  title "FF Usage Percent"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "percent" 0 --> 4.23
  line [3.68, 3.68, 3.68, 3.68, 3.68, 3.68]
```

```mermaid
xychart-beta
  title "BRAM Usage Percent"
  x-axis [3, 4, 5, 6]
  y-axis "percent" 0 --> 1.00
  line [0.00, 0.00, 0.00, 0.00]
```

```mermaid
xychart-beta
  title "BRAM Blocks Used"
  x-axis [3, 4, 5, 6]
  y-axis "blocks" 0 --> 1.00
  line [0.00, 0.00, 0.00, 0.00]
```

```mermaid
xychart-beta
  title "Fmax MHz"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "MHz" 0 --> 109.33
  line [95.07, 95.07, 95.07, 95.07, 95.07, 95.07]
```

## History

| # | timestamp | commit | tag | LUT % | FF % | BRAM % | BRAM blocks | BRAM KiB | Fmax MHz | timing pass |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 2026-07-21T14:59:27+00:00 | 8b987e8 | baseline | 9.68 | 3.68 |  |  |  | 95.07 | yes |
| 2 | 2026-07-21T15:01:00+00:00 | 8b987e8 | baseline-bram | 9.68 | 3.68 |  |  |  | 95.07 | yes |
| 3 | 2026-07-21T15:01:14+00:00 | 8b987e8 | baseline-bram-fixed | 9.68 | 3.68 | 0.00 | 0/56 | 0.00/126.00 | 95.07 | yes |
| 4 | 2026-07-21T15:01:56+00:00 | 8b987e8 | smoke-de | 9.68 | 3.68 | 0.00 | 0/56 | 0.00/126.00 | 95.07 | yes |
| 5 | 2026-07-21T15:02:57+00:00 | 8b987e8 | dev-py-smoke | 9.68 | 3.68 | 0.00 | 0/56 | 0.00/126.00 | 95.07 | yes |
| 6 | 2026-07-21T15:08:08+00:00 | 8b987e8 | runner-logscan-smoke | 9.68 | 3.68 | 0.00 | 0/56 | 0.00/126.00 | 95.07 | yes |

## How To Update

1. Run the build flow (`42_run_vexii_nextpnr_timing.py`).
2. Append a datapoint (`43_record_ecp5_metrics.py --tag ...`).
3. Regenerate this report (`44_generate_ecp5_report.py`).

Or run all three steps together:

- `python3 riscv-64/scripts/dev.py --tag <change-name> --notes "what changed"`
