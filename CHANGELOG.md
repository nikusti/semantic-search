# Changelog

The complete published version history is kept here. Earlier installation archives are retired from this repository; use the latest release. Dates are in UTC.

## Unreleased

- Add an optional Apple Photos source with explicit macOS permission and optional iCloud downloads.
- Index temporary image representations locally; retain vectors and small thumbnails, with a bounded preview cache.
- Reuse unchanged Photos entries and remove unavailable entries only after a completed library pass. Preserve folder indexes and completed work on cancellation.
- Connect the library, track import progress, and open matched assets in Photos from the launcher.
- Keep text search responsive during cloud preview requests and repair thumbnails without re-encoding images.
- Include Apple Photos videos and optional soundtracks using the shared MLX/Core ML media pipeline. Keep original assets intact, reuse unchanged entries, and show previews/open short clips at the matched timestamp.
- Honor content switches for the Photos source and report deferred iCloud videos and per-kind vector storage.
- Preserve phone-video orientation when encoding Photos scenes and preparing matched clips, without changing existing filesystem video vectors.

## [0.5.1] - 2026-10-11

- Handle malformed custom service URLs with a repair message instead of crashing setup.
- Prevent stale settings polls from resetting applied changes or reporting obsolete errors.
- Preserve exact native folder paths, including whitespace and line breaks, through indexing and exclusion requests.
- Keep the exclusion editor synchronized with accepted server updates while preserving unfinished input and newer local edits.
- Share nine UI regression tests and Raycast manifest/type configuration between ZIP and Store builds.

## [0.5.0] - 2026-10-11

- Add a native companion-engine installer and generate a small Store review package from the same extension source.
- Guide missing-engine installation without terminal commands or background executable downloads.
- Manage the library within Search, and safely stop idle packaged engines for updates while preserving models and indexes.
- Clarify local vector search, models and privacy; record Photos/iCloud and automatic indexing in the roadmap.
- Keep Tinycast inline previews and use temporary local JPEGs in Raycast; avoid redundant Search renders during status polling.
- Restore the previous engine after failed updates, including a replacement holding the service lock.
- Provide an unsigned companion installer; Store publication and optional Developer ID signing/notarization remain pending.

## [0.4.1] - 2026-10-11

- Core ML loads only the text function needed for a query, retaining all supported input lengths and the original packed document batch processing.
- Prepare every text function once during model setup or runtime installation, before interactive search. Preparation is refreshed after native bridge or OS updates and reused across normal restarts.
- Preserve existing Core ML vectors, index identities, optional media encoders and one-minute idle unloading. MLX inference is unchanged.

## [0.4.0] - 2026-10-11

- Core ML is available in Model Settings, with verified model downloads and support for text, photos, video scenes and audio. Optional encoders download when enabled; audio loads only for sound indexing.
- Each model remembers its own settings and saved index. Compatible vectors are reused when content switches change, without mixing vectors from different model revisions or backends.
- Cancel Indexing (⌘.) stops at the next safe boundary and keeps completed files. Settings explain how to cancel before switching models; exclusions remain editable during indexing.
- Reduce the bundled engine size and use macOS video encoding and HEIC decoding.


## [0.3.0] - 2026-10-11

- Load only the text encoder for queries, including searches for indexed photos, video and audio.
- Load the combined text/vision encoder only for image and video indexing; exit the previous worker before replacement.
- Reuse the combined encoder throughout an indexing pass and release it when the pass finishes.
- Preserve existing model identities, vectors, indexes, settings and one-minute idle unloading.
- Prepare a local Python bytecode cache during engine setup, avoiding repeated library compilation on model startup without shipping build-machine bytecode.
- Show whether the image encoder is loaded in runtime status.

## [0.2.3] - 2026-10-11

- Consolidate index totals, disk usage, content counts and scan progress into one overview.
- Put the selected model, search folders, exclusions and content choices together.
- Add a native picker for excluded folders, with optional manual path editing.
- Hide vector, chunk, media segment and performance options under Advanced.
- Report actual SQLite and WAL disk usage, including embedded previews.

## [0.2.2] - 2026-10-10

- Place Excluded folders directly below Folders to Search.
- Save exclusions during indexing without replacing the model or index.
- Discard in-flight excluded files and stop processing subsequent media segments.
- Prune excluded entries and previews across saved application indexes.
- Refresh active search results as soon as exclusions are saved.
- Show feedback instead of silently ignoring Apply during a running scan.
- Offer retry for pending exclusion cleanup.

## [0.2.1] - 2026-10-10

- Update Search Index is the primary action when model settings are saved.
- The same incremental scan is available with Cmd+R from settings and search.
- Persist added, updated, unchanged, removed and failed file counts.
- Refresh active search results when an indexing pass finishes.
- Reject scans with no selected folders and require applying edited settings first.
- Reduce the ready-to-use package size.

## [0.2.0] - 2026-10-10

- One ready-to-use ZIP for Tinycast and Raycast, with an embedded local engine and optional official Raycast bundle.
- Automatic verified runtime installation and service startup, shared by both launchers.
- Folder selection from Model Settings through the native folder picker.
- Bundled media and PDF decoders remove external FFmpeg and Poppler installation.
- Separate short end-user instructions and source/developer setup.

## [0.1.0] - 2026-10-10

Initial standalone source package.

- Local semantic search for documents, photos, video scenes and audio.
- Shared storage and model settings for Tinycast and Raycast.
- Pinned EmbeddingGemma 2 MLX 8-bit model with configurable vector length.
- Download, compatibility validation and deletion of supported models.
- Incremental indexing, recursive exclusions and progress by content type.
- PDF layout previews and photo quality refinement with stable geometry.
- On-demand workers, idle unloading and a separate audio indexing worker.
- Portable setup helpers and English documentation.
