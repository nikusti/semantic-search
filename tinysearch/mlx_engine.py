"""Pinned EmbeddingGemma 2 MLX implementation, sharing reference preprocessing."""
import json
import os
from pathlib import Path
import numpy as np
from .engine import normalize, engine_identity


class MLXEngine:
    def __init__(self, config, audio_only=False):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        import mlx.core as mx
        self.cache_limit = 256 * 1024**2
        self.cache_reclaims = 0
        mx.set_cache_limit(self.cache_limit)
        from mlx_vlm.embedding_loader import load_embedding_model
        from transformers import AutoTokenizer, AutoProcessor
        self.config = config
        self.dimensions = config.dimensions
        self.identity = engine_identity(config)
        values = json.loads((config.model_path / "config.json").read_text())
        self.audio_only = audio_only
        if not audio_only:
            values["audio_config"] = None
        if audio_only or (not config.enable_images and not config.enable_video):
            values["vision_config"] = None
        self.model = load_embedding_model(config.model_path, config=values)
        self.tokenizer = AutoTokenizer.from_pretrained(config.model_path, local_files_only=True)
        self.processor = AutoProcessor.from_pretrained(config.model_path, local_files_only=True) if values["vision_config"] or audio_only else None
        mx.eval(self.model.parameters())

    def _encode(self, inputs):
        import mlx.core as mx
        arrays = {key: mx.array(value) for key, value in inputs.items() if isinstance(value, np.ndarray)}
        result = self.model(**arrays).text_embeds
        mx.eval(result)
        return normalize(np.asarray(result[0], dtype=np.float32), self.dimensions)

    def _text(self, text):
        return self._encode(self.tokenizer(text, return_tensors="np", truncation=True,
                                           max_length=self.config.max_sequence_tokens))

    def query(self, text):
        return self._text(f"task: search result | query: {text}")

    def document(self, text):
        return self._text(f"title: none | text: {text}")

    def document_batch(self, texts):
        import mlx.core as mx
        inputs=self.tokenizer([f'title: none | text: {text}' for text in texts],return_tensors='np',padding=True,truncation=True,max_length=self.config.max_sequence_tokens)
        result=self.model(**{key:mx.array(value) for key,value in inputs.items() if isinstance(value,np.ndarray)}).text_embeds
        mx.eval(result)
        return np.stack([normalize(row,self.dimensions) for row in np.asarray(result,dtype=np.float32)])

    def image(self, path: Path):
        return self.image_batch([path])[0]

    def image_batch(self, paths):
        from PIL import Image, ImageOps
        from .images import open_image
        import mlx.core as mx
        images=[]
        for path in paths:
            with open_image(path) as original:
                image=ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((1024,1024));images.append([image])
        inputs=self.processor(images=images,return_tensors='np',padding=True)
        result=self.model(**{key:mx.array(value) for key,value in inputs.items() if isinstance(value,np.ndarray)}).text_embeds
        mx.eval(result)
        vectors=np.asarray(result,dtype=np.float32)
        if len(vectors)!=len(paths):raise ValueError('Incomplete image batch')
        return np.stack([normalize(row,self.dimensions) for row in vectors])

    def chunks(self, text):
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        for start in range(0, len(tokens), self.config.chunk_tokens - self.config.chunk_overlap):
            yield self.tokenizer.decode(tokens[start:start + self.config.chunk_tokens], skip_special_tokens=True)
            if start + self.config.chunk_tokens >= len(tokens):
                break

    def video(self, frames):
        if not self.config.enable_video or self.audio_only:
            raise ValueError("Video indexing is disabled")
        # Already sampled and decoded; do not let the processor sample them again.
        inputs = self.processor(videos=[np.stack([np.asarray(f) for f in frames])],
                                do_sample_frames=False, add_timestamps=False, return_tensors="np")
        return self._encode(inputs)

    def audio(self, samples):
        if not self.config.enable_audio or not self.audio_only:
            raise ValueError("Audio requires the separate on-demand worker")
        inputs = self.processor(audio=[samples], sampling_rate=16000, return_tensors="np")
        return self._encode(inputs)

    def metrics(self):
        import mlx.core as mx
        # Free arrays can briefly exceed the allocator cap until its next allocation.
        # This hook runs after an operation's local tensor references were released.
        if mx.get_cache_memory() > self.cache_limit:
            mx.clear_cache()
            self.cache_reclaims += 1
        return {"device": "mlx:gpu", "precision": self.config.model_variant,
                "audio_loaded": self.audio_only,
                "vision_loaded": self.processor is not None and not self.audio_only,
                "max_sequence_tokens": self.config.max_sequence_tokens,
                "mlx_active_mb": round(mx.get_active_memory() / 1024**2, 1),
                "mlx_cache_mb": round(mx.get_cache_memory() / 1024**2, 1),
                "mlx_cache_limit_mb": self.cache_limit / 1024**2,
                "mlx_cache_reclaims": self.cache_reclaims,
                "mlx_peak_mb": round(mx.get_peak_memory() / 1024**2, 1)}
