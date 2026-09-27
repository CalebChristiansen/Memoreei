import Foundation

/// Runs `memoreei serve --http` as a child process, and keeps it running.
///
/// The child inherits this app's Full Disk Access grant: macOS charges file access to
/// the "responsible" process, which for anything this app starts is the app itself.
final class Server {
    enum State: Equatable {
        case starting
        case running
        case portInUse(holder: String)
        case stopped(reason: String)
    }

    var onChange: ((State) -> Void)?
    /// The dashboard's Stop: the server left because it was asked to, and the app goes too.
    var onAskedToQuit: (() -> Void)?
    private(set) var state: State = .stopped(reason: "Not started") {
        didSet { if state != oldValue { DispatchQueue.main.async { self.onChange?(self.state) } } }
    }

    private var process: Process?
    private var stopping = false
    private var restarts = 0
    private var readyTimer: Timer?
    let port = Settings.port

    /// The environment every Python child gets: the bundle's packages and model, and
    /// nothing written back into the bundle (it would break the code signature), so
    /// bytecode is cached in ~/Library/Caches instead.
    static func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        for name in ["PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"] {
            env.removeValue(forKey: name)
        }
        env["PYTHONPATH"] = Paths.sitePackages.path
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONPYCACHEPREFIX"] = Paths.library.appendingPathComponent("Caches/Memoreei/pycache").path
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        env["FASTEMBED_CACHE_PATH"] = Paths.models.path
        env["HF_HUB_OFFLINE"] = "1"
        env["MEMOREEI_APP"] = "1"
        // So the dashboard's Start at Login switch writes the agent this app would.
        env["MEMOREEI_APP_EXECUTABLE"] = Bundle.main.executablePath
        env["MEMOREEI_HOME"] = Paths.home.path
        env["MEMOREEI_PARENT_PID"] = String(getpid())
        return env
    }

    func start() {
        guard process == nil else { return }
        stopping = false
        // A server left behind by a copy of the app that crashed notices within seconds
        // (see MEMOREEI_PARENT_PID) and exits; give it the chance before calling the port taken.
        var holder = Server.portHolder(port)
        let deadline = Date().addingTimeInterval(10)
        while let h = holder, h.hasPrefix("Memoreei"), Date() < deadline {
            usleep(500_000)
            holder = Server.portHolder(port)
        }
        if let holder = holder {
            state = .portInUse(holder: holder)
            Log.app("port \(port) is in use by \(holder); not starting")
            return
        }
        state = .starting
        Log.rotateIfLarge()
        try? FileManager.default.removeItem(at: Signals.quitRequested)  // a stale request

        let p = Process()
        p.executableURL = Paths.serverExecutable
        p.arguments = ["-m", "memoreei", "serve", "--http"]
        p.environment = Server.environment()
        p.currentDirectoryURL = Paths.home
        if let log = Log.handle() {
            p.standardOutput = log
            p.standardError = log
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.exited(proc) }
        }
        do {
            try p.run()
        } catch {
            state = .stopped(reason: "Couldn't start the server: \(error.localizedDescription)")
            Log.app("couldn't start the server: \(error)")
            return
        }
        process = p
        Log.app("started server, pid \(p.processIdentifier), port \(port)")
        waitUntilListening()
    }

    /// Stop the server and wait for it (up to a few seconds) before returning.
    func stop() {
        stopping = true
        readyTimer?.invalidate()
        guard let p = process, p.isRunning else { process = nil; return }
        p.terminate()
        let deadline = Date().addingTimeInterval(8)
        while p.isRunning && Date() < deadline { usleep(100_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        p.waitUntilExit()
        process = nil
        state = .stopped(reason: "Stopped")
    }

    func restart() {
        stop()
        restarts = 0
        start()
    }

    private func exited(_ proc: Process) {
        readyTimer?.invalidate()
        process = nil
        if stopping { return }
        if FileManager.default.fileExists(atPath: Signals.quitRequested.path) {
            try? FileManager.default.removeItem(at: Signals.quitRequested)
            state = .stopped(reason: "Stopped")
            onAskedToQuit?()
            return
        }
        let code = proc.terminationStatus
        Log.app("server exited unexpectedly (status \(code))")
        restarts += 1
        if restarts > 5 {
            state = .stopped(reason: "The server keeps stopping. See the log.")
            return
        }
        state = .starting
        let delay = min(60.0, pow(2.0, Double(restarts)))
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self, !self.stopping else { return }
            self.start()
        }
    }

    private func waitUntilListening() {
        let started = Date()
        readyTimer?.invalidate()
        readyTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] timer in
            guard let self = self, self.process != nil else { timer.invalidate(); return }
            if Server.canConnect(self.port) {
                timer.invalidate()
                self.state = .running
                // A clean start resets the crash backoff after it has stayed up a while.
                DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak self] in
                    if self?.state == .running { self?.restarts = 0 }
                }
            } else if Date().timeIntervalSince(started) > 180 {
                timer.invalidate()
                self.state = .stopped(reason: "The server didn't start listening. See the log.")
            }
        }
    }

    // MARK: - Running the CLI

    /// Run `memoreei <args>` with the bundled Python and return its stdout.
    static func runCLI(_ args: [String], completion: @escaping (String?) -> Void) {
        DispatchQueue.global().async {
            let p = Process()
            p.executableURL = Paths.serverExecutable
            p.arguments = ["-m", "memoreei"] + args
            p.environment = environment()
            p.currentDirectoryURL = Paths.home
            let out = Pipe()
            p.standardOutput = out
            p.standardError = Log.handle() ?? FileHandle.nullDevice
            var result: String?
            do {
                try p.run()
                let data = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                if p.terminationStatus == 0 {
                    result = String(data: data, encoding: .utf8)?
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                }
            } catch {
                Log.app("couldn't run memoreei \(args.joined(separator: " ")): \(error)")
            }
            DispatchQueue.main.async { completion(result) }
        }
    }

    // MARK: - The port

    static func canConnect(_ port: Int) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port).bigEndian)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
    }

    /// What's listening on the port, if anything: "name (pid N)", or a vaguer answer
    /// when the holder belongs to another user and lsof can't name it.
    static func portHolder(_ port: Int) -> String? {
        guard canConnect(port) else { return nil }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        p.arguments = ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-Fcp"]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return "another program" }
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        p.waitUntilExit()
        var pid = "", command = ""
        for line in text.split(separator: "\n") {
            if line.hasPrefix("p") && pid.isEmpty { pid = String(line.dropFirst()) }
            if line.hasPrefix("c") && command.isEmpty { command = String(line.dropFirst()) }
        }
        return command.isEmpty ? "another program" : "\(command) (pid \(pid))"
    }
}

/// ~/Library/Logs/Memoreei/memoreei.log: the server's output, and the app's own lines.
enum Log {
    private static let queue = DispatchQueue(label: "log")

    /// Opened for appending, by every writer: the server's output and the app's own
    /// lines land in one file without overwriting each other.
    static func handle() -> FileHandle? {
        Paths.ensureDirectories()
        let fd = open(Paths.serverLog.path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0o600)
        return fd < 0 ? nil : FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    }

    static func app(_ message: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        queue.async {
            guard let h = handle(), let data = "\(stamp) [app] \(message)\n".data(using: .utf8) else { return }
            h.write(data)
        }
    }

    /// Keep one previous log, and start afresh past 10 MB.
    static func rotateIfLarge() {
        let path = Paths.serverLog.path
        guard let size = (try? FileManager.default.attributesOfItem(atPath: path))?[.size] as? Int,
              size > 10_000_000 else { return }
        let old = path + ".1"
        try? FileManager.default.removeItem(atPath: old)
        try? FileManager.default.moveItem(atPath: path, toPath: old)
    }
}
