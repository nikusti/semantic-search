# Roadmap

## In review: Store installation

- Keep one engine and UI codebase, with bundled ZIP and small Store packages.
- Version 0.5.0 includes the native companion installer and small Store source package. Python and required libraries are included; no developer toolchain or terminal steps for users.
- Installation/update recovery, Store discovery, launcher UI, synthetic search, build/lint and screenshot checks passed. Independent privacy/license reviews were completed before publication. A factory-clean Mac or separate-account test remains additional validation.
- [Raycast Store PR #32190](https://github.com/raycast/extensions/pull/32190) is submitted and awaiting review. Handle feedback in that PR. The companion is currently unsigned; Developer ID signing/notarization is a future installation improvement.

## Next development

### Apple Photos and iCloud library — released in 0.6.0

Version 0.6.0 includes the native PhotoKit importer for photographs and videos, optional iCloud downloads, incremental updates/cancellation, bounded temporary files and previews, full-resolution clipboard copying and creation dates. Both MLX and Core ML use the shared scene/soundtrack pipeline. Partial video soundtracks can be retried without rebuilding completed scenes. Text searches use saved vectors without loading media encoders.

Independent source review, isolated installer tests with both models, a real 1,000-photo/50-video comparison and a complete library import with 100 retrieval queries have been completed locally. Phone-video orientation, cancellation, exclusions, originals preservation and index-preserving model deletion are covered by regressions. Version 0.6.0 installation packages include these changes. The pending Store submission now targets the matching 0.6.1 companion payload and launcher UI.

### Incremental indexing improvements — released in 0.6.2

Version 0.6.2 uses compact Photos metadata for unchanged checks, persists stable skip reasons, backs off temporary failures, and offers explicit retries. Video scenes are preserved independently of audio decoding failures. Isolated full-library tests, unchanged-vector integrity checks, installer tests with both models, and 162 engine/extension regressions passed. Existing model identities and vectors stay compatible.

### Folder management — released in 0.6.3

Version 0.6.3 adds cumulative folder selection, a visible source list and removal with optional index cleanup. Root-only edits preserve the current database; saved entries can be retained for reuse without remaining searchable. Nested sources and active indexing are protected.

### Review follow-up — pending in 0.6.4

Independent backend and launcher reviewers reproduced settings-request races, retained nested-source cleanup, offline-source validation and image/video content-toggle combinations. The fixes and regression coverage have passed repeated review locally; published 0.6.3 archives and the installed engine remain unchanged. Complete package validation before publishing the next patch or updating the Store PR.

### Automatic incremental indexing

Version 0.7.0 is planned to use opt-in background commands supported by both launchers, without a separate LaunchAgent. Keep one incremental engine, charging/schedule conditions, pause/resume/cancel, and manual Update Search Index. Automatic jobs must stop when their launcher exits or their extension is removed, recover from sleep/restart using committed entries, and avoid duplicate work across hosts.

Automatic indexing remains queued. The Store submission has been updated to the published 0.6.1 companion release. Continue handling reviewer feedback in the existing PR.
