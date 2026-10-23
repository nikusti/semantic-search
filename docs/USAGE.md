# Search, indexing and model options

## What it finds

| Content | Search and preview |
| --- | --- |
| Documents and text | Content in PDF, DOCX, text, Markdown and source files. PDF previews preserve the first page's original layout. |
| Photos | Visual content in JPEG, PNG, HEIC/HEIF and WebP, with thumbnails and larger previews. |
| Video | Sampled scenes and their timestamps, with an action to open the matched fragment. |
| Audio | Sounds in short segments, including soundtracks of enabled video files. |

Try “a project migration checklist”, “a person standing near a window”, “a photo of a mountain lake”, or “a recording with applause”. Query language need not match the file's language, but retrieval quality varies by content and language.

Documents, images, video and audio have independent switches. Video and audio start disabled. Text queries load only the text encoder, including searches for indexed photos, video scenes and sounds. The combined text/vision encoder loads only when indexing images or video, replaces the text-only worker instead of running a second copy, and exits when the indexing pass finishes. Audio encoding loads in a separate worker only when indexing sound.

From version 0.6.0, **Search photos and videos in the Photos library** adds Apple Photos as another source. Connect it in Actions, grant macOS permission, apply the settings and update the index. Existing content switches apply to folders and Photos alike. Optional iCloud downloads are off by default. Photos results show creation dates, open in Photos and offer full-resolution **Copy Photo**; video results can open the matched clip. See [Apple Photos](APPLE_PHOTOS.md) for cloud storage, permissions and limits.

## Folders and indexing

Use **Add Search Folders** in Model Settings to add folders to the visible **Folders to Search** list, then **Save Search Folders** and **Update Search Index**. Opening the picker again keeps earlier selections. Folder changes preserve the active database and reuse existing vectors; only new or changed files need indexing.

Choose a source under **Remove a Folder** and use **Remove Search Folder** to stop searching and indexing it immediately. Saved entries are kept by default and shown as **Saved for reuse**; re-adding the folder restores them without rebuilding unchanged vectors. Check **Delete this folder's saved index entries** before removal to erase its records and previews from the active and saved model indexes. Selected nested sources, Apple Photos and original files are preserved. Failed cleanup is reported with **Retry Folder Cleanup**. Folder removal also works during indexing; completed work in other sources continues. Added folders are picked up by the next requested update.

Each model remembers its own content and chunk settings. Returning to the same model and settings reopens its existing index; it does not regenerate unchanged vectors. Compatible text/photo vectors can be reused across content selections within the same model revision and chunk settings.

Use **Add excluded folders** to add folders without replacing existing exclusions. Enable **Edit exclusion paths manually** to remove folders or enter absolute paths or `~/` paths. Each exclusion includes its subfolders. Exclusions are directly below Folders to Search and can be saved during indexing. Saving removes their indexed records and previews from saved indexes, discards an in-flight excluded result, and skips subsequent files and media segments. A model call already running finishes before its result is discarded. Unavailable older indexes are reported for cleanup retry; other indexes are still cleaned. Original files remain untouched. Remove the exclusion and run **Update Search Index** to include them again.

The settings window starts with one search-index overview: indexed file/vector totals, actual database disk usage (including WAL and embedded previews), counts by content type, and the current scan or last update. Detailed vector, chunk, media segment and performance options are under **Advanced**. Existing indexed files become searchable before a scan finishes. Long media files take longer, so file counts are not a reliable time estimate.

When automatic updates are off, indexing is explicitly requested. There is no continuous filesystem watcher. When settings are saved, **Update Search Index** is the main settings action; **⌘R** also works in settings and search. A scan adds new files, checks size and modification time, hashes changed candidates, reuses vectors for identical content and removes disappeared files from the index. Unchanged files are skipped. Scan counts persist after restarting the engine. Files modified while preserving both size and modification time require a new indexing pipeline or manually changing their timestamp. Use **Update Search Index** after adding or changing files. **Actions → Cancel Indexing** (⌘.) stops at the next safe boundary and preserves completed files and unvisited saved entries. Resume with Update Search Index. Cancel or wait before switching models; excluded folders can still be saved during indexing.

From version 0.6.2, ordinary updates remember stable skips such as documents without extractable text and files exceeding limits. Edited sources and relevant setting changes are checked again during the next requested update. Temporary failures become eligible for retry after one hour, four hours and then daily; these delays do not schedule a background run. **Actions → Retry Skipped Files** bypasses these delays without rebuilding successful entries. Video scenes remain searchable when only audio decoding fails. Reports distinguish new errors from saved skips and postponed audio retries; the report remains available after an engine restart. The first update of an existing library learns skip reasons and Photos thumbnail metadata; it can still take several minutes. Subsequent unchanged checks avoid reading vector/JPEG payloads and repeating saved unsupported work.

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

To update the extension, install its newer package. Models and indexes stay in shared storage. Packaged engines from 0.5.0 onward stop safely for idle updates and restore the previous engine if startup fails. Cancel indexing or wait before updating. An older engine needs a one-time Mac restart before opening the new extension.

## Privacy and limits

Inference, indexing and previews run locally. Raycast writes temporary JPEG preview files in its own support folder, removes them on selection changes, and expires interrupted sessions on the next preview request after 15 minutes. Tinycast keeps previews inline. Files are not sent to a cloud inference service. Internet access is required for the initial model download; the bundled engine installs offline. The service binds only to loopback and rejects browser-origin requests. In the 0.7.1 engine, library requests also require a random installation token from `auth-token` in shared storage, created with `0600` owner-only permissions. Only the minimal engine identity endpoint is public. This prevents requests without the token; processes that can read the token as the same user can also access the service. Update the launcher extension and companion together. A custom local service with separate storage can use the **Custom Service Token File** extension preference. The change preserves model files, indexes and settings. Local indexes contain paths and extracted content.

- Folder indexing reads only your selected readable folders. Apple Photos access requires explicit macOS permission; importing iCloud-only assets requires enabling downloads. Apple controls its own Photos cache and may download a full original even when a smaller image is requested.
- Scanned PDFs without an extractable text layer need OCR, which is not included.
- Audio search embeds sounds; it does not transcribe or identify speakers or songs.
- Video sampling may miss brief events between sampled frames.
- Photos video previews and matched clips apply orientation metadata. The established ordinary-file sampling pipeline retains its existing orientation behavior.
- Limits are 32 MB/document and 1 GB / 2 hours/media file. Larger media files are skipped rather than silently truncated.
- The release contains no model weights, personal library, existing configuration or indexes.


See [Installation](INSTALL.md), [Troubleshooting](TROUBLESHOOTING.md) and [Development](DEVELOPMENT.md).

## Automatic updates

In **Manage Library**, enable **Update the index in the background**, choose 30 minutes or 1, 3, 6 or 24 hours, and save. New settings default to 1 hour. **Only while connected to power** is on by default. Automatic updates are off by default; manual **Update Search Index** remains available.

Each launcher checks the shared schedule about every 15 minutes. A check starts an incremental engine job and returns immediately; it does not wait for a long import. Timing is approximate and the launcher may defer checks. An overdue update runs on the next eligible launcher check after reopening the app; it does not start a new full waiting interval. Immediate execution on app launch is not guaranteed. The selected model uses its existing index: unchanged files reuse vectors, changed/new files are saved as they finish, and missing files are removed. Temporary failures retain their retry delay; there is no separate timer retrying them between passes.

The first explicit run activates native background checks in that launcher. Saving automatic settings does this when there are no other unsaved edits. Otherwise, save or discard those edits and choose **Activate Background Checks** in Actions. Run that action once after reinstalling the extension or when enabling checks in a second launcher. Both launchers share settings and the engine prevents overlapping passes.

**Pause Indexing** stops at a safe boundary and releases model workers. Wait for the pause to finish, then **Resume Indexing**. Completed files are retained across engine restarts; an unfinished file can be processed again. **Cancel Indexing** discards the current continuation and leaves completed files searchable. Automatic updates can run again at the next interval.

Closing a launcher window does not interrupt indexing. Fully quitting the owning launcher or removing/updating its extension stops its job. Automatic work also pauses when external power is required but unavailable. After a normal Mac sleep, the job can continue on wake if its owner and power condition still hold. After a process crash or reboot, a subsequent authorized launcher check resumes from committed files. No LaunchAgent, login item or independent scheduling service is installed.

A background check never installs or upgrades the engine, downloads a missing model or runs initial Core ML preparation. If preparation is needed after an update, open Search once. The same controls work with a compatible Custom Local Service; its own settings remain authoritative. Advanced console jobs without a launcher owner remain independent of launcher lifetime.

Removing a search folder without deleting its index stops scanning and hides its retained entries. A folder still covered by another selected parent source remains in scope; exclude it to stop indexing through every source. A file moved to Trash is already absent from its indexed path and is hidden from search immediately. The next successful parent-folder scan removes its saved record, even before Trash is emptied. An unavailable whole source is preserved instead of being assumed deleted, so disconnected drives cannot erase their indexes; use explicit folder removal and index cleanup when you want to forget that source.
