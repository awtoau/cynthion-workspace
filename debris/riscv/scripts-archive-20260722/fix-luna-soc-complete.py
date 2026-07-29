#!/usr/bin/env python3
"""
Complete luna-soc CSR Register fixer for Amaranth 0.5.x compatibility.
Systematically finds and fixes ALL annotation-only CSR.Register classes.
"""

import re
from pathlib import Path

BASE_PATH = Path("/home/dan/git/awtoau/awto-luna-soc")

def parse_and_fix_file(file_path):
    """Parse file, find CSR.Register classes, and fix annotation-only ones."""
    content = file_path.read_text()
    original = content

    # Pattern to find class definitions with their content until the next class or end
    # Match: class Name(csr.Register...): ... multiple field definitions
    pattern = r'(    class (\w+)\(csr\.Register[^)]*\):((?:\n(?!    class \w+)[^\n]*)*?)(?=\n    (?:class |def )|\n\nclass |\Z))'

    def fix_class(match):
        """Fix a single class definition."""
        full_match = match.group(0)
        class_header = match.group(1)
        class_name = match.group(2)
        class_body = match.group(3)

        # Check if already has __init__
        if 'def __init__' in class_body:
            return full_match

        # Find all field definitions: name: csr.Field(...)
        field_pattern = r'\n(\s+)(\w+)\s*:\s*(csr\.Field\([^)]*(?:\([^)]*\))?[^)]*\))'
        fields = {}
        field_indent = None

        for field_match in re.finditer(field_pattern, class_body):
            indent = field_match.group(1)
            name = field_match.group(2)
            definition = field_match.group(3)
            if field_indent is None:
                field_indent = indent
            fields[name] = definition

        if not fields:
            return full_match

        # Build replacement with __init__
        indent = field_indent or '        '
        init_lines = [f'{indent}def __init__(self):']
        init_lines.append(f'{indent}    super().__init__({{')
        for name, definition in fields.items():
            init_lines.append(f'{indent}        "{name}": {definition},')
        init_lines.append(f'{indent}    }})')

        # Replace field definitions with __init__
        # Remove the original field lines
        new_body = class_body
        for name in fields.keys():
            # Remove field definition lines
            new_body = re.sub(
                rf'\n\s+{re.escape(name)}\s*:\s*csr\.Field\([^)]*(?:\([^)]*\))?[^)]*\)',
                '',
                new_body
            )

        # Add __init__ after docstring/comments
        # Find where to insert (after class header + docstring/comments)
        insert_pos = len(class_header)

        # Check for docstring
        docstring_match = re.search(r'("""[^"]*"""|\'\'\'[^\']*\'\'\')', class_body)
        if docstring_match:
            insert_pos = len(class_header) + docstring_match.end()

        return class_header + class_body[:insert_pos] + '\n' + '\n'.join(init_lines) + class_body[insert_pos:]

    fixed = re.sub(pattern, fix_class, content, flags=re.MULTILINE | re.DOTALL)

    if fixed != original:
        file_path.write_text(fixed)
        return True
    return False


def main():
    """Fix all remaining luna-soc files."""
    files_to_fix = [
        "luna_soc/gateware/core/ila.py",
        "luna_soc/gateware/core/usb2/ep_control.py",
        "luna_soc/gateware/core/usb2/ep_in.py",
        "luna_soc/gateware/core/usb2/ep_out.py",
    ]

    fixed_count = 0
    print("Fixing remaining luna-soc CSR Register classes...\n")

    for file_rel in files_to_fix:
        file_path = BASE_PATH / file_rel
        if not file_path.exists():
            print(f"⚠ {file_rel}: File not found")
            continue

        try:
            if parse_and_fix_file(file_path):
                print(f"✓ {file_rel}: Fixed")
                fixed_count += 1
            else:
                print(f"  {file_rel}: No changes needed")
        except Exception as e:
            print(f"✗ {file_rel}: Error - {e}")

    print(f"\n{'='*60}")
    print(f"✓ Fixed {fixed_count} files")
    if fixed_count == len([f for f in files_to_fix if (BASE_PATH / f).exists()]):
        print("✓ All remaining files patched successfully!")
        print("\nNext steps:")
        print("  1. Commit changes: git add -A && git commit")
        print("  2. Push: git push")
        print("  3. Reinstall: pip install -e /path/to/cynthion")
        print("  4. Test: ./scripts/install.py --parallel setup")


if __name__ == "__main__":
    main()
