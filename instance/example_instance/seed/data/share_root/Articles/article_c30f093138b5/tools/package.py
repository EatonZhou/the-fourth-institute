"""Package the article sources and editable figures, excluding build previews."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json
import shutil

root = Path(__file__).resolve().parents[1]
checks = root / 'build' / 'checks.json'
if checks.exists():
    shutil.copy2(checks, root / 'editorial' / 'checks.json')
required = ['main.tex', 'references.tex', 'README.md',
            'figures-editable.pptx', 'editorial/checks.json']
for name in required:
    if not (root / name).is_file():
        raise SystemExit(f'Missing {name}; build and check the article first.')
files = [root / name for name in required[:4]]
for folder in ['chapters', 'figures', 'tools', 'editorial']:
    files.extend(p for p in (root / folder).rglob('*')
                 if p.is_file() and p.suffix != '.png'
                 and '__pycache__' not in p.parts and p.suffix != '.pyc')
archive = root / 'source-package.zip'
with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(set(files)):
        z.write(p, p.relative_to(root).as_posix())
with ZipFile(archive) as z:
    bad = z.testzip()
    if bad:
        raise SystemExit(f'Corrupt archive member: {bad}')
    for name in required:
        if name not in z.namelist():
            raise SystemExit(f'Archive missing {name}')
    count = len(z.infolist())
print(json.dumps({'archive': str(archive), 'files': count,
                  'bytes': archive.stat().st_size,
                  'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()},
                 ensure_ascii=False, indent=2))
