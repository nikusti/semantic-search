from authenticated_client import TestClient
from test_store import FakeEngine
from tinysearch.config import Config
from tinysearch.server import create_app


def test_packaged_update_refuses_active_jobs_and_preserves_the_index(tmp_path):
    root = tmp_path / 'documents'
    root.mkdir()
    (root / 'sample.txt').write_text('A synthetic project migration checklist')
    config = Config((root,), tmp_path / 'model', 'pinned', tmp_path / 'index.sqlite3', dimensions=128)
    stops = []
    app = create_app(config, FakeEngine(), request_stop=lambda: stops.append(True))
    with TestClient(app) as client:
        assert client.post('/v1/index').status_code == 200
        before = config.database.read_bytes()
        for state in ('downloading', 'applying', 'indexing', 'cancelling'):
            app.state.settings.job['state'] = state
            assert client.post('/v1/engine/stop').status_code == 409
            assert not stops
        app.state.settings.job['state'] = 'complete'
        assert client.post('/v1/engine/stop', headers={'Origin': 'https://example.com'}).status_code == 403
        assert not stops
        assert client.post('/v1/engine/stop').json() == {'stopping': True}
        assert stops == [True]
        assert config.database.read_bytes() == before


def test_external_service_does_not_expose_process_termination(tmp_path):
    config = Config((), tmp_path / 'model', 'pinned', tmp_path / 'index.sqlite3', dimensions=128)
    with TestClient(create_app(config, FakeEngine())) as client:
        assert client.post('/v1/engine/stop').status_code == 409
