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

### Incremental indexing improvements — 0.6.2 in development

Use compact Photos metadata for unchanged checks, persist stable skip reasons, back off temporary failures, and offer explicit retries. Preserve video scenes independently of audio decoding failures. Validate on isolated copies of the full library before enabling a schedule. Existing model identities and vectors stay compatible.

### Automatic incremental indexing

Design an optional schedule or watcher that updates new, changed and deleted files using the existing incremental scanner. Respect exclusions, cancellation, power/battery limits and model unloading. Keep manual Update Search Index available. Decide between a lightweight macOS mechanism and an agent only after measuring the requirements.

Automatic indexing remains queued. The Store submission has been updated to the published 0.6.1 companion release. Continue handling reviewer feedback in the existing PR.
