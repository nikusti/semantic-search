# Changelog

## 0.4.0

- Core ML is available in Model Settings, with verified model downloads and support for text, photos, video scenes and audio. Optional encoders download when enabled; audio loads only for sound indexing.
- Each model remembers its own settings and saved index. Compatible vectors are reused when content switches change, without mixing vectors from different model revisions or backends.
- Cancel Indexing (⌘.) stops at the next safe boundary and keeps completed files. Settings explain how to cancel before switching models; exclusions remain editable during indexing.
- Native codecs are rebuilt without GPL/nonfree components. Unused OpenCV and Pillow-HEIF binaries are removed; macOS decodes HEIC and VideoToolbox encodes video previews.
- Complete corresponding native-library sources, licenses and build recipes accompany releases.


## 0.3.0

- Load only the text encoder for queries, including searches for indexed photos, video and audio.
- Load the combined text/vision encoder only for image and video indexing; exit the previous worker before replacement.
- Reuse the combined encoder throughout an indexing pass and release it when the pass finishes.
- Preserve existing model identities, vectors, indexes, settings and one-minute idle unloading.
- Prepare a local Python bytecode cache during engine setup, avoiding repeated library compilation on model startup without shipping build-machine bytecode.
- Expose vision residency in runtime status and cover mode transitions, process exit and disabled content with regression tests.

## 0.2.3

- Consolidate index totals, disk usage, content counts and scan progress into one overview.
- Put the selected model, search folders, exclusions and content choices together.
- Add a native picker for excluded folders, with optional manual path editing.
- Hide vector, chunk, media segment and performance options under Advanced.
- Report actual SQLite and WAL disk usage, including embedded previews.

## 0.2.2

- Place Excluded folders directly below Folders to Search.
- Save exclusions during indexing without replacing the model or index.
- Discard in-flight excluded files and stop processing subsequent media segments.
- Prune excluded entries and previews across saved application indexes.
- Refresh active search results as soon as exclusions are saved.
- Show feedback instead of silently ignoring Apply during a running scan.
- Add regression tests for live exclusions, persistence, queued files and audio cancellation.

## 0.2.1

- Update Search Index is the primary action when model settings are saved.
- The same incremental scan is available with Cmd+R from settings and search.
- Persist added, updated, unchanged, removed and failed file counts.
- Refresh active search results when an indexing pass finishes.
- Reject scans with no selected folders and require applying edited settings first.
- Add regression coverage for equal-size edits, metadata-only changes and saved scan summaries.
- Trim bundled test frameworks/fixtures and preserve pinned codec source and license records.
- Audit nested runtime archives for build-machine paths and metadata.

## 0.2.0

- One ready-to-use ZIP for Tinycast and Raycast, with an embedded local engine and optional official Raycast bundle.
- Automatic verified runtime installation and service startup, shared by both launchers.
- Folder selection from Model Settings through the native folder picker.
- Bundled media and PDF decoders remove external FFmpeg and Poppler installation.
- Separate short end-user instructions and source/developer setup.
- Fresh-install, concurrent-launch and decoder regression checks.

## 0.1.0

Initial standalone source package.

- Local semantic search for documents, photos, video scenes and audio.
- Shared storage and model settings for Tinycast and Raycast.
- Pinned EmbeddingGemma 2 MLX 8-bit model with configurable vector length.
- Download, compatibility validation and deletion of supported models.
- Incremental indexing, recursive exclusions and progress by content type.
- PDF layout previews and photo quality refinement with stable geometry.
- On-demand workers, idle unloading and a separate audio indexing worker.
- Portable setup helpers, English documentation and synthetic tests.
