from dataclasses import replace
import hashlib
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from tinysearch.config import Config
from tinysearch.engine import normalize
from tinysearch.server import create_app
from tinysearch.store import Store


class FakeEngine:
    """Deterministic vectors for storage/API tests, never used by the application."""
    identity = "test-engine:128:v1"
    dimensions = 128
    calls = 0

    def document(self, text):
        self.calls += 1
        return normalize(list(hashlib.sha256(text.encode()).digest()) * 4, 128)

    image = document
    query = document

    def chunks(self, text):
        yield text


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    return Config((root,), tmp_path / "model", "test", tmp_path / "index.sqlite3", dimensions=128)


def test_incremental_move_delete_and_persistence(config):
    root = config.roots[0]
    original = root / "a.txt"
    original.write_text("first content")
    engine = FakeEngine()
    store = Store(config, engine)
    assert store.index()["indexed"] == 1
    assert store.status()["progress"]["processed"] == 1
    assert store.status()["progress"]["finished_at"] is not None
    assert store.index()["unchanged"] == 1
    assert engine.calls == 1
    original.write_text("changed content")
    assert store.index()["indexed"] == 1
    original.rename(root / "renamed.txt")
    result = store.index()
    assert result["reused"] == result["deleted"] == 1
    assert engine.calls == 2
    store.close()
    engine = FakeEngine()
    store = Store(config, engine)
    assert store.index()["unchanged"] == 1
    assert engine.calls == 0
    (root / "renamed.txt").unlink()
    assert store.search("query")["results"] == []
    assert store.index()["deleted"] == 1
    store.close()


def test_missing_root_does_not_prune(config):
    root = config.roots[0]
    (root / "a.txt").write_text("important content")
    store = Store(config, FakeEngine())
    store.index()
    root.rename(root.with_name("temporarily-unmounted"))
    assert store.index()["errors"]
    assert store.status()["files"] == 1
    store.close()


def test_status_reports_disk_usage_including_uncheckpointed_index(config):
    (config.roots[0] / "document.txt").write_text("indexed content")
    store = Store(config, FakeEngine())
    store.index()
    status = store.status()
    assert status['files'] == 1 and status['chunks'] == 1
    # The main SQLite file alone is incomplete while recent rows are in WAL.
    assert Path(str(config.database) + '-wal').stat().st_size > 0
    assert status['database_bytes'] > config.database.stat().st_size
    store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert store.status()['database_bytes'] >= config.database.stat().st_size > 0
    store.close()


def test_removed_root_is_excluded_after_restart(config):
    (config.roots[0] / "a.txt").write_text("old configured root")
    store = Store(config, FakeEngine())
    store.index()
    store.close()
    store = Store(replace(config, roots=()), FakeEngine())
    assert store.status()["files"] == 0
    assert store.status()["chunks"] == 0
    store.close()


def test_ignored_dependencies_and_symlinks(config):
    root = config.roots[0]
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dependency.js").write_text("ignored")
    (root / "export.csv").write_text("large tables are out of scope")
    outside = root.parent / "outside.txt"
    outside.write_text("do not index")
    (root / "link.txt").symlink_to(outside)
    (root / ".hidden.txt").write_text("ignored")
    store = Store(config, FakeEngine())
    assert store.index()["indexed"] == 0
    store.close()


def test_failed_update_keeps_previous_transaction(config):
    path = config.roots[0] / "a.txt"
    path.write_text("previous")
    store = Store(config, FakeEngine())
    store.index()
    path.write_bytes(b"\xff\x00invalid utf8")
    assert store.index()["errors"]
    assert store.status()["files"] == 1
    store.close()


def test_model_change_rejected(config):
    Store(config, FakeEngine()).close()
    engine = FakeEngine()
    engine.identity = "other-model"
    with pytest.raises(RuntimeError, match="another model"):
        Store(config, engine)

def test_batched_documents_preserve_all_chunks_atomically(config):
    path=config.roots[0]/'a.txt';path.write_text('content')
    engine=FakeEngine()
    engine.chunks=lambda text:iter([text+str(i) for i in range(10)])
    batch_sizes=[]
    def batch(texts):
        batch_sizes.append(len(texts))
        return np.stack([engine.document(t) for t in texts])
    engine.document_batch=batch
    store=Store(replace(config,backend='mlx'),engine)
    assert store.index()['indexed']==1
    assert store.status()['chunks']==10 and batch_sizes==[8,2]
    path.write_text('changed content')
    engine.document_batch=lambda texts:[]
    assert store.index()['errors']
    assert store.status()['chunks']==10
    store.close()


def test_filters_group_chunks_and_limits(config):
    (config.roots[0] / "a.txt").write_text("two chunks")
    (config.roots[0] / "b.txt").write_text("second file")
    engine = FakeEngine()
    engine.chunks = lambda text: iter([text, text + " continuation"])
    store = Store(config, engine)
    store.index()
    assert store.status()["chunks"] == 4
    assert len(store.search("query")["results"]) == 2
    assert len(store.search("query", limit=1)["results"]) == 1
    assert not store.search("query", "image")["results"]
    store.close()


def test_api_validation_and_local_origin(config):
    with TestClient(create_app(config, FakeEngine())) as client:
        assert client.get("/v1/status").status_code == 200
        for payload in [{"query": ""}, {"query": " "}, {"query": "hi", "kind": "unknown"},
                        {"query": "hi", "limit": 0}]:
            assert client.post("/v1/search", json=payload).status_code == 422
        assert client.post("/v1/index").status_code == 200
        assert client.post("/v1/search", json={"query": "hi"},
                           headers={"Origin": "https://example.com"}).status_code == 403
        assert client.get("/v1/status", headers={"Host": "evil.example"}).status_code == 403


def test_nonfinite_or_zero_embeddings_rejected():
    for vector in [[0, 0, 0, 0], [1, 2], [1, 2, 3, np.nan], [1, 2, 3, np.inf]]:
        with pytest.raises(ValueError):
            normalize(vector, 4)


def test_large_document_is_skipped_before_inference(config):
    path = config.roots[0] / "large.txt"
    path.write_text("a" * 30)
    engine = FakeEngine()
    store = Store(replace(config, max_document_chars=20), engine)
    summary = store.index()
    assert summary["indexed"] == 0
    assert "skipped, not truncated" in summary["errors"][0]
    assert engine.calls == 0
    store.close()


def test_thumbnail_is_persistent_and_sent_as_inline_image(config):
    from PIL import Image
    path = config.roots[0] / "picture.png"
    Image.new("RGB", (32, 32), "red").save(path)
    engine = FakeEngine()
    engine.image = lambda path: engine.document(str(path))
    store = Store(config, engine)
    assert store.index()["indexed"] == 1
    store.close()
    store = Store(config, engine)
    assert store.index()["unchanged"] == 1
    result = store.search("red", "image")["results"][0]
    assert result["thumbnail"].startswith("data:image/jpeg;base64,")
    store.close()


def test_pdf_preview_cache_persists_and_refreshes_after_change(config, monkeypatch):
    import base64
    from io import BytesIO
    from PIL import Image
    from reportlab.pdfgen import canvas
    path = config.roots[0] / "document.pdf"
    def write_pdf(text):
        pdf = canvas.Canvas(str(path))
        pdf.drawString(40, 700, text)
        pdf.save()
    write_pdf("first version")
    jpeg = BytesIO()
    Image.new("RGB", (20, 30), "white").save(jpeg, format="JPEG")
    renders = []
    def render(path, renderer):
        renders.append(path)
        return jpeg.getvalue(), 1
    monkeypatch.setattr("tinysearch.store.render_pdf", render)
    store = Store(config, FakeEngine())
    store.index()
    result = store.preview(str(path))
    assert result["page"] == 1 and result["pages"] == 1
    assert base64.b64decode(result["preview"].split(",", 1)[1]) == jpeg.getvalue()
    store.close()
    store = Store(config, FakeEngine())
    store.preview(str(path))
    assert len(renders) == 1
    write_pdf("a modified version with different length")
    store.preview(str(path))
    assert len(renders) == 2
    path.unlink()
    store.index()
    assert store.db.execute("SELECT count(*) FROM pdf_previews").fetchone()[0] == 0
    store.close()


def test_preview_api_rejects_unindexed_files_and_changed_symlinks(config, monkeypatch):
    from reportlab.pdfgen import canvas
    path = config.roots[0] / "document.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 700, "example")
    pdf.save()
    other = config.roots[0].parent / "private.pdf"
    other.write_bytes(path.read_bytes())
    def never_render(*args):
        pytest.fail("Unauthorized file reached the renderer")
    monkeypatch.setattr("tinysearch.store.render_pdf", never_render)
    with TestClient(create_app(config, FakeEngine())) as client:
        assert client.post("/v1/preview", json={"path": str(path)}).status_code == 404
        client.post("/v1/index")
        assert client.post("/v1/preview", json={"path": str(other)}).status_code == 404
        path.unlink()
        path.symlink_to(other)
        assert client.post("/v1/preview", json={"path": str(path)}).status_code == 404


def test_refresh_counts_detect_content_changes_and_reuse_metadata_only_updates(config):
    root = config.roots[0]
    keep, changed, removed = (root / name for name in ('keep.txt', 'changed.txt', 'removed.txt'))
    keep.write_text('stable content'); changed.write_text('version one'); removed.write_text('remove me')
    engine = FakeEngine(); store = Store(config, engine)
    assert store.index()['added'] == 3
    prior = changed.stat()
    changed.write_text('version two')  # Same byte count, different mtime/content.
    import os
    os.utime(changed, ns=(prior.st_atime_ns, prior.st_mtime_ns + 1_000_000))
    (root / 'new.txt').write_text('newly added content'); removed.unlink()
    result = store.index()
    assert {key: result[key] for key in ('added', 'updated', 'unchanged', 'deleted')} == dict(added=1, updated=1, unchanged=1, deleted=1)
    assert engine.calls == 5
    assert store.db.execute('SELECT snippet FROM chunks WHERE path=?', (str(changed),)).fetchone()[0] == 'version two'
    stamp = changed.stat()
    os.utime(changed, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
    touched = store.index()
    assert touched['updated'] == touched['reused'] == 1 and engine.calls == 5
    last = store.status()['last_index_summary']
    assert last['unchanged'] == 2 and last['errors'] == 0 and last['finished_at'] > 0
    store.close(); reopened = Store(config, FakeEngine())
    assert reopened.status()['last_index_summary'] == last
    reopened.close()


def test_background_refresh_summary_and_empty_root_validation(config):
    import time
    config = replace(config, storage_path=config.database.parent)
    (config.roots[0] / 'sample.txt').write_text('new document')
    with TestClient(create_app(config, FakeEngine())) as client:
        assert client.post('/v1/index/start').status_code == 200
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = client.get('/v1/settings').json()['job']
            if job['state'] in {'complete', 'error'}: break
            time.sleep(.01)
        assert job['state'] == 'complete', job
        last = client.get('/v1/status').json()['last_index_summary']
        assert last['added'] == 1 and last['updated'] == last['errors'] == 0
    with TestClient(create_app(replace(config, roots=(), database=config.database.with_name('empty.sqlite3')), FakeEngine())) as client:
        response = client.post('/v1/index/start')
        assert response.status_code == 422 and 'Choose folders' in response.json()['detail']
