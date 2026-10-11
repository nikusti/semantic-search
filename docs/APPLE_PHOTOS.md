# Apple Photos (development)

This importer is in development and is not part of release 0.5.1 or the submitted Store package.

In Model Settings, enable **Search photos and videos in the Photos library**, choose **Actions → Connect Apple Photos**, and allow the macOS permission request. Apply the settings and choose **Update Search Index**. The existing content switches apply to both folders and Photos: **Photographs and images**, **Video scenes**, and **Audio and video soundtracks**. You can index video without photographs, or only video soundtracks. Folder and Photos results use the same selected model's index; changing the model still selects its own vector space.

Cloud downloads are off by default. Enable **Allow iCloud downloads** to include media unavailable locally. PhotoKit requests a 1024-pixel photo representation, which is resized again if needed. Videos may download in full. Apple controls the underlying download and may fetch larger data into its own Photos cache; a small requested image does not guarantee a thumbnail-sized network transfer. The importer never changes or deletes library assets, and never sends media to a model server.

Each photo retains one vector and a small list thumbnail in the index. The temporary model input is deleted after processing. Larger previews are obtained on selection and share the bounded 32 MB preview cache. With no network or revoked permission, an already indexed thumbnail can still be shown. **Open in Photos** opens the original asset in Apple's app.

Videos use the same scene and soundtrack pipeline as ordinary video files, with both MLX and Core ML. PhotoKit supplies the current asset; AVFoundation prepares edited compositions when necessary. Local unedited videos are decoded through a temporary link rather than copied. Edited videos use a temporary 720p representation. Temporary links and exports are removed after each asset, including on cancellation; original videos are never deleted. Duration and file-size limits apply, and long clips are skipped rather than truncated.

Each video scene and audio window retains one vector. With 512-dimensional float32 vectors, 10-second scenes use about 0.74 MB/hour; 10-second audio windows with a 2-second overlap use about 0.92 MB/hour. These are vector bytes only; thumbnails, metadata and bounded preview caches add storage. Photos library status reports indexed photo/video counts and actual vector bytes separately. Video previews use the matched scene's timestamp; **Open Matched Segment** prepares a short local clip in the shared 64 MB segment cache. **Open in Photos** opens the full original video.

Increasing the list-thumbnail size refreshes smaller Photos thumbnails on the next indexing pass, reusing their existing vectors. Slow cloud previews do not block text queries or queue more downloads while browsing results.

Repeat **Update Search Index** to add new assets, retry unavailable cloud assets, and update edited ones. A complete successful inventory removes index entries no longer available in the authorized library. Cancellation or a failed inventory preserves completed entries. Turning the Photos source or an individual content type off hides its results and preserves its saved entries. Folder exclusions apply to filesystem folders; they do not exclude Photos albums. Text queries search saved photo, video and sound vectors without loading their media encoders or reading the Photos library.

The importer stops when less than 512 MB of disk space remains. Only one temporary representation is held at a time. Exports are cancelled if they exceed the configured media size limit or available space falls below this threshold. The macOS Photos cache has its own storage policy and cannot be capped by this importer. Live Photos are indexed as photographs; their paired motion resource is not separately indexed as a video.

The helper uses its own app identity, `app.semanticsearch.photos`, for library permission. Development and unsigned releases are ad-hoc signed; rebuilding/updating them may require granting permission again. A stable Developer ID signature can improve permission continuity in future releases.
