import AppKit
import Photos
import ImageIO
import UniformTypeIdentifiers

// A separate app gives PhotoKit a stable permission identity. No library mutations.
final class PhotosBridge: NSObject, NSApplicationDelegate {
    let folder: URL
    let manager = PHImageManager()
    var busy = false
    var timer: Timer?
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
            let assets = PHAsset.fetchAssets(with: .image, options: options)
            if op == "count" { finish(["photos": assets.count]); return }
            var rows: [[String: Any]] = []
            let formatter = ISO8601DateFormatter()
            assets.enumerateObjects { asset, _, stop in
                if FileManager.default.fileExists(atPath: self.folder.appendingPathComponent("cancel").path) {
                    stop.pointee = true
                }
                let name = asset.creationDate.map { "Photo · " + formatter.string(from: $0) } ?? "Photo"
                rows.append(["id": asset.localIdentifier, "name": name,
                             "modified": (asset.modificationDate ?? asset.creationDate)?.timeIntervalSince1970 ?? 0,
                             "width": asset.pixelWidth, "height": asset.pixelHeight])
            }
            if FileManager.default.fileExists(atPath: folder.appendingPathComponent("cancel").path) {
                finish(["error": "Photos inventory cancelled."]); return
            }
            finish(["assets": rows, "authorization": authorization()]); return
        }
        guard op == "image", let id = value["id"] as? String,
              let asset = PHAsset.fetchAssets(withLocalIdentifiers: [id], options: nil).firstObject else {
            finish(["error": "Photo is no longer available in the authorized library."]); return
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
