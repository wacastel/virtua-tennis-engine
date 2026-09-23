import Foundation

/// Resolves app-owned assets and a separate writable data directory.
/// Exact file identities must be checked by vt_create before the engine runs.
struct VTMedia {
    let assets: URL, saves: URL
    let temporary: Bool
    static func resolve() throws -> VTMedia {
        let fm = FileManager.default
        func option(_ name: String) -> String? {
            guard let index = CommandLine.arguments.firstIndex(of: name), index + 1 < CommandLine.arguments.count else { return nil }
            let value = CommandLine.arguments[index + 1]
            return value.hasPrefix("--") ? nil : value
        }
        let override = option("--assets") ?? ProcessInfo.processInfo.environment["VIRTUA_TENNIS_ASSET_DIR"]
        let assets = override.map { URL(fileURLWithPath: $0, isDirectory: true).standardizedFileURL }
            ?? Bundle.main.resourceURL?.appendingPathComponent("Assets", isDirectory: true)
        var directory: ObjCBool = false
        guard let assets, fm.fileExists(atPath: assets.path, isDirectory: &directory), directory.boolValue else {
            throw VirtuaTennisError.message("Virtua Tennis media is missing. Build the app with its verified local media, or provide --assets DIRECTORY.")
        }
        let explicit = option("--save-dir") ?? ProcessInfo.processInfo.environment["VIRTUA_TENNIS_SAVE_DIR"]
        let diagnostic = ["--headless","--self-test","--diagnostic-run","--audio-report","--audio-replay"].contains(where: CommandLine.arguments.contains)
        let temporary = explicit == nil && (Bundle.main.bundleIdentifier != VTPreferences.domain || diagnostic)
        let saves: URL
        if let explicit { saves = URL(fileURLWithPath: explicit, isDirectory: true).standardizedFileURL }
        else if temporary { saves = fm.temporaryDirectory.appendingPathComponent("virtua-tennis-test-" + UUID().uuidString, isDirectory: true) }
        else {
            saves = try fm.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
                .appendingPathComponent(VTPreferences.domain, isDirectory: true)
        }
        try fm.createDirectory(at: saves, withIntermediateDirectories: true)
        return VTMedia(assets: assets, saves: saves, temporary: temporary)
    }
    func removeTemporarySaves() { if temporary { try? FileManager.default.removeItem(at: saves) } }
}
