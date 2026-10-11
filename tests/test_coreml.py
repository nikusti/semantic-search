from dataclasses import replace
import pytest

from tinysearch.config import Config
from tinysearch.settings import configured
from tinysearch.engine import engine_identity
from tinysearch.model_source import ensure_downloaded, installed


def test_coreml_uses_own_index_and_bounded_inputs(tmp_path):
    config = Config((tmp_path,), tmp_path/'model', 'old', tmp_path/'old.sqlite3',
                    backend='mlx', dimensions=512, chunk_tokens=512,
                    max_sequence_tokens=544, enable_audio=True, enable_video=True,
                    storage_path=tmp_path)
    values = dict(model_variant='coreml-experimental', dimensions=512,
                  chunk_tokens=512, enable_documents=True, enable_images=True,
                  enable_audio=True, enable_video=True, thumbnail_px=256, idle_seconds=60)
    selected = configured(config, values)
    assert selected.backend == 'coreml'
    assert selected.chunk_tokens == 480 and selected.max_sequence_tokens == 512
    assert selected.enable_audio and selected.enable_video
    assert selected.audio_segment_seconds == 10
    assert selected.database != config.database
    assert engine_identity(selected) != engine_identity(config)
    assert replace(selected, dimensions=768).dimensions == 768


def test_coreml_local_assets_are_required(tmp_path, monkeypatch):
    from tinysearch.settings import CATALOG
    entry = next(m for m in CATALOG if m['backend'] == 'coreml')
    import tinysearch.coreml_assets as assets
    monkeypatch.setattr(assets, 'helper_path', lambda path: (_ for _ in ()).throw(RuntimeError('helper is unavailable')))
    assert not installed(tmp_path, entry)
    with pytest.raises(RuntimeError, match='helper is unavailable'):
        ensure_downloaded(tmp_path, entry)


def test_coreml_native_child_exits_with_worker(tmp_path, monkeypatch):
    import os
    from tinysearch.coreml_engine import CoreMLEngine
    folder = tmp_path/'model'
    folder.mkdir()
    helper = folder/'coreml-worker'
    helper.write_text('''#!/usr/bin/env python3
import json,sys
print(json.dumps({'event':'ready'}),flush=True)
for line in sys.stdin:
    print(json.dumps({'vectors':[[1.0]*512]}),flush=True)
''')
    helper.chmod(0o755)
    monkeypatch.setattr('tinysearch.coreml_assets.helper_path', lambda _: helper)
    config = Config((tmp_path,), folder, 'test', tmp_path/'index.sqlite3',
                    backend='coreml', dimensions=512, enable_images=False)
    engine = CoreMLEngine(config)
    pid = engine.child.pid
    assert len(engine.query('test')) == 512
    engine.close()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

def test_new_revision_compiles_fresh_target_and_prepares_again(tmp_path,monkeypatch):
    import tinysearch.coreml_assets as assets
    from hashlib import sha256
    data=b'new source'
    manifest={'repo':'test/model','groups':{'text':{'revision':'new','files':{'EmbeddingGemma2Text.mlpackage/weights.bin':{'bytes':len(data),'sha256':sha256(data).hexdigest()}}}},'processor':{'repo':'test/processor','revision':'p','files':{}}}
    monkeypatch.setattr(assets,'MANIFEST',manifest)
    monkeypatch.setattr(assets,'helper_path',lambda _:tmp_path/'helper')
    (tmp_path/'helper').write_bytes(b'native bridge')
    model=tmp_path/'model';model.mkdir()
    old=model/'EmbeddingGemma2Text.mlmodelc';old.mkdir();(old/'revision').write_text('old')
    (model/'.verified-text').write_text('old');(model/'.prepared-text').write_text('old')
    def download(root,repo,revision,entry):
        target=root/entry['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    monkeypatch.setattr(assets,'_file',download)
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if '--compile' in args:
            target=__import__('pathlib').Path(args[2])/'EmbeddingGemma2Text.mlmodelc'
            assert not target.exists()
            target.mkdir();(target/'revision').write_text('new')
        return __import__('subprocess').CompletedProcess(args,0,stdout=__import__('json').dumps({
            'event':'prepared','text_functions':['pack_256', *[f'embed_{n}' for n in (32,48,64,128,256,512)]]}))
    monkeypatch.setattr(assets.subprocess,'run',run)
    assets.ensure_assets(path=model,groups=['text'])
    assert (old/'revision').read_text()=='new'
    assert len(calls)==2 and calls[1][-1]=='prepare-search'
    assert (model/'.prepared-text').read_text().strip()==assets.preparation_key(tmp_path/'helper','text')
    assets.ensure_assets(path=model,groups=['text'])
    assert len(calls)==2


def test_preparation_survives_restart_but_repeats_after_bridge_or_os_update(tmp_path, monkeypatch):
    import json
    import subprocess
    import tinysearch.coreml_assets as assets
    helper=tmp_path/'helper';helper.write_bytes(b'first bridge')
    model=tmp_path/'model';model.mkdir()
    (model/'.prepared-text').write_text(assets.MANIFEST['groups']['text']['revision'])
    calls=[]
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args,0,stdout=json.dumps({
            'event':'prepared','text_functions':['pack_256', *[f'embed_{n}' for n in (32,48,64,128,256,512)]]}))
    monkeypatch.setattr(assets.subprocess,'run',run)
    monkeypatch.setattr(assets.platform,'version',lambda:'first OS build')
    assets.prepare_assets(model,helper,['text'])
    assets.prepare_assets(model,helper,['text'])
    assert len(calls)==1  # Legacy marker upgraded once; new process/reboot has the same key.
    helper.write_bytes(b'updated bridge')
    assets.prepare_assets(model,helper,['text'])
    assert len(calls)==2
    monkeypatch.setattr(assets.platform,'version',lambda:'updated OS build')
    assets.prepare_assets(model,helper,['text'])
    assert len(calls)==3


def test_failed_or_incomplete_preparation_is_not_cached(tmp_path, monkeypatch):
    import subprocess
    import tinysearch.coreml_assets as assets
    helper=tmp_path/'helper';helper.write_bytes(b'bridge')
    def run(args,**kwargs):
        return subprocess.CompletedProcess(args,0,stdout='{"event":"ready"}')
    monkeypatch.setattr(assets.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='did not complete'):
        assets.prepare_assets(tmp_path,helper,['text'])
    assert not (tmp_path/'.prepared-text').exists()
