from dataclasses import replace
import hashlib

import numpy as np
import pytest
from PIL import Image

from tinysearch.config import Config
from tinysearch.engine import normalize
from tinysearch.photos import asset_path
from tinysearch.store import Store


class Engine:
    identity = 'test-photos:128:v1'
    dimensions = 128
    calls = 0

    def image(self, path):
        self.calls += 1
        return normalize(list(hashlib.sha256(path.read_bytes()).digest()) * 4, 128)

    def query(self, text): return normalize(np.ones(128), 128)
    def document(self, text): return self.query(text)
    def chunks(self, text): return [text]


@pytest.fixture
def library(tmp_path, monkeypatch):
    image = tmp_path/'temporary.jpg'
    Image.new('RGB', (160, 80), 'blue').save(image)

    class Library:
        rows = [{'id': f'asset-{i}/L0/001', 'name': f'Photo {i}', 'modified': 1, 'width': 160, 'height': 80} for i in range(3)]
        deferred = set()
        fail = False
        image_calls = []
        image_name = None
        before_image = None

        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def assets(self):
            if self.fail: raise RuntimeError('Permission denied')
            return list(self.rows)
        def image(self, identifier, network=False, timeout=180):
            self.image_calls.append((identifier, network))
            if self.before_image: self.before_image()
            if identifier in self.deferred and not network: return None
            Image.new('RGB', (160, 80), 'blue').save(image)
            return image

    monkeypatch.setattr('tinysearch.photos.PhotoLibrary', Library)
    return Library


@pytest.fixture
def config(tmp_path):
    root = tmp_path/'documents'; root.mkdir()
    return Config((root,), tmp_path/'model', 'test', tmp_path/'index.sqlite3', dimensions=128, enable_photos_library=True)


def test_library_incremental_updates_and_deletion_preserve_folders(config, library):
    (config.roots[0]/'note.txt').write_text('document')
    engine = Engine(); store = Store(config, engine)
    assert store.index()['added'] == 4
    assert store.index()['unchanged'] == 4 and engine.calls == 3
    library.rows[0] = {**library.rows[0], 'modified': 2}
    library.rows = library.rows[:2]
    result = store.index()
    assert (result['updated'], result['deleted'], result['unchanged']) == (1, 1, 2)
    assert store.status()['files'] == 3
    store.close()
    store = Store(config, Engine())
    assert store.status()['photos_library']['indexed'] == 2
    results = store.search('blue', 'image')['results']
    assert len(results) == 2 and all(r['source'] == 'photos' for r in results)
    assert results[0]['open_target'].startswith('photos://asset?identifier=asset-')
    assert '/L0' not in results[0]['open_target']
    assert results[0]['thumbnail'].startswith('data:image/jpeg;base64,')
    store.close()


def test_disabled_source_and_removed_folder_keep_photos_index(config, library):
    store = Store(config, Engine()); store.index(); store.close()
    store = Store(replace(config, roots=(), enable_photos_library=False), Engine())
    assert store.status()['photos_library']['indexed'] == 3
    assert not store.search('blue')['results']
    with pytest.raises(ValueError): store.preview(asset_path(library.rows[0]['id']))
    store.close()
    store = Store(replace(config, roots=()), Engine())
    assert len(store.search('blue')['results']) == 3
    store.close()


def test_icloud_retry_and_denied_permission_never_prune(config, library):
    library.deferred = {library.rows[0]['id']}
    store = Store(config, Engine())
    first = store.index()
    assert first['added'] == 2 and first['deferred_photos'] == 1 and not first['errors']
    store.config = replace(config, photos_allow_network=True)
    assert store.index()['added'] == 1
    assert library.image_calls[-1][1] is True
    library.fail = True
    assert store.index()['errors'] and store.status()['files'] == 3
    store.close()


def test_cancellation_keeps_completed_photos(config, library):
    store = Store(config, Engine())
    # Cancel on the second request, including before its vector can be committed.
    library.before_image = lambda self: store.cancel_index() if len(self.image_calls) == 2 else None
    result = store.index()
    assert result['cancelled'] and store.status()['files'] == 1
    library.before_image = None
    assert store.index()['added'] == 2
    store.close()


def test_preview_has_bounded_cache_and_offline_thumbnail(config, library):
    store = Store(config, Engine()); store.index()
    path = asset_path(library.rows[0]['id'])
    calls = len(library.image_calls)
    assert store.preview(path)['preview'].startswith('data:image/jpeg;base64,')
    assert len(library.image_calls) == calls + 1
    store.preview(path)
    assert len(library.image_calls) == calls + 1
    library.deferred = {library.rows[1]['id']}
    assert store.preview(asset_path(library.rows[1]['id']))['preview']
    with pytest.raises(ValueError): store.preview(asset_path('unindexed'))
    assert store.db.execute('SELECT sum(length(jpeg)) FROM pdf_previews').fetchone()[0] < 32 * 1024**2
    store.close()


def test_edited_photo_during_download_keeps_old_vector(config, library):
    store = Store(config, Engine()); store.index()
    library.rows[0] = {**library.rows[0], 'modified': 2}
    library.image_modified = 3
    result = store.index()
    assert result['errors'] and result['updated'] == 0
    assert store.status()['files'] == 3
    store.close()


def test_saved_photo_vectors_reuse_only_with_the_same_model(config, library):
    from tinysearch.engine import engine_identity
    from tinysearch.index_reuse import reuse_saved
    class Identified(Engine):
        def __init__(self, config): self.identity = engine_identity(config)
    config = replace(config, backend='mlx', model_variant='mlx-8bit')
    original = Store(config, Identified(config)); original.index()
    target_config = replace(config, roots=(), database=config.database.with_name('target.sqlite3'))
    target = Store(target_config, Identified(target_config))
    assert reuse_saved(target_config, target.db) == 3
    assert target.index()['unchanged'] == 3
    other = replace(target_config, model_revision='other', database=config.database.with_name('foreign.sqlite3'))
    foreign = Store(other, Identified(other))
    assert reuse_saved(other, foreign.db) == 0
    for store in (original, target, foreign): store.close()


def test_legacy_settings_preserve_source_and_photos_only_root_is_valid(config, library):
    from tinysearch.settings import configured
    config = replace(config, roots=(), backend='mlx', model_variant='mlx-8bit', storage_path=config.database.parent)
    values = dict(model_variant='mlx-8bit', dimensions=128, enable_images=True, enable_documents=True,
                  chunk_tokens=384, enable_photos_library=None, photos_allow_network=None)
    next_config = configured(config, values)
    assert next_config.enable_photos_library and not next_config.roots
    with pytest.raises(ValueError, match='Photographs and images'):
        configured(config, {**values, 'enable_images': False})


def test_photos_api_is_explicit_and_uses_mac_permission(config, library):
    from fastapi.testclient import TestClient
    from tinysearch.server import create_app
    def request(self, op):
        return {'authorization':'authorized'} if op == 'authorize' else {'photos':len(self.rows)}
    library.request = request
    with TestClient(create_app(config, Engine())) as client:
        assert client.post('/v1/photos/connect').json() == {'authorization':'authorized','photos':3}
        assert client.post('/v1/photos/connect', headers={'Origin':'https://example.com'}).status_code == 403
        assert client.post('/v1/index').json()['added'] == 3
        path = asset_path(library.rows[0]['id'])
        assert client.post('/v1/preview', json={'path':path}).status_code == 200
        assert client.post('/v1/preview', json={'path':asset_path('unknown')}).status_code == 404


def test_low_disk_stops_without_pruning_unvisited_photos(config, library, monkeypatch):
    store = Store(config, Engine()); store.index()
    library.rows[0] = {**library.rows[0], 'modified': 2}
    calls = len(library.image_calls)
    from types import SimpleNamespace
    monkeypatch.setattr('shutil.disk_usage', lambda path: SimpleNamespace(free=1))
    result = store.index()
    assert len(result['errors']) == 1 and '512 MB' in result['errors'][0]
    assert len(library.image_calls) == calls and result['deleted'] == 0
    assert store.status()['files'] == 3
    store.close()


def test_slow_cloud_preview_does_not_block_search_or_queue_other_previews(config, library, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from tinysearch.server import create_app
    entered, release = threading.Event(), threading.Event()
    original = Store._photos_preview
    def slow(self, path):
        entered.set()
        assert release.wait(3)
        return original(self, path)
    monkeypatch.setattr(Store, '_photos_preview', slow)
    with TestClient(create_app(config, Engine())) as client:
        client.post('/v1/index')
        path = asset_path(library.rows[0]['id'])
        replies = []
        thread = threading.Thread(target=lambda: replies.append(client.post('/v1/preview', json={'path':path})))
        thread.start()
        try:
            assert entered.wait(1)
            assert client.post('/v1/search', json={'query':'blue'}).status_code == 200
            assert client.post('/v1/preview', json={'path':asset_path(library.rows[1]['id'])}).status_code == 200
        finally:
            release.set(); thread.join(3)
        assert replies and replies[0].status_code == 200


def test_missing_thumbnail_is_repaired_without_reloading_visual_model(config, library):
    engine = Engine(); store = Store(config, engine); store.index()
    calls = engine.calls
    path = asset_path(library.rows[0]['id'])
    with store.db: store.db.execute('DELETE FROM thumbnails WHERE path=?', (path,))
    assert store.index()['unchanged'] == 3
    assert engine.calls == calls
    assert store.db.execute('SELECT 1 FROM thumbnails WHERE path=?', (path,)).fetchone()
    store.close()
