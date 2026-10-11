# Apple Photos (development)

This importer is in development and is not part of release 0.5.1 or the submitted Store package.

In Model Settings, enable **Search the Photos library**, choose **Actions → Connect Apple Photos**, and allow the macOS permission request. Apply the settings and choose **Update Search Index**. **Photographs and images** must also be enabled. Folder and Photos results use the same selected model's index; changing the model still selects its own vector space.

Cloud downloads are off by default. Enable **Allow iCloud downloads** to include pictures unavailable locally. PhotoKit requests a 1024-pixel representation, which is resized again if needed. Apple controls the underlying download and may fetch larger data into its own Photos cache; a small requested image does not guarantee a thumbnail-sized network transfer. The importer never changes or deletes library assets, and never sends media to a model server.

Each photo retains one vector and a small list thumbnail in the index. The temporary model input is deleted after processing. Larger previews are obtained on selection and share the bounded 32 MB preview cache. With no network or revoked permission, an already indexed thumbnail can still be shown. **Open in Photos** opens the original asset in Apple's app.

Increasing the list-thumbnail size refreshes smaller Photos thumbnails on the next indexing pass, reusing their existing vectors. Slow cloud previews do not block text queries or queue more downloads while browsing results.

Repeat **Update Search Index** to add new pictures, retry unavailable cloud pictures, and update edited ones. A complete successful inventory removes index entries for photos no longer available in the authorized library. Cancellation or a failed inventory preserves completed entries. Turning the Photos source off hides its results and preserves its index for later use. Folder exclusions apply to filesystem folders; they do not exclude Photos albums.

The importer stops when less than 512 MB of disk space remains. Only one temporary representation is held at a time. The macOS Photos cache has its own storage policy and cannot be capped by this importer.

The helper uses its own app identity, `app.semanticsearch.photos`, for library permission. Development and unsigned releases are ad-hoc signed; rebuilding/updating them may require granting permission again. A stable Developer ID signature can improve permission continuity in future releases.
