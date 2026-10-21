#!/usr/bin/env python3
"""Package only the V2 workbench assets for an existing compatible deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['library_web/index.html', 'library_web/style.css', 'library_web/app.js',
         'library_web/workdays.js', 'library_web/workspace.js', 'library_web/brand.svg']


def main():
    parser = argparse.ArgumentParser(description='建立 V2 介面更新包；不包含私人資料或重新建立後台')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).expanduser().absolute()
    paths = FILES + sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / 'assets/icons').glob('*.svg'))
    manifest = {'product': 'Blueprint Studio Open Source', 'version': '2.0.0', 'database_schema': 4,
                'database_migration': 'none', 'requires': 'Existing authenticated Blueprint API; Sites adapters must preserve their current auth and storage.',
                'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in paths: archive.write(ROOT / name, name)
        archive.writestr('workbench-release.json', json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'archive': str(output), 'version': '2.0.0', 'files': len(paths)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
