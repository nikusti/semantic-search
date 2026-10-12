"""Persist indexing failures without confusing them with searchable entries."""
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import time


class UnsupportedContent(ValueError):
    """A stable limitation; retry when the source or relevant policy changes."""
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


@lru_cache(maxsize=None)
def dependency(name):
    try: return version(name)
    except PackageNotFoundError: return None


def policy(config):
    # Bump the parser revision when extraction/decoding support changes. UI-only
    # releases should not retry every unsupported document in the library.
    values = {name: getattr(config, name) for name in (
        'backend', 'model_revision', 'pipeline_revision', 'dimensions',
        'max_file_mb', 'max_media_file_mb', 'max_media_seconds',
        'max_document_chars', 'max_document_chunks', 'chunk_tokens',
        'chunk_overlap', 'max_sequence_tokens', 'enable_documents',
        'enable_images', 'enable_video', 'enable_audio', 'photos_allow_network',
        'video_segment_seconds', 'video_frames', 'audio_segment_seconds',
        'audio_overlap_seconds', 'ffmpeg', 'pdf_renderer')}
    values.update(parser_revision=1, dependencies={name: dependency(name) for name in ('pypdf', 'cryptography', 'av', 'pillow')})
    return sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class FailureCache:
    def __init__(self, db, lock):
        self.db, self.lock = db, lock
        self.db.execute('''CREATE TABLE IF NOT EXISTS index_failures (
            path TEXT NOT NULL, scope TEXT NOT NULL, root TEXT NOT NULL,
            fingerprint TEXT NOT NULL, policy TEXT NOT NULL, reason TEXT NOT NULL,
            category TEXT NOT NULL, attempts INTEGER NOT NULL,
            retry_at REAL, last_attempt REAL NOT NULL,
            PRIMARY KEY(path, scope))''')

    def lookup(self, path, scope, fingerprint, policy_key, force=False):
        if force: return None
        with self.lock:
            row = self.db.execute('SELECT * FROM index_failures WHERE path=? AND scope=?', (str(path), scope)).fetchone()
        if row and row['fingerprint'] == fingerprint and row['policy'] == policy_key:
            if row['retry_at'] is None or row['retry_at'] > time.time(): return dict(row)
        return None

    def record(self, path, scope, root, fingerprint, policy_key, error):
        with self.lock, self.db:
            old = self.db.execute('SELECT * FROM index_failures WHERE path=? AND scope=?', (str(path), scope)).fetchone()
            attempts = old['attempts'] + 1 if old and (old['fingerprint'], old['policy']) == (fingerprint, policy_key) else 1
            permanent = isinstance(error, UnsupportedContent)
            now = time.time()
            retry_at = None if permanent else now + (3600, 4*3600, 24*3600)[min(attempts-1, 2)]
            self.db.execute('INSERT OR REPLACE INTO index_failures VALUES (?,?,?,?,?,?,?,?,?,?)',
                (str(path), scope, str(root), fingerprint, policy_key, str(error)[:2048],
                 error.code if permanent else 'temporary', attempts, retry_at, now))

    def clear(self, path, scope):
        with self.lock:
            if self.db.execute('SELECT 1 FROM index_failures WHERE path=? AND scope=?', (str(path), scope)).fetchone():
                with self.db:
                    self.db.execute('DELETE FROM index_failures WHERE path=? AND scope=?', (str(path), scope))

    def prune(self, root, seen):
        with self.lock, self.db:
            paths = [(r[0],) for r in self.db.execute('SELECT DISTINCT path FROM index_failures WHERE root=?', (str(root),)) if r[0] not in seen]
            self.db.executemany('DELETE FROM index_failures WHERE path=?', paths)


def summarize_skip(summary, row):
    key = 'audio_retries_deferred' if row['scope'] == 'audio' else 'cached_skips' if row['retry_at'] is None else 'retry_deferred'
    summary[key] += 1
    summary['skipped'].append({key: row[key] for key in ('path', 'scope', 'reason', 'category', 'retry_at')})
