"""Native temporary-copy boundaries, independent of private Photos permissions."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native Photos helper requires macOS')
def test_photo_clipboard_contains_full_size_png_on_private_pasteboard(tmp_path):
    root = Path(__file__).resolve().parents[1]
    main = tmp_path/'ClipboardTest.swift'
    main.write_text('''
import AppKit
@main struct ClipboardTest {
    static func main() throws {
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 320, pixelsHigh: 180,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        let image = bitmap.cgImage!
        let board = NSPasteboard(name: NSPasteboard.Name("semantic-test-" + UUID().uuidString))
        defer { board.releaseGlobally() }
        try PhotoClipboard.write(image, to: board)
        let data = board.data(forType: .png)!
        precondition(Array(data.prefix(8)) == [137,80,78,71,13,10,26,10])
        let pasted = NSImage(pasteboard: board)!
        let rep = NSBitmapImageRep(data: data)!
        precondition(rep.pixelsWide == 320 && rep.pixelsHigh == 180)
        precondition(pasted.size.width > 0)
    }
}
''')
    binary = tmp_path/'clipboard-test'
    subprocess.run(['swiftc','-O','-parse-as-library','-module-cache-path',str(tmp_path/'modules'),
        str(root/'native/photos/PhotoClipboard.swift'),str(main),'-o',str(binary)],check=True,capture_output=True,timeout=90)
    subprocess.run([str(binary)],check=True,capture_output=True,timeout=30)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native Photos helper requires macOS')
def test_video_copy_preserves_original_and_removes_failed_outputs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    main = tmp_path/'CopyTest.swift'
    main.write_text('''
import Foundation
@main struct CopyTest {
    static func main() async throws {
        let session = URL(fileURLWithPath: CommandLine.arguments[1])
        let source = session.appendingPathComponent("original.mov")
        let output = session.appendingPathComponent("temporary.mov")
        let data = Data(repeating: 42, count: 5 * 1024 * 1024 + 1)
        try data.write(to: source)
        try await VideoCopy.copy(from: source, to: output, maxBytes: Int64(data.count),
                                 deadline: Date().addingTimeInterval(30), session: session)
        precondition(try Data(contentsOf: output) == data)
        try FileManager.default.removeItem(at: output)
        for mode in 0..<3 {
            if mode == 0 { try Data().write(to: session.appendingPathComponent("cancel")) }
            do {
                try await VideoCopy.copy(from: source, to: output,
                    maxBytes: mode == 2 ? 1 : Int64(data.count),
                    deadline: Date().addingTimeInterval(mode == 1 ? -1 : 30), session: session)
                fatalError("Expected copy rejection")
            } catch {
                precondition(!FileManager.default.fileExists(atPath: output.path))
                precondition(try Data(contentsOf: source) == data)
            }
            try? FileManager.default.removeItem(at: session.appendingPathComponent("cancel"))
        }
    }
}
''')
    # Throwing reads are evaluated before Swift's nonthrowing precondition closure.
    main.write_text(main.read_text().replace('precondition(try Data(contentsOf: output) == data)',
        'let copied = try Data(contentsOf: output); precondition(copied == data)').replace(
        'precondition(try Data(contentsOf: source) == data)',
        'let original = try Data(contentsOf: source); precondition(original == data)'))
    executable = tmp_path/'copy-test'
    subprocess.run(['swiftc','-O','-parse-as-library','-module-cache-path',str(tmp_path/'modules'),
                    str(root/'native/photos/VideoCopy.swift'),str(main),'-o',str(executable)],
                   check=True, capture_output=True, timeout=90)
    subprocess.run([str(executable),str(tmp_path)], check=True, capture_output=True, timeout=30)
