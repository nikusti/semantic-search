from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import sqlite3
import threading
import time

from fastapi.testclient import TestClient
import pytest
from tinysearch.config import Config
from tinysearch.engine import engine_identity
from tinysearch.server import create_app
from tinysearch.store import Store
from test_store import FakeEngine


class Engine(FakeEngine):
    def __init__(self, config):
        self.identity = engine_identity(config)
        self.dimensions = config.dimensions
        self.documents = []

    def document(self, text):
        self.documents.append(text)
        return super().document(text)

    def pin(self): return nullcontext()
    def close(self): pass


def library(tmp_path, roots=('a',)):
    selected = []
    for name in roots:
        root = tmp_path/name; root.mkdir()
        (root/'entry.txt').write_text('Content in '+name)
        selected.append(root)
    config = Config(tuple(selected),tmp_path/'model','test',tmp_path/'data/index.sqlite3',
                    dimensions=128,source_path=tmp_path/'config.toml',storage_path=tmp_path)
    config.save()
    engine = Engine(config)
    store = Store(config,engine); store.index(); store.close()
    engine.documents.clear()
    return config,engine


def backup(source, target):
    with sqlite3.connect(source) as old, sqlite3.connect(target) as new: old.backup(new)


def test_add_sibling_roots_keeps_database_and_vectors(tmp_path):
    config,engine = library(tmp_path)
    b = tmp_path/'b'; b.mkdir(); (b/'entry.txt').write_text('Another source')
    c = tmp_path/'c'; c.mkdir(); (c/'entry.txt').write_text('Third source')
    with sqlite3.connect(config.database) as db: vector = db.execute('SELECT vector FROM chunks').fetchone()[0]
    with TestClient(create_app(config,engine)) as client:
        assert client.post('/v1/folders/add',json={'paths':[str(b)]}).json()['roots'] == [str(tmp_path/'a'),str(b)]
        assert client.post('/v1/folders/add',json={'paths':[str(c),str(b)]}).status_code == 200
        saved = Config.load(config.source_path)
        assert saved.database == config.database and saved.roots == (tmp_path/'a',b,c)
        assert engine.documents == []
        assert client.post('/v1/index').json()['added'] == 2
        assert engine.documents == ['Another source','Third source']
        assert client.get('/v1/status').json()['files'] == 3
        assert len(client.get('/v1/settings').json()['folders']) == 3
        with sqlite3.connect(config.database) as db:
            assert db.execute('SELECT vector FROM chunks WHERE path=?',(str(tmp_path/'a/entry.txt'),)).fetchone()[0] == vector
        assert {r['path'] for r in client.post('/v1/search',json={'query':'content','limit':10}).json()['results']} == {
            str(tmp_path/name/'entry.txt') for name in ('a','b','c')}


def test_remove_keep_restart_and_restore_without_reencoding(tmp_path):
    config,engine = library(tmp_path,('a','b'))
    a = tmp_path/'a'
    with TestClient(create_app(config,engine)) as client:
        result = client.post('/v1/folders/remove',json={'path':str(a)}).json()
        assert result['deleted_entries'] == 0 and result['roots'] == [str(tmp_path/'b')]
        status = client.get('/v1/status').json()
        assert status['files'] == status['retained_files'] == 1
        assert [r['path'] for r in client.post('/v1/search',json={'query':'content'}).json()['results']] == [str(tmp_path/'b/entry.txt')]
        assert client.post('/v1/preview',json={'path':str(a/'entry.txt')}).status_code == 404
    saved = Config.load(config.source_path)
    with TestClient(create_app(saved,engine)) as client:
        assert client.get('/v1/status').json()['retained_files'] == 1
        assert client.post('/v1/folders/add',json={'paths':[str(a)]}).status_code == 200
        assert client.get('/v1/status').json()['files'] == 2
        assert client.post('/v1/index').json()['unchanged'] == 2
        assert engine.documents == []
        client.post('/v1/folders/remove',json={'path':str(a)})
        assert client.post('/v1/folders/remove',json={'path':str(tmp_path/'b')}).status_code == 200
        assert client.get('/v1/status').json()['files'] == 0
        assert client.post('/v1/search',json={'query':'content'}).json()['results'] == []
        assert client.post('/v1/index/start').status_code == 422
    assert (a/'entry.txt').exists()


def test_explicit_folder_cleanup_removes_only_its_records_in_saved_indexes(tmp_path):
    config,engine = library(tmp_path,('a','ab'))
    a,b = config.roots
    with sqlite3.connect(config.database) as db:
        db.execute('INSERT INTO thumbnails VALUES (?,?)',(str(a/'entry.txt'),b'preview'))
        db.execute('INSERT INTO thumbnail_specs VALUES (?,?,?)',(str(a/'entry.txt'),256,60))
        db.execute('INSERT INTO pdf_previews VALUES (?,?,?,?,?,?)',(str(a/'entry.txt'),0,0,b'preview',1,0))
        db.execute('INSERT INTO files VALUES (?,?,?,?,?,?)',('photos://asset/example','apple-photos','image',0,0,'sample'))
        db.execute('INSERT INTO photos_assets VALUES (?,?,?,?)',('photos://asset/example','example','image.jpg','sample'))
        db.commit()
    old = config.database.parent/'other-model.sqlite3'; backup(config.database,old)
    foreign = config.database.parent/'unrelated.sqlite3'; backup(config.database,foreign)
    with sqlite3.connect(foreign) as db: db.execute("UPDATE meta SET value='unrelated' WHERE key='engine'")
    with TestClient(create_app(config,engine)) as client:
        response = client.post('/v1/folders/remove',json={'path':str(a),'delete_saved_index':True})
        assert response.status_code == 200 and response.json()['deleted_entries'] == 2
        assert response.json()['cleanup_pending'] == 0
    for database in (config.database,old):
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT count(*) FROM files WHERE path=?',(str(a/'entry.txt'),)).fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM chunks WHERE path=?',(str(a/'entry.txt'),)).fetchone()[0] == 0
            for table in ('thumbnails','thumbnail_specs','pdf_previews'):
                assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM files').fetchone()[0] == 2  # Sibling and Photos.
    with sqlite3.connect(foreign) as db: assert db.execute('SELECT count(*) FROM files').fetchone()[0] == 3
    assert (a/'entry.txt').exists() and (b/'entry.txt').exists()


def test_removing_parent_keeps_selected_child_and_does_not_rebuild(tmp_path):
    config,engine = library(tmp_path)
    child = config.roots[0]/'child'; child.mkdir(); (child/'entry.txt').write_text('Nested source')
    store = Store(config,engine);store.index();store.close();engine.documents.clear()
    with TestClient(create_app(config,engine)) as client:
        assert client.post('/v1/folders/add',json={'paths':[str(child)]}).status_code == 200
        assert client.post('/v1/folders/remove',json={'path':str(config.roots[0]),'delete_saved_index':True}).json()['deleted_entries'] == 1
        assert client.get('/v1/status').json()['files'] == 1
        assert client.post('/v1/index').json()['unchanged'] == 1
        assert engine.documents == []
        assert client.post('/v1/search',json={'query':'nested'}).json()['results'][0]['path'] == str(child/'entry.txt')


def test_nested_sources_scan_once_and_prune_by_path_not_old_owner(tmp_path):
    config,engine = library(tmp_path)
    child = config.roots[0]/'child'; child.mkdir(); file = child/'entry.txt'; file.write_text('Nested source')
    store = Store(config,engine);store.index();store.close();engine.documents.clear()
    with TestClient(create_app(config,engine)) as client:
        client.post('/v1/folders/add',json={'paths':[str(child)]})
        result = client.post('/v1/index').json()
        assert result['unchanged'] == 2 and result['indexed'] == 0
        assert engine.documents == []
        file.unlink()
        assert client.post('/v1/index').json()['deleted'] == 1
        assert client.get('/v1/status').json()['files'] == 1


def test_removed_inflight_root_cannot_reinsert_but_other_sources_continue(tmp_path):
    config,unused = library(tmp_path,('a','b'))
    a,b = config.roots
    (a/'entry.txt').write_text('Changed in-flight content')
    (a/'queued.txt').write_text('Removed queued content')
    entered,release = threading.Event(),threading.Event()
    class BlockingEngine(Engine):
        def document(self,text):
            if text == 'Changed in-flight content': entered.set(); assert release.wait(5)
            return super().document(text)
    engine = BlockingEngine(config)
    with TestClient(create_app(config,engine)) as client:
        assert client.post('/v1/index/start').status_code == 200
        try:
            assert entered.wait(3)
            assert client.post('/v1/folders/remove',json={'path':str(a),'delete_saved_index':True}).status_code == 200
        finally: release.set()
        deadline = time.monotonic()+4
        while client.get('/v1/status').json()['indexing'] and time.monotonic()<deadline: time.sleep(.02)
        assert client.get('/v1/status').json()['files'] == 1
        assert engine.documents == ['Changed in-flight content']
        with sqlite3.connect(config.database) as db: assert db.execute('SELECT count(*) FROM files').fetchone()[0] == 1
        assert [r['path'] for r in client.post('/v1/search',json={'query':'content'}).json()['results']] == [str(b/'entry.txt')]


def test_removing_delegated_child_during_parent_walk_keeps_covered_entries(tmp_path):
    config,engine = library(tmp_path)
    parent = config.roots[0]
    child = parent/'child'; child.mkdir(); (child/'entry.txt').write_text('Nested content')
    store = Store(config,engine); store.index(); store.close()
    config = replace(config,roots=(parent,child)); config.save()
    store = Store(config,engine)
    scan = store._scan
    def edit_during_walk(root):
        result = scan(root)
        if root == parent: store.update_folders(replace(store.config,roots=(parent,)))
        return result
    store._scan = edit_during_walk
    try:
        assert store.index()['deleted'] == 0
        assert store.status()['files'] == 2
        assert store.index()['unchanged'] == 2
    finally: store.close()


def test_cleanup_pending_survives_restart_and_can_retry(tmp_path):
    config,engine = library(tmp_path,('a','b'))
    broken = config.database.parent/'unavailable.sqlite3'; broken.write_bytes(b'not sqlite')
    with TestClient(create_app(config,engine)) as client:
        assert client.post('/v1/folders/remove',json={'path':str(config.roots[0]),'delete_saved_index':True}).json()['cleanup_pending'] == 1
    backup(config.database,config.database.parent/'good-copy.sqlite3')
    broken.unlink(); backup(config.database.parent/'good-copy.sqlite3',broken)
    with TestClient(create_app(Config.load(config.source_path),engine)) as client:
        assert client.get('/v1/settings').json()['job']['folder_cleanup_pending'] == 1
        assert client.post('/v1/folders/cleanup').json()['cleanup_pending'] == 0
    assert not (tmp_path/'folder-cleanup.json').exists()


def test_readding_folder_cancels_pending_deletion_before_later_keep(tmp_path):
    config,engine = library(tmp_path,('a','b'))
    broken = config.database.parent/'unavailable.sqlite3'; broken.write_bytes(b'not sqlite')
    with TestClient(create_app(config,engine)) as client:
        assert client.post('/v1/folders/remove',json={'path':str(config.roots[0]),'delete_saved_index':True}).json()['cleanup_pending'] == 1
        assert client.post('/v1/folders/add',json={'paths':[str(config.roots[0])]}).status_code == 200
        assert not (tmp_path/'folder-cleanup.json').exists()
        client.post('/v1/index')
        assert client.post('/v1/folders/remove',json={'path':str(config.roots[0])}).status_code == 200
        assert client.post('/v1/folders/cleanup').json()['deleted_entries'] == 0
        assert client.get('/v1/status').json()['retained_files'] == 1


def test_legacy_roots_only_apply_preserves_database_and_rejects_invalid_addition(tmp_path):
    config,engine = library(tmp_path)
    b = tmp_path/'b';b.mkdir()
    with TestClient(create_app(config,engine)) as client:
        values = client.get('/v1/settings').json()['current']
        assert client.post('/v1/settings',json={**values,'roots':[str(config.roots[0]),str(b)]}).status_code == 200
        assert Config.load(config.source_path).database == config.database
        before = config.source_path.read_bytes()
        assert client.post('/v1/folders/add',json={'paths':['relative']}).status_code == 422
        assert client.post('/v1/folders/add',json={'paths':[str(config.roots[0]/'entry.txt')]}).status_code == 422
        assert config.source_path.read_bytes() == before
        assert engine.documents == []
