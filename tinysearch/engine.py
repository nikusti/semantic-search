import os
import threading
from pathlib import Path
import numpy as np


def normalize(vector, dimensions):
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    if len(vector) < dimensions or not np.isfinite(vector).all():
        raise ValueError("Invalid embedding: dimensions or non-finite values")
    vector = vector[:dimensions]
    norm = np.linalg.norm(vector)
    if norm <= 1e-12:
        raise ValueError("Invalid embedding: zero norm")
    return vector / norm


def engine_identity(config):
    identity = f"embeddinggemma-2:{config.model_revision}:{config.dimensions}:v1"
    if config.backend != "torch" or config.model_variant != "reference":
        identity += f":{config.backend}:{config.model_variant}"
    if config.model_variant == 'custom':
        import hashlib
        identity += ':' + hashlib.sha256(config.model_url.encode()).hexdigest()[:16]
    if config.pipeline_revision:
        identity += ':assets-' + config.pipeline_revision
    if not config.enable_images and not config.enable_video:
        identity += ":text-only"
    if (config.chunk_tokens, config.chunk_overlap, config.max_sequence_tokens) != (384, 64, 512):
        identity += f":chunk{config.chunk_tokens}:{config.chunk_overlap}:{config.max_sequence_tokens}"
    if config.enable_video or config.enable_audio:
        # Hidden settings for a disabled modality must not invalidate its sibling's index.
        video_seconds, video_frames = (config.video_segment_seconds, config.video_frames) if config.enable_video else (10, 3)
        audio_seconds, audio_overlap = (config.audio_segment_seconds, config.audio_overlap_seconds) if config.enable_audio else (20, 2)
        identity += f":media-v1:video{int(config.enable_video)}-{video_seconds}-{video_frames}:audio{int(config.enable_audio)}-{audio_seconds}-{audio_overlap}"
    return identity


class GemmaEngine:
    """One resident model; serialized GPU calls, no network at inference time."""

    def __init__(self, config):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        import torch
        from sentence_transformers import SentenceTransformer
        if config.device == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("MPS is unavailable; use device='cpu' in config.toml")
        torch.set_num_threads(2)
        self.dimensions = config.dimensions
        self.identity = engine_identity(config)
        self.config = config
        self.lock = threading.Lock()
        self.model = SentenceTransformer(
            str(config.model_path), device=config.device, local_files_only=True,
            config_kwargs={"audio_config": None, **({"vision_config": None} if not config.enable_images and not config.enable_video else {})},
            model_kwargs={"dtype": torch.float32, "attn_implementation": "eager"},
        )
        self.model.max_seq_length = config.max_sequence_tokens
        self.model.eval()
        self.tokenizer = self.model.tokenizer

    def _encode(self, content):
        with self.lock:
            result = self.model.encode(content, batch_size=1, convert_to_numpy=True,
                                       normalize_embeddings=True, show_progress_bar=False)
        return normalize(result, self.dimensions)

    def query(self, text):
        return self._encode(f"task: search result | query: {text}")

    def document(self, text):
        # Filename deliberately omitted: retrieve actual contents.
        return self._encode(f"title: none | text: {text}")

    def image(self, path: Path):
        from PIL import Image, ImageOps
        from .images import open_image
        with open_image(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1024, 1024))
            return self._encode({"image": image})

    def chunks(self, text):
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        for start in range(0, len(tokens), self.config.chunk_tokens - self.config.chunk_overlap):
            yield self.tokenizer.decode(tokens[start:start + self.config.chunk_tokens], skip_special_tokens=True)
            if start + self.config.chunk_tokens >= len(tokens):
                break

    def metrics(self):
        import torch
        result = {"device": str(self.model.device), "dtype": str(self.model.dtype),
                  "vision_loaded": self.config.enable_images or self.config.enable_video,
                  "max_sequence_tokens": self.model.max_seq_length}
        if self.model.device.type == "mps":
            result["mps_allocated_mb"] = round(torch.mps.current_allocated_memory() / 1024**2, 1)
            result["mps_driver_mb"] = round(torch.mps.driver_allocated_memory() / 1024**2, 1)
        return result
