"""Render previews locally; keep original files untouched."""
import subprocess
from io import BytesIO
from pathlib import Path
from pypdf import PdfReader


def photo_transition(small_jpeg: bytes, full_jpeg: bytes):
    """JPEG refinement frames with identical geometry and full colour.

    Tinycast uses different native views for GIF and JPEG; switching between
    those views changes layout. Keep every frame in its static image view.
    Intermediate frames are ephemeral and never enter the disk cache.
    """
    from PIL import Image
    with Image.open(BytesIO(full_jpeg)) as source:
        full = source.convert('RGB')
    with Image.open(BytesIO(small_jpeg)) as source:
        small = source.convert('RGB').resize(full.size, Image.Resampling.BICUBIC)
    frames = []
    for n in range(1, 6):
        t = n / 6
        eased = t * t * (3 - 2 * t)
        output = BytesIO()
        Image.blend(small, full, eased).save(output, format='JPEG', quality=80)
        frames.append(output.getvalue())
    frames.append(full_jpeg)
    return frames, 240


def render_pdf(path: Path, renderer: str):
    pages = len(PdfReader(path).pages)
    if not pages:
        raise ValueError("PDF has no pages")
    if renderer == 'pdfium':
        import sys
        result = subprocess.run([sys.executable, '-B', '-m', 'tinysearch.media_decoder',
                                 'pdf', str(path)], capture_output=True, timeout=20, check=True)
        return result.stdout, pages
    result = subprocess.run(
        [renderer, "-f", "1", "-l", "1", "-singlefile", "-scale-to", "1400",
         "-jpeg", "-jpegopt", "quality=88", str(path)],
        capture_output=True, timeout=20, check=True,
    )
    if not result.stdout.startswith(b"\xff\xd8"):
        raise ValueError("PDF renderer did not produce a JPEG")
    return result.stdout, pages
