"""Generate the small Store source package from the shared extension codebase."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STORE_NAME = 'semantic-file-search'
STORE_TITLE = 'Semantic File Search'


def store_sources(source, output):
    """Keep host compatibility in shared code; localize only Store-facing text."""
    shutil.copytree(source / 'src', output / 'src')
    replacements = {
        'launcher.ts': [('in Tinycast or Raycast', 'in Raycast')],
        'launcher-probe.ts': [('from Tinycast or Raycast', 'from Raycast')],
        'automatic-settings.tsx': [('while Tinycast or Raycast is running', 'while Raycast is running')],
        'semantic-search.tsx': [('Open Model Settings, add folders', 'Open Manage Library, add folders')],
    }
    for name, changes in replacements.items():
        path = output / 'src' / name
        text = path.read_text()
        for before, after in changes:
            assert text.count(before) == 1, f'Store wording changed in {name}: {before}'
            text = text.replace(before, after)
        path.write_text(text)


def main():
    source = ROOT / 'extension'
    manifest = json.loads((source / 'assets/runtime-manifest.json').read_text())
    package = json.loads((source / 'package.json').read_text())
    version = package['version']
    assert manifest['version'] == version, 'Build the matching runtime first'
    output = ROOT / '.build/store' / STORE_NAME
    shutil.rmtree(output, ignore_errors=True)
    (output / 'assets').mkdir(parents=True)
    store_sources(source, output)
    tsconfig = json.loads((source / 'tsconfig.json').read_text())
    tsconfig['include'] = [entry for entry in tsconfig['include'] if entry != 'tests']
    (output / 'tsconfig.json').write_text(json.dumps(tsconfig, indent=2) + '\n')
    shutil.copyfile(source / 'eslint.config.js', output / 'eslint.config.js')
    shutil.copyfile(source / 'assets/store-icon.png', output / 'assets/icon.png')
    shutil.copyfile(source / 'assets/runtime-manifest.json', output / 'assets/runtime-manifest.json')
    (output / 'assets/distribution.json').write_text(json.dumps(dict(mode='companion',
        installer_url=f'https://github.com/nikusti/semantic-search/releases/tag/v{version}'), indent=2) + '\n')
    package['commands'] = [entry for entry in package['commands'] if entry['name'] in {'semantic-search','automatic-index'}]
    package['name'] = STORE_NAME
    package['title'] = STORE_TITLE
    for command in package['commands']:
        if command['name'] == 'semantic-search':
            command['title'] = 'Search Files'
            command['subtitle'] = STORE_TITLE
    package['description'] = 'Find local documents, photos, video scenes and sounds by meaning. Requires the Semantic Search Engine.'
    package['preferences'][0]['description'] = 'Leave blank to use the installed Semantic Search Engine. Advanced: connect to your own local service.'
    package['scripts'] = dict(build='ray build', dev='ray develop', lint='ray lint', publish='ray publish')
    for name in ('esbuild', 'react-test-renderer', '@types/react-test-renderer'):
        package['devDependencies'].pop(name, None)
    (output / 'package.json').write_text(json.dumps(package, indent=2) + '\n')
    shutil.copyfile(source / 'package-lock.json', output / 'package-lock.json')
    # npm reconciles the dependency graph; copying the development lock retains
    # test-only dependencies and an incorrect root package identity.
    subprocess.run(['npm', 'install', '--package-lock-only', '--ignore-scripts',
                    '--no-audit', '--no-fund', *(['--offline'] if os.environ.get('SEMANTIC_STORE_OFFLINE') == '1' else [])],
                   cwd=output, check=True, timeout=180)
    shutil.copyfile(ROOT / 'LICENSE', output / 'LICENSE')
    (output / 'README.md').write_text(f'''# {STORE_TITLE}

Search local files by meaning using EmbeddingGemma 2. Documents, photos, video scenes and sounds are indexed on your Mac. Your files and queries are not uploaded; the project includes no telemetry.

## Requirements and setup

Apple silicon, macOS 26+, and the separately installed **Semantic Search Engine {version}** are required. Allow about 2.5 GB for engine installation, plus model weights and your library index.

1. Open **Search Files** in {STORE_TITLE} and choose **Download Search Engine** if the engine is missing.
2. Open the companion installer from the release page and click **Install**. Python and decoding libraries are included. No Homebrew or terminal setup.
3. Return to Search and choose **Check Installation**.
4. Choose **Manage Library** in Actions, download a model, select folders and apply. Model weights download from Hugging Face.

The {version} companion is unsigned; macOS may require explicit opening approval. See the [installation guide](https://github.com/nikusti/semantic-search/blob/main/docs/INSTALL.md).

MLX 8-bit and Core ML are supported; each model keeps a separate index. Text queries load only the text encoder even when searching indexed media. The default index uses 512-dimensional float32 vectors. By default, model workers unload one minute after leaving search when no indexing or request is active.

**Update Search Index** adds new/changed files and removes missing files. Add Search Folders keeps existing sources. Remove a Folder hides saved entries by default, with optional index cleanup. Folder additions preserve existing vectors; removals and exclusions can be saved during indexing. Deleting a model preserves indexes and originals. Engine data is stored in `~/Library/Application Support/Semantic Search/`.

Apple Photos is optional: enable the source in Manage Library, connect it in Actions and allow macOS access. Enable iCloud downloads to include media missing locally. Both models support photographs, video scenes and optional soundtracks. Originals stay in Photos; saved vectors and small thumbnails are retained locally. Photo results show creation dates, open in Photos and offer full-resolution **Copy Photo**. Apple controls its own Photos download cache.

Automatic updates are optional and off by default. Manage Library offers 30-minute and 1/3/6/24-hour schedules (1 hour by default), an external-power condition and pause/resume. Short Raycast checks start incremental work without waiting for a long scan. Closing the window is fine; quitting Raycast or removing its extension stops the job. Completed files are reused. No LaunchAgent is installed. Run **Activate Background Checks** after enabling updates or reinstalling.

The engine listens only on loopback, rejects browser origins, and requires an installation token for library requests. The token is stored locally with owner-only permissions. Processes able to read the token as the same user can also access the service. Audio search matches sounds, not transcripts.

Source, release notices and matching native dependency sources: [Semantic Search](https://github.com/nikusti/semantic-search).
''')
    # Store starts with one entry; shared extension keeps its release history.
    (output / 'CHANGELOG.md').write_text('''# Semantic File Search Changelog

## [Initial Version] - {PR_MERGE_DATE}

- Search local documents, photographs, video scenes and sounds by meaning with on-device embeddings.
- Manage models, folders, exclusions and incremental indexing from Search Files.
- Include optional Apple Photos indexing, full-resolution photo copying and creation dates.
- Offer optional scheduled library updates with power conditions and pause/resume.
- Connect to a separately installed local engine with authenticated library requests; files and queries stay on the Mac.
''')
    screenshots = ROOT / 'extension/metadata'
    if screenshots.is_dir(): shutil.copytree(screenshots, output / 'metadata')
    forbidden = {'runtime.zip', 'node_modules', '.build', 'tests', 'scripts'}
    for path in output.rglob('*'):
        assert not forbidden.intersection(path.relative_to(output).parts), f'Unexpected bundled engine asset: {path.name}'
    archive = ROOT.parent / f'{STORE_NAME}-store-v{version}-review.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target:
        for path in sorted(output.rglob('*')):
            if path.is_file(): target.write(path, STORE_NAME + '/' + path.relative_to(output).as_posix())
    (archive.with_suffix('.sha256')).write_text(f'{sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
    print(f'Store source package: {archive.name} ({archive.stat().st_size / 1e6:.2f} MB). No executable runtime or model files.')


if __name__ == '__main__':
    main()
