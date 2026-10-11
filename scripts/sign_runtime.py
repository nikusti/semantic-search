"""Developer ID sign every nested Mach-O before sealing the engine payload."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MACHO = {bytes.fromhex(value) for value in ('feedface', 'feedfacf', 'cefaedfe', 'cffaedfe', 'cafebabe', 'bebafeca')}


def sign_runtime(identity):
    assets = ROOT / 'extension/assets'
    manifest = json.loads((assets / 'runtime-manifest.json').read_text())
    archive = assets / 'runtime.zip'
    if sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Runtime checksum mismatch before signing')
    if manifest.get('developer_id_signed'):
        # Preserve one immutable payload across installer, ZIP and Store builds.
        # To change identities, build a fresh runtime before signing again.
        return manifest
    (ROOT / '.build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='signed-runtime-', dir=ROOT / '.build') as temporary:
        runtime = Path(temporary)
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), str(runtime)], check=True)
        for path in sorted(runtime.rglob('*')):
            if not path.is_file(): continue
            with path.open('rb') as stream: magic = stream.read(4)
            if magic not in MACHO: continue
            command = ['/usr/bin/codesign', '--force', '--timestamp', '--options', 'runtime', '--sign', identity]
            if path.name.startswith('python3'):
                command.extend(['--entitlements', str(ROOT / 'packaging/python-entitlements.plist')])
            subprocess.run([*command, str(path)], check=True)
            subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(path)], check=True)
        output = assets / 'runtime.signed.zip'
        total = 0
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
            for path in sorted(runtime.rglob('*')):
                if not path.is_file(): continue
                content = path.read_bytes(); total += len(content)
                info = zipfile.ZipInfo(path.relative_to(runtime).as_posix())
                info.date_time = (2026, 10, 11, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
                target.writestr(info, content)
        output.replace(archive)
    manifest.update(sha256=sha256(archive.read_bytes()).hexdigest(), unpacked_bytes=total, developer_id_signed=True)
    (assets / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--identity', required=True)
    args = parser.parse_args()
    sign_runtime(args.identity)
