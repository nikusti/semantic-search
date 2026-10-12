from dataclasses import replace
import sqlite3
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tinysearch.config import Config
from tinysearch.server import create_app
from tinysearch.store import Store, PHOTOS_AUDIO_PENDING
from test_store import FakeEngine
from test_photos import config, library, videos, Engine, VideoEngine


def folder_config(tmp_path):
    root = tmp_path/'files'; root.mkdir()
    return Config((root,), tmp_path/'model', 'test', tmp_path/'index.sqlite3', dimensions=128)


def test_unchanged_photos_never_read_saved_vectors_or_jpeg_payloads(config, library):
    engine = Engine(); store = Store(config, engine); store.index()
    before = [tuple(r) for r in store.db.execute('SELECT * FROM chunks')]
    calls = len(library.image_calls)
    def authorize(action, table, column, *args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and (table, column) in {('chunks', 'vector'), ('thumbnails', 'jpeg')} else sqlite3.SQLITE_OK
    store.db.set_authorizer(authorize)
    try:
        result = store.index()
        assert result['unchanged'] == 3 and not result['errors']
    finally: store.db.set_authorizer(None)
    assert len(library.image_calls) == calls and engine.calls == 3
    assert before == [tuple(r) for r in store.db.execute('SELECT * FROM chunks')]
    store.close()


def test_small_photos_representation_is_not_regenerated_each_pass(config, library):
    library.rows = [{**row, 'width':4000, 'height':3000} for row in library.rows]
    engine = Engine(); store = Store(config, engine); store.index()
    before = [tuple(r) for r in store.db.execute('SELECT * FROM chunks')]
    assert store.index()['unchanged'] == 3 and len(library.image_calls) == 3
    # Simulate migration of an old index without requested-size metadata.
    with store.db: store.db.execute('DELETE FROM thumbnail_specs')
    assert store.index()['unchanged'] == 3 and len(library.image_calls) == 6
    assert store.index()['unchanged'] == 3 and len(library.image_calls) == 6
    assert engine.calls == 3 and before == [tuple(r) for r in store.db.execute('SELECT * FROM chunks')]
    store.close()


def test_permanent_skip_survives_restart_and_changed_file_retries(tmp_path, monkeypatch):
    config = folder_config(tmp_path); path = config.roots[0]/'empty.txt'; path.write_text('')
    from tinysearch import store as module
    original = module.extract; calls=[]
    monkeypatch.setattr(module, 'extract', lambda path:calls.append(path) or original(path))
    store = Store(config, FakeEngine()); assert store.index()['errors']; store.close()
    store = Store(config, FakeEngine())
    second = store.index()
    assert second['cached_skips'] == 1 and not second['errors'] and len(calls) == 1
    assert second['skipped'][0]['category'] == 'no_text'
    path.write_text('Now there is searchable content.')
    assert store.index()['added'] == 1 and len(calls) == 2
    assert not store.db.execute('SELECT * FROM index_failures').fetchall()
    store.close()


def test_limit_changes_invalidate_cached_skip(tmp_path):
    config = replace(folder_config(tmp_path), max_file_mb=1, max_document_chars=2*1024**2)
    (config.roots[0]/'large.txt').write_text('x'*(1024**2+1))
    store = Store(config, FakeEngine())
    assert store.index()['errors']
    assert store.index()['cached_skips'] == 1
    store.config = replace(config, max_file_mb=2)
    assert store.index()['added'] == 1
    store.close()


def test_transient_backoff_and_manual_retry_do_not_discard_existing_entry(tmp_path, monkeypatch):
    config = folder_config(tmp_path); path = config.roots[0]/'note.txt'; path.write_text('original')
    engine = FakeEngine(); store = Store(config, engine); store.index()
    original = bytes(store.db.execute('SELECT vector FROM chunks').fetchone()[0])
    path.write_text('changed content')
    attempts=[]
    monkeypatch.setattr(engine, 'document', lambda text:attempts.append(text) or (_ for _ in ()).throw(RuntimeError('Temporary encoder failure')))
    assert store.index()['errors']
    assert store.index()['retry_deferred'] == 1 and len(attempts) == 1
    assert bytes(store.db.execute('SELECT vector FROM chunks').fetchone()[0]) == original
    assert store.index(retry_skipped=True)['errors'] and len(attempts) == 2
    row = store.db.execute('SELECT * FROM index_failures').fetchone()
    assert row['attempts'] == 2 and row['retry_at'] > time.time()+3*3600
    monkeypatch.setattr(engine, 'document', FakeEngine().document)
    assert store.index(retry_skipped=True)['updated'] == 1
    assert not store.db.execute('SELECT * FROM index_failures').fetchall()
    store.close()


def test_expired_transient_failure_is_retried(tmp_path, monkeypatch):
    config = folder_config(tmp_path); (config.roots[0]/'note.txt').write_text('content')
    engine = FakeEngine(); store = Store(config, engine)
    monkeypatch.setattr(engine, 'document', lambda text:(_ for _ in ()).throw(RuntimeError('Temporary failure')))
    assert store.index()['errors']
    with store.db: store.db.execute('UPDATE index_failures SET retry_at=0')
    monkeypatch.setattr(engine, 'document', FakeEngine().document)
    assert store.index()['added'] == 1
    store.close()


def test_failure_cleanup_respects_exclusions_and_unavailable_roots(tmp_path):
    config = folder_config(tmp_path); excluded = config.roots[0]/'excluded'; excluded.mkdir()
    (excluded/'empty.txt').write_text('')
    store = Store(config, FakeEngine()); store.index()
    root = config.roots[0]; root.rename(root.with_name('unavailable'))
    assert store.index()['errors']
    assert store.db.execute('SELECT count(*) FROM index_failures').fetchone()[0] == 1
    root.with_name('unavailable').rename(root)
    store.config = replace(config, excluded_paths=(excluded,)); store.prune_excluded()
    assert store.db.execute('SELECT count(*) FROM index_failures').fetchone()[0] == 0
    store.close()


def test_exclusion_during_failed_work_does_not_recreate_failure_cache(tmp_path, monkeypatch):
    config = folder_config(tmp_path); (config.roots[0]/'note.txt').write_text('content')
    engine = FakeEngine(); store = Store(config, engine)
    def exclude(text):
        store.config = replace(config, excluded_paths=config.roots)
        store.prune_excluded()
        raise RuntimeError('Failed after folder exclusion')
    monkeypatch.setattr(engine, 'document', exclude)
    result = store.index()
    assert result['excluded'] == 1 and not result['errors']
    assert not store.db.execute('SELECT * FROM index_failures').fetchall()
    store.close()


def test_photos_size_skip_avoids_export_and_limit_change_retries(config, videos, monkeypatch):
    config = replace(config, backend='mlx', enable_images=False, enable_video=True)
    original = videos.video; attempts=[]
    def fail(self, identifier, **values):
        attempts.append(identifier); raise RuntimeError('Video exceeds the configured file size limit.')
    monkeypatch.setattr(videos, 'video', fail)
    store = Store(config, VideoEngine())
    assert store.index()['errors']
    assert store.index()['cached_skips'] == 1 and len(attempts) == 1
    store.config = replace(config, max_media_file_mb=2048)
    monkeypatch.setattr(videos, 'video', original)
    assert store.index()['added'] == 1 and videos.original.exists()
    store.close()


def test_photos_transient_retry_is_deferred_but_edited_asset_retries(config, library, monkeypatch):
    original = library.image; attempts=[]
    identifier = library.rows[0]['id']
    def image(self, asset, **values):
        if asset == identifier:
            attempts.append(asset); raise RuntimeError('Temporary iCloud failure')
        return original(self, asset, **values)
    monkeypatch.setattr(library, 'image', image)
    store = Store(config, Engine())
    assert store.index()['added'] == 2
    assert store.index()['retry_deferred'] == 1 and len(attempts) == 1
    library.rows[0] = {**library.rows[0], 'modified':2}
    monkeypatch.setattr(library, 'image', original)
    assert store.index()['added'] == 1
    store.close()


@pytest.mark.parametrize('backend', ['mlx', 'coreml'])
def test_partial_photos_audio_backoff_preserves_scene_vectors(config, videos, monkeypatch, backend):
    from tinysearch import media
    config = replace(config, backend=backend, enable_images=False, enable_video=True, enable_audio=True, audio_segment_seconds=10)
    attempts=[]
    monkeypatch.setattr(media, 'audio_samples', lambda *a:attempts.append(a[1]) or (_ for _ in ()).throw(ValueError('Invalid audio')))
    engine = VideoEngine(); store = Store(config, engine); store.index()
    before = [tuple(r) for r in store.db.execute('SELECT * FROM chunks')]
    result = store.index()
    assert result['audio_retries_deferred'] == 1 and result['partial_videos'] == 1
    assert len(videos.video_calls) == 1 and len(attempts) == 3 and engine.video_calls == 3
    assert before == [tuple(r) for r in store.db.execute('SELECT * FROM chunks')]
    assert store.search('scene', 'video')['results']
    store.close()


def test_filesystem_video_keeps_visual_scenes_and_only_retries_missing_audio(tmp_path, monkeypatch):
    from PIL import Image
    from tinysearch import media
    config = replace(folder_config(tmp_path), backend='mlx', enable_video=True, enable_audio=True, audio_segment_seconds=10)
    path = config.roots[0]/'clip.mov'; path.write_bytes(b'synthetic movie')
    monkeypatch.setattr(media, 'probe', lambda *a:media.MediaInfo(23, True, True))
    monkeypatch.setattr(media, 'video_frames', lambda *a:[Image.new('RGB', (160,80), 'blue')]*3)
    monkeypatch.setattr(media, 'audio_samples', lambda *a:(_ for _ in ()).throw(ValueError('Invalid audio')))
    engine = VideoEngine(); store = Store(config, engine)
    assert store.index()['partial_videos'] == 1 and engine.video_calls == 3
    result = store.index()
    assert result['audio_retries_deferred'] == 1 and engine.video_calls == 3
    assert store.search('scene', 'video')['results']
    monkeypatch.setattr(media, 'audio_samples', lambda *a:np.ones(16000, dtype=np.float32))
    assert store.index(retry_skipped=True)['updated'] == 1 and engine.video_calls == 3 and engine.audio_calls == 3
    assert not store.db.execute('SELECT digest FROM files').fetchone()[0].startswith(PHOTOS_AUDIO_PENDING)
    assert path.read_bytes() == b'synthetic movie'
    store.close()


def test_manual_retry_api_bypasses_skip_without_rebuilding_successful_files(tmp_path):
    config = folder_config(tmp_path)
    (config.roots[0]/'empty.txt').write_text(''); (config.roots[0]/'ready.txt').write_text('searchable')
    engine = FakeEngine()
    with TestClient(create_app(config, engine)) as client:
        assert client.post('/v1/index').json()['added'] == 1
        assert client.post('/v1/index', json={}).json()['cached_skips'] == 1
        result = client.post('/v1/index', json={'retry_skipped':True}).json()
        assert len(result['errors']) == 1 and not result['cached_skips'] and result['unchanged'] == 1
        assert engine.calls == 1


def test_async_retry_request_reaches_the_indexer(tmp_path):
    config = replace(folder_config(tmp_path), storage_path=tmp_path)
    (config.roots[0]/'empty.txt').write_text('')
    with TestClient(create_app(config, FakeEngine())) as client:
        client.post('/v1/index')
        assert client.post('/v1/index/start', json={'retry_skipped':True}).status_code == 200
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            state = client.get('/v1/settings').json()['job']['state']
            if state in {'complete', 'error'}: break
            time.sleep(.01)
        assert state == 'complete'
        summary = client.get('/v1/status').json()['last_index_summary']
        assert summary['errors'] == 1 and summary['cached_skips'] == 0
        report = client.get('/v1/settings').json()['job']['report_path']
    with TestClient(create_app(config, FakeEngine())) as client:
        assert client.get('/v1/settings').json()['job']['report_path'] == report
