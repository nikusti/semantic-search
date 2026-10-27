# Store publication

Semantic Search is currently distributed through GitHub releases. Publishing a GitHub repository does not publish a Raycast Store extension.

Tinycast enables the [Raycast Store registry](https://tinycast.dev/docs/extensions/installing/) by default and can install its prebuilt extensions. A compatible Store release should therefore be installable from Tinycast too; both hosts still need an installation test.

Raycast publication creates a pull request in [raycast/extensions](https://github.com/raycast/extensions). The Raycast team reviews it and, after acceptance and merge, publishes the extension. See the [publication workflow](https://developers.raycast.com/basics/publish-an-extension).

The extension has been submitted for review in [raycast/extensions#32190](https://github.com/raycast/extensions/pull/32190). The source repository and companion assets are public. The PR targets the published 0.7.1 companion and includes Apple Photos, clipboard/date actions, startup fixes, incremental scan improvements, independently reviewed folder management and optional scheduled updates. This is an open review, not an accepted Store listing. The GitHub author is subscribed to PR updates.

The submission contains the lightweight launcher UI and two native Raycast Capture Window screenshots using a synthetic document and a generated photograph. Engine/model binaries and local library data are not included in the PR. The unsigned companion installer is disclosed in the PR and setup guide; acceptance of this installation design remains a reviewer decision.

The [Store preparation guidelines](https://developers.raycast.com/basics/prepare-an-extension-for-store) and [publication workflow](https://developers.raycast.com/basics/publish-an-extension) govern review. Maintainer feedback and automated checks are handled on the same PR; merge triggers Store publication.

## One project, two distribution packages

The Store design keeps one engine and one extension codebase. The offline ZIP contains both. The Store package contains the small launcher interface and connects to the same separately installed local engine.

Both packages should use the same pinned engine release, model revisions, indexing pipeline, shared storage and local protocol. Packaging alone does not change retrieval quality, model memory use or search speed. Installation and update steps differ; startup and search still need verification in both hosts.

Version 0.5.0 provides a Store source package and a native companion installer. Store review is pending; the companion installer is distributed without Developer ID signing/notarization. A simple download of an executable from a maintainer-controlled server is not automatically acceptable under Raycast's rules. The engine installation method must satisfy the Store review while keeping setup free of terminal commands.

## Companion installation experience

The submitted Store release uses a companion-engine installer. Published integrations already use this approach: the [LM Studio extension](https://github.com/raycast/extensions/blob/main/extensions/lm-studio/README.md) connects to an independently installed local application. This is a precedent for the design, not approval of Semantic Search.

1. After Store acceptance, install **Semantic File Search** from the launcher Store.
2. Open Search. If the engine is missing, show one setup screen explaining that processing is local, the supported Mac requirements, and the required disk space.
3. Offer **Download Search Engine**, opening the current installer release in the browser. The user explicitly opens a normal macOS installer. It contains Python and all required libraries; there are no Homebrew, npm, pip or terminal steps.
4. The installer verifies and installs the same pinned runtime used by the ZIP. Returning to the extension detects the installed engine and offers model download and folder selection.
5. An existing compatible engine skips setup entirely. Installing the extension in the other launcher reuses the same model, settings and indexes.

The Store extension must not silently download and execute our own runtime archive. The installer is an explicit companion installation. Developer ID signing, hardened runtime and [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) make macOS installation smoother. Raycast does not document Developer ID signing as a prerequisite for submitting an extension PR; companion acceptance remains a reviewer decision. Source archives, dependency notices and reproducible native build recipes remain available with each engine release. Signing and notarization require a maintainer's Apple Developer credentials; they are not prerequisites for end users.

The current ZIP already includes portable Python, MLX, Core ML support and the decoders. Initial model weights are a separate, explicit download. A Store installation still needs that same runtime and model; changing packaging does not remove these requirements or move inference to a cloud service.

## Shared implementation

Keep the engine, local protocol and launcher UI in this repository. Generate two packages from one release:

| Package | Contents | Engine setup |
| --- | --- | --- |
| Ready-to-use ZIP | Built launcher commands, runtime manifest and complete runtime payload | Existing offline automatic setup |
| Store extension | The same commands, small assets and expected engine/protocol metadata | Detect a companion installation and guide setup when missing |

Separate installation policy from service startup in `extension/src/runtime.ts`. Bundled mode may verify and unpack the included archive. Store mode may discover and start an explicitly installed companion, but must not contain the large archive or run a fallback executable download. Both modes check application identity, engine version and protocol before making requests.

Use the same atomic installation checks and single-service lock. Updates must finish or safely cancel active work, stop the old service, and select the new runtime without deleting models or indexes. A failed update must preserve the previous working installation. Version 0.5.0 stops an idle packaged service through its own local API, rejects updates during active jobs, and attempts to restart the previous runtime if the replacement fails. Engines older than 0.5.0 still need a one-time restart because they do not expose this safe stop endpoint.

Static launcher preferences should use Raycast's preferences API. Model downloads, index status, cancellation and folder management need a library-management screen with live state; discuss its fit with the Store's restriction on separate configuration commands during review. The generated Store package exposes Search and a short no-view background check. Manage Library uses the navigation API to open the shared model/index controls within Search. The ZIP retains its separate settings command for existing users; confirm the live library screen with Store reviewers.

## Review revision for 0.7.1

The source repository remains `nikusti/semantic-search`. The generated Store folder and package name are `semantic-file-search`, titled **Semantic File Search**, with **Search Files** as the main command. The shared development extension retains its existing name and Tinycast support. Both names are accepted by launcher ownership checks.

`build_store.py` omits development tests, test scripts and direct test dependencies, adapts user-facing host wording for Raycast, and regenerates the Store lockfile with npm. Tests remain in this repository and run before packaging. Store screenshots are kept in `packaging/store-metadata/` and copied unchanged into the generated package; the shared `extension/metadata/` stays separate. The Store icon has rounded corners, smooth edges and transparent padding. The extension changelog contains one initial-release entry.

Authenticated library requests require the matching 0.7.1 companion. Independent source reviews, native Raycast verification and installer smoke tests were completed, and the maintainer approved publication. Future revisions must validate the companion, both launcher clients and generated Store source, undergo independent review and receive maintainer approval before publication or PR changes.

## Build the two packages

The same engine payload and manifest must be used by every package. Build/sign the runtime once before building the installer, ZIP and Store package. Do not rebuild or re-sign the payload after generating the Store manifest. The signer reuses an already sealed payload; changing signing identities requires a fresh runtime build.

```sh
python scripts/build_runtime.py
python scripts/package_native_sources.py
python scripts/build_installer.py
python scripts/build_store.py
```

This creates a native installer under `.build/companion` and a small Store source tree under `.build/store/semantic-file-search`, plus review ZIPs outside the checkout. The Store tree has no runtime archive or model weights. The unsigned installer is not notarized and macOS may require explicit approval when opening a downloaded copy. Disclose this in release notes and during Store review.

For an optionally signed and notarized companion build, use an existing Developer ID Application identity and a configured notarytool Keychain profile:

```sh
python scripts/build_installer.py --identity "Developer ID Application: Example Developer (TEAMID)" --notary-profile "semantic-search-notary"
python scripts/build_store.py
```

The signing path signs nested runtime executables/libraries, updates the shared payload hash, signs the app with hardened runtime, submits to Apple's notary service, staples the ticket and checks Gatekeeper acceptance. Python's documented entitlements permit native libraries/JIT and replacement of LGPL libraries. This credential-dependent path still requires a real signed/quarantined installation test; an ad-hoc test build does not prove notarization success.

Build both launcher packages only after the final payload is sealed. Run `scripts/check_packages.py` to verify matching payload hashes, native sources and absence of a runtime archive in the Store package. Developer ID signing generally requires a paid [Apple Developer Program](https://developer.apple.com/programs/) membership; end users do not need one. Raycast's [publication workflow](https://developers.raycast.com/basics/publish-an-extension) uses a GitHub PR, and its [free plan](https://www.raycast.com/pricing) includes extensions/developer tooling.

## Validation and review status

- Downloaded installer extraction, fresh/repeat installation with a minimal PATH, Store discovery, both built-in models on synthetic text/photos, PDF/photo previews, incremental scans, exclusions, idle unloading, engine restart and index-preserving model deletion have been tested. Raycast UI tests cover setup, text/photo results, previews, Actions shortcuts and library navigation. An isolated Tinycast command also verifies photo search and its inline preview loader. Isolated failed-update tests restarted the previous engine and preserved its data, including a replacement that remained alive and held the service lock. Service/extension tests cover refusal to stop active jobs, data preservation and no executable fallback in Store mode.
- The Raycast account author is `nik_ustinovich`. Store packages use the official build/lint and upstream metadata validator for two 2000 by 1250 screenshots, a 512-pixel icon, supported category, README and changelog. Version 0.7.1 passes 200 engine/native tests and 61 shared launcher/runtime checks, including 41 Store UI/command regressions. Search, previews, Actions, library navigation and a native scheduled invocation were tested in Raycast against isolated storage. Both launchers have native background activation checks.
- Optionally add Developer ID signing/notarization for smoother macOS installation. Without it, disclose macOS opening approval. Current validation uses fresh isolated storage and a minimal PATH, not a factory-clean Mac or a separate macOS account.
- Independent source/history/release-asset privacy and license reviews were completed before publication. The final submission scope and source/package privacy checks passed; reviewed companion assets and matching native dependency sources are public.
- [Store PR #32190](https://github.com/raycast/extensions/pull/32190) is open for review. Verified automated-review fixes are also in the shared source; the earlier fixes ship in 0.5.1, and the current 0.7.1 submission includes Photos, incremental indexing, folder management, photo preference, scheduled updates and their independently reviewed fixes. Review feedback and CI results belong to that PR. Store acceptance of the companion design remains a reviewer decision.

To remove only the engine, close/stop the service and delete its versioned `runtime` folder in shared storage. Keep `models`, `data` and `config.toml` to preserve the library. Removing the installer app itself does not uninstall the installed engine.

## Possible future native Tinycast integration

Tinycast already runs dictation in an on-demand helper, downloads pinned Core ML weights from settings and terminates the helper after idle time. Its [dictation architecture](https://github.com/abue-ammar/tinycast/blob/main/docs/features/dictation.md) is a useful precedent for semantic search.

A first integration could provide native search and library controls while using the same engine and protocol. A later Swift/Core ML implementation could remove Python from that particular backend, but would require matching tokenization, preprocessing, model revisions and index identities. Treat it as a separate measured implementation change, not a packaging shortcut. Native UI alone does not eliminate model initialization time or guarantee faster retrieval.

Tinycast's [contribution guidelines](https://github.com/abue-ammar/tinycast/blob/main/CONTRIBUTING.md) require an approved issue before a feature PR, strict main-app memory discipline and measured leak-free teardown. Agree on the feature and helper-process memory budget with the maintainer before implementing an upstream contribution. No native integration or external proposal has been submitted.
