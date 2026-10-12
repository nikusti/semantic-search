"""Read-only PhotoKit bridge. Temporary representations are removed on close."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import quote

ROOT = 'apple-photos'
PREFIX = 'photos://asset/'

def asset_path(identifier):
    return PREFIX + quote(identifier, safe='')

def helper_path():
    import sys
    candidates = [Path(sys.executable).resolve().parents[2]/'photos'/'Semantic Search Photos.app',
                  Path(__file__).resolve().parents[1]/'.build/photos/Semantic Search Photos.app']
    for path in candidates:
        if (path/'Contents/MacOS/semantic-search-photos').is_file():
            return path
    raise RuntimeError('The Photos helper is unavailable. Install a release with Apple Photos support.')

class PhotoLibrary:
    def __init__(self, cancelled=None, progress=None):
        self.cancelled = cancelled or (lambda: False)
        self.progress = progress or (lambda value: None)
        self.folder = tempfile.TemporaryDirectory(prefix='semantic-search-photos-')
        self.path = Path(self.folder.name)
        os.chmod(self.path, 0o700)
        self.child = None
        try:
            self.child = subprocess.Popen(['/usr/bin/open', '-W', '-n', '-a', str(helper_path()),
                                           '--args', '--session', str(self.path)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._wait(self.path/'ready.json', 30)
        except Exception:
            self.close()
            raise

    def _wait(self, response, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancelled():
                (self.path/'cancel').touch()
                raise RuntimeError('Photos import cancelled')
            if response.is_file():
                result = json.loads(response.read_text())
                if result.get('error'):
                    raise RuntimeError(result['error'])
                return result
            if self.child and self.child.poll() is not None:
                raise RuntimeError('The Photos helper stopped. Check macOS Photos permissions.')
            progress = self.path/'progress.json'
            if progress.is_file():
                try: self.progress(json.loads(progress.read_text()))
                except (OSError, ValueError): pass
            time.sleep(.05)
        raise RuntimeError('Photos operation timed out. Retry after allowing Photos access.')

    def request(self, op, timeout=180, **values):
        for name in ('response.json', 'progress.json', 'cancel', 'image.jpg', 'video.mov'):
            (self.path/name).unlink(missing_ok=True)
        temporary = self.path/'request.tmp'
        temporary.write_text(json.dumps({'op': op, **values}))
        temporary.replace(self.path/'request.json')
        return self._wait(self.path/'response.json', timeout)

    def assets(self):
        return self.request('list')['assets']

    def metadata(self, identifier):
        return self.request('metadata', timeout=15, id=identifier)

    def copy_photo(self, identifier, network=False):
        result = self.request('copy_image', id=identifier, network=network)
        if result.get('deferred'):
            raise RuntimeError('Photo is in iCloud; allow downloads or open it in Photos.')
        return result

    def image(self, identifier, network=False, edge=1024, timeout=180):
        result = self.request('image', timeout=timeout, id=identifier, network=network, edge=edge)
        self.image_name = result.get('name')
        self.image_modified = result.get('modified')
        return None if result.get('deferred') else self.path/'image.jpg'

    def video(self, identifier, network=False, max_bytes=1024**3, timeout=330):
        result = self.request('video', timeout=timeout, id=identifier, network=network, max_bytes=max_bytes)
        self.video_name = result.get('name')
        self.video_modified = result.get('modified')
        return None if result.get('deferred') else self.path/'video.mov'

    def video_frame(self, identifier, seconds=0, network=False, timeout=10):
        result = self.request('video_frame', timeout=timeout, id=identifier, network=network, seconds=seconds, edge=1024)
        return None if result.get('deferred') else self.path/'image.jpg'

    def close(self):
        if self.child:
            (self.path/'cancel').touch()
            (self.path/'stop').touch()
            try: self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # open is a launcher, not the app. The app watches stop itself.
                self.child.terminate()
                self.child.wait(timeout=5)
        self.folder.cleanup()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
