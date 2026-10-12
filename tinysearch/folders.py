"""Folder selection is independent of the model's saved vector space."""
from pathlib import Path
import hashlib
import os


def covers(path, roots):
    path = Path(path)
    return path.is_absolute() and any(path.is_relative_to(root) for root in roots)


def selected_roots(values, previous=()):
    roots = set()
    for value in values:
        path = Path(str(value).strip()).expanduser()
        if not path.is_absolute():
            raise ValueError('Choose an absolute folder path or ~/folder')
        path = path.resolve()
        # An unavailable saved source can still be retained or removed.
        if path not in previous:
            if not path.is_dir():
                raise ValueError('Each added folder must exist and be readable')
            if not os.access(path, os.R_OK | os.X_OK):
                raise ValueError('Allow access to the selected folder in macOS Privacy settings')
        roots.add(path)
    if len(roots) > 100:
        raise ValueError('Choose at most 100 search folders')
    return tuple(sorted(roots))


def purge_folders(db, removed, remaining, database, verify_identity=True):
    """Forget only removed sources; never delete files or remaining nested sources."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'files', 'chunks', 'meta'} <= tables:
        return 0
    identity = db.execute("SELECT value FROM meta WHERE key='engine'").fetchone()
    if not identity or (verify_identity and not identity[0].startswith('embeddinggemma-2:')):
        return 0
    def forget(path):
        return covers(path, removed) and not covers(path, remaining)
    paths = [(r[0],) for r in db.execute('SELECT path FROM files') if forget(r[0])]
    segments = []
    columns = {r[1] for r in db.execute('PRAGMA table_info(chunks)')}
    if {'start_seconds', 'end_seconds'} <= columns:
        for path, in paths:
            for row in db.execute('''SELECT DISTINCT f.mtime,f.size,f.kind,c.start_seconds,c.end_seconds
                    FROM files f JOIN chunks c ON c.path=f.path WHERE f.path=?
                    AND c.start_seconds IS NOT NULL AND c.end_seconds IS NOT NULL''', (path,)):
                mtime, size, kind, start, end = row
                key = hashlib.sha256(f'{path}:{mtime}:{size}:{start}:{end}'.encode()).hexdigest()
                segments.append(Path(database).parent/'segment-previews'/(key+('.mp4' if kind=='video' else '.wav')))
    with db:
        if 'index_failures' in tables:
            failures = [(r[0],) for r in db.execute('SELECT DISTINCT path FROM index_failures') if forget(r[0])]
            db.executemany('DELETE FROM index_failures WHERE path=?', failures)
        for table in ('chunks', 'thumbnails', 'thumbnail_specs', 'pdf_previews', 'media_files', 'photos_metadata', 'photos_assets'):
            if table in tables:
                db.executemany(f'DELETE FROM {table} WHERE path=?', paths)
        db.executemany('DELETE FROM files WHERE path=?', paths)
    # Also finishes reclaiming pages if a previous cleanup attempt was interrupted.
    db.execute('VACUUM')
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    for path in segments:
        path.unlink(missing_ok=True)
    return len(paths)
