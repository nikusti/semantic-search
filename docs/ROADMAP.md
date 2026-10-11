# Roadmap

## In review: Store installation

- Keep one engine and UI codebase, with bundled ZIP and small Store packages.
- Version 0.5.0 includes the native companion installer and small Store source package. Python and required libraries are included; no developer toolchain or terminal steps for users.
- Installation/update recovery, Store discovery, launcher UI, synthetic search, build/lint and screenshot checks passed. Independent privacy/license reviews were completed before publication. A factory-clean Mac or separate-account test remains additional validation.
- [Raycast Store PR #32190](https://github.com/raycast/extensions/pull/32190) is submitted and awaiting review. Handle feedback in that PR. The companion is currently unsigned; Developer ID signing/notarization is a future installation improvement.

## Next development

### Apple Photos and iCloud library — in development

The local PhotoKit importer supports photographs and videos, with explicit library permission, optional iCloud downloads, shared scene/soundtrack indexing for MLX and Core ML, persistent vectors/small thumbnails, bounded previews, progress/cancellation, and incremental updates. A real 100-photo import/search check and synthetic photo/video/soundtrack flows with both models have passed, including previews, matched clips and text-only search after unloading. Phone-video rotation is covered by a real decoder/segment regression. Real-library video and installation checks remain part of development validation before shipping. Folder indexes remain intact. Keep all processing on the Mac; do not introduce remote processing without an explicit decision.

### Automatic incremental indexing

Design an optional schedule or watcher that updates new, changed and deleted files using the existing incremental scanner. Respect exclusions, cancellation, power/battery limits and model unloading. Keep manual Update Search Index available. Decide between a lightweight macOS mechanism and an agent only after measuring the requirements.

Automatic indexing remains queued. Photos development can continue while Store review is pending. Keep changes separate from the submitted version until tested and ready for a new release.
