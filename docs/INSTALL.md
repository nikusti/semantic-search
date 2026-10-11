# Install Semantic Search

You need an Apple silicon Mac running macOS 26 or newer. No terminal, Homebrew, Python or Node.js installation is needed for the ready-to-use package.

## Tinycast

1. Download `semantic-search-v0.5.0.zip` from the GitHub release and extract it.
2. In Tinycast, open **Settings → Extensions → Install → Add from folder**.
3. Select the extracted **Semantic Search** folder, containing `package.json`.
4. Open **Semantic Search Model Settings**. The first launch prepares the bundled engine automatically.
5. Use **Folders to Search** to choose one or more folders.
6. Select the content types you want, then **Download & Apply**. Wait for the model download and indexing.
7. Open **Semantic Search** and describe a file. Use **Update Search Index** (⌘R) after adding, editing or removing files.

Leave **Custom Local Service** blank. The extension starts its own engine when needed.

## Raycast

1. Download and extract the same `semantic-search-v0.5.0.zip`.
2. Move the extracted **Semantic Search** folder to a permanent location.
3. Run **Import Extension** in Raycast and choose that folder.
4. Raycast may show generic developer instructions to run npm. This package is already built, so skip those instructions.
5. Open **Semantic Search Model Settings**, choose **Folders to Search**, and press **Download & Apply**.
6. Open **Semantic Search** to search.

The folder-import workflow was tested in Raycast v2 without installing Node.js or running npm. Other Raycast versions may behave differently. The `.rayext` asset is an official build artifact, but direct archive installation is currently restricted to internal Raycast builds. Use the extracted ZIP folder for the supported local installation path.

Both launchers share the engine, model and index storage.

## The first launch

Preparing the engine is a one-time local operation. The package already includes Python, MLX, media decoders and the PDF renderer. There is no additional dependency download. The model is downloaded separately when you request it in Model Settings.

Allow the launcher to access the folders you select if macOS asks. Keep enough free space for the extension archive, the installed engine and the model. Once installation and model download finish, indexing and search work locally.

## Updating

Install the newer extension package using the same launcher workflow. Your models and indexes stay in the shared Semantic Search folder. Version 0.5.0 and newer stop an idle packaged engine safely before updating and restore it if the replacement fails. Cancel indexing or wait for it to finish before updating. Engines older than 0.5.0 require a one-time restart of your Mac; your models and indexes are preserved.

## Optional companion installer

The release also includes `semantic-search-engine-v0.5.0-unsigned.zip`. Extract it, open **Semantic Search Engine Installer**, and click **Install**. It installs the same engine as the ready-to-use ZIP, with Python and libraries included. Models still download from the extension. The companion installer is optional for the bundled ZIP and prepares the small Store package; no Store listing is available yet.

This installer has no Apple Developer ID signature or notarization. macOS may block the downloaded app until you explicitly approve opening it. See [Apple's opening-app guidance](https://support.apple.com/en-us/102445). Signing is not required to submit the extension for Raycast review; acceptance of the companion design is decided during that review. Use the bundled extension ZIP if you prefer its existing installation workflow.
