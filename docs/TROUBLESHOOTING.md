# Troubleshooting

## First launch is preparing the engine

The first launch extracts and verifies the bundled engine once. Keep the command open while setup completes. Model downloading is a separate operation in Model Settings. Subsequent command launches reuse the installed engine.

## The engine cannot start or fails its integrity check

Download the ready-to-use release archive again, extract it and reinstall the extension. Do not use GitHub's source ZIP as the end-user installation package. Check free disk space and confirm Apple silicon with macOS 26 or newer.

In Model Settings, **Actions → Open Shared Storage Folder** opens local storage. The service diagnostic log is in `logs/service.log`. These logs can contain local paths; remove personal information before sharing them.

## An older engine is running

After installing a newer extension, restart the Mac and open Semantic Search again. The new engine starts automatically. This does not remove your models or indexes.

## No files are searchable

Open Model Settings, choose **Folders to Search**, choose content types, and select **Download & Apply**. Then watch the indexing counters. Use **Update Search Index** to scan newly added files. Apply any changed settings first.

## A folder is inaccessible

Allow the launcher to access the selected folder when macOS asks. Check **System Settings → Privacy & Security → Files and Folders** for the launcher you use. Choose a small readable folder for the first scan.

## A photo or PDF has no preview

The original file must still exist and be readable. This release includes its media and PDF rendering libraries; no FFmpeg or Poppler installation is needed for the packaged engine. A scanned PDF can have a visual preview but no searchable extracted text because OCR is not included.

## The model uses memory after leaving search

The model worker exits after the configured idle timeout, normally one minute. Indexing and active requests delay unloading. The lightweight local parent service remains running. The audio encoder is used only while indexing sound.

## The model was deleted

Download the same model in Model Settings to reuse its saved index. Deleting model weights leaves indexes and original files untouched.

## Custom Local Service

Ordinary users should leave this preference blank. A value such as `http://127.0.0.1:8765` connects to a developer-managed service and bypasses automatic engine startup. Only local HTTP addresses are supported.

For source installation, developer CLI commands and packaging instructions, see [Development](DEVELOPMENT.md).
