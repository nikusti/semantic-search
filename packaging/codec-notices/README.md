# Native runtime licenses and corresponding source

The application is MIT licensed. Bundled dependencies retain their upstream licenses.

## FFmpeg and PyAV

PyAV 19.0.1 is rebuilt from source against unmodified FFmpeg 9.0.2, configured without GPL, nonfree or version-3 components. OpenCV and Pillow-HEIF are omitted. H.264 preview encoding uses Apple's system VideoToolbox, rather than x264 or x265. The build is defined in `scripts/build_lgpl_codecs.py`; FFmpeg is LGPL-2.1-or-later. License text is included here.

## SoundFile

SoundFile 0.14.0 ships libsndfile 1.2.2, mpg123 1.32.3 and LAME 3.100 (LGPL), plus BSD-licensed libogg 1.3.5, libvorbis 1.3.7, FLAC 1.4.3 and Opus 1.4. The native binary exactly matches libsndfile-binaries commit `a3e6f9769d0c7e91d2d036cf0fdbe5b4bbf18b87`.


The packaged MLX/Core ML runtime omits the legacy sentence-transformers, SciPy and scikit-learn backend; no GCC runtime libraries are bundled. Torch remains only for the upstream image/video data preparation.

## Obtaining and replacing source

Every release includes `semantic-search-native-sources-vVERSION.zip` beside the binary downloads. It contains complete source archives, pinned build recipes/patches, license texts and a SHA-256 manifest. `packaging/native-sources.json` records exact URLs and checksums; `scripts/package_native_sources.py` creates the source asset.

Users may modify and replace LGPL shared libraries in the installed runtime. The extension does not forbid reverse engineering for debugging modifications. Replacement libraries must preserve the ABI; macOS may require local ad-hoc signing after modification. Bundled package metadata retains dependency copyright/license notices. Model weights are downloaded separately under their model-card licenses.
