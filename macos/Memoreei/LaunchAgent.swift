import Foundation

/// "Start at login": a LaunchAgent whose program is this app's own executable.
///
/// It has to be the app itself, not a script or Python: macOS holds the first program a
/// launchd job runs responsible for its file access, so that's what the Full Disk Access
/// grant must name. KeepAlive only on a crash; Quit (a clean exit) stays quit.
/// SMAppService would do this for us, but only from macOS 13.
enum LaunchAgent {
    /// Set in the agent's environment, so the app knows launchd started it.
    static let marker = "MEMOREEI_LAUNCHD"

    static var launchedByLaunchd: Bool { ProcessInfo.processInfo.environment[marker] == "1" }
    static var isInstalled: Bool { FileManager.default.fileExists(atPath: Paths.launchAgent.path) }
    private static var domain: String { "gui/\(getuid())" }

    static func write() throws {
        var env: [String: String] = [marker: "1"]
        // A custom home (for development) follows the app into launchd.
        if let home = ProcessInfo.processInfo.environment["MEMOREEI_HOME"] { env["MEMOREEI_HOME"] = home }
        let plist: [String: Any] = [
            "Label": Paths.bundleID,
            "ProgramArguments": [Bundle.main.executablePath!],
            "EnvironmentVariables": env,
            "RunAtLoad": true,
            "KeepAlive": ["SuccessfulExit": false],
            "LimitLoadToSessionType": "Aqua",
            "ProcessType": "Interactive",
            "StandardOutPath": Paths.logs.appendingPathComponent("app.log").path,
            "StandardErrorPath": Paths.logs.appendingPathComponent("app.log").path,
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try FileManager.default.createDirectory(
            at: Paths.launchAgent.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: Paths.launchAgent, options: .atomic)
    }

    /// The executable the installed agent starts, or nil if there's no agent.
    static var installedProgram: String? {
        guard let data = try? Data(contentsOf: Paths.launchAgent),
              let plist = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              let args = plist["ProgramArguments"] as? [String] else { return nil }
        return args.first
    }

    /// Whether the agent starts this copy of the app, rather than one somewhere else.
    static var startsThisCopy: Bool {
        guard let program = installedProgram else { return false }
        return URL(fileURLWithPath: program).resolvingSymlinksInPath().path
            == URL(fileURLWithPath: Bundle.main.executablePath!).resolvingSymlinksInPath().path
    }

    /// Point the agent at this copy, keeping the rest of it (a custom home, say).
    static func repoint() throws {
        let data = try Data(contentsOf: Paths.launchAgent)
        guard var plist = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else {
            throw CocoaError(.propertyListReadCorrupt)
        }
        plist["ProgramArguments"] = [Bundle.main.executablePath!]
        let out = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try out.write(to: Paths.launchAgent, options: .atomic)
    }

    /// Off: remove the file, so nothing starts at the next login. The job stays loaded
    /// until then; unloading it now would stop this very app if launchd started it.
    static func remove() {
        try? FileManager.default.removeItem(at: Paths.launchAgent)
    }

    /// Hand this app over to launchd: ask it to start the agent's copy, which waits for
    /// this one to exit (see InstanceLock) before starting the server. The caller exits.
    /// The job is unloaded first: launchd keeps the file it loaded, not the one on disk,
    /// so a rewritten agent (repoint) only counts once it's loaded again. Nothing of the
    /// job is running to be stopped by that: the caller holds the instance lock.
    static func handOver() {
        let script = """
            sleep 0.5
            /bin/launchctl bootout \(domain)/\(Paths.bundleID) 2>/dev/null
            /bin/launchctl bootstrap \(domain) '\(Paths.launchAgent.path)' 2>/dev/null
            /bin/launchctl kickstart \(domain)/\(Paths.bundleID)
            """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", script]
        try? p.run()
        Log.app("handing over to launchd")
    }
}

/// One Memoreei per user at a time, decided by an flock.
enum InstanceLock {
    private static var fd: Int32 = -1

    static func tryAcquire() -> Bool {
        Paths.ensureDirectories()
        let path = Paths.state.appendingPathComponent("app.lock").path
        if fd < 0 { fd = open(path, O_CREAT | O_RDWR | O_CLOEXEC, 0o600) }  // not inherited by children
        return fd >= 0 && flock(fd, LOCK_EX | LOCK_NB) == 0
    }

    /// For launchd's copy after a hand-over: the other copy is on its way out.
    static func acquire(waiting seconds: Double) -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if tryAcquire() { return true }
            usleep(200_000)
        }
        return tryAcquire()
    }
}

/// Messages between two copies of the app: the second tells the first to show itself.
enum Signals {
    static let show = Notification.Name("\(Paths.bundleID).show")
    /// One-shot: the next copy to start opens the dashboard once the server is up.
    static let openOnStart = Paths.state.appendingPathComponent("open-dashboard")
    /// One-shot, written by the server (service/_app.py): its exit was the dashboard's
    /// Stop, so the app quits instead of restarting it.
    static let quitRequested = Paths.state.appendingPathComponent("quit-requested")

    static func postShow() {
        DistributedNotificationCenter.default().postNotificationName(
            show, object: nil, userInfo: nil, deliverImmediately: true)
    }
}
