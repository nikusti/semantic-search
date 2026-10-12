import hashlib
import json
import base64
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from typing import NamedTuple
import numpy as np
from .engine import normalize
from .extract import EXCLUDED, extract, kind, thumbnail
from .preview import render_pdf, photo_transition
from . import media
from . import photos
from .index_failures import FailureCache, UnsupportedContent, policy, summarize_skip
from .folders import covers, walk_includes
from .ranking import rank_results

PHOTOS_AUDIO_PENDING = 'audio-pending:'

class PhotoState(NamedTuple):
    fingerprint: str
    has_thumbnail: bool
    pixels: int | None
    quality: int | None
    duration_seconds: float | None
    has_audio: bool | None
    has_vectors: bool

class IndexPathExcluded(Exception):
    """An in-flight file was excluded; discard it without reporting failure."""

class IndexCancelled(Exception):
    """Stop at a safe boundary, preserving all completed files."""

class PhotosImportPaused(RuntimeError):
    """Stop a library pass without deleting its unvisited entries."""


def purge_excluded(db, config, verify_identity=True):
    """Remove only excluded paths, including caches in older index schemas."""
    if not config.excluded_paths:
        return 0
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'files', 'chunks', 'meta'} <= tables:
        return 0
    row = db.execute("SELECT value FROM meta WHERE key='engine'").fetchone()
    if not row or (verify_identity and not row[0].startswith('embeddinggemma-2:')):
        return 0
    paths = [(row[0],) for row in db.execute('SELECT path FROM files') if config.is_excluded(row[0])]
    if 'index_failures' in tables:
        failures = [(row[0],) for row in db.execute('SELECT DISTINCT path FROM index_failures') if config.is_excluded(row[0])]
        with db: db.executemany('DELETE FROM index_failures WHERE path=?', failures)
    if not paths: return 0
    cached_segments = []
    columns = {r[1] for r in db.execute('PRAGMA table_info(chunks)')}
    if {'start_seconds', 'end_seconds'} <= columns:
        for path, in paths:
            for row in db.execute('''SELECT DISTINCT f.mtime,f.size,f.kind,c.start_seconds,c.end_seconds
                FROM files f JOIN chunks c ON c.path=f.path
                WHERE f.path=? AND c.start_seconds IS NOT NULL AND c.end_seconds IS NOT NULL''', (path,)):
                mtime, size, kind, start, end = row
                key = hashlib.sha256(f'{path}:{mtime}:{size}:{start}:{end}'.encode()).hexdigest()
                cached_segments.append(config.database.parent / 'segment-previews' /
                                       (key + ('.mp4' if kind == 'video' else '.wav')))
    with db:
        for table in ('chunks', 'thumbnails', 'thumbnail_specs', 'pdf_previews', 'media_files', 'photos_metadata', 'photos_assets'):
            if table in tables:
                db.executemany(f'DELETE FROM {table} WHERE path=?', paths)
        db.executemany('DELETE FROM files WHERE path=?', paths)
    db.execute('VACUUM')
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    for path in cached_segments:
        path.unlink(missing_ok=True)
    return len(paths)


class Store:
    def __init__(self, config, engine):
        self.config, self.engine = config, engine
        config.database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(config.database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.index_lock = threading.Lock()
        self.cancel_requested = threading.Event()
        self.preview_lock = threading.RLock()
        self.progress = None
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY, root TEXT NOT NULL, kind TEXT NOT NULL,
            mtime INTEGER NOT NULL, size INTEGER NOT NULL, digest TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
            position INTEGER NOT NULL, snippet TEXT NOT NULL, vector BLOB NOT NULL);
          CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
          CREATE INDEX IF NOT EXISTS files_digest ON files(digest);
          CREATE TABLE IF NOT EXISTS thumbnails (
            path TEXT PRIMARY KEY REFERENCES files(path) ON DELETE CASCADE, jpeg BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS pdf_previews (
            path TEXT PRIMARY KEY REFERENCES files(path) ON DELETE CASCADE,
            mtime INTEGER NOT NULL, size INTEGER NOT NULL,
            jpeg BLOB NOT NULL, pages INTEGER NOT NULL, used REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS media_files (
            path TEXT PRIMARY KEY REFERENCES files(path) ON DELETE CASCADE,
            duration_seconds REAL NOT NULL, has_audio INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS photos_assets (
            path TEXT PRIMARY KEY REFERENCES files(path) ON DELETE CASCADE,
            asset_id TEXT NOT NULL, name TEXT NOT NULL, fingerprint TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS photos_metadata (
            path TEXT PRIMARY KEY REFERENCES files(path) ON DELETE CASCADE,
            created_at REAL);
          CREATE TABLE IF NOT EXISTS thumbnail_specs (
            path TEXT PRIMARY KEY REFERENCES files(path) ON DELETE CASCADE,
            pixels INTEGER NOT NULL, quality INTEGER NOT NULL);
        """)
        self.failures = FailureCache(self.db, self.lock)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(chunks)")}
        for name, sql_type in (("start_seconds", "REAL"), ("end_seconds", "REAL"), ("modality", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE chunks ADD COLUMN {name} {sql_type}")
        row = self.db.execute("SELECT value FROM meta WHERE key='engine'").fetchone()
        if row and row[0] != engine.identity:
            self.db.close()
            raise RuntimeError("Index belongs to another model/pipeline. Use a new database path.")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('engine', ?)", (engine.identity,))
        # Unselected sources remain saved until explicitly deleted, but never
        # participate in search/counts. No vector-space migration is needed.
        self.db.create_function('walk_includes', 2, walk_includes, deterministic=True)
        self.db.executescript('''CREATE TEMP TABLE active_roots (path TEXT PRIMARY KEY, prefix TEXT NOT NULL);
            CREATE TEMP VIEW searchable_files AS SELECT f.* FROM files f
            WHERE f.root='apple-photos' OR EXISTS
              (SELECT 1 FROM active_roots r WHERE instr(f.path,r.prefix)=1
               AND (f.root=r.path OR walk_includes(f.path,r.path)));''')
        self._select_roots(config.roots)
        self.db.commit()
        self.excluded_removed = self.prune_excluded()

    def prune_excluded(self):
        with self.lock:
            return purge_excluded(self.db, self.config, verify_identity=False)

    def update_exclusions(self, config):
        with self.lock:
            config.save()
            self.config = config
            removed = purge_excluded(self.db, config, verify_identity=False)
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('index_revision', ?)", (str(time.time_ns()),))
            return removed

    def _select_roots(self, roots):
        self.db.execute('DELETE FROM active_roots')
        self.db.executemany('INSERT INTO active_roots VALUES (?,?)',
                            [(str(p), str(p).rstrip('/')+'/') for p in roots])

    def update_folders(self, config):
        with self.lock:
            previous = self.config
            try:
                with self.db:
                    self._select_roots(config.roots)
                    self.db.execute("INSERT OR REPLACE INTO meta VALUES ('index_revision', ?)", (str(time.time_ns()),))
                    config.save()
                self.config = config
            except Exception:
                previous.save()
                raise

    def folder_counts(self):
        with self.lock:
            return [{'path':str(root), 'files':self.db.execute(
                'SELECT count(*) FROM searchable_files WHERE instr(path,?)=1',
                (str(root).rstrip('/')+'/',)).fetchone()[0]} for root in self.config.roots]

    def _check_included(self, path):
        self._check_cancelled()
        if self.config.is_excluded(path) or (not str(path).startswith(photos.PREFIX) and not any(walk_includes(path, root) for root in self.config.roots)):
            raise IndexPathExcluded()

    def cancel_index(self):
        self.cancel_requested.set()

    def _check_cancelled(self):
        if self.cancel_requested.is_set(): raise IndexCancelled()

    def _save_thumbnail(self, path, jpeg):
        self.db.execute('INSERT OR REPLACE INTO thumbnails VALUES (?,?)', (str(path), jpeg))
        self.db.execute('INSERT OR REPLACE INTO thumbnail_specs VALUES (?,?,?)',
                        (str(path), self.config.thumbnail_px, self.config.thumbnail_quality))

    def close(self):
        with self.preview_lock:
            self.db.close()

    def status(self):
        with self.lock:
            last = self.db.execute("SELECT value FROM meta WHERE key='last_index_summary'").fetchone()
            revision = self.db.execute("SELECT value FROM meta WHERE key='index_revision'").fetchone()
            disk_bytes = 0
            # WAL pages also occupy disk before the next checkpoint. Cached
            # thumbnails/previews are already inside this database total.
            for path in (self.config.database, Path(str(self.config.database) + '-wal'),
                         Path(str(self.config.database) + '-shm')):
                try:
                    disk_bytes += path.stat().st_size
                except OSError:
                    pass
            return {"index_revision": int(revision[0]) if revision else 0,
                    "database_bytes": disk_bytes,
                    "last_index_summary": json.loads(last[0]) if last else None,
                    "files": self.db.execute("SELECT count(*) FROM searchable_files").fetchone()[0],
                    "chunks": self.db.execute("SELECT count(*) FROM chunks c JOIN searchable_files f ON f.path=c.path").fetchone()[0],
                    "retained_files": self.db.execute("SELECT count(*) FROM files WHERE path NOT IN (SELECT path FROM searchable_files)").fetchone()[0],
                    "model": self.engine.identity, "dimensions": self.engine.dimensions,
                    "roots": [str(r) for r in self.config.roots],
                    "excluded_paths": [str(p) for p in self.config.excluded_paths],
                    "photos_library": {"enabled": self.config.enable_photos_library,
                        "allow_network": self.config.photos_allow_network,
                        "indexed": self.db.execute('SELECT count(*) FROM photos_assets').fetchone()[0],
                        "by_kind": [dict(row) for row in self.db.execute('''SELECT f.kind, count(DISTINCT f.path) AS files,
                            count(c.id) AS vectors, count(c.id) * ? AS vector_bytes
                            FROM photos_assets p JOIN files f ON f.path=p.path LEFT JOIN chunks c ON c.path=f.path GROUP BY f.kind''',
                            (self.engine.dimensions * 4,))]},
                    "runtime": self.engine.metrics() if hasattr(self.engine, "metrics") else {},
                    "indexing": self.index_lock.locked(),
                    "progress": dict(self.progress) if self.progress else None}

    def storage_by_kind(self):
        with self.lock:
            # Each saved vector has the index's fixed dimensions in float32.
            # Count via chunks_path instead of fetching every vector BLOB just
            # to report its size when a launcher opens or polls settings.
            return [dict(row) for row in self.db.execute("""
              SELECT f.kind, count(DISTINCT f.path) AS files, count(c.id) AS vectors,
                     count(c.id) * ? AS vector_bytes
              FROM searchable_files f LEFT JOIN chunks c ON c.path=f.path GROUP BY f.kind
            """, (self.engine.dimensions * 4,))]

    def segment(self, filename, start, end):
        """Open a bounded indexed match, never copy the entire original recording."""
        if filename.startswith(photos.PREFIX):
            return self._photos_segment(filename, start, end)
        path = Path(filename)
        with self.preview_lock:
            resolved = path.resolve()
            if resolved != path or not any(walk_includes(resolved, root) for root in self.config.roots):
                raise ValueError("Segment path is outside indexed roots")
            with self.lock:
                row = self.db.execute("""SELECT f.kind,f.mtime,f.size,m.has_audio FROM files f JOIN chunks c ON c.path=f.path
                    LEFT JOIN media_files m ON m.path=f.path
                    WHERE f.path=? AND c.start_seconds=? AND c.end_seconds=? LIMIT 1""", (str(path),start,end)).fetchone()
            if not row or row["kind"] not in {"video", "audio"} or not 0 < end-start <= 30:
                raise ValueError("Requires an indexed media segment of at most 30 seconds")
            stat = path.stat()
            if (stat.st_mtime_ns, stat.st_size) != (row["mtime"],row["size"]):
                raise ValueError("Media changed since indexing")
            folder = self.config.database.parent / "segment-previews"
            folder.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(f"{path}:{stat.st_mtime_ns}:{stat.st_size}:{start}:{end}".encode()).hexdigest()
            output = folder / (key + (".mp4" if row["kind"] == "video" else ".wav"))
            if not output.exists():
                temp = output.with_name(output.stem + ".partial" + output.suffix)
                try:
                    media.render_segment(path,temp,start,end,self.config,row["kind"]=="video")
                    after = path.stat()
                    if (after.st_mtime_ns,after.st_size) != (stat.st_mtime_ns,stat.st_size):
                        raise ValueError("Media changed while preparing the segment")
                    if temp.stat().st_size > 32*1024**2:
                        raise ValueError("Segment preview exceeds cache limit")
                    temp.replace(output)
                finally:
                    temp.unlink(missing_ok=True)
            output.touch()
            cached = sorted((p for p in folder.iterdir() if p.suffix in {".mp4", ".wav"}),key=lambda p:p.stat().st_mtime)
            total = sum(p.stat().st_size for p in cached)
            for old in cached:
                if total <= 64*1024**2: break
                if old != output:
                    total -= old.stat().st_size
                    old.unlink()
            return {"path":str(output),"start_seconds":start,"end_seconds":end,
                    "audio_unavailable": bool(row['kind'] == 'video' and row['has_audio'] and not media.probe(output, self.config).has_audio)}

    def _photos_segment(self, path, start, end):
        with self.preview_lock:
            with self.lock:
                row = self.db.execute('''SELECT f.kind,f.mtime,f.size,p.asset_id,m.has_audio FROM files f
                    JOIN photos_assets p ON p.path=f.path JOIN chunks c ON c.path=f.path
                    LEFT JOIN media_files m ON m.path=f.path
                    WHERE f.path=? AND c.start_seconds=? AND c.end_seconds=? LIMIT 1''', (path, start, end)).fetchone()
            if not row or row['kind'] != 'video' or not self._photo_enabled('video') or not 0 < end-start <= 30:
                raise ValueError('Requires an indexed Photos video segment of at most 30 seconds')
            folder = self.config.database.parent/'segment-previews'
            folder.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(f"{path}:{row['mtime']}:{row['size']}:{start}:{end}".encode()).hexdigest()
            output = folder/(key+'.mp4')
            if not output.exists():
                temp = output.with_suffix('.partial.mp4')
                try:
                    with photos.PhotoLibrary() as library:
                        source = library.video(row['asset_id'], network=self.config.photos_allow_network,
                                               max_bytes=self.config.max_media_file_mb*1024**2)
                        if source is None:
                            raise ValueError('Video is in iCloud; allow downloads or open it in Photos')
                        modified = getattr(library, 'video_modified', None)
                        if modified is not None and int(modified*1e9) != row['mtime']:
                            raise ValueError('Video changed since indexing; update the index')
                        media.render_segment(source, temp, start, end, self.config, True, True)
                    if temp.stat().st_size > 32*1024**2:
                        raise ValueError('Segment preview exceeds cache limit')
                    temp.replace(output)
                finally:
                    temp.unlink(missing_ok=True)
            output.touch()
            cached = sorted(folder.glob('*'), key=lambda p:p.stat().st_mtime)
            total = sum(p.stat().st_size for p in cached if p.suffix in {'.mp4', '.wav'})
            for old in cached:
                if total <= 64*1024**2: break
                if old != output and old.suffix in {'.mp4', '.wav'}:
                    total -= old.stat().st_size; old.unlink()
            return {'path': str(output), 'start_seconds': start, 'end_seconds': end,
                    'audio_unavailable': bool(row['has_audio'] and not media.probe(output, self.config).has_audio)}

    def _scan(self, root):
        errors, paths = [], []
        if self.config.is_excluded(root) or root not in self.config.roots:
            return paths, errors
        if not root.is_dir():
            return paths, [f"Root unavailable: {root}"]
        def onerror(error):
            errors.append(str(error))
        for folder, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
            self._check_cancelled()
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith(".")
                             and not (Path(folder) / d).is_symlink()
                             and (Path(folder) / d) not in self.config.roots
                             and not self.config.is_excluded(Path(folder) / d))
            for name in sorted(names):
                path = Path(folder) / name
                file_kind = kind(path)
                enabled = self._enabled(file_kind)
                if not name.startswith(".") and not path.is_symlink() and enabled:
                    paths.append(path)
        return paths, errors

    def _enabled(self, file_kind):
        return {"document": self.config.enable_documents, "image": self.config.enable_images,
                "video": self.config.enable_video or self.config.enable_audio, "audio": self.config.enable_audio}.get(file_kind, False)

    def _media_chunks(self, path, info, file_kind=None, identity_path=None, orient=False,
                      saved_chunks=(), poster=None, audio_errors=None):
        file_kind = file_kind or kind(path)
        identity_path = identity_path or path
        if info.duration > self.config.max_media_seconds:
            raise ValueError(f"Media exceeds {self.config.max_media_seconds} seconds; skipped, not truncated")
        if file_kind == "video" and self.config.enable_video and not info.has_video:
            raise ValueError("Video file has no video stream")
        if file_kind == "audio" and not info.has_audio:
            raise ValueError("Audio file has no audio stream")
        windows = []
        if file_kind == "video" and self.config.enable_video:
            windows.extend((start, end, "video") for start, end in media.segments(info.video_duration or info.duration, self.config.video_segment_seconds))
        if self.config.enable_audio and info.has_audio:
            windows.extend((start, end, "audio") for start, end in media.segments(info.audio_duration or info.duration, self.config.audio_segment_seconds, self.config.audio_overlap_seconds))
        if not windows: raise ValueError('No enabled media stream in this file')
        self.progress["current_chunks_total"] = len(windows)
        chunks = []
        saved = {(r['start_seconds'], r['end_seconds'], r['modality']): tuple(r) for r in saved_chunks}
        for start, end, modality in windows:
            self._check_included(identity_path)
            if (start, end, modality) in saved:
                chunks.append(saved[(start, end, modality)])
                self.progress['current_chunks_done'] += 1
                continue
            if modality == "video":
                frames = media.video_frames(path, start, end, self.config, orient)
                vector = self.engine.video(frames)
                if poster is None:
                    poster = media.jpeg(frames[len(frames)//2], self.config.thumbnail_px, self.config.thumbnail_quality)
            else:
                try:
                    samples = media.audio_samples(path, start, end, self.config)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
                    self._check_included(identity_path)
                    if audio_errors is None or file_kind != 'video' or not self.config.enable_video:
                        raise
                    audio_errors.append((start, end))
                    self.progress['current_chunks_done'] += 1
                    continue
                vector = self.engine.audio(samples)
            self._check_included(identity_path)
            snippet = f"{'Video scene' if modality == 'video' else 'Audio'} · {media.timestamp(start)}–{media.timestamp(end)}"
            chunks.append((snippet, normalize(vector, self.engine.dimensions).astype("<f4").tobytes(), start, end, modality))
            self.progress["current_chunks_done"] += 1
        return chunks, poster

    def index(self, retry_skipped=False):
        if not self.index_lock.acquire(blocking=False):
            raise RuntimeError("An indexing pass is already running")
        summary = {"indexed": 0, "added": 0, "updated": 0, "unchanged": 0, "reused": 0, "deleted": 0, "excluded": 0, "errors": [],
                   "cached_skips": 0, "retry_deferred": 0, "audio_retries_deferred": 0, "skipped": []}
        failure_policy = policy(self.config)
        self.progress = {"total": 0, "processed": 0, "started_at": time.time(),
                         "finished_at": None, "errors": 0,
                         "cached_skips": 0, "retry_deferred": 0,
                         "current_chunks_done": 0, "current_chunks_total": 0}
        try:
            for root in self.config.roots:
                selection = self.config.roots
                paths, scan_errors = self._scan(root)
                self.progress["total"] += len(paths)
                summary["errors"].extend(scan_errors)
                seen = {str(p) for p in paths}
                for path in paths:
                    fingerprint = None
                    self.progress["current_chunks_done"] = 0
                    self.progress["current_chunks_total"] = 0
                    try:
                        self._check_included(path)
                        stat = path.stat()
                        fingerprint = f'{stat.st_mtime_ns}:{stat.st_size}'
                        cached_failure = self.failures.lookup(path, 'file', fingerprint, failure_policy, retry_skipped)
                        if cached_failure:
                            with self.lock, self.db:
                                self.db.execute('UPDATE index_failures SET root=? WHERE path=?', (str(root), str(path)))
                            summarize_skip(summary, cached_failure)
                            continue
                        file_kind = kind(path)
                        is_media = file_kind in {"video", "audio"}
                        limit = self.config.max_media_file_mb if is_media else self.config.max_file_mb
                        if stat.st_size > limit * 1024 * 1024:
                            raise UnsupportedContent(f"Larger than {limit} MB limit", 'file_size')
                        with self.lock:
                            old = self.db.execute("SELECT * FROM files WHERE path=?", (str(path),)).fetchone()
                        same_file = bool(old and old['mtime'] == stat.st_mtime_ns and old['size'] == stat.st_size)
                        retry_audio = bool(same_file and file_kind == 'video' and self.config.enable_audio and old['digest'].startswith(PHOTOS_AUDIO_PENDING))
                        if retry_audio:
                            cached_audio = self.failures.lookup(path, 'audio', fingerprint, failure_policy, retry_skipped)
                            if cached_audio:
                                summarize_skip(summary, cached_audio)
                                summary['partial_videos'] = summary.get('partial_videos', 0) + 1
                                summary['unchanged'] += 1
                                continue
                        if same_file and not retry_audio:
                            if old['root'] != str(root):
                                with self.lock, self.db:
                                    self._check_included(path)
                                    self.db.execute('UPDATE files SET root=? WHERE path=?', (str(root), str(path)))
                            if old["kind"] == "image":
                                with self.lock:
                                    has_thumbnail = self.db.execute('''SELECT 1 FROM thumbnails t
                                        JOIN thumbnail_specs s ON s.path=t.path
                                        WHERE t.path=? AND s.pixels>=? AND s.quality>=?''',
                                        (str(path),self.config.thumbnail_px,self.config.thumbnail_quality)).fetchone()
                                if not has_thumbnail:
                                    jpeg = thumbnail(path, self.config.thumbnail_px, self.config.thumbnail_quality)
                                    with self.lock, self.db:
                                        self._check_included(path)
                                        self._save_thumbnail(path, jpeg)
                            self.failures.clear(path, 'file')
                            summary["unchanged"] += 1
                            continue
                        if retry_audio:
                            digest = old['digest'].removeprefix(PHOTOS_AUDIO_PENDING)
                        else:
                            with path.open("rb") as source:
                                digest = hashlib.file_digest(source, "sha256").hexdigest()
                        info = media.probe(path, self.config) if is_media else None
                        if info and info.duration > self.config.max_media_seconds:
                            raise UnsupportedContent(f"Media exceeds {self.config.max_media_seconds} seconds; skipped, not truncated", 'duration')
                        jpeg = None
                        audio_errors = []
                        with self.lock:
                            reusable = (str(path),) if retry_audio else self.db.execute(
                                "SELECT path FROM files WHERE digest=? AND kind=? LIMIT 1",
                                (digest, kind(path))).fetchone()
                            saved = self.db.execute("SELECT snippet, vector, start_seconds, end_seconds, modality FROM chunks WHERE path=? ORDER BY position",
                                                    (reusable[0],)).fetchall() if reusable else []
                        if retry_audio:
                            with self.lock:
                                poster = self.db.execute('SELECT jpeg FROM thumbnails WHERE path=?', (str(path),)).fetchone()
                            chunks, jpeg = self._media_chunks(path, info, saved_chunks=saved, poster=poster[0] if poster else None, audio_errors=audio_errors)
                        elif saved:
                            chunks = [(r["snippet"], r["vector"], r["start_seconds"], r["end_seconds"], r["modality"]) for r in saved]
                            if file_kind == "video":
                                with self.lock:
                                    reused_poster = self.db.execute("SELECT jpeg FROM thumbnails WHERE path=?", (reusable[0],)).fetchone()
                                jpeg = reused_poster[0] if reused_poster else None
                        elif is_media:
                            chunks, jpeg = self._media_chunks(path, info, audio_errors=audio_errors if file_kind == 'video' and self.config.enable_video else None)
                        elif kind(path) == "image":
                            chunks = [("", self.engine.image(path).astype("<f4").tobytes(), None, None, None)]
                        else:
                            text = extract(path).strip()
                            if not text:
                                raise UnsupportedContent("No extractable text (scanned PDFs need OCR)", 'no_text')
                            if len(text) > self.config.max_document_chars:
                                raise UnsupportedContent(f"Document exceeds {self.config.max_document_chars} characters; skipped, not truncated", 'document_length')
                            parts = list(self.engine.chunks(text))
                            if len(parts) > self.config.max_document_chunks:
                                raise UnsupportedContent(f"Document exceeds {self.config.max_document_chunks} chunks; skipped, not truncated", 'document_chunks')
                            self.progress["current_chunks_total"] = len(parts)
                            chunks = []
                            if self.config.backend in {'mlx', 'coreml'} and hasattr(self.engine,'document_batch'):
                                for start in range(0,len(parts),8):
                                    self._check_included(path)
                                    batch=parts[start:start+8]
                                    vectors=self.engine.document_batch(batch)
                                    if len(vectors)!=len(batch):raise ValueError('Incomplete embedding batch')
                                    chunks.extend((part,normalize(vector,self.engine.dimensions).astype('<f4').tobytes(),None,None,None) for part,vector in zip(batch,vectors))
                                    self.progress['current_chunks_done']+=len(batch)
                            else:
                                for part in parts:
                                    self._check_included(path)
                                    chunks.append((part, self.engine.document(part).astype("<f4").tobytes(),None,None,None))
                                    self.progress["current_chunks_done"] += 1
                        self._check_included(path)
                        if file_kind == "image":
                            jpeg = thumbnail(path, self.config.thumbnail_px, self.config.thumbnail_quality)
                        after = path.stat()
                        if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                            raise ValueError("File changed while indexing; retry next pass")
                        with self.lock, self.db:
                            self._check_included(path)
                            self.db.execute("DELETE FROM files WHERE path=?", (str(path),))
                            self.db.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)",
                                            (str(path), str(root), kind(path), stat.st_mtime_ns, stat.st_size, PHOTOS_AUDIO_PENDING + digest if audio_errors else digest))
                            self.db.executemany("INSERT INTO chunks(path, position, snippet, vector, start_seconds, end_seconds, modality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                                [(str(path), i, *chunk) for i, chunk in enumerate(chunks)])
                            if info:
                                self.db.execute("INSERT INTO media_files VALUES (?, ?, ?)", (str(path), info.duration, int(info.has_audio)))
                            if jpeg:
                                self._save_thumbnail(path, jpeg)
                        self.failures.clear(path, 'file')
                        if audio_errors:
                            message = f'{len(audio_errors)} audio window(s) could not be decoded; video scenes are indexed.'
                            with self.lock:
                                self._check_included(path)
                                self.failures.record(path, 'audio', root, fingerprint, failure_policy, ValueError(message))
                            summary['partial_videos'] = summary.get('partial_videos', 0) + 1
                            summary['errors'].append(f'{path}: {message}')
                        else:
                            self.failures.clear(path, 'audio')
                        summary["reused" if saved and not retry_audio else "indexed"] += 1
                        summary["updated" if old else "added"] += 1
                    except IndexCancelled:
                        raise
                    except IndexPathExcluded:
                        summary['excluded'] += 1
                    except Exception as error:
                        try:
                            with self.lock:
                                self._check_included(path)
                                if path.suffix.lower() == '.pdf' and 'cryptography' in str(error) and 'AES' in str(error):
                                    error = UnsupportedContent(str(error), 'pdf_encryption_dependency')
                                if fingerprint is not None:
                                    self.failures.record(path, 'file', root, fingerprint, failure_policy, error)
                            summary["errors"].append(f"{path}: {error}")
                        except IndexPathExcluded:
                            summary['excluded'] += 1
                    finally:
                        self.progress["processed"] += 1
                        self.progress["errors"] = len(summary["errors"])
                        self.progress.update(cached_skips=summary['cached_skips'], retry_deferred=summary['retry_deferred']+summary['audio_retries_deferred'])
                # Never prune if part of the tree could not be read.
                if not scan_errors:
                    self._check_cancelled()
                    with self.lock, self.db:
                        if selection != self.config.roots or root not in self.config.roots:
                            continue  # The walk may have delegated folders selected before this edit.
                        delegated = tuple(p for p in self.config.roots if p != root and p.is_relative_to(root))
                        old_paths = self.db.execute('SELECT path FROM files WHERE instr(path,?)=1',
                                                    (str(root).rstrip('/')+'/',)).fetchall()
                        removed = [(r[0],) for r in old_paths if
                                   (r[0] not in seen and walk_includes(r[0],root) and not covers(r[0],delegated)) or self.config.is_excluded(r[0])]
                        self.db.executemany("DELETE FROM files WHERE path=?", removed)
                        summary["deleted"] += len(removed)
                        self.failures.prune(root, seen, delegated)
            if self.config.enable_photos_library and (self.config.enable_images or self._enabled('video')):
                self._index_photos(summary, failure_policy, retry_skipped)
            return summary
        except IndexCancelled:
            summary['cancelled'] = True
            return summary
        finally:
            self.progress["finished_at"] = time.time()
            last = {key: value for key, value in summary.items() if key not in {'errors', 'skipped'}}
            last.update(errors=len(summary["errors"]), finished_at=self.progress["finished_at"])
            try:
                with self.lock, self.db:
                    self.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_index_summary', ?)", (json.dumps(last),))
            finally:
                try:
                    try:
                        if hasattr(self.engine, "release_audio"):
                            self.engine.release_audio()
                    finally:
                        if hasattr(self.engine, "release_vision"):
                            self.engine.release_vision()
                finally:
                    self.cancel_requested.clear()
                    self.index_lock.release()

    def _photo_enabled(self, file_kind):
        return self.config.enable_photos_library and self._enabled(file_kind)

    def _index_photos(self, summary, failure_policy, retry_skipped):
        """Compare small metadata first; fetch payloads only for actual work."""
        def progress(value): self.progress.update(value)
        legacy_specs = []
        def save_legacy_specs():
            if legacy_specs:
                with self.lock, self.db:
                    self.db.executemany('INSERT OR REPLACE INTO thumbnail_specs VALUES (?,?,?)', legacy_specs)
                legacy_specs.clear()
        try:
            with photos.PhotoLibrary(self.cancel_requested.is_set, progress) as library:
                self.progress.update(source='photos', phase='scanning')
                inventory = library.assets()
                seen = {photos.asset_path(asset['id']) for asset in inventory}
                assets = [asset for asset in inventory if self._photo_enabled(asset.get('kind', 'image'))]
                summary['library_photos'] = sum(asset.get('kind', 'image') == 'image' for asset in inventory)
                summary['library_videos'] = sum(asset.get('kind') == 'video' for asset in inventory)
                summary['library_video_seconds'] = sum(asset.get('duration', 0) for asset in inventory if asset.get('kind') == 'video')
                self.progress['total'] += len(assets)
                self.progress.update(phase='indexing', photos_total=len(assets))
                with self.lock:
                    existing = {row[0]: PhotoState(*row[1:]) for row in self.db.execute('''SELECT p.path,p.fingerprint,
                        t.path IS NOT NULL,s.pixels,s.quality,m.duration_seconds,m.has_audio,
                        EXISTS(SELECT 1 FROM chunks c WHERE c.path=p.path) AS has_vectors
                        FROM photos_assets p LEFT JOIN thumbnails t ON t.path=p.path
                        LEFT JOIN thumbnail_specs s ON s.path=p.path LEFT JOIN media_files m ON m.path=p.path''')}
                for asset in assets:
                    self._check_cancelled()
                    path = photos.asset_path(asset['id'])
                    file_kind = asset.get('kind', 'image')
                    fingerprint = hashlib.sha256(json.dumps(asset, sort_keys=True).encode()).hexdigest()
                    source = None
                    self.progress.update(current_chunks_done=0, current_chunks_total=0, download_fraction=0)
                    try:
                        old = existing.get(path)
                        reusable = bool(old and old.fingerprint.removeprefix(PHOTOS_AUDIO_PENDING) == fingerprint and old.has_vectors and (file_kind != 'video' or old.duration_seconds is not None))
                        retry_audio = bool(reusable and self.config.enable_audio and old.fingerprint.startswith(PHOTOS_AUDIO_PENDING))
                        cached_audio = self.failures.lookup(path, 'audio', fingerprint, failure_policy, retry_skipped) if retry_audio else None
                        good_thumbnail = bool(reusable and old.has_thumbnail and old.pixels is not None and old.pixels >= self.config.thumbnail_px and old.quality >= self.config.thumbnail_quality)
                        # Old indexes learn their thumbnail metadata once. Newly
                        # generated thumbnails remember the requested size even
                        # when Photos delivers a smaller/cropped representation.
                        if reusable and old.has_thumbnail and old.pixels is None:
                            with self.lock:
                                cached = self.db.execute('SELECT jpeg FROM thumbnails WHERE path=?', (path,)).fetchone()
                            if cached:
                                from io import BytesIO
                                from PIL import Image
                                with Image.open(BytesIO(cached[0])) as small: edge = max(small.size)
                                if edge >= min(self.config.thumbnail_px, max(asset['width'], asset['height'])):
                                    legacy_specs.append((path, self.config.thumbnail_px, self.config.thumbnail_quality))
                                    if len(legacy_specs) >= 256: save_legacy_specs()
                                    good_thumbnail = True
                        if reusable and good_thumbnail and (not retry_audio or cached_audio):
                            summary['unchanged'] += 1
                            if cached_audio:
                                summarize_skip(summary, cached_audio)
                                summary['partial_videos'] = summary.get('partial_videos', 0) + 1
                            continue
                        cached_failure = self.failures.lookup(path, 'file', fingerprint, failure_policy, retry_skipped)
                        if cached_failure:
                            summarize_skip(summary, cached_failure)
                            continue
                        import shutil
                        if shutil.disk_usage(self.config.database.parent).free < 512 * 1024**2:
                            raise PhotosImportPaused('Less than 512 MB free. Free disk space before importing more Photos assets')
                        # Thumbnail-only repair updates no vector/date/media row.
                        if reusable and (not retry_audio or cached_audio):
                            source = library.image(asset['id'], network=self.config.photos_allow_network)
                            if source is None:
                                key = 'deferred_videos' if file_kind == 'video' else 'deferred_photos'
                                summary[key] = summary.get(key, 0) + 1
                                continue
                            modified = getattr(library, 'image_modified', None)
                            if modified is not None and modified != asset['modified']:
                                raise ValueError('Photo changed during import; retry on the next pass')
                            jpeg = thumbnail(source, self.config.thumbnail_px, self.config.thumbnail_quality)
                            self._check_cancelled()
                            with self.lock, self.db: self._save_thumbnail(path, jpeg)
                            self.failures.clear(path, 'file')
                            summary['unchanged'] += 1
                            if cached_audio:
                                summarize_skip(summary, cached_audio)
                                summary['partial_videos'] = summary.get('partial_videos', 0) + 1
                            continue
                        info = None
                        audio_errors = []
                        saved, poster = [], None
                        if reusable:
                            with self.lock:
                                saved = self.db.execute('SELECT snippet,vector,start_seconds,end_seconds,modality FROM chunks WHERE path=? ORDER BY position', (path,)).fetchall()
                                cached = self.db.execute('SELECT jpeg FROM thumbnails WHERE path=?', (path,)).fetchone()
                            poster = cached[0] if cached and good_thumbnail else None
                        if file_kind == 'video':
                            import math
                            duration = asset.get('duration', 0)
                            if not math.isfinite(duration) or duration <= 0 or duration > self.config.max_media_seconds:
                                raise UnsupportedContent('Video exceeds the duration limit or has no usable duration; skipped, not truncated', 'duration')
                            source = library.video(asset['id'], network=self.config.photos_allow_network,
                                                   max_bytes=self.config.max_media_file_mb * 1024**2)
                            if source is not None:
                                modified = getattr(library, 'video_modified', None)
                                if modified is not None and modified != asset['modified']:
                                    raise ValueError('Video changed during import; retry on the next pass')
                                if source.stat().st_size > self.config.max_media_file_mb * 1024**2:
                                    raise UnsupportedContent('Video exceeds the configured file size limit.', 'file_size')
                                info = media.probe(source, self.config)
                                chunks, jpeg = self._media_chunks(source, info, file_kind='video', identity_path=path, orient=True,
                                    saved_chunks=saved, poster=poster, audio_errors=audio_errors)
                                if jpeg is None:
                                    jpeg = media.jpeg(media.frame(source, 0, self.config, orient=True), self.config.thumbnail_px, self.config.thumbnail_quality)
                                name = getattr(library, 'video_name', None)
                        else:
                            source = library.image(asset['id'], network=self.config.photos_allow_network)
                            if source is not None:
                                modified = getattr(library, 'image_modified', None)
                                if modified is not None and modified != asset['modified']:
                                    raise ValueError('Photo changed during import; retry on the next pass')
                                vector = normalize(self.engine.image(source), self.engine.dimensions).astype('<f4').tobytes()
                                chunks = [('', vector, None, None, None)]
                                jpeg = thumbnail(source, self.config.thumbnail_px, self.config.thumbnail_quality)
                                name = getattr(library, 'image_name', None)
                        if source is None:
                            key = 'deferred_videos' if file_kind == 'video' else 'deferred_photos'
                            summary[key] = summary.get(key, 0) + 1
                            continue
                        self._check_cancelled()
                        stored_fingerprint = PHOTOS_AUDIO_PENDING + fingerprint if audio_errors else fingerprint
                        with self.lock, self.db:
                            self.db.execute('DELETE FROM files WHERE path=?', (path,))
                            self.db.execute('INSERT INTO files VALUES (?,?,?,?,?,?)',
                                            (path, photos.ROOT, file_kind, int(asset['modified']*1e9), source.stat().st_size, stored_fingerprint))
                            self.db.executemany('INSERT INTO chunks(path,position,snippet,vector,start_seconds,end_seconds,modality) VALUES (?,?,?,?,?,?,?)',
                                                [(path, i, *chunk) for i, chunk in enumerate(chunks)])
                            self._save_thumbnail(path, jpeg)
                            self.db.execute('INSERT INTO photos_assets VALUES (?,?,?,?)', (path, asset['id'], name or asset['name'], stored_fingerprint))
                            if info: self.db.execute('INSERT INTO media_files VALUES (?,?,?)', (path, info.duration, int(info.has_audio)))
                        self.failures.clear(path, 'file')
                        if audio_errors:
                            message = f'{len(audio_errors)} audio window(s) could not be decoded; video scenes are indexed.'
                            self.failures.record(path, 'audio', photos.ROOT, fingerprint, failure_policy, ValueError(message))
                            summary['partial_videos'] = summary.get('partial_videos', 0) + 1
                            summary['errors'].append('Apple Photos: ' + message)
                        else:
                            self.failures.clear(path, 'audio')
                        summary['indexed'] += 1
                        summary['updated' if old else 'added'] += 1
                    except (IndexCancelled, PhotosImportPaused):
                        raise
                    except Exception as error:
                        self._check_cancelled()
                        if str(error) in {'Video exceeds the configured file size limit.', 'Video exceeds the configured file size limit'}:
                            error = UnsupportedContent(str(error), 'file_size')
                        self.failures.record(path, 'file', photos.ROOT, fingerprint, failure_policy, error)
                        summary['errors'].append(f'Apple Photos: {error}')
                    finally:
                        if source is not None: source.unlink(missing_ok=True)
                        self.progress['processed'] += 1
                        self.progress['errors'] = len(summary['errors'])
                        self.progress.update(cached_skips=summary['cached_skips'], retry_deferred=summary['retry_deferred']+summary['audio_retries_deferred'])
                self._check_cancelled()
                with self.lock, self.db:
                    removed = [(r[0],) for r in self.db.execute('SELECT path FROM photos_assets') if r[0] not in seen]
                    self.db.executemany('DELETE FROM files WHERE path=?', removed)
                    summary['deleted'] += len(removed)
                self.failures.prune(photos.ROOT, seen)
        except IndexCancelled:
            raise
        except Exception as error:
            self._check_cancelled()
            summary['errors'].append(f'Apple Photos: {error}')
        finally:
            save_legacy_specs()

    def photos_details(self, path):
        with self.preview_lock:
            with self.lock:
                asset = self.db.execute('SELECT p.asset_id,p.fingerprint,f.kind FROM photos_assets p JOIN files f ON f.path=p.path WHERE p.path=?', (path,)).fetchone()
                cached = self.db.execute('SELECT created_at FROM photos_metadata WHERE path=?', (path,)).fetchone()
            if not asset or not self._photo_enabled(asset['kind']):
                raise ValueError('Photo is not indexed or enabled')
            if cached: return {'created_at': cached[0]}
            with photos.PhotoLibrary() as library:
                result = library.metadata(asset['asset_id'])
            import math
            created = result.get('created_at')
            if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
                created = None
            with self.lock, self.db:
                current = self.db.execute('SELECT fingerprint FROM photos_assets WHERE path=?', (path,)).fetchone()
                if not current or current[0] != asset['fingerprint']:
                    raise RuntimeError('Photo changed while reading its date; retry shortly')
                self.db.execute('INSERT OR REPLACE INTO photos_metadata VALUES (?,?)', (path, created))
            return {'created_at': created}

    def copy_photo(self, path):
        with self.preview_lock:
            with self.lock:
                asset = self.db.execute('SELECT p.asset_id,f.kind FROM photos_assets p JOIN files f ON f.path=p.path WHERE p.path=?', (path,)).fetchone()
            if not asset or asset['kind'] != 'image' or not self._photo_enabled('image'):
                raise ValueError('Photo is not indexed or enabled')
            with photos.PhotoLibrary() as library:
                return library.copy_photo(asset['asset_id'], network=self.config.photos_allow_network)

    def photos_thumbnail(self, path):
        with self.lock:
            row = self.db.execute('SELECT t.jpeg,f.kind FROM thumbnails t JOIN photos_assets p ON p.path=t.path JOIN files f ON f.path=t.path WHERE p.path=?', (path,)).fetchone()
        if not row or not self._photo_enabled(row['kind']):
            raise ValueError('Photo is not indexed')
        return {'preview': 'data:image/jpeg;base64,' + base64.b64encode(row[0]).decode(), 'page': 0, 'pages': 0}

    def _photos_preview(self, path, start_seconds=0):
        with self.preview_lock:
            with self.lock:
                asset = self.db.execute('SELECT p.*,f.kind FROM photos_assets p JOIN files f ON f.path=p.path WHERE p.path=?', (path,)).fetchone()
                cached = self.db.execute('SELECT jpeg,size FROM pdf_previews WHERE path=?', (path,)).fetchone()
                small = self.db.execute('SELECT jpeg FROM thumbnails WHERE path=?', (path,)).fetchone()
            if not asset or not small or not self._photo_enabled(asset['kind']):
                raise ValueError('Photo is not indexed')
            is_video = asset['kind'] == 'video'
            if is_video:
                with self.lock:
                    match = self.db.execute('SELECT 1 FROM chunks WHERE path=? AND start_seconds=?', (path, start_seconds)).fetchone()
                if not match:
                    raise ValueError('Video preview requires an indexed segment')
                if cached and cached['size'] != round(start_seconds*1000):
                    cached = None
            if cached:
                jpeg = cached[0]
                with self.lock, self.db:
                    self.db.execute('UPDATE pdf_previews SET used=? WHERE path=?', (time.time(), path))
            else:
                jpeg = small[0]
                try:
                    with photos.PhotoLibrary() as library:
                        image = (library.video_frame(asset['asset_id'], start_seconds, network=self.config.photos_allow_network, timeout=10)
                                 if is_video else library.image(asset['asset_id'], network=self.config.photos_allow_network, timeout=10))
                        if image:
                            jpeg = image.read_bytes()
                            with self.lock, self.db:
                                self.db.execute('INSERT OR REPLACE INTO pdf_previews VALUES (?,?,?,?,?,?)',
                                                (path, 0, round(start_seconds*1000) if is_video else len(jpeg), jpeg, 0, time.time()))
                                total = self.db.execute('SELECT coalesce(sum(length(jpeg)),0) FROM pdf_previews').fetchone()[0]
                                for row in self.db.execute('SELECT path,length(jpeg) AS bytes FROM pdf_previews ORDER BY used').fetchall():
                                    if total <= 32 * 1024**2: break
                                    self.db.execute('DELETE FROM pdf_previews WHERE path=?', (row['path'],))
                                    total -= row['bytes']
                except RuntimeError:
                    pass  # Offline/revoked permission: keep the indexed thumbnail usable.
            return {'preview': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode(), 'page': 0, 'pages': 0}

    def search(self, query, file_kind="all", limit=20):
        start = time.perf_counter()
        vector = normalize(self.engine.query(query), self.engine.dimensions)
        with self.lock:
            rows = self.db.execute("""
                SELECT f.path, f.kind, c.snippet, c.vector, c.start_seconds, c.end_seconds, c.modality,
                       m.duration_seconds, p.name AS photo_name, p.asset_id FROM chunks c JOIN searchable_files f ON f.path=c.path
                       LEFT JOIN media_files m ON m.path=f.path
                       LEFT JOIN photos_assets p ON p.path=f.path
                WHERE (?='all' OR f.kind=?)
                  AND (p.asset_id IS NULL OR (? AND ((f.kind='image' AND ?) OR (f.kind='video' AND ?))))
            """, (file_kind, file_kind, self.config.enable_photos_library, self.config.enable_images, self._enabled('video'))).fetchall()
        # Exact cosine scan is intentional for the prototype; no DB server required.
        best = {}
        if rows:
            matrix = np.stack([np.frombuffer(r["vector"], dtype="<f4") for r in rows])
            scores = matrix @ vector
            for row, score in zip(rows, scores):
                path = row["path"]
                if path not in best or score > best[path]["score"]:
                    best[path] = {"path": path, "name": row['photo_name'] or Path(path).name, "kind": row["kind"],
                                  "source": 'photos' if row['asset_id'] else 'file',
                                  "open_target": ('photos://asset?identifier=' + photos.quote(row['asset_id'].split('/')[0], safe='')) if row['asset_id'] else path,
                                  "score": float(score), "snippet": row["snippet"][:1200],
                                  "start_seconds": row["start_seconds"], "end_seconds": row["end_seconds"],
                                  "matched_modality": row["modality"], "duration_seconds": row["duration_seconds"]}
        visible = [r for r in best.values()
                   if (r['source'] == 'photos' and self._photo_enabled(r['kind']))
                   or (r['source'] == 'file' and any(walk_includes(r['path'], root) for root in self.config.roots) and not self.config.is_excluded(r['path']) and Path(r['path']).is_file())]
        results = rank_results(visible, file_kind)[:limit]
        # Decode thumbnails only for returned results, not the whole image library.
        with self.lock:
            for result in results:
                row = self.db.execute("SELECT jpeg FROM thumbnails WHERE path=?", (result["path"],)).fetchone()
                result["thumbnail"] = "data:image/jpeg;base64," + base64.b64encode(row[0]).decode() if row else None
        return {"query": query, "results": results,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1)}

    def preview(self, filename, animate=False, start_seconds=0):
        if filename.startswith(photos.PREFIX):
            return self._photos_preview(filename, start_seconds)
        path = Path(filename)
        with self.preview_lock:
            with self.lock:
                indexed = self.db.execute("SELECT path FROM files WHERE path=?", (str(path),)).fetchone()
            if not indexed or (path.suffix.lower() != ".pdf" and kind(path) not in {"image", "video"}):
                raise ValueError("Preview requires an indexed PDF, image or video")
            resolved = path.resolve()
            if resolved != path or not any(walk_includes(resolved, root) for root in self.config.roots):
                raise ValueError("Preview path is outside indexed roots")
            stat = path.stat()
            if kind(path) == "video":
                if stat.st_size > self.config.max_media_file_mb * 1024**2:
                    raise ValueError("Video exceeds size limit")
                with self.lock:
                    info = self.db.execute("SELECT duration_seconds FROM media_files WHERE path=?", (str(path),)).fetchone()
                if not info or not 0 <= start_seconds < info[0]:
                    raise ValueError("Preview time is outside the indexed clip")
                jpeg = media.jpeg(media.frame(path, min(start_seconds + .5, info[0] * .95), self.config), 1024, 80)
                return {"preview": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(), "page": 0, "pages": 0}
            if stat.st_size > self.config.max_file_mb * 1024**2:
                raise ValueError("PDF exceeds size limit")
            with self.lock:
                cached = self.db.execute("SELECT * FROM pdf_previews WHERE path=?", (str(path),)).fetchone()
            if cached and (cached["mtime"], cached["size"]) == (stat.st_mtime_ns, stat.st_size):
                jpeg, pages = cached["jpeg"], cached["pages"]
                with self.lock, self.db:
                    self.db.execute("UPDATE pdf_previews SET used=? WHERE path=?", (time.time(), str(path)))
            else:
                jpeg, pages = render_pdf(path, self.config.pdf_renderer) if path.suffix.lower() == ".pdf" else (thumbnail(path, 1024, 80), 0)
                after = path.stat()
                if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                    raise ValueError("PDF changed during preview")
                with self.lock, self.db:
                    self.db.execute("INSERT OR REPLACE INTO pdf_previews VALUES (?, ?, ?, ?, ?, ?)",
                                    (str(path), stat.st_mtime_ns, stat.st_size, jpeg, pages, time.time()))
                    # Keep a bounded LRU cache; thumbnails for photographs are separate.
                    total = self.db.execute("SELECT coalesce(sum(length(jpeg)),0) FROM pdf_previews").fetchone()[0]
                    for row in self.db.execute("SELECT path,length(jpeg) AS bytes FROM pdf_previews ORDER BY used").fetchall():
                        if total <= 32 * 1024**2:
                            break
                        self.db.execute("DELETE FROM pdf_previews WHERE path=?", (row["path"],))
                        total -= row["bytes"]
            result = {"preview": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
                      "page": 1 if pages else 0, "pages": pages}
            if animate and not pages:
                with self.lock:
                    small = self.db.execute('SELECT jpeg FROM thumbnails WHERE path=?', (str(path),)).fetchone()
                if small:
                    # Animation is an enhancement: preserve the working static preview
                    # if an unusual image cannot produce a transition.
                    try:
                        frames, duration = photo_transition(small[0], jpeg)
                        result.update(transition_frames=[
                            'data:image/jpeg;base64,' + base64.b64encode(frame).decode() for frame in frames],
                                      transition_ms=duration)
                    except (OSError, ValueError):
                        pass
            return result
