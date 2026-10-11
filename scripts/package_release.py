"""Create user-facing archives; source builds remain available through GitHub."""
import argparse
from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PRIVATE_PATH=re.compile(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/')


def audit(name, content):
    if name.endswith('.zip'):
        with zipfile.ZipFile(BytesIO(content)) as runtime:
            if runtime.testzip() is not None: raise ValueError('Bundled runtime failed CRC verification')
            own_path = str(Path.home()).encode() + b'/'
            for entry in runtime.namelist():
                if entry.endswith('/'): continue
                data = runtime.read(entry)
                if own_path in data: raise ValueError(f'Personal machine path in runtime: {entry}')
                if entry.endswith(('.pyc', '.map', '/direct_url.json')) or '/__pycache__/' in entry:
                    raise ValueError(f'Build metadata in runtime: {entry}')
                if '/site-packages/tinysearch/' in entry: audit(entry, data)
        return
    if name.endswith('.png'):return
    if PRIVATE_PATH.search(content): raise ValueError(f'Personal path in release: {name}')
    if re.search(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,})\b',content):
        raise ValueError(f'Credential in release: {name}')


def main():
    parser=argparse.ArgumentParser(description='Package prebuilt launcher extensions')
    parser.add_argument('--raycast',type=Path,required=True)
    args=parser.parse_args()
    version=json.loads((ROOT/'extension/package.json').read_text())['version']
    output=ROOT.parent
    installer=output/f'semantic-search-v{version}.zip'
    bundle=ROOT/'extension/dist'
    instructions=(ROOT/'docs/INSTALL.md').read_bytes()
    with zipfile.ZipFile(installer,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        archive.writestr('Semantic Search/INSTALL.md',instructions)
        for path in sorted(bundle.rglob('*')):
            if not path.is_file():continue
            name=path.relative_to(bundle).as_posix();content=path.read_bytes();audit(name,content)
            compression=zipfile.ZIP_STORED if name.endswith('runtime.zip') else zipfile.ZIP_DEFLATED
            archive.writestr('Semantic Search/'+name,content,compress_type=compression)
    # The official Raycast bundler includes maps with absolute source filenames.
    # Strip maps and their references from the distributable build.
    raw=args.raycast;clean=raw.with_suffix('.clean.rayext')
    with zipfile.ZipFile(raw) as source, zipfile.ZipFile(clean,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as target:
        for name in source.namelist():
            if name.endswith('.map') or name.endswith('/'):continue
            content=source.read(name)
            if name.endswith('.js'):content=re.sub(rb'(?m)^//# sourceMappingURL=.*\n?',b'',content)
            audit(name,content)
            target.writestr(name,content,compress_type=zipfile.ZIP_STORED if name.endswith('runtime.zip') else zipfile.ZIP_DEFLATED)
    clean.replace(raw)
    lines=[]
    sources=output/f'semantic-search-native-sources-v{version}.zip'
    if not sources.exists():
        raise ValueError('Build corresponding native sources before packaging the release')
    for path in [installer,raw,sources]:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:raise ValueError('Archive failed CRC verification')
        lines.append(f'{sha256(path.read_bytes()).hexdigest()}  {path.name}')
        print(f'{path.name}: {path.stat().st_size/1e6:.1f} MB')
    (output/f'SHA256SUMS-v{version}.txt').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':main()
