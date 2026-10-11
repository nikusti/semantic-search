"""Reuse unchanged vectors only within the same model revision and text pipeline."""
import json
from pathlib import Path
import re
import sqlite3
from .engine import engine_identity
from . import photos

def text_identity(identity):
    return identity.split(':media-v1:')[0].replace(':text-only', '')

def _media(identity, modality):
    match = re.search(rf':{modality}([01])-([0-9]+)-([0-9]+)', identity)
    return tuple(map(int, match.groups())) if match else (0, 10 if modality == 'video' else 20, 3 if modality == 'video' else 2)

def reuse_saved(config, target):
    """Merge missing paths; never overwrite newer destination entries or foreign vectors."""
    identity = engine_identity(config)
    reused = 0
    for path in sorted(config.database.parent.glob('*.sqlite3')):
        if path.resolve() == config.database.resolve() or path.is_symlink(): continue
        old = None
        try:
            old = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
            old.row_factory = sqlite3.Row
            row = old.execute("SELECT value FROM meta WHERE key='engine'").fetchone()
            if not row or text_identity(row[0]) != text_identity(identity): continue
            prior = row[0]
            for file in old.execute('SELECT * FROM files').fetchall():
                filename, kind = file['path'], file['kind']
                is_photo = file['root'] == photos.ROOT
                if is_photo:
                    if not config.enable_photos_library: continue
                    if not old.execute("SELECT 1 FROM sqlite_master WHERE name='photos_assets'").fetchone(): continue
                    photo = old.execute('SELECT * FROM photos_assets WHERE path=?', (filename,)).fetchone()
                    if not photo: continue
                elif config.is_excluded(filename) or file['root'] not in {str(root) for root in config.roots}: continue
                if not {'document':config.enable_documents, 'image':config.enable_images, 'video':config.enable_video or config.enable_audio, 'audio':config.enable_audio}.get(kind): continue
                if target.execute('SELECT 1 FROM files WHERE path=?', (filename,)).fetchone(): continue
                if kind in {'video', 'audio'}:
                    required = ['video'] if kind == 'video' and config.enable_video else []
                    if config.enable_audio: required.append('audio')
                    if any(_media(prior, m) != _media(identity, m) for m in required): continue
                chunks = old.execute('SELECT position,snippet,vector,start_seconds,end_seconds,modality FROM chunks WHERE path=? ORDER BY position', (filename,)).fetchall()
                chunks = [c for c in chunks if c['modality'] != 'audio' or config.enable_audio]
                chunks = [c for c in chunks if c['modality'] != 'video' or config.enable_video]
                if not chunks or any(len(c['vector']) != config.dimensions * 4 for c in chunks): continue
                with target:
                    target.execute('INSERT INTO files VALUES (?,?,?,?,?,?)', tuple(file))
                    if is_photo:
                        target.execute('INSERT INTO photos_assets VALUES (?,?,?,?)', tuple(photo))
                    target.executemany('INSERT INTO chunks (path,position,snippet,vector,start_seconds,end_seconds,modality) VALUES (?,?,?,?,?,?,?)', [(filename,*tuple(c)) for c in chunks])
                    for table, columns in [('thumbnails',2),('media_files',3)]:
                        cached = old.execute(f'SELECT * FROM {table} WHERE path=?', (filename,)).fetchone()
                        if cached: target.execute(f'INSERT OR IGNORE INTO {table} VALUES ({",".join("?" * columns)})', tuple(cached))
                reused += 1
        except sqlite3.Error:
            # Old or unrelated SQLite files are not valid sources for reuse.
            continue
        finally:
            if old: old.close()
    return reused

PROFILE_FIELDS = ('model_variant','model_url','dimensions','enable_documents','enable_images','enable_video','enable_audio','video_segment_seconds','audio_segment_seconds','thumbnail_px','chunk_tokens','idle_seconds')

def profile(config):
    return {key:getattr(config,key) for key in PROFILE_FIELDS}

def historical_profiles(config, catalog):
    """Recover pre-profile model settings from the largest saved, identified index."""
    profiles = {}
    scores = {}
    for path in config.database.parent.glob('*.sqlite3'):
        db = None
        try:
            db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
            row = db.execute("SELECT value FROM meta WHERE key='engine'").fetchone()
            if not row: continue
            identity = row[0]
            model = next((entry for entry in catalog if f':{entry["backend"]}:{entry["id"]}' in identity and f':{entry["revision"]}:' in identity), None)
            if not model: continue
            count = (db.execute('SELECT count(*) FROM files').fetchone()[0], db.execute('SELECT count(*) FROM chunks').fetchone()[0])
            if count <= scores.get(model['id'], (-1,-1)): continue
            values = profile(config)
            values.update(model_variant=model['id'], model_url='', dimensions=int(identity.split(':')[2]),
                          enable_images=':text-only' not in identity, chunk_tokens=384,
                          enable_documents='-photos' not in path.stem)
            chunk = re.search(r':chunk([0-9]+):', identity)
            if chunk: values['chunk_tokens'] = int(chunk[1])
            for kind in ['video','audio']:
                enabled, seconds, _ = _media(identity, kind)
                values['enable_' + kind] = bool(enabled)
                values[kind + '_segment_seconds'] = seconds
            profiles[model['id']] = values; scores[model['id']] = count
        except (sqlite3.Error, ValueError, IndexError): continue
        finally:
            if db: db.close()
    return profiles
