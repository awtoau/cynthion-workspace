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

The board's HyperRAM reads `ID0 = 0x0c86`, which decodes as 12 row + 9 column address
bits — apparently 4 MiB. Probing found storage to 8 MiB, and that looked like a part
carrying twice its marking.

The 128 Mbit datasheet explains it instead: *"The device is a dual die stack of two 64Mb
die"*, and its table 5.2 repurposes ID0 bits 15:14 — reserved on the single-die part — as
a **Die Address**. `0x0c86` has those bits `00`, so **ID0 is describing die 0 of a stack,
not the whole package**. Two 4 MiB dies is 8 MiB, exactly as measured. Documented
capacity, not hidden capacity.

The 64 Mbit datasheet is the control: same family, single die, bits 15:14 reserved.

One field still unexplained: both datasheets give ISSI as manufacturer code `0011`, and
this part reports `0110`.

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
