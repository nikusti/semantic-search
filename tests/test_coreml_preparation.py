"""Real process/lock checks use a synthetic helper, never Core ML or user models."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

import tinysearch.coreml_assets as assets


EXPECTED_FUNCTIONS = ['pack_256', *[f'embed_{n}' for n in (32, 48, 64, 128, 256, 512)]]
SOURCE = str(Path(assets.__file__).resolve().parents[1])


def wait_for(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('Synthetic preparation did not reach the expected state')


def fixture(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    helper = tmp_path / 'helper'
    control = tmp_path / 'control.json'
    control.write_text('{}')
    events = tmp_path / 'events.jsonl'
    release = tmp_path / 'release'
    helper.write_text(f'''#!{sys.executable}
import json,os,pathlib,sys,time
root=pathlib.Path({str(tmp_path)!r})
mode='compile' if sys.argv[1]=='--compile' else sys.argv[3]
with (root/'events.jsonl').open('a') as output:
    output.write(json.dumps({{'mode':mode,'pid':os.getpid()}})+'\\n')
while not (root/'release').exists():time.sleep(.01)
control=json.loads((root/'control.json').read_text())
if mode=='compile':
    (pathlib.Path(sys.argv[2])/(sys.argv[3]+'.mlmodelc')).mkdir()
elif control.get('failure')=='exit':
    sys.exit(3)
elif control.get('failure')=='json':
    print('invalid json')
elif control.get('failure')=='incomplete':
    print(json.dumps({{'event':'ready'}}))
else:
    print(json.dumps({{'event':'prepared','text_functions':{EXPECTED_FUNCTIONS!r}}}))
''')
    helper.chmod(0o755)
    return model, helper, control, events, release


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@contextmanager
def starter(model, helper, operation='prepare', group='text', manifest=None):
    code = '''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tinysearch import coreml_assets as assets
model,helper=Path(sys.argv[2]),Path(sys.argv[3])
if sys.argv[4]=='ensure':
    assets.MANIFEST=json.loads(sys.argv[6])
    assets.helper_path=lambda _:helper
    def local_file(root,repo,revision,entry):
        target=root/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'synthetic asset')
    assets._file=local_file
    assets.ensure_assets(path=model,groups=[sys.argv[5]])
else:
    assets.prepare_assets(model,helper,[sys.argv[5]])
'''
    process = subprocess.Popen([sys.executable, '-B', '-c', code, SOURCE, str(model), str(helper),
                                operation, group, json.dumps(manifest)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    try:
        yield process
    finally:
        # Each fixture owns a separate group, including an orphaned helper if
        # the owner-death test killed its Python parent deliberately.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def completed(process):
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)


def test_concurrent_preparers_wait_and_recheck_marker(tmp_path):
    model, helper, _, events, release = fixture(tmp_path)
    # An old empty lock file needs no stale-age cleanup.
    (model / '.prepare.lock').touch()
    inode = (model / '.prepare.lock').stat().st_ino
    with starter(model, helper) as first:
        wait_for(lambda: read_events(events))
        with starter(model, helper) as second:
            wait_for(lambda: assets._preparation_busy(model))
            time.sleep(.1)
            assert len(read_events(events)) == 1
            assert second.poll() is None
            release.touch()
            completed(first)
            completed(second)
    assert len(read_events(events)) == 1
    assert (model / '.prepared-text').read_text().strip() == assets.preparation_key(helper, 'text')
    assert (model / '.prepare.lock').stat().st_ino == inode
    assert not assets._preparation_busy(model)
    assert not list(model.glob('.prepared-*.tmp'))


def test_surviving_native_helper_retains_lock_after_python_owner_dies(tmp_path):
    model, helper, _, events, release = fixture(tmp_path)
    with starter(model, helper) as owner:
        native = wait_for(lambda: read_events(events))[0]['pid']
        owner.kill()
        owner.wait(timeout=5)
        os.kill(native, 0)
        assert assets._preparation_busy(model)
        with starter(model, helper) as follower:
            time.sleep(.15)
            assert follower.poll() is None
            assert len(read_events(events)) == 1
            release.touch()
            completed(follower)
    # The dead owner cannot publish a stamp. Its follower retries only after
    # the surviving helper closes the inherited lock descriptor.
    assert len(read_events(events)) == 2
    assert (model / '.prepared-text').read_text().strip() == assets.preparation_key(helper, 'text')
    assert not assets._preparation_busy(model)


def test_different_encoder_groups_share_one_model_lock(tmp_path):
    model, helper, _, events, release = fixture(tmp_path)
    with starter(model, helper, group='vision') as first:
        wait_for(lambda: read_events(events))
        with starter(model, helper, group='text') as second:
            time.sleep(.15)
            assert len(read_events(events)) == 1
            assert second.poll() is None
            release.touch()
            completed(first)
            completed(second)
    assert {event['mode'] for event in read_events(events)} == {'prepare-index', 'prepare-search'}


def test_compile_and_prepare_are_serialized_for_same_model(tmp_path):
    model, helper, _, events, release = fixture(tmp_path)
    manifest = {'repo': 'synthetic', 'groups': {'text': {'revision': 'new', 'files': {
        'EmbeddingGemma2Text.mlpackage/weights.bin': {'bytes': 15, 'sha256': sha256(b'synthetic asset').hexdigest()}}}},
        'processor': {'repo': 'synthetic', 'revision': 'new', 'files': {}}}
    with starter(model, helper, operation='ensure', manifest=manifest) as first:
        wait_for(lambda: read_events(events))
        with starter(model, helper, operation='ensure', manifest=manifest) as second:
            time.sleep(.15)
            assert len(read_events(events)) == 1
            release.touch()
            completed(first)
            completed(second)
    assert [event['mode'] for event in read_events(events)] == ['compile', 'prepare-search']
    assert (model / '.verified-text').read_text().strip() == 'new'
    assert (model / '.prepared-text').is_file()


@pytest.mark.parametrize('failure', ['exit', 'json', 'incomplete'])
def test_failed_preparation_never_publishes_marker_and_can_retry(tmp_path, failure):
    model, helper, control, events, release = fixture(tmp_path)
    release.touch()
    control.write_text(json.dumps({'failure': failure}))
    with pytest.raises((subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError)):
        assets.prepare_assets(model, helper, ['text'])
    assert not (model / '.prepared-text').exists()
    assert not list(model.glob('.prepared-*.tmp'))
    assert not assets._preparation_busy(model)
    control.write_text('{}')
    assets.prepare_assets(model, helper, ['text'])
    assert len(read_events(events)) == 2
    assert (model / '.prepared-text').read_text().strip() == assets.preparation_key(helper, 'text')


def test_status_reads_current_os_key_and_busy_state_without_loading_or_writing(tmp_path, monkeypatch):
    model, helper, _, events, release = fixture(tmp_path)
    monkeypatch.setattr(assets, '_installed_text_models', lambda _: iter([('synthetic', model)]))
    monkeypatch.setattr(assets, 'helper_path', lambda _: helper)
    monkeypatch.setattr(assets.platform, 'version', lambda: 'first OS build')
    before = set(model.iterdir())
    status = assets.preparation_status(tmp_path)
    assert status == {'needed': True, 'waiting': False, 'models': [
        {'id': 'synthetic', 'group': 'text', 'needed': True, 'waiting': False}]}
    assert set(model.iterdir()) == before
    assert not events.exists()
    release.touch()
    assets.prepare_assets(model, helper, ['text'])
    assert assets.preparation_status(tmp_path)['needed'] is False
    with assets._model_lock(model):
        status = assets.preparation_status(tmp_path)
        assert status['waiting'] is True and status['needed'] is False
    assert assets.preparation_status(tmp_path)['waiting'] is False
    monkeypatch.setattr(assets.platform, 'version', lambda: 'updated OS build')
    assert assets.preparation_status(tmp_path)['needed'] is True
    assert len(read_events(events)) == 1


def test_status_cli_on_empty_storage_is_read_only(tmp_path):
    storage = tmp_path / 'missing-storage'
    result = subprocess.run([sys.executable, '-B', '-m', 'tinysearch.coreml_assets',
                             '--storage', str(storage), '--status'],
                            cwd=SOURCE, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {'needed': False, 'waiting': False, 'models': []}
    assert not storage.exists()
