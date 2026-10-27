# Changelog

The complete published version history is kept here. Earlier installation archives are retired from this repository; use the latest release. Dates are in UTC.

## [0.7.2] - 2026-10-27

- Serialize Core ML preparation across foreground launches and model setup so repeated openings share one preparation after a macOS or native bridge update.
- Explain when Core ML is being prepared for the current macOS version and when another launch is already preparing it.
- Preserve existing model weights, indexes and settings; keep scheduled launcher checks from running foreground Core ML startup preparation.

## [0.7.1] - 2026-10-23

- Authenticate local library requests with a persistent installation token stored with owner-only permissions. Preserve browser-origin and loopback checks; expose only minimal engine identity without authentication.
- Keep Raycast Store packaging separate from development tests and Tinycast wording. Name the Store extension Semantic File Search, with Search Files as its main command, and retain compatibility with existing shared extensions.
- Reconcile accepted folder and exclusion changes after settings apply, including intermediate indexing polls, while preserving later user edits and model capability changes.
- Preserve existing models, indexes and shared storage. Existing clients must be updated together with the authenticated companion engine.

## [0.7.0] - 2026-10-12

- Add opt-in incremental background updates with 30-minute and 1/3/6/24-hour schedules (1 hour by default) and an external-power condition; off by default.
- Add persistent pause/resume and cancellation, retain completed files after interruption, and stop launcher-owned jobs when their launcher exits or extension is removed.
- Share scheduling between Tinycast and Raycast and prevent overlapping passes. Keep background checks short; engine/model installation and Core ML preparation remain foreground operations.
- Preserve unsaved settings during background activation, support compatible custom local services, and retain existing model/vector/index identities.

## [0.6.5] - 2026-10-12

- Prefer photos over video scenes within a 0.03 cosine-similarity margin in All Files. Preserve stronger video matches, raw scores, document/audio positions, best-segment timestamps and explicit content filters; existing vectors need no rebuilding.

## [0.6.4] - 2026-10-12

- Preserve folder additions made while a full settings request is pending, and keep a newer folder removal's explicit index-deletion choice.
- Explain and defer removal of a submitted source until model setup accepts it; do not show a removal that completion polling would undo.
- Keep saved indexes and skip records for removed explicit sources inside hidden or normally skipped folders. Hide those entries unless a selected source can actually scan them; clean them only when requested.
- Allow settings changes with saved offline sources. Refresh thumbnails only for selected entries and repair returned sources without re-embedding their vectors.
- Honor image-content toggles while video remains enabled, preserving the compatible saved indexes for later reuse.
- Require independent code review and regression verification before releases or Store PR updates.

## [0.6.3] - 2026-10-12

- Add search folders cumulatively and show the full selected list with indexed file counts. Native picker selections no longer replace existing sources.
- Remove individual sources during indexing. Keep their saved entries hidden for reuse by default, or explicitly delete their records and previews across saved indexes.
- Preserve the active database and vectors when folders change. Protect remaining nested sources, skip duplicate nested scans, and retain pending cleanup for retry.
- Keep concurrent launcher edits and unfinished folder additions from being overwritten by settings polling.

## [0.6.2] - 2026-10-12

- Check unchanged Photos using compact metadata without reading saved vectors or JPEG payloads. Learn legacy thumbnail metadata once and avoid repeatedly regenerating small representations.
- Persist stable indexing limitations until the source or relevant settings change. Back off temporary failures for one hour, four hours and then daily.
- Preserve searchable filesystem video scenes when soundtrack decoding fails, matching Photos behavior; retry only missing audio windows.
- Add Retry Skipped Files in Search and Manage Library. Report saved skips, postponed retries and incomplete audio separately, and retain access to the local report after restart.
- Preserve model/index identities and existing vectors. Scheduled background indexing remains a separate planned feature.

## [0.6.1] - 2026-10-12

- Stop obsolete Photos metadata retries after switching results, closing Search or opening Manage Library. Preserve retries for the active photo and explicit clipboard copying.

## [0.6.0] - 2026-10-12

- Keep Search and settings in their normal loading views while connecting; show engine installation/retry instructions only after a connection failure.
- Show the ready search prompt immediately on an empty launch and count fixed-size vectors without reading their payloads when opening/polling library statistics.
- Replace Photos link copying with a native full-resolution image clipboard action and show the Photos creation date instead of internal identifiers. Cache dates lazily without rebuilding vectors.
- Open matched video clips without sound when a malformed soundtrack cannot be decoded, and show a clear notice.
- Preserve searchable Photos video scenes when audio decoding fails, report incomplete soundtracks, and retry only missing audio windows on the next incremental scan.
- Preserve Photos source status in Search so a Photos-only library is correctly labeled without prompting for folders.
- Place photo/video file details beneath the preview in its scrollable content, preserving the image size and showing creation dates for Photos assets.
- Show a neutral settings-loading state, offer retry only after an error, and retry fetching settings without requiring an engine restart.
- Add an optional Apple Photos source with explicit macOS permission and optional iCloud downloads.
- Index temporary image representations locally; retain vectors and small thumbnails, with a bounded preview cache.
- Reuse unchanged Photos entries and remove unavailable entries only after a completed library pass. Preserve folder indexes and completed work on cancellation.
- Connect the library, track import progress, and open matched assets in Photos from the launcher.
- Keep text search responsive during cloud preview requests and repair thumbnails without re-encoding images.
- Include Apple Photos videos and optional soundtracks using the shared MLX/Core ML media pipeline. Keep original assets intact, reuse unchanged entries, and show previews/open short clips at the matched timestamp.
- Honor content switches for the Photos source and report deferred iCloud videos and per-kind vector storage.
- Preserve phone-video orientation when encoding Photos scenes and preparing matched clips, without changing existing filesystem video vectors.
- Copy Photos video bytes into a bounded private temporary file so the local decoder can read them under macOS Photos permissions; clean up cancelled and incomplete copies.

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
