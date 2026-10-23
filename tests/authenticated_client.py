"""Use the same on-disk credential as real launcher and CLI API clients."""
from fastapi.testclient import TestClient as BaseTestClient
from tinysearch.auth import TOKEN_HEADER, read_token


class TestClient(BaseTestClient):
    __test__ = False

    def __init__(self, app, **kwargs):
        headers = {TOKEN_HEADER: read_token(app.state.auth_token_path), **kwargs.pop('headers', {})}
        super().__init__(app, headers=headers, **kwargs)
