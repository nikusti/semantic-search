from contextlib import nullcontext
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import time

from authenticated_client import TestClient
from test_store import FakeEngine
from tinysearch.config import Config
from tinysearch.engine import engine_identity
from tinysearch.server import create_app
from tinysearch.store import Store


def test_exclusion_prunes_nested_records_caches_and_not_similar_names(tmp_path):
    root=tmp_path/'files';root.mkdir()
    excluded=root/'private%_';(excluded/'nested').mkdir(parents=True)
    sibling=root/'private%_other';sibling.mkdir()
    a=excluded/'a.txt';b=excluded/'nested/b.txt';keep=sibling/'keep.txt'
    for path in (a,b,keep):path.write_text(str(path.name))
    config=Config((root,),tmp_path/'model','test',tmp_path/'index.sqlite3',dimensions=128)
    store=Store(config,FakeEngine());assert store.index()['indexed']==3
    original=store.db.execute('SELECT vector FROM chunks WHERE path=?',(str(keep),)).fetchone()[0]
    for path in (a,keep):
        store.db.execute('INSERT INTO thumbnails VALUES (?,?)',(str(path),b'thumbnail'))
        store.db.execute('INSERT INTO pdf_previews VALUES (?,?,?,?,?,?)',(str(path),1,1,b'preview',1,0))
        store.db.execute('INSERT INTO media_files VALUES (?,?,?)',(str(path),10,1))
    store.db.execute('UPDATE files SET kind=? WHERE path=?',('video',str(a)))
    store.db.execute('UPDATE chunks SET start_seconds=0,end_seconds=10,modality=? WHERE path=?',('video',str(a)))
    row=store.db.execute('SELECT mtime,size FROM files WHERE path=?',(str(a),)).fetchone()
    key=hashlib.sha256(f'{a}:{row[0]}:{row[1]}:0.0:10.0'.encode()).hexdigest()
    cache=tmp_path/'segment-previews';cache.mkdir();clip=cache/(key+'.mp4');clip.write_bytes(b'clip')
    store.db.commit();store.close()
    engine=FakeEngine();store=Store(replace(config,excluded_paths=(excluded,)),engine)
    try:
        assert store.excluded_removed==2 and store.status()['files']==1
        for table in ('files','chunks','thumbnails','pdf_previews','media_files'):
            assert store.db.execute(f'SELECT count(*) FROM {table} WHERE path=?',(str(a),)).fetchone()[0]==0
            assert store.db.execute(f'SELECT count(*) FROM {table} WHERE path=?',(str(keep),)).fetchone()[0]==1
        assert not clip.exists() and a.exists() and b.exists()
        assert store.db.execute('SELECT vector FROM chunks WHERE path=?',(str(keep),)).fetchone()[0]==original
        assert store.index()['indexed']==0 and engine.calls==0
        assert [r['path'] for r in store.search('a')['results']]==[str(keep)]
    finally:store.close()
    store=Store(replace(config,excluded_paths=(root,)),FakeEngine())
    assert store.status()['files']==0 and store.index()['errors']==[];store.close()


def test_settings_exclusion_persists_purges_saved_indexes_and_can_restore(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    class Engine(FakeEngine):
        documents=[]
        def __init__(self,config):self.identity=engine_identity(config);self.dimensions=config.dimensions
        def document(self,text):type(self).documents.append(text);return super().document(text)
        def close(self):pass
        def pin(self):return nullcontext()
    root=tmp_path/'files';root.mkdir();folder=root/'exclude me';folder.mkdir()
    a=folder/'a.txt';a.write_text('excluded content');keep=root/'keep.txt';keep.write_text('retained content')
    model=tmp_path/'models/new';model.mkdir(parents=True)
    for name in ('config.json','tokenizer.json','model.safetensors'):(model/name).write_text('test')
    monkeypatch.setattr(settings,'ROOT',tmp_path);monkeypatch.setattr(settings,'APP_SUPPORT',tmp_path);monkeypatch.setattr(settings,'ManagedEngine',Engine)
    monkeypatch.setattr(settings,'CATALOG',[{'id':'mlx-8bit','path':'models/new','repo':'test','revision':'abc','backend':'mlx','label':'test'}])
    values=dict(model_variant='mlx-8bit',dimensions=128,enable_documents=True,enable_images=False,enable_video=False,enable_audio=False,thumbnail_px=128,chunk_tokens=384,idle_seconds=60)
    original=Config((root,),model,'abc',tmp_path/'unused.sqlite3',dimensions=128,backend='mlx',source_path=tmp_path/'config.toml')
    config=settings.configured(original,values);config.save()
    store=Store(config,Engine(config));store.index()
    saved=config.database.parent/'alternate.sqlite3'
    with sqlite3.connect(saved) as db:store.db.backup(db)
    other=config.database.parent/'unrelated.sqlite3'
    with sqlite3.connect(other) as db:
        store.db.backup(db);db.execute("UPDATE meta SET value='unrelated' WHERE key='engine'")
    vector=store.db.execute('SELECT vector FROM chunks WHERE path=?',(str(keep),)).fetchone()[0];store.close()
    def wait(client):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            status=client.get('/v1/settings').json()
            if status['job']['state'] in {'complete','error'}:break
            time.sleep(.02)
        assert status['job']['state']=='complete',status
        return status
    with TestClient(create_app(config,Engine(config))) as client:
        assert client.post('/v1/settings',json={**values,'excluded_paths':['relative/path']}).status_code==422
        assert client.post('/v1/settings',json={**values,'excluded_paths':[str(a)]}).status_code==422
        assert client.post('/v1/settings',json={**values,'excluded_paths':[str(folder),str(folder)]}).status_code==200
        status=wait(client)
        assert status['current']['excluded_paths']==[str(folder)]
        assert status['job']['excluded_files_removed']==1 and status['job']['excluded_saved_entries_removed']==1
        assert client.get('/v1/status').json()['files']==1
        persisted=Config.load(config.source_path)
        assert persisted.excluded_paths==(folder,) and persisted.database==config.database
        with sqlite3.connect(config.database) as db:
            assert db.execute('SELECT vector FROM chunks').fetchone()[0]==vector
        with sqlite3.connect(saved) as db:assert db.execute('SELECT count(*) FROM files').fetchone()[0]==1
        with sqlite3.connect(other) as db:assert db.execute('SELECT count(*) FROM files').fetchone()[0]==2
        assert len(Engine.documents)==2 and a.read_text()=='excluded content'
        assert client.post('/v1/index').json()['indexed']==0
        # Older clients omitting the list must preserve existing exclusions.
        assert client.post('/v1/settings',json=values).status_code==200
        assert wait(client)['current']['excluded_paths']==[str(folder)]
        assert client.post('/v1/settings',json={**values,'excluded_paths':[]}).status_code==200
        wait(client)
        assert client.post('/v1/index').json()['indexed']==1
        assert client.get('/v1/status').json()['files']==2
