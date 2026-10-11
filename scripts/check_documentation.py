"""Catch stale release versions, missing local documentation and model links."""
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    manifest = json.loads((ROOT / 'extension/package.json').read_text())
    lock = json.loads((ROOT / 'extension/package-lock.json').read_text())
    assert manifest['version'] == lock['version'] == lock['packages']['']['version'] == version, 'Release versions disagree'
    assert f'"{version}"' in (ROOT / 'tinysearch/__init__.py').read_text(), 'Engine version differs'
    readme = (ROOT / 'README.md').read_text()
    changelog = (ROOT / 'CHANGELOG.md').read_text()
    assert f'## [{version}]' in changelog, 'Changelog is missing this version'
    # An unreleased branch still points users to the last working release.
    shipped = re.findall(r'## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}', changelog)[0]
    allowed = {version, shipped} if f'## [{version}] - Unreleased' in changelog else {version}
    for path in [ROOT / 'README.md', *sorted((ROOT / 'docs').glob('*.md'))]:
        content = path.read_text()
        for match in re.findall(r'semantic-search-(?:native-sources-)?v(\d+\.\d+\.\d+)\.(?:zip|rayext)', content):
            assert match in allowed, f'{path.name}: stale download/build version {match}'
        for match in re.findall(r'/releases/tag/v(\d+\.\d+\.\d+)', content):
            assert match in allowed, f'{path.name}: stale release link {match}'
        for link in re.findall(r'\]\(([^)]+)\)', content):
            if '://' in link or link.startswith('#'):
                continue
            target = link.split('#')[0]
            assert (path.parent / target).exists(), f'{path.name}: missing link {link}'
    for model in json.loads((ROOT / 'tinysearch/model_catalog.json').read_text()):
        assert 'https://huggingface.co/' + model['repo'] in readme, f'Model {model["id"]} is undocumented'
    print(f'Documentation checks passed for {version}. Review prose and release assets separately.')


if __name__ == '__main__':
    main()
