# Roadmap

## In review: Store installation

- Keep one engine and UI codebase, with bundled ZIP and small Store packages.
- Version 0.5.0 includes the native companion installer and small Store source package. Python and required libraries are included; no developer toolchain or terminal steps for users.
- Installation/update recovery, Store discovery, launcher UI, synthetic search, build/lint and screenshot checks passed. Independent privacy/license reviews were completed before publication. A factory-clean Mac or separate-account test remains additional validation.
- [Raycast Store PR #32190](https://github.com/raycast/extensions/pull/32190) is submitted and awaiting review. Handle feedback in that PR. The companion is currently unsigned; Developer ID signing/notarization is a future installation improvement.

## Next development

### Apple Photos and iCloud library — implemented, release pending

The 0.6.0 source includes the native PhotoKit importer for photographs and videos, optional iCloud downloads, incremental updates/cancellation, bounded temporary files and previews, full-resolution clipboard copying and creation dates. Both MLX and Core ML use the shared scene/soundtrack pipeline. Partial video soundtracks can be retried without rebuilding completed scenes. Text searches use saved vectors without loading media encoders.

Independent source review, isolated installer tests with both models, a real 1,000-photo/50-video comparison and a complete library import with 100 retrieval queries have been completed locally. Phone-video orientation, cancellation, exclusions, originals preservation and index-preserving model deletion are covered by regressions. Published installation packages and the Store submission remain on 0.5.1 until the next release is prepared.

### Automatic incremental indexing

Design an optional schedule or watcher that updates new, changed and deleted files using the existing incremental scanner. Respect exclusions, cancellation, power/battery limits and model unloading. Keep manual Update Search Index available. Decide between a lightweight macOS mechanism and an agent only after measuring the requirements.

Automatic indexing remains queued. Update the Store submission after publishing the next companion release so its metadata and engine payload refer to the same tested version.
