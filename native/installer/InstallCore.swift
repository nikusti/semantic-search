import Foundation
import CryptoKit

struct RuntimePayload: Codable {
    let version: String
    let sha256: String
    let platform: String
    let unpacked_bytes: Int64

    func validate() throws {
        guard version.range(of: #"^\d+\.\d+\.\d+$"#, options: .regularExpression) != nil,
              sha256.range(of: #"^[a-f0-9]{64}$"#, options: .regularExpression) != nil,
              platform == "darwin-arm64", unpacked_bytes > 0 else {
            throw InstallError.message("The installer payload is invalid. Download it again.")
        }
    }
}
enum InstallError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

struct EngineInstaller {
    let resources: URL
    let storage: URL
    let progress: @Sendable (String) -> Void
    private let fm = FileManager.default

    static var sharedStorage: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Semantic Search", isDirectory: true)
    }

    func run(_ executable: URL, _ arguments: [String], timeout: Double) throws {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        // Diagnostic output stays local, and the user sees a concise error.
        let logs = storage.appendingPathComponent("logs", isDirectory: true)
        try fm.createDirectory(at: logs, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let log = logs.appendingPathComponent("installer.log")
        if !fm.fileExists(atPath: log.path) { fm.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        let handle = try FileHandle(forWritingTo: log)
        defer { try? handle.close() }
        try handle.seekToEnd()
        process.standardError = handle
        process.environment = ProcessInfo.processInfo.environment.merging([
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false"
        ]) { _, new in new }
        try process.run()
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
        if process.isRunning {
            process.terminate()
            Thread.sleep(forTimeInterval: 1)
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
            throw InstallError.message("Engine setup timed out. Try again; your models and indexes are preserved.")
        }
        guard process.terminationStatus == 0 else {
            throw InstallError.message("Engine setup failed. See installer.log in the shared storage folder.")
        }
    }

    func digest(_ file: URL) throws -> String {
        let input = try FileHandle(forReadingFrom: file)
        defer { try? input.close() }
        var hash = SHA256()
        while let data = try input.read(upToCount: 1024 * 1024), !data.isEmpty { hash.update(data: data) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    func install() throws -> String {
        #if !arch(arm64)
        throw InstallError.message("Semantic Search requires an Apple silicon Mac.")
        #endif
        guard ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26 else {
            throw InstallError.message("Semantic Search requires macOS 26 or newer.")
        }
        let manifestData = try Data(contentsOf: resources.appendingPathComponent("runtime-manifest.json"))
        let payload = try JSONDecoder().decode(RuntimePayload.self, from: manifestData)
        try payload.validate()
        let parent = storage.appendingPathComponent("runtime", isDirectory: true)
        try fm.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let destination = parent.appendingPathComponent(payload.version, isDirectory: true)
        let lock = parent.appendingPathComponent("install.lock", isDirectory: true)
        // Same lock and stale interval as the bundled launcher installer.
        let deadline = Date().addingTimeInterval(180)
        while true {
            do { try fm.createDirectory(at: lock, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]); break }
            catch {
                guard fm.fileExists(atPath: lock.path) else { throw error }
                if let attributes = try? fm.attributesOfItem(atPath: lock.path),
                   let modified = attributes[.modificationDate] as? Date,
                   Date().timeIntervalSince(modified) > 1200 {
                    try fm.removeItem(at: lock); continue
                }
                guard Date() < deadline else { throw InstallError.message("Another installer is still running. Try again shortly.") }
                progress("Waiting for another installer…")
                Thread.sleep(forTimeInterval: 0.5)
            }
        }
        defer { try? fm.removeItem(at: lock) }
        let python = destination.appendingPathComponent("python/bin/python3.12")
        if let saved = try? Data(contentsOf: destination.appendingPathComponent("installed.json")),
           let current = try? JSONDecoder().decode(RuntimePayload.self, from: saved),
           current.version == payload.version && current.sha256 == payload.sha256 && current.platform == payload.platform,
           fm.isExecutableFile(atPath: python.path) {
            progress("Engine is already installed.")
            try run(python, ["-I", "-B", "-m", "tinysearch.coreml_assets", "--storage", storage.path], timeout: 660)
            return payload.version
        }
        // Never overwrite a possibly running or modified installation in place.
        guard !fm.fileExists(atPath: destination.path) else {
            throw InstallError.message("An incomplete installation already exists for this version. Close the engine and remove only that runtime version folder before retrying. Models and indexes are separate.")
        }
        let free = (try fm.attributesOfFileSystem(forPath: parent.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
        guard free > payload.unpacked_bytes + 512 * 1024 * 1024 else {
            throw InstallError.message("Not enough free space. Allow at least 2.5 GB for the engine, plus your model and index.")
        }
        progress("Verifying the bundled search engine…")
        let archive = resources.appendingPathComponent("runtime.zip")
        guard try digest(archive) == payload.sha256 else {
            throw InstallError.message("The engine archive failed its integrity check. Download the installer again.")
        }
        let staging = parent.appendingPathComponent(".install-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: staging, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? fm.removeItem(at: staging) }
        progress("Installing Python and the search libraries…")
        try run(URL(fileURLWithPath: "/usr/bin/ditto"), ["-x", "-k", archive.path, staging.path], timeout: 180)
        let stagedPython = staging.appendingPathComponent("python/bin/python3.12")
        try run(stagedPython, ["-I", "-B", "-c", "import tinysearch, av, pypdfium2, fastapi; assert tinysearch.__version__ == '\(payload.version)'"], timeout: 30)
        progress("Optimizing engine startup — first installation only…")
        try run(stagedPython, ["-I", "-B", "-m", "compileall", "-q", "-j", "2", "-s", staging.path, "-p", destination.path, staging.appendingPathComponent("python/lib/python3.12").path], timeout: 120)
        try Data("1".utf8).write(to: staging.appendingPathComponent(".startup-cache-ready"), options: .atomic)
        progress("Preparing any already downloaded Core ML model…")
        try run(stagedPython, ["-I", "-B", "-m", "tinysearch.coreml_assets", "--storage", storage.path], timeout: 660)
        try manifestData.write(to: staging.appendingPathComponent("installed.json"), options: .atomic)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: staging.appendingPathComponent("installed.json").path)
        try fm.moveItem(at: staging, to: destination)
        return payload.version
    }
}
