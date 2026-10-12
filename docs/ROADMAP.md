# Roadmap

## Store publication — awaiting review

[Raycast Store PR #32190](https://github.com/raycast/extensions/pull/32190) targets the same 0.7.0 engine and shared launcher UI as the bundled ZIP. Automated checks and human acceptance are separate steps. Continue handling feedback in that PR.

One codebase produces two packages: a bundled ZIP and a small Store extension with a separately installed native companion. The companion includes Python and decoders; users need no terminal setup. Developer ID signing/notarization remain an installation improvement. A factory-clean Mac or separate-account test is additional validation.

## Delivered

- Apple Photos/iCloud photographs, video scenes and optional soundtracks: 0.6.0, with local processing, permission controls, creation dates and full-resolution photo copying.
- Faster incremental scans, saved skip reasons and delayed temporary retries: 0.6.2.
- Additive folder selection and source removal with optional index cleanup: 0.6.3, followed by independently reviewed settings/source fixes in 0.6.4.
- A small preference for photos over similarly matching video scenes: 0.6.5.
- Opt-in scheduled updates, external-power conditions, persistent pause/resume/cancel and launcher lifetime controls: 0.7.0. Both launchers share scheduling; no LaunchAgent is installed. Existing model indexes and vectors are reused.

See [version history](../CHANGELOG.md) and [automatic update behavior](USAGE.md#automatic-updates) for details.

## Further validation and improvements

- Test physical sleep/wake and charger changes on a separate test Mac; simulated transitions and native launcher checks are covered.
- Evaluate a progress watchdog for unusually stalled indexing beyond the existing operation-specific timeouts.
- Consider native Tinycast integration while keeping the same engine, model revisions and index format.
