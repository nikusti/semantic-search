from contextlib import asynccontextmanager, nullcontext
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal
from .worker import ManagedEngine
from .store import Store
import threading
import secrets
from .auth import TOKEN_HEADER, load_or_create_token, token_path
from .settings import Settings


class LauncherOwner(BaseModel):
    pid: int = Field(gt=1)
    started: str = Field(min_length=1,max_length=128)
    executable: str = Field(min_length=1,max_length=4096)
    host: Literal['Tinycast','Raycast']
    extension_path: str = Field(min_length=1,max_length=4096)
    extension_marker: str = Field(min_length=1,max_length=128)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    kind: Literal["all", "image", "document", "video", "audio"] = "all"
    limit: int = Field(default=20, ge=1, le=100)


class PreviewRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    animate: bool = False
    start_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)

class SettingsRequest(BaseModel):
    launcher: LauncherOwner | None = None
    model_variant: Literal['reference','mlx-bf16','mlx-8bit','mlx-4bit','custom','coreml-experimental']
    model_url: str = Field(default='',max_length=2048)
    dimensions: Literal[128,256,512,768] = 768
    enable_documents: bool = True
    enable_images: bool = True
    enable_video: bool = False
    enable_audio: bool = False
    video_segment_seconds: Literal[5,10,20,30] = 10
    audio_segment_seconds: Literal[10,20,30] = 20
    thumbnail_px: Literal[128,256,384] = 256
    chunk_tokens: Literal[256,384,480,512] = 384
    idle_seconds: Literal[60,300] = 60
    excluded_paths: list[str] | None = Field(default=None, max_length=100)
    roots: list[str] | None = Field(default=None, max_length=100)
    enable_photos_library: bool | None = None
    photos_allow_network: bool | None = None


class SegmentRequest(BaseModel):
    path: str = Field(min_length=1,max_length=4096)
    start_seconds: float = Field(ge=0,le=86400,allow_inf_nan=False)
    end_seconds: float = Field(gt=0,le=86400,allow_inf_nan=False)

class PhotoRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)

class ModelSourceRequest(BaseModel):
    model_variant: Literal['mlx-8bit','custom','coreml-experimental']
    model_url: str = Field(default='', max_length=2048)

class ExclusionsRequest(BaseModel):
    excluded_paths: list[str] = Field(max_length=100)

class IndexRequest(BaseModel):
    retry_skipped: bool = False
    launcher: LauncherOwner | None = None

class AutomaticRequest(BaseModel):
    enabled: bool
    interval_seconds: Literal[1800,3600,10800,21600,86400] = 3600
    only_on_power: bool = True
    launcher: LauncherOwner | None = None

class AutomaticTick(BaseModel):
    launcher: LauncherOwner

class FolderAddRequest(BaseModel):
    paths: list[str] = Field(min_length=1,max_length=100)

class FolderRemoveRequest(BaseModel):
    path: str = Field(min_length=1,max_length=4096)
    delete_saved_index: bool = False


def create_app(config, engine=None, request_stop=None):
    auth_token = load_or_create_token(config)
    operation_lock = threading.RLock()
    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = engine or ManagedEngine(config)
        app.state.store = Store(config, app.state.engine)
        app.state.settings = Settings(app, operation_lock)
        try:
            yield
        finally:
            if app.state.settings.shutdown():
                app.state.store.close()
                if hasattr(app.state.engine, "close"):
                    app.state.engine.close()

    app = FastAPI(title="Semantic Search", lifespan=lifespan)
    app.state.auth_token_path = token_path(config)

    def launcher_owner(body):
        owner = body.launcher.model_dump() if body and body.launcher else None
        if owner is not None and not app.state.settings.automatic.probe(owner):
            raise HTTPException(422,'The launcher or extension is no longer running')
        return owner

    def require_model():
        config = app.state.store.config
        available = all((config.model_path/name).is_file() for name in ('config.json','tokenizer.json'))
        available = available and any(config.model_path.glob('model*.safetensors'))
        if config.backend == 'coreml':
            from .coreml_assets import ready
            available = ready(config.model_path)
        if config.model_variant == 'custom':
            available = available and (config.model_path/'.validated').is_file()
        if isinstance(app.state.engine, ManagedEngine) and not available:
            raise HTTPException(409, 'Selected model is not downloaded. Download it in Model Settings.')

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        from starlette.responses import JSONResponse
        # No CORS. Reject browser-originated requests and DNS rebinding.
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"} or request.headers.get("origin"):
            return JSONResponse({"detail": "Local launcher/CLI access only"}, status_code=403)
        # Only the minimal identity/version probe is public so launchers can
        # discover and upgrade an older engine. Library data and all actions
        # require the per-install credential, including newly added routes.
        if not (request.method == 'GET' and request.url.path == '/v1/info'):
            provided = request.headers.get(TOKEN_HEADER, '')
            if not secrets.compare_digest(provided.encode('utf-8'), auth_token.encode('ascii')):
                return JSONResponse({"detail": "Engine authentication required"}, status_code=401)
        return await call_next(request)

    @app.get("/v1/status")
    def status():
        with operation_lock: return app.state.store.status()

    @app.get('/v1/info')
    def info():
        from . import __version__
        return {'application': 'semantic-search', 'version': __version__, 'protocol': 1}

    @app.post('/v1/engine/stop')
    def stop_engine():
        # Used for explicit runtime updates. Never terminate active work or
        # signal unrelated processes; uvicorn performs normal lifespan cleanup.
        with operation_lock, app.state.settings.job_lock:
            settings = app.state.settings
            if request_stop is None:
                raise HTTPException(409, 'This service is managed externally')
            if (getattr(app.state, 'manual_index', False) or
                    settings.job['state'] in {'downloading', 'applying', 'indexing', 'cancelling'} or
                    (settings.thread and settings.thread.is_alive())):
                raise HTTPException(409, 'Cancel indexing or wait for current work before updating the engine')
            settings.stopping.set()
            request_stop()
            return {'stopping': True}

    @app.post("/v1/search")
    def search(body: SearchRequest):
        if not body.query.strip():
            raise HTTPException(422, "Query is empty")
        with operation_lock:
            require_model()
            return app.state.store.search(body.query.strip(), body.kind, body.limit)

    @app.post("/v1/index")
    def index(body: IndexRequest | None = None):
        # Explicit scans only; folders are selected in settings.
        owned = False
        result = None
        try:
            with operation_lock:
                require_model()
                owner = launcher_owner(body)
                if app.state.settings.job['state'] in {'downloading','applying','indexing','cancelling'} or getattr(app.state,'manual_index',False):
                    raise RuntimeError('Wait for model settings to finish')
                app.state.manual_index=True
                owned=True
                import time
                app.state.settings.job={'state':'indexing','started_at':time.time(),'error':None,'origin':'manual'}
                job = app.state.settings.job
            with app.state.settings.automatic.guard(owner,False,job):
                with app.state.engine.pin() if hasattr(app.state.engine, "pin") else nullcontext():
                    result=app.state.store.index(retry_skipped=True) if body and body.retry_skipped else app.state.store.index()
                    return result
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        finally:
            if owned:
                with operation_lock:
                    app.state.settings.automatic.completed(job,result)
                    with app.state.settings.job_lock:
                        app.state.manual_index=False
                        job.update(state='paused' if result and result.get('paused') else 'cancelled' if result and result.get('cancelled') else 'complete' if result is not None else 'error',finished_at=time.time())

    @app.post("/v1/session")
    def session():
        with operation_lock:
            require_model()
            return app.state.engine.activate() if hasattr(app.state.engine, "activate") else {"state": "ready"}

    @app.get('/v1/settings')
    def settings():
        with operation_lock: return app.state.settings.status()

    @app.post('/v1/settings')
    def change_settings(body: SettingsRequest):
        try:
            with operation_lock: return app.state.settings.apply(body.model_dump(exclude={'launcher'}), owner=launcher_owner(body))
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @app.post('/v1/exclusions')
    def change_exclusions(body: ExclusionsRequest):
        try:
            with operation_lock: return app.state.settings.update_exclusions(body.excluded_paths)
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @app.post('/v1/folders/add')
    def add_folders(body: FolderAddRequest):
        try:
            with operation_lock: return app.state.settings.add_folders(body.paths)
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @app.post('/v1/folders/remove')
    def remove_folder(body: FolderRemoveRequest):
        try:
            with operation_lock: return app.state.settings.remove_folder(body.path,body.delete_saved_index)
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @app.post('/v1/folders/cleanup')
    def cleanup_folders():
        with operation_lock:
            if app.state.settings.job['state'] in {'downloading','applying'}:
                raise HTTPException(409,'Wait for model setup before cleanup')
            return app.state.settings.cleanup_folders()

    @app.post('/v1/photos/connect')
    def connect_photos():
        from .photos import PhotoLibrary
        try:
            with PhotoLibrary() as library:
                result = library.request('authorize')
                if result['authorization'] in {'authorized', 'limited'}:
                    result.update(library.request('count'))
                return result
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    def photo_action(body, action):
        with operation_lock:
            store = app.state.store
            if not store.preview_lock.acquire(blocking=False):
                raise HTTPException(409, 'Another Photos operation is being prepared; retry shortly')
        try:
            return getattr(store, action)(body.path)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error
        finally: store.preview_lock.release()

    @app.post('/v1/photos/details')
    def photo_details(body: PhotoRequest):
        return photo_action(body, 'photos_details')

    @app.post('/v1/photos/copy')
    def copy_photo(body: PhotoRequest):
        return photo_action(body, 'copy_photo')

    @app.post('/v1/models/inspect')
    def inspect_model(body: ModelSourceRequest):
        try:
            return app.state.settings.inspect_model(body.model_variant, body.model_url)
        except ValueError as error: raise HTTPException(422,str(error)) from error

    @app.post('/v1/models/download')
    def download_model(body: ModelSourceRequest):
        try:
            with operation_lock:
                return app.state.settings.download_model(body.model_variant, body.model_url)
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @app.post('/v1/models/delete')
    def delete_model(body: ModelSourceRequest):
        try:
            with operation_lock:
                return app.state.settings.delete_model(body.model_variant, body.model_url)
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @app.post("/v1/unload")
    def unload():
        try:
            with operation_lock:
                return app.state.engine.unload() if hasattr(app.state.engine, "unload") else {"state": "ready"}
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/v1/segment")
    def segment(body: SegmentRequest):
        try:
            if body.path.startswith('photos://asset/'):
                with operation_lock:
                    store = app.state.store
                    if not store.preview_lock.acquire(blocking=False):
                        raise HTTPException(409, 'Another Photos preview is being prepared; retry shortly')
                try:
                    return store.segment(body.path, body.start_seconds, body.end_seconds)
                finally:
                    store.preview_lock.release()
            with operation_lock: return app.state.store.segment(body.path,body.start_seconds,body.end_seconds)
        except HTTPException:
            raise
        except (ValueError,FileNotFoundError):
            raise HTTPException(404,"Indexed segment unavailable") from None
        except Exception:
            raise HTTPException(503,"Could not prepare the media segment; open the original file") from None

    @app.post("/v1/index/cancel")
    def cancel_index():
        with operation_lock:
            return app.state.settings.cancel_index()

    @app.post("/v1/index/start")
    def start_index(body: IndexRequest | None = None):
        try:
            with operation_lock:
                require_model()
                return app.state.settings.start_index(retry_skipped=bool(body and body.retry_skipped), owner=launcher_owner(body))
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409,str(error)) from error

    @app.get('/v1/automatic')
    def automatic_status():
        return app.state.settings.automatic.status()

    @app.post('/v1/automatic')
    def configure_automatic(body: AutomaticRequest):
        try:
            with operation_lock:
                return app.state.settings.automatic.configure(body.enabled,body.interval_seconds,body.only_on_power,launcher_owner(body))
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @app.post('/v1/automatic/tick')
    def automatic_tick(body: AutomaticTick):
        try:
            with operation_lock:
                scheduler = app.state.settings.automatic
                if scheduler.status()['enabled']:
                    try:
                        require_model()
                    except HTTPException:
                        return scheduler.not_ready('Download the selected model in Manage Library')
                return scheduler.tick(launcher_owner(body))
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @app.post('/v1/index/pause')
    def pause_index():
        with operation_lock:
            return app.state.settings.automatic.pause()

    @app.post('/v1/index/resume')
    def resume_index(body: IndexRequest | None = None):
        try:
            with operation_lock:
                require_model()
                return app.state.settings.automatic.resume(launcher_owner(body))
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409,str(error)) from error

    @app.post("/v1/preview")
    def preview(body: PreviewRequest):
        try:
            if body.path.startswith('photos://asset/'):
                with operation_lock:
                    store = app.state.store
                    # Reserve its lifetime while settings can replace the store.
                    if not store.preview_lock.acquire(blocking=False):
                        return store.photos_thumbnail(body.path)
                try:
                    # The preview lock is reentrant for this wrapper. Network
                    # requests must not hold the model/search operation lock.
                    return store.preview(body.path, animate=body.animate, start_seconds=body.start_seconds)
                finally:
                    store.preview_lock.release()
            with operation_lock: return app.state.store.preview(body.path, animate=body.animate, start_seconds=body.start_seconds)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Preview unavailable") from None
        except Exception:
            raise HTTPException(503, "Preview unavailable; open the original file") from None

    return app
