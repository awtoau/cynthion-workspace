# ECP5 Usage Report

This report tracks ECP5 resource and timing growth as RV64 features are added.

## Latest Snapshot

- Timestamp: 2026-07-22T00:31:02+00:00
- Commit: b4f9b4d
- Tag: core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_t32
- Stage: core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual
- Device: LFE5U-12F BG256 speed 8
- LUT: 9753/24288 (40.16%)
- FF: 3968/24288 (16.34%)
- BRAM: 12/56 blocks (21.43%), 27.00/126.00 KiB
- Fmax: 60.71 MHz (target 25.00 MHz, pass=yes)
- Notes: core exhaustive i4k + d4k + btb + gshare + ras + dual

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
| 1 | core_x64_sv_rvm_rvc_rdtime_i4k_t32 | 34.64 | 13.24 | 3.57 | 54.56 | core x64 + 4KiB I-cache |
| 2 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_t32 | 40.76 | 16.96 | 17.86 | 89.93 | core x64 + 4KiB I-cache + 4KiB D-cache |
| 3 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_t32 | 41.79 | 19.29 | 25.00 | 72.24 | core x64 + i4k + d4k + btb + gshare + ras |
| 4 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_t32 | 65.58 | 25.98 | 28.57 | 57.00 | core x64 + i4k + d4k + bpred + dual-issue |
| 5 | core_x64_sv_rvm_rvc_rdtime_base_t32 | 32.66 | 12.41 | 0.00 | 44.82 | core exhaustive base |
| 6 | core_x64_sv_rvm_rvc_rdtime_i4k_t32 | 34.64 | 13.24 | 3.57 | 54.56 | core exhaustive i4k |
| 7 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_t32 | 40.76 | 16.96 | 17.86 | 89.93 | core exhaustive i4k + d4k |
| 8 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_dual_t32 | 61.55 | 21.81 | 17.86 | 71.55 | core exhaustive i4k + d4k + dual |
| 9 | core_x64_sv_rvm_rvc_rdtime_base_t32 | 32.66 | 12.41 | 0.00 | 44.82 | core exhaustive base |
| 10 | core_x64_sv_rvm_rvc_rdtime_base_t32 | 32.66 | 12.41 | 0.00 | 44.82 | core exhaustive base |
| 11 | core_x64_sv_rvm_rvc_rdtime_base_t32 | 32.66 | 12.41 | 0.00 | 44.82 | core exhaustive base |
| 12 | core_x64_sv_rvm_rvc_rdtime_i4k_t32 | 34.64 | 13.24 | 3.57 | 54.56 | core exhaustive i4k |
| 13 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_t32 | 40.76 | 16.96 | 17.86 | 89.93 | core exhaustive i4k + d4k |
| 14 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_dual_t32 | 61.55 | 21.81 | 17.86 | 71.55 | core exhaustive i4k + d4k + dual |
| 15 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_t32 | 40.66 | 18.33 | 21.43 | 67.55 | core exhaustive i4k + d4k + btb |
| 16 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_dual_t32 | 63.15 | 24.27 | 25.00 | 64.03 | core exhaustive i4k + d4k + btb + dual |
| 17 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_t32 | 41.02 | 18.50 | 21.43 | 71.27 | core exhaustive i4k + d4k + btb + ras |
| 18 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_t32 | 41.34 | 19.11 | 25.00 | 63.73 | core exhaustive i4k + d4k + btb + gshare |
| 19 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_dual_t32 | 63.95 | 24.44 | 25.00 | 61.77 | core exhaustive i4k + d4k + btb + ras + dual |
| 20 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_dual_t32 | 64.46 | 25.81 | 28.57 | 61.46 | core exhaustive i4k + d4k + btb + gshare + dual |
| 21 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_t32 | 41.79 | 19.29 | 25.00 | 72.24 | core exhaustive i4k + d4k + btb + gshare + ras |
| 22 | core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_t32 | 65.58 | 25.98 | 28.57 | 57.00 | core exhaustive i4k + d4k + btb + gshare + ras + dual |
| 23 | core_x32_sv_rvm_rvc_rdtime_base_t32 | 20.14 | 7.03 | 0.00 | 49.25 | core exhaustive base |
| 24 | core_x32_sv_rvm_rvc_rdtime_i4k_t32 | 21.68 | 7.83 | 3.57 | 52.87 | core exhaustive i4k |
| 25 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_t32 | 26.28 | 10.52 | 10.71 | 84.60 | core exhaustive i4k + d4k |
| 26 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_dual_t32 | 40.03 | 13.76 | 10.71 | 70.81 | core exhaustive i4k + d4k + dual |
| 27 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_t32 | 27.25 | 11.68 | 14.29 | 67.46 | core exhaustive i4k + d4k + btb |
| 28 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_dual_t32 | 41.98 | 15.88 | 17.86 | 58.42 | core exhaustive i4k + d4k + btb + dual |
| 29 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_t32 | 27.26 | 11.82 | 14.29 | 63.12 | core exhaustive i4k + d4k + btb + ras |
| 30 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_t32 | 28.47 | 12.47 | 17.86 | 66.26 | core exhaustive i4k + d4k + btb + gshare |
| 31 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_dual_t32 | 42.52 | 16.02 | 17.86 | 65.32 | core exhaustive i4k + d4k + btb + ras + dual |
| 32 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_dual_t32 | 44.15 | 17.42 | 21.43 | 57.84 | core exhaustive i4k + d4k + btb + gshare + dual |
| 33 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_t32 | 28.40 | 12.61 | 17.86 | 72.80 | core exhaustive i4k + d4k + btb + gshare + ras |
| 34 | core_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_t32 | 45.12 | 17.56 | 21.43 | 59.95 | core exhaustive i4k + d4k + btb + gshare + ras + dual |
| 35 | core_x32_rva_rvm_rvc_rdtime_base_t32 | 15.49 | 6.54 | 0.00 | 79.11 | core exhaustive base |
| 36 | core_x32_rva_rvm_rvc_rdtime_i4k_t32 | 16.51 | 7.17 | 3.57 | 92.58 | core exhaustive i4k |
| 37 | core_x32_rva_rvm_rvc_rdtime_i4k_d4k_t32 | 20.35 | 9.29 | 10.71 | 98.44 | core exhaustive i4k + d4k |
| 38 | core_x32_rva_rvm_rvc_rdtime_i4k_d4k_dual_t32 | 35.14 | 12.53 | 10.71 | 79.30 | core exhaustive i4k + d4k + dual |
| 39 | core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_ras_t32 | 40.16 | 16.34 | 21.43 | 60.71 | core exhaustive i4k + d4k + btb + ras |
| 40 | core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_t32 | 40.16 | 16.34 | 21.43 | 60.71 | core exhaustive i4k + d4k + btb + gshare + ras |
| 41 | core_x32_rva_rvm_rvc_rdtime_base_t32 | 40.16 | 16.34 | 21.43 | 60.71 | core exhaustive base |
| 42 | core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_t32 | 40.16 | 16.34 | 21.43 | 60.71 | core exhaustive i4k + d4k + btb + gshare |
| 43 | core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_t32 | 40.16 | 16.34 | 21.43 | 60.71 | core exhaustive i4k + d4k + btb |
| 44 | core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_t32 | 40.16 | 16.34 | 21.43 | 60.71 | core exhaustive i4k + d4k + btb + gshare + ras + dual |

## SoC History

| # | tag | LUT % | FF % | BRAM % | Fmax MHz | notes |
|---|---|---:|---:|---:|---:|---|
| 1 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t32 | 41.13 | 17.98 | 14.29 | 68.01 | MicroSoc x64 + CLINT + UART |
| 2 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_clint_uart_t32 | 74.83 | 31.88 | 42.86 | 64.13 | microsoc x64 + core stack + clint + uart |
| 3 | soc_x64_sv_rvm_rvc_rdtime_clint_uart_t32 | 41.13 | 17.98 | 14.29 | 68.01 | soc exhaustive base + clint + uart |
| 4 | soc_x64_sv_rvm_rvc_rdtime_i4k_clint_uart_t32 | 42.68 | 18.98 | 17.86 | 74.02 | soc exhaustive i4k + clint + uart |
| 5 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_clint_uart_t32 | 47.36 | 21.74 | 32.14 | 87.57 | soc exhaustive i4k + d4k + clint + uart |
| 6 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_dual_clint_uart_t32 | 71.22 | 27.02 | 32.14 | 78.02 | soc exhaustive i4k + d4k + dual + clint + uart |
| 7 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_clint_uart_t32 | 49.99 | 23.39 | 35.71 | 73.40 | soc exhaustive i4k + d4k + btb + clint + uart |
| 8 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_dual_clint_uart_t32 | 74.19 | 30.06 | 39.29 | 63.86 | soc exhaustive i4k + d4k + btb + dual + clint + uart |
| 9 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_clint_uart_t32 | 50.48 | 23.57 | 35.71 | 77.18 | soc exhaustive i4k + d4k + btb + ras + clint + uart |
| 10 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_clint_uart_t32 | 50.21 | 24.20 | 39.29 | 73.04 | soc exhaustive i4k + d4k + btb + gshare + clint + uart |
| 11 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_dual_clint_uart_t32 | 73.98 | 30.24 | 39.29 | 66.07 | soc exhaustive i4k + d4k + btb + ras + dual + clint + uart |
| 12 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_dual_clint_uart_t32 | 74.73 | 31.71 | 42.86 | 66.29 | soc exhaustive i4k + d4k + btb + gshare + dual + clint + uart |
| 13 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_clint_uart_t32 | 52.58 | 24.37 | 39.29 | 76.89 | soc exhaustive i4k + d4k + btb + gshare + ras + clint + uart |
| 14 | soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_clint_uart_t32 | 74.83 | 31.88 | 42.86 | 64.13 | soc exhaustive i4k + d4k + btb + gshare + ras + dual + clint + uart |
| 15 | soc_x32_sv_rvm_rvc_rdtime_clint_uart_t32 | 26.88 | 11.10 | 14.29 | 72.14 | soc exhaustive base + clint + uart |
| 16 | soc_x32_sv_rvm_rvc_rdtime_i4k_clint_uart_t32 | 27.91 | 12.08 | 17.86 | 81.57 | soc exhaustive i4k + clint + uart |
| 17 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_clint_uart_t32 | 31.68 | 14.13 | 25.00 | 91.12 | soc exhaustive i4k + d4k + clint + uart |
| 18 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_dual_clint_uart_t32 | 47.69 | 18.93 | 25.00 | 77.67 | soc exhaustive i4k + d4k + dual + clint + uart |
| 19 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_clint_uart_t32 | 32.10 | 15.56 | 28.57 | 73.96 | soc exhaustive i4k + d4k + btb + clint + uart |
| 20 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_dual_clint_uart_t32 | 52.24 | 21.57 | 32.14 | 68.69 | soc exhaustive i4k + d4k + btb + dual + clint + uart |
| 21 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_clint_uart_t32 | 34.00 | 15.70 | 28.57 | 77.13 | soc exhaustive i4k + d4k + btb + ras + clint + uart |
| 22 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_clint_uart_t32 | 33.82 | 16.36 | 32.14 | 74.59 | soc exhaustive i4k + d4k + btb + gshare + clint + uart |
| 23 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_ras_dual_clint_uart_t32 | 51.11 | 21.71 | 32.14 | 62.11 | soc exhaustive i4k + d4k + btb + ras + dual + clint + uart |
| 24 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_dual_clint_uart_t32 | 52.46 | 23.21 | 35.71 | 66.32 | soc exhaustive i4k + d4k + btb + gshare + dual + clint + uart |
| 25 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_clint_uart_t32 | 34.55 | 16.50 | 32.14 | 77.70 | soc exhaustive i4k + d4k + btb + gshare + ras + clint + uart |
| 26 | soc_x32_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_clint_uart_t32 | 52.99 | 23.36 | 35.71 | 63.87 | soc exhaustive i4k + d4k + btb + gshare + ras + dual + clint + uart |
| 27 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_ras_clint_uart_t32 | 30.73 | 17.03 | 28.57 | 134.41 | soc exhaustive i4k + d4k + btb + ras + clint + uart |
| 28 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_dual_clint_uart_t32 | 45.91 | 22.90 | 32.14 | 151.84 | soc exhaustive i4k + d4k + btb + dual + clint + uart |
| 29 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_ras_dual_clint_uart_t32 | 46.38 | 23.05 | 32.14 | 134.48 | soc exhaustive i4k + d4k + btb + ras + dual + clint + uart |
| 30 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_clint_uart_t32 | 49.70 | 24.69 | 35.71 | 192.09 | soc exhaustive i4k + d4k + btb + gshare + ras + dual + clint + uart |
| 31 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_dual_clint_uart_t32 | 49.77 | 24.55 | 35.71 | 134.12 | soc exhaustive i4k + d4k + btb + gshare + dual + clint + uart |
| 32 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_dual_clint_uart_t32 | 42.89 | 20.26 | 25.00 | 157.78 | soc exhaustive i4k + d4k + dual + clint + uart |
| 33 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_clint_uart_t32 | 30.15 | 17.84 | 32.14 | 144.51 | soc exhaustive i4k + d4k + btb + gshare + ras + clint + uart |
| 34 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_clint_uart_t32 | 29.87 | 16.89 | 28.57 | 154.42 | soc exhaustive i4k + d4k + btb + clint + uart |
| 35 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb_gshare_clint_uart_t32 | 29.96 | 17.69 | 32.14 | 183.35 | soc exhaustive i4k + d4k + btb + gshare + clint + uart |
| 36 | soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_clint_uart_t32 | 28.01 | 15.46 | 25.00 | 146.33 | soc exhaustive i4k + d4k + clint + uart |

## Core Cumulative Matrix

Legend: rows are ordered by enabled feature count (fewest to most), then by feature bit pattern.

| # | fetch L1 | lsu L1 | btb | gshare | ras | dual issue | clint | uart | LUT % | FF % | BRAM % | Fmax MHz |
|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|
| 1 |  |  |  |  |  |  |  |  | 40.16 | 16.34 | 21.43 | 60.71 |
| 2 |  |  |  |  |  |  |  |  | 20.14 | 7.03 | 0.00 | 49.25 |
| 3 |  |  |  |  |  |  |  |  | 32.66 | 12.41 | 0.00 | 44.82 |
| 4 | yes |  |  |  |  |  |  |  | 16.51 | 7.17 | 3.57 | 92.58 |
| 5 | yes |  |  |  |  |  |  |  | 21.68 | 7.83 | 3.57 | 52.87 |
| 6 | yes |  |  |  |  |  |  |  | 34.64 | 13.24 | 3.57 | 54.56 |
| 7 | yes | yes |  |  |  |  |  |  | 20.35 | 9.29 | 10.71 | 98.44 |
| 8 | yes | yes |  |  |  |  |  |  | 26.28 | 10.52 | 10.71 | 84.60 |
| 9 | yes | yes |  |  |  |  |  |  | 40.76 | 16.96 | 17.86 | 89.93 |
| 10 | yes | yes |  |  |  | yes |  |  | 35.14 | 12.53 | 10.71 | 79.30 |
| 11 | yes | yes |  |  |  | yes |  |  | 40.03 | 13.76 | 10.71 | 70.81 |
| 12 | yes | yes |  |  |  | yes |  |  | 61.55 | 21.81 | 17.86 | 71.55 |
| 13 | yes | yes | yes |  |  |  |  |  | 40.16 | 16.34 | 21.43 | 60.71 |
| 14 | yes | yes | yes |  |  |  |  |  | 27.25 | 11.68 | 14.29 | 67.46 |
| 15 | yes | yes | yes |  |  |  |  |  | 40.66 | 18.33 | 21.43 | 67.55 |
| 16 | yes | yes | yes |  |  | yes |  |  | 41.98 | 15.88 | 17.86 | 58.42 |
| 17 | yes | yes | yes |  |  | yes |  |  | 63.15 | 24.27 | 25.00 | 64.03 |
| 18 | yes | yes | yes |  | yes |  |  |  | 40.16 | 16.34 | 21.43 | 60.71 |
| 19 | yes | yes | yes |  | yes |  |  |  | 27.26 | 11.82 | 14.29 | 63.12 |
| 20 | yes | yes | yes |  | yes |  |  |  | 41.02 | 18.50 | 21.43 | 71.27 |
| 21 | yes | yes | yes | yes |  |  |  |  | 40.16 | 16.34 | 21.43 | 60.71 |
| 22 | yes | yes | yes | yes |  |  |  |  | 28.47 | 12.47 | 17.86 | 66.26 |
| 23 | yes | yes | yes | yes |  |  |  |  | 41.34 | 19.11 | 25.00 | 63.73 |
| 24 | yes | yes | yes |  | yes | yes |  |  | 42.52 | 16.02 | 17.86 | 65.32 |
| 25 | yes | yes | yes |  | yes | yes |  |  | 63.95 | 24.44 | 25.00 | 61.77 |
| 26 | yes | yes | yes | yes |  | yes |  |  | 44.15 | 17.42 | 21.43 | 57.84 |
| 27 | yes | yes | yes | yes |  | yes |  |  | 64.46 | 25.81 | 28.57 | 61.46 |
| 28 | yes | yes | yes | yes | yes |  |  |  | 40.16 | 16.34 | 21.43 | 60.71 |
| 29 | yes | yes | yes | yes | yes |  |  |  | 28.40 | 12.61 | 17.86 | 72.80 |
| 30 | yes | yes | yes | yes | yes |  |  |  | 41.79 | 19.29 | 25.00 | 72.24 |
| 31 | yes | yes | yes | yes | yes | yes |  |  | 40.16 | 16.34 | 21.43 | 60.71 |
| 32 | yes | yes | yes | yes | yes | yes |  |  | 45.12 | 17.56 | 21.43 | 59.95 |
| 33 | yes | yes | yes | yes | yes | yes |  |  | 65.58 | 25.98 | 28.57 | 57.00 |

## SoC Cumulative Matrix

Legend: rows are ordered by enabled feature count (fewest to most), then by feature bit pattern.

| # | fetch L1 | lsu L1 | btb | gshare | ras | dual issue | clint | uart | LUT % | FF % | BRAM % | Fmax MHz |
|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|
| 1 |  |  |  |  |  |  | yes | yes | 26.88 | 11.10 | 14.29 | 72.14 |
| 2 |  |  |  |  |  |  | yes | yes | 41.13 | 17.98 | 14.29 | 68.01 |
| 3 | yes |  |  |  |  |  | yes | yes | 27.91 | 12.08 | 17.86 | 81.57 |
| 4 | yes |  |  |  |  |  | yes | yes | 42.68 | 18.98 | 17.86 | 74.02 |
| 5 | yes | yes |  |  |  |  | yes | yes | 28.01 | 15.46 | 25.00 | 146.33 |
| 6 | yes | yes |  |  |  |  | yes | yes | 31.68 | 14.13 | 25.00 | 91.12 |
| 7 | yes | yes |  |  |  |  | yes | yes | 47.36 | 21.74 | 32.14 | 87.57 |
| 8 | yes | yes |  |  |  | yes | yes | yes | 42.89 | 20.26 | 25.00 | 157.78 |
| 9 | yes | yes |  |  |  | yes | yes | yes | 47.69 | 18.93 | 25.00 | 77.67 |
| 10 | yes | yes |  |  |  | yes | yes | yes | 71.22 | 27.02 | 32.14 | 78.02 |
| 11 | yes | yes | yes |  |  |  | yes | yes | 29.87 | 16.89 | 28.57 | 154.42 |
| 12 | yes | yes | yes |  |  |  | yes | yes | 32.10 | 15.56 | 28.57 | 73.96 |
| 13 | yes | yes | yes |  |  |  | yes | yes | 49.99 | 23.39 | 35.71 | 73.40 |
| 14 | yes | yes | yes |  |  | yes | yes | yes | 45.91 | 22.90 | 32.14 | 151.84 |
| 15 | yes | yes | yes |  |  | yes | yes | yes | 52.24 | 21.57 | 32.14 | 68.69 |
| 16 | yes | yes | yes |  |  | yes | yes | yes | 74.19 | 30.06 | 39.29 | 63.86 |
| 17 | yes | yes | yes |  | yes |  | yes | yes | 30.73 | 17.03 | 28.57 | 134.41 |
| 18 | yes | yes | yes |  | yes |  | yes | yes | 34.00 | 15.70 | 28.57 | 77.13 |
| 19 | yes | yes | yes |  | yes |  | yes | yes | 50.48 | 23.57 | 35.71 | 77.18 |
| 20 | yes | yes | yes | yes |  |  | yes | yes | 29.96 | 17.69 | 32.14 | 183.35 |
| 21 | yes | yes | yes | yes |  |  | yes | yes | 33.82 | 16.36 | 32.14 | 74.59 |
| 22 | yes | yes | yes | yes |  |  | yes | yes | 50.21 | 24.20 | 39.29 | 73.04 |
| 23 | yes | yes | yes |  | yes | yes | yes | yes | 46.38 | 23.05 | 32.14 | 134.48 |
| 24 | yes | yes | yes |  | yes | yes | yes | yes | 51.11 | 21.71 | 32.14 | 62.11 |
| 25 | yes | yes | yes |  | yes | yes | yes | yes | 73.98 | 30.24 | 39.29 | 66.07 |
| 26 | yes | yes | yes | yes |  | yes | yes | yes | 49.77 | 24.55 | 35.71 | 134.12 |
| 27 | yes | yes | yes | yes |  | yes | yes | yes | 52.46 | 23.21 | 35.71 | 66.32 |
| 28 | yes | yes | yes | yes |  | yes | yes | yes | 74.73 | 31.71 | 42.86 | 66.29 |
| 29 | yes | yes | yes | yes | yes |  | yes | yes | 30.15 | 17.84 | 32.14 | 144.51 |
| 30 | yes | yes | yes | yes | yes |  | yes | yes | 34.55 | 16.50 | 32.14 | 77.70 |
| 31 | yes | yes | yes | yes | yes |  | yes | yes | 52.58 | 24.37 | 39.29 | 76.89 |
| 32 | yes | yes | yes | yes | yes | yes | yes | yes | 49.70 | 24.69 | 35.71 | 192.09 |
| 33 | yes | yes | yes | yes | yes | yes | yes | yes | 52.99 | 23.36 | 35.71 | 63.87 |
| 34 | yes | yes | yes | yes | yes | yes | yes | yes | 74.83 | 31.88 | 42.86 | 64.13 |

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
