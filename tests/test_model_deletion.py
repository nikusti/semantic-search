from pathlib import Path
import time
import json
import os
import numpy as np
from fastapi.testclient import TestClient
from test_worker import fake_worker
from tinysearch.config import Config
from tinysearch.worker import ManagedEngine
from tinysearch.server import create_app


def test_delete_loaded_main_and_audio_workers_keep_index_redownload_and_search(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    import tinysearch.worker as worker
    import huggingface_hub
    root=tmp_path/'files';root.mkdir();original=root/'a.txt';original.write_text('sample text')
    model=tmp_path/'shared/models/recommended';model.mkdir(parents=True)
    payloads={'config.json':'{}','tokenizer.json':'{}','model.safetensors':'weights'}
    for name,value in payloads.items():(model/name).write_text(value)
    cfg=Config((root,),model,'pinned',tmp_path/'shared/data/active.sqlite3',dimensions=128,
               backend='mlx',model_variant='mlx-8bit',enable_audio=True,storage_path=tmp_path/'shared',source_path=tmp_path/'config.toml')
    cfg.save()
    monkeypatch.setattr(settings,'CATALOG',[{'id':'mlx-8bit','repo':'test/pinned','revision':'pinned','path':'models/recommended','backend':'mlx','label':'Recommended','download_bytes':10}])
    monkeypatch.setattr(settings,'ManagedEngine',lambda config:ManagedEngine(config,target=fake_worker))
    monkeypatch.setattr(worker,'audio_worker_main',fake_worker)
    downloads=[]
    def download(**kwargs):
        downloads.append(kwargs);kwargs['local_dir'].mkdir(parents=True)
        for name,value in payloads.items():(kwargs['local_dir']/name).write_text(value)
    monkeypatch.setattr(huggingface_hub,'snapshot_download',download)
    engine=ManagedEngine(cfg,target=fake_worker)
    from test_store import FakeEngine
    from tinysearch.store import Store
    seed_engine=FakeEngine();seed_engine.identity=engine.identity
    seed=Store(cfg,seed_engine);assert seed.index()['indexed']==1;seed.close()
    with TestClient(create_app(cfg,engine)) as client:
        assert client.post('/v1/index').status_code==200
        engine.query('test');engine.audio(np.zeros(16000))
        pids=[engine.process.pid,engine.audio_manager.process.pid]
        vector=client.app.state.store.db.execute('SELECT vector FROM chunks').fetchone()[0]
        config_before=cfg.source_path.read_text()
        store=client.app.state.store
        store.index_lock.acquire()
        try:assert client.post('/v1/models/delete',json={'model_variant':'mlx-8bit'}).status_code==409
        finally:store.index_lock.release()
        removed=client.post('/v1/models/delete',json={'model_variant':'mlx-8bit'})
        assert removed.status_code==200 and removed.json()['freed_bytes']>0
        assert removed.json()['active_model_removed'] and not model.exists()
        for pid in pids:
            try:os.kill(pid,0)
            except ProcessLookupError:pass
            else:raise AssertionError('Worker remained alive')
        assert client.get('/v1/status').json()['runtime']['state']=='unloaded'
        assert cfg.source_path.read_text()==config_before and original.exists()
        assert store.db.execute('SELECT vector FROM chunks').fetchone()[0]==vector
        assert not client.get('/v1/settings').json()['models'][0]['installed']
        for route,body in [('search',{'query':'text'}),('session',{}),('index/start',{})]:
            assert client.post('/v1/'+route,json=body).status_code==409
        assert client.post('/v1/models/download',json={'model_variant':'mlx-8bit'}).status_code==200
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            job=client.get('/v1/settings').json()['job']
            if job['state'] in {'downloaded','error'}:break
            time.sleep(.01)
        assert job['state']=='downloaded',job
        assert downloads[0]['local_dir']==model and model.exists()
        result=client.post('/v1/search',json={'query':'text'})
        assert result.status_code==200 and result.json()['results'][0]['path']==str(original)
        assert store.db.execute('SELECT vector FROM chunks').fetchone()[0]==vector


def test_delete_refuses_model_folder_symlink(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    from test_store import FakeEngine
    shared=tmp_path/'shared';(shared/'models').mkdir(parents=True)
    external=tmp_path/'keep';external.mkdir();(external/'weights').write_text('keep')
    (shared/'models/recommended').symlink_to(external,target_is_directory=True)
    monkeypatch.setattr(settings,'CATALOG',[{'id':'mlx-8bit','repo':'test','revision':'pinned','path':'models/recommended','backend':'mlx','label':'test','download_bytes':1}])
    cfg=Config((tmp_path,),shared/'models/recommended','pinned',tmp_path/'index.sqlite3',storage_path=shared)
    with TestClient(create_app(cfg,FakeEngine())) as client:
        assert client.post('/v1/models/delete',json={'model_variant':'mlx-8bit'}).status_code==422
    assert (external/'weights').read_text()=='keep'
