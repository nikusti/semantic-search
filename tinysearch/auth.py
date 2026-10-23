"""A local credential shared by the engine and its launcher/CLI clients."""
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import urllib.error
import urllib.request


TOKEN_HEADER = 'X-Semantic-Search-Token'
TOKEN_FILENAME = 'auth-token'


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib otherwise forwards custom headers to the redirect target.
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def local_urlopen(request, *, timeout):
    # A loopback credential must never reach an environment-configured proxy or
    # be replayed to another host by urllib's automatic redirect handling.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
    return opener.open(request, timeout=timeout)


def token_path(config):
    # Packaged services always set storage_path. Keep explicit development/test
    # configurations isolated alongside their own configuration or database.
    storage = config.storage_path or (config.source_path.parent if config.source_path else config.database.parent)
    return Path(storage) / TOKEN_FILENAME


def read_token(path):
    """Read a complete credential, refusing symlinks and non-ordinary files."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Engine authentication token must be a regular file owned by this user')
        os.fchmod(source.fileno(), 0o600)
        value = source.read(66)
    if not re.fullmatch(rb'[0-9a-f]{64}\n?', value):
        raise ValueError('Engine authentication token is invalid')
    return value.decode('ascii').strip()


def load_or_create_token(config):
    """Publish a complete 256-bit token once, even when engines start together."""
    path = token_path(config)
    try:
        return read_token(path)
    except FileNotFoundError:
        pass
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.auth-token-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write((secrets.token_hex(32) + '\n').encode('ascii'))
            output.flush()
            os.fsync(output.fileno())
        try:
            # A hard link atomically publishes the fully written file without
            # replacing another concurrent starter's credential.
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary)
    return read_token(path)
