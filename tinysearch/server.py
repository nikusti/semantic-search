from contextlib import asynccontextmanager, nullcontext
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal
from .worker import ManagedEngine
from .store import Store
import threading
from .settings import Settings


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    kind: Literal["all", "image", "document", "video", "audio"] = "all"
    limit: int = Field(default=20, ge=1, le=100)


class PreviewRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    animate: bool = False
    start_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)

class SettingsRequest(BaseModel):
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

class ModelSourceRequest(BaseModel):
    model_variant: Literal['mlx-8bit','custom','coreml-experimental']
    model_url: str = Field(default='', max_length=2048)

class ExclusionsRequest(BaseModel):
    excluded_paths: list[str] = Field(max_length=100)


def create_app(config, engine=None, request_stop=None):
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
    def index():
        # Explicit scans only; folders are selected in settings.
        owned = False
        result = None
        try:
            with operation_lock:
                require_model()
                if app.state.settings.job['state'] in {'downloading','applying','indexing','cancelling'} or getattr(app.state,'manual_index',False):
                    raise RuntimeError('Wait for model settings to finish')
                app.state.manual_index=True
                owned=True
                import time
                app.state.settings.job={'state':'indexing','started_at':time.time(),'error':None}
            with app.state.engine.pin() if hasattr(app.state.engine, "pin") else nullcontext():
                result=app.state.store.index()
                return result
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        finally:
            if owned:
                with operation_lock, app.state.settings.job_lock:
                    app.state.manual_index=False
                    app.state.settings.job.update(state='cancelled' if result and result.get('cancelled') else 'complete' if result is not None else 'error',finished_at=time.time())

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
            with operation_lock: return app.state.settings.apply(body.model_dump())
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @app.post('/v1/exclusions')
    def change_exclusions(body: ExclusionsRequest):
        try:
            with operation_lock: return app.state.settings.update_exclusions(body.excluded_paths)
        except ValueError as error: raise HTTPException(422,str(error)) from error
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

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
        return app.state.settings.cancel_index()

    @app.post("/v1/index/start")
    def start_index():
        try:
            with operation_lock:
                require_model()
                return app.state.settings.start_index()
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
