"""Maintainer-only: build the portable, offline engine archive on Apple silicon."""
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def clean_metadata(python):
    # Test frameworks and vendor test fixtures are not part of the engine.
    site = python/'lib/python3.12/site-packages'
    for path in list(site.iterdir()):
        if path.name in {'pytest', '_pytest', 'reportlab'} or path.name.startswith(('pytest-', 'reportlab-')):
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
    for path in list(site.rglob('*')):
        if path.is_dir() and path.name in {'tests', 'fixtures'} and path.exists():
            shutil.rmtree(path)
    (site/'transformers/testing_utils.py').unlink(missing_ok=True)
    # Do not distribute local build paths, installers, caches or bytecode.
    for path in list(python.rglob('*')):
        if path.is_dir() and path.name=='__pycache__': shutil.rmtree(path,ignore_errors=True)
        elif path.is_file() and (path.suffix=='.pyc' or path.name=='direct_url.json'): path.unlink(missing_ok=True)
    for path in (python/'bin').iterdir():
        if path.name not in {'python3','python3.12'}:
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
    for path in python.rglob('*.json'):
        text=path.read_text()
        sanitized=re.sub(r'/(?:Users|home)/[A-Za-z0-9_.-]+/', '/build/', text)
        if sanitized!=text: path.write_text(sanitized)


def main():
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise SystemExit('Build this runtime on an Apple silicon Mac.')
    source=json.loads((ROOT/'packaging/runtime-source.json').read_text())
    version=json.loads((ROOT/'extension/package.json').read_text())['version']
    build=ROOT/'.build';build.mkdir(exist_ok=True)
    archive=build/'python.tar.gz'
    if not archive.exists(): urllib.request.urlretrieve(source['python_url'],archive)
    if sha256(archive.read_bytes()).hexdigest()!=source['python_sha256']:
        raise SystemExit('Portable Python checksum mismatch')
    runtime=build/'runtime';python=runtime/'python'
    if not python.exists():
        runtime.mkdir(exist_ok=True)
        with tarfile.open(archive) as tar:tar.extractall(runtime,filter='data')
    executable=python/'bin/python3.12'
    subprocess.run([str(executable),'-m','pip','install','--disable-pip-version-check','--no-deps','--no-compile','-r',str(ROOT/'requirements.lock.txt')],check=True)
    site=python/'lib/python3.12/site-packages'
    shutil.copytree(ROOT/'tinysearch',site/'tinysearch',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for pattern in ('cv2', 'opencv_python*', 'pillow_heif*', 'scipy*', 'sklearn', 'scikit_learn*',
                    'sentence_transformers*', 'joblib*', 'threadpoolctl*', 'sounddevice*', '_sounddevice*'):
        for obsolete in site.glob(pattern):
            if obsolete.is_dir(): shutil.rmtree(obsolete)
            else: obsolete.unlink()
    subprocess.run([str(executable),'-B',str(ROOT/'scripts/build_lgpl_codecs.py')],check=True)
    native=runtime/'coreml';native.mkdir(exist_ok=True)
    sources=sorted((ROOT/'native/coreml/vendor').glob('*.swift'))+[ROOT/'native/coreml/LocalAssets.swift',ROOT/'native/coreml/Worker.swift']
    subprocess.run(['swiftc','-O','-parse-as-library','-module-cache-path',str(build/'coreml-module-cache'),
                    '-file-prefix-map',str(ROOT)+'=/source','-o',str(native/'semantic-search-coreml'),*map(str,sources)],check=True)
    shutil.copytree(ROOT/'native/coreml',runtime/'COREML_SOURCES',dirs_exist_ok=True)
    from build_photos import build as build_photos
    build_photos(runtime/'photos')
    shutil.copytree(ROOT/'native/photos',runtime/'PHOTOS_SOURCES',dirs_exist_ok=True)
    clean_metadata(python)
    shutil.rmtree(runtime/'CODEC_NOTICES',ignore_errors=True)
    shutil.copytree(ROOT/'packaging/codec-notices',runtime/'CODEC_NOTICES')
    # Preserve dependency license files and list upstream source locations.
    notices=runtime/'THIRD_PARTY_NOTICES.md'
    inventory=subprocess.check_output([str(executable),'-B','-c',
        "import importlib.metadata,json; print(json.dumps([{'name':d.metadata['Name'],'version':d.version,'license':d.metadata.get('License-Expression') or d.metadata.get('License') or 'See bundled license files','urls':d.metadata.get_all('Project-URL') or [d.metadata.get('Home-page','')]} for d in importlib.metadata.distributions()]))"],text=True)
    rows=json.loads(inventory)
    text=f'# Bundled runtime notices\n\nPython is supplied by [python-build-standalone](https://github.com/astral-sh/python-build-standalone), under Python and bundled dependency licenses.\n\nThe application source is MIT licensed. Each bundled dependency retains its upstream license. License texts are retained in the Python distribution and package metadata folders. PyAV includes FFmpeg codec libraries; see `CODEC_NOTICES` for license texts, pinned build scripts, patches and exact source URLs/hashes. Complete corresponding native sources are published beside the binary release as `semantic-search-native-sources-v{version}.zip`; users may modify/replace LGPL shared libraries. Model weights are not included.\n\n'
    for row in sorted(rows,key=lambda row:row['name'].lower()):
        text+=f"## {row['name']} {row['version']}\n\n"+str(row['license'])+'\n\n'+'\n'.join(row['urls'])+'\n\n'
    notices.write_text(text)
    shutil.copyfile(ROOT/'LICENSE',runtime/'LICENSE')
    for path in python.rglob('*'):
        if path.is_file() and not path.is_symlink():
            # Our build must not retain personal absolute paths in text metadata.
            if path.suffix in {'.json','.pth'} and b'/Users/' in path.read_bytes():
                raise SystemExit(f'Nonportable metadata: {path.relative_to(runtime)}')
    output=ROOT/'extension/assets/runtime.zip'
    output.parent.mkdir(parents=True,exist_ok=True)
    total=0
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as target:
        for path in sorted(runtime.rglob('*')):
            if not path.is_file():continue
            if path.is_symlink() and not path.resolve().is_relative_to(runtime.resolve()):
                raise SystemExit('Runtime contains a link outside the bundle')
            content=path.read_bytes();total+=len(content)
            info=zipfile.ZipInfo(path.relative_to(runtime).as_posix())
            info.date_time=(2026,10,11,0,0,0);info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(stat.S_IFREG|stat.S_IMODE(path.stat().st_mode))<<16
            target.writestr(info,content)
    manifest=dict(version=version,sha256=sha256(output.read_bytes()).hexdigest(),platform='darwin-arm64',unpacked_bytes=total)
    (output.parent/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'Bundled engine: {output.stat().st_size/1e6:.1f} MB compressed, {total/1e6:.1f} MB installed')


if __name__=='__main__':main()
