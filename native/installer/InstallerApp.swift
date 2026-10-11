import SwiftUI
import AppKit

@MainActor
final class InstallerDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var busy = false
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { busy ? .terminateCancel : .terminateNow }
    func windowShouldClose(_ sender: NSWindow) -> Bool { !busy }
}

@MainActor
final class InstallState: ObservableObject {
    @Published var busy = false
    @Published var complete = false
    @Published var message = "Includes Python and all required search libraries. No terminal setup."
    @Published var failed = false
    func install() {
        busy = true; failed = false
        (NSApplication.shared.delegate as? InstallerDelegate)?.busy = true
        Task {
            do {
                let resources = Bundle.main.resourceURL!
                let version = try await Task.detached {
                    try EngineInstaller(resources: resources, storage: EngineInstaller.sharedStorage, progress: { text in
                        Task { @MainActor in if self.busy { self.message = text } }
                    }).install()
                }.value
                message = "Engine \(version) installed. Return to the extension and choose Check Installation."
                complete = true
            } catch { message = error.localizedDescription; failed = true }
            busy = false
            (NSApplication.shared.delegate as? InstallerDelegate)?.busy = false
        }
    }
}

struct InstallerView: View {
    @StateObject private var state = InstallState()
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 14) {
                Image(nsImage: NSApplication.shared.applicationIconImage).resizable().frame(width: 64, height: 64)
                VStack(alignment: .leading, spacing: 5) {
                    Text("Semantic Search Engine").font(.title2.bold())
                    Text("Local search for Tinycast and Raycast").foregroundStyle(.secondary)
                }
            }
            Divider()
            Text("Runs EmbeddingGemma 2 on your Mac. Model weights are downloaded later from the extension.")
            Text("Requires Apple silicon and macOS 26+. Allow 2.5 GB of free space, plus model weights and indexes.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Installs for your account in ~/Library/Application Support/Semantic Search/. Existing models, settings and indexes are preserved.")
                .font(.callout).foregroundStyle(.secondary)
            if state.busy { ProgressView().controlSize(.small) }
            Text(state.message).font(.callout).foregroundStyle(state.failed ? Color.red : Color.secondary)
            Spacer(minLength: 0)
            HStack {
                Button("Open Storage Folder") { NSWorkspace.shared.open(EngineInstaller.sharedStorage) }
                Spacer()
                if state.complete {
                    Button("Done") { NSApplication.shared.terminate(nil) }.keyboardShortcut(.defaultAction)
                } else {
                    Button(state.failed ? "Try Again" : "Install") { state.install() }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(state.busy)
                }
            }
        }.padding(28).frame(width: 560, height: 430)
    }
}

@main
struct InstallerApp {
    @MainActor static func main() {
        // Maintainer smoke tests use the exact same core without automating UI.
        let arguments = CommandLine.arguments
        if arguments.contains("--install-test") {
            guard let index = arguments.firstIndex(of: "--storage"), arguments.count > index + 1 else { exit(2) }
            do {
                let version = try EngineInstaller(resources: Bundle.main.resourceURL!, storage: URL(fileURLWithPath: arguments[index + 1]), progress: { print($0) }).install()
                print("Installed \(version)"); exit(0)
            } catch { fputs("\(error.localizedDescription)\n", stderr); exit(1) }
        }
        let app = NSApplication.shared
        let delegate = InstallerDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 430), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Semantic Search Engine Installer"
        window.delegate = delegate
        window.contentViewController = NSHostingController(rootView: InstallerView())
        window.center(); window.makeKeyAndOrderFront(nil)
        app.activate(ignoringOtherApps: true)
        withExtendedLifetime(delegate) { app.run() }
    }
}
