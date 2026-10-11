"""Check runtime prerequisites without downloading weights or reading user files."""
import importlib
import platform
import shutil
import sys


def main():
    failures = []
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        failures.append("The MLX runtime requires an Apple silicon Mac.")
    elif int(platform.mac_ver()[0].split('.')[0]) < 26:
        failures.append("The pinned MLX runtime requires macOS 26 or newer.")
    if sys.version_info < (3, 12):
        failures.append("Python 3.12 or newer is required.")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    try:
        importlib.import_module("mlx.core")
    except Exception as error:
        print(f"Cannot initialize MLX: {type(error).__name__}. Check Metal access and the pinned runtime.", file=sys.stderr)
        return 1
    for name in ["mlx_vlm.embedding_loader", "fastapi", "uvicorn", "pypdf", "pillow_heif"]:
        try:
            importlib.import_module(name)
        except Exception as error:
            failures.append(f"Cannot import {name}: {type(error).__name__}")
    try:
        from transformers import EmbeddingGemma2Processor
    except ImportError:
        failures.append("Transformers must include EmbeddingGemma2Processor. Install the pinned dependencies.")
    for tool in ["pdftoppm", "ffmpeg", "ffprobe"]:
        print(f"{tool}: {'available' if shutil.which(tool) else 'missing (install Poppler or FFmpeg)'}")
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print("Runtime prerequisites are available. No model was loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
