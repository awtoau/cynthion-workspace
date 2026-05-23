#!/usr/bin/env python3
"""
Fix luna-soc CSR Register classes for Amaranth 0.5.x compatibility.

This script manually processes each file to convert annotation-only CSR Register
classes to proper __init__ methods.
"""

import re
from pathlib import Path


def fix_uart():
    """Fix uart.py - UART peripheral registers."""
    file_path = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core/uart.py")
    content = file_path.read_text()

    # Fix TxData
    content = content.replace(
        """    class TxData(csr.Register, access="w"):
        \"\"\"valid to write to when tx_rdy is high, will trigger a transmit\"\"\"
        data: csr.Field(csr.action.W, unsigned(8))""",
        """    class TxData(csr.Register, access="w"):
        \"\"\"valid to write to when tx_rdy is high, will trigger a transmit\"\"\"
        def __init__(self):
            super().__init__({
                "data": csr.Field(csr.action.W, unsigned(8))
            })"""
    )

    # Fix RxData
    content = content.replace(
        """    class RxData(csr.Register, access="r"):
        \"\"\"valid to read from when rx_avail is high, last received byte\"\"\"
        data: csr.Field(csr.action.R, unsigned(8))""",
        """    class RxData(csr.Register, access="r"):
        \"\"\"valid to read from when rx_avail is high, last received byte\"\"\"
        def __init__(self):
            super().__init__({
                "data": csr.Field(csr.action.R, unsigned(8))
            })"""
    )

    # Fix TxReady
    content = content.replace(
        """    class TxReady(csr.Register, access="r"):
        \"\"\"is '1' when 1-byte transmit buffer is empty\"\"\"
        ready: csr.Field(csr.action.R, unsigned(1))""",
        """    class TxReady(csr.Register, access="r"):
        \"\"\"is '1' when 1-byte transmit buffer is empty\"\"\"
        def __init__(self):
            super().__init__({
                "ready": csr.Field(csr.action.R, unsigned(1))
            })"""
    )

    # Fix RxAvail
    content = content.replace(
        """    class RxAvail(csr.Register, access="r"):
        \"\"\"is '1' when there's at least one byte in the receive buffer\"\"\"
        avail: csr.Field(csr.action.R, unsigned(1))""",
        """    class RxAvail(csr.Register, access="r"):
        \"\"\"is '1' when there's at least one byte in the receive buffer\"\"\"
        def __init__(self):
            super().__init__({
                "avail": csr.Field(csr.action.R, unsigned(1))
            })"""
    )

    # Fix BaudRate
    content = content.replace(
        """    class BaudRate(csr.Register, access="rw"):
        \"\"\"8-bit baud rate divisor used in a 16-cycle accumulator; baud_freq = uart_freq / (16 * (divisor + 1))\"\"\"
        divisor: csr.Field(csr.action.RW, unsigned(8))""",
        """    class BaudRate(csr.Register, access="rw"):
        \"\"\"8-bit baud rate divisor used in a 16-cycle accumulator; baud_freq = uart_freq / (16 * (divisor + 1))\"\"\"
        def __init__(self):
            super().__init__({
                "divisor": csr.Field(csr.action.RW, unsigned(8))
            })"""
    )

    file_path.write_text(content)
    print("✓ Fixed uart.py")


def fix_timer():
    """Fix timer.py - Timer peripheral registers."""
    file_path = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core/timer.py")
    content = file_path.read_text()

    replacements = [
        (
            """    class Enable(csr.Register, access="rw"):
        enable: csr.Field(csr.action.RW, unsigned(1))""",
            """    class Enable(csr.Register, access="rw"):
        def __init__(self):
            super().__init__({
                "enable": csr.Field(csr.action.RW, unsigned(1))
            })"""
        ),
        (
            """    class Mode(csr.Register, access="rw"):
        oneshot: csr.Field(csr.action.RW, unsigned(1))""",
            """    class Mode(csr.Register, access="rw"):
        def __init__(self):
            super().__init__({
                "oneshot": csr.Field(csr.action.RW, unsigned(1))
            })"""
        ),
        (
            """    class Reload(csr.Register, access="rw"):
        reload: csr.Field(csr.action.RW, unsigned(32))""",
            """    class Reload(csr.Register, access="rw"):
        def __init__(self):
            super().__init__({
                "reload": csr.Field(csr.action.RW, unsigned(32))
            })"""
        ),
        (
            """    class Counter(csr.Register, access="r"):
        counter: csr.Field(csr.action.R, unsigned(32))""",
            """    class Counter(csr.Register, access="r"):
        def __init__(self):
            super().__init__({
                "counter": csr.Field(csr.action.R, unsigned(32))
            })"""
        ),
    ]

    for old, new in replacements:
        content = content.replace(old, new)

    file_path.write_text(content)
    print("✓ Fixed timer.py")


def fix_ila():
    """Fix ila.py - Logic analyzer peripheral registers."""
    file_path = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core/ila.py")
    content = file_path.read_text()

    replacements = [
        (
            """    class Control(csr.Register, access="w"):
        \"\"\"Triggers when writing a 1.\"\"\"
        trigger: csr.Field(csr.action.W, unsigned(1))""",
            """    class Control(csr.Register, access="w"):
        \"\"\"Triggers when writing a 1.\"\"\"
        def __init__(self):
            super().__init__({
                "trigger": csr.Field(csr.action.W, unsigned(1))
            })"""
        ),
        (
            """    class Trace(csr.Register, access="w"):
        \"\"\"Bits to append to the captured trace.\"\"\"
        bits: csr.Field(csr.action.W, unsigned(self.sample_width))""",
            """    class Trace(csr.Register, access="w"):
        \"\"\"Bits to append to the captured trace.\"\"\"
        def __init__(self, sample_width):
            super().__init__({
                "bits": csr.Field(csr.action.W, unsigned(sample_width))
            })"""
        ),
    ]

    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)

    file_path.write_text(content)
    print("✓ Fixed ila.py")


def fix_device():
    """Fix usb2/device.py - USB device registers."""
    file_path = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core/usb2/device.py")
    content = file_path.read_text()

    # Read the file to find exact patterns
    import re
    # Find and replace all simple field annotations with __init__
    pattern = r'(    class (\w+)\(csr\.Register[^)]*\):\s*(?:"[^"]*"|\'[^\']*\')?\s*)((?:        \w+:\s*csr\.Field\([^)]*\)\s*)+)'

    def replace_class(match):
        class_header = match.group(1)
        fields_block = match.group(3)

        # Extract individual fields
        field_pattern = r'        (\w+):\s*(csr\.Field\([^)]*\))'
        fields = []
        for field_match in re.finditer(field_pattern, fields_block):
            name = field_match.group(1)
            definition = field_match.group(2)
            fields.append(f'                "{name}": {definition},')

        if not fields:
            return match.group(0)

        fields_str = '\n'.join(fields)
        init = f'''
        def __init__(self):
            super().__init__({{
{fields_str}
            }})'''

        return class_header + init

    content = re.sub(pattern, replace_class, content)
    file_path.write_text(content)
    print("✓ Fixed usb2/device.py")


def fix_ep_control():
    """Fix usb2/ep_control.py - Endpoint control registers."""
    file_path = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core/usb2/ep_control.py")
    content = file_path.read_text()

    import re
    pattern = r'(    class (\w+)\(csr\.Register[^)]*\):\s*)((?:        \w+:\s*csr\.Field\([^)]*\)\s*)+)'

    def replace_class(match):
        class_header = match.group(1)
        fields_block = match.group(3)

        field_pattern = r'        (\w+):\s*(csr\.Field\([^)]*\))'
        fields = []
        for field_match in re.finditer(field_pattern, fields_block):
            name = field_match.group(1)
            definition = field_match.group(2)
            fields.append(f'                "{name}": {definition},')

        if not fields:
            return match.group(0)

        fields_str = '\n'.join(fields)
        init = f'''
        def __init__(self):
            super().__init__({{
{fields_str}
            }})'''

        return class_header + init

    content = re.sub(pattern, replace_class, content)
    file_path.write_text(content)
    print("✓ Fixed usb2/ep_control.py")


def fix_ep_in():
    """Fix usb2/ep_in.py - Endpoint IN registers."""
    file_path = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core/usb2/ep_in.py")
    if not file_path.exists():
        return

    content = file_path.read_text()
    import re
    pattern = r'(    class (\w+)\(csr\.Register[^)]*\):\s*)((?:        \w+:\s*csr\.Field\([^)]*\)\s*)+)'

    def replace_class(match):
        class_header = match.group(1)
        fields_block = match.group(3)

        field_pattern = r'        (\w+):\s*(csr\.Field\([^)]*\))'
        fields = []
        for field_match in re.finditer(field_pattern, fields_block):
            name = field_match.group(1)
            definition = field_match.group(2)
            fields.append(f'                "{name}": {definition},')

        if not fields:
            return match.group(0)

        fields_str = '\n'.join(fields)
        init = f'''
        def __init__(self):
            super().__init__({{
{fields_str}
            }})'''

        return class_header + init

    content = re.sub(pattern, replace_class, content)
    file_path.write_text(content)
    print("✓ Fixed usb2/ep_in.py")


def fix_ep_out():
    """Fix usb2/ep_out.py - Endpoint OUT registers."""
    file_path = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core/usb2/ep_out.py")
    if not file_path.exists():
        return

    content = file_path.read_text()
    import re
    pattern = r'(    class (\w+)\(csr\.Register[^)]*\):\s*)((?:        \w+:\s*csr\.Field\([^)]*\)\s*)+)'

    def replace_class(match):
        class_header = match.group(1)
        fields_block = match.group(3)

        field_pattern = r'        (\w+):\s*(csr\.Field\([^)]*\))'
        fields = []
        for field_match in re.finditer(field_pattern, fields_block):
            name = field_match.group(1)
            definition = field_match.group(2)
            fields.append(f'                "{name}": {definition},')

        if not fields:
            return match.group(0)

        fields_str = '\n'.join(fields)
        init = f'''
        def __init__(self):
            super().__init__({{
{fields_str}
            }})'''

        return class_header + init

    content = re.sub(pattern, replace_class, content)
    file_path.write_text(content)
    print("✓ Fixed usb2/ep_out.py")


def main():
    print("Fixing luna-soc CSR Register classes for Amaranth 0.5.x...\n")
    fix_uart()
    fix_timer()
    fix_ila()
    fix_device()
    fix_ep_control()
    fix_ep_in()
    fix_ep_out()
    print("\n✓ All luna-soc files fixed!")


if __name__ == "__main__":
    main()
