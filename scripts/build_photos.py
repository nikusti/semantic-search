"""Build the small PhotoKit permission app; no model or user data is embedded."""
import plistlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def build(destination):
    app = Path(destination) / 'Semantic Search Photos.app'
    contents = app / 'Contents'
    executable = contents / 'MacOS' / 'semantic-search-photos'
    executable.parent.mkdir(parents=True, exist_ok=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleExecutable': executable.name,
        'CFBundleIdentifier': 'app.semanticsearch.photos',
        'CFBundleName': 'Semantic Search Photos',
        'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
        'LSMinimumSystemVersion': '26.0', 'LSUIElement': True,
        'NSPhotoLibraryUsageDescription': 'Search your photos and videos locally by their content. Your library is never modified. Cloud media is downloaded only when enabled in Semantic Search.',
    }))
    subprocess.run(['swiftc', '-O', '-module-cache-path', str(ROOT/'.build/photos-module-cache'),
                    '-file-prefix-map', str(ROOT)+'=/source', '-o', str(executable),
                    str(ROOT/'native/photos/PhotosBridge.swift'), str(ROOT/'native/photos/VideoCopy.swift'),
                    str(ROOT/'native/photos/PhotoClipboard.swift')], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    return app

if __name__ == '__main__':
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT/'.build/photos'))
