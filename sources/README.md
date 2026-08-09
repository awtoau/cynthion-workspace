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

**What these settled** (#109, `../docs/chips/hyperram/w956a8.md`):

**The part is 8 MiB and always was.** `ID0 = 0x0c86` gives raw fields of 12 and 8, and
**both are count-minus-one** — table 5.2 states `00000` = *"One Row address bit"*. So it
is 13 row + 9 column bits: 8192 x 512 x 2 = 8 MiB. Table 5.7 independently states "Array
Rows: 8192".

**64 Mbit is 8 MiB**, and misreading that as 4 MiB is what made a perfectly ordinary part
look like it held twice its marking. Two further hypotheses were published to explain
that non-existent 2x gap — including a dual-die reading of ID0[15:14], which the 128 Mbit
datasheet does document but which does not apply here. Both are retracted; the detail is
in `../docs/chips/hyperram/w956a8.md`.

These datasheets were still worth fetching: they are what settled it, and the 128 Mbit
one is the control that let the dual-die hypothesis be tested and dropped.

### A 10-page Winbond PDF is in circulation and is ABRIDGED (both local copies are now full)

The copy fetched first was **10 pages**. The full document is **45**. Everything
about timing behaviour is in the missing 35 -- including **section 10.2.2 Active
Clock Stop** and Figure 13, on printed page 27, which is what says whether CK may
legally be halted mid-burst. A question about stalling the data phase could not
be answered from the abridged copy, and the absence looked like the part not
supporting it rather than the document not covering it.

**Validity check before trusting any candidate for this part:**

    pdfinfo <file>            # Pages >= 45
    pdftotext -layout <file> - | grep -c "Active Clock Stop"    # non-zero

**Winbond's own site cannot serve it unauthenticated.** A
`winbond.com/hq/support/documentation/downloadV2022.jsp?...` URL returns **HTTP
200** whose body is a JavaScript redirect to the technical-support login page --
so it fails as a success, not as an error, and a fetch that checks the status
code will save the login page under the datasheet's name.

### The board's actual part is Winbond -- and its datasheet is now here

`repos/cynthion/.../gateware/facedancer/top.py:42` names it: **`W956A8MBYA6I`**, a Winbond
64 Mbit (8 MiB) HyperRAM. The ISSI datasheets above are the closest available equivalents
and were used to decode ID0 -- correctly, since the HyperBus register layout is common --
but they are **not** the part on the board.

| file | part | source |
|---|---|---|
| `Winbond-W956A8MBYA-64Mbit-HyperRAM.pdf` | rev A01-002, **47 pp -- complete** (re-fetched; the earlier 10 pp abridged copy is gone). Superseded by A01-006 below, but keep it: **A01-002 section 2 is the only order table that still lists the board's `W956A8MBYA6I` / `W956D8MBYA6I` 166 MHz parts.** | `https://xonstorage.z8.web.core.windows.net/pdf/winbond_w956d8mbya6i_apr22_xonlink.pdf` |
| `W956x8MBYA_A01-006.pdf` | **W956D8MBYA / W956A8MBYA, rev A01-006 (2022-07-29), 45 pp -- the full document** | `https://xonstorage.z8.web.core.windows.net/pdf/winbond_w956d8mbya6i_apr22_xonlink.pdf` |
| `Winbond-W956D8MBY-128Mbit-HyperRAM.pdf` | W957D8MFYA / W957A8MFYA, 128 Mbit, rev A01-004 (Aug 2022), 45 pp | `https://media.digikey.com/pdf/Data%20Sheets/Winbond%20PDFs/W957x8MFYA_Rev_A01-004_8-4-22.pdf` |

Two naming corrections to what was previously assumed here. **`W956D8MBY` is not the
128 Mbit sibling** -- it is the *same* 64 Mbit die as `W956A8MBYA`, differing only in
supply voltage (1.8 V vs 3.0 V), and both are covered by one combined datasheet. That
combined document is the first file above and it is the datasheet for the part on the
board. The nearest genuine 128 Mbit HyperRAM is **`W957D8MFYA`** (a 2-die DDP), which is
the second file, kept under the requested filename.

#### Rev A01-006 no longer lists the 6I part that is on the board

Section 2, Order Information, in `W956x8MBYA_A01-006.pdf` has **two rows** and both
are the 200 MHz `5I`: `W956D8MBYA5I` (1.8 V) and `W956A8MBYA5I` (3.0 V). The
board's `W956A8MBYA6I` is not in it. The revision history says why — **A01-004,
2 Sep 2021: *"Remove W956D8MBYA6I (1.8V) and W956A8MBYA6I (3.0V) 166MHz part
number"***.

The 166 MHz **specifications survive** — Table 21 (read timing, p. 37), Table 22
(clock timing, p. 38) and Table 24 (write timing) all still carry a 166 MHz
column, and it is the column the fitted part is graded to. So the part is
specified but no longer orderable, which is the opposite of the usual failure and
worth stating: a future build of this board cannot buy the 6I and must take the
5I substitution the schematic already approves.

`../docs/chips/hyperram/w956a8.md` previously said *"Section 2 of the datasheet
lists both"*. It does not, and that is corrected there.

#### CR0[7:4] Initial Latency is a SPARSE encoding -- only 5 of 16 codes are legal

**Table 8, printed p. 21** (A01-006; same table in A01-002 and in the 128 Mbit
`W957D8MFYA`, so it is family-wide, not part-number specific):

| CR0[7:4] | latency | max frequency |
|---|---|---|
| `0000b` | 5 clocks | 133 MHz |
| `0001b` | 6 clocks | 166 MHz |
| `0010b` | 7 clocks | 200 MHz (**default**) |
| `0011b`..`1101b` (3..13) | **Reserved** | -- |
| `1110b` | 3 clocks | 83 MHz |
| `1111b` | 4 clocks | 100 MHz |

- Encoding is `clocks = 5 + sext4(code)`; the two short latencies live at the top of the range.
- "Max frequency" is an upper bound on that code, not a requirement -- **more latency is always safe at a lower clock.**
- POR default CR0 = **`0x8F2F`** (`[15]=1`, `[14:12]=000`, `[11:8]=1111`, `[7:4]=0010`, `[3]=1`, `[2]=1`, `[1:0]=11`).
- `tACC` = 35/36/37.5/40 ns at 200/166/133/100 MHz (**Table 21, p. 37**) -- exactly `clocks x tCK` for the codes above.
- **Drive strength `000b` and `100b` are both 34 ohms** (Table 8) -- an 8-code sweep has only 7 distinct impedances.

#### What they settled: `0x1000` is the Manufacturer Information Register

**Section 9.1, Table 5 -- "Register Space Address Map (for single die 64Mb device)".** The
map lists a fifth register beyond the four the HyperBus spec defines:

    Manufacturer Information Register (0~17) read    C0h or E0h ... 02h  00h  00h  00h~11h

The address arithmetic in that table maps system address bits `18~11` to CA[31:24], so a
CA[31:24] of `02h` is system word address `0x02 << 11` = **`0x1000`**. The same arithmetic
gives `00h` -> `0x0000` (ID0/ID1) and `01h` -> `0x0800` (CR0/CR1), which is the standard
map and confirms the decoding. The register spans `00h`~`11h`, i.e. **`0x1000`-`0x1011`,
18 words**, and is marked **read only** -- matching the observed refusal to accept writes
while a CR0 write on the same code path succeeded.

The note under Table 6 adds: *"For the Die Manufacturing Information Register: 06h~0Ah and
0Fh~11h should be 'reserved'."* The observed words at `0x1006`/`0x1007` (`3320 3320`) fall
inside that reserved span.

So the block is real, vendor-defined, and exactly where the datasheet says it is. **What
it is *not* is decoded**: the datasheet names the register in the address map and nowhere
defines its fields -- section 9 has subsections for ID0/ID1 (9.3), CR0 (9.4) and CR1 (9.5)
but none for the MIR. The ASCII `00029740` reading of the first eight bytes is therefore
consistent with a lot/date/trace code but remains unconfirmed by the vendor document.

This is also a Winbond-family extension rather than a HyperRAM-wide one: neither the
128 Mbit `W957D8MFYA` nor the 256 Mbit `W958D8NBYA` register map has an MIR entry at all.

**Bonus:** Table 8 gives `[3:0] Manufacturer = 0110b - Winbond`, resolving the manufacturer
code the ISSI datasheets could not (they give ISSI as `0011`).

#### ID0 field widths, 64 vs 128 Mbit

| | 64 Mbit (`W956A8MBYA`, Table 8) | 128 Mbit (`W957D8MFYA`, Table 6) |
|---|---|---|
| `[15:14]` | MCP Die Address (00b..11b, 4 die) | DDP Die Address (00b/01b; 10b/11b reserved) |
| `[12:8]` Row Address Bit Count | `01100b` = 13th row address bit | `01100b` = 13th row address bit |
| `[7:4]` Column Address Bit Count | `1000b` = 9th column address bit | `1000b` = 9th column address bit |
| `[3:0]` Manufacturer | `0110b` Winbond | `0110b` Winbond |

**The fields are identical, and they are not sized for a larger die.** The 128 Mbit part
reports the *same* 13 row / 9 column bits as the 64 Mbit part, because it is two 64 Mbit
dies in a DDP package -- ID0 is documented "For each die". Density scales by die count in
`[15:14]`, not by widening the address-bit-count fields. The 256 Mbit single-die
`W958D8NBYA` is the contrast case: it widens the row field to `01110b` (15 row bits) and
zeroes `[15:14]` to Reserved.

This also confirms the count-minus-one convention independently of ISSI: section 8.1.1
states a 64 Mbit device has "9 column address bits and 13 row address bits ... 2^22 = 4M
words = 8M bytes", with 8192 rows.

#### How they were fetched, so the next attempt does not repeat the dead ends

Both PDFs came from third-party mirrors. **Winbond itself will not serve them.**

Working method:

1. Load `https://www.winbond.com/hq/support/documentation/index.html?__locale=en&categoryName=Specialty%20DRAM&pno=W956D8MBY`
   in awto-playwrong, then read the anchors back with a `js` op --
   `document.querySelectorAll("a")` **does** return them once the page's JS has run. The
   earlier "anchors come back empty" note was an artefact of scraping the wrong URL.
2. That reveals the real download endpoint:
   `downloadV2022.jsp?xmlPath=/support/resources/.content/item/<DOCID>.html&level=<N>`.
   The datasheet is `DA00-W956D8MBYA` at **`level=4`**, and level 4 is login-walled --
   it redirects to the technical-support request form. App notes and IBIS models at
   `level=1` are open. So the vendor route ends here for datasheets specifically.
3. Instead, search Bing via playwrong and decode the result links: Bing wraps targets in
   `bing.com/ck/a?...&u=a1<base64url>`, so `base64.urlsafe_b64decode(...)` on the `u=a1`
   payload recovers the real URL. Plain-`curl` DuckDuckGo returns HTTP 202 with an
   "anomaly" page and is useless here.
4. Download the recovered URL with plain `curl` and a browser user-agent. Neither mirror
   needed cookies.

Mirrors that worked: **`xonstorage.z8.web.core.windows.net/pdf/`** (note `.z8.web`, not
`.blob`, which 404s) and **`media.digikey.com/pdf/Data Sheets/Winbond PDFs/`**. Mouser's
`mouser.com/datasheet/...` URLs return a 13 KB **HTML** bot page under a `.pdf` name --
`file` reporting "HTML document" is what catches it. Arrow's `static6.arrow.com` and
`marthel.eu` both failed to connect.

The `resource-files/<PART>_<REV>.pdf` pattern still 404s for every `W956*` combination,
but `productResource-files/Winbond_DRAM_HyperRAM_Product_Brief_2023Q2.pdf` is fetchable
and is a useful family overview.

`scripts/fetch_winbond_hyperram.py` wraps the probe/links/get steps above.

GitHub code search finds the part in Zephyr
(`drivers/memc/memc_mcux_flexspi_w956a8mbya.c`) and in `aesc-silicon/elements-zibal`, but
**neither encodes register addresses or contents**. The Zephyr driver is still worth
reading for the HyperBus command encoding (`0xA0` read, `0x20` write, `0xE0` register
read, `0x60` register write).

### Note on fetching from ISSI

`issi.com` is behind Incapsula and returns a 1.2 KB bot-check page to plain `curl`,
regardless of user agent. It looks like a successful download — `file` reporting "HTML
document" rather than "PDF document" is what catches it.

Working method: clear the challenge with **awto-playwrong**, pull the cookies from its
`/cookies` endpoint, then `curl` with that jar. The cookies expire quickly enough that a
second download may need them refreshed.

### The 256 Mbit contrast case

| file | part | source |
|---|---|---|
| `Winbond-W958D8NBYA-256Mbit-HyperRAM.pdf` | W958D8NBYA, 256 Mbit single die | mirror; see the fetching method above |
| `Winbond-HyperRAM-Product-Brief-2023Q2.pdf` | family overview, one page | `https://www.winbond.com/.../productResource-files/Winbond_DRAM_HyperRAM_Product_Brief_2023Q2.pdf` |

This is the part the ID0 comparison above uses as its control: single die, `[15:14]`
zeroed to Reserved, and the row field widened to `01110b` (15 row bits). It is the
evidence that density scales by die count in the 128 Mbit part rather than by widening
the address-bit fields.

## Configuration flash

The board's flash is a **Winbond W25Q32JV**, JEDEC `EF 40 16`.

| file | part | pages | source |
|---|---|---|---|
| `Winbond-W25Q32JV-32Mbit-SPI-Flash-RevG.pdf` | W25Q32**JV**, 3V 32 Mbit, dual/quad SPI — **Revision G, 27 March 2018, the revision the schematic names** | **80** | `https://www.winbond.com/resource-files/w25q32jv%20revg%2003272018%20plus.pdf` |
| `Winbond-W25Q32JV-32Mbit-SPI-Flash.pdf` | W25Q32JV — **Preliminary Revision A1, 18 November 2014.** Superseded; see the warning below | 78 | the `revi 05182022` URL below now **404s**; this file is not what that URL named |
| `Winbond-W25Q32FV-32Mbit-SPI-Flash.pdf` | W25Q32**FV**, the previous generation | — | vendor mirror |

Both generations are kept because they differ in the timing maximums that
`../docs/luna_ecp5_fpga/flash-detailed.md` transcribes, and reading the wrong one is an
easy way to attribute a JV limit to an FV part.

### The JV copy that was here was the 2014 PRELIMINARY, and one number differs

`Winbond-W25Q32JV-32Mbit-SPI-Flash.pdf` is titled *"Preliminary W25Q32JVXXIQ RevA0
Nov182014"* in its own PDF metadata and carries `Preliminary-Revision A1` in every
page footer. The manifest row claimed it was Revision I (May 2022) and gave a URL
that returns **HTTP 404** today, so nothing could have caught it — the row recorded
a URL and a title but no way to check the copy, which is the same failure the
Infineon section below describes.

**It matters.** §9.6 AC Electrical Characteristics, Page Program Time `tPP`:

| | typ | max |
|---|---|---|
| Preliminary Rev A1 (2014) | **0.7 ms** | 3 ms |
| **Revision G (2018) — the fitted part** | **0.4 ms** | 3 ms |

A 231-page write is 162 ms by the preliminary and **92 ms** by Rev G, and that
figure is the denominator in the host→flash transport gap in
`../docs/chips/w25q32-config-flash.md`. Everything else checked identical:
`fC1` 133 MHz / `fC2` 104 MHz / `fR` 50 MHz, `tSE` 45/400 ms, `tBE1` 120/1600 ms,
`tBE2` 150/2000 ms, `tCE` 10/50 s.

**Validity check before trusting a candidate for this part:**

    pdfinfo <file> | grep Pages                              # Pages = 80
    pdftotext -layout <file> - | grep -c 'Revision G'         # non-zero
    pdftotext -layout <file> - | grep -c 'Preliminary'        # ZERO

## FPGA

| file | part | source |
|---|---|---|
| `Lattice-ECP5-Family-DataSheet-FPGA-DS-02012.pdf` | ECP5 / ECP5-5G family, FPGA-DS-02012 v1.9, March 2018 | `https://www.latticesemi.com/view_document?document_id=50461` | **108 pages** |
| `Lattice-ECP5-sysCONFIG-FPGA-TN-02039.pdf` | sysCONFIG user guide, FPGA-TN-02039-2.3, March 2024 — configuration modes, timing, and the SPI boot path | `https://www.latticesemi.com/view_document?document_id=50462` | **74 pages** |

### One file here was never a PDF, and nothing caught it

`Infineon-AN226576-Getting-Started-with-HyperRAM.pdf` was **14 KB of HTML** — a
bot page saved under a datasheet's name — sitting between multi-megabyte real
datasheets since 2026-08-06. Deleted.

It is the failure this file's own last section describes, and it survived because
the manifest recorded a URL and a title but no way to check the copy. **Every row
above now carries a page count**, verified with `pdfinfo`, which is the cheapest
check that distinguishes a document from a login page:

    pdfinfo <file> | grep Pages      # a bot page has no Pages line at all

## Other parts

| file | what |
|---|---|
| `Atmel-42363-SAM-D11_Datasheet.pdf` | SAMD11 — the Apollo MCU |
| `Atmel-42336-ASF-USB-Stack-Manual_ApplicationNote_AT09331.pdf` | ASF USB stack |
| `FUSB302B-958669.pdf` | USB-C PD controller (#98) |
| `PAC195X-Family-DS20006539B.pdf` | PAC1954 power monitor (#82, #84) |
| `334x.pdf` | USB3343 PHY |
| `Vishay-SiA483ADJ-P-Channel-30V-MOSFET.pdf` | SiA483ADJ — the VBUS pass MOSFET, Q1/Q2/Q4/Q5/Q6/Q7 (`https://www.vishay.com/docs/77080/sia483adj.pdf`) |
| `SiTime-SiT1602-MEMS-oscillator.pdf` | SiT1602B, **rev 1.08, 1 Jan 2023, 18 pp** — Y1, the board's only oscillator (`https://www.sitime.com/datasheet/SiT1602`) |
| `S9c76cb8ac7dc4b77b5edfe7984049618q.pdf` | unidentified — rename when someone works out what it is |

### What the SiT1602 settled: the part number decodes, and it is ±50 ppm

`clock_misc.kicad_sch` gives Y1 as **`SIT1602BC-23-33E-60.000000E`**, and every
rate on this board descends from it — `usb` is that oscillator passed straight
through, and `sync` is a PLL off the same pin. The Ordering Information guide
(p. 13) decodes it field by field:

| field | value | meaning |
|---|---|---|
| `C` | temperature range | **Commercial, −20 to +70 °C** |
| `–` | drive strength | datasheet default |
| `2` | package | 3.2 × 2.5 mm — matches the `Crystal_SMD_3225` footprint |
| `3` | **frequency stability** | **±50 ppm** |
| `33` | supply | 3.3 V ±10% |
| `E` | pin 1 | Output Enable |
| `E` | packing | 8 mm tape & reel, 1 ku |

±50 ppm is Table 1's `F_stab`, *"inclusive of initial tolerance at 25 °C, 1st year
aging at 25 °C, and variations over operating temperature, rated power supply
voltage and load"* — so it is the total, not a bin at 25 °C. Against the USB3343's
REFCLK accuracy requirement of ±500 ppm (Rev 1.2 Table 4.3) and USB 2.0 high
speed's own ±500 ppm, that is **10× margin**. Duty cycle 45–55% against the PHY's
20–80% requirement; RMS period jitter 1.8 ps typ.

**Validity check before trusting a copy:**

    pdfinfo <file>                                      # Pages = 18
    pdftotext -layout <file> - | grep -c 'F_stab'       # non-zero

Used by [`../docs/chips/bus-speed-audit.md`](../docs/chips/bus-speed-audit.md)
and [`../docs/chips/usb3343-ulpi-phy.md`](../docs/chips/usb3343-ulpi-phy.md).

### The SiA483ADJ is a SINGLE device, and that closes an open question

`../docs/hardware.md` records the VBUS switches as unsettled: the KiCad symbol is
`Transistor_FET:SiA449DJ` with three pins, the fitted part is `SIA483ADJ-T1-GE3`, and
Vishay's `DJ` suffix appears on dual devices in the same package — so a symbol/part-number
disagreement left "one device or two?" open, and with it whether an open switch isolates
or merely stops driving.

Page 1 of this datasheet states **"PowerPAK SC-70-6L Single"** and **"P-Channel 30 V (D-S)
MOSFET"**. One device per switch function, as the six-designator count already suggested.

**Validity check before trusting a copy:**

    pdfinfo <file>                                          # Pages = 9
    pdftotext -layout <file> - | grep -ci 'SC-70-6L Single' # non-zero

**Vishay serves a missing document as HTTP 200 with an HTML body.** The first fetch of
this part landed as a 720 KB `<title>Page Not Found | Vishay</title>` page saved under a
`.pdf` name — indistinguishable from a datasheet by name and size alone. The URL above
is the working one; `file` on the result is what tells them apart.

## A failed download looks exactly like a datasheet

Six files arrived here named `*.pdf` and were **HTML bot-check or error pages**: an
8-byte `fusb.pdf`, two 3 KB Winbond pages, and three identical 14 KB ones. They sat in
`tmp/` for a week looking like fetched datasheets.

`file <name>.pdf` is the check — "HTML document" rather than "PDF document" catches every
one of them, and it costs nothing to run after a fetch. Both notes above about Mouser and
ISSI describe the same failure; this is the general form of it.

## `lattice-ecp5-sysconfig-FPGA-TN-02039.pdf`

ECP5 and ECP5-5G sysCONFIG User Guide, **FPGA-TN-02039-2.3, March 2024**, 74 pages.

    https://0x04.net/~mwk/doc/lattice/ecp5/FPGA-TN-02039-2-3-ECP5-and-ECP5-5G-sysCONFIG.pdf

A mirror, because Lattice's own copy is behind a block page — Mouser's returned an
HTML interstitial rather than a PDF. Verify a good copy by page count (74) and by
the presence of the string `6.6. TransFR Operation`; a truncated download loses
exactly the configuration sections that matter.

Answers #234: the ECP5 does support loading without taking the design down.
**Background Mode** is "a configuration mode where all the I/O pins remain
operational"; **NDR (TransFR)**, bit 28 of the control register, keeps an I/O at
its previous value through `PROGRAMN`/`REFRESH` instead of tristating it; and
Dual Boot / Multi Boot hold two patterns in the one SPI flash. `REFRESH` is
issuable over JTAG, which is the port we already have.
