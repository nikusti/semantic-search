"""Generate the small Store source package from the shared extension codebase."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / 'extension'
    manifest = json.loads((source / 'assets/runtime-manifest.json').read_text())
    package = json.loads((source / 'package.json').read_text())
    version = package['version']
    assert manifest['version'] == version, 'Build the matching runtime first'
    output = ROOT / '.build/store/semantic-search'
    shutil.rmtree(output, ignore_errors=True)
    (output / 'assets').mkdir(parents=True)
    shutil.copytree(source / 'src', output / 'src')
    shutil.copyfile(source / 'tsconfig.json', output / 'tsconfig.json')
    shutil.copyfile(source / 'eslint.config.js', output / 'eslint.config.js')
    shutil.copyfile(source / 'assets/icon.png', output / 'assets/icon.png')
    shutil.copyfile(source / 'assets/runtime-manifest.json', output / 'assets/runtime-manifest.json')
    (output / 'assets/distribution.json').write_text(json.dumps(dict(mode='companion',
        installer_url=f'https://github.com/nikusti/semantic-search/releases/tag/v{version}'), indent=2) + '\n')
    package['commands'] = [entry for entry in package['commands'] if entry['name'] == 'semantic-search']
    package['description'] = 'Find local documents, photos, video scenes and sounds by meaning. Requires the Semantic Search Engine.'
    package['preferences'][0]['description'] = 'Leave blank to use the installed Semantic Search Engine. Advanced: connect to your own local service.'
    package['scripts'] = dict(build='ray build', dev='ray develop', lint='ray lint', publish='ray publish')
    (output / 'package.json').write_text(json.dumps(package, indent=2) + '\n')
    lock = json.loads((source / 'package-lock.json').read_text())
    (output / 'package-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    shutil.copyfile(ROOT / 'LICENSE', output / 'LICENSE')
    (output / 'README.md').write_text(f'''# Semantic Search

Search local files by meaning using EmbeddingGemma 2. Documents, photos, video scenes and sounds are indexed on your Mac. Your files and queries are not uploaded; the project includes no telemetry.

## Requirements and setup

Apple silicon, macOS 26+, and the separately installed **Semantic Search Engine {version}** are required. Allow about 2.5 GB for engine installation, plus model weights and your library index.

1. Open Semantic Search and choose **Download Search Engine** if the engine is missing.
2. Open the companion installer from the release page and click **Install**. Python and decoding libraries are included. No Homebrew or terminal setup.
3. Return to Search and choose **Check Installation**.
4. Choose **Manage Library** in Actions, download a model, select folders and apply. Model weights download from Hugging Face.

The {version} companion is unsigned; macOS may require explicit opening approval. See the [installation guide](https://github.com/nikusti/semantic-search/blob/main/docs/INSTALL.md).

MLX 8-bit and Core ML are supported; each model keeps a separate index. Text queries load only the text encoder even when searching indexed media. The default index uses 512-dimensional float32 vectors. By default, model workers unload one minute after leaving search when no indexing or request is active.

**Update Search Index** adds new/changed files and removes missing files. Exclusions can be saved during indexing. Deleting a model preserves indexes and originals. Engine data is stored in `~/Library/Application Support/Semantic Search/`.

The engine listens only on loopback, rejects browser origins, and is accessible to other local processes. Audio search matches sounds, not transcripts.

Source, release notices and matching native dependency sources: [Semantic Search](https://github.com/nikusti/semantic-search).
''')
    (output / 'CHANGELOG.md').write_text('# Semantic Search Changelog\n\n## [Initial Store Release] - {PR_MERGE_DATE}\n\n- Search local files with EmbeddingGemma 2 through a separately installed local engine.\n- Manage models, folders, exclusions and incremental indexing from Search.\n')
    screenshots = ROOT / 'extension/metadata'
    if screenshots.is_dir(): shutil.copytree(screenshots, output / 'metadata')
    forbidden = {'runtime.zip', 'node_modules', '.build'}
    for path in output.rglob('*'):
        assert not forbidden.intersection(path.relative_to(output).parts), f'Unexpected bundled engine asset: {path.name}'
    archive = ROOT.parent / f'semantic-search-store-v{version}-review.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target:
        for path in sorted(output.rglob('*')):
            if path.is_file(): target.write(path, 'semantic-search/' + path.relative_to(output).as_posix())
    (archive.with_suffix('.sha256')).write_text(f'{sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
    print(f'Store source package: {archive.name} ({archive.stat().st_size / 1e6:.2f} MB). No executable runtime or model files.')


if __name__ == '__main__':
    main()
