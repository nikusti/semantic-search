import AppKit

enum PhotoClipboard {
    enum Failure: LocalizedError {
        case tooLarge, encoding, writing
        var errorDescription: String? {
            switch self {
            case .tooLarge: return "This photo is too large for clipboard export. Open it in Photos."
            case .encoding: return "Could not prepare the clipboard image."
            case .writing: return "Could not write the image to the clipboard."
            }
        }
    }

    static func write(_ image: CGImage, to pasteboard: NSPasteboard = .general) throws {
        guard image.height > 0 && image.width <= 64_000_000 / image.height else { throw Failure.tooLarge }
        guard let data = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
            throw Failure.encoding
        }
        guard data.count <= 128 * 1024 * 1024 else { throw Failure.tooLarge }
        // Encode first so failed downloads/decoding leave the previous clipboard intact.
        pasteboard.clearContents()
        guard pasteboard.setData(data, forType: .png) else { throw Failure.writing }
    }
}
