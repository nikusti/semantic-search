# Store publication planning

Semantic Search is currently distributed through GitHub releases. Publishing a GitHub repository does not publish a Raycast Store extension.

Tinycast enables the [Raycast Store registry](https://tinycast.dev/docs/extensions/installing/) by default and can install its prebuilt extensions. A compatible Store release should therefore be installable from Tinycast too; both hosts still need an installation test.

Raycast publication creates a pull request in [raycast/extensions](https://github.com/raycast/extensions). The Raycast team reviews it and, after acceptance and merge, publishes the extension. See the [publication workflow](https://developers.raycast.com/basics/publish-an-extension).

Before submitting this project:

- Replace the large embedded engine asset with a distribution design meeting Raycast's binary-dependency rules. The guidelines prohibit heavy bundled dependencies and require verifiable sources, builds and integrity checks. A maintainer-controlled binary download is not automatically acceptable.
- Confirm the Raycast account author, latest supported API, macOS restriction and required metadata. Add the extension README, Store-format changelog and clean screenshots showing synthetic files.
- Validate the distribution build and test model setup, indexing, cancellation, previews and search in both launchers.
- Keep the published version history in `CHANGELOG.md`. Distribute only the reviewed current installers and their corresponding native source archive; historical installers and development objects are not part of this repository.

The full [Store preparation guidelines](https://developers.raycast.com/basics/prepare-an-extension-for-store) determine acceptance. Store submission is a separate step from the ready-to-use ZIP release.

## One project, two distribution packages

The intended Store design keeps one engine and one extension codebase. The existing offline ZIP contains both. A Store package would contain the small launcher interface and connect to the same separately installed local engine.

Both packages should use the same pinned engine release, model revisions, indexing pipeline, shared storage and local protocol. Packaging alone does not change retrieval quality, model memory use or search speed. Installation and update steps differ; startup and search still need verification in both hosts.

The Store package and a suitable engine installer are planned, not yet published. A simple download of an executable from a maintainer-controlled server is not automatically acceptable under Raycast's rules. The engine installation method must satisfy the Store review while keeping setup free of terminal commands.

## Proposed installation experience

The practical first Store release should use a companion-engine installer. Published integrations already use this approach: the [LM Studio extension](https://github.com/raycast/extensions/blob/main/extensions/lm-studio/README.md) connects to an independently installed local application. This is a precedent for the design, not approval of Semantic Search.

1. Install **Semantic Search** from the launcher Store.
2. Open Search. If the engine is missing, show one setup screen explaining that processing is local, the supported Mac requirements, and the required disk space.
3. Offer **Download Search Engine**, opening the current installer release in the browser. The user explicitly opens a normal macOS installer. It contains Python and all required libraries; there are no Homebrew, npm, pip or terminal steps.
4. The installer verifies and installs the same pinned runtime used by the ZIP. Returning to the extension detects the installed engine and offers model download and folder selection.
5. An existing compatible engine skips setup entirely. Installing the extension in the other launcher reuses the same model, settings and indexes.

The Store extension must not silently download and execute our own runtime archive. The installer is an explicit companion installation. Its executable code should use Developer ID signing, hardened runtime and [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution). Source archives, dependency notices and reproducible native build recipes remain available with each engine release. Signing and notarization require a maintainer's Apple Developer credentials; they are not prerequisites for end users.

The current ZIP already includes portable Python, MLX, Core ML support and the decoders. Initial model weights are a separate, explicit download. A Store installation still needs that same runtime and model; changing packaging does not remove these requirements or move inference to a cloud service.

## Shared implementation

Keep the engine, local protocol and launcher UI in this repository. Generate two packages from one release:

| Package | Contents | Engine setup |
| --- | --- | --- |
| Ready-to-use ZIP | Built launcher commands, runtime manifest and complete runtime payload | Existing offline automatic setup |
| Store extension | The same commands, small assets and expected engine/protocol metadata | Detect a companion installation and guide setup when missing |

Separate installation policy from service startup in `extension/src/runtime.ts`. Bundled mode may verify and unpack the included archive. Store mode may discover and start an explicitly installed companion, but must not contain the large archive or run a fallback executable download. Both modes check application identity, engine version and protocol before making requests.

Use the same atomic installation checks and single-service lock. Updates must finish or safely cancel active work, stop the old service, and select the new runtime without deleting models or indexes. A failed update must preserve the previous working installation. The current restart-required update message should be replaced by this flow before Store submission.

Static launcher preferences should use Raycast's preferences API. Model downloads, index status, cancellation and folder management need a library-management screen with live state; discuss its fit with the Store's restriction on separate configuration commands during review. Do not copy the current settings command into a Store submission without addressing that guideline.

## Work required before submission

- Build and validate the companion installer, including signed nested executables and libraries, notarization and a clear uninstall path. Preserve user models and indexes unless explicitly requested otherwise.
- Add guided missing-engine, incompatible-version and failed-installation states; share the existing search and library-management components across both package builds.
- Use the actual Raycast account username and supported categories, add the official lint/publication scripts, and produce a normal Store distribution build with no runtime payload.
- Create screenshots with synthetic documents and photos. Include model/runtime download sizes, local-processing details and companion requirements in the Store README.
- Exercise both packages on a clean Mac/account: installation without a developer toolchain, model download, indexing, exclusions, cancellation, previews, search, unloading and updates. Test both Tinycast and Raycast, and compare the same engine version for startup time and memory.
- Submit the extension for Raycast review only after the distribution design and tests are ready. Store acceptance remains a reviewer decision.

## Possible future native Tinycast integration

Tinycast already runs dictation in an on-demand helper, downloads pinned Core ML weights from settings and terminates the helper after idle time. Its [dictation architecture](https://github.com/abue-ammar/tinycast/blob/main/docs/features/dictation.md) is a useful precedent for semantic search.

A first integration could provide native search and library controls while using the same engine and protocol. A later Swift/Core ML implementation could remove Python from that particular backend, but would require matching tokenization, preprocessing, model revisions and index identities. Treat it as a separate measured implementation change, not a packaging shortcut. Native UI alone does not eliminate model initialization time or guarantee faster retrieval.

Tinycast's [contribution guidelines](https://github.com/abue-ammar/tinycast/blob/main/CONTRIBUTING.md) require an approved issue before a feature PR, strict main-app memory discipline and measured leak-free teardown. Agree on the feature and helper-process memory budget with the maintainer before implementing an upstream contribution. No native integration or external proposal has been submitted.
