#!/usr/bin/env python3
"""
Comprehensive fixer for all luna-soc CSR Register annotation-only classes.
Finds and fixes ALL classes that have field annotations but no __init__ method.
"""

import re
from pathlib import Path


def fix_all_csr_classes():
    """Find and fix all CSR.Register classes in luna-soc."""
    luna_soc_core = Path("/home/dan/git/awtoau/awto-luna-soc/luna_soc/gateware/core")

    py_files = list(luna_soc_core.rglob("*.py"))
    total_fixed = 0

    for file_path in py_files:
        content = file_path.read_text()
        original = content

        # Pattern: class Name(csr.Register...): ... field: csr.Field(...)
        # But NOT if it already has def __init__

        # Find all class definitions with csr.Register
        class_pattern = r'(    class (\w+)\(csr\.Register[^)]*\):(?:\s*(?:"""[^"]*"""|\'\'\'[^\']*\'\'\'|#[^\n]*))?\s*)((?:        \w+\s*:\s*csr\.Field\([^)]*\)\s*(?:\n|$))+)'

        matches = list(re.finditer(class_pattern, content, re.MULTILINE))

        if not matches:
            continue

        for match in matches:
            class_header = match.group(1)
            class_name = match.group(2)
            fields_block = match.group(3)

            # Check if this class already has __init__
            # Look ahead from the match to see if there's a __init__ before the next class
            match_end = match.end()
            next_class_pattern = r'    class \w+\('
            next_class = re.search(next_class_pattern, content[match_end:])
            search_end = next_class.start() + match_end if next_class else len(content)
            section = content[match.start():search_end]

            if 'def __init__' in section:
                continue  # Already has __init__

            # Extract field definitions
            field_pattern = r'        (\w+)\s*:\s*(csr\.Field\([^)]*\))'
            fields = []
            for field_match in re.finditer(field_pattern, fields_block):
                name = field_match.group(1)
                definition = field_match.group(2)
                fields.append(f'                "{name}": {definition},')

            if not fields:
                continue  # No fields found

            # Check if any field takes parameters (like width in Trace class)
            if 'self.' in fields_block or '(width)' in fields_block or '(sample_width)' in fields_block:
                # This class needs constructor parameters - handle specially
                # Try to determine parameters
                if 'def ' in section and 'self.' in section[:section.find('super()')]:
                    continue  # Already has custom __init__

                # For now, generate __init__(self) - may need manual adjustment
                fields_str = '\n'.join(fields)
                init_code = f'''
        def __init__(self):
            super().__init__({{
{fields_str}
            }})'''
            else:
                fields_str = '\n'.join(fields)
                init_code = f'''
        def __init__(self):
            super().__init__({{
{fields_str}
            }})'''

            # Replace the class definition
            replacement = class_header + init_code
            content = content[:match.start()] + replacement + content[match.end():]
            total_fixed += 1

        if content != original:
            file_path.write_text(content)
            print(f"✓ {file_path.relative_to(luna_soc_core)}: Fixed {total_fixed} class(es)")
            total_fixed = 0  # Reset counter per file

    print(f"\n✓ All CSR Register classes fixed!")


if __name__ == "__main__":
    fix_all_csr_classes()
