# USB PD: research catalogue

- Purpose: every PD message, vendor message, quirk and reply seen in the main open stacks and the spec, for the TARGET_C PD source ([#633](https://github.com/awtoau/cynthion-workspace/issues/633)) and its exerciser.
- Stack decision: adopt [elagil/usbpd](https://github.com/elagil/usbpd) + [okhsunrog/fusb302b](https://github.com/okhsunrog/fusb302b) (Dan, 2026-11-07).
- Machine-readable catalogue: `messages.toml` (schema owned by the exerciser, `scripts/pd_exercise.py`, when it lands).

## Reports (Haiku, read-only, 2026-11-07; leads not verified facts)

| File | Source |
|---|---|
| [research/spec-tables.md](research/spec-tables.md) | PD R3.1 V1.6 (`~/vendors/usb-if/sources/USB_PD_R3_1_V1.6_2022-10.pdf`): all message tables, PDO/RDO/VDO layouts, timers, source-legal queries, disruptive messages |
| [research/linux-tcpm.md](research/linux-tcpm.md) | Linux `drivers/usb/typec/tcpm/` (GPL-2.0+): replies, VDM, quirks Q1-Q15, FUSB302 FIFO sequence, timers |
| [research/chrome-ec.md](research/chrome-ec.md) | coreboot/chrome-ec (BSD-3): source role, Google vendor commands, quirks, timers |
| [research/rust-zephyr.md](research/rust-zephyr.md) | elagil/usbpd API and coverage matrix, fusb302b/fusb302-rs/usb-pd-rs, Zephyr usb_c, integration needs |
| [research/sink-libs-vendor.md](research/sink-libs-vendor.md) | Ralim/pdsink/kws-x1: phone and charger quirks, AFC/QC/SCP/FCP/BC1.2, cable e-marker rules |

## Findings that change the work

- elagil/usbpd source mode is host-tested only; it sends no queries beyond Get_Sink_Cap/EPR; no VDM TX; everything else is answered Not_Supported. Queries and VDM must be added (raw path through the fusb302b `Driver`).
- fusb302b needs a global `embassy-time` driver and an async I2C; no repo builds on riscv32imac.
- FUSB302B cannot set GoodCRC to PD 3.0 (SPECREV `10` is "Do Not Use"); Linux steps the revision down after two Hard Resets (Q1).
- FUSB302B cannot address SOP' (cable); no FRS; no BIST test-data on the Linux driver.
- No Samsung or Apple vendor handling exists in Linux, Chrome EC or the Rust stacks; no phone PD captures exist in any repo read.
- A source may send the S20: Get_Sink_Cap, Get_Revision, Get_Status, Get_Manufacturer_Info, Get_Battery_*, Discover Identity/SVIDs/Modes, Alert, Ping, GotoMin, Get_Country_Codes, DR/VCONN_Swap, Enter_USB, BIST, EPR_Get_Sink_Cap, Vendor_Defined.
- Disruptive (force flag): Hard_Reset, Soft_Reset, Data_Reset, PR_Swap, DR_Swap, VCONN_Swap, FR_Swap, BIST test data, Enter_USB, EPR entry.

## Gaps

- Spec: no per-message revision column, PD 2.0 not on disk, product-type codes and AMS tables not read.
- Chrome EC: `usb_pd_dpm.c`, `usb_pd_pdo.c`, board policy files not read.
- Code search on GitHub was rate-limited; quirk searches cover fetched files only.
- Not found anywhere: Oppo VOOC, MediaTek PE, QC4, Samsung PD VDM (SVID 0x04E8), Apple divider names, phone-as-sink PPS behaviour.
