# ECP5 Usage Report

This report tracks ECP5 resource and timing growth as RV64 features are added.

## Latest Snapshot

- Timestamp: 2026-07-22T01:14:37+00:00
- Commit: 4b94ea5
- Tag: soc_x64_sv_rvm_rvc_rdtime_clint_uart_t16
- Stage: MicroSoc x64 + CLINT + UART
- Device: LFE5U-12F BG256 speed 8
- LUT: 9989/24288 (41.13%)
- FF: 4368/24288 (17.98%)
- BRAM: 8/56 blocks (14.29%), 18.00/126.00 KiB
- Fmax: 68.01 MHz (target 25.00 MHz, pass=yes)
- Notes: MicroSoc x64 + CLINT + UART

## Delta Vs Previous

- LUT percent delta: +0.00%
- FF percent delta: +0.00%
- BRAM percent delta: +0.00%
- BRAM blocks delta: +0 blocks
- BRAM KiB delta: +0.00 KiB
- Fmax delta: +0.00 MHz

## Core History

| # | tag | LUT % | FF % | BRAM % | Fmax MHz | notes |
|---|---|---:|---:|---:|---:|---|

## SoC History

| # | tag | LUT % | FF % | BRAM % | Fmax MHz | notes |
|---|---|---:|---:|---:|---:|---|
| 1 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t16 | 41.13 | 17.98 | 14.29 | 68.01 | MicroSoc x64 + CLINT + UART |
| 2 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t16 | 41.13 | 17.98 | 14.29 | 68.01 | MicroSoc x64 + CLINT + UART |
| 3 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t16 | 41.13 | 17.98 | 14.29 | 68.01 | MicroSoc x64 + CLINT + UART |
| 4 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t16 | 41.13 | 17.98 | 14.29 | 68.01 | MicroSoc x64 + CLINT + UART |
| 5 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t16 | 41.13 | 17.98 | 14.29 | 68.01 | MicroSoc x64 + CLINT + UART |
| 6 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t16 | 41.13 | 17.98 | 14.29 | 68.01 | MicroSoc x64 + CLINT + UART |

## Core Cumulative Matrix

Legend: rows are ordered by enabled feature count (fewest to most), then by feature bit pattern.

| # | fetch L1 | lsu L1 | btb | gshare | ras | dual issue | clint | uart | LUT % | FF % | BRAM % | Fmax MHz |
|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|

## SoC Cumulative Matrix

Legend: rows are ordered by enabled feature count (fewest to most), then by feature bit pattern.

| # | fetch L1 | lsu L1 | btb | gshare | ras | dual issue | clint | uart | LUT % | FF % | BRAM % | Fmax MHz |
|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|
| 1 |  |  |  |  |  |  | yes | yes | 41.13 | 17.98 | 14.29 | 68.01 |

## Trend Graphs

```mermaid
xychart-beta
  title "LUT Usage Percent"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "percent" 0 --> 86.05
  line [41.13, 34.64, 40.76, 41.79, 65.58, 74.83]
```

```mermaid
xychart-beta
  title "FF Usage Percent"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "percent" 0 --> 36.66
  line [17.98, 13.24, 16.96, 19.29, 25.98, 31.88]
```

```mermaid
xychart-beta
  title "BRAM Usage Percent"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "percent" 0 --> 49.29
  line [14.29, 3.57, 17.86, 25.00, 28.57, 42.86]
```

```mermaid
xychart-beta
  title "BRAM Blocks Used"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "blocks" 0 --> 27.60
  line [8.00, 2.00, 10.00, 14.00, 16.00, 24.00]
```

```mermaid
xychart-beta
  title "Fmax MHz"
  x-axis [1, 2, 3, 4, 5, 6]
  y-axis "MHz" 0 --> 103.42
  line [68.01, 54.56, 89.93, 72.24, 57.00, 64.13]
```


## How To Update

1. Run the build flow (`42_run_vexii_nextpnr_timing.py`).
2. Append a datapoint (`43_record_ecp5_metrics.py --tag ...`).
3. Regenerate this report (`44_generate_ecp5_report.py`).

Or run all three steps together:

- `python3 riscv-64/scripts/dev.py --tag <change-name> --notes "what changed"`
