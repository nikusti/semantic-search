from dataclasses import replace
from pathlib import Path
import time
from contextlib import nullcontext
from authenticated_client import TestClient
from test_store import FakeEngine
from tinysearch.config import Config
from tinysearch.server import create_app

def test_download_apply_persist_and_isolate_indexes(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    import huggingface_hub
    class Engine(FakeEngine):
        def __init__(self,config=None): self.closed=False
        def close(self): self.closed=True
        def pin(self): return nullcontext()
    root=tmp_path/'files';root.mkdir();(root/'a.txt').write_text('sample content')
    config=Config((root,),tmp_path/'original','old',tmp_path/'old.sqlite3',dimensions=128,source_path=tmp_path/'config.toml')
    config.save()
    catalog=[{'id':'mlx-8bit','path':'models/new','repo':'test/pinned','revision':'abc123','backend':'mlx','label':'test'}]
    monkeypatch.setattr(settings,'ROOT',tmp_path);monkeypatch.setattr(settings,'APP_SUPPORT',tmp_path);monkeypatch.setattr(settings,'CATALOG',catalog)
    monkeypatch.setattr(settings,'ManagedEngine',Engine)
    downloads=[]
    def download(**kwargs):
        downloads.append(kwargs)
        kwargs['local_dir'].mkdir(parents=True)
        for name in ('config.json','tokenizer.json','model.safetensors'): (kwargs['local_dir']/name).write_text('test')
    monkeypatch.setattr(huggingface_hub,'snapshot_download',download)
    values=dict(model_variant='mlx-8bit',dimensions=128,enable_documents=True,enable_images=False,thumbnail_px=128,chunk_tokens=256,idle_seconds=60)
    engine=Engine()
    with TestClient(create_app(config,engine)) as client:
        assert client.post('/v1/index').status_code==200
        assert client.post('/v1/settings',json={**values,'enable_documents':False}).status_code==422
        assert client.post('/v1/settings',json={**values,'dimensions':42}).status_code==422
        assert client.post('/v1/settings',json=values).status_code==200
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            result=client.get('/v1/settings').json()
            if result['job']['state'] in {'complete','error'}: break
            time.sleep(.05)
        assert result['job']['state']=='complete',result
        assert downloads[0]['revision']=='abc123' and downloads[0]['repo_id']=='test/pinned'
        assert result['models'][0]['installed']
        assert result['current']['dimensions']==128
        assert client.get('/v1/status').json()['files']==1
        assert engine.closed
        saved=Config.load(config.source_path)
        assert saved.backend=='mlx' and saved.database!=config.database
        assert saved.chunk_tokens==256
        assert config.database.exists()

def test_download_error_keeps_working_configuration(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    import huggingface_hub
    config=Config((tmp_path,),tmp_path/'old','old',tmp_path/'index.sqlite3',dimensions=128,source_path=tmp_path/'config.toml')
    config.save();previous=config.source_path.read_text()
    monkeypatch.setattr(settings,'ROOT',tmp_path);monkeypatch.setattr(settings,'APP_SUPPORT',tmp_path)
    monkeypatch.setattr(settings,'CATALOG',[{'id':'mlx-4bit','path':'missing','repo':'test/missing','revision':'pinned','backend':'mlx','label':'test'}])
    def fail(**kwargs): raise RuntimeError('Network unavailable')
    monkeypatch.setattr(huggingface_hub,'snapshot_download',fail)
    with TestClient(create_app(config,FakeEngine())) as client:
        values=dict(model_variant='mlx-4bit',dimensions=128,enable_documents=True,enable_images=True,thumbnail_px=256,chunk_tokens=384,idle_seconds=60)
        client.post('/v1/settings',json=values)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            job=client.get('/v1/settings').json()['job']
            if job['state']=='error':break
            time.sleep(.05)
        assert job['state']=='error' and 'Network unavailable' in job['error']
        assert client.get('/v1/status').status_code==200
        assert config.source_path.read_text()==previous

def test_enable_audio_seeds_documents_and_blocks_parallel_jobs(tmp_path,monkeypatch):
    import threading
    import numpy as np
    import tinysearch.settings as settings
    from tinysearch import media
    from tinysearch.engine import engine_identity,normalize
    from tinysearch.store import Store
    entered,release=threading.Event(),threading.Event()
    class Engine(FakeEngine):
        document_calls=0
        def __init__(self,config):
            self.identity=engine_identity(config);self.dimensions=config.dimensions
        def document(self,text):
            type(self).document_calls+=1
            return super().document(text)
        def audio(self,samples):
            entered.set();assert release.wait(3)
            return normalize(np.ones(self.dimensions),self.dimensions)
        def pin(self):return nullcontext()
        def close(self):pass
    root=tmp_path/'files';root.mkdir()
    (root/'a.txt').write_text('existing document');(root/'recording.mp3').write_bytes(b'audio')
    model=tmp_path/'models/new';model.mkdir(parents=True)
    for name in ('config.json','tokenizer.json','model.safetensors'):(model/name).write_text('test')
    monkeypatch.setattr(settings,'ROOT',tmp_path);monkeypatch.setattr(settings,'APP_SUPPORT',tmp_path)
    monkeypatch.setattr(settings,'CATALOG',[{'id':'mlx-8bit','path':'models/new','repo':'test/pinned','revision':'abc','backend':'mlx','label':'test'}])
    monkeypatch.setattr(settings,'ManagedEngine',Engine)
    monkeypatch.setattr(media,'probe',lambda *args:media.MediaInfo(10,False,True))
    monkeypatch.setattr(media,'audio_samples',lambda *args:np.zeros(16000,dtype=np.float32))
    values=dict(model_variant='mlx-8bit',dimensions=128,enable_documents=True,enable_images=True,enable_video=False,enable_audio=False,thumbnail_px=256,chunk_tokens=384,idle_seconds=60)
    original=Config((root,),model,'abc',tmp_path/'unused.sqlite3',backend='mlx',dimensions=128,source_path=tmp_path/'config.toml')
    config=settings.configured(original,values);config.save()
    base=Store(config,Engine(config));assert base.index()['indexed']==1
    old_vector=base.db.execute('SELECT vector FROM chunks').fetchone()[0];base.close()
    with TestClient(create_app(config,Engine(config))) as client:
        assert client.post('/v1/settings',json={**values,'enable_audio':True}).status_code==200
        try:
            assert entered.wait(3)
            assert client.post('/v1/index/start').status_code==409
            assert client.post('/v1/settings',json=values).status_code==409
        finally:release.set()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            job=client.get('/v1/settings').json()['job']
            if job['state'] in {'complete','error'}:break
            time.sleep(.02)
        assert job['state']=='complete',job
        assert client.get('/v1/status').json()['files']==2
        assert Engine.document_calls==1
        saved=Config.load(config.source_path)
        assert saved.enable_audio and saved.database!=config.database
        import sqlite3
        with sqlite3.connect(saved.database) as db:
            assert db.execute('SELECT vector FROM chunks WHERE path=?',(str(root/'a.txt'),)).fetchone()[0]==old_vector


def test_exclusions_save_during_scan_and_discard_inflight_and_queued_files(tmp_path):
    import threading
    import sqlite3
    from tinysearch.store import Store
    entered, release = threading.Event(), threading.Event()
    class Engine(FakeEngine):
        identity = 'embeddinggemma-2:test:128'
        block = False
        processed = []
        def document(self, text):
            self.processed.append(text)
            if self.block and text == 'changed in-flight content':
                entered.set()
                assert release.wait(5)
            return super().document(text)
    root = tmp_path / 'files'; root.mkdir()
    excluded = root / 'excluded'; excluded.mkdir()
    first = excluded / 'a.txt'; first.write_text('old content')
    keep = root / 'keep.txt'; keep.write_text('keep content')
    config = Config((root,), tmp_path/'model', 'test', tmp_path/'data/index.sqlite3', dimensions=128,
                    source_path=tmp_path/'config.toml', storage_path=tmp_path)
    config.save()
    seed = Store(config, Engine()); seed.index()
    seed.db.execute('INSERT INTO thumbnails VALUES (?,?)', (str(first), b'cached preview')); seed.db.commit(); seed.close()
    saved = tmp_path / 'data/other.sqlite3'
    source = sqlite3.connect(config.database); backup = sqlite3.connect(saved); source.backup(backup); source.close(); backup.close()
    first.write_text('changed in-flight content')
    (excluded/'b.txt').write_text('queued excluded content')
    engine = Engine(); engine.block = True; engine.processed = []
    with TestClient(create_app(config, engine)) as client:
        assert client.post('/v1/index/start').status_code == 200
        try:
            assert entered.wait(3)
            revision = client.get('/v1/status').json()['index_revision']
            response = client.post('/v1/exclusions', json={'excluded_paths':[str(excluded)]})
            assert response.status_code == 200, response.text
            assert response.json()['indexing'] and response.json()['excluded_files_removed'] == 1
            assert response.json()['excluded_saved_entries_removed'] == 1
            assert Config.load(config.source_path).excluded_paths == (excluded,)
            assert client.get('/v1/settings').json()['current']['excluded_paths'] == [str(excluded)]
            status = client.get('/v1/status').json()
            assert status['files'] == 1 and status['index_revision'] != revision
            assert all(not Path(row['path']).is_relative_to(excluded) for row in client.post('/v1/search', json={'query':'keep'}).json()['results'])
        finally:
            release.set()
        deadline = time.monotonic()+4
        while client.get('/v1/status').json()['indexing'] and time.monotonic()<deadline: time.sleep(.02)
        status = client.get('/v1/status').json()
        assert status['files'] == 1 and status['last_index_summary']['excluded'] == 2
        assert status['last_index_summary']['errors'] == 0
        assert engine.processed == ['changed in-flight content']  # No queued embedding or unchanged-file re-embedding.
    for database in (config.database, saved):
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT count(*) FROM files').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM thumbnails').fetchone()[0] == 0
    assert first.exists()


def test_exclusion_validation_and_regular_apply_fast_path(tmp_path):
    root = tmp_path/'files'; root.mkdir(); folder = root/'excluded'; folder.mkdir()
    file = root/'keep.txt'; file.write_text('retained document')
    config = Config((root,), tmp_path/'model', 'test', tmp_path/'data/index.sqlite3', dimensions=128,
                    source_path=tmp_path/'config.toml', storage_path=tmp_path)
    config.save()
    with TestClient(create_app(config, FakeEngine())) as client:
        assert client.post('/v1/exclusions', json={'excluded_paths':['relative/path']}).status_code == 422
        assert client.post('/v1/exclusions', json={'excluded_paths':[str(file)]}).status_code == 422
        values = client.get('/v1/settings').json()['current']
        response = client.post('/v1/settings', json={**values, 'excluded_paths':[str(folder)]})
        assert response.status_code == 200 and response.json()['state'] == 'saved'
        assert Config.load(config.source_path).excluded_paths == (folder,)
        assert client.post('/v1/exclusions', json={'excluded_paths':[]}).status_code == 200
        assert Config.load(config.source_path).excluded_paths == ()


def test_excluding_audio_stops_after_current_segment(tmp_path, monkeypatch):
    import threading
    import numpy as np
    from tinysearch import media
    entered, release = threading.Event(), threading.Event()
    class Engine(FakeEngine):
        audio_calls = 0
        def audio(self, samples):
            self.audio_calls += 1; entered.set(); assert release.wait(5)
            return super().document('sound')
    root = tmp_path/'files'; root.mkdir(); folder = root/'excluded'; folder.mkdir()
    (folder/'recording.mp3').write_bytes(b'synthetic audio')
    config = Config((root,), tmp_path/'model', 'test', tmp_path/'data/index.sqlite3', backend='mlx',
                    enable_audio=True, dimensions=128, source_path=tmp_path/'config.toml', storage_path=tmp_path)
    config.save()
    monkeypatch.setattr(media, 'probe', lambda *args: media.MediaInfo(60,False,True))
    decodes = []
    monkeypatch.setattr(media, 'audio_samples', lambda *args: decodes.append(True) or np.zeros(16000,dtype=np.float32))
    engine = Engine()
    with TestClient(create_app(config, engine)) as client:
        assert client.post('/v1/index/start').status_code == 200
        try:
            assert entered.wait(3)
            assert client.post('/v1/exclusions', json={'excluded_paths':[str(folder)]}).status_code == 200
        finally: release.set()
        deadline = time.monotonic()+4
        while client.get('/v1/status').json()['indexing'] and time.monotonic()<deadline: time.sleep(.02)
        status = client.get('/v1/status').json()
        assert status['files'] == status['chunks'] == 0
        assert status['last_index_summary']['errors'] == 0
        assert status['last_index_summary']['excluded'] == 1
        assert engine.audio_calls == len(decodes) == 1


def test_unavailable_old_index_does_not_block_live_exclusion_or_later_cleanup(tmp_path):
    import sqlite3
    from tinysearch.store import Store
    class Engine(FakeEngine): identity = 'embeddinggemma-2:test:128'
    root = tmp_path/'files'; root.mkdir(); folder = root/'excluded'; folder.mkdir()
    (folder/'a.txt').write_text('synthetic excluded document')
    config = Config((root,), tmp_path/'model', 'test', tmp_path/'data/index.sqlite3', dimensions=128,
                    source_path=tmp_path/'config.toml', storage_path=tmp_path)
    config.save(); seed = Store(config, Engine()); seed.index()
    valid = config.database.parent/'z-valid.sqlite3'
    with sqlite3.connect(valid) as db: seed.db.backup(db)
    seed.close()
    corrupt = config.database.parent/'a-unavailable.sqlite3'; corrupt.write_bytes(b'not sqlite')
    app = create_app(config, Engine())
    with TestClient(app) as client:
        app.state.settings.job = {'state':'error','error':'previous setup failure'}
        response = client.post('/v1/exclusions', json={'excluded_paths':[str(folder)]})
        assert response.status_code == 200
        assert response.json()['cleanup_pending'] == 1
        assert response.json()['excluded_saved_entries_removed'] == 1
        assert client.get('/v1/status').json()['files'] == 0
        assert Config.load(config.source_path).excluded_paths == (folder,)
        job = client.get('/v1/settings').json()['job']
        assert job['state'] == 'complete' and job['error'] is None
        assert job['exclusion_cleanup_pending'] == 1
    with sqlite3.connect(valid) as db: assert db.execute('SELECT count(*) FROM files').fetchone()[0] == 0
    assert corrupt.read_bytes() == b'not sqlite'
