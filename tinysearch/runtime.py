"""Start the packaged service without a checkout, shell or system Python."""
import argparse
from dataclasses import replace
import fcntl
from pathlib import Path

from .config import Config
from .settings import CATALOG, configured
from .storage import APP_SUPPORT


def initial_config(storage, port=8766):
    storage = Path(storage).expanduser().resolve()
    destination = storage/'config.toml'
    if destination.exists():
        config = Config.load(destination)
        if config.storage_path != storage:
            raise ValueError('Stored configuration uses a different storage folder')
        return replace(config, port=port, ffmpeg='pyav', ffprobe='pyav', pdf_renderer='pdfium')
    entry = next(model for model in CATALOG if model.get('recommended'))
    config = Config((), storage/entry['path'], entry['revision'], storage/'data/index.sqlite3',
                    storage_path=storage, source_path=destination, port=port,
                    ffmpeg='pyav', ffprobe='pyav', pdf_renderer='pdfium')
    config = configured(config, dict(model_variant=entry['id'], dimensions=512,
        chunk_tokens=512, thumbnail_px=256, idle_seconds=60,
        enable_documents=True, enable_images=True, enable_audio=False, enable_video=False))
    storage.mkdir(parents=True,exist_ok=True)
    config.save()
    return config


def main():
    parser=argparse.ArgumentParser(description='Packaged Semantic Search service')
    parser.add_argument('--storage',type=Path,default=APP_SUPPORT)
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if not 1024 <= args.port <= 65535: parser.error('Invalid local port')
    args.storage.mkdir(parents=True,exist_ok=True)
    with (args.storage/'service.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        config=initial_config(args.storage,args.port)
        import uvicorn
        from .server import create_app
        server = None
        def stop():
            server.should_exit = True
        app = create_app(config, request_stop=stop)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, access_log=False))
        server.run()


if __name__ == '__main__': main()
