"""Build/install a verified offline media bundle for Ubuntu 24 x86_64.

Prepare before Web starts. No global install, activation or network download.
The bundle contains tools, speech data, non-glibc shared libraries and CJK font.
Pass the delivered bundle SHA256 when installing on another code checkout.
"""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def directory(code=ROOT):
    return Path(code).parent / 'dependencies/native/home-report-media-v2'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build(tools, bundle):
    tools = Path(tools)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root/'bin').mkdir(); (root/'lib').mkdir(); (root/'share').mkdir()
        speech = tools/'espeak/usr/bin/espeak-ng'
        shutil.copy2(speech, root/'bin/espeak-ng')
        shutil.copy2(tools/'imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v9.0.2', root/'bin/ffmpeg')
        # Pin transitive dependencies from the tested Ubuntu installation.
        libs = subprocess.check_output(['ldd', str(speech)], text=True)
        for line in libs.splitlines():
            if '=>' not in line: continue
            name, resolved = line.split('=>', 1)
            path = Path(resolved.strip().split()[0])
            if name.strip() not in {'libc.so.6','libm.so.6'} and path.is_file():
                shutil.copy2(path, root/'lib'/name.strip())
        shutil.copytree('/usr/lib/x86_64-linux-gnu/espeak-ng-data', root/'share/espeak-ng-data')
        shutil.copy2('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', root/'share/font.ttc')
        files = {str(p.relative_to(root)): sha(p) for p in root.rglob('*') if p.is_file()}
        (root/'manifest.json').write_text(json.dumps({'platform':'linux/x86_64','glibc_min':'2.39', 'versions':{'ffmpeg':'9.0.2 imageio build','espeak-ng':'1.51 Ubuntu noble'},'files':files},indent=2))
        with tarfile.open(bundle,'w:gz') as archive:
            for child in root.iterdir(): archive.add(child,arcname=child.name)
    return sha(bundle)


def install(bundle, expected, code=ROOT):
    if platform.system()!='Linux' or platform.machine()!='x86_64': raise RuntimeError('This verified bundle targets Linux x86_64')
    if sha(bundle)!=expected: raise ValueError('Media bundle SHA256 mismatch')
    destination=directory(code); destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        receipt=json.loads((destination/'receipt.json').read_text())
        if receipt['bundle_sha256']==expected: return destination
        raise FileExistsError('Existing media bundle differs; use a versioned destination before replacement')
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        root=Path(temp)
        with tarfile.open(bundle) as archive: archive.extractall(root,filter='data')
        manifest=json.loads((root/'manifest.json').read_text())
        for name, expected_file in manifest['files'].items():
            path=root/name
            if root not in path.resolve().parents or sha(path)!=expected_file: raise ValueError('Media bundle content mismatch')
        (root/'receipt.json').write_text(json.dumps({'bundle_sha256':expected,'platform':manifest['platform']}))
        root.rename(destination)
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--build-from');parser.add_argument('--bundle',required=True);parser.add_argument('--sha256');parser.add_argument('--code',default=str(ROOT))
    args=parser.parse_args()
    print(build(args.build_from,args.bundle) if args.build_from else install(args.bundle,args.sha256,args.code))
