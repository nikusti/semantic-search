from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import re
import stat
import sys

from fastapi.testclient import TestClient
import pytest

from test_store import FakeEngine, config
from tinysearch.auth import TOKEN_HEADER, NoRedirects, load_or_create_token, local_urlopen, read_token, token_path
from tinysearch.server import create_app


def test_token_is_private_persistent_and_specific_to_storage(config, tmp_path):
    config = replace(config, storage_path=tmp_path/'storage')
    first = load_or_create_token(config)
    path = token_path(config)
    assert path == config.storage_path/'auth-token'
    assert re.fullmatch('[0-9a-f]{64}', first)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_or_create_token(config) == first
    assert load_or_create_token(replace(config, storage_path=tmp_path/'other')) != first
    path.chmod(0o644)
    assert read_token(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_concurrent_starters_share_one_complete_token(config):
    with ThreadPoolExecutor(max_workers=12) as pool:
        tokens = list(pool.map(lambda _: load_or_create_token(config), range(30)))
    assert len(set(tokens)) == 1
    assert read_token(token_path(config)) == tokens[0]
    assert not list(token_path(config).parent.glob('.auth-token-*'))


@pytest.mark.parametrize('contents', ['', 'short', 'g'*64, 'a'*64+'\ntrailing'])
def test_invalid_existing_token_fails_closed(config, contents):
    path = token_path(config)
    path.write_text(contents)
    with pytest.raises(ValueError):
        load_or_create_token(config)
    assert path.read_text() == contents


def test_token_symlink_and_non_regular_file_are_rejected(config, tmp_path):
    target = tmp_path/'target'
    target.write_text('a'*64)
    target.chmod(0o644)
    token_path(config).symlink_to(target)
    with pytest.raises(OSError):
        load_or_create_token(config)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    token_path(config).unlink()
    os.mkfifo(token_path(config))
    with pytest.raises(ValueError):
        load_or_create_token(config)


@pytest.mark.parametrize('header', [None, '', 'a'*64])
def test_every_non_discovery_route_requires_authentication(config, header):
    app = create_app(config, FakeEngine())
    with TestClient(app) as client:
        headers = {} if header is None else {TOKEN_HEADER: header}
        for route in app.routes:
            for method in route.methods or ():
                if method == 'GET' and route.path == '/v1/info':
                    continue
                response = client.request(method, route.path, headers=headers)
                assert response.status_code == 401, (method, route.path, response.text)
        assert client.post('/v1/info', headers=headers).status_code == 401
        assert client.get('/future-route', headers=headers).status_code == 401


def test_valid_token_unlocks_api_but_does_not_bypass_host_or_origin_checks(config):
    app = create_app(config, FakeEngine())
    token = read_token(token_path(config))
    with TestClient(app) as client:
        assert client.get('/v1/status').status_code == 401
        assert client.get('/v1/status', headers={TOKEN_HEADER: token}).status_code == 200
        assert client.post('/v1/search', json={'query': 'example'}, headers={TOKEN_HEADER: token}).status_code == 200
        for endpoint in ('/v1/info', '/v1/status'):
            assert client.get(endpoint, headers={TOKEN_HEADER: token, 'Origin': 'https://example.com'}).status_code == 403
            assert client.get(endpoint, headers={TOKEN_HEADER: token, 'Host': 'attacker.example'}).status_code == 403
        # Deliberately minimal public discovery supports pre-auth engine updates.
        assert set(client.get('/v1/info').json()) == {'application', 'version', 'protocol'}


def test_restarting_app_preserves_authenticated_access(config):
    first = create_app(config, FakeEngine())
    token = read_token(first.state.auth_token_path)
    second = create_app(config, FakeEngine())
    with TestClient(second) as client:
        assert client.get('/v1/status', headers={TOKEN_HEADER: token}).status_code == 200


def test_cli_reads_installation_token_without_creating_it(config, monkeypatch, capsys):
    from tinysearch import cli
    config = replace(config, source_path=config.database.parent/'config.toml')
    config.save()
    monkeypatch.setattr(sys, 'argv', ['tinysearch', '--config', str(config.source_path), 'status'])
    calls = []

    def request(req, **kwargs):
        calls.append(req)
        from io import BytesIO
        return BytesIO(json.dumps({'files': 0}).encode())

    monkeypatch.setattr(cli, 'local_urlopen', request)
    with pytest.raises(SystemExit) as missing:
        cli.main()
    assert missing.value.code == 1
    assert not calls and not token_path(config).exists()
    token = load_or_create_token(config)
    cli.main()
    assert len(calls) == 1
    assert calls[0].get_header(TOKEN_HEADER.capitalize()) == token
    assert json.loads(capsys.readouterr().out) == {'files': 0}


def test_authenticated_python_requests_refuse_redirects_and_disable_proxies(monkeypatch):
    from email.message import Message
    from io import BytesIO
    import urllib.request
    from urllib.error import HTTPError
    request = urllib.request.Request('http://127.0.0.1:8766/v1/status', headers={TOKEN_HEADER: 'secret'})
    headers = Message()
    headers['location'] = 'https://example.com/collect'
    # Exercise urllib's real redirect dispatcher; it must raise instead of
    # constructing the follow-up request that would forward the custom header.
    with pytest.raises(HTTPError) as redirect:
        NoRedirects().http_error_302(request, BytesIO(), 302, 'Found', headers)
    assert redirect.value.code == 302
    calls = []
    class Opener:
        def open(self, req, **kwargs):
            calls.append((req, kwargs))
            return 'response'
    def build_opener(*handlers):
        assert any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers)
        assert any(isinstance(handler, NoRedirects) for handler in handlers)
        return Opener()
    monkeypatch.setattr(urllib.request, 'build_opener', build_opener)
    assert local_urlopen(request, timeout=5) == 'response'
    assert calls == [(request, {'timeout': 5})]
