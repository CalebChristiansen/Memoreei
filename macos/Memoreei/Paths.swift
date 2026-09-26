import Foundation

/// Where everything is: inside the bundle, and in the user's Library.
enum Paths {
    static let bundleID = Bundle.main.bundleIdentifier ?? "Memoreei"
    static let fileManager = FileManager.default
    static let library = fileManager.homeDirectoryForCurrentUser.appendingPathComponent("Library")

    // The server is a small app inside this one, so macOS names it "Memoreei Server" (in
    // the firewall's prompt, Activity Monitor) rather than "python3.12". It's the bundled
    // standalone CPython with its bin/ renamed to MacOS/.
    static let serverApp = Bundle.main.bundleURL
        .appendingPathComponent("Contents/Helpers/Memoreei Server.app")
    static let serverExecutable = serverApp.appendingPathComponent("Contents/MacOS/Memoreei Server")
    static let resources = Bundle.main.resourceURL!
    static let sitePackages = resources.appendingPathComponent("site-packages")
    static let models = resources.appendingPathComponent("models")

    /// Memoreei's home: config.env and memoreei.db. The same default the CLI uses on
    /// macOS, so `memoreei` in Terminal sees the same keys and database.
    static var home: URL {
        if let custom = ProcessInfo.processInfo.environment["MEMOREEI_HOME"], !custom.isEmpty {
            return URL(fileURLWithPath: (custom as NSString).expandingTildeInPath)
        }
        return library.appendingPathComponent("Application Support/Memoreei")
    }

    static let logs = library.appendingPathComponent("Logs/Memoreei")
    /// Per user, whatever the home: the single-instance lock and one-shot flags.
    static let state = library.appendingPathComponent("Caches/Memoreei")
    static let serverLog = logs.appendingPathComponent("memoreei.log")
    static let launchAgent = library.appendingPathComponent("LaunchAgents/\(bundleID).plist")
    static let messagesDB = library.appendingPathComponent("Messages/chat.db")

    /// Created before anything else runs; mode 700, like the CLI's ensure_home().
    static func ensureDirectories() {
        for dir in [home, logs, state] where !fileManager.fileExists(atPath: dir.path) {
            try? fileManager.createDirectory(
                at: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        }
    }
}

/// The few settings the app itself needs, read the way memoreei reads them: the real
/// environment first, then config.env.
enum Settings {
    static func value(_ name: String) -> String? {
        if let v = ProcessInfo.processInfo.environment[name], !v.isEmpty { return v }
        guard let text = try? String(contentsOf: Paths.home.appendingPathComponent("config.env"))
        else { return nil }
        var found: String?
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let key = line[..<eq].trimmingCharacters(in: .whitespaces)
                .replacingOccurrences(of: "export ", with: "")
            if key == name {
                var v = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
                if v.count >= 2, let q = v.first, (q == "\"" || q == "'"), v.last == q {
                    v = String(v.dropFirst().dropLast())
                }
                found = v.isEmpty ? nil : v
            }
        }
        return found
    }

    static var port: Int { Int(value("MEMOREEI_PORT") ?? "") ?? 3679 }
}

enum AppInfo {
    static let version = Bundle.main.object(forInfoDictionaryKey: "MemoreeiVersion") as? String ?? "?"
    static let releasesAPI = Bundle.main.object(forInfoDictionaryKey: "MemoreeiReleasesAPI") as? String
    static let settingsName = ProcessInfo.processInfo.isOperatingSystemAtLeast(
        OperatingSystemVersion(majorVersion: 13, minorVersion: 0, patchVersion: 0))
        ? "System Settings" : "System Preferences"
}
