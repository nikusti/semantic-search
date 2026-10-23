"""The public Store package must not inherit development-only infrastructure."""
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile


def test_store_export_keeps_shared_tests_and_host_support_outside_package(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('build_store', root / 'scripts/build_store.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    project = tmp_path / 'project'
    source = project / 'extension'
    source.mkdir(parents=True)
    for name in ('package.json', 'package-lock.json', 'tsconfig.json', 'eslint.config.js', 'CHANGELOG.md'):
        shutil.copyfile(root / 'extension' / name, source / name)
    shutil.copytree(root / 'extension/src', source / 'src')
    (source / 'assets').mkdir()
    (source / 'assets/store-icon.png').write_bytes(b'store icon fixture')
    version = json.loads((source / 'package.json').read_text())['version']
    (source / 'assets/runtime-manifest.json').write_text(json.dumps({'version': version}))
    (project / 'LICENSE').write_text('MIT')
    (source / 'tests').mkdir()
    (source / 'tests/keep.test.ts').write_text('development regression')
    (source / 'scripts').mkdir()
    (source / 'scripts/test.mjs').write_text('development runner')
    original = (source / 'src/launcher-probe.ts').read_text()
    original_changelog = (source / 'CHANGELOG.md').read_text()
    monkeypatch.setattr(builder, 'ROOT', project)
    installs = []

    def reconcile_lock(command, cwd, **kwargs):
        installs.append(command)
        assert command[:2] == ['npm', 'install']
        assert '--ignore-scripts' in command and '--package-lock-only' in command
        lock = json.loads((cwd / 'package-lock.json').read_text())
        package = json.loads((cwd / 'package.json').read_text())
        lock['name'] = package['name']
        lock['packages'][''] = package
        (cwd / 'package-lock.json').write_text(json.dumps(lock))

    monkeypatch.setattr(builder.subprocess, 'run', reconcile_lock)
    builder.main()
    output = project / '.build/store/semantic-file-search'
    package = json.loads((output / 'package.json').read_text())
    assert package['name'] == 'semantic-file-search'
    assert package['title'] == 'Semantic File Search'
    assert package['commands'][0]['name'] == 'semantic-search'
    assert package['commands'][0]['title'] == 'Search Files'
    assert set(package['scripts']) == {'build', 'dev', 'lint', 'publish'}
    assert not {'esbuild', 'react-test-renderer', '@types/react-test-renderer'} & package['devDependencies'].keys()
    assert 'tests' not in json.loads((output / 'tsconfig.json').read_text())['include']
    assert not (output / 'tests').exists() and not (output / 'scripts').exists()
    assert (source / 'tests/keep.test.ts').exists()
    assert (source / 'src/launcher-probe.ts').read_text() == original
    assert (source / 'CHANGELOG.md').read_text() == original_changelog
    store_changelog = (output / 'CHANGELOG.md').read_text()
    assert store_changelog.startswith('# Semantic File Search Changelog\n')
    assert store_changelog.count('\n## ') == 1
    assert '## [Initial Version] - {PR_MERGE_DATE}' in store_changelog
    assert 'Run this command from Raycast' in (output / 'src/launcher-probe.ts').read_text()
    assert 'while Raycast is running' in (output / 'src/automatic-settings.tsx').read_text()
    assert 'Open Manage Library, add folders' in (output / 'src/semantic-search.tsx').read_text()
    assert installs
    with zipfile.ZipFile(tmp_path / f'semantic-file-search-store-v{version}-review.zip') as archive:
        assert all(name.startswith('semantic-file-search/') for name in archive.namelist())
        assert not any({'tests', 'scripts', 'node_modules'} & set(Path(name).parts) for name in archive.namelist())
