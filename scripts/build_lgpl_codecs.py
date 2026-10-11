"""Build the bundled decoder from unmodified LGPL-only FFmpeg sources."""
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import urllib.request
import tarfile

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz'
SHA = '8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e'

def build():
    folder = ROOT / '.build/lgpl-codecs'
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / 'ffmpeg-9.0.2.tar.xz'
    if not archive.exists(): urllib.request.urlretrieve(URL, archive)
    if sha256(archive.read_bytes()).hexdigest() != SHA: raise RuntimeError('FFmpeg source checksum mismatch')
    source = folder / 'ffmpeg-9.0.2'
    if not source.exists():
        with tarfile.open(archive) as tar: tar.extractall(folder, filter='data')
    stage = folder / 'stage'
    if not list((stage / 'opt/semantic-search-codecs/lib').glob('libavcodec.*.dylib')):
        flags = ['--prefix=/opt/semantic-search-codecs', '--disable-gpl', '--disable-nonfree',
                 '--disable-version3', '--disable-autodetect', '--disable-programs', '--disable-doc',
                 '--disable-debug', '--enable-shared', '--disable-static', '--disable-network',
                 '--disable-encoders', '--enable-encoder=aac', '--enable-encoder=h264_videotoolbox',
                 '--enable-encoder=mpeg4', '--enable-videotoolbox', '--enable-audiotoolbox',
                 '--extra-cflags=-ffile-prefix-map=.=./source']
        subprocess.run(['./configure', *flags], cwd=source, check=True)
        subprocess.run(['make', '-j4'], cwd=source, check=True)
        subprocess.run(['make', 'install', f'DESTDIR={stage}'], cwd=source, check=True)
    return stage / 'opt/semantic-search-codecs/lib'

def install(libs, site):
    target = site / 'av/.dylibs'
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    for file in libs.glob('*.dylib'):
        if not file.is_symlink(): shutil.copyfile(file, target / file.name)
    natives = list((site / 'av').rglob('*.so')) + list(target.glob('*.dylib'))
    for file in natives:
        relative = '@loader_path/' + __import__('os').path.relpath(target, file.parent)
        output = subprocess.check_output(['otool', '-L', str(file)], text=True)
        args = []
        if file.suffix == '.dylib': args += ['-id', '@rpath/' + file.name]
        for line in output.splitlines()[1:]:
            dependency = line.strip().split(' (')[0]
            if '/libav' in dependency or '/libsw' in dependency:
                name = Path(dependency).name
                # Wheels append their own hash to library names; match by major ABI.
                matches = list(target.glob(name))
                if not matches:
                    stem = name.split('.')[0].split('-')[0]
                    matches = list(target.glob(stem + '.*.dylib'))
                if len(matches) != 1: raise RuntimeError(f'Cannot resolve {name}')
                args += ['-change', dependency, relative + '/' + matches[0].name]
        if args: subprocess.run(['install_name_tool', *args, str(file)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(file)], check=True, stdout=subprocess.DEVNULL)

if __name__ == '__main__':
    libs = build()
    python = ROOT / '.build/runtime/python'
    marker = python / '.lgpl-pyav'
    if not marker.exists():
        import os
        env = dict(os.environ, PKG_CONFIG_PATH=str(libs/'pkgconfig'),
                   PKG_CONFIG_SYSROOT_DIR=str(ROOT/'.build/lgpl-codecs/stage'),
                   CFLAGS='-g0 -ffile-prefix-map=/Users=/build -ffile-prefix-map=/private/var/folders=/build')
        subprocess.run([str(python/'bin/python3.12'), '-m', 'pip', 'install', '--no-binary=av',
                        '--no-cache-dir', '--no-deps', '--force-reinstall', '--no-compile', 'av==19.0.1'], env=env, check=True)
        marker.write_text('FFmpeg 9.0.2 LGPL-only\n')
    install(libs, python / 'lib/python3.12/site-packages')
