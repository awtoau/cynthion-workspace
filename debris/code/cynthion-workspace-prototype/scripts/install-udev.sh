#!/usr/bin/env bash
# Install udev rules for stable Cynthion symlinks.
# Creates /dev/cynthion-rv0, /dev/cynthion-fpg, /dev/cynthion-apl
# based on Apollo USB serial number — survives ttyACM# reordering.
set -euo pipefail

RULES=/etc/udev/rules.d/54-cynthion.rules

# Apollo serial number (read from hardware or override via env)
# Default is the serial from this dev machine; detect dynamically if blank.
SERIAL="${CYNTHION_SERIAL:-}"

if [ -z "$SERIAL" ]; then
    SERIAL=$(udevadm info -a -n /dev/ttyACM0 2>/dev/null \
        | awk -F'"' '/ATTRS{serial}/ && /[0-9A-F]{20,}/ {print $2; exit}')
fi

if [ -z "$SERIAL" ]; then
    echo "ERROR: could not determine Cynthion serial number."
    echo "  Plug in the Cynthion and try again, or set CYNTHION_SERIAL=<serial>."
    exit 1
fi

echo "Cynthion serial: $SERIAL"
echo "Writing $RULES"

sudo tee "$RULES" > /dev/null <<EOF
# Cynthion / Apollo CDC-ACM TTYs — stable symlinks by USB serial number.
# Serial: $SERIAL
#
# Apollo exposes three CDC-ACM interfaces:
#   interface 0 (rv0)  UART bridge to VexRiscv
#   interface 1 (fpg)  FPGA event stream
#   interface 2 (apl)  Apollo console / GDB RSP
#
# Symlinks: /dev/cynthion-rv0, /dev/cynthion-fpg, /dev/cynthion-apl

SUBSYSTEM=="tty", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="615c", \\
    ATTRS{serial}=="$SERIAL", ATTRS{bInterfaceNumber}=="00", \\
    SYMLINK+="cynthion-rv0", MODE="0664", TAG+="uaccess"

SUBSYSTEM=="tty", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="615c", \\
    ATTRS{serial}=="$SERIAL", ATTRS{bInterfaceNumber}=="02", \\
    SYMLINK+="cynthion-fpg", MODE="0664", TAG+="uaccess"

SUBSYSTEM=="tty", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="615c", \\
    ATTRS{serial}=="$SERIAL", ATTRS{bInterfaceNumber}=="04", \\
    SYMLINK+="cynthion-apl", MODE="0664", TAG+="uaccess"

# Cynthion USB device node (analyzer / facedancer gateware)
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="615b", \\
    ATTRS{serial}=="$SERIAL", MODE="0664", TAG+="uaccess"

# Apollo DFU bootloader mode
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="60e6", \\
    MODE="0664", TAG+="uaccess"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --attr-match=idVendor=1d50

echo ""
echo "Reload done. Symlinks after reconnect:"
echo "  /dev/cynthion-rv0  →  ttyACM interface 0 (VexRiscv UART)"
echo "  /dev/cynthion-fpg  →  ttyACM interface 2 (FPGA events)"
echo "  /dev/cynthion-apl  →  ttyACM interface 4 (Apollo console)"
