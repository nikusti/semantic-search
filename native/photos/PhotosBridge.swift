import AppKit
import Photos
import ImageIO
import UniformTypeIdentifiers
import AVFoundation

// A separate app gives PhotoKit a stable permission identity. No library mutations.
final class PhotosBridge: NSObject, NSApplicationDelegate {
    let folder: URL
    let manager = PHImageManager()
    var busy = false
    var timer: Timer?
    var retainedVideo: AVAsset?
    init(folder: URL) { self.folder = folder }

    func write(_ value: [String: Any], to name: String = "response.json") {
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            try? data.write(to: folder.appendingPathComponent(name), options: .atomic)
        }
    }
    func authorization() -> String {
        switch PHPhotoLibrary.authorizationStatus(for: .readWrite) {
        case .authorized: return "authorized"
        case .limited: return "limited"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not_determined"
        @unknown default: return "unknown"
        }
    }
    func finish(_ response: [String: Any]) { write(response); busy = false }
    func video(_ asset: PHAsset, _ value: [String: Any]) {
        let options = PHVideoRequestOptions()
        options.isNetworkAccessAllowed = value["network"] as? Bool ?? false
        options.deliveryMode = .highQualityFormat
        options.version = .current
        options.progressHandler = { progress, _, _, _ in
            self.write(["download_fraction": progress], to: "progress.json")
        }
        let output = folder.appendingPathComponent("video.mov")
        let maxBytes = Int64(value["max_bytes"] as? Int ?? 1024 * 1024 * 1024)
        let started = Date()
        var completed = false
        var watchdog: Timer?
        var exportTask: Task<Void, Never>?
        var generator: AVAssetImageGenerator?
        func done(_ response: [String: Any]) {
            guard !completed else { return }
            completed = true; watchdog?.invalidate()
            if response["error"] != nil { try? FileManager.default.removeItem(at: output) }
            self.finish(response)
        }
        func metadata() -> [String: Any] {
            let latest = PHAsset.fetchAssets(withLocalIdentifiers: [asset.localIdentifier], options: nil).firstObject
            let resource = PHAssetResource.assetResources(for: asset).first { [.video, .fullSizeVideo].contains($0.type) }
            var name = "Video"
            if #available(macOS 27, *) { name = resource?.filename ?? name }
            else { name = resource?.originalFilename ?? name }
            return ["name": name, "modified": (latest?.modificationDate ?? latest?.creationDate)?.timeIntervalSince1970 ?? 0]
        }
        let requestID = manager.requestAVAsset(forVideo: asset, options: options) { avAsset, audioMix, info in
            DispatchQueue.main.async {
                guard !completed else { return }
                guard let avAsset = avAsset else {
                    if info?[PHImageResultIsInCloudKey] as? Bool == true && !options.isNetworkAccessAllowed {
                        done(["deferred": true]); return
                    }
                    done(["error": "Could not obtain this video from Photos or iCloud."]); return
                }
                self.retainedVideo = avAsset
                if value["op"] as? String == "video_frame" {
                    let imageGenerator = AVAssetImageGenerator(asset: avAsset)
                    generator = imageGenerator
                    imageGenerator.appliesPreferredTrackTransform = true
                    let edge = min(2048, max(64, value["edge"] as? Int ?? 1024))
                    imageGenerator.maximumSize = CGSize(width: edge, height: edge)
                    let seconds = max(0, min(asset.duration - 0.05, value["seconds"] as? Double ?? 0))
                    imageGenerator.generateCGImagesAsynchronously(forTimes: [NSValue(time: CMTime(seconds: seconds, preferredTimescale: 600))]) { _, image, _, result, _ in
                        DispatchQueue.main.async {
                            guard !completed else { return }
                            guard result == .succeeded, let image = image,
                                  let target = CGImageDestinationCreateWithURL(self.folder.appendingPathComponent("image.jpg") as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else {
                                done(["error": "Could not prepare the video preview."]); return
                            }
                            CGImageDestinationAddImage(target, image, [kCGImageDestinationLossyCompressionQuality: 0.82] as CFDictionary)
                            guard CGImageDestinationFinalize(target) else {
                                done(["error": "Could not save the video preview."]); return
                            }
                            done(metadata().merging(["image": "image.jpg"], uniquingKeysWith: { _, new in new }))
                        }
                    }
                    return
                }
                // Unedited assets can be decoded in place. The temporary symlink
                // is removed afterwards; the original and Apple's cache are untouched.
                if let urlAsset = avAsset as? AVURLAsset, urlAsset.url.isFileURL, audioMix == nil {
                    do {
                        let bytes = (try FileManager.default.attributesOfItem(atPath: urlAsset.url.path)[.size] as? NSNumber)?.int64Value ?? 0
                        guard bytes > 0 && bytes <= maxBytes else {
                            done(["error": "Video exceeds the configured file size limit."]); return
                        }
                        try FileManager.default.createSymbolicLink(at: output, withDestinationURL: urlAsset.url)
                        done(metadata().merging(["video": "video.mov"], uniquingKeysWith: { _, new in new }))
                    } catch { done(["error": "Could not prepare the local video."]) }
                    return
                }
                // Edited clips may be compositions rather than files. Export the
                // current edit, preserving audio, to a single temporary 720p clip.
                guard let session = AVAssetExportSession(asset: avAsset, presetName: AVAssetExportPreset1280x720) else {
                    done(["error": "Could not prepare the edited video."]); return
                }
                session.audioMix = audioMix
                exportTask = Task { @MainActor in
                    do {
                        try await session.export(to: output, as: .mov)
                        guard !completed else {
                            try? FileManager.default.removeItem(at: output); return
                        }
                        done(metadata().merging(["video": "video.mov"], uniquingKeysWith: { _, new in new }))
                    } catch { done(["error": "Could not export the current video edit."]) }
                }
            }
        }
        watchdog = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { timer in
            if completed { timer.invalidate(); return }
            let cancelled = FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("cancel").path)
            let bytes = (try? FileManager.default.attributesOfItem(atPath: output.path)[.size] as? NSNumber)?.int64Value ?? 0
            let free = (try? FileManager.default.attributesOfFileSystem(forPath: self.folder.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? Int64.max
            let expired = Date().timeIntervalSince(started) > 300
            if cancelled || expired || bytes > maxBytes || free < 512 * 1024 * 1024 {
                self.manager.cancelImageRequest(requestID); exportTask?.cancel(); generator?.cancelAllCGImageGeneration()
                done(["error": cancelled ? "Video request cancelled." : expired ? "iCloud video request timed out; retry on the next pass." : "Video preparation reached the storage limit."])
            }
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        write(["ready": true], to: "ready.json")
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in self?.poll() }
    }
    func poll() {
        if !FileManager.default.fileExists(atPath: folder.path) || FileManager.default.fileExists(atPath: folder.appendingPathComponent("stop").path) {
            NSApp.terminate(nil); return
        }
        guard !busy else { return }
        let request = folder.appendingPathComponent("request.json")
        guard let data = try? Data(contentsOf: request),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        busy = true
        retainedVideo = nil
        try? FileManager.default.removeItem(at: request)
        let op = value["op"] as? String ?? ""
        if op == "status" { finish(["authorization": authorization()]); return }
        if op == "authorize" {
            PHPhotoLibrary.requestAuthorization(for: .readWrite) { _ in
                DispatchQueue.main.async { self.finish(["authorization": self.authorization()]) }
            }
            return
        }
        guard ["authorized", "limited"].contains(authorization()) else {
            finish(["error": "Allow access to Photos in macOS Privacy & Security settings."]); return
        }
        if op == "list" || op == "count" {
            let options = PHFetchOptions()
            options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]
            if op == "count" {
                finish(["photos": PHAsset.fetchAssets(with: .image, options: nil).count,
                        "videos": PHAsset.fetchAssets(with: .video, options: nil).count]); return
            }
            options.predicate = NSPredicate(format: "mediaType IN %@", [PHAssetMediaType.image.rawValue, PHAssetMediaType.video.rawValue])
            let assets = PHAsset.fetchAssets(with: options)
            var rows: [[String: Any]] = []
            let formatter = ISO8601DateFormatter()
            assets.enumerateObjects { asset, _, stop in
                if FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("cancel").path) {
                    stop.pointee = true
                }
                let type = asset.mediaType == .video ? "Video" : "Photo"
                let name = asset.creationDate.map { type + " · " + formatter.string(from: $0) } ?? type
                var row: [String: Any] = ["id": asset.localIdentifier, "name": name,
                             "modified": (asset.modificationDate ?? asset.creationDate)?.timeIntervalSince1970 ?? 0,
                             "width": asset.pixelWidth, "height": asset.pixelHeight]
                if asset.mediaType == .video { row["kind"] = "video"; row["duration"] = asset.duration }
                rows.append(row)
            }
            if FileManager.default.fileExists(atPath: folder.appendingPathComponent("cancel").path) {
                finish(["error": "Photos inventory cancelled."]); return
            }
            finish(["assets": rows, "authorization": authorization()]); return
        }
        guard ["image", "video", "video_frame"].contains(op), let id = value["id"] as? String,
              let asset = PHAsset.fetchAssets(withLocalIdentifiers: [id], options: nil).firstObject else {
            finish(["error": "Photo is no longer available in the authorized library."]); return
        }
        if op != "image" {
            guard asset.mediaType == .video else { finish(["error": "This asset is not a video."]); return }
            video(asset, value); return
        }
        let options = PHImageRequestOptions()
        options.isNetworkAccessAllowed = value["network"] as? Bool ?? false
        options.deliveryMode = .highQualityFormat
        options.resizeMode = .exact
        options.version = .current
        options.progressHandler = { progress, _, _, _ in
            self.write(["download_fraction": progress], to: "progress.json")
        }
        let edge = min(2048, max(64, value["edge"] as? Int ?? 1024))
        var completed = false
        var watchdog: Timer?
        var requestID: PHImageRequestID = PHInvalidImageRequestID
        let started = Date()
        requestID = manager.requestImage(for: asset, targetSize: CGSize(width: edge, height: edge),
                                        contentMode: .aspectFit, options: options) { image, info in
            DispatchQueue.main.async {
                guard !completed else { return }
                if info?[PHImageResultIsDegradedKey] as? Bool == true { return }
                completed = true; watchdog?.invalidate()
                if info?[PHImageCancelledKey] as? Bool == true {
                    self.finish(["error": "Photo request cancelled."]); return
                }
                guard let image = image else {
                    if info?[PHImageResultIsInCloudKey] as? Bool == true && !options.isNetworkAccessAllowed {
                        self.finish(["deferred": true]); return
                    }
                    self.finish(["error": "Could not obtain this photo from Photos or iCloud."]); return
                }
                var rect = CGRect(origin: .zero, size: image.size)
                guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
                    self.finish(["error": "Could not decode the photo."]); return
                }
                // PhotoKit may return an original larger than requested; bound it again.
                let scale = min(1, Double(edge) / Double(max(cg.width, cg.height)))
                let width = max(1, Int(Double(cg.width) * scale)), height = max(1, Int(Double(cg.height) * scale))
                guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                              bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
                    self.finish(["error": "Could not resize the photo."]); return
                }
                context.interpolationQuality = .high
                context.draw(cg, in: CGRect(x: 0, y: 0, width: width, height: height))
                let output = self.folder.appendingPathComponent("image.jpg")
                guard let resized = context.makeImage(),
                      let target = CGImageDestinationCreateWithURL(output as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else {
                    self.finish(["error": "Could not prepare the photo."]); return
                }
                CGImageDestinationAddImage(target, resized, [kCGImageDestinationLossyCompressionQuality: 0.82] as CFDictionary)
                guard CGImageDestinationFinalize(target) else {
                    self.finish(["error": "Could not save the temporary photo."]); return
                }
                let resource = PHAssetResource.assetResources(for: asset).first
                var name = "Photo"
                if #available(macOS 27, *) { name = resource?.filename ?? name }
                else { name = resource?.originalFilename ?? name }
                let latest = PHAsset.fetchAssets(withLocalIdentifiers: [id], options: nil).firstObject
                self.finish(["image": "image.jpg", "width": width, "height": height, "name": name,
                             "modified": (latest?.modificationDate ?? latest?.creationDate)?.timeIntervalSince1970 ?? 0])
            }
        }
        watchdog = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { timer in
            if completed { timer.invalidate(); return }
            let cancelled = FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("cancel").path)
            if cancelled || Date().timeIntervalSince(started) > 120 {
                completed = true; timer.invalidate(); self.manager.cancelImageRequest(requestID)
                self.finish(["error": cancelled ? "Photo request cancelled." : "iCloud photo request timed out; retry on the next indexing pass."])
            }
        }
    }
}

guard CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--session" else { exit(2) }
let folder = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
let app = NSApplication.shared
let delegate = PhotosBridge(folder: folder)
app.delegate = delegate
app.run()
