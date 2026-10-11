"""Catalog-backed downloads and isolated indexes for each vector space."""
from dataclasses import replace
from contextlib import nullcontext
import json
import os
from pathlib import Path
import threading
import time
from .worker import ManagedEngine
from .store import Store, purge_excluded
from .model_source import resolve_source, installed, ensure_downloaded
from .storage import APP_SUPPORT

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((Path(__file__).parent/'model_catalog.json').read_text())

def storage_root(config):
    return config.storage_path or APP_SUPPORT

def validated_exclusions(exclusions):
    folders = []
    for value in exclusions:
        path = Path(str(value).strip()).expanduser()
        if not path.is_absolute():
            raise ValueError('Excluded folders require an absolute path or ~/folder')
        path = path.resolve()
        if path.is_file():
            raise ValueError(f'Choose a folder, not a file: {path}')
        folders.append(path)
    return tuple(sorted(set(folders)))

def configured(config, values):
    values = dict(values)
    for key in ('enable_photos_library', 'photos_allow_network'):
        if values.get(key) is None:
            values.pop(key, None)
    roots = values.pop('roots', None)
    if roots is not None:
        selected = []
        for value in roots:
            path = Path(str(value).strip()).expanduser()
            if not path.is_absolute():
                raise ValueError('Choose an absolute folder path or ~/folder')
            path = path.resolve()
            if not path.is_dir():
                raise ValueError('Each selected folder must exist and be readable')
            if not os.access(path, os.R_OK | os.X_OK):
                raise ValueError('Allow access to the selected folder in macOS Privacy settings')
            selected.append(path)
        config = replace(config, roots=tuple(sorted(set(selected))))
    exclusions = values.pop('excluded_paths', None)
    if exclusions is None:
        exclusions = config.excluded_paths
    values['excluded_paths'] = validated_exclusions(exclusions)
    values = {"enable_video": config.enable_video, "enable_audio": config.enable_audio,
              "video_segment_seconds": config.video_segment_seconds,
              "audio_segment_seconds": config.audio_segment_seconds, **values}
    model_url = values.pop('model_url', config.model_url)
    root = storage_root(config)
    entry = resolve_source(root, CATALOG, values['model_variant'], model_url)
    values['model_variant'] = entry['id']
    values['model_url'] = entry.get('url', '')
    if entry['backend'] == 'coreml':
        values['chunk_tokens'] = min(values['chunk_tokens'], 480)
        values['audio_segment_seconds'] = min(values['audio_segment_seconds'], 10)
    capabilities = entry.get('capabilities', {'images':True, 'video':True, 'audio':True})
    if values.get('enable_photos_library', config.enable_photos_library) and not any(values.get(key, getattr(config, key)) for key in ('enable_images','enable_video','enable_audio')):
        raise ValueError('Enable Photographs and images, video or audio to index Apple Photos')
    for flag, capability in [('enable_images','images'),('enable_video','video'),('enable_audio','audio')]:
        if values.get(flag) and not capabilities[capability]:
            raise ValueError(f'This model does not support {capability}; turn off that content type')
    dimensions = values['dimensions']
    # Different embedding pipelines require separate databases.
    suffix = ''
    if not values['enable_images']: suffix += '-text'
    if not values['enable_documents']: suffix += '-photos'
    if values['chunk_tokens'] != 384: suffix += f"-chunk{values['chunk_tokens']}"
    if values['enable_video']: suffix += f"-video{values['video_segment_seconds']}x{config.video_frames}"
    if values['enable_audio']: suffix += f"-audio{values['audio_segment_seconds']}o{config.audio_overlap_seconds}"
    if config.roots != ((Path.home()/'Downloads').resolve(),):
        import hashlib
        signature=hashlib.sha256('\n'.join(sorted(str(p) for p in config.roots)).encode()).hexdigest()[:12]
        suffix += f'-roots{signature}'
    model_key = entry['id'] + ('-' + entry['storage_key'] if entry.get('storage_key') else '')
    name = f"downloads-{model_key}-{dimensions}{suffix}.sqlite3"
    if entry['id']=='reference' and dimensions==768 and not suffix: name='downloads.sqlite3'
    maximum = 512 if entry['backend'] == 'coreml' else max(512, values['chunk_tokens']+32)
    if entry['backend'] == 'coreml':
        values['video_frames'] = min(config.video_frames, 3)
    return replace(config, **values, storage_path=root, max_sequence_tokens=maximum, model_path=root/entry['path'],model_revision=entry['revision'],pipeline_revision=entry.get('pipeline_revision',''),backend=entry['backend'],database=root/'data'/name)

def downloaded(entry, root=None):
    return installed(root or APP_SUPPORT, entry)

class Settings:
    def __init__(self,app,lock):
        self.app,self.lock=app,lock
        self.job={'state':'idle'}
        self.job_lock=threading.Lock()
        self.thread = None
        self.stopping = threading.Event()
        from .index_reuse import historical_profiles
        saved = self.storage/'model-profiles.json'
        self.profiles = json.loads(saved.read_text()) if saved.is_file() else historical_profiles(app.state.store.config, CATALOG)

    @property
    def storage(self):
        return storage_root(self.app.state.store.config)

    def status(self):
        config=self.app.state.store.config
        models=[]
        entries = list(CATALOG)
        if config.model_variant == 'custom':
            entries.append(resolve_source(self.storage, CATALOG, 'custom', config.model_url))
        for raw in entries:
            entry = resolve_source(self.storage, CATALOG, raw['id'], config.model_url)
            if entry.get('experimental') and config.model_variant != entry['id'] and not downloaded(entry, self.storage):
                continue
            path=self.storage/entry['path']
            models.append({**entry,'installed':downloaded(entry,self.storage),'disk_bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0})
        import shutil
        current = {key:getattr(config,key) for key in ('model_variant','dimensions','enable_documents','enable_images','enable_video','enable_audio','video_segment_seconds','audio_segment_seconds','thumbnail_px','chunk_tokens','idle_seconds','enable_photos_library','photos_allow_network')}
        current['excluded_paths'] = [str(p) for p in config.excluded_paths]
        current['roots'] = [str(p) for p in config.roots]
        current['model_url'] = config.model_url
        from .media import decoder_available
        return {'models':models,'current':current,'profiles':dict(self.profiles),'job':dict(self.job),'storage_path':str(self.storage),'video_supported':True,'audio_supported':True,'media_decoder_available':decoder_available(config),'storage_by_kind':self.app.state.store.storage_by_kind()}

    def _start(self, target, *args):
        if self.stopping.is_set():
            raise RuntimeError('The engine is shutting down')
        self.thread = threading.Thread(target=target, args=args, daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stopping.set()
        if self.thread and self.thread.is_alive():
            self.app.state.store.cancel_index()
            self.thread.join(timeout=180)
            return not self.thread.is_alive()
        return True

    def apply(self,values):
        current = self.app.state.store.config
        # Exclusions do not change the model or its vector space. Save them
        # in place even while the existing indexing worker is running.
        unchanged = all(
            value is None or key == 'excluded_paths' or
            (key == 'roots' and tuple(sorted(Path(p).expanduser().resolve() for p in value)) == tuple(sorted(current.roots))) or
            (key == 'model_url' and current.model_variant != 'custom') or
            value == getattr(current, key)
            for key, value in values.items())
        if unchanged and values.get('excluded_paths') is not None and validated_exclusions(values['excluded_paths']) != current.excluded_paths:
            return self.update_exclusions(values['excluded_paths'])
        if not any(values.get(key,False) for key in ('enable_documents','enable_images','enable_video','enable_audio')): raise ValueError('Select at least one content type')
        config=configured(self.app.state.store.config,values)
        if not config.roots and not config.enable_photos_library:
            raise ValueError('Choose at least one folder to index')
        if config.enable_video or config.enable_audio:
            from .media import decoder_available
            if not decoder_available(config):
                raise ValueError('The media decoder is unavailable. Reinstall the packaged extension.')
        if not config.source_path: raise ValueError('A saved config is required')
        with self.job_lock:
            if self.job['state'] in {'downloading','applying'}: raise RuntimeError('A settings operation is already running')
            if self.job['state'] in {'indexing','cancelling'} or self.app.state.store.index_lock.locked() or getattr(self.app.state,'manual_index',False): raise RuntimeError('Cancel indexing or wait for it to finish before switching models')
            entry = resolve_source(self.storage, CATALOG, config.model_variant, config.model_url)
            self.job={'state':'applying' if downloaded(entry,self.storage) else 'downloading','started_at':time.time(),'error':None}
            self._start(self._run, config)
        return dict(self.job)

    def update_exclusions(self, paths):
        with self.job_lock:
            if self.job['state'] in {'downloading', 'applying'}:
                raise RuntimeError('Wait for model settings to finish before saving exclusions')
            store = self.app.state.store
            config = replace(store.config, excluded_paths=validated_exclusions(paths))
            if not config.source_path:
                raise ValueError('A saved config is required')
            removed = store.update_exclusions(config)
            saved_removed = 0
            cleanup_pending = 0
            import sqlite3
            for path in sorted(config.database.parent.glob('*.sqlite3')):
                if path.resolve() == config.database.resolve() or path.is_symlink():
                    continue
                db = None
                try:
                    db = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=1)
                    db.execute('PRAGMA foreign_keys=ON')
                    saved_removed += purge_excluded(db, config)
                except (sqlite3.Error, OSError):
                    # An unavailable old index must not undo a live exclusion
                    # or prevent cleanup of subsequent healthy indexes.
                    cleanup_pending += 1
                finally:
                    if db: db.close()
            self.job.update(excluded_files_removed=removed, excluded_saved_entries_removed=saved_removed,
                            exclusion_cleanup_pending=cleanup_pending)
            if self.job['state'] != 'indexing' and not store.index_lock.locked() and not getattr(self.app.state, 'manual_index', False):
                self.job.update(state='complete', error=None, finished_at=time.time())
            return {'state':'saved', 'excluded_paths':[str(p) for p in config.excluded_paths],
                    'excluded_files_removed':removed, 'excluded_saved_entries_removed':saved_removed,
                    'cleanup_pending':cleanup_pending, 'indexing':store.index_lock.locked()}

    def inspect_model(self, variant, url=''):
        entry = resolve_source(self.storage, CATALOG, variant, url)
        path = self.storage / entry['path']
        return {**entry, 'installed':downloaded(entry,self.storage),
                'disk_bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0}

    def download_model(self, variant, url=''):
        entry = resolve_source(self.storage, CATALOG, variant, url)
        with self.job_lock:
            if self.job['state'] in {'downloading','applying','indexing','cancelling'} or self.app.state.store.index_lock.locked() or getattr(self.app.state,'manual_index',False):
                raise RuntimeError('Wait for the current operation to finish')
            if downloaded(entry,self.storage):
                return {'state':'downloaded','model':entry['id']}
            self.job={'state':'downloading','started_at':time.time(),'error':None,'model':entry['id']}
            self._start(self._download_only, entry)
            return dict(self.job)

    def _download_only(self, entry):
        try:
            ensure_downloaded(self.storage, entry)
            self.job.update(state='downloaded', finished_at=time.time())
        except Exception as error:
            self.job.update(state='error',error=str(error),finished_at=time.time())

    def delete_model(self, variant, url=''):
        import shutil
        entry = resolve_source(self.storage, CATALOG, variant, url)
        raw_path = self.storage / entry['path']
        path = raw_path.resolve()
        if raw_path.is_symlink() or path.parent != (self.storage/'models').resolve():
            raise ValueError('Only model folders in shared storage can be deleted')
        with self.job_lock, self.lock:
            if self.job['state'] in {'downloading','applying','indexing','cancelling'} or self.app.state.store.index_lock.locked() or getattr(self.app.state,'manual_index',False):
                raise RuntimeError('Wait for the current operation to finish')
            if not path.exists():
                return {'state':'deleted','freed_bytes':0}
            active = path == self.app.state.store.config.model_path.resolve()
            if active:
                # Close both main/audio workers and pending activation tasks.
                self.app.state.engine.close()
                fresh = ManagedEngine(self.app.state.store.config)
                self.app.state.engine = self.app.state.store.engine = fresh
            size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())
            try:
                shutil.rmtree(path)
            except OSError as error:
                self.job={'state':'error','error':f'Could not remove model: {error}'}
                raise RuntimeError(self.job['error']) from error
            self.job={'state':'deleted','model':entry['id'],'active_model_removed':active,'freed_bytes':size,'finished_at':time.time()}
            return dict(self.job)

    def start_index(self):
        if not self.app.state.store.config.roots and not self.app.state.store.config.enable_photos_library:
            raise ValueError("Choose folders in Model Settings before updating the index")
        with self.job_lock:
            if self.job['state'] in {'downloading','applying','indexing','cancelling'} or getattr(self.app.state,'manual_index',False) or self.app.state.store.index_lock.locked():
                raise RuntimeError('An indexing or settings operation is already running')
            self.job={'state':'indexing','started_at':time.time(),'error':None}
            self._start(self._index_current)
        return dict(self.job)

    def cancel_index(self):
        with self.job_lock:
            if self.job['state'] not in {'indexing','cancelling'} and not self.app.state.store.index_lock.locked():
                return {'state':'idle'}
            self.app.state.store.cancel_index()
            self.job.update(state='cancelling')
            return dict(self.job)

    def _index_current(self):
        try:
            with self.app.state.engine.pin() if hasattr(self.app.state.engine,'pin') else nullcontext(): result=self.app.state.store.index()
            report=self.storage/'data'/'reports'/f'index-{time.time_ns()}.json'
            report.parent.mkdir(parents=True,exist_ok=True)
            report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            self.job.update(state='cancelled' if result.get('cancelled') else 'complete',finished_at=time.time(),index_errors=len(result['errors']),
                            report_path=str(report),storage_by_kind=self.app.state.store.storage_by_kind())
        except Exception as error:
            self.job.update(state='error',error=str(error),finished_at=time.time())

    def _run(self,config):
        candidate_engine=candidate_store=None
        try:
            entry=resolve_source(storage_root(config), CATALOG, config.model_variant, config.model_url)
            if config.backend == 'coreml':
                from .coreml_assets import ensure_assets
                ensure_assets(config)
            else:
                ensure_downloaded(storage_root(config), entry)
            if self.stopping.is_set():
                raise RuntimeError('The engine is shutting down')
            self.job.update(state='applying')
            new_index = not config.database.exists()
            candidate_engine=ManagedEngine(config)
            # A newly downloaded custom conversion must actually load and encode
            # before it can replace the working model or touch the active index.
            if config.model_variant == 'custom':
                candidate_engine.query('model compatibility check')
                candidate_engine.unload()
            with self.lock:
                candidate_store=Store(config,candidate_engine)
                from .index_reuse import reuse_saved
                self.job["reused_files"] = reuse_saved(config, candidate_store.db)
            # Only thumbnails change; embedding vectors remain intact.
            row=candidate_store.db.execute("SELECT value FROM meta WHERE key='thumbnail_px'").fetchone()
            prior=int(row[0]) if row else 256
            if prior != config.thumbnail_px:
                from .extract import thumbnail
                for row in candidate_store.db.execute("SELECT path FROM files WHERE kind='image'").fetchall():
                    if row[0].startswith('photos://asset/'):
                        from io import BytesIO
                        from PIL import Image
                        saved = candidate_store.db.execute('SELECT jpeg FROM thumbnails WHERE path=?', (row[0],)).fetchone()
                        if not saved:
                            continue  # Repaired by the next Photos pass without re-encoding.
                        image = Image.open(BytesIO(saved[0])).convert('RGB')
                        image.thumbnail((config.thumbnail_px, config.thumbnail_px))
                        output = BytesIO(); image.save(output, format='JPEG', quality=config.thumbnail_quality)
                        jpeg = output.getvalue()
                    else:
                        jpeg=thumbnail(Path(row[0]),config.thumbnail_px,config.thumbnail_quality)
                    candidate_store.db.execute('INSERT OR REPLACE INTO thumbnails VALUES (?,?)',(row[0],jpeg))
                candidate_store.db.commit()
            candidate_store.db.execute("INSERT OR REPLACE INTO meta VALUES ('thumbnail_px',?)",(str(config.thumbnail_px),))
            candidate_store.db.commit()
            with self.lock:
                if self.stopping.is_set():
                    raise RuntimeError('The engine is shutting down')
                if self.app.state.store.index_lock.locked(): raise RuntimeError('Indexing is still running')
                config.save()
                from .index_reuse import profile
                self.profiles[config.model_variant] = profile(config)
                saved = self.storage / "model-profiles.json"
                temporary = saved.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.profiles, indent=2))
                temporary.replace(saved)
                self.job['excluded_files_removed'] = candidate_store.excluded_removed
                old_engine,old_store=self.app.state.engine,self.app.state.store
                self.app.state.engine,self.app.state.store=candidate_engine,candidate_store
                candidate_engine=candidate_store=None
                old_engine.close();old_store.close()
            # Exclusions apply across saved vector sizes and pipelines as well.
            # Only this application's verified SQLite indexes are touched.
            if config.excluded_paths:
                import sqlite3
                removed = 0
                for path in sorted(config.database.parent.glob('*.sqlite3')):
                    if path.resolve() == config.database.resolve() or path.is_symlink():
                        continue
                    db = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True)
                    try:
                        db.execute('PRAGMA foreign_keys=ON')
                        removed += purge_excluded(db, config)
                    finally:
                        db.close()
                self.job['excluded_saved_entries_removed'] = removed
            # Scan when the selected pipeline needs a new index.
            if new_index or self.app.state.store.status()['files']==0:
                if self.stopping.is_set():
                    raise RuntimeError('The engine is shutting down')
                self.job.update(state='indexing')
                with self.app.state.engine.pin(): result=self.app.state.store.index()
                self.job['index_errors']=len(result['errors'])
                self.job['cancelled']=bool(result.get('cancelled'))
                report=self.storage/'data'/'reports'/f'index-{time.time_ns()}.json'
                report.parent.mkdir(parents=True,exist_ok=True)
                report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
                self.job['report_path']=str(report)
                self.job['storage_by_kind']=self.app.state.store.storage_by_kind()
            self.job.update(state='cancelled' if self.job.get('cancelled') else 'complete',finished_at=time.time())
        except Exception as error:
            self.job.update(state='error',error=str(error),finished_at=time.time())
        finally:
            if candidate_store: candidate_store.close()
            if candidate_engine: candidate_engine.close()
