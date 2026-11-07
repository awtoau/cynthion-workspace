# cynthion_udc

Out-of-tree Linux UDC: the Cynthion as a USB device controller for the PC's gadget stack. Design: [`docs/usb-device-mode.md`](../../docs/usb-device-mode.md). Backends: **AUX** (`cudc_aux`, real: a `usb_driver` over the [AUX link](../../docs/aux-link.md)) and **stub** (in-kernel fake host, tests). Status: [increment 3](../../docs/usb-device-mode.md#increment-3-status), AUX backend tested against a software model only.

| file | role |
|---|---|
| [`cudc.h`](cudc.h) | core <-> backend interface: `struct cudc_backend_ops`, backend event calls |
| [`cudc_core.c`](cudc_core.c) | `usb_gadget_ops` / `usb_ep_ops`, request queues, module init |
| [`cudc_aux.c`](cudc_aux.c) | AUX backend: `usb_driver` for interface `FF/43/01` (1d50:6189); control channel, per-endpoint pipes, ordered workqueue, bounce buffers |
| [`cudc_stub.c`](cudc_stub.c) | stub backend: in-kernel fake host + firmware/gateware contract (local answers, toggle/NAK/STALL/ZLP), debugfs `ctl` |

- Licence: GPL-2.0 (the kernel's; see [`docs/licensing.md`](../../docs/licensing.md)).
- UDC name: stub `cynthion_udc`; AUX: the interface's device name (e.g. `1-1:1.2`). Module parameter `stub=0` leaves the stub out. HS only; EP0 + ep1-4 in/out, bulk + interrupt.

## Build

```
mkdir -p tmp/cudc-build
make -C linux/cynthion_udc MO=$PWD/tmp/cudc-build      # KDIR defaults to /usr/src/kernels/$(uname -r)
```

- `MO=` keeps build output out of the source tree.

## Test

```
./scripts/linux/cudc_vm_test.py            # build + virtme-ng VM test
./scripts/linux/cudc_vm_test.py --no-build
./scripts/linux/cudc_vm_test.py --backend aux     # cudc_aux vs the Python model of the Cynthion
./scripts/linux/cudc_vm_test.py --backend both
./scripts/linux/cudc_vm_test.py --mutate seq      # must exit 0 = the deliberate bug was caught
```

- AUX test: [`cudc_aux_model.py`](../../scripts/linux/cudc_aux_model.py) (firmware relay + gateware FIFO + `Ep0Device` rules + the remote host) is a virtual USB device on `vhci_hcd` ([`cudc_usbip.py`](../../scripts/linux/cudc_usbip.py)); dummy_hcd cannot give 5 + 5 bulk endpoints at fixed addresses. The same test groups run through it; extra groups: control messages of 512 B (ZLP) and 4102 B, stale control reply, stale endpoint command, AUX console next to the bridge.
- Mutations (`--mutate`): kernel `seq`, `zlp`, `flush`, `stale_ep` (`-DCUDC_MUTATE_*` in `cudc_aux.c`); model `model_stale_ctl`, `model_stale_ep`, `model_console`.

- **Never insmod on the host** (shared dev box). The test boots the host kernel in `vng`, rootfs read-only, and loads everything there.
- Guest half: [`scripts/linux/cudc_vm_guest.py`](../../scripts/linux/cudc_vm_guest.py). Log: `tmp/logs/cudc_vm_test.log` (includes the module's `dev_dbg` output, loaded with `dyndbg=+p`).
- udc-core's own tracepoints (`events/gadget/`) cover every op too; enable in the guest via tracefs when needed.

## Stub host control (debugfs `ctl`)

`/sys/kernel/debug/cynthion_udc/ctl`: write one command line (blocks until done), read the result line. Numbers take base 0.

| command | result |
|---|---|
| `ctrl RT REQ VAL IDX LEN [hexdata]` | `ctrl ok n= [pkts=] [data=]`, `ctrl stall`, `ctrl timeout`; `pkts` = EP0 data-stage packet sizes |
| `in EP WAIT_MS MAXPKTS` | `in ok\|nak\|stall bytes= pkts= tog= data=`; stops at a short packet |
| `out EP WAIT_MS LEN...` | `out ok\|nak\|stall done= tog=`; payload byte j of the command = `(13 j + 17) & 0xFF` |
| `stat EP` | `tog halted wedged enabled naks dequeues` |
| `devstat` | `address wakeup selfpowered testmode wakeups refused attaches detaches suspended` |
| `suspend`, `resume`, `vbus 0\|1`, `autoloop 0\|1` | `<cmd> ok` |

- `WAIT_MS` = how long a NAKed token is retried (the gadget has not queued a request yet).
- `autoloop 1` (default, set before bind): IN endpoint of each interface looped into its OUT endpoint packet for packet.
- Local answers (as the firmware will): SET_ADDRESS, GET_STATUS, SET/CLEAR_FEATURE (DEVICE_REMOTE_WAKEUP, TEST_MODE, ENDPOINT_HALT).
