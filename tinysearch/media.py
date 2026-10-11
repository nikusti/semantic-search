"""Bounded local decoding. No original media or decoded audio is stored in the index."""
from dataclasses import dataclass
from io import BytesIO
import json
import math
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image, UnidentifiedImageError


def decoder_available(config):
    import importlib.util
    import shutil
    if config.ffmpeg == 'pyav':
        return importlib.util.find_spec('av') is not None
    return bool(shutil.which(config.ffmpeg)) and bool(shutil.which(config.ffprobe))


def bundled_decode(operation, path, *arguments, timeout=45):
    import sys
    return subprocess.run([sys.executable, '-B', '-m', 'tinysearch.media_decoder',
                           operation, str(path), *map(str, arguments)],
                          capture_output=True, timeout=timeout, check=True).stdout


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    video_duration: float | None = None
    audio_duration: float | None = None


def probe(path: Path, config):
    if config.ffmpeg == 'pyav':
        return MediaInfo(**json.loads(bundled_decode('probe', path, timeout=20)))
    result = subprocess.run(
        [config.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
         "-show_entries", "format=duration:stream=codec_type,duration", "-of", "json", str(path)],
        capture_output=True, timeout=20, check=True)
    data = json.loads(result.stdout)
    durations = [float(s.get("duration", 0)) for s in data.get("streams", []) if s.get("duration") not in (None, "N/A")]
    duration = float(data.get("format", {}).get("duration", max(durations, default=0)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Media has no finite positive duration")
    kinds = {s.get("codec_type") for s in data.get("streams", [])}
    def stream_duration(kind):
        # Decoders select the first stream of each type, not the longest one.
        stream = next((s for s in data.get("streams", []) if s.get("codec_type") == kind), {})
        value = stream.get("duration")
        if value not in (None, "N/A"):
            value = float(value)
            if math.isfinite(value) and value > 0:
                return min(value, duration)
        return duration if kind in kinds else None
    return MediaInfo(duration, "video" in kinds, "audio" in kinds,
                     stream_duration("video"), stream_duration("audio"))


def segments(duration, seconds, overlap=0):
    """Cover the entire clip, including its last partial segment."""
    start = 0.0
    while start < duration:
        end = min(duration, start + seconds)
        yield start, end
        if end == duration:
            break
        start += seconds - overlap


def frame(path, seconds, config, size=1024, orient=False):
    if config.ffmpeg == 'pyav':
        with Image.open(BytesIO(bundled_decode('frame', path, seconds, size, int(orient)))) as image:
            return image.convert('RGB')
    result = subprocess.run(
        [config.ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
         "-ss", str(max(0, seconds)), "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
         "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
         "-threads", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        capture_output=True, timeout=45, check=True)
    with Image.open(BytesIO(result.stdout)) as image:
        return image.convert("RGB")


def video_frames(path, start, end, config, orient=False):
    # Centres of equal subintervals avoid an initial black frame and seeking past EOF.
    times = start + (np.arange(config.video_frames) + .5) * (end - start) / config.video_frames
    frames = []
    for t in times:
        # A sub-second tail can lie after the last frame timestamp, although it
        # is still within the stream duration. Seek at most one second earlier.
        offsets = (0, .1, .25, .5, 1) if end - start < 1 else (0,)
        for index, offset in enumerate(offsets):
            try:
                frames.append(frame(path, max(0, float(t) - offset), config, size=640, orient=orient))
                break
            except (subprocess.CalledProcessError, UnidentifiedImageError):
                if index == len(offsets) - 1:
                    raise
    return frames


def audio_samples(path, start, end, config):
    if config.ffmpeg == 'pyav':
        data = bundled_decode('audio', path, start, end)
        samples = np.frombuffer(data, dtype='<f4').copy()
        if len(samples) < 320 or not np.isfinite(samples).all() or len(samples) > math.ceil((end-start+.1)*16000):
            raise ValueError('Audio segment has no usable bounded samples')
        return samples
    result = subprocess.run(
        [config.ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
         "-ss", str(start), "-i", str(path), "-t", str(end - start), "-map", "0:a:0",
         "-vn", "-ac", "1", "-ar", "16000", "-threads", "1", "-f", "f32le", "pipe:1"],
        capture_output=True, timeout=45, check=True)
    samples = np.frombuffer(result.stdout, dtype="<f4").copy()
    if len(samples) < 320 or not np.isfinite(samples).all():
        raise ValueError("Audio segment has no usable samples")
    if len(samples) > math.ceil((end - start + .1) * 16000):
        raise ValueError("Decoded audio exceeds requested segment")
    return samples


def jpeg(image, size=256, quality=60):
    image = image.copy()
    image.thumbnail((size, size))
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def timestamp(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def render_segment(path, output, start, end, config, video, orient=False):
    if config.ffmpeg == 'pyav':
        bundled_decode('segment', path, output, start, end, int(video), int(orient), timeout=90)
        return
    args = [config.ffmpeg, "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
            "-ss", str(start), "-i", str(path), "-t", str(end - start)]
    if video:
        args += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "25", "-threads", "2", "-c:a", "aac", "-movflags", "+faststart"]
    else:
        args += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
    subprocess.run(args + [str(output)], capture_output=True, check=True, timeout=90)
