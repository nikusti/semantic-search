"""Audit tracked release files; local settings and indexed content must stay private."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_FOLDERS = {"models", "data", "artifacts", "fixtures", "node_modules", ".venv", "__pycache__", ".build"}
PRIVATE_SUFFIXES = {".sqlite3", ".db", ".npy", ".npz", ".safetensors", ".gguf", ".mp4", ".mov", ".mp3", ".wav", ".heic", ".jpg", ".jpeg", ".zip", ".rayext"}
# Native Capture Window screenshots are kept intact, including the background.
STORE_CAPTURE_ASSETS = {
    "packaging/store-metadata/semantic-file-search-1.png",
    "packaging/store-metadata/semantic-file-search-2.png",
}
# Only the icon and individually reviewed screenshots are public binary assets.
PUBLIC_PNG_ASSETS = {
    "extension/assets/icon.png",
    "extension/assets/store-icon.png",
    "docs/screenshots/document-search.png",
    "docs/screenshots/photo-search.png",
    "docs/screenshots/audio-search.png",
    "extension/metadata/semantic-search-1.png",
    "extension/metadata/semantic-search-2.png",
    "extension/metadata/semantic-search-3.png",
    "extension/metadata/semantic-search-4.png",
} | STORE_CAPTURE_ASSETS
PATTERNS = {
    "personal absolute path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    "GitHub credential": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "Hugging Face credential": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "non-English Cyrillic text": re.compile(r"[\u0400-\u04ff]"),
}


def main():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    names = [item.decode() for item in result.stdout.split(b"\0") if item]
    if not names:
        print("No tracked files to check. Stage the intended release first.", file=sys.stderr)
        return 1
    failures = []
    for name in names:
        path = ROOT / name
        if path.is_symlink():
            failures.append(f"{name}: symlinks are not release files")
            continue
        if FORBIDDEN_FOLDERS.intersection(Path(name).parts) or path.suffix.lower() in PRIVATE_SUFFIXES:
            failures.append(f"{name}: runtime data or dependencies")
        if path.name in {"config.toml", "auth-token"} or path.name.startswith((".env", ".auth-token-")):
            failures.append(f"{name}: local settings")
        content = subprocess.run(["git", "show", f":{name}"], cwd=ROOT, capture_output=True, check=True).stdout
        size_limit = (4 if name in STORE_CAPTURE_ASSETS else 1) * 1024 * 1024
        if len(content) > size_limit:
            failures.append(f"{name}: unexpectedly large source file")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            if name not in PUBLIC_PNG_ASSETS or not content.startswith(b"\x89PNG\r\n\x1a\n"):
                failures.append(f"{name}: unexpected binary asset")
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{name}: {label}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Release audit passed for {len(names)} tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
