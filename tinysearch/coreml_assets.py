"""Pinned, checksum-verified Core ML assets; optional encoders download on demand."""
from hashlib import sha256
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil
import stat
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

def _lock_path(path):
    return path / '.prepare.lock'

@contextmanager
def _model_lock(path):
    """Keep one inode: an inherited helper descriptor holds this lock after owner death."""
    descriptor = os.open(_lock_path(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
            raise RuntimeError('Core ML preparation lock must be a regular file owned by this user')
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        # Never unlink or explicitly unlock: another process may be waiting on
        # this inode, and a surviving native child may still own the descriptor.
        os.close(descriptor)

def _preparation_busy(path):
    """Probe existing locks without creating files or changing model state."""
    try:
        descriptor = os.open(_lock_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError('Core ML preparation lock must be a regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(descriptor)

def _prepared(path, helper, group):
    try:
        return (path / ('.prepared-' + group)).read_text().strip() == preparation_key(helper, group)
    except (FileNotFoundError, UnicodeError):
        return False

def _write_marker(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(value + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

def _prepare_group(path, helper, group, descriptor):
    # Check again after waiting. Another process may already have completed the
    # exact helper/OS/model specialization required by this caller.
    if _prepared(path, helper, group):
        return
    key = preparation_key(helper, group)
    mode = {'text':'search', 'vision':'index', 'audio':'audio'}[group]
    result = subprocess.run([str(helper), str(path), str(path), 'prepare-' + mode],
                            text=True, capture_output=True, check=True, timeout=600,
                            pass_fds=(descriptor,))
    reply = json.loads(result.stdout)
    expected = {'pack_256', *(f'embed_{n}' for n in (32, 48, 64, 128, 256, 512))}
    if reply.get('event') != 'prepared' or set(reply.get('text_functions', [])) != expected:
        raise RuntimeError('Core ML startup preparation did not complete')
    _write_marker(path / ('.prepared-' + group), key)

def prepare_assets(path, helper, groups):
    path = Path(path)
    with _model_lock(path) as descriptor:
        for group in groups:
            _prepare_group(path, helper, group, descriptor)

def _installed_text_models(storage):
    from .model_source import resolve_source
    catalog = json.loads(Path(__file__).with_name('model_catalog.json').read_text())
    for entry in catalog:
        if entry['backend'] == 'coreml':
            path = Path(storage) / resolve_source(storage, catalog, entry['id'])['path']
            if ready(path):
                yield entry['id'], path

def preparation_status(storage):
    """Read offline specialization state without loading models or touching caches."""
    models = []
    for identity, path in _installed_text_models(storage):
        models.append({'id': identity, 'group': 'text',
                       'needed': not _prepared(path, helper_path(path), 'text'),
                       'waiting': _preparation_busy(path)})
    return {'needed': any(model['needed'] for model in models),
            'waiting': any(model['waiting'] for model in models), 'models': models}

def prepare_installed_text(storage):
    """Prepare downloaded Core ML text assets during a runtime upgrade, offline."""
    for _, path in _installed_text_models(storage):
        prepare_assets(path, helper_path(path), ['text'])

def ensure_assets(config=None, *, path=None, groups=None):
    if config is not None:
        path = config.model_path
        groups = ['text'] + (['vision'] if config.enable_images or config.enable_video else []) + (['audio'] if config.enable_audio else [])
    path = Path(path)
    helper = helper_path(path)
    path.mkdir(parents=True, exist_ok=True)
    # Every encoder also specializes text functions. One model lock covers all
    # groups, shared processor downloads, compilation and preparation together.
    with _model_lock(path) as descriptor:
        processor = MANIFEST['processor']
        for name, entry in processor['files'].items():
            _file(path / 'processor', processor['repo'], processor['revision'], {'path':name, **entry})
        for group in groups or ['text']:
            if not ready(path, group):
                section = MANIFEST['groups'][group]
                with tempfile.TemporaryDirectory(prefix='.coreml-prepare-', dir=path.parent) as temporary:
                    staging = Path(temporary)
                    for name, entry in section['files'].items():
                        existing = path/name
                        if existing.is_file() and sha256(existing.read_bytes()).hexdigest() == entry['sha256']:
                            (staging/name).parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(existing, staging/name)
                        _file(staging, MANIFEST['repo'], section['revision'], {'path':name, **entry})
                    # The same model lock covers compilation, replacement and
                    # specialization, so no starter prepares a partial upgrade.
                    subprocess.run([str(helper), '--compile', str(staging), NAMES[group]],
                                   check=True, capture_output=True, timeout=600, pass_fds=(descriptor,))
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
                _write_marker(path / ('.verified-' + group), section['revision'])
            _prepare_group(path, helper, group, descriptor)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Prepare installed Core ML text functions offline')
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--status', action='store_true', help='Read preparation state without loading models')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(preparation_status(args.storage)))
    else:
        prepare_installed_text(args.storage)

