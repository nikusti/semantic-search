# Roadmap

## Next: Store installation

- Keep one engine and UI codebase, with bundled ZIP and small Store packages.
- Native companion installer and small Store source package are implemented for the 0.5.0 preview. Python and required libraries are included; no developer toolchain or terminal steps for users.
- Offline isolated installation, repeated installation, Store engine discovery and synthetic search pass. Complete final host/update tests and clean-account validation before submission.
- Complete signing/notarization, Store metadata, screenshots and independent privacy/license review before public distribution.

## After Store packaging

### Apple Photos and iCloud library

Investigate a local importer using the authorized Photos library, including cloud-only assets, temporary downloads, progress/cancellation and disk-space limits. Prefer building and searching the index on the Mac. If local indexing is impractical, evaluate indexing on a user-controlled server with search from the Mac. Do not upload a library or introduce remote processing without an explicit decision.

### Automatic incremental indexing

Design an optional schedule or watcher that updates new, changed and deleted files using the existing incremental scanner. Respect exclusions, cancellation, power/battery limits and model unloading. Keep manual Update Search Index available. Decide between a lightweight macOS mechanism and an agent only after measuring the requirements.

These two features are queued, not implemented. Revisit them with the user when the Store installation stage is complete.
