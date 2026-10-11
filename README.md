<p align="center"><img src="extension/assets/icon.png" width="88" alt="Semantic Search icon"></p>
<h1 align="center">Semantic Search</h1>
<p align="center">Find local files by what you remember.<br>Documents, photos, video scenes and audio — on your Mac.</p>
<p align="center"><a href="https://github.com/nikusti/semantic-search/releases/tag/v0.4.1">Download</a> · <a href="#installation">Install</a> · <a href="#models-and-storage">Models & storage</a> · <a href="CHANGELOG.md">Version history</a> · <a href="docs/DEVELOPMENT.md">Develop</a></p>

Describe a file in natural language, preview the match, and open the original. Semantic Search runs locally and shares its engine, models and indexes between **Tinycast and Raycast**.

The ready-to-use package includes its own Python, MLX and file decoders. **You do not need to install Python, Homebrew, FFmpeg, Poppler or Node.js to use the ready-to-use release.** The extension prepares and starts its engine automatically, including a local code cache that speeds up subsequent model launches.

## Installation

You need an **Apple silicon Mac with macOS 26 or newer**, Tinycast or a compatible Raycast, and space for the engine and model.

### Tinycast

1. [Download the latest release](https://github.com/nikusti/semantic-search/releases/tag/v0.4.1): choose **`semantic-search-v0.4.1.zip`** and extract it.
2. In Tinycast, open **Settings → Extensions → Install → Add from folder** and choose the extracted **Semantic Search** folder.
3. Open **Semantic Search Model Settings**. The engine prepares itself on the first launch.
4. Use **Folders to Search** to choose the folders you want to search.
5. Choose your content types and press **Download & Apply**.
6. When files become searchable, open **Semantic Search** and describe a file.

Leave **Custom Local Service** blank. It is an optional setting for developers using a separate local service.

### Raycast

1. Download and extract the same **`semantic-search-v0.4.1.zip`**.
2. Run **Import Extension** in Raycast and select the extracted **Semantic Search** folder.
3. The package is already built. The generic developer welcome screen may suggest npm commands; **you can skip those**.
4. Open **Semantic Search Model Settings**, choose folders in **Folders to Search**, and press **Download & Apply**.

This folder-import workflow has been tested in Raycast v2. Other Raycast versions may have different local-import behavior. Keep the extracted folder in a permanent location. The optional `.rayext` asset is produced by the official bundler, but the installed Raycast host currently restricts direct archive imports to internal builds; use the extracted ZIP folder instead. There is no Store listing yet.

For the short setup guide, see [Install](docs/INSTALL.md). Source setup and terminal commands live in [Development](docs/DEVELOPMENT.md).

## What it finds

| Content | Search and preview |
| --- | --- |
| Documents and text | Content in PDF, DOCX, text, Markdown and source files. PDF previews preserve the first page's original layout. |
| Photos | Visual content in JPEG, PNG, HEIC/HEIF and WebP, with thumbnails and larger previews. |
| Video | Sampled scenes and their timestamps, with an action to open the matched fragment. |
| Audio | Sounds in short segments, including soundtracks of enabled video files. |

Try “a project migration checklist”, “a person standing near a window”, “a photo of a mountain lake”, or “a recording with applause”. Query language need not match the file's language, but retrieval quality varies by content and language.

Documents, images, video and audio have independent switches. Video and audio start disabled. Text queries load only the text encoder, including searches for indexed photos, video scenes and sounds. The combined text/vision encoder loads only when indexing images or video, replaces the text-only worker instead of running a second copy, and exits when the indexing pass finishes. Audio encoding loads in a separate worker only when indexing sound.

## Folders and indexing

Choose folders using **Folders to Search** in Model Settings. Remove a selected folder to stop searching it after applying settings. A new folder selection gets its own compatible index. Previously saved indexes remain on disk. Each model remembers its own content and chunk settings. Returning to the same model and settings reopens its existing index; it does not regenerate unchanged vectors. Compatible text/photo vectors can be reused across content selections within the same model revision and chunk settings.

Use **Add excluded folders** to add folders without replacing existing exclusions. Enable **Edit exclusion paths manually** to remove folders or enter absolute paths or `~/` paths. Each exclusion includes its subfolders. Exclusions are directly below Folders to Search and can be saved during indexing. Saving removes their indexed records and previews from saved indexes, discards an in-flight excluded result, and skips subsequent files and media segments. A model call already running finishes before its result is discarded. Unavailable older indexes are reported for cleanup retry; other indexes are still cleaned. Original files remain untouched. Remove the exclusion and run **Update Search Index** to include them again.

The settings window starts with one search-index overview: indexed file/vector totals, actual database disk usage (including WAL and embedded previews), counts by content type, and the current scan or last update. Detailed vector, chunk, media segment and performance options are under **Advanced**. Existing indexed files become searchable before a scan finishes. Long media files take longer, so file counts are not a reliable time estimate.

Indexing is explicitly requested. There is no continuous watcher. When settings are saved, **Update Search Index** is the main settings action; **⌘R** also works in settings and search. A scan adds new files, checks size and modification time, hashes changed candidates, reuses vectors for identical content and removes disappeared files from the index. Unchanged files are skipped. Scan counts persist after restarting the engine. Files modified while preserving both size and modification time require a new indexing pipeline or manually changing their timestamp. Use **Update Search Index** after adding or changing files. **Actions → Cancel Indexing** (⌘.) stops at the next safe boundary and preserves completed files and unvisited saved entries. Resume with Update Search Index. Cancel or wait before switching models; excluded folders can still be saved during indexing.

## Models and storage

The default model is [EmbeddingGemma 2 · MLX 8-bit](https://huggingface.co/mlx-community/embeddinggemma-2-8bit), pinned to an immutable Hugging Face revision. It is an MLX conversion of Google's model. Vision and audio towers retain BF16 weights.

The release page lists the engine archive size. The bundled runtime needs additional installed and temporary extraction space. The recommended model adds approximately **1.27 GB**. Allow additional temporary extraction/download space. Indexes and previews grow with the files you select. Model weights are downloaded only when requested and are not included in the archive.

```text
~/Library/Application Support/Semantic Search/
├── config.toml             Selected folders and settings
├── runtime/                Installed engine versions
├── models/                 Downloaded model weights
├── data/                   SQLite indexes, preview cache and reports
└── logs/                   Local service diagnostics
```

Both launchers use this same folder. The engine starts when a command is opened; no terminal or login startup item is required. A lightweight parent service remains running. Inference workers unload after **one minute** of inactivity after leaving search; indexing and in-flight work prevent premature unloading.

[EmbeddingGemma 2 · Core ML](https://huggingface.co/FluidInference/embeddinggemma-2-coreml) uses the same model architecture through Apple's Core ML runtime, with its own separately generated index. Text input is limited to 512 tokens, so document chunks are at most 480 tokens; audio uses windows up to 10 seconds and video uses 3 frames. Text search loads neither visual nor audio encoders. Optional Core ML encoders download when enabled. Setup prepares all text functions once; searches then load only the function needed for their input length. Normal restarts reuse preparation. This startup optimization preserves existing Core ML indexes and does not change MLX.

**Actions → Delete Selected Model** unloads and removes its weights while preserving indexes and original files. Re-download the same model/revision to reuse its index. Different model weights normally require a new index; matching vector lengths do not make models interchangeable.

### Defaults and choices

| Setting | Default | Other choices |
| --- | --- | --- |
| Vector dimensions | 512, stored as float32 | 128, 256, 768 |
| Document chunks | 512 tokens, overlap 64 | 256 or 384 tokens |
| List thumbnails | 256 pixels | 128 or 384 pixels |
| Model unload | 1 minute after leaving search | 5 minutes |
| Video segments | 10 seconds, 3 frames | 5, 20 or 30 seconds |
| Audio segments | 20 seconds, overlap 2 | 10 or 30 seconds |

“8-bit model” describes its weights. Index vectors remain float32; INT8 vector storage is not implemented. A 512-dimensional vector is **2,048 bytes**. At the defaults, video scene vectors add about **0.74 MB/hour**, and audio vectors about **0.41 MB/hour**, excluding metadata and previews. Video sound adds audio vectors when enabled.

### Custom models and updates

**Custom model URL** accepts Hugging Face HTTPS repositories and `/tree/revision` links. Supported conversions are **EmbeddingGemma 2 in MLX**, native 768-dimensional output, BF16 or affine 4/8-bit quantization with group size 64. Repositories must declare `mlx` or `mlx-vlm`. Images/video require the vision encoder; audio requires the audio encoder. Chat LLMs, GGUF, original PyTorch weights and other embedding families are unsupported.

Compatibility is checked before weight download; custom weights must load and encode before replacing a working configuration. Revisions are pinned. To update compatible weights, enter a URL with the new explicit commit revision. A bare URL already used before does not check for updates. A new architecture needs a service update.

Updated built-in weights use separate model storage and indexes. Core ML also tracks the pinned vision, audio and preprocessing artifacts. Older indexes are kept; unchanged compatible settings reuse their existing vectors.

To update the extension, install its newer package. Models and indexes stay in shared storage. If a previous engine is still running, restart the Mac and reopen search.

## Privacy and limits

Inference, indexing and previews run locally. Files are not sent to a cloud inference service. Internet access is required for the initial model download; the bundled engine installs offline. The service binds only to loopback and rejects browser-origin requests. Other local processes can access it. Local indexes contain paths and extracted content.

- Only readable files in your chosen folders are indexed. Apple Photos libraries and remote iCloud-only assets need a dedicated importer, which is not included.
- Scanned PDFs without an extractable text layer need OCR, which is not included.
- Audio search embeds sounds; it does not transcribe or identify speakers or songs.
- Video sampling may miss brief events between sampled frames.
- Video rotation metadata is not currently applied by the bundled decoder; some phone recordings may appear sideways in sampled previews.
- Limits are 32 MB/document and 1 GB / 2 hours/media file. Larger media files are skipped rather than silently truncated.
- The release contains no model weights, personal library, existing configuration or indexes.

## Help and development

See [Troubleshooting](docs/TROUBLESHOOTING.md), [Development](docs/DEVELOPMENT.md), [Changelog](CHANGELOG.md) and [Store publication planning](docs/STORE.md).

Application source is [MIT-licensed](LICENSE). Complete native dependency sources and build recipes are available as the `semantic-search-native-sources-v0.4.1.zip` release asset; see [native licenses and source](packaging/codec-notices/README.md). Bundled libraries and model weights retain their upstream licenses; third-party notices and dependency license files accompany the installed runtime. See the [model card](https://huggingface.co/mlx-community/embeddinggemma-2-8bit), [Google's guide](https://ai.google.dev/gemma/docs/embeddinggemma/inference-embeddinggemma-with-sentence-transformers), [MLX-VLM](https://github.com/Blaizzy/mlx-vlm), [portable Python](https://github.com/astral-sh/python-build-standalone), [PyAV](https://github.com/PyAV-Org/PyAV) and [PDFium](https://github.com/pypdfium2-team/pypdfium2).

Maintainers: stage only source and run `python scripts/check_release.py` before committing. CI audits source and runs synthetic tests and builds. Runtime binaries, configs, databases, models and personal examples stay outside Git history.
