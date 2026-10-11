"""Lightweight parent; terminate the inference child after inactivity."""
from contextlib import contextmanager
from dataclasses import replace
import multiprocessing as mp
import threading
import time
from .engine import engine_identity


def worker_main(connection, config, audio_only=False, vision=False):
    engine = None
    try:
        # Content selection belongs to the index identity, not residency.
        # A text query searches saved vectors of every enabled content type.
        load_config = config if vision else replace(config, enable_images=False, enable_video=False)
        if config.backend == "mlx":
            from .mlx_engine import MLXEngine
            engine = MLXEngine(load_config, audio_only=audio_only)
        elif config.backend == "coreml":
            from .coreml_engine import CoreMLEngine
            engine = CoreMLEngine(load_config, audio_only=audio_only)
        else:
            from .engine import GemmaEngine
            engine = GemmaEngine(load_config)
        connection.send({"ok": True, "metrics": engine.metrics()})
        while True:
            command, argument = connection.recv()
            if command == "close":
                break
            try:
                result = getattr(engine, command)(argument)
                if command == "chunks":
                    result = list(result)
                connection.send({"ok": True, "result": result, "metrics": engine.metrics()})
            except Exception as error:
                connection.send({"ok": False, "error": str(error)})
    except EOFError:
        pass
    except Exception as error:
        try:
            connection.send({"ok": False, "error": str(error)})
        except (BrokenPipeError, EOFError):
            pass
    finally:
        if config.backend == "coreml" and engine is not None:
            engine.close()
        connection.close()


def audio_worker_main(connection, config):
    worker_main(connection, config, audio_only=True)


def vision_worker_main(connection, config):
    worker_main(connection, config, vision=True)


class ManagedEngine:
    def __init__(self, config, target=worker_main, vision_target=None):
        self.config = config
        self.identity = engine_identity(config)
        self.dimensions = config.dimensions
        self.target = target
        self.vision_target = vision_target or vision_worker_main
        self.loaded_vision = False
        self.lock = threading.RLock()
        self.state_lock = threading.Lock()
        self.process = None
        self.audio_manager = None
        self.connection = None
        self.last_activity = time.monotonic()
        self.pins = 0
        self.state = {"state": "unloaded", "worker_pid": None, "loads": 0, "unloads": 0,
                      "idle_seconds": config.idle_seconds, "load_seconds": None, "vision_loaded": False}
        self.closed = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

    def _stop_locked(self):
        if self.process:
            if self.process.pid is not None:
                # Normal process exit also cleans Python/Metal resources and semaphores.
                if self.process.is_alive() and self.connection:
                    try: self.connection.send(('close',None))
                    except (BrokenPipeError,EOFError,OSError):pass
                self.process.join(timeout=2)
                if self.process.is_alive():
                    self.process.terminate()
                    self.process.join(timeout=2)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(timeout=2)
            self.process.close()
        if self.connection:
            self.connection.close()
        self.process = self.connection = None
        self.loaded_vision = False
        self.state = {"state": "unloaded", "worker_pid": None, "loads": self.state["loads"],
                      "unloads": self.state["unloads"] + 1, "idle_seconds": self.config.idle_seconds,
                      "load_seconds": self.state["load_seconds"], "unloaded_at": time.time(), "vision_loaded": False}

    def _ensure_locked(self, vision=False):
        if self.closed.is_set():
            raise RuntimeError("Model manager is closed")
        if self.process and self.process.is_alive() and self.state["state"] == "ready":
            # Reuse the combined encoder during an indexing pass, but return to
            # text-only outside it. Stop the previous worker before loading the
            # next mode: two resident copies of the text model are unnecessary.
            if vision == self.loaded_vision or (self.loaded_vision and self.pins):
                return
        if self.process:
            self._stop_locked()
        self.state.update(state="loading", error=None)
        started = time.perf_counter()
        ctx = mp.get_context("spawn")
        self.connection, child = ctx.Pipe()
        self.process = ctx.Process(target=self.vision_target if vision else self.target,
                                   args=(child, self.config), daemon=True)
        try:
            self.process.start()
            child.close()
            self.state["worker_pid"] = self.process.pid
            if not self.connection.poll(180):
                raise RuntimeError("Model loading timed out")
            response = self.connection.recv()
            if not response["ok"]:
                raise RuntimeError(response["error"])
            self.state.update(response["metrics"])
            self.loaded_vision = vision
            self.state["vision_loaded"] = vision
            self.state.update(state="ready", loads=self.state["loads"] + 1,
                              load_seconds=round(time.perf_counter() - started, 3))
            self.last_activity = time.monotonic()
        except Exception as error:
            child.close()
            self._stop_locked()
            self.state.update(state="error", error=str(error))
            raise

    def _warm(self):
        with self.lock:
            try:
                self._ensure_locked()
            except Exception:
                pass  # Exposed in /status; a later explicit call can retry.

    def activate(self):
        self.last_activity = time.monotonic()
        with self.state_lock:
            if self.state["state"] in {"unloaded", "error"}:
                self.state["state"] = "loading"
                threading.Thread(target=self._warm, daemon=True).start()
        return self.metrics()

    def _call(self, command, argument):
        with self.lock:
            vision = command in {"image", "image_batch", "video"}
            if command in {"image", "image_batch"} and not self.config.enable_images:
                raise ValueError("Image indexing is disabled")
            if command == "video" and not self.config.enable_video:
                raise ValueError("Video indexing is disabled")
            self.last_activity = time.monotonic()
            self._ensure_locked(vision=vision)
            try:
                self.connection.send((command, argument))
                if not self.connection.poll(180):
                    self._stop_locked()
                    raise RuntimeError("Model operation timed out")
                response = self.connection.recv()
                if not response["ok"]:
                    raise ValueError(response["error"])
                self.state.update(response["metrics"])
                return response["result"]
            except (EOFError, BrokenPipeError, OSError):
                self._stop_locked()
                raise RuntimeError("Model worker stopped; retry search") from None
            finally:
                self.last_activity = time.monotonic()

    def query(self, text): return self._call("query", text)
    def document(self, text): return self._call("document", text)
    def document_batch(self, texts): return self._call("document_batch", texts)
    def image(self, path): return self._call("image", path)
    def image_batch(self, paths): return self._call("image_batch", paths)
    def video(self, frames): return self._call("video", frames)
    def audio(self, samples):
        if not self.config.enable_audio:
            raise ValueError("Audio indexing is disabled")
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError("Model manager is closed")
            if self.audio_manager is None:
                self.audio_manager = ManagedEngine(self.config, target=audio_worker_main)
            return self.audio_manager._call("audio", samples)

    def release_audio(self):
        with self.lock:
            if self.audio_manager:
                self.audio_manager.unload()

    def release_vision(self):
        """Indexing has finished; release its combined text/vision worker."""
        with self.lock:
            if self.loaded_vision:
                self._stop_locked()
    def chunks(self, text): return iter(self._call("chunks", text))

    @contextmanager
    def pin(self):
        with self.state_lock:
            self.pins += 1
        try:
            yield
        finally:
            with self.state_lock:
                self.pins -= 1
                self.last_activity = time.monotonic()

    def _monitor(self):
        while not self.closed.wait(0.5):
            if self.pins or time.monotonic() - self.last_activity < self.config.idle_seconds:
                continue
            if not self.lock.acquire(blocking=False):
                continue
            try:
                if self.process and not self.pins and time.monotonic() - self.last_activity >= self.config.idle_seconds:
                    self._stop_locked()
            finally:
                self.lock.release()

    def metrics(self):
        result = dict(self.state)
        result["audio"] = self.audio_manager.metrics() if self.audio_manager else {"state": "unloaded", "worker_pid": None, "loads": 0, "unloads": 0}
        result["idle_for_seconds"] = round(time.monotonic() - self.last_activity, 1)
        return result

    def unload(self):
        with self.lock:
            if self.pins:
                raise RuntimeError("Indexing is using the model")
            if self.process:
                self._stop_locked()
            if self.audio_manager:
                self.audio_manager.unload()
        return self.metrics()

    def close(self):
        self.closed.set()
        with self.lock:
            if self.audio_manager:
                self.audio_manager.close()
            if self.process:
                self._stop_locked()
        self.monitor.join(timeout=2)
