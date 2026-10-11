"""Image decoding with macOS ImageIO fallback for HEIC/HEIF."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile


@contextmanager
def open_image(path):
    from PIL import Image, UnidentifiedImageError
    try:
        image = Image.open(path)
    except UnidentifiedImageError:
        if Path(path).suffix.lower() not in {'.heic', '.heif', '.avif'}:
            raise
        with tempfile.TemporaryDirectory(prefix='semantic-search-image-') as folder:
            target = Path(folder)/'decoded.jpg'
            subprocess.run(['/usr/bin/sips', '-s', 'format', 'jpeg', str(path), '--out', str(target)],
                           check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            with Image.open(target) as image:
                yield image
        return
    with image:
        yield image
