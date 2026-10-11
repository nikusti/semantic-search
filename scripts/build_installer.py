"""Build the explicit companion installer; Developer ID signing is optional locally."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--identity', help='Developer ID Application signing identity')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile; requires --identity')
    args = parser.parse_args()
    if args.notary_profile and not args.identity:
        parser.error('Notarization requires a Developer ID signing identity')
    manifest = json.loads((ROOT / 'extension/assets/runtime-manifest.json').read_text())
    version = json.loads((ROOT / 'extension/package.json').read_text())['version']
    if manifest['version'] != version:
        raise SystemExit('Build this version of the runtime first')
    if not (ROOT.parent / f'semantic-search-native-sources-v{version}.zip').is_file():
        raise SystemExit('Build corresponding native sources before packaging the installer')
    if args.identity:
        from sign_runtime import sign_runtime
        manifest = sign_runtime(args.identity)
    archive = ROOT / 'extension/assets/runtime.zip'
    if sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
        raise SystemExit('Runtime checksum mismatch')
    build = ROOT / '.build/companion'
    app = build / 'Semantic Search Engine Installer.app'
    shutil.rmtree(app, ignore_errors=True)
    executable = app / 'Contents/MacOS/semantic-search-installer'
    resources = app / 'Contents/Resources'
    executable.parent.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True)
    subprocess.run(['swiftc', '-O', '-parse-as-library', '-module-cache-path', str(build / 'module-cache'),
                    '-file-prefix-map', str(ROOT) + '=/source',
                    '-o', str(executable), str(ROOT / 'native/installer/InstallCore.swift'),
                    str(ROOT / 'native/installer/InstallerApp.swift')], check=True)
    shutil.copyfile(archive, resources / 'runtime.zip')
    shutil.copyfile(ROOT / 'extension/assets/runtime-manifest.json', resources / 'runtime-manifest.json')
    shutil.copyfile(ROOT / 'LICENSE', resources / 'LICENSE')
    info = dict(CFBundleIdentifier='dev.semantic-search.engine-installer', CFBundleName='Semantic Search Engine Installer',
                CFBundleDisplayName='Semantic Search Engine Installer', CFBundleExecutable=executable.name,
                CFBundlePackageType='APPL', CFBundleShortVersionString=version, CFBundleVersion=version,
                LSMinimumSystemVersion='26.0', NSHighResolutionCapable=True, CFBundleIconFile='icon.icns')
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    # Generate a normal native icon from the same project asset.
    iconset = build / 'icon.iconset'
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            output = iconset / f'icon_{size}x{size}{"@2x" if scale == 2 else ""}.png'
            subprocess.run(['/usr/bin/sips', '-z', str(size * scale), str(size * scale), str(ROOT / 'extension/assets/icon.png'),
                            '--out', str(output)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'icon.icns')], check=True)
    command = ['/usr/bin/codesign', '--force', '--sign', args.identity or '-']
    if args.identity: command.extend(['--timestamp', '--options', 'runtime'])
    subprocess.run([*command, str(app)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    if args.notary_profile:
        submission = build / 'notarization.zip'
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(app), str(submission)], check=True)
        result = subprocess.run(['xcrun', 'notarytool', 'submit', str(submission), '--keychain-profile', args.notary_profile,
                                 '--wait', '--output-format', 'json'], check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        if report.get('status') != 'Accepted':
            raise SystemExit('Notarization was not accepted. Inspect the notarytool log; do not publish.')
        subprocess.run(['xcrun', 'stapler', 'staple', str(app)], check=True)
        subprocess.run(['xcrun', 'stapler', 'validate', str(app)], check=True)
        subprocess.run(['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose', str(app)], check=True)
    suffix = '' if args.notary_profile else '-unsigned' if not args.identity else '-unnotarized'
    destination = ROOT.parent / f'semantic-search-engine-v{version}{suffix}.zip'
    subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(app), str(destination)], check=True)
    (destination.with_suffix('.sha256')).write_text(f'{sha256(destination.read_bytes()).hexdigest()}  {destination.name}\n')
    print(f'Local test installer: {destination.name} ({destination.stat().st_size / 1e6:.1f} MB)')
    print('Notarized installer' if args.notary_profile else 'Local test build: public distribution still needs signing/notarization.')


if __name__ == '__main__':
    main()
