import hashlib
import json
import base64
import os
from pathlib import Path
import sqlite3
import threading
import time
import numpy as np
from .engine import normalize
from .extract import EXCLUDED, extract, kind, thumbnail
from .preview import render_pdf, photo_transition
from . import media

class IndexPathExcluded(Exception):
    """An in-flight file was excluded; discard it without reporting failure."""

class IndexCancelled(Exception):
    """Stop at a safe boundary, preserving all completed files."""


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
    if not paths:
        return 0
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
        for table in ('chunks', 'thumbnails', 'pdf_previews', 'media_files'):
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
        self.preview_lock = threading.Lock()
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
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(chunks)")}
        for name, sql_type in (("start_seconds", "REAL"), ("end_seconds", "REAL"), ("modality", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE chunks ADD COLUMN {name} {sql_type}")
        row = self.db.execute("SELECT value FROM meta WHERE key='engine'").fetchone()
        if row and row[0] != engine.identity:
            self.db.close()
            raise RuntimeError("Index belongs to another model/pipeline. Use a new database path.")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('engine', ?)", (engine.identity,))
        allowed_roots = {str(root) for root in config.roots}
        obsolete = [(row[0],) for row in self.db.execute("SELECT DISTINCT root FROM files")
                    if row[0] not in allowed_roots]
        self.db.executemany("DELETE FROM files WHERE root=?", obsolete)
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

    def _check_included(self, path):
        self._check_cancelled()
        if self.config.is_excluded(path):
            raise IndexPathExcluded()

    def cancel_index(self):
        self.cancel_requested.set()

    def _check_cancelled(self):
        if self.cancel_requested.is_set(): raise IndexCancelled()

    def close(self):
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
                    "files": self.db.execute("SELECT count(*) FROM files").fetchone()[0],
                    "chunks": self.db.execute("SELECT count(*) FROM chunks").fetchone()[0],
                    "model": self.engine.identity, "dimensions": self.engine.dimensions,
                    "roots": [str(r) for r in self.config.roots],
                    "excluded_paths": [str(p) for p in self.config.excluded_paths],
                    "runtime": self.engine.metrics() if hasattr(self.engine, "metrics") else {},
                    "indexing": self.index_lock.locked(),
                    "progress": dict(self.progress) if self.progress else None}

    def storage_by_kind(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("""
              SELECT f.kind, count(DISTINCT f.path) AS files, count(c.id) AS vectors,
                     coalesce(sum(length(c.vector)),0) AS vector_bytes
              FROM files f LEFT JOIN chunks c ON c.path=f.path GROUP BY f.kind
            """)]

    def segment(self, filename, start, end):
        """Open a bounded indexed match, never copy the entire original recording."""
        path = Path(filename)
        with self.preview_lock:
            resolved = path.resolve()
            if resolved != path or not any(resolved.is_relative_to(root) for root in self.config.roots):
                raise ValueError("Segment path is outside indexed roots")
            with self.lock:
                row = self.db.execute("""SELECT f.kind,f.mtime,f.size FROM files f JOIN chunks c ON c.path=f.path
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
            return {"path":str(output),"start_seconds":start,"end_seconds":end}

    def _scan(self, root):
        errors, paths = [], []
        if self.config.is_excluded(root):
            return paths, errors
        if not root.is_dir():
            return paths, [f"Root unavailable: {root}"]
        def onerror(error):
            errors.append(str(error))
        for folder, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
            self._check_cancelled()
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith(".")
                             and not (Path(folder) / d).is_symlink()
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

    def _media_chunks(self, path, info):
        if info.duration > self.config.max_media_seconds:
            raise ValueError(f"Media exceeds {self.config.max_media_seconds} seconds; skipped, not truncated")
        if kind(path) == "video" and self.config.enable_video and not info.has_video:
            raise ValueError("Video file has no video stream")
        if kind(path) == "audio" and not info.has_audio:
            raise ValueError("Audio file has no audio stream")
        windows = []
        if kind(path) == "video" and self.config.enable_video:
            windows.extend((start, end, "video") for start, end in media.segments(info.video_duration or info.duration, self.config.video_segment_seconds))
        if self.config.enable_audio and info.has_audio:
            windows.extend((start, end, "audio") for start, end in media.segments(info.audio_duration or info.duration, self.config.audio_segment_seconds, self.config.audio_overlap_seconds))
        if not windows: raise ValueError('No enabled media stream in this file')
        self.progress["current_chunks_total"] = len(windows)
        chunks, poster = [], None
        for start, end, modality in windows:
            self._check_included(path)
            if modality == "video":
                frames = media.video_frames(path, start, end, self.config)
                vector = self.engine.video(frames)
                if poster is None:
                    poster = media.jpeg(frames[len(frames)//2], self.config.thumbnail_px, self.config.thumbnail_quality)
            else:
                samples = media.audio_samples(path, start, end, self.config)
                vector = self.engine.audio(samples)
            self._check_included(path)
            snippet = f"{'Video scene' if modality == 'video' else 'Audio'} · {media.timestamp(start)}–{media.timestamp(end)}"
            chunks.append((snippet, normalize(vector, self.engine.dimensions).astype("<f4").tobytes(), start, end, modality))
            self.progress["current_chunks_done"] += 1
        return chunks, poster

    def index(self):
        if not self.index_lock.acquire(blocking=False):
            raise RuntimeError("An indexing pass is already running")
        summary = {"indexed": 0, "added": 0, "updated": 0, "unchanged": 0, "reused": 0, "deleted": 0, "excluded": 0, "errors": []}
        self.progress = {"total": 0, "processed": 0, "started_at": time.time(),
                         "finished_at": None, "errors": 0,
                         "current_chunks_done": 0, "current_chunks_total": 0}
        try:
            for root in self.config.roots:
                paths, scan_errors = self._scan(root)
                self.progress["total"] += len(paths)
                summary["errors"].extend(scan_errors)
                seen = {str(p) for p in paths}
                for path in paths:
                    self.progress["current_chunks_done"] = 0
                    self.progress["current_chunks_total"] = 0
                    try:
                        self._check_included(path)
                        stat = path.stat()
                        file_kind = kind(path)
                        is_media = file_kind in {"video", "audio"}
                        limit = self.config.max_media_file_mb if is_media else self.config.max_file_mb
                        if stat.st_size > limit * 1024 * 1024:
                            raise ValueError(f"Larger than {limit} MB limit")
                        with self.lock:
                            old = self.db.execute("SELECT * FROM files WHERE path=?", (str(path),)).fetchone()
                        if old and old["mtime"] == stat.st_mtime_ns and old["size"] == stat.st_size:
                            if old["kind"] == "image":
                                with self.lock:
                                    has_thumbnail = self.db.execute("SELECT 1 FROM thumbnails WHERE path=?", (str(path),)).fetchone()
                                if not has_thumbnail:
                                    jpeg = thumbnail(path, self.config.thumbnail_px, self.config.thumbnail_quality)
                                    with self.lock, self.db:
                                        self._check_included(path)
                                        self.db.execute("INSERT INTO thumbnails VALUES (?, ?)", (str(path), jpeg))
                            summary["unchanged"] += 1
                            continue
                        with path.open("rb") as source:
                            digest = hashlib.file_digest(source, "sha256").hexdigest()
                        info = media.probe(path, self.config) if is_media else None
                        if info and info.duration > self.config.max_media_seconds:
                            raise ValueError(f"Media exceeds {self.config.max_media_seconds} seconds; skipped, not truncated")
                        jpeg = None
                        with self.lock:
                            reusable = self.db.execute(
                                "SELECT path FROM files WHERE digest=? AND kind=? LIMIT 1",
                                (digest, kind(path))).fetchone()
                            saved = self.db.execute("SELECT snippet, vector, start_seconds, end_seconds, modality FROM chunks WHERE path=? ORDER BY position",
                                                    (reusable[0],)).fetchall() if reusable else []
                        if saved:
                            chunks = [(r["snippet"], r["vector"], r["start_seconds"], r["end_seconds"], r["modality"]) for r in saved]
                            if file_kind == "video":
                                with self.lock:
                                    reused_poster = self.db.execute("SELECT jpeg FROM thumbnails WHERE path=?", (reusable[0],)).fetchone()
                                jpeg = reused_poster[0] if reused_poster else None
                        elif is_media:
                            chunks, jpeg = self._media_chunks(path, info)
                        elif kind(path) == "image":
                            chunks = [("", self.engine.image(path).astype("<f4").tobytes(), None, None, None)]
                        else:
                            text = extract(path).strip()
                            if not text:
                                raise ValueError("No extractable text (scanned PDFs need OCR)")
                            if len(text) > self.config.max_document_chars:
                                raise ValueError(f"Document exceeds {self.config.max_document_chars} characters; skipped, not truncated")
                            parts = list(self.engine.chunks(text))
                            if len(parts) > self.config.max_document_chunks:
                                raise ValueError(f"Document exceeds {self.config.max_document_chunks} chunks; skipped, not truncated")
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
                                            (str(path), str(root), kind(path), stat.st_mtime_ns, stat.st_size, digest))
                            self.db.executemany("INSERT INTO chunks(path, position, snippet, vector, start_seconds, end_seconds, modality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                                [(str(path), i, *chunk) for i, chunk in enumerate(chunks)])
                            if info:
                                self.db.execute("INSERT INTO media_files VALUES (?, ?, ?)", (str(path), info.duration, int(info.has_audio)))
                            if jpeg:
                                self.db.execute("INSERT INTO thumbnails VALUES (?, ?)", (str(path), jpeg))
                        summary["reused" if saved else "indexed"] += 1
                        summary["updated" if old else "added"] += 1
                    except IndexCancelled:
                        raise
                    except IndexPathExcluded:
                        summary['excluded'] += 1
                    except Exception as error:
                        summary["errors"].append(f"{path}: {error}")
                    finally:
                        self.progress["processed"] += 1
                        self.progress["errors"] = len(summary["errors"])
                # Never prune if part of the tree could not be read.
                if not scan_errors:
                    self._check_cancelled()
                    with self.lock, self.db:
                        old_paths = self.db.execute("SELECT path FROM files WHERE root=?", (str(root),)).fetchall()
                        removed = [(r[0],) for r in old_paths if r[0] not in seen or self.config.is_excluded(r[0])]
                        self.db.executemany("DELETE FROM files WHERE path=?", removed)
                        summary["deleted"] += len(removed)
            return summary
        except IndexCancelled:
            summary['cancelled'] = True
            return summary
        finally:
            self.progress["finished_at"] = time.time()
            last = {key: value for key, value in summary.items() if key != "errors"}
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

    def search(self, query, file_kind="all", limit=20):
        start = time.perf_counter()
        vector = normalize(self.engine.query(query), self.engine.dimensions)
        with self.lock:
            rows = self.db.execute("""
                SELECT f.path, f.kind, c.snippet, c.vector, c.start_seconds, c.end_seconds, c.modality,
                       m.duration_seconds FROM chunks c JOIN files f ON f.path=c.path
                       LEFT JOIN media_files m ON m.path=f.path
                WHERE (?='all' OR f.kind=?)
            """, (file_kind, file_kind)).fetchall()
        # Exact cosine scan is intentional for the prototype; no DB server required.
        best = {}
        if rows:
            matrix = np.stack([np.frombuffer(r["vector"], dtype="<f4") for r in rows])
            scores = matrix @ vector
            for row, score in zip(rows, scores):
                path = row["path"]
                if path not in best or score > best[path]["score"]:
                    best[path] = {"path": path, "name": Path(path).name, "kind": row["kind"],
                                  "score": float(score), "snippet": row["snippet"][:1200],
                                  "start_seconds": row["start_seconds"], "end_seconds": row["end_seconds"],
                                  "matched_modality": row["modality"], "duration_seconds": row["duration_seconds"]}
        results = [r for r in sorted(best.values(), key=lambda r: -r["score"]) if not self.config.is_excluded(r['path']) and Path(r["path"]).is_file()][:limit]
        # Decode thumbnails only for returned results, not the whole image library.
        with self.lock:
            for result in results:
                row = self.db.execute("SELECT jpeg FROM thumbnails WHERE path=?", (result["path"],)).fetchone()
                result["thumbnail"] = "data:image/jpeg;base64," + base64.b64encode(row[0]).decode() if row else None
        return {"query": query, "results": results,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1)}

    def preview(self, filename, animate=False, start_seconds=0):
        path = Path(filename)
        with self.preview_lock:
            with self.lock:
                indexed = self.db.execute("SELECT path FROM files WHERE path=?", (str(path),)).fetchone()
            if not indexed or (path.suffix.lower() != ".pdf" and kind(path) not in {"image", "video"}):
                raise ValueError("Preview requires an indexed PDF, image or video")
            resolved = path.resolve()
            if resolved != path or not any(resolved.is_relative_to(root) for root in self.config.roots):
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
