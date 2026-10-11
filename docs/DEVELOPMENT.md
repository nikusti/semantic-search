# Development and release builds

These instructions are for contributors building from source. End users should use the [ready-to-use installation](INSTALL.md).

## Source setup

Use Apple silicon, macOS 26+, Python 3.12 and Node.js 22.14+. The pinned MLX runtime requires macOS 26. FFmpeg and Poppler are optional for the legacy command-line decoder path; the packaged engine uses PyAV and PDFium instead.

```sh
brew install python@3.12 node ffmpeg poppler
gh repo clone nikusti/semantic-search
cd semantic-search
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --no-deps -r requirements.lock.txt
python -m pip install --no-deps -e .
python scripts/configure.py --root "$HOME/Documents"
./scripts/start.sh
```

For this development service, enter `http://127.0.0.1:8765` in the extension's optional Custom Local Service preference. The packaged engine uses port 8766 and shared configuration instead. Do not run two services indexing the same database concurrently.

## Console use without a launcher

The service and CLI can run without Tinycast or Raycast. After extracting the ready package, extract `assets/runtime.zip` with macOS Archive Utility. Use its embedded `python/bin/python3.12`; a system Python installation is unnecessary. Run `python3.12 -I -B -m tinysearch.runtime` to start the service and create shared settings. Configure folders/model through the local `/v1/settings` API or the generated `config.toml` before indexing. This is an advanced flow; launcher setup is the ordinary user path.

For an already configured engine, use the embedded executable with the shared config:

```sh
python3.12 -I -B -m tinysearch --config "$HOME/Library/Application Support/Semantic Search/config.toml" status
python3.12 -I -B -m tinysearch --config "$HOME/Library/Application Support/Semantic Search/config.toml" index
python3.12 -I -B -m tinysearch --config "$HOME/Library/Application Support/Semantic Search/config.toml" search "data migration"
```

Here `python3.12` means the path to the embedded executable, not a requirement to install a system interpreter. The service must be running; the CLI sends requests to it over loopback. The release intentionally does not install a global CLI or shell startup item.

## Extension development

```sh
cd extension
npm ci --ignore-scripts
npm run check
npm test
npm run build
```

Tinycast can import `extension/dist`. Raycast developers can use `npm run dev` or `npm run build:raycast`. The automatic engine requires the runtime assets generated below; a source checkout intentionally excludes binary runtime assets.

## Tests

```sh
python -m pytest -q
```

Tests create synthetic files and fake inference workers. The packaged decoder tests exercise real PDF, video and audio decoding without Homebrew tools. CI installs the lightweight `requirements.testing.txt`, audits staged source, and runs Python tests, the extension tests, typechecking and the Tinycast build. CI does not download model weights.

## Build a ready-to-use release

From the repository root on an Apple silicon Mac:

```sh
python scripts/build_runtime.py
python scripts/package_native_sources.py
cd extension
npm ci --ignore-scripts
npm run check
npm test
npm run build
npm run bundle:raycast -- -o ../../semantic-search-v0.4.0.rayext
cd ..
python scripts/package_release.py --raycast ../semantic-search-v0.4.0.rayext
```

The runtime build also requires Xcode Command Line Tools (Swift, clang, make and pkg-config). It compiles the Core ML bridge and rebuilds PyAV against LGPL-only FFmpeg; `--no-deps` intentionally omits unused GPL codec wheels. Complete native dependency source archives and build recipes accompany binary releases.

The runtime builder verifies the pinned portable Python archive, installs pinned libraries, copies the service, preserves third-party license files and removes local build metadata and bytecode. Its outputs are ignored `extension/assets/runtime.zip` and `runtime-manifest.json`. The extension verifies the archive SHA-256 before extracting it atomically into shared storage.

The installer compiles a Python bytecode cache locally, once per runtime, before finalizing the installation. Cache entries use the final install path for tracebacks. Workers can read this cache even with `-B`, avoiding repeated source parsing while model workers still exit after inactivity. The distributable archive contains no bytecode or user paths.

The release packager creates a prebuilt ZIP for Tinycast and Raycast and cleans source maps out of the official Raycast bundle, preventing build-machine source paths from appearing in distributed files. It produces SHA-256 checksums next to the archives. Models, indexed files, configuration and personal libraries are never release inputs.

Before uploading, stage the intended source and run `python scripts/check_release.py`. Test a fresh archive installation with empty settings and a minimal `PATH`; test model inference on Metal separately. Keep all local smoke-test data in ignored `.build` or a temporary directory.

## Architecture

The launcher installs the embedded runtime once and starts a detached local Python service. A filesystem installation lock serializes extraction across launchers. The service holds its own lock, so concurrent command launches cannot start duplicate engines for one storage folder.

The parent service remains available while inference workers load on demand and exit after inactivity. Query/session activation loads text only, preserving the configured index identity. An image/video operation replaces that worker with a combined text/vision worker. During a pinned indexing pass it is reused for text and vision, then released; a query outside indexing returns to text-only. Previous workers exit before replacement. The audio worker is loaded only for indexing sound. Bounded decoder subprocesses use the bundled PyAV/FFmpeg libraries and PDFium, with operation timeouts. The HTTP API accepts loopback requests and rejects browser origins.

The Core ML bridge source is in `native/coreml`; vendor code retains its Apache-2.0 license and provenance. Verified pinned Core ML artifacts and processor files are listed in `tinysearch/coreml_assets.json`. Media has bounded input sizes: at most 10 seconds per audio window, 3 video frames and 512 input tokens. MLX and Core ML have separate vector spaces. Model profiles restore each backend's settings; index reuse compares model revision, dimensions and chunk pipeline before copying unchanged vectors.

Core ML storage and index identity also include a hash of the complete text/vision/audio/processor manifest. Updated artifacts receive separate model folders and indexes, including updates that leave the text model revision unchanged. Existing experimental Core ML folders predating this manifest require a one-time verified migration or new index; they are preserved. The optional `reference` source extra installs the old PyTorch comparison backend. It is omitted from ready packages; Torch remains for upstream media preprocessing, while inference uses MLX or Core ML.
