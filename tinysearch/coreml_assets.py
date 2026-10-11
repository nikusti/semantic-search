"""Pinned, checksum-verified Core ML assets; optional encoders download on demand."""
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

MANIFEST = json.loads(Path(__file__).with_suffix('.json').read_text())
PIPELINE_REVISION = sha256(json.dumps(MANIFEST, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
NAMES = {'text': 'EmbeddingGemma2Text', 'vision': 'EmbeddingGemma2Vision', 'audio': 'EmbeddingGemma2Audio'}

def helper_path(model_path=None):
    bundled = Path(sys.executable).resolve().parents[2] / 'coreml/semantic-search-coreml'
    if bundled.is_file(): return bundled
    if model_path and (model_path / 'coreml-worker').is_file(): return model_path / 'coreml-worker'
    raise RuntimeError('Core ML helper is unavailable. Reinstall the packaged extension.')

def ready(path, group='text'):
    marker = path / ('.verified-' + group)
    return (marker.is_file() and marker.read_text().strip() == MANIFEST['groups'][group]['revision']
            and (path / (NAMES[group] + '.mlmodelc')).is_dir())

def _file(root, repo, revision, entry):
    target = root / entry['path']
    if target.is_file() and target.stat().st_size == entry['bytes'] and sha256(target.read_bytes()).hexdigest() == entry['sha256']:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.download')
    try:
        digest = sha256()
        url = f'https://huggingface.co/{repo}/resolve/{revision}/{entry["path"]}'
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
            while block := response.read(1024 * 1024):
                digest.update(block); output.write(block)
        if temporary.stat().st_size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            raise RuntimeError('Core ML asset checksum mismatch')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)

def preparation_key(helper, group):
    # Specialization depends on the native bridge and OS as well as the weights.
    # A normal reboot leaves this identity unchanged. Never use it as an index identity.
    identity = {
        'format': 2,
        'text_revision': MANIFEST['groups']['text']['revision'],
        'group_revision': MANIFEST['groups'][group]['revision'],
        'helper_sha256': sha256(helper.read_bytes()).hexdigest(),
        'os_build': platform.version(),
        'architecture': platform.machine(),
    }
    return sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

def prepare_assets(path, helper, groups):
    for group in groups:
        prepared = path / ('.prepared-' + group)
        key = preparation_key(helper, group)
        if prepared.is_file() and prepared.read_text().strip() == key:
            continue
        mode = {'text':'search', 'vision':'index', 'audio':'audio'}[group]
        # Loading a lazy worker alone does not specialize its functions. Setup
        # explicitly predicts through all six text shapes and the packed batch.
        result = subprocess.run([str(helper), str(path), str(path), 'prepare-' + mode],
                                text=True, capture_output=True, check=True, timeout=600)
        reply = json.loads(result.stdout)
        expected = {'pack_256', *(f'embed_{n}' for n in (32, 48, 64, 128, 256, 512))}
        if reply.get('event') != 'prepared' or set(reply.get('text_functions', [])) != expected:
            raise RuntimeError('Core ML startup preparation did not complete')
        temporary = prepared.with_suffix('.tmp')
        temporary.write_text(key + '\n')
        temporary.replace(prepared)

def prepare_installed_text(storage):
    """Prepare downloaded Core ML text assets during a runtime upgrade, offline."""
    from .model_source import resolve_source
    catalog = json.loads(Path(__file__).with_name('model_catalog.json').read_text())
    for entry in catalog:
        if entry['backend'] != 'coreml':
            continue
        path = storage / resolve_source(storage, catalog, entry['id'])['path']
        if ready(path):
            prepare_assets(path, helper_path(path), ['text'])

def ensure_assets(config=None, *, path=None, groups=None):
    if config is not None:
        path = config.model_path
        groups = ['text'] + (['vision'] if config.enable_images or config.enable_video else []) + (['audio'] if config.enable_audio else [])
    path = Path(path)
    helper = helper_path(path)
    path.mkdir(parents=True, exist_ok=True)
    for group in groups or ['text']:
        if ready(path, group): continue
        section = MANIFEST['groups'][group]
        with tempfile.TemporaryDirectory(prefix='.coreml-prepare-', dir=path.parent) as temporary:
            staging = Path(temporary)
            for name, entry in section['files'].items():
                existing = path/name
                if existing.is_file() and sha256(existing.read_bytes()).hexdigest() == entry['sha256']:
                    (staging/name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(existing, staging/name)
                _file(staging, MANIFEST['repo'], section['revision'], {'path':name, **entry})
            # A fresh destination must be compiled, even when an old revision exists.
            subprocess.run([str(helper), '--compile', str(staging), NAMES[group]], check=True, capture_output=True, timeout=600)
            target = path/(NAMES[group] + '.mlmodelc')
            previous = staging/'previous.mlmodelc'
            if target.exists(): target.rename(previous)
            try:
                (staging/target.name).rename(target)
            except Exception:
                if previous.exists(): previous.rename(target)
                raise
            for name in section['files']:
                if '.mlpackage/' not in name:
                    (path/name).parent.mkdir(parents=True,exist_ok=True)
                    (staging/name).replace(path/name)
        (path / ('.verified-' + group)).write_text(section['revision'] + '\n')
    processor = MANIFEST['processor']
    for name, entry in processor['files'].items():
        _file(path / 'processor', processor['repo'], processor['revision'], {'path':name, **entry})
    prepare_assets(path, helper, groups or ['text'])

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Prepare installed Core ML text functions offline')
    parser.add_argument('--storage', type=Path, required=True)
    prepare_installed_text(parser.parse_args().storage)

