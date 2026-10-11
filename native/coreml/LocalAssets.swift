import Foundation

// Asset download is owned by the Python service, pinned and checksum verified.
public enum EmbeddingGemma2ModelStore {
    public typealias Progress = @Sendable (_ file: String, _ bytes: Int64) -> Void
    public static func ensure(progress: Progress? = nil) async throws -> URL {
        throw EmbeddingGemma2Error.invalidAsset("An explicit local model directory is required")
    }
}
