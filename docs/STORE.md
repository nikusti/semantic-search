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
