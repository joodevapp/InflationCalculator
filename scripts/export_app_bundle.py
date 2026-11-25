#!/usr/bin/env python3
"""Copy data/cpi.json into the FlutterFlow app as its bundled data (spec 11.4).

Usage:
    python scripts/export_app_bundle.py <FlutterFlow workspace folder>

Writes <workspace>/custom_code/cpi_bundle.dart; then run `./fft push` there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPI_PATH = ROOT / 'data' / 'cpi.json'


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1]) / 'custom_code' / 'cpi_bundle.dart'
    if not target.parent.is_dir():
        print(f'No custom_code folder in {sys.argv[1]}')
        return 2
    raw = CPI_PATH.read_text(encoding='utf-8')
    data = json.loads(raw)
    if "'''" in raw:
        print('cpi.json contains three quotes in a row and cannot be embedded.')
        return 1
    target.write_text(
        '// Bundled copy of data/cpi.json (spec 4.1 / 11.4), used on first run and offline.\n'
        f"// Version {data['version']}, generated {data['generatedAt']}.\n"
        '// Regenerate with worth-data/scripts/export_app_bundle.py before each release.\n'
        f"const String kBundledCpiJson = r'''{raw}''';\n",
        encoding='utf-8', newline='\n')
    print(f"Wrote {target} (version {data['version']}, {len(data['countries'])} countries).")
    return 0


if __name__ == '__main__':
    sys.exit(main())
