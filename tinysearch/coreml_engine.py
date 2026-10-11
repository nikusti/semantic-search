"""Core ML bridge with separate, on-demand visual and audio encoders."""
import json
from pathlib import Path
import selectors
import subprocess
import tempfile

import numpy as np

from .engine import engine_identity, normalize


class CoreMLEngine:
    def __init__(self, config, audio_only=False):
        self.config = config
        self.dimensions = config.dimensions
        self.identity = engine_identity(config)
        self.processor = None
        self.audio_only = audio_only
        self.visual = not audio_only and (config.enable_images or config.enable_video)
        self.tokenizer = None
        self.child = None
        self.folder = tempfile.TemporaryDirectory(prefix='semantic-search-coreml-')
        try:
            if self.visual or audio_only:
                from transformers import AutoProcessor
                self.processor = AutoProcessor.from_pretrained(config.model_path / 'processor', local_files_only=True)
            from .coreml_assets import helper_path
            self.child = subprocess.Popen([
                str(helper_path(config.model_path)), str(config.model_path),
                str(config.model_path), 'audio' if audio_only else 'index' if self.visual else 'search',
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1)
            ready = self._read()
            if ready.get('event') != 'ready':
                raise RuntimeError('Core ML worker failed to initialize')
        except Exception:
            self.close()
            raise

    def _read(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.child.stdout, selectors.EVENT_READ)
            if not selector.select(180):
                raise RuntimeError('Core ML operation timed out')
        line = self.child.stdout.readline()
        if not line:
            raise RuntimeError('Core ML worker stopped')
        result = json.loads(line)
        if 'error' in result:
            raise ValueError(result['error'])
        return result

    def _send(self, operation, **values):
        self.child.stdin.write(json.dumps({'op': operation, **values}) + '\n')
        self.child.stdin.flush()
        return self._read()

    def _vector(self, vector):
        return normalize(vector, self.dimensions)

    def query(self, text):
        return self._vector(self._send('query', text=text)['vectors'][0])

    def document_batch(self, texts):
        return np.stack([self._vector(v) for v in self._send('documents', texts=texts)['vectors']])

    def document(self, text):
        return self.document_batch([text])[0]

    def chunks(self, text):
        if self.tokenizer is None:
            from transformers import AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(self.config.model_path / 'processor', local_files_only=True)
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        for start in range(0, len(tokens), self.config.chunk_tokens - self.config.chunk_overlap):
            yield self.tokenizer.decode(tokens[start:start + self.config.chunk_tokens], skip_special_tokens=True)
            if start + self.config.chunk_tokens >= len(tokens):
                break

    def _inputs(self, operation, inputs, samples=None):
        folder = Path(self.folder.name)
        description = {}
        for key, value in inputs.items():
            if not isinstance(value, np.ndarray): continue
            array = np.asarray(value, dtype=np.int32 if value.dtype.kind in 'iu' else np.float32)
            array.tofile(folder / (key + '.bin'))
            description[key] = {'shape': list(array.shape), 'dtype': str(array.dtype)}
        if samples is not None: np.asarray(samples, dtype=np.float32).tofile(folder / 'waveform.bin')
        (folder / 'inputs.json').write_text(json.dumps({'inputs': description}))
        return self._vector(self._send(operation, folder=str(folder))['vectors'][0])

    def image(self, path):
        if not self.visual:
            raise ValueError('Image indexing is disabled')
        from PIL import Image, ImageOps
        from .images import open_image
        with open_image(path) as original:
            image = ImageOps.exif_transpose(original).convert('RGB')
            image.thumbnail((1024, 1024))
        inputs = self.processor(images=[[image]], return_tensors='np', padding=True)
        return self._inputs('image', inputs)

    def image_batch(self, paths):
        return np.stack([self.image(path) for path in paths])

    def video(self, frames):
        if not self.config.enable_video or not self.visual: raise ValueError('Video indexing is disabled')
        inputs = self.processor(videos=[np.stack([np.asarray(frame) for frame in frames])],
                                do_sample_frames=False, add_timestamps=False, return_tensors='np')
        return self._inputs('video', inputs)

    def audio(self, samples):
        if not self.config.enable_audio or not self.audio_only: raise ValueError('Audio requires the on-demand worker')
        if len(samples) > 160000: raise ValueError('Core ML audio windows are limited to 10 seconds')
        inputs = self.processor(audio=[samples], sampling_rate=16000, return_tensors='np')
        return self._inputs('audio', inputs, samples)

    def metrics(self):
        return {'device': 'coreml:cpu-and-neural-engine', 'precision': 'coreml-fp16',
                'vision_loaded': self.visual, 'audio_loaded': self.audio_only,
                'native_worker_pid': self.child.pid, 'max_sequence_tokens': 512}

    def close(self):
        if self.child is not None:
            if self.child.stdin:
                self.child.stdin.close()
            try:
                self.child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.child.terminate()
                try:
                    self.child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait()
            self.child.stdout.close()
            self.child = None
        self.folder.cleanup()
