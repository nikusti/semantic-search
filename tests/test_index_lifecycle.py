from dataclasses import replace
import threading
import time
from contextlib import nullcontext
from fastapi.testclient import TestClient
from test_store import FakeEngine
from tinysearch.config import Config
from tinysearch.engine import engine_identity
from tinysearch.store import Store
from tinysearch.index_reuse import historical_profiles, reuse_saved
from tinysearch.server import create_app

class Engine(FakeEngine):
    def __init__(self, config):
        self.identity = engine_identity(config)
        self.dimensions = config.dimensions
    def close(self): pass
    def pin(self): return nullcontext()

def test_reuse_modalities_preserves_vectors_but_rejects_other_models(tmp_path):
    root = tmp_path/'files'; root.mkdir()
    (root/'note.txt').write_text('saved content')
    base = Config((root,),tmp_path/'model','revision',tmp_path/'data/full.sqlite3',
                  backend='mlx',model_variant='mlx-8bit',dimensions=128,
                  enable_audio=True,enable_video=True)
    original = Store(base, Engine(base)); original.index()
    vector = original.db.execute('SELECT vector FROM chunks').fetchone()[0]
    target_config = replace(base, enable_audio=False,enable_video=False,database=tmp_path/'data/text.sqlite3')
    target = Store(target_config,Engine(target_config))
    assert reuse_saved(target_config,target.db)==1
    assert target.db.execute('SELECT vector FROM chunks').fetchone()[0] == vector
    assert reuse_saved(target_config,target.db)==0
    assert target.index()['unchanged']==1
    foreign_config = replace(target_config, model_revision='other',database=tmp_path/'data/foreign.sqlite3')
    foreign = Store(foreign_config,Engine(foreign_config))
    assert reuse_saved(foreign_config,foreign.db)==0
    catalog=[{'id':'mlx-8bit','backend':'mlx','revision':'revision'}]
    profiles=historical_profiles(base,catalog)
    assert profiles['mlx-8bit']['dimensions']==128
    for store in (original,target,foreign): store.close()

def test_cancel_keeps_finished_and_unvisited_files_and_resumes(tmp_path):
    root=tmp_path/'files';root.mkdir()
    first=root/'a.txt';first.write_text('old')
    keep=root/'z.txt';keep.write_text('keep')
    config=Config((root,),tmp_path/'model','rev',tmp_path/'data/index.sqlite3',dimensions=128,
                  source_path=tmp_path/'config.toml',storage_path=tmp_path)
    config.save()
    seed=Store(config,Engine(config));seed.index();seed.close()
    first.write_text('new content')
    entered, release=threading.Event(),threading.Event()
    class Blocking(Engine):
        def document(self,text):
            if text=='new content':
                entered.set();assert release.wait(3)
            return super().document(text)
    with TestClient(create_app(config,Blocking(config))) as client:
        assert client.post('/v1/index/start').status_code==200
        assert entered.wait(2)
        assert client.post('/v1/index/cancel').json()['state']=='cancelling'
        release.set()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            job=client.get('/v1/settings').json()['job']
            if job['state']=='cancelled':break
            time.sleep(.01)
        assert job['state']=='cancelled'
        assert client.get('/v1/status').json()['files']==2
        result=client.post('/v1/index').json()
        assert result['updated']==1 and result['unchanged']==1 and not result.get('cancelled')

def test_model_round_trip_reopens_own_index_without_reencoding(tmp_path, monkeypatch):
    import tinysearch.settings as settings
    from tinysearch.settings import configured
    root=tmp_path/'files';root.mkdir();(root/'note.txt').write_text('test document')
    catalog=[dict(id='mlx-8bit',backend='mlx',revision='mlx-rev',path='models/mlx',repo='test/mlx'),
             dict(id='coreml-experimental',backend='coreml',revision='core-rev',path='models/core',repo='test/core')]
    for entry in catalog:
        folder=tmp_path/entry['path'];folder.mkdir(parents=True)
        for name in ('config.json','tokenizer.json','model.safetensors'):(folder/name).write_text('test')
    monkeypatch.setattr(settings,'CATALOG',catalog)
    monkeypatch.setattr(settings,'ManagedEngine',Engine)
    monkeypatch.setattr('tinysearch.coreml_assets.ensure_assets',lambda *a,**k:None)
    values=dict(model_variant='mlx-8bit',dimensions=128,enable_documents=True,enable_images=False,
                enable_video=False,enable_audio=False,chunk_tokens=256,thumbnail_px=256,idle_seconds=60)
    original=Config((root,),tmp_path/'model','test',tmp_path/'data/initial.sqlite3',dimensions=128,
                    storage_path=tmp_path,source_path=tmp_path/'config.toml')
    config=configured(original,values);config.save()
    seed=Store(config,Engine(config));seed.index()
    vector=seed.db.execute('SELECT vector FROM chunks').fetchone()[0];seed.close()
    def wait(client):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=client.get('/v1/settings').json()
            if result['job']['state'] in {'complete','error'}:break
            time.sleep(.01)
        assert result['job']['state']=='complete',result
        return result
    with TestClient(create_app(config,Engine(config))) as client:
        assert client.post('/v1/settings',json={**values,'model_variant':'coreml-experimental'}).status_code==200
        selected=wait(client)
        assert selected['profiles']['mlx-8bit']['chunk_tokens']==256
        assert Config.load(config.source_path).database!=config.database
        assert client.post('/v1/settings',json=selected['profiles']['mlx-8bit']).status_code==200
        returned=wait(client)
        assert Config.load(config.source_path).database==config.database
        assert returned['job']['reused_files']==0
        assert client.get('/v1/status').json()['files']==1
        import sqlite3
        with sqlite3.connect(config.database) as db:assert db.execute('SELECT vector FROM chunks').fetchone()[0]==vector

def test_cancel_synchronous_cli_scan_does_not_leave_settings_locked(tmp_path):
    root=tmp_path/'files';root.mkdir();(root/'a.txt').write_text('test')
    config=Config((root,),tmp_path/'model','rev',tmp_path/'data/index.sqlite3',dimensions=128,
                  source_path=tmp_path/'config.toml',storage_path=tmp_path)
    config.save()
    entered,release=threading.Event(),threading.Event()
    class Blocking(Engine):
        def document(self,text):
            entered.set();assert release.wait(3)
            return super().document(text)
    with TestClient(create_app(config,Blocking(config))) as client:
        response=[]
        thread=threading.Thread(target=lambda:response.append(client.post('/v1/index')));thread.start()
        assert entered.wait(2)
        assert client.post('/v1/index/cancel').json()['state']=='cancelling'
        release.set();thread.join(3)
        assert response[0].json()['cancelled']
        assert client.get('/v1/settings').json()['job']['state']=='cancelled'
        assert client.post('/v1/index/start').status_code==200


def test_shutdown_during_model_preparation_does_not_swap_or_start_index(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    root=tmp_path/'files';root.mkdir();(root/'a.txt').write_text('test')
    config=Config((root,),tmp_path/'old-model','old',tmp_path/'data/old.sqlite3',dimensions=128,
                  source_path=tmp_path/'config.toml',storage_path=tmp_path)
    config.save();original=config.source_path.read_text()
    entered,release=threading.Event(),threading.Event()
    monkeypatch.setattr(settings,'CATALOG',[dict(id='mlx-8bit',backend='mlx',revision='new',path='models/new',repo='test/new')])
    monkeypatch.setattr(settings,'ManagedEngine',Engine)
    def prepare(*args):
        entered.set();assert release.wait(3)
    monkeypatch.setattr(settings,'ensure_downloaded',prepare)
    with TestClient(create_app(config,Engine(config))) as client:
        response=client.post('/v1/settings',json=dict(model_variant='mlx-8bit',dimensions=128,
            chunk_tokens=256,enable_documents=True,enable_images=False))
        assert response.status_code==200 and entered.wait(2)
        stopped=[]
        thread=threading.Thread(target=lambda:stopped.append(client.app.state.settings.shutdown()))
        thread.start();assert client.app.state.settings.stopping.wait(2)
        release.set();thread.join(3)
        assert stopped==[True] and config.source_path.read_text()==original
        assert client.get('/v1/status').json()['files']==0
