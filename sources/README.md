# Datasheets and primary sources

**The PDFs here are not committed** — they are third-party documents, several megabytes
each, and freely fetchable from the vendor. This file is the manifest: what each one is,
where it came from, and what it settled.

Fetch anything missing with the URL below. `sources/*.pdf` is gitignored.

## HyperRAM (Cynthion r1.4)

| file | part | source |
|---|---|---|
| `ISSI-IS66WVH8M8-64Mbit-HyperRAM.pdf` | IS66/67WVH8M8ALL/BLL, 64 Mbit, 8M x 8 | `https://www.issi.com/WW/pdf/66-67WVH8M8ALL-BLL.pdf` |
| `ISSI-IS66WVH16M8-128Mbit-HyperRAM.pdf` | IS66/67WVH16M8ALL/BLL, 128 Mbit, 16M x 8 | `https://www.issi.com/WW/pdf/66-67WVH16M8ALL-BLL.pdf` |

**What these settled** (#109, `../docs/luna_ecp5_fpga/hyperram-detailed.md`):

**The part is 8 MiB and always was.** `ID0 = 0x0c86` gives raw fields of 12 and 8, and
**both are count-minus-one** — table 5.2 states `00000` = *"One Row address bit"*. So it
is 13 row + 9 column bits: 8192 x 512 x 2 = 8 MiB. Table 5.7 independently states "Array
Rows: 8192".

**64 Mbit is 8 MiB**, and misreading that as 4 MiB is what made a perfectly ordinary part
look like it held twice its marking. Two further hypotheses were published to explain
that non-existent 2x gap — including a dual-die reading of ID0[15:14], which the 128 Mbit
datasheet does document but which does not apply here. Both are retracted; the detail is
in `../docs/luna_ecp5_fpga/hyperram-detailed.md`.

These datasheets were still worth fetching: they are what settled it, and the 128 Mbit
one is the control that let the dual-die hypothesis be tested and dropped.

### The board's actual part is Winbond, and its datasheet is NOT here

`repos/cynthion/.../gateware/facedancer/top.py:42` names it: **`W956A8MBYA6I`**, a Winbond
64 Mbit (8 MiB) HyperRAM. The ISSI datasheets above are the closest available equivalents
and were used to decode ID0 -- correctly, since the HyperBus register layout is common --
but they are **not** the part on the board. That mismatch explains the manufacturer code
that would not resolve: ID0[3:0] reads `0110` while both ISSI datasheets give ISSI as
`0011`.

**Wanted, and not yet obtained:**

| part | density | why |
|---|---|---|
| `W956A8MBYA6I` | 64 Mbit | the actual part -- would explain the read-only block at register `0x1000` |
| `W956D8MBY` | 128 Mbit | the larger sibling; comparing register field widths across densities is what would show whether the fields are sized for bigger dies |

**What was tried, so the next attempt does not repeat it:**

- `winbond.com` product and documentation pages render their links via JS and return
  nothing to a text scrape; playwrong loads them but the anchors come back empty.
- The `resource-files/<PART>_<REV>.pdf` URL pattern that works for other Winbond parts
  404s for every `W956*` / revision combination tried.
- GitHub code search finds the part in Zephyr
  (`drivers/memc/memc_mcux_flexspi_w956a8mbya.c`, a real production driver) and in
  `aesc-silicon/elements-zibal`, but **neither encodes register addresses or contents** --
  the Zephyr driver leaves those to the caller and the Zibal file is a clocking harness.
  GitHub code search does not index PDF contents, so datasheets vendored into repos are
  not findable this way.

The Zephyr driver is still worth reading for the HyperBus command encoding (`0xA0` read,
`0x20` write, `0xE0` register read, `0x60` register write).

### Note on fetching from ISSI

`issi.com` is behind Incapsula and returns a 1.2 KB bot-check page to plain `curl`,
regardless of user agent. It looks like a successful download — `file` reporting "HTML
document" rather than "PDF document" is what catches it.

Working method: clear the challenge with **awto-playwrong**, pull the cookies from its
`/cookies` endpoint, then `curl` with that jar. The cookies expire quickly enough that a
second download may need them refreshed.

## Other parts

| file | what |
|---|---|
| `Atmel-42363-SAM-D11_Datasheet.pdf` | SAMD11 — the Apollo MCU |
| `Atmel-42336-ASF-USB-Stack-Manual_ApplicationNote_AT09331.pdf` | ASF USB stack |
| `FUSB302B-958669.pdf` | USB-C PD controller (#98) |
| `PAC195X-Family-DS20006539B.pdf` | PAC1954 power monitor (#82, #84) |
| `334x.pdf` | USB3343 PHY |
| `S9c76cb8ac7dc4b77b5edfe7984049618q.pdf` | unidentified — rename when someone works out what it is |

## Not here

The **configuration flash** (Winbond W25Q32, JEDEC `EF 40 16`) has no datasheet in this
directory; its behaviour is recorded in `../docs/luna_ecp5_fpga/flash-detailed.md`, where
the vendor maximums table is transcribed from the W25Q32JV datasheet.
