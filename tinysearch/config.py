from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Config:
    roots: tuple[Path, ...]
    model_path: Path
    model_revision: str
    database: Path
    device: str = "mps"
    dimensions: int = 768
    max_file_mb: int = 32
    max_document_chars: int = 250000
    max_document_chunks: int = 256
    port: int = 8765
    pdf_renderer: str = "pdftoppm"
    backend: str = "torch"
    model_variant: str = "reference"
    enable_documents: bool = True
    enable_images: bool = True
    enable_video: bool = False
    enable_audio: bool = False
    video_segment_seconds: int = 10
    audio_segment_seconds: int = 20
    audio_overlap_seconds: int = 2
    video_frames: int = 3
    max_media_file_mb: int = 1024
    max_media_seconds: int = 7200
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    idle_seconds: int = 60
    thumbnail_px: int = 256
    thumbnail_quality: int = 60
    max_sequence_tokens: int = 512
    chunk_tokens: int = 384
    chunk_overlap: int = 64
    source_path: Path | None = None
    excluded_paths: tuple[Path, ...] = ()
    model_url: str = ''
    storage_path: Path | None = None
    pipeline_revision: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'excluded_paths', tuple(sorted({
            Path(p).expanduser().resolve() for p in self.excluded_paths})))
        if self.dimensions not in {128, 256, 512, 768}:
            raise ValueError("Unsupported embedding dimension")
        if self.backend not in {"torch", "mlx", "coreml"}:
            raise ValueError("Unsupported model backend")
        if self.backend == "coreml" and self.max_sequence_tokens > 512:
            raise ValueError("Core ML supports up to 512 input tokens")
        if not 0 <= self.chunk_overlap < self.chunk_tokens <= self.max_sequence_tokens:
            raise ValueError("Invalid document chunk settings")
        if not 64 <= self.thumbnail_px <= 640 or not 30 <= self.thumbnail_quality <= 90:
            raise ValueError("Invalid thumbnail settings")
        if not 1 <= self.idle_seconds <= 3600:
            raise ValueError("Invalid idle timeout")
        if self.enable_video or self.enable_audio:
            if self.backend not in {"mlx", "coreml"}:
                raise ValueError("Video and audio require MLX or Core ML")
        if self.backend == 'coreml' and ((self.enable_audio and self.audio_segment_seconds > 10) or (self.enable_video and self.video_frames > 3)):
            raise ValueError('Core ML supports audio windows up to 10 seconds and up to 3 video frames')
        if not 5 <= self.video_segment_seconds <= 30 or not 5 <= self.audio_segment_seconds <= 30:
            raise ValueError("Invalid media segment duration")
        if not 0 <= self.audio_overlap_seconds < self.audio_segment_seconds:
            raise ValueError("Invalid audio overlap")
        if not 2 <= self.video_frames <= 8 or self.max_media_file_mb < 1 or self.max_media_seconds < 1:
            raise ValueError("Invalid media limits")

    @classmethod
    def load(cls, path: Path):
        path = path.resolve()
        values = tomllib.loads(path.read_text())
        def resolve(value):
            return (path.parent / Path(value).expanduser()).resolve()
        roots = tuple(resolve(r) for r in values.pop("roots"))
        excluded_paths = tuple(resolve(p) for p in values.pop('excluded_paths', []))
        storage_path = values.pop('storage_path', None)
        return cls(roots=roots, source_path=path, model_path=resolve(values.pop("model_path")),
                   database=resolve(values.pop("database")), excluded_paths=excluded_paths,
                   storage_path=resolve(storage_path) if storage_path else None, **values)

    def is_excluded(self, path):
        if not self.excluded_paths:
            return False
        path = Path(path).expanduser().absolute()
        if any(path.is_relative_to(folder) for folder in self.excluded_paths):
            return True
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return False
        return any(resolved.is_relative_to(folder) for folder in self.excluded_paths)

    def save(self):
        import json
        from dataclasses import fields
        if not self.source_path:
            raise ValueError("Settings require a saved configuration")
        values = {}
        for field in fields(self):
            if field.name == "source_path": continue
            value = getattr(self, field.name)
            if value is None: continue
            if isinstance(value, Path): value = str(value)
            if field.name in {"roots", "excluded_paths"}: value = [str(p) for p in value]
            values[field.name] = value
        text = '\n'.join(f'{key} = {json.dumps(value, ensure_ascii=False)}' for key, value in values.items()) + '\n'
        temp = self.source_path.with_suffix('.toml.tmp')
        temp.write_text(text)
        temp.replace(self.source_path)
