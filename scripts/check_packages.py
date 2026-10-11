"""Gate companion/Store release assets against one sealed runtime manifest."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile
from package_release import audit

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--public', action='store_true', help='Require signed/notarized public companion assets')
    args = parser.parse_args()
    expected = json.loads((ROOT / 'extension/assets/runtime-manifest.json').read_text())
    version = json.loads((ROOT / 'extension/package.json').read_text())['version']
    assert expected['version'] == version, 'Runtime version differs'
    sources = ROOT.parent / f'semantic-search-native-sources-v{version}.zip'
    assert sources.is_file(), 'Corresponding native sources are missing'
    with zipfile.ZipFile(sources) as archive:
        assert archive.testzip() is None, 'Native source archive is corrupt'
        source_manifest = json.loads((ROOT / 'packaging/native-sources.json').read_text())
        assert json.loads(archive.read('manifest.json')) == source_manifest, 'Native source manifest differs'
        for entry in source_manifest:
            assert sha256(archive.read(entry['name'])).hexdigest() == entry['sha256'], 'Native source checksum differs: ' + entry['name']
        for name, source in [('build_lgpl_codecs.py', ROOT / 'scripts/build_lgpl_codecs.py'), ('LICENSE', ROOT / 'LICENSE')]:
            assert archive.read(name) == source.read_bytes(), 'Native build recipe/license differs'
        for path in (ROOT / 'packaging/codec-notices').rglob('*'):
            if path.is_file():
                name = 'NOTICES/' + path.relative_to(ROOT / 'packaging/codec-notices').as_posix()
                assert archive.read(name) == path.read_bytes(), 'Native dependency notice differs'
    store = ROOT.parent / f'semantic-search-store-v{version}-review.zip'
    with zipfile.ZipFile(store) as archive:
        assert archive.testzip() is None, 'Store archive is corrupt'
        assert json.loads(archive.read('semantic-search/assets/runtime-manifest.json')) == expected, 'Store/runtime hashes differ'
        for name in archive.namelist():
            if name.endswith('/'): continue
            assert 'node_modules' not in name and not name.endswith(('runtime.zip', '.map', '.pyc')), 'Store contains executable dependencies/build metadata'
            audit(name, archive.read(name))
    with zipfile.ZipFile(args.installer) as archive:
        assert archive.testzip() is None, 'Installer archive is corrupt'
        prefix = 'Semantic Search Engine Installer.app/Contents/Resources/'
        assert json.loads(archive.read(prefix + 'runtime-manifest.json')) == expected, 'Installer/runtime hashes differ'
        payload = archive.read(prefix + 'runtime.zip')
        assert sha256(payload).hexdigest() == expected['sha256'], 'Installer payload checksum differs'
        audit('runtime.zip', payload)
        for name in archive.namelist():
            if name.endswith('/') or name.startswith('__MACOSX/') or name.endswith('runtime.zip'): continue
            audit(name, archive.read(name))
    if args.public:
        assert expected.get('developer_id_signed'), 'Public release requires a Developer ID signed runtime'
        assert not any(marker in args.installer.name for marker in ('unsigned', 'unnotarized')), 'Public installer is a test build'
        with tempfile.TemporaryDirectory(prefix='semantic-search-notary-check-') as directory:
            subprocess.run(['/usr/bin/ditto', '-x', '-k', str(args.installer), directory], check=True)
            app = Path(directory) / 'Semantic Search Engine Installer.app'
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
            subprocess.run(['xcrun', 'stapler', 'validate', str(app)], check=True)
            subprocess.run(['/usr/sbin/spctl', '--assess', '--type', 'execute', str(app)], check=True)
    print('Matching installer/Store payloads, source archive and privacy audit passed.' + (' Public signing checks passed.' if args.public else ' Local preview only; signing/notarization not verified.'))


if __name__ == '__main__':
    main()
