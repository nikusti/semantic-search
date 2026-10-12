import Foundation

enum VideoCopy {
    enum Failure: LocalizedError {
        case cancelled, storageLimit, incomplete
        var errorDescription: String? {
            switch self {
            case .cancelled: return "Video preparation cancelled or timed out."
            case .storageLimit: return "Video preparation reached the storage limit."
            case .incomplete: return "The source video changed during preparation."
            }
        }
    }

    // Preserve original encoded bytes without another lossy encode. Memory is
    // bounded to one 4 MB block; the importer deletes this temporary file.
    static func copy(from source: URL, to destination: URL, maxBytes: Int64,
                     deadline: Date, session: URL) async throws {
        let fm = FileManager.default
        let expected = (try fm.attributesOfItem(atPath: source.path)[.size] as? NSNumber)?.int64Value ?? 0
        guard expected > 0 && expected <= maxBytes else { throw Failure.storageLimit }
        guard fm.createFile(atPath: destination.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
            throw Failure.storageLimit
        }
        var complete = false
        defer { if !complete { try? fm.removeItem(at: destination) } }
        let input = try FileHandle(forReadingFrom: source)
        defer { try? input.close() }
        let output = try FileHandle(forWritingTo: destination)
        defer { try? output.close() }
        var written: Int64 = 0
        while true {
            guard !Task.isCancelled && Date() < deadline &&
                    !fm.fileExists(atPath: session.appendingPathComponent("cancel").path) &&
                    !fm.fileExists(atPath: session.appendingPathComponent("stop").path) else { throw Failure.cancelled }
            let free = (try fm.attributesOfFileSystem(forPath: session.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
            guard free >= 512 * 1024 * 1024 else { throw Failure.storageLimit }
            guard let data = try input.read(upToCount: 4 * 1024 * 1024), !data.isEmpty else { break }
            written += Int64(data.count)
            guard written <= maxBytes else { throw Failure.storageLimit }
            try output.write(contentsOf: data)
            await Task.yield()
        }
        guard written == expected else { throw Failure.incomplete }
        complete = true
    }
}
