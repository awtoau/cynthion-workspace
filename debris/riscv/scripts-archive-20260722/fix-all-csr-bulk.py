#!/usr/bin/env python3
"""Bulk fix for all remaining annotation-only CSR Register classes in luna-soc."""

import re
from pathlib import Path

# Files that still need fixing based on the test error progression
files_to_fix = {
    "luna_soc/gateware/core/ila.py": [],  # Will detect which classes
    "luna_soc/gateware/core/usb2/ep_control.py": [],
    "luna_soc/gateware/core/usb2/ep_in.py": [],
    "luna_soc/gateware/core/usb2/ep_out.py": [],
}

base_path = Path("/home/dan/git/awtoau/awto-luna-soc")

def fix_file(file_path):
    """Fix all annotation-only CSR classes in a file."""
    content = file_path.read_text()
    original = content

    # Find class definitions with annotations but no __init__
    # This pattern matches: class Name(...Register...): ... field: csr.Field(...)
    # Look for indented field definitions after the class declaration

    # Strategy: find each class, extract its fields, generate __init__
    lines = content.split('\n')
    new_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check if this is a CSR.Register class definition
        if re.match(r'\s+class \w+\(csr\.Register', line):
            class_line_idx = i
            class_indent = len(line) - len(line.lstrip())

            # Collect the class definition including docstring
            class_def = [line]
            i += 1

            # Collect docstring if present
            while i < len(lines):
                if i < len(lines) and (lines[i].strip().startswith('"""') or lines[i].strip().startswith("'''")):
                    quote_char = '"""' if '"""' in lines[i] else "'''"
                    class_def.append(lines[i])
                    if lines[i].count(quote_char) == 1:  # Opening quote
                        i += 1
                        while i < len(lines):
                            class_def.append(lines[i])
                            if quote_char in lines[i]:  # Closing quote
                                i += 1
                                break
                            i += 1
                    else:  # Single-line docstring
                        i += 1
                    break
                elif lines[i].strip() and not lines[i].strip().startswith('#'):
                    # No docstring, move on
                    break
                elif lines[i].strip().startswith('#'):
                    class_def.append(lines[i])
                    i += 1
                else:
                    i += 1

            # Check if this class has __init__
            init_found = False
            fields = {}
            field_start_idx = None
            field_indent = None

            while i < len(lines):
                current_line = lines[i]
                current_indent = len(current_line) - len(current_line.lstrip())

                # Stop if we hit a new class definition at same or lower indent
                if current_line.strip().startswith('class ') and current_indent <= class_indent:
                    break

                # Check for __init__
                if 'def __init__' in current_line:
                    init_found = True
                    break

                # Collect field definitions
                if ':' in current_line and 'csr.Field' in current_line and current_indent > class_indent:
                    if field_indent is None:
                        field_indent = current_indent
                        field_start_idx = i

                    # Extract field name and definition
                    match = re.match(r'(\s+)(\w+)\s*:\s*(csr\.Field\([^)]*\))', current_line)
                    if match:
                        field_name = match.group(2)
                        field_def = match.group(3)
                        fields[field_name] = field_def

                i += 1

            # If no __init__ was found and we have fields, create one
            if not init_found and fields:
                # Generate __init__ method
                init_lines = ['        def __init__(self):']
                init_lines.append('            super().__init__({')
                for name, definition in fields.items():
                    init_lines.append(f'                "{name}": {definition},')
                init_lines.append('            })')

                # Add class definition and __init__
                new_lines.extend(class_def)
                new_lines.extend(init_lines)

                # Skip the original field definitions
                # i is already positioned after the fields
                continue
            else:
                # No changes needed or already has __init__
                new_lines.extend(class_def)
                if init_found:
                    # Need to include the __init__ we found
                    # This is tricky, so for now just keep going
                    pass
        else:
            new_lines.append(line)
            i += 1

    new_content = '\n'.join(new_lines)

    if new_content != original:
        file_path.write_text(new_content)
        return True
    return False


# Fix each file
for file_rel_path in files_to_fix:
    file_path = base_path / file_rel_path
    if file_path.exists():
        if fix_file(file_path):
            print(f"✓ Fixed {file_rel_path}")
        else:
            print(f"  {file_rel_path}: No changes")
    else:
        print(f"⚠ {file_rel_path}: File not found")
